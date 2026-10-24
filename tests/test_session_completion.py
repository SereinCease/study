from server import db, jobs, study


def test_end_session_unblocks_next_recommendation_without_scoring(client, monkeypatch):
    from server import memory
    monkeypatch.setattr(memory,'schedule_summary',lambda *args,**kw:None)
    goal=db.put('goal',{'space_id':'automation','title':'验收目标','source_ids':[],'concept_ids':[]})
    session=db.put('session',{'space_id':'automation','goal_id':goal['id'],'skill_id':'explain','mode':'learn','status':'active','memory_enabled':True})
    assert study.state('automation',goal['id'])['next_action']['kind']=='resume'
    r=client.patch('/api/skill-sessions/'+session['id'],json={'status':'completed'})
    assert r.status_code==200
    assert study.state('automation',goal['id'])['next_action']['kind']!='resume'
    assert not db.all('attempt') and not db.all('mastery')
    assert client.post('/api/skill-sessions/'+session['id']+'/events',json={'action':'continue','request_id':'closed-session'}).status_code==409
    assert client.patch('/api/skill-sessions/'+session['id'],json={'status':'active'}).status_code==200
    assert study.state('automation',goal['id'])['next_action']['kind']=='resume'


def test_cannot_end_session_during_generation(client):
    session=db.put('session',{'space_id':'automation','skill_id':'explain','status':'active'})
    jobs.enqueue('session',{'session_id':session['id'],'action':'start'})
    r=client.patch('/api/skill-sessions/'+session['id'],json={'status':'completed'})
    assert r.status_code==400
    assert db.get(session['id'])['status']=='active'
