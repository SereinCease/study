import json
from contextlib import contextmanager
import httpx,pytest
from server import db,models

@pytest.mark.parametrize('explicit',[0,1000])
def test_truncated_budget_retry_keeps_mode_and_usage(monkeypatch,explicit):
    bodies=[]
    class Client:
        def post(self,*a,**kw):
            bodies.append(json.loads(json.dumps(kw['json'])))
            return httpx.Response(200,json={'choices':[{'finish_reason':'length' if len(bodies)==1 else 'stop','message':{'content':'{}'}}],'usage':{'completion_tokens':16384,'completion_tokens_details':{'reasoning_tokens':16000}}})
    @contextmanager
    def transport(*a,**kw):yield Client()
    monkeypatch.setattr(models.transport,'client',transport);monkeypatch.setattr(models,'key',lambda *a:'test')
    with models.options(reasoning='standard',max_output_tokens=explicit):
        if explicit:
            with pytest.raises(models.ModelError):models.generate('a','b')
        else:assert models.generate('a','b')=='{}'
    assert len(bodies)==(1 if explicit else 2)
    assert bodies[0]['max_tokens']==(explicit or 16384)
    if not explicit:assert bodies[1]['max_tokens']==32768 and bodies[1]['reasoning_effort']=='low'
    assert db.all('model_call')[0]['usage']['completion_tokens']==16384
    assert 'reasoning_content' not in json.dumps(db.all('model_call'))

def recovery_fixture():
    c=db.put('concept',{'space_id':'automation','title':'断言'})
    s=db.put('session',{'space_id':'automation','mode':'learn','concept_id':c['id'],'current_task':{'question':'下一题'}})
    prior=db.put('event',{'session_id':s['id'],'space_id':'automation','action':'reveal','output_task_id':'t','output':{'question':'旧题','is_transfer':True}})
    snap=db.put('context_snapshot',{'assessment_scope':{'task_id':'t','question':'旧题','rubric':'按旧题评分'}})
    e=db.put('event',{'session_id':s['id'],'space_id':'automation','concept_id':c['id'],'context_id':snap['id'],'answered_task_id':'t','action':'answer','answer':'原答案','output':{'score':None,'question':'下一题'}})
    db.put('summary',{'session_id':s['id'],'status':'ready','evidence_ids':[e['id']]})
    return e,s

def test_reviewed_recovery_idempotent_preserves_time_and_assistance(client):
    e,s=recovery_fixture();payload={'space_id':'automation','revision':e['revision'],'request_id':'recover-unique','score':1,'reason':'核对原题已满足'}
    url='/api/assessments/'+e['id']+'/recover-missing'
    a=client.post(url,json=payload);assert a.status_code==200,a.text
    a=a.json();assert a['created']==e['created'] and a['answer_seen'] and a['question']=='旧题'
    assert db.get(e['id'])['output']['score'] is None
    assert db.all('summary')[0]['status']=='stale'
    assert db.all('mastery')[0]['score']==0
    assert client.post(url,json=payload).json()['id']==a['id']
    assert db.count('attempt')==1
    assert client.post(url,json={**payload,'score':.5}).status_code==409
    assert db.get(s['id'])['current_task']=={'question':'下一题'}

def test_recovery_rejects_wrong_scope_stale_revision_and_explore(client):
    e,s=recovery_fixture();url='/api/assessments/'+e['id']+'/recover-missing'
    data={'space_id':'automation','revision':e['revision'],'request_id':'recover-unique','score':1,'reason':'核对'}
    assert client.post(url,json={**data,'space_id':'inbox'}).status_code==404
    assert client.post(url,json={**data,'revision':999}).status_code==409
    db.update(s['id'],{'mode':'explore'})
    assert client.post(url,json=data).status_code==400
    assert not db.all('attempt')


def test_low_recovered_grade_creates_one_traceable_mistake(client):
    e,s=recovery_fixture();data={'space_id':'automation','revision':e['revision'],'request_id':'recover-low-score','score':.4,'reason':'原题要求缺少两项'}
    url='/api/assessments/'+e['id']+'/recover-missing'
    first=client.post(url,json=data);assert first.status_code==200
    assert client.post(url,json=data).status_code==200
    assert db.count('mistake')==1
    m=db.all('mistake')[0];assert m['attempt_id']==first.json()['id'] and m['answer']==e['answer']
