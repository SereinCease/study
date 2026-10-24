"""Explicit knowledge scopes; content can be referenced without copying bytes."""
import hashlib
import json
from . import db, domains

DEFAULTS = (
    {'id':'inbox','title':'知识收件箱','domain_id':'general','mode':'collect','system':True},
    {'id':'automation','title':'自动化测试','domain_id':'automation-testing','mode':'learn','system':True},
    {'id':'unassigned','title':'未归属历史记录','domain_id':'general','mode':'collect','system':True,'needs_assignment':True},
)
MODES={'collect','explore','learn'}


def ensure(con=None):
    if con is None:
        with db.connect() as c:return ensure(c)
    for value in DEFAULTS:
        if not con.execute('SELECT 1 FROM entities WHERE id=?',(value['id'],)).fetchone():
            db.put('space',dict(value,deleted=False),id=value['id'],con=con)
    return db.all('space',con)


def resolve(space_id=None, domain_id=None, con=None):
    if con is None:
        with db.connect() as c:return resolve(space_id,domain_id,c)
    if space_id is None:
        space_id={'automation-testing':'automation','general':'inbox',None:'inbox'}.get(domain_id)
        if not space_id:raise ValueError('此领域需要明确选择知识空间')
    space=db.get(space_id,'space',con)
    if space.get('deleted'):raise ValueError('知识空间已归档')
    if domain_id and space['domain_id']!=domain_id:raise ValueError('知识空间与领域不匹配')
    return space


def create(data):
    title=str(data.get('title','')).strip()
    if not title or len(title)>120:raise ValueError('空间名称需为 1–120 字')
    domain=data.get('domain_id','general')
    if not domains.get(domain):raise ValueError('领域模板未安装')
    mode=data.get('mode','collect')
    if mode not in MODES:raise ValueError('无效空间模式')
    return db.put('space',{'title':title,'domain_id':domain,'mode':mode,'description':str(data.get('description',''))[:2000],'deleted':False})


def source_ref_id(space_id, source_id):
    return 'source-ref-'+hashlib.sha256((space_id+'\0'+source_id).encode()).hexdigest()[:32]


def attach_source(space_id,source_id,name=None,url=None,con=None):
    if con is None:
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            return attach_source(space_id,source_id,name,url,c)
    space=resolve(space_id,con=con); source=db.get(source_id,'source',con)
    key=source_ref_id(space['id'],source_id)
    existing=con.execute('SELECT data FROM entities WHERE id=?',(key,)).fetchone()
    if existing:
        old=json.loads(existing[0])
        if not old.get('deleted'):
            origin={'name':name or source.get('name','资料'),'url':url or source.get('url')}
            origins=old.get('origins',[{'name':old.get('name'),'url':old.get('url')}])
            if origin not in origins:
                origins.append(origin)
                return db.update(key,{'origins':origins},con=con)
            return old
    value={'space_id':space['id'],'domain_id':space['domain_id'],'source_id':source_id,
           'name':name or source.get('name','资料'),'url':url or source.get('url'),
           'source_version':source.get('version',1),'deleted':False,
           'origins':[{'name':name or source.get('name','资料'),'url':url or source.get('url')}]}
    ref=db.put('source_ref',value,id=key,con=con)
    db.link(key,source_id,'source',con)
    return ref


def source_ids(space_id,con=None):
    if con is None:
        with db.connect() as c:return source_ids(space_id,c)
    return {json.loads(r[0])['source_id'] for r in con.execute("SELECT data FROM entities WHERE kind='source_ref' AND json_extract(data,'$.space_id')=? AND COALESCE(json_extract(data,'$.deleted'),0)=0",(space_id,))}


allowed_source_ids=source_ids


def assert_member(record,space_id,con=None):
    if con is None:
        with db.connect() as c:return assert_member(record,space_id,c)
    if record.get('deleted'):raise ValueError('关联记录已删除')
    if record.get('kind')=='chunk':
        assert_member(db.get(record['source_id'],'source',con),space_id,con)
        return record
    if record.get('kind')=='source':
        if record['id'] in source_ids(space_id,con):return record
    elif record.get('space_id')==space_id:return record
    raise ValueError('关联记录不属于当前知识空间')


def scoped_get(id,kind,space_id,con=None):
    if con is None:
        with db.connect() as c:return scoped_get(id,kind,space_id,c)
    return assert_member(db.get(id,kind,con),space_id,con)


def migrate_legacy(con):
    """Only explicit automation provenance is migrated; titles never infer scope."""
    kinds={'goal','session','event','attempt','mistake','run','conversation','summary','concept','relation','source','chunk','mastery','review','artifact','context_snapshot','draft','answer_draft','learner_fact','legacy_attempt'}
    records={r['id']:json.loads(r['data']) for r in con.execute('SELECT id,data FROM entities')}
    assigned={id:v['space_id'] for id,v in records.items() if v.get('space_id')}
    for id,v in records.items():
        if v.get('kind') in kinds and v.get('domain_id')=='automation-testing':assigned.setdefault(id,'automation')
    for _ in range(12):
        old=dict(assigned)
        for id,v in records.items():
            if v.get('kind') not in kinds:continue
            parents=[v.get(k) for k in ('goal_id','session_id','event_id','attempt_id','run_id','concept_id','source_id')]
            known={assigned[p] for p in parents if p in assigned}
            if id not in assigned and len(known)==1:assigned[id]=next(iter(known))
            # A goal/session explicitly establishes its concepts and sources.
            if assigned.get(id)=='automation' and v.get('kind') in ('goal','session'):
                for child in [v.get('concept_id'),*v.get('source_ids',[])]:
                    if child in records and child not in assigned:assigned[child]='automation'
            if v.get('kind')=='concept' and id not in assigned:
                refs=v.get('source_ids',[])
                if refs and all(assigned.get(x)=='automation' for x in refs):assigned[id]='automation'
        if assigned==old:break
    for id,v in records.items():
        if v.get('kind') not in kinds:continue
        if v.get('kind')=='learner_fact' and v.get('domain_id')=='global':continue
        space=assigned.get(id,'unassigned')
        if not v.get('space_id'):
            v.update(space_id=space,scope_migration='explicit_link' if space!='unassigned' else 'unassigned')
            con.execute('UPDATE entities SET data=? WHERE id=?',(json.dumps(v,ensure_ascii=False),id))
        con.execute('UPDATE evidence SET space_id=? WHERE id=?',(space,id))
        if v.get('kind')=='source':attach_source(space,id,con=con)
