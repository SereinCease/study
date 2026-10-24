"""Durable personal focus timers. Time is never a mastery/assessment signal."""
import json
import time
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import db, spaces

router = APIRouter(prefix='/api/focus-sessions', tags=['focus'])
ACTIVE = ('ready', 'running', 'paused')
clock = time.time

class FocusCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    space_id: str
    goal_id: str | None = None
    session_id: str | None = None
    task_title: str = Field(default='', max_length=160)
    duration_minutes: int = Field(default=25, ge=1, le=180)
    request_id: str = Field(min_length=8, max_length=100)

class FocusAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    action: Literal['start', 'pause', 'resume', 'reset', 'finish']


def _remaining(value, stamp):
    if value['status'] == 'running':
        return max(0., min(value['duration_seconds'], value['deadline'] - stamp))
    return value['remaining_seconds']


def _expire(value, stamp, con):
    if value and value['status'] == 'running' and _remaining(value, stamp) <= 0:
        value = db.update(value['id'], {'status': 'completed', 'remaining_seconds': 0, 'deadline': None,
            'finished_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat(), 'finished_reason': 'elapsed'}, con=con)
    return value


def _result(value, stamp):
    return {'session': value, 'server_time': stamp}


def _active(con):
    row = con.execute("SELECT data FROM entities WHERE kind='focus_session' AND json_extract(data,'$.status') IN ('ready','running','paused') ORDER BY created DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def _scope(body, con):
    space = spaces.resolve(body.space_id, con=con)
    goal = db.get(body.goal_id, 'goal', con) if body.goal_id else None
    session = db.get(body.session_id, 'session', con) if body.session_id else None
    if session and not goal and session.get('goal_id'):
        goal = db.get(session['goal_id'], 'goal', con)
    for value in (goal, session):
        if value: spaces.assert_member(value, space['id'], con=con)
    if session and body.goal_id and session.get('goal_id') != body.goal_id:
        raise ValueError('教学会话与学习目标不一致')
    return space, goal, session


@router.get('')
def current():
    stamp = clock()
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        value = _expire(_active(con), stamp, con)
        if not value:
            row = con.execute("SELECT data FROM entities WHERE kind='focus_session' ORDER BY json_extract(data,'$.updated') DESC LIMIT 1").fetchone()
            value = json.loads(row[0]) if row else None
        return _result(value, stamp)


@router.post('')
def create(body: FocusCreate):
    stamp = clock()
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        space, goal, session = _scope(body, con)
        key = 'focus:' + body.request_id
        old = con.execute('SELECT entity FROM request_keys WHERE key=?', (key,)).fetchone()
        if old:
            existing = db.get(old[0], 'focus_session', con)
            expected = {'space_id': space['id'], 'goal_id': goal['id'] if goal else None, 'session_id': body.session_id, 'task_title': body.task_title.strip()}
            if any(existing.get(k) != v for k, v in expected.items()) or existing['duration_seconds'] != body.duration_minutes * 60:
                raise HTTPException(409, '该请求标识已用于另一轮专注')
            return _result(_expire(existing, stamp, con), stamp)
        active = _expire(_active(con), stamp, con)
        if active and active['status'] in ACTIVE:
            raise HTTPException(409, '已有一轮专注，请恢复或结束当前计时')
        value = db.put('focus_session', {'space_id': space['id'], 'goal_id': goal['id'] if goal else None,
            'session_id': body.session_id, 'task_title': body.task_title.strip(), 'duration_seconds': body.duration_minutes*60,
            'remaining_seconds': body.duration_minutes*60, 'status': 'ready', 'deadline': None,
            'started_at': None, 'finished_at': None, 'finished_reason': None}, con=con)
        con.execute('INSERT INTO request_keys VALUES(?,?)', (key, value['id']))
        for item in (space, goal, session):
            if item: db.link(value['id'], item['id'], 'focus_context', con)
        return _result(value, stamp)


@router.patch('/{id}')
def act(id: str, body: FocusAction):
    stamp = clock()
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        value = db.get(id, 'focus_session', con)
        if body.revision != value['revision']:
            raise HTTPException(409, '计时已在另一页面更新，请刷新当前状态')
        value = _expire(value, stamp, con)
        status = value['status']; remaining = _remaining(value, stamp)
        changes = {}
        if body.action == 'finish':
            if status == 'completed': return _result(value, stamp)
            changes = {'status': 'completed', 'remaining_seconds': remaining, 'deadline': None,
                'finished_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat(), 'finished_reason': 'user'}
        elif body.action == 'reset':
            active = _active(con)
            if active and active['id'] != id: raise HTTPException(409, '另一轮专注正在进行，不能重置旧计时')
            changes = {'status': 'ready', 'remaining_seconds': value['duration_seconds'], 'deadline': None,
                'started_at': None, 'finished_at': None, 'finished_reason': None}
        elif body.action in ('start', 'resume'):
            expected = 'ready' if body.action == 'start' else 'paused'
            if status != expected: raise HTTPException(409, '计时状态已经改变，请刷新后继续')
            changes = {'status': 'running', 'deadline': stamp + remaining,
                'started_at': value['started_at'] or datetime.fromtimestamp(stamp, timezone.utc).isoformat()}
        elif body.action == 'pause':
            if status == 'completed': return _result(value, stamp)
            if status != 'running': raise HTTPException(409, '当前计时没有运行')
            changes = {'status': 'paused', 'remaining_seconds': remaining, 'deadline': None}
        return _result(db.update(id, changes, kind='focus_session', con=con), stamp)
