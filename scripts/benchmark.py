"""Scoped scale benchmark in an independent temporary SQLite database.
Synthetic document/event vectors measure computation, NOT retrieval quality.
No cloud model request, no personal data mutation, no model download.
"""
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from fastapi.testclient import TestClient
from server import db, ingest, spaces
from server.app import app

QUERY='fixture teardown 资源释放'
SPACE='automation'

def hardware():
    def sysctl(key):
        try:return subprocess.check_output(['/usr/sbin/sysctl','-n',key],text=True).strip()
        except (OSError,subprocess.CalledProcessError):return None
    return {'os':platform.platform(),'machine':platform.machine(),'cpu':sysctl('machdep.cpu.brand_string') or platform.processor(),
        'logical_cpus':sysctl('hw.logicalcpu'),'memory_bytes':sysctl('hw.memsize'),'python':platform.python_version(),
        'numpy':version('numpy'),'torch':version('torch'),'sqlite':__import__('sqlite3').sqlite_version}

def main():
    original=db.DATA
    output=db.ROOT/'reports/performance-v4.json'
    # Load from the already installed personal model cache, without opening its database.
    ingest._encoder=None
    start=time.perf_counter();query=ingest.vector(QUERY);cold=time.perf_counter()-start
    warm=[]
    for _ in range(10):
        start=time.perf_counter();ingest.vector(QUERY);warm.append((time.perf_counter()-start)*1000)
    results={'measured_at':datetime.now(timezone.utc).isoformat(),'hardware':hardware(),
        'dataset':{'source_chunks':10000,'learning_events':100000,'space_id':SPACE,'vector_dimensions':len(query),'vector_seed':17,
          'vectors':'seeded normalized synthetic vectors; every 1000th row matches the real query embedding'},
        'measurement_notes':['Actual local CPU query encoder; no cloud latency or network calls.',
          'First encoder call is process-cold; OS filesystem caches were not flushed.',
          'first_query_ms is the first measured request after seeding; SQLite/OS caches may already be warm.',
          'Subsequent samples are sequential requests in one process using FastAPI TestClient; not a concurrent load test.',
          'Background desktop workloads are not controlled. These results do not measure retrieval quality.'],
        'cold_encoder_seconds':round(cold,3),'warm_encoder':{'median_ms':round(statistics.median(warm),2),'p95_ms':round(float(np.percentile(warm,95)),2),'runs':len(warm)},
        'targets':{'paginated_list_p95_ms':300,'local_hybrid_search_p95_ms':1500},'queries':{}}
    rng=np.random.default_rng(17);vector=np.asarray(query,dtype=np.float32)
    try:
        with tempfile.TemporaryDirectory(prefix='learnloop-benchmark-v4-') as folder:
            db.DATA=Path(folder);db.init();spaces.ensure()
            start=time.perf_counter()
            source=db.put('source',{'name':'性能基线教程','status':'ready','space_id':SPACE})
            spaces.attach_source(SPACE,source['id'])
            epoch=datetime(2025,1,1,tzinfo=timezone.utc)
            with db.connect() as c:
                for i in range(10000):
                    chunk=db.put('chunk',{'source_id':source['id'],'space_id':SPACE,'text':f'{QUERY} {i}','location':{'line_start':i+1}},id=f'chunk-{i:05}',con=c)
                    c.execute('INSERT INTO chunks_fts(id,tokens) VALUES(?,?)',(chunk['id'],ingest.tokens(chunk['text'])))
                    v=rng.normal(size=len(query)).astype(np.float32);v/=np.linalg.norm(v)
                    if i%1000==0:v=vector
                    c.execute('INSERT INTO vectors(id,model,vector) VALUES(?,?,?)',(chunk['id'],ingest.MODEL,v.tobytes()))
                for i in range(100000):
                    e={'id':f'event-{i:06}','kind':'event','revision':1,'created':(epoch+timedelta(seconds=i)).isoformat(),
                       'space_id':SPACE,'session_id':f'session-{i%100}','domain_id':'automation-testing','action':'answer',
                       'answer':f'{QUERY} 第{i}轮','output':{'explanation':'合成性能记录；AI 反馈不计作掌握证据'}}
                    text=json.dumps(e,ensure_ascii=False);digest=hashlib.sha256(text.encode()).hexdigest()
                    c.execute('INSERT INTO entities(id,kind,data,created) VALUES(?,?,?,?)',(e['id'],'event',text,e['created']))
                    c.execute('INSERT INTO evidence(id,kind,session_id,concept_id,domain_id,created,revision,active,text,hash,space_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (e['id'],'event',e['session_id'],None,'automation-testing',e['created'],1,1,text,digest,SPACE))
                    c.execute('INSERT INTO evidence_fts(id,tokens) VALUES(?,?)',(e['id'],ingest.tokens(e['answer'])))
                    v=rng.normal(size=len(query)).astype(np.float32);v/=np.linalg.norm(v)
                    if i%1000==0:v=vector
                    c.execute('INSERT INTO evidence_vectors(id,model,hash,vector) VALUES(?,?,?,?)',(e['id'],ingest.MODEL,digest,v.tobytes()))
            results['seeding_seconds']=round(time.perf_counter()-start,3)
            with TestClient(app) as client:
                def get(path,params):
                    r=client.get(path,params=params);r.raise_for_status();return r.json()
                def measure(name,call,n,validate,target=None):
                    t=time.perf_counter();first=call();first_ms=(time.perf_counter()-t)*1000;validate(first)
                    samples=[]
                    for _ in range(n):
                        t=time.perf_counter();value=call();samples.append((time.perf_counter()-t)*1000);validate(value)
                    p95=float(np.percentile(samples,95))
                    results['queries'][name]={'first_query_ms':round(first_ms,2),'median_ms':round(statistics.median(samples),2),'p95_ms':round(p95,2),
                       'min_ms':round(min(samples),2),'max_ms':round(max(samples),2),'runs':n,'samples_ms':[round(s,2) for s in samples],
                       'target_ms':target,'target_met':p95<target if target else None}
                def check_records(value):
                    assert len(value['items'])==30 and all(e['space_id']==SPACE for e in value['items'])
                def check_sources(value):
                    assert value and all(e['source_id']==source['id'] for e in value)
                def check_evidence(value):
                    assert value['items'] and '本地语义' in value['modes'] and not value['warning']
                measure('scoped_paginated_event_list',lambda:get('/api/records/event',{'limit':30,'space_id':SPACE}),30,check_records,300)
                measure('scoped_overview',lambda:get('/api/overview',{'space_id':SPACE}),20,lambda v:v['sources']==1)
                measure('scoped_source_hybrid_search',lambda:get('/api/search',{'q':QUERY,'space_id':SPACE}),15,check_sources,1500)
                measure('scoped_evidence_hybrid_search',lambda:get('/api/evidence/search',{'q':QUERY,'space_id':SPACE}),15,check_evidence,1500)
                assert not get('/api/records/event',{'space_id':'inbox'})['items']
                assert not get('/api/search',{'q':QUERY,'space_id':'inbox'})
                assert not get('/api/evidence/search',{'q':QUERY,'space_id':'inbox'})['items']
                results['scope_smoke_checks']='inbox has zero records, source hits and evidence hits; automation filters validated'
            results['database_bytes']=(db.DATA/'learnloop.sqlite3').stat().st_size
            results['temporary_database_removed']=True
    finally:db.DATA=original
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in results.items() if k!='queries'},ensure_ascii=False,indent=2))
    print(json.dumps({name:{k:v for k,v in record.items() if k!='samples_ms'} for name,record in results['queries'].items()},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
