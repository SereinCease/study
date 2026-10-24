import json,os,time
from datetime import datetime,timezone,timedelta
from array import array
import pytest
from server import db,memory,models,jobs,learning,maintenance,ingest


def make_session():
 c=db.put('concept',{'title':'fixture','citation_ids':[],'source_ids':[]})
 s=db.put('session',{'skill_id':'explain','concept_id':c['id'],'domain_id':'automation-testing','memory_enabled':True,'definition':{'version':1,'profile_id':'default'},'current_task':{'question':'边界如何隔离？','rubric':'fixture 销毁资源'},'hint_level':2,'answer_seen':True})
 return s,c

def event(s,i,answer='fixture 隔离'):
 return db.put('event',{'session_id':s['id'],'concept_id':s['concept_id'],'action':'answer','answer':answer,'output':{'question':'问题','rubric':'依据','explanation':'AI 讲解'},'created':(datetime(2025,1,1,tzinfo=timezone.utc)+timedelta(minutes=i)).isoformat()})

def fake_summary(monkeypatch):
 def model(system,content,*args,**kwargs):
  ctx=json.loads(content)
  return json.dumps({'learned':['用户解释了 fixture；不是掌握结论'],'unresolved':['继续验证'],'corrections':['采用更正后的评价'],'next_step':'独立完成变形题','evidence_ids':[ctx['events'][0]['id']]})
 monkeypatch.setattr(models,'generate',model)

def test_34_round_summary_search_correction_restart(client,monkeypatch):
 s,c=make_session();fake_summary(monkeypatch)
 events=[event(s,i,'早期错误：资源没有释放' if i==0 else 'fixture 独立回忆 '+str(i)) for i in range(34)]
 a=db.put('attempt',{'session_id':s['id'],'concept_id':c['id'],'event_id':events[0]['id'],'score':0,'answer':'资源没有释放','error_type':'concept','hint_level':0,'answer_seen':False})
 job=memory.schedule_summary(s['id']);jobs.tick();assert db.get(job['id'])['status']=='completed'
 assert len(db.all('summary'))==5 and not db.all('mastery')
 ctx,manifest=memory.context(db.get(s['id']),answer='资源释放错误')
 assert len(ctx['history'])==6 and ctx['current_task']['rubric']=='fixture 销毁资源'
 assert memory.search('资源没有释放',semantic=False)['items']
 db.update(a['id'],{'corrected_score':1,'correction_reason':'已有 teardown 资源释放'});learning.recompute(c['id'])
 assert all(x['status']=='stale' for x in db.all('summary'))
 hits=memory.search('teardown',semantic=False)['items'];assert any('更正' in x['excerpt'] or 'teardown' in x['excerpt'] for x in hits)
 assert db.get(events[0]['id'])['revision']>1
 memory.schedule_summary(s['id'],True);jobs.tick();assert any(x['status']=='ready' for x in db.all('summary'))
 # Idempotent summary scheduling must not create mastery evidence.
 count=db.count('attempt');assert memory.schedule_summary(s['id'],True) is None
 db.init();assert db.count('attempt')==count and db.get(s['id'])['answer_seen']
 assert db.get('mastery-'+c['id'])['score']==.3


def test_disabled_deleted_cascade_and_rebuild(client,monkeypatch):
 s,c=make_session();e=event(s,1,'只能出现一次的旧误解')
 a=db.put('attempt',{'event_id':e['id'],'concept_id':c['id'],'session_id':s['id'],'answer':e['answer'],'score':0})
 m=db.put('mistake',{'attempt_id':a['id'],'concept_id':c['id'],'session_id':s['id'],'answer':e['answer']})
 for item in (e,a,m):assert memory.visible(item['id'])
 memory.visibility(e['id'],False)
 for item in (e,a,m):assert not memory.visible(item['id'])
 assert not memory.search('旧误解',semantic=False)['items']
 assert not memory.context(s)[0]['history']
 monkeypatch.setattr(ingest,'vector',lambda x:[1.,0.])
 j=jobs.enqueue('memory_index',{});jobs.tick();assert db.get(j['id'])['status']=='completed'
 assert not memory.search('旧误解')['items']
 memory.visibility(e['id'],True);assert memory.visible(e['id'])
 memory.visibility(e['id'],delete=True);db.init()
 assert not memory.visible(e['id']) and not memory.search('旧误解',semantic=False)['items']


def test_summary_source_change_during_generation_rejected(monkeypatch):
 s,c=make_session();e=event(s,1)
 def generate(*args,**kw):
  memory.visibility(e['id'],False)
  return json.dumps({'next_step':'test','evidence_ids':[e['id']]})
 monkeypatch.setattr(models,'generate',generate)
 j=memory.schedule_summary(s['id'],True);jobs.tick()
 assert db.get(j['id'])['status']=='failed' and not db.all('summary')


def test_summary_failure_preserves_raw_and_old_summary(monkeypatch):
 s,c=make_session();e=event(s,1)
 old=db.put('summary',{'session_id':s['id'],'status':'ready','event_ids':[e['id']],'evidence_ids':[e['id']],'output':{'next_step':'old'}})
 event(s,2)
 monkeypatch.setattr(models,'generate',lambda *a,**k:'invalid json')
 j=memory.schedule_summary(s['id'],True);jobs.tick()
 assert db.get(j['id'])['status']=='failed' and db.get(old['id'])['status']=='ready' and len(memory.events(s['id']))==2


def test_over_1000_events_never_lost(monkeypatch):
 s,c=make_session()
 with db.connect() as con:
  for i in range(1012):
   db.put('event',{'session_id':s['id'],'concept_id':c['id'],'action':'answer','answer':'fixture '+str(i),'output':{},'created':str(i).zfill(8)},con=con)
 assert len(list(memory.pending_events(s['id'])))==1012
 assert len(memory.events(s['id']))==1012


def test_context_budget_preserves_current_answer_and_rubric():
 s,c=make_session()
 for i in range(8):event(s,i,'旧话题'*2000)
 answer='独立回答'*3000
 with pytest.raises(ValueError,match='未被截断'):memory.context(s,answer=answer)
 answer='独立回答'*900
 ctx,manifest=memory.context(s,answer=answer)
 assert ctx['answer']==answer and ctx['current_task']==s['current_task'] and manifest['estimated_tokens']<=10000


def test_user_fact_is_not_mastery_and_invalidation(client):
 s,c=make_session();e=event(s,1)
 f=client.post('/api/learner-profile/facts',json={'text':'我喜欢先看例子','category':'preference'}).json()
 assert not db.all('mastery') and memory.context(s)[0]['learner_facts'][0]['id']==f['id']
 summary=db.put('summary',{'session_id':s['id'],'status':'ready','output':{},'event_ids':[e['id']]})
 assert client.patch('/api/learner-profile/facts/'+f['id'],json={'text':'更短解释','revision':0}).status_code==400
 client.delete('/api/learner-profile/facts/'+f['id'])
 assert not memory.context(s)[0]['learner_facts'] and db.get(summary['id'])['status']=='stale'


def test_draft_cas_session_restart_preferences_and_csrf(client):
 s,c=make_session();url='/api/skill-sessions/'+s['id']+'/draft'
 r=client.put(url,json={'text':'我的未完成答案','revision':0});assert r.status_code==200
 assert client.put(url,json={'text':'旧标签覆盖','revision':0}).status_code==409
 db.init();assert client.get(url).json()['text']=='我的未完成答案'
 assert client.put('/api/preferences',json={'theme':'noir'}).status_code==200
 assert client.get('/api/preferences').json()['theme']=='noir'
 assert client.put('/api/preferences',json={'theme':'forest'}).status_code==422
 assert client.put(url,json={'text':'evil','revision':1},headers={'Origin':'https://evil.example'}).status_code==403


def test_cleanup_protects_originals_active_jobs_symlink_and_idempotency():
 for name in ('cache','originals','runner-workspaces'):(db.DATA/name).mkdir()
 for name in ('cache/expired','originals/protected','runner-workspaces/learnloop-active'):
  p=db.DATA/name;p.write_text('keep');os.utime(p,(0,0))
 (db.DATA/'cache'/'link').symlink_to(db.DATA/'originals'/'protected')
 active=jobs.enqueue('run',{});db.update(active['id'],{'workspace':str(db.DATA/'runner-workspaces'/'learnloop-active')})
 old=jobs.enqueue('session',{'session_id':'test','answer':'retained in event'},'idempotency');db.update(old['id'],{'status':'completed'})
 with db.connect() as c:c.execute('UPDATE entities SET created=? WHERE id=?',('2020-01-01',old['id']))
 snapshot=db.put('attempt',{'answer':'original attempt','score':1})
 preview=maintenance.candidates();assert [x['path'] for x in preview['files']]==['cache/expired']
 maintenance.clean();assert not (db.DATA/'cache/expired').exists()
 assert (db.DATA/'originals/protected').read_text()=='keep' and (db.DATA/'runner-workspaces/learnloop-active').exists()
 assert db.get(snapshot['id'])==snapshot
 assert jobs.enqueue('session',{},'idempotency')['id']==old['id']
 with maintenance.lock():
  with pytest.raises(ValueError):maintenance.clean()


def test_semantic_active_scope_and_changed_hash(monkeypatch):
 s,c=make_session();e=event(s,1,'语义证据')
 monkeypatch.setattr(ingest,'vector',lambda x:[1.,0.])
 j=jobs.enqueue('memory_index',{});jobs.tick()
 assert '本地语义' in memory.search('unmatched')['modes']
 db.update(e['id'],{'answer':'已更正'})
 with db.connect() as con:assert con.execute('SELECT COUNT(*) FROM evidence_vectors').fetchone()[0]==0
 assert memory.search('" OR * (',domain='another-domain')['items']==[]


def test_page_pagination_has_no_duplicates(client):
 s,c=make_session()
 for i in range(65):event(s,i)
 found=[];cursor=''
 while True:
  r=client.get('/api/records/event?limit=30&cursor='+cursor).json();found.extend(x['id'] for x in r['items']);cursor=r['next_cursor']
  if not cursor:break
 assert len(found)==len(set(found))==65


def test_index_build_failure_does_not_publish_partial(monkeypatch):
 s,c=make_session();e1=event(s,1);e2=event(s,2)
 count=[]
 def vector(text):
  count.append(1)
  if len(count)>1:raise RuntimeError('encoding failed')
  return [1.,0.]
 monkeypatch.setattr(ingest,'vector',vector)
 j=jobs.enqueue('memory_index',{});jobs.tick()
 assert db.get(j['id'])['status']=='failed'
 with db.connect() as con:
  assert con.execute('SELECT COUNT(*) FROM evidence_vectors').fetchone()[0]==0
  assert con.execute('SELECT COUNT(*) FROM evidence_vector_staging').fetchone()[0]==0
 assert len(memory.search('fixture',semantic=False)['items'])==2


def test_binary_vector_migration_idempotent():
 c=db.put('chunk',{'text':'fixture'})
 with db.connect() as con:
  con.execute('DELETE FROM schema_version WHERE version=3')
  con.execute('INSERT INTO vectors VALUES(?,?,?)',(c['id'],ingest.MODEL,'[1.0,0.0]'))
 db.init();db.init()
 with db.connect() as con:
  row=con.execute('SELECT vector FROM vectors WHERE id=?',(c['id'],)).fetchone()
  assert array('f',row[0]).tolist()==[1.,0.]


def test_profile_reference_is_historical_version(client):
 s,c=make_session()
 f=memory.save_fact({'text':'先示例','category':'preference'})
 db.put('context_snapshot',{'session_id':s['id'],'fact_ids':[f['id']],'source_revisions':{f['id']:f['revision']}})
 memory.save_fact({'text':'先理论','category':'preference','revision':f['revision']},f['id'])
 actual=client.get('/api/skill-sessions/'+s['id']+'/memory').json()
 assert actual['context']['facts'][0]['text']=='先示例'
 client.delete('/api/learner-profile/facts/'+f['id'])
 assert client.get('/api/skill-sessions/'+s['id']+'/memory').json()['context']['facts']==[]


def test_failed_summary_rebuild_not_requeued_forever(monkeypatch):
 s,c=make_session();e=event(s,1)
 su=db.put('summary',{'session_id':s['id'],'status':'stale','event_ids':[e['id']],'output':{}})
 monkeypatch.setattr(models,'generate',lambda *a,**k:'not JSON')
 memory.schedule_invalidated_summaries();jobs.tick()
 total=db.count('job');memory.schedule_invalidated_summaries()
 assert db.count('job')==total and db.get(su['id'])['rebuild_job_id']
