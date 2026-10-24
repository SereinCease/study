"""Goal-scoped recall and task assistance survive long teaching sessions.

Dates here are deterministic rule tests, not evidence of a real cross-day study.
"""
from datetime import datetime, timedelta, timezone
import pytest
from server import assessments, db, jobs, learning, memory, models


def session(goal='goal-a'):
    concept=db.put('concept',{'space_id':'automation','domain_id':'automation-testing','title':'fixture','citation_ids':[],'source_ids':[]})
    s=db.put('session',{'space_id':'automation','goal_id':goal,'concept_id':concept['id'],
        'domain_id':'automation-testing','skill_id':'explain','status':'active','step':1,
        'definition':{'version':1,'profile_id':'default','prompt':'核对当前题目与作答'},
        'memory_enabled':True,'hint_level':0,'answer_seen':False,'current_task_answered':False,
        'current_task_id':'original-task','current_task':{'question':'解释 fixture 何时释放资源？','rubric':'说明 teardown','is_transfer':True}})
    db.put('event',{'space_id':s['space_id'],'goal_id':goal,'session_id':s['id'],'concept_id':concept['id'],
        'action':'start','output':s['current_task']})
    return s,concept


def evidence(s, goal, answer='fixture', **extra):
    return db.put('attempt',{'space_id':s['space_id'],'goal_id':goal,'concept_id':s['concept_id'],
        'answer':answer,'question':'fixture','score':1,'hint_level':0,'answer_seen':False,
        'error_type':'unknown','is_transfer':True,**extra})


def test_goal_filter_precedes_rank_limit_and_context_selection(client):
    s,_=session()
    wanted=evidence(s,'goal-a','fixture 早期错误，尚未写出资源释放')
    for i in range(45):evidence(s,'goal-b','fixture',created=f'2026-10-24T00:{i:02d}:00+00:00')
    # An unrelated goal fills the global top results; local recall still finds ours.
    assert wanted['id'] not in {x['id'] for x in memory.search('fixture',semantic=False,limit=6)['items']}
    scoped=memory.search('fixture',semantic=False,goal_id='goal-a',space_id='automation')['items']
    assert wanted['id'] in {x['id'] for x in scoped}
    assert all(db.get(x['id']).get('goal_id')=='goal-a' for x in scoped)
    ctx,manifest=memory.context(s)
    assert wanted['id'] in manifest['evidence_ids']
    assert all(db.get(x['id']).get('goal_id')=='goal-a' for x in ctx['historical_evidence'])
    result=client.get('/api/evidence/search',params={'q':'fixture','space_id':'automation','goal_id':'goal-a'}).json()
    assert wanted['id'] in {x['id'] for x in result['items']}


def test_unassigned_goal_context_does_not_borrow_targeted_evidence():
    s,_=session(None)
    wanted=evidence(s,None,'fixture 无目标的历史回忆')
    for _ in range(45):evidence(s,'goal-b','fixture')
    ctx,manifest=memory.context(s)
    assert wanted['id'] in manifest['evidence_ids']
    assert all(db.get(x['id']).get('goal_id') is None for x in ctx['historical_evidence'])


def test_recent_events_and_run_channel_do_not_displace_earlier_evidence():
    s,_=session()
    wanted=evidence(s,'goal-a','fixture 早期错误，尚未写出资源释放')
    for _ in range(6):
        db.put('event',{'space_id':s['space_id'],'goal_id':s['goal_id'],'session_id':s['id'],
            'concept_id':s['concept_id'],'action':'answer','answer':'fixture','output':{}})
    for _ in range(45):
        db.put('run',{'space_id':s['space_id'],'goal_id':s['goal_id'],'concept_id':s['concept_id'],
            'answer':'fixture','exit_code':0})
    ctx,manifest=memory.context(s)
    assert wanted['id'] in manifest['evidence_ids']
    assert not set(manifest['event_ids']) & set(manifest['evidence_ids'])
    assert all(x['kind']!='run' for x in ctx['historical_evidence'])


def test_other_goal_cannot_complete_or_revoke_a_mistake_review():
    s,c=session();start=datetime(2026,1,1,tzinfo=timezone.utc)
    fix=evidence(s,'goal-a',created=start.isoformat())
    mistake=db.put('mistake',{'space_id':s['space_id'],'goal_id':'goal-a','concept_id':c['id'],
        'status':'pending_practice','practice_attempts':[fix['id']]})
    evidence(s,'goal-b',created=(start+timedelta(days=2)).isoformat())
    learning.recompute(c['id'])
    assert db.get(mistake['id'])['status']=='corrected_pending_review'
    review=evidence(s,'goal-a',created=(start+timedelta(days=3)).isoformat())
    learning.recompute(c['id'])
    assert db.get(mistake['id'])['review_attempt']==review['id']
    evidence(s,'goal-b',score=0,created=(start+timedelta(days=4)).isoformat())
    learning.recompute(c['id'])
    assert db.get(mistake['id'])['status']=='review_passed'
    assert db.get(mistake['id'])['review_attempt']==review['id']


def test_foreign_goal_practice_link_is_not_a_correction_or_review():
    s,c=session();fix=evidence(s,'goal-b')
    mistake=db.put('mistake',{'space_id':s['space_id'],'goal_id':'goal-a','concept_id':c['id'],
        'status':'review_passed','practice_attempts':[fix['id']],'review_attempt':fix['id']})
    learning.recompute(c['id'])
    assert db.get(mistake['id'])['status']=='pending_practice'
    assert db.get(mistake['id'])['review_attempt'] is None


def fake_turn(monkeypatch, question):
    calls=[]
    def structured(system,ctx,schema,profile_id='default',validate=None):
        calls.append(ctx)
        value=schema.model_validate({'explanation':'按当前题目解释','question':question['value'],
            'rubric':'说明 teardown','feedback':'依照作答评价','score':1 if ctx['action']=='answer' else None,
            'is_transfer':True})
        if validate:validate(value)
        return value.model_dump()
    monkeypatch.setattr(models,'structured',structured)
    return calls


def step(s, action):
    job=jobs.enqueue('session',{'session_id':s['id'],'action':action,'answer':'yield 后释放资源'})
    jobs.tick()
    return db.get(job['id'])


@pytest.mark.parametrize('assistance',['reveal','hint'])
def test_skip_same_question_does_not_erase_help(monkeypatch,assistance):
    s,c=session();question={'value':s['current_task']['question']};fake_turn(monkeypatch,question)
    assert step(s,assistance)['status']=='completed'
    assert step(s,'skip')['status']=='completed'
    current=db.get(s['id'])
    assert current['answer_seen']==(assistance=='reveal')
    assert current['hint_level']==(1 if assistance=='hint' else 0)
    assert step(s,'answer')['status']=='completed'
    attempt=db.all('attempt')[0]
    assert attempt['answer_seen']==(assistance=='reveal')
    assert attempt['hint_level']==(1 if assistance=='hint' else 0)
    assert db.get('mastery-'+c['id'])['score']==(0 if assistance=='reveal' else .276)


def test_blank_skip_keeps_assistance_and_cannot_be_graded(client,monkeypatch):
    s,_=session();question={'value':s['current_task']['question']};calls=fake_turn(monkeypatch,question)
    assert step(s,'reveal')['status']=='completed'
    question['value']=' '
    assert step(s,'skip')['status']=='completed'
    assert db.get(s['id'])['answer_seen'] is True
    count=len(calls)
    response=client.post('/api/skill-sessions/'+s['id']+'/events',json={
        'action':'answer','answer':'没有当前题目也试图提交','request_id':'empty-task-answer'})
    assert response.status_code in (400,409)
    assert step(s,'answer')['status']=='failed'
    assert len(calls)==count and not db.all('attempt')
    question['value']='设计一个不同的 fixture 资源隔离用例？'
    assert step(s,'continue')['status']=='completed'
    assert db.get(s['id'])['answer_seen'] is False
    assert step(s,'answer')['status']=='completed'
    assert db.all('attempt')[0]['answer_seen'] is False


def test_completed_request_replay_survives_an_empty_next_question(client,monkeypatch):
    s,_=session();fake_turn(monkeypatch,{'value':''})
    payload={'action':'answer','answer':'yield 后释放资源','request_id':'completed-empty-next'}
    url='/api/skill-sessions/'+s['id']+'/events'
    first=client.post(url,json=payload)
    assert first.status_code==200
    jobs.tick()
    assert db.get(first.json()['id'])['status']=='completed'
    second=client.post(url,json=payload)
    assert second.status_code==200 and second.json()['id']==first.json()['id']
    assert len(db.all('attempt'))==1
    assert client.post(url,json={**payload,'request_id':'new-empty-next'}).status_code==409


@pytest.mark.parametrize('span,strong',[(120,False),(86400,True)])
def test_midnight_is_not_a_spaced_review_without_24_elapsed_hours(monkeypatch,span,strong):
    # Synthetic timestamps validate the rule only, never a real study acceptance.
    monkeypatch.setenv('LEARNLOOP_TIMEZONE','Asia/Shanghai')
    s,c=session();start=datetime(2026,10,23,15,59,tzinfo=timezone.utc)
    for offset in (0,20,40,60,80,span):
        evidence(s,'goal-a',created=(start+timedelta(seconds=offset)).isoformat())
    mastery=learning.recompute(c['id'])
    assert mastery['score']>=.8 and mastery['independent_dates']==2 and mastery['transfer_verified']
    assert (mastery['state']=='strong') is strong
    assert mastery['independent_span_seconds']==span
    assert mastery['spaced_success_verified'] is strong


def test_withdrawing_delayed_success_removes_spaced_mastery():
    s,c=session();start=datetime(2026,10,23,15,59,tzinfo=timezone.utc)
    for offset in (0,20,40,60,80):
        evidence(s,'goal-a',created=(start+timedelta(seconds=offset)).isoformat())
    delayed=evidence(s,'goal-a',created=(start+timedelta(days=1)).isoformat())
    assert learning.recompute(c['id'])['state']=='strong'
    db.update(delayed['id'],{'assessment_voided':True})
    mastery=learning.recompute(c['id'])
    assert mastery['score']>=.8 and mastery['state']=='learning'
    assert not mastery['spaced_success_verified']
    # A delayed answer copied after reveal is not independent retest evidence.
    evidence(s,'goal-a',answer_seen=True,created=(start+timedelta(days=2)).isoformat())
    assert learning.recompute(c['id'])['state']=='learning'


def test_grade_scope_remains_original_task_when_model_emits_a_new_rubric(monkeypatch):
    s,_=session();captured=[]
    def provider(system,ctx,schema,profile_id='default',validate=None):
        captured.append((system,ctx))
        # This validates the boundary and storage, not the live model's accuracy.
        value=schema.model_validate({'explanation':'下一步讨论参数化','question':'如何参数化得到4个测试项？',
            'rubric':'说明4个case的收集和执行','feedback':'本题要求均已满足；参数化为下一步建议。',
            'score':1,'is_transfer':True})
        if validate:validate(value)
        return value.model_dump()
    monkeypatch.setattr(models,'structured',provider)
    result=step(s,'answer');assert result['status']=='completed'
    system,ctx=captured[0]
    scope=ctx['assessment_scope']
    assert scope['task_id']==s['current_task_id']
    assert scope['question']==s['current_task']['question'] and scope['rubric']==s['current_task']['rubric']
    assert scope['next_question_is_ungraded'] and ctx[scope['answer_field']]=='yield 后释放资源'
    assert '语义等价' in system and '不能倒用于当前回答扣分' in system and 'score 必须为 1.0' in system
    attempt=db.all('attempt')[0]
    assert attempt['question']==scope['question'] and attempt['rubric']==scope['rubric'] and attempt['score']==1
    assert db.get(s['id'])['current_task']['rubric']=='说明4个case的收集和执行'
    event=db.get(result['result']['event_id'])
    assert db.get(event['context_id'])['assessment_scope']==scope


def test_final_budget_trims_old_records_but_keeps_complete_grading_target(monkeypatch):
    s,_=session();question='分析这段代码：'+'题'*3200;rubric='标准'*150;answer='完整答案'*200
    s=db.update(s['id'],{'current_task':{'question':question,'rubric':rubric,'is_transfer':True}})
    for _ in range(6):
        db.put('event',{'space_id':s['space_id'],'goal_id':s['goal_id'],'session_id':s['id'],
            'concept_id':s['concept_id'],'action':'hint','answer':'旧对话'*500,'output':{}})
    initial,_=memory.context(s,'answer',answer)
    assert memory.estimate(initial)<=10000
    calls=fake_turn(monkeypatch,{'value':'下一道短题'})
    job=jobs.enqueue('session',{'session_id':s['id'],'action':'answer','answer':answer});jobs.tick()
    result=db.get(job['id']);assert result['status']=='completed'
    sent=calls[0]
    assert memory.estimate(sent)<=10000 and sent['answer']==answer
    assert sent['current_task']['question']==question and sent['current_task']['rubric']==rubric
    assert sent['assessment_scope']['question']==question and sent['assessment_scope']['rubric']==rubric
    manifest=db.get(db.get(result['result']['event_id'])['context_id'])
    assert manifest['final_context_trimmed']
    assert manifest['estimated_tokens']==memory.estimate(sent)
    assert manifest['event_ids']==[e['id'] for e in sent['history']]
    assert set(manifest['source_revisions'])<=set(manifest['source_ids']+manifest['event_ids']+manifest['fact_ids']+manifest['practice_ids'])


def test_oversized_frozen_grading_target_fails_without_model_call_or_score(monkeypatch):
    s,_=session();question='题'*5000;rubric='标准'*250
    s=db.update(s['id'],{'current_task':{'question':question,'rubric':rubric,'is_transfer':True}})
    assert memory.estimate(memory.context(s,'answer','原稿保留')[0])<=10000
    calls=fake_turn(monkeypatch,{'value':'下一题'})
    before=db.count('event')
    result=step(s,'answer')
    assert result['status']=='failed' and '未被截断' in result['error']
    assert calls==[] and not db.all('attempt') and not db.all('context_snapshot')
    assert db.count('event')==before and db.get(s['id'])['current_task']==s['current_task']


def test_voided_question_list_is_budgeted_but_omission_cannot_reenable_a_task(monkeypatch):
    s,_=session();withdrawn=[]
    for i in range(30):
        question=f'错误旧题 {i}：'+'错误要求'*125;withdrawn.append(question)
        db.put('assessment_action',{'space_id':s['space_id'],'concept_id':s['concept_id'],'status':'active',
            'action':'void','question':question,'reason':'已核对为错误契约','task_fingerprint':assessments.fingerprint(question)})
    output={'value':'明确不同的新题'};calls=fake_turn(monkeypatch,output)
    result=step(s,'answer');assert result['status']=='completed'
    sent=calls[0];assert memory.estimate(sent)<=10000
    manifest=db.get(db.get(result['result']['event_id'])['context_id'])
    assert manifest['final_context_trimmed']['voided_questions']>0
    assert manifest['voided_questions_included']==len(sent['voided_questions'])
    omitted=next(q for q in withdrawn if q not in {x['question'] for x in sent['voided_questions']})
    output['value']=omitted
    failed=step(s,'continue')
    assert failed['status']=='failed' and '已作废' in failed['error']
    assert len(db.all('attempt'))==1


def test_missing_score_does_not_silently_complete_answer(monkeypatch):
    s,_=session()
    def missing(system,ctx,schema,profile_id='default',validate=None):
        value=schema.model_validate({'explanation':'答案正确但漏评分','question':'新题','rubric':'标准','score':None})
        validate(value)
        return value.model_dump()
    monkeypatch.setattr(models,'structured',missing)
    result=step(s,'answer')
    assert result['status']=='failed'
    assert not db.all('attempt')
    assert db.get(s['id'])['current_task_id']=='original-task'
