"""Stable study scope and the shared daily recommendation contract."""
import json
from . import db,spaces


def scope(space_id=None,goal_id=None,concept_id=None,session_id=None,domain_id=None):
    parents=[db.get(i,k) for i,k in ((goal_id,'goal'),(concept_id,'concept'),(session_id,'session')) if i]
    sid=space_id or next((p.get('space_id') for p in parents if p.get('space_id')),None)
    space=spaces.resolve(sid,domain_id if sid is None else None) if sid or domain_id else spaces.resolve('automation')
    for p in parents:
        if p.get('space_id',space['id'])!=space['id']:raise ValueError('目标、会话与知识点必须属于当前空间')
    goal=next((p for p in parents if p['kind']=='goal'),None)
    concept=next((p for p in parents if p['kind']=='concept'),None)
    session=next((p for p in parents if p['kind']=='session'),None)
    if session and concept and session.get('concept_id')!=concept_id:raise ValueError('会话与练习知识点不一致')
    if session and goal_id and session.get('goal_id')!=goal_id:raise ValueError('会话不属于当前目标')
    if goal and concept and not set(goal.get('source_ids',[])).intersection(concept.get('source_ids',[])) and concept_id not in goal.get('concept_ids',[]):raise ValueError('知识点未纳入当前目标')
    return {'space_id':space['id'],'domain_id':space['domain_id'],'goal_id':goal_id or (session or {}).get('goal_id')}


def sources(space_id,limit=100,cursor=None):
    ids=spaces.source_ids(space_id)
    if not ids:return []
    with db.connect() as c:
        args=list(ids);sql='SELECT data FROM entities WHERE kind=\'source\' AND id IN ('+','.join('?'*len(ids))+')'
        if cursor:
            pivot=db.get(cursor,'source',c);sql+=' AND (created,id)<(?,?)';args += [pivot['created'],cursor]
        sql+=' ORDER BY created DESC,id DESC LIMIT ?';args.append(limit)
        return [json.loads(r[0]) for r in c.execute(sql,args)]


def records(kind,space_id=None,goal_id=None,limit=100,cursor=None):
    if kind=='source' and space_id:return sources(space_id,limit,cursor)
    filters={k:v for k,v in [('space_id',space_id),('goal_id',goal_id)] if v}
    return db.select(kind,filters=filters or None,limit=limit,cursor=cursor,descending=True)


def state(space_id,goal_id=None):
    from .learning import goal_path
    spaces.resolve(space_id)
    goals=records('goal',space_id)
    goal=db.get(goal_id,'goal') if goal_id else next((g for g in goals if g.get('status')!='completed'),None)
    if goal and goal.get('space_id')!=space_id:raise ValueError('目标不属于当前空间')
    gid=(goal or {}).get('id')
    sessions=records('session',space_id,gid)
    resumable=[s for s in sessions if s.get('status') in ('active','paused') and s.get('skill_id')!='decompose' and s.get('mode','learn')=='learn']
    resumable=[s for s in resumable if not s.get('mistake_id') or not db.get(s['mistake_id'],'mistake').get('assessment_voided') and db.get(s['mistake_id'],'mistake').get('status')!='voided']
    resumable.sort(key=lambda s:s.get('updated',s['created']),reverse=True)
    path=goal_path(goal) if goal else []
    concept_ids={u['concept_id'] for u in path}
    due=[r for r in records('review',space_id,limit=1000) if r['due']<=db.now() and r.get('concept_id') in concept_ids]
    mistakes=[m for m in records('mistake',space_id,gid) if m.get('status') in ('pending_analysis','pending_practice') and not m.get('assessment_voided')]
    action={'kind':'collect','reason':'先收藏资料或创建一个学习目标'}
    if resumable:action={'kind':'resume','reason':'继续上次未完成的教学任务','session_id':resumable[0]['id'],'concept_id':resumable[0].get('concept_id')}
    elif due:action={'kind':'review','reason':due[0]['reason'],'concept_id':due[0]['concept_id']}
    elif mistakes:action={'kind':'remediate','reason':'这道错题还需要独立补练','mistake_id':mistakes[0]['id'],'concept_id':mistakes[0].get('concept_id')}
    else:
        next_unit=next((u for u in path if u['status']!='skip_mastered' and not u['blocked']),None)
        if next_unit:action={'kind':'learn','reason':'开始当前路径中前置条件已满足的单元','concept_id':next_unit['concept_id']}
        elif goal and path:action={'kind':'project','reason':'完成阶段任务，单次通过不代表长期掌握'}
    action['goal_id']=gid
    return {'space_id':space_id,'goal':goal,'goals':goals,'session':resumable[0] if resumable else None,'path':path,'due':due,'next_action':action,'minutes':(goal or {}).get('minutes'), 'mistakes':mistakes[:10]}
