"""Explicit, reversible assessment withdrawal; never turn a bad question into zero marks."""
import hashlib
import json
import unicodedata
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from . import db

router = APIRouter(prefix='/api/assessments', tags=['assessments'])


def question_text(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split())


def fingerprint(question):
    return hashlib.sha256(question_text(question).encode()).hexdigest()


def task_identity(session):
    task = session.get('current_task') or {}
    question = task.get('question', '')
    return {'task_id': session.get('current_task_id') or ('legacy-' + session['id'] + '-' + fingerprint(question)),
            'task_event_id': session.get('current_task_event_id'),
            'task_fingerprint': fingerprint(question), 'task_question': question, 'task_concept_id':session.get('concept_id')}


def _void_action(session, question=None, task_id=None, con=None):
    if con is None:
        with db.connect() as c:
            return _void_action(session, question, task_id, c)
    question = (session.get('current_task') or {}).get('question', '') if question is None else question
    digest = fingerprint(question)
    if not question_text(question):
        return False
    # Scope by both space and concept. A repeated model question in a new session
    # is still the same withdrawn exercise; other spaces remain independent.
    for row in con.execute("SELECT data FROM entities WHERE kind='assessment_action' AND json_extract(data,'$.action')='void' AND json_extract(data,'$.status')='active' AND json_extract(data,'$.space_id')=?", (session.get('space_id'),)):
        record = json.loads(row[0])
        if record.get('concept_id') != session.get('concept_id'):
            continue
        if record.get('task_fingerprint') == digest or task_id and record.get('task_id') == task_id:
            return record
    return None


def task_voided(session, question=None, task_id=None, con=None):
    return bool(_void_action(session, question, task_id, con))


def run_task_scope(session_id, task_id=None, exercise_id=None, con=None):
    """A session supplies context; only an explicit task ID establishes task evidence.

    Even an explicit link does not make a fixed exercise's rubric the AI task's
    rubric. Runner grades always remain fixed-exercise grades.
    """
    if not session_id:
        if task_id:raise ValueError('关联题目必须指定其会话')
        return {'task_binding':'unbound'}
    session = db.get(session_id, 'session', con)
    if not task_id:
        return {'task_binding':'session_context_only'}
    identity = task_identity(session)
    if task_id != identity['task_id']:
        raise HTTPException(409, '当前题目已改变，请重新核对后关联；未启动运行')
    if not question_text(identity['task_question']):
        raise ValueError('当前没有可以关联的题目')
    if task_voided(session, con=con):
        raise HTTPException(409, '这道题已作废，不能关联其运行证据；可取消关联并独立练习')
    return {**identity,'task_binding':'explicit_user','linked_exercise_id':exercise_id,
            'grading_scope':'fixed_exercise_only'}


def run_flags(payload, con):
    if payload.get('task_binding')!='explicit_user' or not payload.get('task_id') or not payload.get('task_question'):
        return {}
    scope = {'space_id':payload.get('space_id'), 'concept_id':payload.get('task_concept_id',payload.get('concept_id'))}
    void = _void_action(scope, payload['task_question'], payload['task_id'], con)
    if void:
        return {'assessment_voided': True, 'assessment_void_id':void['id'], 'assessment_void_reason': '运行期间对应题目已作废；报告仅保留实际运行事实，不计入成绩'}
    return {}


def _records(kind, field, value, con):
    if field not in ('event_id','run_id','session_id','space_id'):
        raise ValueError('无效关联字段')
    return [json.loads(r[0]) for r in con.execute(f"SELECT data FROM entities WHERE kind=? AND json_extract(data,'$.{field}') IS ? ORDER BY created,id", (kind,value))]


def _load_target(id, space_id, subject, con):
    target = db.get(id, con=con)
    if target['kind'] not in ('attempt', 'event'):
        raise ValueError('只能作废题目或作答评价')
    if target.get('space_id') != space_id:
        raise HTTPException(404, '当前空间不存在此评价')
    linked = _records('attempt','event_id',id,con) if target['kind'] == 'event' else [target]
    if any(a.get('space_id') != space_id for a in linked):
        raise ValueError('评价关联的空间不一致，请先修复关联')
    if subject == 'question' and target['kind'] != 'event':
        raise ValueError('题目定位需要原始出题事件')
    subject = 'question' if target['kind'] == 'event' and (subject == 'question' or not linked) else 'assessment'
    anchor = linked[0] if subject == 'assessment' else target
    question = anchor.get('question', '') if subject == 'assessment' else anchor.get('output', {}).get('question', '')
    if not question_text(question):
        raise ValueError('该记录没有可定位的题目')
    return target, anchor, subject, question


def describe(id, space_id, subject='assessment', con=None):
    if con is None:
        with db.connect() as c:
            return describe(id, space_id, subject, c)
    target, anchor, subject, question = _load_target(id, space_id, subject, con)
    flag = 'question_voided' if subject == 'question' else 'assessment_voided'
    void_id = target.get('question_void_id' if subject == 'question' else 'assessment_void_id')
    if subject == 'assessment':
        void_id = anchor.get('assessment_void_id') or void_id
    action = db.get(void_id, 'assessment_action', con) if void_id else {}
    attempts = _records('attempt','session_id',anchor['session_id'],con) if anchor.get('session_id') else [anchor] if anchor['kind'] == 'attempt' else []
    attempts = [a for a in attempts if a.get('space_id') == space_id and question_text(a.get('question')) == question_text(question)]
    return {'target': target, 'subject': subject, 'question': question, 'revision': target.get('revision', 0),
            'attempts': attempts, 'voided': bool(anchor.get(flag) or target.get(flag)), 'reason': action.get('reason', ''),
            'void_id': void_id, 'audit': {k: action.get(k) for k in ('id', 'reason', 'created', 'original_assessments', 'affected_ids')} if action else None}


class ActionIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    space_id: str
    revision: int = Field(ge=1)
    request_id: str = Field(min_length=8, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)
    subject: Literal['assessment', 'question'] = 'assessment'


def _collect(anchor, subject, question, con):
    session_id = anchor.get('session_id')
    candidates = _records('attempt','session_id',session_id,con) if session_id else []
    attempts = [a for a in candidates if a.get('space_id') == anchor.get('space_id') and question_text(a.get('question')) == question_text(question)]
    if anchor['kind'] == 'attempt' and anchor['id'] not in {a['id'] for a in attempts}:
        attempts.append(anchor)
    records = {a['id']: (a, 'assessment') for a in attempts}
    task_id = anchor.get('task_id') if subject == 'assessment' else anchor.get('output_task_id')
    if not task_id and session_id:
        session=db.get(session_id,'session',con)
        if question_text(session.get('current_task',{}).get('question'))==question_text(question):
            task_id=task_identity(session)['task_id']
    task_id=task_id or ('legacy-' + session_id + '-' + fingerprint(question) if session_id else None)
    events = _records('event','session_id',session_id,con) if session_id else []
    for event in events:
        if event.get('space_id') != anchor.get('space_id'):
            continue
        is_question = question_text(event.get('output', {}).get('question')) == question_text(question)
        is_assessment = event['id'] in {a.get('event_id') for a in attempts}
        if is_question or is_assessment:
            records[event['id']] = (event, 'both' if is_question and is_assessment else 'question' if is_question else 'assessment')
    if anchor['kind']=='event' and anchor['id'] not in records:
        records[anchor['id']]=(anchor,subject)
    run_ids = {a['run_id'] for a in attempts if a.get('run_id')}
    for run in _records('run','space_id',anchor.get('space_id'),con):
        explicit_link=run.get('task_binding')=='explicit_user' and (task_id and run.get('task_id')==task_id or subject=='question' and run.get('task_event_id')==anchor['id'])
        if run['id'] in run_ids or explicit_link:
            records[run['id']] = (run, 'assessment')
            for a in _records('attempt','run_id',run['id'],con):
                if a.get('space_id') != anchor.get('space_id'):
                    raise ValueError('运行与作答的空间关联不一致')
                records[a['id']] = (a, 'assessment')
                if a['id'] not in {x['id'] for x in attempts}: attempts.append(a)
    return records, attempts, task_id


def _invalidate_dependents(ids, con):
    """Deactivate conclusions derived from withdrawn evidence, preserving originals."""
    for summary in db.all('summary', con):
        if ids.intersection(summary.get('evidence_ids', [])) and summary.get('status') != 'stale':
            db.update(summary['id'], {'status': 'stale', 'invalidated_by': sorted(ids)[0], 'rebuild_job_id': None}, con=con)
    for fact in db.all('learner_fact', con):
        if ids.intersection(fact.get('evidence_ids', [])):
            db.update(fact['id'], {'needs_review': True, 'assessment_evidence_invalid': True}, con=con)
    invalid_contexts=set()
    for snapshot in db.all('context_snapshot', con):
        used = set(snapshot.get('evidence_ids', []) + snapshot.get('event_ids', []))
        if ids.intersection(used):
            db.update(snapshot['id'], {'stale': True, 'assessment_invalidated_by': sorted(ids)[0]}, con=con)
            invalid_contexts.add(snapshot['id'])
    if invalid_contexts:
        from . import memory
        for row in con.execute("SELECT data FROM entities WHERE kind IN ('event','conversation')").fetchall():
            record=json.loads(row[0])
            if record.get('context_id') in invalid_contexts:
                memory.index_record(record,con)
                memory.invalidate(record,con)
                if record.get('session_id'):
                    db.update(record['session_id'],{'current_task_stale':True},'session',con)


def act(id, value, action='void'):
    from . import learning, memory
    if not value.reason.strip(): raise ValueError('请填写作废或恢复原因')
    key = f'assessment:{action}:{id}:{value.subject}:{value.request_id}'
    digest = hashlib.sha256(json.dumps(value.model_dump(exclude={'revision'}), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        target, anchor, subject, question = _load_target(id, value.space_id, value.subject, con)
        existing = con.execute('SELECT entity FROM request_keys WHERE key=?', (key,)).fetchone()
        if existing:
            prior = db.get(existing[0], 'assessment_action', con)
            if prior['request_hash'] != digest: raise HTTPException(409, '请求编号已被不同操作使用，请刷新后重试')
            return prior['result']
        if target.get('revision') != value.revision:
            raise HTTPException(409, '评价已在其他页面变更，请重新载入后核对')
        status = describe(id, value.space_id, subject, con)
        if status['voided'] == (action == 'void'):
            return dict(status, already_applied=True)
        records, attempts, task_id = _collect(anchor, subject, question, con)
        audit_id = db.uid()
        audit = {'space_id': value.space_id, 'concept_id': anchor.get('concept_id'), 'goal_id': anchor.get('goal_id'),
                 'session_id': anchor.get('session_id'), 'target_id': id, 'subject': subject, 'action': action,
                 'reason': value.reason.strip(), 'request_hash': digest, 'task_id': task_id,
                 'task_fingerprint': fingerprint(question), 'question': question, 'status': 'active' if action == 'void' else 'restored',
                 'affected_ids': list(records), 'original_assessments': [{k: a.get(k) for k in ('id', 'score', 'corrected_score', 'answer', 'error_type', 'revision')} for a in attempts]}
        if action == 'restore':
            previous = db.get(status['void_id'], 'assessment_action', con)
            audit['restores'] = previous['id']
            db.update(previous['id'], {'status': 'restored', 'restored_by': audit_id, 'restored_at': db.now()}, con=con)
        db.put('assessment_action', audit, id=audit_id, con=con)
        for record, role in records.values():
            changes = {}
            for typ in ('assessment', 'question'):
                if role not in (typ, 'both'): continue
                if action == 'restore' and record.get(typ + '_void_id') != status['void_id']: continue
                changes.update({typ + '_voided': action == 'void', typ + '_void_id': audit_id if action == 'void' else None,
                                typ + '_void_reason': value.reason.strip() if action == 'void' else None})
            if changes: db.update(record['id'], changes, con=con)
        concept_ids = {a.get('concept_id') for a in attempts if a.get('concept_id')}
        # Mistakes generated by the invalid assessment remain auditable but leave
        # the practice queue. Withdrawing a later fix must not void the original mistake.
        attempt_ids = {a['id'] for a in attempts}
        for mistake in db.all('mistake', con):
            if mistake.get('space_id') != value.space_id: continue
            if mistake.get('attempt_id') in attempt_ids:
                if action == 'void':
                    db.update(mistake['id'], {'assessment_voided': True, 'assessment_void_id': audit_id,
                        'status_before_assessment_void': mistake.get('status'), 'status': 'voided'}, con=con)
                elif mistake.get('assessment_void_id') == status['void_id']:
                    db.update(mistake['id'], {'assessment_voided': False, 'assessment_void_id': None,
                        'status': mistake.get('status_before_assessment_void', 'pending_analysis')}, con=con)
        for concept_id in concept_ids:
            learning.recompute(concept_id, con)
        for session in _records('session','space_id',value.space_id,con):
            if session.get('concept_id') != anchor.get('concept_id'): continue
            if fingerprint(session.get('current_task', {}).get('question')) == fingerprint(question):
                db.update(session['id'], {'current_task_voided': task_voided(session, con=con)}, con=con)
        ids = set(records)
        _invalidate_dependents(ids, con)
        # Refresh dependency order, including events modified by the normal
        # assessment invalidator, so stale vectors cannot revive a voided grade.
        for kind in ('event', 'run', 'attempt', 'mistake'):
            for record_id in ids:
                record = db.get(record_id, con=con)
                if record['kind'] == kind: memory.index_record(record, con)
        result = describe(id, value.space_id, subject, con)
        result.update(action_id=audit_id, affected_ids=list(records),
            mastery=[db.get('mastery-' + cid, con=con) for cid in concept_ids],
            reviews=[r for r in db.all('review', con) if r.get('concept_id') in concept_ids])
        db.update(audit_id, {'result': result}, con=con)
        con.execute('INSERT INTO request_keys VALUES(?,?)', (key, audit_id))
        return result


@router.get('/{id}')
def get_assessment(id: str, space_id: str, subject: Literal['assessment', 'question'] = 'assessment'):
    return describe(id, space_id, subject)


@router.post('/{id}/void')
def void_assessment(id: str, value: ActionIn):
    return act(id, value, 'void')


@router.post('/{id}/restore')
def restore_assessment(id: str, value: ActionIn):
    return act(id, value, 'restore')

class RecoveryIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    space_id:str
    revision:int=Field(ge=1)
    request_id:str=Field(min_length=8,max_length=100)
    score:float=Field(ge=0,le=1)
    reason:str=Field(min_length=1,max_length=2000)

@router.post('/{id}/recover-missing')
def recover_missing(id:str,value:RecoveryIn):
    """Explicit reviewed assessment of a legacy ungraded answer, never a new attempt date."""
    from . import learning,memory
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        event=db.get(id,'event',c)
        if event.get('space_id')!=value.space_id:raise HTTPException(404,'当前空间不存在此作答')
        existing=_records('attempt','event_id',id,c)
        if existing:
            a=existing[0]
            if a.get('recovery_request_id')==value.request_id and a['score']==value.score and a.get('correction_reason')==value.reason:return a
            raise HTTPException(409,'该作答已有评价，请使用评价纠正入口')
        if event.get('revision',1)!=value.revision:raise HTTPException(409,'原记录已变更，请重新核对')
        if event.get('action')!='answer' or event.get('output',{}).get('score') is not None or event.get('assessment_voided') or event.get('deleted') or event.get('memory_enabled') is False or not event.get('answer','').strip():raise ValueError('仅可补记未作废且缺评分的原始作答')
        session=db.get(event['session_id'],'session',c)
        if session.get('mode')=='explore':raise ValueError('探索模式不补记评分')
        snapshot=db.get(event['context_id'],'context_snapshot',c)
        scope=snapshot.get('assessment_scope',{})
        if scope.get('task_id')!=event.get('answered_task_id') or not scope.get('question') or not scope.get('rubric'):raise ValueError('缺少当时的原题及评分快照，不能猜测补记')
        if task_voided(session,scope['question'],scope['task_id'],c):raise ValueError('原题已作废，不能补记评分')
        earlier=[e for e in _records('event','session_id',event['session_id'],c) if e['created']<event['created'] and e.get('output_task_id')==scope['task_id']]
        if not earlier:raise ValueError('原题辅助状态不可核对')
        hints=min(3,sum(e.get('action')=='hint' for e in earlier));seen=any(e.get('action')=='reveal' for e in earlier)
        a=db.put('attempt',{**{k:event.get(k) for k in ('space_id','goal_id','domain_id','concept_id','session_id')},
            'domain_id':event.get('domain_id') or session.get('domain_id') or 'automation-testing','event_id':id,'task_id':scope['task_id'],'task_question':scope['question'],'task_fingerprint':fingerprint(scope['question']),
            'question':scope['question'],'rubric':scope['rubric'],'answer':event['answer'],'score':value.score,'corrected_score':value.score,
            'feedback':value.reason,'correction_reason':value.reason,'correction_source':'explicit_review','evaluator':'reviewed_recovery',
            'original_score_missing':True,'recovered_at':db.now(),'recovery_request_id':value.request_id,
            'hint_level':hints,'answer_seen':seen,'is_transfer':earlier[-1].get('output',{}).get('is_transfer',False),'error_type':'unknown',
            'created':event['created']},id='recovered-'+id,con=c)
        if value.score<.7:
            db.put('mistake',{**{k:a.get(k) for k in ('space_id','goal_id','session_id','concept_id')},'attempt_id':a['id'],'question':a['question'],'answer':a['answer'],'expected':a['rubric'],'feedback':value.reason,'error_type':'unknown','status':'pending_analysis'},id='mistake-'+a['id'],con=c)
        memory.invalidate(a,c)
        learning.recompute(event['concept_id'],c)
        return a
