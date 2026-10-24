import json
from datetime import datetime,timedelta,timezone
import pytest
from server import db,ingest,learning,jobs,models

def source_concept():
    s=ingest.import_bytes('notes.md','# 断言\n断言验证预期结果。'.encode());jobs.tick()
    chunk=db.all('chunk')[0]
    c=db.put('concept',{'title':'断言','definition':'验证预期','source_ids':[s['id']],'citation_ids':[chunk['id']]})
    return s,c,chunk

def fake_model(monkeypatch,chunk):
    def generate(system,content,profile_id='default',structured=False):
        ctx=json.loads(content)
        if 'cards' in system:
            return json.dumps({'cards':[{'title':'断言','definition':'验证预期','purpose':'测试','question':'什么是断言？','citation_ids':[chunk['id']]}],'relations':[],'gaps':[]},ensure_ascii=False)
        if 'citation_ids' in system and 'insufficient' in system:
            return json.dumps({'answer':'断言检查预期。','citation_ids':[chunk['id']]})
        action=ctx['action'];answer=ctx.get('answer','')
        question=f'第 {len(db.all("attempt"))+1} 道变形题：设计一个不同边界的断言' if action=='answer' else '设计一个不同边界的断言'
        return json.dumps({'explanation':'断言检查实际与预期。','citations':[chunk['id']],'question':question,'rubric':'能说明预期与实际','feedback':'对应评分标准','score':(1 if answer=='正确' else 0) if action=='answer' else None,'error_type':'concept','is_transfer':True,'reason_kind':'evidence'})
    monkeypatch.setattr(models,'generate',generate)

def test_full_mock_learning_loop_citations_idempotency(client,monkeypatch):
    s,c,chunk=source_concept();fake_model(monkeypatch,chunk)
    decomp=client.post('/api/skill-sessions',json={'skill_id':'decompose','source_ids':[s['id']]}).json();jobs.tick()
    a=db.all('artifact')[0];assert a['status']=='draft'
    accepted=client.patch('/api/artifacts/'+a['id'],json={'accept':True,'review_revision':a['revision'],
        'decisions':[{'item_id':'suggestion:0','action':'merge','target_id':c['id'],'target_revision':c['revision'],'reason':'明确同义，保留引用'}]}).json()
    assert accepted['status']=='accepted'
    assert client.patch('/api/artifacts/'+a['id'],json={'accept':True}).json()==accepted
    assert len(db.all('concept'))==1
    q=client.post('/api/ask',json={'question':'断言'}).json();assert q['citations'][0]['id']==chunk['id']
    session=client.post('/api/skill-sessions',json={'skill_id':'explain','concept_id':c['id']}).json();jobs.tick()
    for action in ('hint','skip','reveal'):
        client.post('/api/skill-sessions/'+session['id']+'/events',json={'action':action,'request_id':db.uid()});jobs.tick()
    assert not db.all('attempt')
    client.post('/api/skill-sessions/'+session['id']+'/events',json={'action':'answer','answer':'正确','request_id':db.uid()});jobs.tick()
    assert db.all('mastery')[0]['score']==0
    req={'action':'answer','answer':'错误','request_id':db.uid()}
    a=client.post('/api/skill-sessions/'+session['id']+'/events',json=req).json();b=client.post('/api/skill-sessions/'+session['id']+'/events',json=req).json();assert a['id']==b['id']
    jobs.tick();assert len(db.all('attempt'))==2 and len(db.all('mistake'))==1
    attempt=db.all('attempt')[-1]
    client.patch('/api/attempts/'+attempt['id'],json={'score':1,'reason':'按资料该答案包含预期与实际'})
    assert db.all('mastery')[0]['score']==.3
    assert db.all('review')[0]['interval_days']==1
    db.init();assert db.get(session['id'])['step']==6
    assert client.get('/api/skill-sessions/'+session['id']).json()['events']

def test_invalid_citations_do_not_commit(monkeypatch):
    s,c,chunk=source_concept()
    monkeypatch.setattr(models,'generate',lambda *a,**k:json.dumps({'cards':[{'title':'x','definition':'x','purpose':'x','question':'x','citation_ids':['fabricated']}]}))
    session=learning.start('decompose',[s['id']]);jobs.tick()
    assert db.get(session['job_id'])['status']=='failed'
    assert not db.all('artifact')

def test_source_citation_cannot_impersonate_learning_evidence(client,monkeypatch):
    s,c,chunk=source_concept()
    monkeypatch.setattr(models,'generate',lambda *a,**k:json.dumps({'answer':'断言检查预期','citation_ids':[chunk['id']],'evidence_ids':[chunk['id']]}))
    result=client.post('/api/ask',json={'question':'断言'})
    assert result.status_code==502 and result.json()['code']=='validation'
    assert not db.all('conversation') and not db.all('attempt')

def test_skill_snapshot_and_checkpoint_retry(client,monkeypatch):
    s,c,chunk=source_concept();fake_model(monkeypatch,chunk)
    session=learning.start('explain',concept_id=c['id'])
    old=session['definition']['prompt']
    client.put('/api/skills/explain',json={'prompt':'新的教学风格'})
    jobs.tick()
    assert db.get(session['id'])['definition']['prompt']==old
    assert learning.skill('explain')['version']==2
    job=jobs.enqueue('import',{'source_id':s['id']});db.update(job['id'],{'status':'running'})
    jobs.recover();assert db.get(job['id'])['status']=='queued'

def test_mastery_rules_dates_transfer_environment_and_correction():
    s,c,ch=source_concept()
    def attempt(day,score=1,**kw):
        t=(datetime.now(timezone.utc)-timedelta(days=10-day)).isoformat()
        return db.put('attempt',dict(concept_id=c['id'],score=score,error_type='concept',hint_level=0,answer_seen=False,created=t)|kw)
    attempt(0,error_type='environment',score=0);attempt(0,answer_seen=True)
    assert learning.recompute(c['id'])['score']==0
    a=attempt(1);learning.recompute(c['id']);assert db.all('review')[0]['interval_days']==1
    attempt(2);learning.recompute(c['id']);assert db.all('review')[0]['interval_days']==3
    attempt(3);attempt(4);attempt(5,is_transfer=True)
    m=learning.recompute(c['id']);assert m['state']=='strong' and m['independent_dates']==5
    db.update(a['id'],{'corrected_score':0});m=learning.recompute(c['id']);assert m['score']<.832
    assert db.all('review')[0]['interval_days']==14

def test_goal_path_skips_mastered_honors_order(client):
    s,c,ch=source_concept();d=db.put('concept',dict(title='fixture',definition='隔离',source_ids=[s['id']],citation_ids=[ch['id']]))
    db.put('mastery',{'concept_id':c['id'],'state':'strong','score':.9})
    goal=client.post('/api/goals',json={'title':'目标','source_ids':[s['id']]}).json()
    path=client.get('/api/goals/'+goal['id']+'/path').json();assert path[0]['status']=='skip_mastered'
    assert client.put('/api/goals/'+goal['id']+'/path',json={'concept_ids':[d['id'],c['id']]}).status_code==200
    assert client.get('/api/goals/'+goal['id']+'/path').json()[0]['concept_id']==d['id']

def test_review_state_recomputed_after_grade_correction():
    s,c,ch=source_concept()
    start=datetime.now(timezone.utc)-timedelta(days=5)
    fix=db.put('attempt',{'concept_id':c['id'],'score':1,'hint_level':0,'answer_seen':False,'is_transfer':True,'error_type':'concept','created':start.isoformat()})
    m=db.put('mistake',{'concept_id':c['id'],'status':'pending_practice','practice_attempts':[fix['id']]})
    learning.recompute(c['id']);assert db.get(m['id'])['status']=='corrected_pending_review'
    db.put('attempt',{'concept_id':c['id'],'score':1,'hint_level':0,'answer_seen':False,'error_type':'concept','created':(start+timedelta(days=2)).isoformat()})
    learning.recompute(c['id']);assert db.get(m['id'])['status']=='review_passed'
    db.update(fix['id'],{'corrected_score':0})
    learning.recompute(c['id']);assert db.get(m['id'])['status']=='pending_practice'

def test_hint_cannot_change_current_question(monkeypatch):
    s,c,ch=source_concept();fake_model(monkeypatch,ch)
    session=learning.start('explain',concept_id=c['id']);jobs.tick()
    first=db.all('event')[0]['output']
    def generate(*args,**kwargs):
        return json.dumps({'explanation':'提示','question':'不应该替换的问题','rubric':'不同的标准','citations':[ch['id']],'score':1,'is_transfer':False})
    monkeypatch.setattr(models,'generate',generate)
    job=jobs.enqueue('session',{'session_id':session['id'],'action':'hint'});jobs.tick()
    event=db.all('event')[-1]['output']
    assert event['question']==first['question'] and event['rubric']==first['rubric'] and event['score'] is None
    assert not db.all('attempt')

def test_concept_validation_and_conflicts(client):
    s,c,ch=source_concept()
    before=db.get(c['id'])
    assert client.patch('/api/concepts/'+c['id'],json={'title':{'malicious':'shape'},'id':'other'}).status_code==400
    assert db.get(c['id'])==before
    a=db.put('artifact',{'session_id':'example','source_ids':[s['id']],'status':'draft','batch':0,'output':{'cards':[{'title':c['title'],'definition':'不同定义','purpose':'核对','question':'问题','citation_ids':[ch['id']]}]}})
    learning.accept_artifact(a['id'],decisions=[{'item_id':'card:0','action':'merge','target_id':c['id'],'target_revision':c['revision']}])
    updated=db.get(c['id']);assert updated['definition']==before['definition']
    assert updated['needs_review'] and updated['definition_variants'][0]['card']['definition']=='不同定义'

def test_environment_reclassification_removes_learning_penalty(client):
    s,c,ch=source_concept()
    a=db.put('attempt',{'concept_id':c['id'],'score':0,'error_type':'concept','hint_level':0,'answer_seen':False})
    m=db.put('mistake',{'concept_id':c['id'],'attempt_id':a['id'],'status':'pending_analysis','error_type':'concept'})
    learning.recompute(c['id']);assert db.all('review')
    assert client.patch('/api/mistakes/'+m['id'],json={'error_type':'environment','reason':'浏览器未安装'}).status_code==200
    assert not db.all('review')
    assert db.get('mastery-'+c['id'])['state']=='unverified'
