from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from . import db,memory,models,jobs,maintenance,study,spaces

router=APIRouter(prefix='/api')
class PreferenceIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    theme:Literal['ink','casefile','noir','minimal']='ink'
    motion:bool=True
    scene3d:bool=True
    font_size:int=Field(default=16,ge=13,le=20)
    density:Literal['comfortable','compact']='comfortable'
    line_height:float=Field(default=1.85,ge=1.5,le=2.2)
    focus:bool=False

@router.get('/preferences')
def preferences():
    try:return db.get('preferences','preferences')
    except ValueError:return PreferenceIn().model_dump()

@router.put('/preferences')
def save_preferences(value:PreferenceIn):return db.put('preferences',value.model_dump(),id='preferences')

@router.get('/learner-profile')
def learner_profile(domain:str='automation-testing',concept_id:str|None=None,space_id:str|None=None):return memory.profile(spaces.resolve(space_id)['domain_id'] if space_id else domain,concept_id,space_id)

@router.post('/learner-profile/facts')
def add_fact(data:dict):return memory.save_fact(data)

@router.patch('/learner-profile/facts/{id}')
def edit_fact(id:str,data:dict):return memory.save_fact(data,id)

@router.delete('/learner-profile/facts/{id}')
def remove_fact(id:str):return db.update(id,{'deleted':True,'memory_enabled':False},'learner_fact')

@router.get('/evidence/search')
def evidence_search(q:str='',concept_id:str|None=None,domain:str='automation-testing',limit:int=6,space_id:str|None=None,goal_id:str|None=None):
    return memory.search(q,concept_id,spaces.resolve(space_id)['domain_id'] if space_id else domain,limit,space_id=space_id,**({'goal_id':goal_id} if goal_id is not None else {}))

@router.get('/evidence/{id}')
def evidence_detail(id:str):return memory.detail(id)

@router.patch('/evidence/{id}')
def evidence_visibility(id:str,data:dict):return memory.visibility(id,bool(data.get('memory_enabled')))

@router.delete('/evidence/{id}')
def evidence_delete(id:str):return memory.visibility(id,delete=True)

@router.get('/skill-sessions/{id}/memory')
def session_memory(id:str):
    s=db.get(id,'session')
    summaries=db.select('summary',filters={'session_id':id},limit=50,descending=True)
    contexts=db.select('context_snapshot',filters={'session_id':id},limit=1,descending=True)
    if contexts:
        import json
        context=contexts[0];context['facts']=[]
        with db.connect() as c:
            for fid in context.get('fact_ids',[]):
                try:fact=db.get(fid,'learner_fact',c)
                except ValueError:continue
                if fact.get('deleted') or not fact.get('memory_enabled',True):continue
                rev=context.get('source_revisions',{}).get(fid)
                if rev and rev!=fact.get('revision'):
                    row=c.execute('SELECT data FROM entity_revisions WHERE id=? AND revision=?',(fid,rev)).fetchone()
                    if not row:continue
                    fact=json.loads(row[0])
                context['facts'].append(fact)
    return {'summaries':summaries,'practice_evidence':memory.practice_memory.evidence(s),'summary_needs_update':bool(memory.practice_memory.pending(s) or next(memory.pending_events(id),None)) if s.get('memory_enabled',True) else False,'context':contexts[0] if contexts else None,'enabled':s.get('memory_enabled',True),'current_task':s.get('current_task',{}),'jobs':[x for x in db.select('job',limit=100,descending=True) if x.get('task')=='summarize' and x.get('payload',{}).get('session_id')==id]}

@router.post('/skill-sessions/{id}/summary')
def request_summary(id:str):return memory.schedule_summary(id,force=True) or {'status':'up_to_date'}

@router.patch('/skill-sessions/{id}')
def update_session(id:str,data:dict):
    changes={}
    if 'reasoning' in data:
        if data['reasoning'] not in (*models.MODES,None):raise ValueError('未知推理档位')
        changes['reasoning']=data['reasoning']
    if 'memory_enabled' in data:changes['memory_enabled']=bool(data['memory_enabled'])
    if 'status' in data:
        if data['status'] not in ('active','paused','completed'):raise ValueError('无效会话状态')
        changes['status']=data['status']
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if data.get('status')=='completed' and c.execute("SELECT 1 FROM entities WHERE kind='job' AND json_extract(data,'$.task') IN ('session','decompose') AND json_extract(data,'$.status') IN ('queued','running') AND json_extract(data,'$.payload.session_id')=? LIMIT 1",(id,)).fetchone():
            raise ValueError('本轮仍在处理中，请等待结束后完成会话')
        s=db.update(id,changes,'session',c)
        if 'memory_enabled' in changes:
            for row in c.execute('SELECT x.data FROM entities x JOIN evidence e ON e.id=x.id WHERE e.session_id=?',(id,)).fetchall():
                import json
                value=json.loads(row[0]);memory.index_record(value,c)
            c.execute('UPDATE memory_state SET epoch=epoch+1 WHERE id=1')
    if data.get('status') in ('paused','completed') and s.get('memory_enabled',True):memory.schedule_summary(id,True)
    return s

@router.get('/skill-sessions/{id}/draft')
def answer_draft(id:str):
    db.get(id,'session')
    try:return db.get('answer-'+id,'answer_draft')
    except ValueError:return {'text':'','revision':0}

@router.put('/skill-sessions/{id}/draft')
def save_answer_draft(id:str,data:dict):
    db.get(id,'session')
    if len(str(data.get('text','')))>30000:raise ValueError('草稿过长')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        try:old=db.get('answer-'+id,'answer_draft',c)
        except ValueError:old={'revision':0}
        if old['revision']!=data.get('revision',0):raise HTTPException(409,'草稿已在其他页面修改，请重新载入后合并')
        return db.put('answer_draft',{'session_id':id,'text':str(data.get('text',''))},id='answer-'+id,con=c)

@router.get('/storage')
def storage():return maintenance.stats()

@router.get('/storage/preview')
def preview():return maintenance.candidates()

@router.post('/storage/cleanup')
def cleanup():return jobs.enqueue('cleanup',{})

@router.post('/evidence/reindex')
def reindex():
    for j in db.select('job',filters={'status':'queued'},limit=1000)+db.select('job',filters={'status':'running'},limit=1000):
        if j.get('task')=='memory_index':return j
    return jobs.enqueue('memory_index',{})

@router.get('/records/{kind}')
def records(kind:str,cursor:str|None=None,limit:int=30,space_id:str|None=None,goal_id:str|None=None):
    if kind not in ('source','concept','session','event','attempt','mistake','mastery','review','goal','relation','run','conversation'):raise ValueError('不支持的记录类型')
    items=study.records(kind,space_id,goal_id,limit=min(100,max(1,limit)),cursor=cursor)
    return {'items':items,'next_cursor':items[-1]['id'] if len(items)==min(100,max(1,limit)) else None,'total':None if space_id else db.count(kind)}
