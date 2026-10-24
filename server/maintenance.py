"""Conservative local maintenance: never delete originals, answers or credentials."""
import fcntl,json,os,shutil,time
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
from . import db

@contextmanager
def lock():
    db.DATA.mkdir(exist_ok=True,parents=True)
    with open(db.DATA/'maintenance.lock','a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('索引或存储维护正在运行，请稍后重试')
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)

def size(path):
    if path.is_symlink():return 0
    if path.is_file():return path.stat().st_size
    return sum(x.stat().st_size for x in path.rglob('*') if x.is_file() and not x.is_symlink()) if path.exists() else 0

def stats():
    with db.connect() as c:
        evidence=c.execute('SELECT COUNT(*) FROM evidence WHERE active=1').fetchone()[0]
        vectors=c.execute('SELECT COUNT(*) FROM evidence_vectors').fetchone()[0]
        tables=[dict(r) for r in c.execute('SELECT kind,COUNT(*) AS count,SUM(length(data)) AS bytes FROM entities GROUP BY kind')]
    return {'database_bytes':size(db.DATA/'learnloop.sqlite3'),'wal_bytes':size(db.DATA/'learnloop.sqlite3-wal'),'original_bytes':size(db.DATA/'originals'),'model_bytes':size(db.DATA/'models'),'cache_bytes':size(db.DATA/'cache'),'evidence_count':evidence,'vector_count':vectors,'entities':tables,'last_runs':db.select('maintenance',limit=10,descending=True),'policy':{'temporary_hours':24,'cache_days':7,'old_index_days':7,'log_days':30,'job_days':90}}

def candidates():
    now=time.time();files=[]
    active=db.select('job',filters={'status':'running'},limit=1000)+db.select('job',filters={'status':'queued'},limit=1000)
    referenced={str(Path(j['workspace']).resolve()) for j in active if j.get('workspace')}
    # Dedicated directories only. No general recursive deletion of the data directory.
    for folder,days in [('cache',7),('logs',30),('old-indexes',7),('runner-workspaces',1)]:
        root=db.DATA/folder
        if not root.exists() or root.is_symlink():continue
        for p in root.iterdir():
            if p.is_symlink() or str(p.resolve()) in referenced:continue
            if folder=='runner-workspaces' and active:continue
            if folder=='old-indexes' and active:continue
            if p.stat().st_mtime<now-days*86400:files.append({'path':str(p.relative_to(db.DATA)),'bytes':size(p),'reason':f'超过 {days} 天，且无活动引用'})
    with db.connect() as c:
        old=(datetime.now(timezone.utc)-timedelta(days=90)).isoformat()
        jobs=[r[0] for r in c.execute("SELECT id FROM entities WHERE kind='job' AND created<? AND json_extract(data,'$.status') IN ('completed','failed','cancelled') AND COALESCE(json_extract(data,'$.compacted'),0)=0",(old,))]
        retired=c.execute('SELECT COUNT(*) FROM evidence_vector_history WHERE retired<?',((datetime.now(timezone.utc)-timedelta(days=7)).isoformat(),)).fetchone()[0]
        orphans=c.execute('SELECT COUNT(*) FROM evidence_vectors v JOIN evidence e ON e.id=v.id WHERE e.active=0 OR v.hash!=e.hash').fetchone()[0]
    return {'files':files,'bytes':sum(f['bytes'] for f in files),'compact_job_ids':jobs,'obsolete_vectors':orphans,'retired_vectors':retired,'protected':'原始资料、答案、错题、报告、摘要引用和密钥不参与自动删除'}

def clean(job=None):
    with lock():
        plan=candidates();removed=[]
        for item in plan['files']:
            p=db.DATA/item['path']
            if not p.exists() or p.is_symlink():continue
            if p.parent.resolve().parent!=db.DATA.resolve():continue
            if p.is_dir():shutil.rmtree(p)
            else:p.unlink()
            removed.append(item)
        with db.connect() as c:
            c.execute('DELETE FROM evidence_vector_history WHERE retired<?',((datetime.now(timezone.utc)-timedelta(days=7)).isoformat(),))
            c.execute("DELETE FROM evidence_vector_staging WHERE job_id NOT IN (SELECT id FROM entities WHERE kind='job' AND json_extract(data,'$.status') IN ('queued','running'))")
            c.execute('DELETE FROM evidence_vectors WHERE id IN (SELECT v.id FROM evidence_vectors v JOIN evidence e ON e.id=v.id WHERE e.active=0 OR v.hash!=e.hash)')
            for id in plan['compact_job_ids']:
                row=db.get(id,'job',c)
                # Keep result, idempotency keys, owner IDs, and execution status.
                row['payload']={k:v for k,v in row.get('payload',{}).items() if k not in ('code','answer')}
                row['checkpoint']={};row['compacted']=True
                db.put('job',row,con=c)
            return db.put('maintenance',{'removed':removed,'reclaimed_bytes':sum(x['bytes'] for x in removed),'compacted_jobs':len(plan['compact_job_ids']),'obsolete_vectors':plan['obsolete_vectors'],'status':'completed'},con=c)

def automatic():
    previous=db.select('maintenance',limit=1,descending=True)
    if previous and (datetime.now(timezone.utc)-datetime.fromisoformat(previous[0]['created'])).total_seconds()<86400:return
    try:clean()
    except ValueError:pass
