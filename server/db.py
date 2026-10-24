"""Versioned local storage. Domain entities use stable UUIDs; links enforce references."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('LEARNLOOP_DATA_DIR', ROOT / 'server/data'))

# Index progress is operational metadata, not a change to the cited material.
SOURCE_MATERIAL_FIELDS=('hash','version','name','url','path','content','text','space_id','domain_id','deleted','memory_enabled')

def source_material_changed(previous,value):
    defaults={'version':1,'deleted':False,'memory_enabled':True}
    return any(previous.get(field,defaults.get(field))!=value.get(field,defaults.get(field)) for field in SOURCE_MATERIAL_FIELDS)

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(DATA / 'learnloop.sqlite3', timeout=30)
    (DATA/'learnloop.sqlite3').chmod(0o600)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA journal_mode=WAL')
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()

def init():
    # API and worker may start together; serialize all version transitions.
    import fcntl
    DATA.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (DATA/'.migration.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        _init_schema()


def _init_schema():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL CHECK(json_valid(data)), created TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS entities_kind ON entities(kind);
        CREATE TABLE IF NOT EXISTS entity_revisions(id TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(id,revision));
        CREATE TABLE IF NOT EXISTS links(owner TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE, target TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE, relation TEXT NOT NULL, PRIMARY KEY(owner,target,relation));
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, tokens);
        CREATE TABLE IF NOT EXISTS vectors(id TEXT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE, model TEXT NOT NULL, vector TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS request_keys(key TEXT PRIMARY KEY, entity TEXT NOT NULL REFERENCES entities(id));
        ''')
        c.executescript('''
        CREATE INDEX IF NOT EXISTS entities_page ON entities(kind,created,id);
        CREATE INDEX IF NOT EXISTS entities_session ON entities(kind,json_extract(data,'$.session_id'),created,id);
        CREATE INDEX IF NOT EXISTS entities_concept ON entities(kind,json_extract(data,'$.concept_id'),created,id);
        CREATE INDEX IF NOT EXISTS entities_source ON entities(kind,json_extract(data,'$.source_id'),created,id);
        CREATE INDEX IF NOT EXISTS entities_event ON entities(kind,json_extract(data,'$.event_id'));
        CREATE INDEX IF NOT EXISTS entities_attempt ON entities(kind,json_extract(data,'$.attempt_id'));
        CREATE INDEX IF NOT EXISTS entities_job ON entities(kind,json_extract(data,'$.job_id'));
        CREATE INDEX IF NOT EXISTS entities_status ON entities(kind,json_extract(data,'$.status'),created);
        CREATE TABLE IF NOT EXISTS memory_state(id INTEGER PRIMARY KEY CHECK(id=1),epoch INTEGER NOT NULL);
        INSERT OR IGNORE INTO memory_state VALUES(1,0);
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
          kind TEXT NOT NULL,session_id TEXT,concept_id TEXT,domain_id TEXT NOT NULL,created TEXT NOT NULL,
          revision INTEGER NOT NULL,active INTEGER NOT NULL,text TEXT NOT NULL,hash TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS evidence_scope ON evidence(active,domain_id,concept_id,created);
        CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(id UNINDEXED,tokens);
        CREATE TABLE IF NOT EXISTS evidence_vector_staging(job_id TEXT NOT NULL,id TEXT NOT NULL,model TEXT NOT NULL,hash TEXT NOT NULL,vector BLOB NOT NULL,PRIMARY KEY(job_id,id));
        CREATE TABLE IF NOT EXISTS evidence_vector_history(id TEXT NOT NULL,model TEXT NOT NULL,hash TEXT NOT NULL,vector BLOB NOT NULL,retired TEXT NOT NULL,PRIMARY KEY(id,model,hash));
        CREATE TABLE IF NOT EXISTS evidence_vectors(id TEXT PRIMARY KEY REFERENCES evidence(id) ON DELETE CASCADE,
          model TEXT NOT NULL,hash TEXT NOT NULL,vector BLOB NOT NULL);
        ''')
        if 'space_id' not in {r['name'] for r in c.execute('PRAGMA table_info(evidence)')}:
            c.execute("ALTER TABLE evidence ADD COLUMN space_id TEXT NOT NULL DEFAULT 'unassigned'")
        if not c.execute('SELECT 1 FROM schema_version WHERE version=1').fetchone():
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for old, kind in [('sources','source'),('concepts','concept'),('attempts','legacy_attempt')]:
                if old not in tables:
                    continue
                for row in c.execute(f'SELECT * FROM {old}').fetchall():
                    value = dict(row)
                    legacy_id = value.pop('id')
                    value.update(id=f'legacy-{kind}-{legacy_id}', kind=kind, legacy=True, evidence_incomplete=True)
                    if kind=='concept':
                        value['title']=value.get('title') or value.get('label') or '历史知识点'
                        value.setdefault('definition','')
                        value.setdefault('citation_ids',[])
                        value.setdefault('source_ids',[])
                    if kind=='source':
                        value['name']=value.get('name') or value.get('title') or '历史资料'
                        value['status']='legacy'
                        value['warning']='历史资料缺少可验证原文，请重新导入'
                    if kind=='legacy_attempt':
                        value['score']=None
                        value['evaluator']='legacy_incomplete'
                    c.execute('INSERT OR IGNORE INTO entities VALUES(?,?,?,?)', (value['id'],kind,json.dumps(value,ensure_ascii=False),now()))
            c.execute('INSERT INTO schema_version VALUES(1,?)',(now(),))
        if not c.execute('SELECT 1 FROM schema_version WHERE version=2').fetchone():
            from .memory import index_record
            for row in c.execute("SELECT data FROM entities WHERE kind IN ('event','attempt','mistake','run','conversation','summary')").fetchall():
                index_record(json.loads(row[0]),c)
            c.execute('INSERT INTO schema_version VALUES(2,?)',(now(),))
        if not c.execute('SELECT 1 FROM schema_version WHERE version=3').fetchone():
            from array import array
            for row in c.execute("SELECT id,vector FROM vectors WHERE typeof(vector)='text'").fetchall():
                c.execute('UPDATE vectors SET vector=? WHERE id=?',(array('f',json.loads(row['vector'])).tobytes(),row['id']))
            c.execute('INSERT INTO schema_version VALUES(3,?)',(now(),))
        c.execute('INSERT OR IGNORE INTO schema_version VALUES(4,?)',(now(),))
        c.executescript('''
        CREATE INDEX IF NOT EXISTS entities_space ON entities(kind,json_extract(data,'$.space_id'),created,id);
        CREATE INDEX IF NOT EXISTS entities_goal ON entities(kind,json_extract(data,'$.goal_id'),created,id);
        CREATE INDEX IF NOT EXISTS entities_entry ON entities(kind,json_extract(data,'$.entry_id'),created,id);
        CREATE INDEX IF NOT EXISTS entities_source_ref ON entities(kind,json_extract(data,'$.space_id'),json_extract(data,'$.source_id'));
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS evidence_space ON evidence(active,space_id,concept_id,created)')
        from . import spaces
        spaces.ensure(c)
        if not c.execute('SELECT 1 FROM schema_version WHERE version=5').fetchone():
            spaces.migrate_legacy(c)
            c.execute('INSERT INTO schema_version VALUES(5,?)',(now(),))

def put(kind, data, id=None, con=None):
    value = dict(data)
    value.update(id=id or value.get('id') or uid(), kind=kind)
    value.setdefault('created', now())
    if con is None:
        with connect() as c:
            return put(kind,value,con=c)
    existing=con.execute('SELECT kind,data FROM entities WHERE id=?',(value['id'],)).fetchone()
    if existing and existing['kind']!=kind: raise ValueError('ID 已被其他类型记录使用')
    scoped={'goal','session','event','attempt','mistake','run','conversation','summary','concept','relation','source','chunk','mastery','review','artifact','context_snapshot','draft','answer_draft','learner_fact','legacy_attempt'}
    if kind in scoped and not value.get('space_id') and not (kind=='learner_fact' and value.get('domain_id')=='global'):
        candidates=set()
        for parent_id in [value.get(f) for f in ('goal_id','session_id','event_id','attempt_id','run_id','concept_id','source_id')]+value.get('source_ids',[]):
            if not parent_id:continue
            parent=con.execute('SELECT data FROM entities WHERE id=?',(parent_id,)).fetchone()
            parent=json.loads(parent[0]) if parent else {}
            if parent.get('space_id'):candidates.add(parent['space_id'])
        if len(candidates)==1:value['space_id']=next(iter(candidates))
        elif not candidates and value.get('domain_id')=='automation-testing':value['space_id']='automation'
        else:value['space_id']='unassigned'
    if existing and kind in ('learner_fact','entry','collection','exam_question'):
        previous=json.loads(existing['data']);con.execute('INSERT OR IGNORE INTO entity_revisions VALUES(?,?,?,?)',(value['id'],previous.get('revision',0),existing['data'],now()))
    value['revision']=(json.loads(existing['data']).get('revision',0) if existing else 0)+1
    value['updated']=now()
    con.execute('INSERT INTO entities VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (value['id'],kind,json.dumps(value,ensure_ascii=False),value['created']))
    if kind in ('event','attempt','mistake','run','conversation','summary','learner_fact'):
        from .memory import index_record, invalidate
        index_record(value,con)
        if existing and kind!='summary': invalidate(value,con)
    if existing and (kind in ('chunk','concept') or kind=='source' and source_material_changed(json.loads(existing['data']),value)):
        from .memory import invalidate_material
        invalidate_material(value,con)
    return value

def get(id, kind=None, con=None):
    if con is None:
        with connect() as c:
            return get(id,kind,c)
    row = con.execute('SELECT kind,data FROM entities WHERE id=?',(id,)).fetchone()
    if not row or kind and row['kind'] != kind:
        raise ValueError('记录不存在或类型不匹配')
    return json.loads(row['data'])

def all(kind, con=None):
    if con is None:
        with connect() as c:
            return all(kind,c)
    return [json.loads(r[0]) for r in con.execute('SELECT data FROM entities WHERE kind=? ORDER BY created,id',(kind,))]

def update(id, changes, kind=None, con=None):
    if con is None:
        with connect() as c:
            return update(id,changes,kind,c)
    value = get(id,kind,con)
    value.update({k:v for k,v in changes.items() if k not in ('id','kind','created')})
    return put(value['kind'],value,con=con)

def link(owner,target,relation,con):
    con.execute('INSERT OR IGNORE INTO links VALUES(?,?,?)',(owner,target,relation))

def select(kind, *, filters=None, limit=30, cursor=None, descending=False, con=None):
    """Bounded keyset queries; field names are an internal allowlist."""
    if con is None:
        with connect() as c: return select(kind,filters=filters,limit=limit,cursor=cursor,descending=descending,con=c)
    where=['kind=?'];args=[kind]
    for field,value in (filters or {}).items():
        if field not in ('session_id','concept_id','source_id','job_id','status','skill_id','domain_id','space_id','goal_id','entry_id','collection_id','deleted','entry_type','question_id'): raise ValueError('无效查询字段')
        where.append(f"json_extract(data,'$.{field}') IS ?");args.append(value)
    if cursor:
        anchor=get(cursor,kind,con)
        where.append('(created,id) '+('<' if descending else '>')+' (?,?)');args.extend([anchor['created'],anchor['id']])
    order='DESC' if descending else 'ASC'
    args.append(min(max(int(limit),1),1000))
    return [json.loads(r[0]) for r in con.execute(f"SELECT data FROM entities WHERE {' AND '.join(where)} ORDER BY created {order},id {order} LIMIT ?",args)]

def count(kind,con=None):
    if con is None:
        with connect() as c:return count(kind,c)
    return con.execute('SELECT COUNT(*) FROM entities WHERE kind=?',(kind,)).fetchone()[0]
