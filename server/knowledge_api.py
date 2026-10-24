"""Knowledge capture and exam routes; host app supplies local-origin/token middleware."""
import io
import json
import zipfile
from typing import Literal,Any
from fastapi import APIRouter,UploadFile,File,Form,Query,Body
from fastapi.responses import FileResponse,Response
from pydantic import BaseModel,Field,ConfigDict,AliasChoices
from . import db,spaces,entries,exams

router=APIRouter(prefix='/api')
class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Revision(Strict):revision:int=Field(ge=1)
class SpaceIn(Strict):
    title:str=Field(min_length=1,max_length=120)
    domain_id:str='general'
    mode:Literal['collect','explore','learn']=Field(default='collect',validation_alias=AliasChoices('mode','default_mode'))
    description:str=''
class EntryIn(Strict):
    space_id:str='inbox'
    entry_type:Literal['note','excerpt','bookmark','ai_answer']='note'
    title:str=Field(default='',max_length=200)
    body:str=Field(default='',max_length=200000)
    tags:list[str]=[]
    goal_id:str|None=None
    concept_id:str|None=None
    session_id:str|None=None
    references:list[dict]=[]
    memory_enabled:bool=False
class EntryPatch(Strict):
    revision:int=Field(ge=1)
    title:str|None=Field(default=None,max_length=200)
    body:str|None=Field(default=None,max_length=200000)
    entry_type:Literal['note','excerpt','bookmark','ai_answer']|None=None
    tags:list[str]|None=None
    goal_id:str|None=None
    concept_id:str|None=None
    session_id:str|None=None
    references:list[dict]|None=None
    memory_enabled:bool|None=None
class CardNoteIn(Strict):
    space_id:str
    card_kind:Literal['source','concept']
    card_id:str=Field(min_length=1,max_length=200)
    request_id:str=Field(min_length=8,max_length=200)
class CollectionIn(Strict):
    space_id:str='inbox'
    title:str=Field(min_length=1,max_length=200)
    description:str=''
    entry_ids:list[str]=[]
    source_ids:list[str]=[]
class CollectionPatch(Strict):
    revision:int=Field(ge=1)
    title:str|None=Field(default=None,min_length=1,max_length=200)
    description:str|None=None
    entry_ids:list[str]|None=None
    source_ids:list[str]|None=None

@router.get('/spaces')
def space_list():return [x for x in db.all('space') if not x.get('deleted')]
@router.post('/spaces')
def space_create(value:SpaceIn):return spaces.create(value.model_dump())
@router.patch('/spaces/{id}')
def space_update(id:str,data:dict=Body(...)):
    allowed={'revision','title','description','mode'}
    if not set(data)<=allowed:raise ValueError('不支持修改空间标识或领域')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE');old=spaces.resolve(id,con=c);entries.checked_revision(old,data.get('revision'))
        changes={k:v for k,v in data.items() if k!='revision'}
        if 'mode' in changes and changes['mode'] not in spaces.MODES:raise ValueError('无效空间模式')
        if 'title' in changes and (not isinstance(changes['title'],str) or not changes['title'].strip() or len(changes['title'])>120):raise ValueError('空间名称需为 1–120 字')
        if 'description' in changes and (not isinstance(changes['description'],str) or len(changes['description'])>2000):raise ValueError('描述过长')
        return db.update(id,changes,con=c)
@router.get('/spaces/{id}/sources')
def source_refs(id:str):
    spaces.resolve(id)
    return [x for x in db.select('source_ref',filters={'space_id':id},limit=1000) if not x.get('deleted')]
@router.post('/spaces/{id}/sources')
def source_ref_add(id:str,data:dict=Body(...)):
    return spaces.attach_source(id,str(data.get('source_id','')),data.get('name'),data.get('url'))

@router.get('/entries')
def entry_list(space_id:str='inbox',query:str='',deleted:bool=False,limit:int=30,cursor:str|None=None,goal_id:str|None=None,session_id:str|None=None):
    return entries.page('entry',space_id,query,deleted,limit,cursor,{k:v for k,v in [('goal_id',goal_id),('session_id',session_id)] if v})
@router.post('/entries')
def entry_create(value:EntryIn):return entries.save(value.model_dump())
@router.post('/entries/from-card')
def entry_from_card(value:CardNoteIn):return entries.from_card(**value.model_dump())
@router.get('/entries/{id}')
def entry_detail(id:str,space_id:str|None=None):return entries.with_reference_status(entries.detail(id,space_id=space_id))
@router.patch('/entries/{id}')
def entry_update(id:str,value:EntryPatch):return entries.save(value.model_dump(exclude_unset=True),id)
@router.delete('/entries/{id}')
def entry_delete(id:str,revision:int|None=None,data:Revision|None=Body(None)):
    return entries.trash(id,revision if revision is not None else data.revision if data else None)
@router.post('/entries/{id}/restore')
def entry_restore(id:str,value:Revision):return entries.trash(id,value.revision,restore=True)
@router.get('/entries/{id}/revisions')
def entry_revisions(id:str):return entries.revisions(id)
@router.post('/entries/{id}/organize')
def entry_organize(id:str):return entries.organize(id)
@router.get('/entries/{id}/suggestions')
def entry_suggestions(id:str):
    entries.detail(id)
    return {'items':db.select('entry_suggestion',filters={'entry_id':id},limit=30,descending=True)}
@router.post('/entries/{id}/suggestions/{suggestion_id}/accept')
def entry_accept_suggestion(id:str,suggestion_id:str,value:Revision):return entries.accept_suggestion(id,suggestion_id,value.revision)
@router.post('/entries/{id}/revisions/{target_revision}/restore')
def entry_restore_revision(id:str,target_revision:int,value:Revision):return entries.restore_revision(id,target_revision,value.revision)
@router.post('/entries/{id}/attachments')
async def entry_attach(id:str,file:UploadFile=File(...),revision:int=Form(...)):
    data=await file.read(entries.MAX_ATTACHMENT+1)
    return entries.add_attachment(id,file.filename or 'attachment',data,revision)
@router.get('/entries/{id}/attachments/{attachment_id}')
def entry_attachment(id:str,attachment_id:str):
    value,path=entries.attachment(id,attachment_id)
    with path.open('rb') as stream:head=stream.read(16)
    mime='image/png' if head.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if head.startswith(b'\xff\xd8\xff') else 'image/gif' if head[:6] in (b'GIF87a',b'GIF89a') else 'image/webp' if head[:4]==b'RIFF' and head[8:12]==b'WEBP' else 'application/octet-stream'
    return FileResponse(path,filename=value['name'],media_type=mime,content_disposition_type='inline' if mime.startswith('image/') else 'attachment',headers={'Content-Security-Policy':"default-src 'none'; sandbox"})
@router.delete('/entries/{id}/attachments/{attachment_id}')
def entry_detach(id:str,attachment_id:str,revision:int):return entries.detach_attachment(id,attachment_id,revision)
@router.get('/entries/{id}/export')
def entry_export(id:str):return Response(entries.export_zip(id),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="note-{id}.zip"'})

@router.get('/collections')
def collection_list(space_id:str='inbox',query:str='',deleted:bool=False,limit:int=30,cursor:str|None=None):
    return entries.page('collection',space_id,query,deleted,limit,cursor)
@router.post('/collections')
def collection_create(value:CollectionIn):return entries.save(value.model_dump(),kind='collection')
@router.get('/collections/{id}')
def collection_detail(id:str,space_id:str|None=None):return entries.detail(id,'collection',space_id)
@router.patch('/collections/{id}')
def collection_update(id:str,value:CollectionPatch):return entries.save(value.model_dump(exclude_unset=True),id,'collection')
@router.delete('/collections/{id}')
def collection_delete(id:str,revision:int|None=None,data:Revision|None=Body(None)):
    return entries.trash(id,revision if revision is not None else data.revision if data else None,kind='collection')
@router.post('/collections/{id}/restore')
def collection_restore(id:str,value:Revision):return entries.trash(id,value.revision,restore=True,kind='collection')
@router.get('/collections/{id}/revisions')
def collection_revisions(id:str):return entries.revisions(id,'collection')
@router.get('/collections/{id}/export')
def collection_export(id:str):
    collection=entries.detail(id,'collection');buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('collection.json',json.dumps(collection,ensure_ascii=False,indent=2))
        for eid in collection.get('entry_ids',[]):
            entry=entries.detail(eid,space_id=collection['space_id'])
            if not entry.get('deleted'):z.writestr('notes/'+eid+'.zip',entries.export_zip(eid))
        z.writestr('sources.json',json.dumps([db.get(sid,'source') for sid in collection.get('source_ids',[])],ensure_ascii=False,indent=2))
    return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="collection-{id}.zip"'})

class QuestionIn(Strict):
    space_id:str
    title:str=''
    prompt:str=Field(min_length=1,max_length=30000)
    question_type:Literal['single_choice','multiple_choice','true_false','fill_blank','short_answer','essay']
    options:list[dict]=[]
    answer:str|list[str]|list[list[str]]|bool|None=None
    rubric:list[dict]=[]
    max_score:float=Field(default=100,gt=0,le=1000)
    concept_id:str|None=None
    goal_id:str|None=None
    references:list[dict]=[]
    exam_year:str|int|None=None
    region:str=''
    syllabus_version:str=''
    is_transfer:bool=False
    time_limit_seconds:int|None=Field(default=None,ge=1,le=86400)
class AttemptIn(Strict):
    answer:str|list[str]|bool
    request_id:str=Field(min_length=8,max_length=200)
    hint_level:int=Field(default=0,ge=0,le=3)
    answer_seen:bool=False
    is_transfer:bool=False
    practice_session_id:str|None=None
@router.get('/exams/questions')
def question_list(space_id:str,query:str='',limit:int=30,cursor:str|None=None):
    return entries.page('exam_question',space_id,query,False,limit,cursor)
@router.post('/exams/questions')
def question_create(value:QuestionIn):return exams.save(value.model_dump())
@router.get('/exams/questions/{id}')
def question_detail(id:str):return exams.question(id)
@router.patch('/exams/questions/{id}')
def question_update(id:str,data:dict=Body(...)):
    allowed=set(QuestionIn.model_fields)|{'revision'}
    if not set(data)<=allowed:raise ValueError('无效题目字段')
    return exams.save(data,id)
@router.post('/exams/questions/{id}/verify')
def question_verify(id:str,value:Revision):return exams.verify(id,value.revision)
@router.get('/exams/questions/{id}/practice')
def question_practice(id:str):return exams.practice(id)
@router.post('/exams/questions/{id}/start')
def question_start(id:str):return exams.start_practice(id)
@router.post('/exams/questions/{id}/reveal')
def question_reveal(id:str):return exams.reveal(id)
@router.get('/exams/questions/{id}/attempts')
def question_attempts(id:str):
    exams.question(id)
    return {'items':db.select('attempt',filters={'question_id':id},limit=100,descending=True)}
@router.post('/exams/questions/{id}/attempts')
def question_submit(id:str,value:AttemptIn):return exams.submit(id,value.model_dump())
