"""Focus records are durable time records, isolated from learning evidence."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from server import db, spaces, focus_api

@pytest.fixture
def focus_client(monkeypatch):
    spaces.ensure()
    stamp=[1800000000.0]
    monkeypatch.setattr(focus_api,'clock',lambda:stamp[0])
    app=FastAPI();app.include_router(focus_api.router)
    @app.exception_handler(ValueError)
    async def errors(request,exc):return JSONResponse({'detail':str(exc)},400)
    with TestClient(app) as client:yield client,stamp


def new(client,**extra):
    return client.post('/api/focus-sessions',json={'space_id':'automation','duration_minutes':1,'task_title':'阅读 fixture','request_id':'focus-request-one',**extra})

def change(client,v,action):
    return client.patch('/api/focus-sessions/'+v['id'],json={'revision':v['revision'],'action':action})


def test_deadline_pause_resume_restart_and_no_learning_evidence(focus_client):
    c,t=focus_client
    v=new(c).json()['session'];assert v['status']=='ready'
    v=change(c,v,'start').json()['session'];assert v['deadline']==t[0]+60
    t[0]+=18
    v=change(c,v,'pause').json()['session'];assert v['remaining_seconds']==42
    t[0]+=200
    assert c.get('/api/focus-sessions').json()['session']['remaining_seconds']==42
    db.init()  # Reopening storage must not restart the countdown.
    v=change(c,v,'resume').json()['session'];assert v['deadline']==t[0]+42
    t[0]+=43
    v=c.get('/api/focus-sessions').json()['session'];assert v['status']=='completed' and v['remaining_seconds']==0
    assert c.get('/api/focus-sessions').json()['session']['revision']==v['revision']
    for kind in ('attempt','assessment','mastery','review','event'):
        assert db.count(kind)==0


def test_one_active_and_create_idempotency(focus_client):
    c,t=focus_client
    one=new(c).json()['session'];two=new(c).json()['session'];assert one==two
    assert new(c,request_id='new-request').status_code==409
    assert new(c,task_title='different').status_code==409
    change(c,one,'finish')
    assert new(c,request_id='new-request').status_code==200
    assert change(c,db.get(one['id']),'reset').status_code==409


def test_stale_tabs_and_boundary_pause_do_not_restart(focus_client):
    c,t=focus_client
    ready=new(c).json()['session'];running=change(c,ready,'start').json()['session']
    assert change(c,ready,'start').status_code==409
    t[0]+=60
    paused=change(c,running,'pause').json()['session']
    assert paused['status']=='completed' and paused['deadline'] is None
    assert change(c,paused,'resume').status_code==409


def test_scope_and_goal_session_consistency(focus_client):
    c,t=focus_client
    other=db.put('space',{'title':'兴趣','domain_id':'general'})
    goal=db.put('goal',{'title':'目标','space_id':other['id']})
    assert new(c,goal_id=goal['id']).status_code==400
    goal=db.put('goal',{'title':'目标','space_id':'automation'})
    session=db.put('session',{'space_id':'automation','goal_id':'different'})
    assert new(c,goal_id=goal['id'],session_id=session['id']).status_code==400
    session=db.update(session['id'],{'goal_id':goal['id']})
    assert new(c,goal_id=goal['id'],session_id=session['id']).status_code==200


def test_expired_timer_allows_new_one_and_early_finish_records_remaining(focus_client):
    c,t=focus_client
    v=new(c).json()['session'];v=change(c,v,'start').json()['session']
    t[0]+=61
    fresh=new(c,request_id='fresh-request').json()['session'];assert db.get(v['id'])['status']=='completed'
    running=change(c,fresh,'start').json()['session'];t[0]+=7
    done=change(c,running,'finish').json()['session']
    assert done['remaining_seconds']==53 and done['finished_reason']=='user'


def test_parallel_create_serializes_single_active(focus_client):
    c,t=focus_client
    def create(i):
        try:return focus_api.create(focus_api.FocusCreate(space_id='automation',request_id='parallel-'+str(i)))['session']['id']
        except HTTPException as error:return error.status_code
    with ThreadPoolExecutor(max_workers=4) as executor:results=list(executor.map(create,range(4)))
    assert sum(isinstance(x,str) for x in results)==1
    assert results.count(409)==3 and db.count('focus_session')==1
