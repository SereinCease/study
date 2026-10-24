import time
from . import db

def enqueue(task, payload, request_key=None):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if request_key:
            row=c.execute('SELECT entity FROM request_keys WHERE key=?',(request_key,)).fetchone()
            if row:
                return db.get(row[0],con=c)
        if task=='session' and c.execute("SELECT 1 FROM entities WHERE kind='job' AND json_extract(data,'$.task')='session' AND json_extract(data,'$.status') IN ('queued','running') AND json_extract(data,'$.payload.session_id')=? LIMIT 1",(payload.get('session_id'),)).fetchone():
            raise ValueError('上一轮仍在处理中')
        scope={k:payload[k] for k in ('space_id','goal_id') if payload.get(k)}
        for field,kind in [('session_id','session'),('source_id','source')]:
            if payload.get(field):
                try:parent=db.get(payload[field],kind,c)
                except ValueError:parent={}
                scope.update({k:parent[k] for k in ('space_id','goal_id') if parent.get(k) and not (field=='source_id' and k in scope)})
        job=db.put('job',{**scope,'task':task,'payload':payload,'status':'queued','progress':0,'cancel_requested':False,'checkpoint':{},'error':None},con=c)
        if request_key:
            c.execute('INSERT INTO request_keys VALUES(?,?)',(request_key,job['id']))
        return job

def check(id):
    if db.get(id)['cancel_requested']:
        raise Cancelled('任务已取消')

class Cancelled(RuntimeError): pass

def step(id, progress, **changes):
    check(id)
    return db.update(id,dict(progress=progress,**changes))

def recover():
    with db.connect() as c:
        for job in db.all('job',c):
            if job['status']=='running':
                workspace=job.get('workspace')
                if workspace:
                    from pathlib import Path
                    import shutil
                    folder=Path(workspace)
                    from .runner import WORKSPACES
                    if folder.parent.resolve()==WORKSPACES.resolve() and folder.name.startswith('learnloop-') and not folder.is_symlink():
                        shutil.rmtree(folder,ignore_errors=True)
                db.update(job['id'],{'status':'queued','error':'上次进程中断，等待恢复'},con=c)

def tick():
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT id FROM entities WHERE kind='job' AND json_extract(data,'$.status')='queued' ORDER BY CASE WHEN json_extract(data,'$.task') IN ('summarize','memory_index','cleanup') THEN 1 ELSE 0 END,created LIMIT 1").fetchone()
        if not row:
            return False
        job=db.get(row['id'],con=c)
        db.update(job['id'],{'status':'running'},con=c)
    try:
        check(job['id'])
        from . import ingest, learning, runner, memory, maintenance, study_api
        handlers={'relations':study_api.generate,'import':ingest.process,'decompose':learning.decompose,'session':learning.session_step,'run':runner.execute,'summarize':memory.summarize,'memory_index':memory.rebuild,'cleanup':maintenance.clean}
        result=handlers[job['task']](job)
        check(job['id'])
        db.update(job['id'],{'status':'completed','progress':100,'result':result,'finished':db.now()})
    except Cancelled:
        db.update(job['id'],{'status':'cancelled','finished':db.now()})
    except Exception as error:
        # Never persist raw provider responses/headers or arbitrary exception reprs.
        from .models import ModelError
        message=str(error) if isinstance(error,(ModelError,ValueError)) else '处理失败，请检查资料或运行环境并重试'
        db.update(job['id'],{'status':'failed','error':message[:600],'error_code':getattr(error,'code','processing'),'finished':db.now()})
    return True

def main():
    db.init()
    # One worker per data directory (prevents recovering another live worker).
    import fcntl
    lock=open(db.DATA/'worker.lock','w')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('已有 worker 正在运行')
    import signal
    def stop(signum,frame): raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    recover()
    from .maintenance import automatic
    automatic()
    checked=time.monotonic();index_checked=0
    while True:
        if not tick():
            if time.monotonic()-index_checked>60:
                from .memory import schedule_index,schedule_invalidated_summaries
                schedule_invalidated_summaries();schedule_index();index_checked=time.monotonic()
            if time.monotonic()-checked>3600:
                automatic();checked=time.monotonic()
            time.sleep(.5)

if __name__=='__main__': main()
