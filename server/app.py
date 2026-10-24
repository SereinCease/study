"""Local-first Learnloop API and production web host."""
import json
import hashlib
import os
import secrets
import zipfile
import io
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from typing import Literal
from fastapi import FastAPI,Request,HTTPException,UploadFile,File,Form
from fastapi.responses import JSONResponse,FileResponse,StreamingResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,ConfigDict
from . import db,models,jobs,ingest,learning,exercises,runner,domains,reporting,memory,study,spaces,study_api
from .memory_api import router as memory_router
from . import assessments

TOKEN=secrets.token_urlsafe(32)
ORIGINS={'http://127.0.0.1:8000','http://localhost:8000','http://localhost:5173','http://127.0.0.1:5173','http://127.0.0.1:4173','http://localhost:4173'}

ORIGINS.update({f'http://{host}:{os.environ.get("LEARNLOOP_PORT","8000")}' for host in ('localhost','127.0.0.1')})

@asynccontextmanager
async def lifespan(app):
    db.init()
    yield

app=FastAPI(title='Learnloop',version='4.0.0',lifespan=lifespan)
app.include_router(memory_router)
app.include_router(assessments.router)
from .knowledge_api import router as knowledge_router
from .focus_api import router as focus_router
app.include_router(knowledge_router)
app.include_router(focus_router)
app.include_router(study_api.router)

@app.middleware('http')
async def local_only(request:Request,call_next):
    if request.url.hostname not in ('127.0.0.1','localhost','testserver'):
        return JSONResponse({'detail':'仅接受本地访问'},403)
    if request.url.path.startswith('/api'):
        origin=request.headers.get('origin')
        if origin and origin not in ORIGINS:
            return JSONResponse({'detail':'请求来源不受信任'},403)
        if request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'拒绝跨站请求'},403)
        if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('x-learnloop-token',''),TOKEN):
            return JSONResponse({'detail':'本地会话已过期，请刷新页面'},403)
        if len(request.headers.get('content-length','0'))>12 or (request.headers.get('content-length','0').isdigit() and int(request.headers.get('content-length','0'))>25*1024*1024):
            return JSONResponse({'detail':'请求超过大小限制'},413)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['X-Frame-Options']='DENY'
    if request.url.path.startswith('/api'): response.headers['Cache-Control']='no-store'
    return response

@app.exception_handler(ValueError)
async def value_error(request,err):
    return JSONResponse({'detail':str(err)},400)

@app.exception_handler(models.ModelError)
async def model_error(request,err):
    return JSONResponse({'detail':str(err),'code':err.code,'diagnostics':err.diagnostics},502)

class Body(BaseModel):
    model_config=ConfigDict(extra='forbid')

class ProfileIn(Body):
    provider:Literal['gemini','deepseek','custom']
    endpoint:str
    model:str
    timeout:int=Field(default=120,ge=5,le=300)
    api_key:str|None=None
    reasoning:Literal['fast','standard','deep','max']='standard'
    max_output_tokens:int=Field(default=0,ge=0,le=32768)

class TextSource(Body):
    space_id:str|None=None
    name:str=Field(min_length=1,max_length=200)
    content:str=Field(min_length=1,max_length=1000000)

class WebSource(Body):
    space_id:str|None=None
    url:str=Field(max_length=2000)

class Question(Body):
    space_id:str|None=None
    mode:Literal["collect","explore","learn"]="explore"
    question:str=Field(min_length=1,max_length=2000)

class SessionIn(Body):
    space_id:str|None=None
    goal_id:str|None=None
    mode:Literal["explore","learn"]="learn"
    skill_id:str
    source_ids:list[str]=[]
    concept_id:str|None=None
    mistake_id:str|None=None
    goal:str=''
    domain_id:str|None=None
    reasoning:Literal['fast','standard','deep','max']|None=None

class EventIn(Body):
    action:str
    reasoning:Literal['fast','standard','deep','max']|None=None
    answer:str=Field(default='',max_length=30000)
    request_id:str=Field(min_length=8,max_length=100)

class RunIn(Body):
    task_id:str|None=None
    space_id:str|None=None
    goal_id:str|None=None
    session_id:str|None=None
    code:str=Field(min_length=1,max_length=50000)
    concept_id:str|None=None
    request_id:str=Field(min_length=8,max_length=100)

class GoalIn(Body):
    space_id:str|None=None
    exam:dict|None=None
    title:str=Field(min_length=1,max_length=200)
    source_ids:list[str]
    minutes:int=Field(default=30,ge=5,le=480)
    domain_id:str|None=None

class ConceptEdit(BaseModel):
    title:str|None=Field(default=None,min_length=1,max_length=160)
    definition:str|None=Field(default=None,max_length=10000)
    purpose:str|None=Field(default=None,max_length=3000)
    prerequisites:list[str]|None=None
    points:list[str]|None=None
    example:str|None=Field(default=None,max_length=10000)
    pitfalls:list[str]|None=None
    question:str|None=Field(default=None,max_length=3000)
    needs_review:bool|None=None
    x:float|None=None
    y:float|None=None

@app.get('/api/session')
def session(): return {'token':TOKEN}

@app.get('/api/health')
def health(): return {'status':'ok','version':'4.0.0'}

@app.get('/api/model-profiles')
def profiles():
    ids={'default'} | {x['id'] for x in db.all('profile')}
    return [models.public_profile(id) for id in sorted(ids)]

@app.put('/api/model-profiles/{id}')
def save_profile(id:str,value:ProfileIn):
    if not id.replace('-','').isalnum() or len(id)>80: raise ValueError('配置 ID 无效')
    return models.save(value.model_dump(exclude_none=True),id)

class CapabilityTest(BaseModel):
    reasoning:str|None=None

@app.post('/api/model-profiles/{id}/test')
def test_profile(id:str,value:CapabilityTest|None=None):
    return models.test_connection(id,value.reasoning if value else None)

@app.get('/api/model-calls')
def calls(): return list(reversed(db.select('model_call',limit=30,descending=True)))

@app.get('/api/sources')
def sources(space_id:str|None=None): return study.records('source',space_id)

@app.post('/api/sources')
def add_text(value:TextSource): return ingest.import_bytes(value.name,value.content.encode(),space_id=value.space_id)

@app.post('/api/sources/import')
async def upload(file:UploadFile=File(...),space_id:str|None=None):
    data=await file.read(ingest.MAX_BYTES+1)
    return ingest.import_bytes(file.filename or 'upload.txt',data,space_id=space_id)

@app.post('/api/sources/directory')
async def directory(files:list[UploadFile]=File(...),space_id:str|None=None):
    if len(files)>500: raise ValueError('最多导入 500 个文件')
    output=io.BytesIO()
    total=0
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for f in files:
            name=f.filename or ''
            if not ingest.filename_allowed(name) or not any(name.endswith(ext) for ext in ingest.EXTENSIONS): continue
            data=await f.read(ingest.MAX_BYTES+1)
            total+=len(data)
            if total>ingest.MAX_EXPANDED: raise ValueError('目录超过 50MB')
            z.writestr(name,data)
    return ingest.import_bytes('project.zip',output.getvalue(),space_id=space_id)

@app.post('/api/sources/web')
def web(value:WebSource):
    data,url=ingest.fetch_web(value.url)
    name=(urlparse(url).hostname or 'web')+'-'+hashlib.sha256(url.encode()).hexdigest()[:8]+'.html'
    return ingest.import_bytes(name,data,url,space_id=value.space_id)

@app.post('/api/sources/{id}/reindex')
def reindex(id:str,data:dict|None=None):
    source=db.get(id,'source')
    space_id=(data or {}).get('space_id') or source.get('space_id')
    if space_id and id not in spaces.source_ids(space_id):raise ValueError('资料不属于当前空间')
    db.update(id,{'status':'uploaded'},'source')
    job=jobs.enqueue('import',{'source_id':id,'space_id':space_id})
    db.update(id,{'job_id':job['id']})
    return job

@app.get('/api/sources/{id}/chunks')
def chunks(id:str,cursor:str|None=None,limit:int=30):
    db.get(id,'source')
    return db.select('chunk',filters={'source_id':id},limit=min(100,max(1,limit)),cursor=cursor)

@app.get('/api/chunks/{id}')
def chunk(id:str): return db.get(id,'chunk')

@app.get('/api/search')
def search(q:str='',space_id:str|None=None): return ingest.search(q,space_id=space_id)

@app.post('/api/ask')
def ask(value:Question): return learning.ask(value.question,value.space_id,value.mode)

@app.get('/api/concepts')
def concepts(space_id:str|None=None,goal_id:str|None=None): return study.records('concept',space_id,goal_id)

@app.patch('/api/concepts/{id}')
def concept_update(id:str,data:dict):
    updates=ConceptEdit.model_validate(data).model_dump(exclude_none=True)
    return db.update(id,updates,'concept')

@app.post('/api/concepts/{id}/merge')
def merge(id:str,data:dict):
    target_id=data.get('target_id')
    if target_id==id: raise ValueError('不能合并到自身')
    with db.connect() as c:
        source=db.get(id,'concept',c); target=db.get(target_id,'concept',c)
        if source.get('space_id')!=target.get('space_id'):raise ValueError('不能跨空间合并知识点')
        if source.get('merged_into') or target.get('merged_into'): raise ValueError('知识点已合并')
        target['citation_ids']=list(set(source.get('citation_ids',[])+target.get('citation_ids',[])))
        target['source_ids']=list(set(source.get('source_ids',[])+target.get('source_ids',[])))
        target['merge_notes']=target.get('merge_notes',[])+[{'title':source['title'],'definition':source['definition']}]
        db.put('concept',target,con=c)
        db.update(id,{'merged_into':target_id},con=c)
        for kind,fields in [('attempt',['concept_id']),('mistake',['concept_id']),('session',['concept_id']),('relation',['source','target'])]:
            for row in db.all(kind,c):
                changes={f:target_id for f in fields if row.get(f)==id}
                if changes: db.update(row['id'],changes,con=c)
    learning.recompute(target_id)
    return target

@app.get('/api/relations')
def relations(space_id:str|None=None,goal_id:str|None=None): return study.records('relation',space_id,goal_id)

@app.post('/api/relations')
def relation(data:dict):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        sid=study_api.validate_relation(data,c)
        r=db.put('relation',{k:data[k] for k in ('source','target','type')} | {'space_id':sid,'reason':data.get('reason','用户创建'),'confirmed':True,'status':'confirmed','created_by':'user','inferred':False,'confidence':None},con=c)
        db.link(r['id'],r['source'],'source',c);db.link(r['id'],r['target'],'target',c)
        return r

@app.patch('/api/relations/{id}')
def edit_relation(id:str,data:dict):return study_api.confirm(id,data)

@app.delete('/api/relations/{id}')
def remove_relation(id:str): return db.update(id,{'deleted':True},'relation')

@app.get('/api/skills')
def skills(): return learning.skills()

@app.put('/api/skills/{id}')
def edit_skill(id:str,data:dict):
    if id not in ('decompose','explain','review'): raise ValueError('未知 Skill')
    current=learning.skill(id)
    if data.get('reasoning') not in (*models.MODES,None):raise ValueError('未知推理档位')
    prompt=(learning.SKILL_DIR/f'{id}.md').read_text() if data.get('reset') else str(data.get('prompt',current['prompt']))
    if not 1<=len(prompt)<=10000: raise ValueError('提示词长度无效')
    profile_id=data.get('profile_id',current['profile_id'])
    if profile_id!='default': db.get(profile_id,'profile')
    return db.put('skill',{'skill_id':id,'version':current['version']+1,'prompt':prompt,'profile_id':data.get('profile_id',current['profile_id']),'reasoning':data.get('reasoning')})

@app.post('/api/skill-sessions')
def new_session(value:SessionIn): return learning.start(**value.model_dump())

@app.get('/api/skill-sessions')
def sessions(space_id:str|None=None,goal_id:str|None=None): return study.records('session',space_id,goal_id)

@app.get('/api/skill-sessions/{id}')
def get_session(id:str,before:str|None=None):
    s=db.get(id,'session')
    events=db.select('event',filters={'session_id':id},cursor=before,limit=30,descending=True)
    return dict(s,current_task_voided=assessments.task_voided(s),current_task_answered=learning.task_answered(s),events=list(reversed(events)),next_cursor=events[-1]['id'] if len(events)==30 else None,artifacts=db.select('artifact',filters={'session_id':id},limit=1000))

@app.post('/api/skill-sessions/{id}/events')
def event(id:str,value:EventIn):
    s=db.get(id,'session')
    if s.get('status')=='completed':raise HTTPException(409,'本次会话已结束，请先重新打开后继续')
    if s['skill_id']=='decompose': raise ValueError('资料拆解不接受教学事件')
    if value.action not in ('answer','hint','reveal','simpler','deeper','example','skip','continue'): raise ValueError('未知动作')
    if value.action=='answer' and assessments.task_voided(s):raise HTTPException(409,'这道题已作废，请生成新题后再作答；本题不会计分')
    if value.action=='answer' and s.get('current_task_stale'):raise HTTPException(409,'题目依据已改变，请继续生成新题后再作答；原草稿仍保留')
    if value.action=='answer' and not value.answer.strip(): raise ValueError('请先作答')
    key=f'event:{id}:{value.request_id}'
    with db.connect() as c:
        existing=c.execute('SELECT entity FROM request_keys WHERE key=?',(key,)).fetchone()
        if existing: return db.get(existing[0],con=c)
    if value.action=='answer' and not learning._task_text((s.get('current_task') or {}).get('question')):
        raise HTTPException(409,'当前没有可评分的题目，请继续生成新题后再作答；原草稿仍保留')
    if value.action=='answer' and learning.task_answered(s):raise HTTPException(409,'这道题已经提交过，请继续生成新题；本题不会重复计分')
    if any(j['status'] in ('queued','running') and j['payload'].get('session_id')==id for j in db.all('job')): raise ValueError('上一轮仍在处理中')
    job=jobs.enqueue('session',dict(session_id=id,action=value.action,answer=value.answer,reasoning=value.reasoning),key)
    db.update(id,{'job_id':job['id']})
    return job

@app.get('/api/artifacts')
def artifacts(): return db.all('artifact')

@app.get('/api/artifacts/{id}/review')
def artifact_review(id:str):
    from . import card_review
    return card_review.review(db.get(id,'artifact'))

@app.patch('/api/artifacts/{id}')
def artifact(id:str,data:dict):
    if data.get('accept'): return learning.accept_artifact(id,data.get('output'),data.get('decisions'),data.get('review_revision'))
    output=learning.Decomposition.model_validate(data['output']).model_dump()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=db.get(id,'artifact',c)
        if current['status']=='accepted': raise HTTPException(409,'已采纳，请在知识卡中编辑；本地修改仍保留')
        if data.get('revision')!=current.get('revision',1): raise HTTPException(409,'草稿已被另一页面更新，请核对最新版本；本地修改仍保留')
        return db.update(id,{'output':output},'artifact',con=c)

@app.get('/api/domains')
def domain_list(): return domains.packages()

@app.get('/api/goals')
def goals(space_id:str|None=None,goal_id:str|None=None): return study.records('goal',space_id,goal_id)

@app.post('/api/goals')
def create_goal(value:GoalIn):
    scope=study.scope(value.space_id,domain_id=value.domain_id)
    for id in value.source_ids:spaces.scoped_get(id,'source',scope['space_id'])
    goal=db.put('goal',value.model_dump()|scope)
    return db.update(goal['id'],{'path':learning.goal_path(goal)})

@app.get('/api/goals/{id}/path')
def goal_path(id:str): return learning.goal_path(db.get(id,'goal'))

@app.put('/api/goals/{id}/path')
def save_path(id:str,data:dict):
    goal=db.get(id,'goal')
    ids=data.get('concept_ids',[])
    allowed={x['concept_id'] for x in learning.goal_path(goal)}
    if len(set(ids))!=len(ids) or set(ids)!=allowed: raise ValueError('必须包含该目标的所有知识点且不重复')
    return db.update(id,{'order':ids},'goal')

@app.get('/api/exercises')
def exercise_list(): return exercises.public()

@app.get('/api/runner/status')
def runner_status(): return runner.status()

@app.put('/api/exercises/{id}/draft')
def draft(id:str,data:dict):
    if not exercises.get(id): raise ValueError('未知练习')
    code=str(data.get('code',''))
    if len(code)>50000: raise ValueError('代码过长')
    scope=study.scope(data.get('space_id'),data.get('goal_id'),data.get('concept_id'),data.get('session_id'))
    key='draft-'+id+('-'+scope['space_id']+'-'+(scope.get('goal_id') or 'free') if any(data.get(k) for k in ('space_id','goal_id','session_id','concept_id')) else '')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        try:old=db.get(key,'draft',c)
        except ValueError:old={'revision':0}
        if data.get('revision',0)!=old['revision']:raise HTTPException(409,'代码草稿版本冲突，请重新载入后合并')
        return db.put('draft',{**scope,'exercise_id':id,'code':code,'concept_id':data.get('concept_id'),'session_id':data.get('session_id')},id=key,con=c)

@app.get('/api/drafts')
def drafts(space_id:str|None=None,goal_id:str|None=None): return study.records('draft',space_id,goal_id)

@app.post('/api/exercises/{id}/runs')
def run(id:str,value:RunIn):
    if not exercises.get(id): raise ValueError('未知练习')
    scope=study.scope(value.space_id,value.goal_id,value.concept_id,value.session_id)
    if scope['domain_id']!='automation-testing':raise ValueError('代码实训需要自动化测试空间')
    return jobs.enqueue('run',dict(**scope,**assessments.run_task_scope(value.session_id,value.task_id,id),session_id=value.session_id,exercise_id=id,code=value.code,concept_id=value.concept_id),f'run:{id}:{scope["space_id"]}:{scope.get("goal_id")}:{value.request_id}')

@app.get('/api/runs')
def runs(space_id:str|None=None,goal_id:str|None=None): return study.records('run',space_id,goal_id)

@app.get('/api/attempts')
def attempts(space_id:str|None=None): return study.records('attempt',space_id)

@app.patch('/api/attempts/{id}')
def correct(id:str,data:dict):
    score=float(data['score'])
    if not 0<=score<=1 or not str(data.get('reason','')).strip(): raise ValueError('请填写 0–1 分数与纠正依据')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        a=db.update(id,{'corrected_score':score,'correction_reason':data['reason'],'correction_source':'user'},'attempt',c)
        if a.get('concept_id'): learning.recompute(a['concept_id'],c)
        return a

@app.get('/api/mistakes')
def mistakes(space_id:str|None=None,goal_id:str|None=None): return study.records('mistake',space_id,goal_id)

@app.post('/api/mistakes')
def manual_mistake(data:dict):
    required=('question','answer','expected')
    if any(not str(data.get(k,'')).strip() for k in required): raise ValueError('请填写题目、原答案和预期依据')
    if data.get('concept_id'): db.get(data['concept_id'],'concept')
    return db.put('mistake',study.scope(data.get('space_id'),data.get('goal_id'),data.get('concept_id'))|{k:data.get(k) for k in (*required,'concept_id')}|{'status':'pending_analysis','error_type':'unknown'})

@app.patch('/api/mistakes/{id}')
def mistake_edit(id:str,data:dict):
    if data.get('error_type') not in ('concept','application','syntax','assertion','design','environment','unknown'): raise ValueError('错误类型无效')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        mistake=db.update(id,{'error_type':data['error_type'],'user_correction':str(data.get('reason',''))},'mistake',c)
        if mistake.get('attempt_id'):
            attempt=db.get(mistake['attempt_id'],'attempt',c)
            db.update(attempt['id'],{'error_type':data['error_type'],'original_error_type':attempt.get('original_error_type',attempt.get('error_type')),'classification_correction':str(data.get('reason',''))},con=c)
            if attempt.get('concept_id'): learning.recompute(attempt['concept_id'],c)
        return mistake

@app.get('/api/flashcards')
def flashcards(space_id:str|None=None):
    return [{'id':c['id'],'title':c['title'],'question':c['question'],'answer':c.get('definition',''),'citation_ids':c.get('citation_ids',[])} for c in study.records('concept',space_id) if c.get('question') and not c.get('merged_into')]

@app.get('/api/reviews')
def reviews(space_id:str|None=None):
    return [x for x in study.records('review',space_id) if not db.get(x['concept_id'],'concept').get('merged_into')]

@app.get('/api/jobs')
def list_jobs(space_id:str|None=None):
    active=db.select('job',filters={'status':'queued'},limit=1000)+db.select('job',filters={'status':'running'},limit=1000)
    latest=db.select('job',limit=30,descending=True)
    return sorted({j['id']:j for j in active+latest if not space_id or j.get('space_id')==space_id}.values(),key=lambda j:j['created'])

@app.get('/api/jobs/{id}')
def get_job(id:str): return db.get(id,'job')

@app.post('/api/jobs/{id}/cancel')
def cancel_job(id:str):
    job=db.get(id,'job')
    if job['status'] not in ('queued','running'): raise ValueError('任务已经结束')
    return db.update(id,{'cancel_requested':True,'status':'cancelled' if job['status']=='queued' else 'running'})

@app.post('/api/jobs/{id}/retry')
def retry_job(id:str):
    job=db.get(id,'job')
    if job['status'] not in ('failed','cancelled'): raise ValueError('仅可重试失败或取消的任务')
    return db.update(id,{'status':'queued','cancel_requested':False,'error':None})

@app.get('/api/mastery')
def mastery(space_id:str|None=None,goal_id:str|None=None): return study.records('mastery',space_id,goal_id)

@app.get('/api/overview')
def overview(space_id:str|None=None):
    if space_id:
        with db.connect() as c:
            counts={kind:c.execute("SELECT COUNT(*) FROM entities WHERE kind=? AND json_extract(data,'$.space_id')=? AND COALESCE(json_extract(data,'$.deleted'),0)=0",(kind,space_id)).fetchone()[0] for kind in ('concept','attempt','entry','collection')}
            average=c.execute("SELECT AVG(json_extract(data,'$.score')) FROM entities WHERE kind='mastery' AND json_extract(data,'$.space_id')=? AND json_extract(data,'$.evidence_count')>0",(space_id,)).fetchone()[0]
            due=c.execute("SELECT COUNT(*) FROM entities WHERE kind='review' AND json_extract(data,'$.space_id')=? AND json_extract(data,'$.due')<=?",(space_id,db.now())).fetchone()[0]
            unfiled=c.execute("SELECT COUNT(*) FROM entities e WHERE kind='entry' AND json_extract(e.data,'$.space_id')=? AND COALESCE(json_extract(e.data,'$.deleted'),0)=0 AND NOT EXISTS (SELECT 1 FROM links l JOIN entities col ON col.id=l.owner WHERE l.target=e.id AND l.relation='collection_item' AND col.kind='collection' AND COALESCE(json_extract(col.data,'$.deleted'),0)=0)",(space_id,)).fetchone()[0]
        return {'sources':len(spaces.source_ids(space_id)),'concepts':counts['concept'],'attempts':counts['attempt'],'entries':counts['entry'],'collections':counts['collection'],'unfiled_entries':unfiled,'average_mastery':round(average,3) if average is not None else None,'due':due}
    with db.connect() as c:
        active="SELECT id FROM entities WHERE kind='concept' AND json_extract(data,'$.merged_into') IS NULL"
        average=c.execute("SELECT AVG(json_extract(data,'$.score')) FROM entities WHERE kind='mastery' AND json_extract(data,'$.concept_id') IN ("+active+")").fetchone()[0]
        due=c.execute("SELECT COUNT(*) FROM entities WHERE kind='review' AND json_extract(data,'$.due')<=? AND json_extract(data,'$.concept_id') IN ("+active+")",(db.now(),)).fetchone()[0]
        attempts=c.execute("SELECT COUNT(*) FROM entities WHERE kind='attempt' AND COALESCE(json_extract(data,'$.deleted'),0)=0").fetchone()[0]
        return {'sources':db.count('source',c),'concepts':c.execute('SELECT COUNT(*) FROM ('+active+')').fetchone()[0],'attempts':attempts,'average_mastery':round(average or 0,3),'due':due}

@app.post('/api/sample')
def sample(data:dict|None=None): return ingest.import_bytes('自动化测试入门.md',(db.ROOT/'samples/自动化测试入门.md').read_bytes(),space_id=(data or {}).get('space_id'))

@app.get('/api/export')
def export():
    kinds=['card_decision','assessment_action','space','source_ref','entry','collection','collection_member','attachment','focus_session','exam_question','practice_session','exam_help','entry_suggestion','source','chunk','concept','relation','goal','session','event','artifact','legacy_attempt','attempt','run','mistake','mastery','review','skill','draft','answer_draft','learner_fact','summary','context_snapshot','conversation','preferences']
    with db.connect() as c:revisions=[dict(r) for r in c.execute('SELECT * FROM entity_revisions')]
    return {'version':4,'exported_at':db.now(),'data':{k:db.all(k) for k in kinds},'profile_revisions':revisions}

@app.get('/api/export/report')
def report_export():
    return Response(reporting.learning_report(),media_type='text/markdown',headers={'Content-Disposition':'attachment; filename=learning-report.md'})

@app.get('/api/export/archive')
def archive_export():
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('learning.json',json.dumps(export(),ensure_ascii=False,indent=2))
        z.writestr('learning-report.md',reporting.learning_report())
        for attachment in db.all('attachment'):
            storage=attachment.get('storage_id','')
            if len(storage)==32 and all(ch in '0123456789abcdef' for ch in storage):
                path=db.DATA/'attachments'/storage
                if path.is_file() and not path.is_symlink():z.write(path,'attachments/'+storage)
        for source in db.all('source'):
            raw=db.DATA/'originals'/source['id']
            if raw.is_file(): z.write(raw,'originals/'+source['id'])
    output.seek(0)
    return StreamingResponse(output,media_type='application/zip',headers={'Content-Disposition':'attachment; filename=learnloop-export.zip'})

if (db.ROOT/'dist/autumn').exists():
    app.mount('/autumn',StaticFiles(directory=db.ROOT/'dist/autumn'),name='autumn')

if (db.ROOT/'dist/assets').exists():
    app.mount('/assets',StaticFiles(directory=db.ROOT/'dist/assets'),name='assets')

@app.get('/')
def index():
    path=db.ROOT/'dist/index.html'
    if not path.exists(): raise HTTPException(503,'请先运行 npm run build')
    return FileResponse(path,headers={'Cache-Control':'no-cache'})
