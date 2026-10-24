"""A bad task is withdrawn with its provenance, never replaced by a zero score."""
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from server import assessments, db, learning, memory, jobs, models, ingest, runner


def records():
    concept = db.put('concept', {'space_id':'automation','domain_id':'automation-testing','title':'断言','citation_ids':[],'source_ids':[]})
    session = db.put('session', {'space_id':'automation','domain_id':'automation-testing','concept_id':concept['id'], 'status':'active', 'skill_id':'explain','current_task':{'question':'错误题：负折扣必须报错','rubric':'错误要求'},'step':1})
    event = db.put('event', {'space_id':'automation','concept_id':concept['id'],'session_id':session['id'],'action':'start','output':{'question':session['current_task']['question'],'rubric':'错误要求'}})
    db.update(session['id'], {'current_task_event_id':event['id'],'current_task_id':'task-bad'})
    return concept, db.get(session['id']), event


def add_attempt(concept, session, day=0, **extra):
    return db.put('attempt', {'space_id':'automation','concept_id':concept['id'],'session_id':session['id'],
        'question':session['current_task']['question'],'answer':'原稿保留','score':1,'error_type':'unknown','is_transfer':True,
        'hint_level':0,'answer_seen':False,'task_id':session.get('current_task_id'),
        'created':(datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=day)).isoformat(),**extra})


def action(target, subject='assessment', revision=None, request='request-0001', reason='题目要求与实际实现不一致'):
    return assessments.ActionIn(space_id=target['space_id'], revision=revision or db.get(target['id'])['revision'], request_id=request, reason=reason,subject=subject)


def test_void_is_atomic_idempotent_and_not_a_zero_penalty():
    concept,session,event=records()
    valid=add_attempt(concept,session,question='正确的旧题',score=1,task_id='good')
    bad=add_attempt(concept,session,1,event_id=event['id'])
    mistake=db.put('mistake', {'space_id':'automation','concept_id':concept['id'],'status':'corrected_pending_review','practice_attempts':[bad['id']],'question':'原始错题'})
    learning.recompute(concept['id'])
    assert db.get('mastery-'+concept['id'])['score']==.51
    request=action(bad)
    result=assessments.act(bad['id'],request)
    assert result['voided'] and result['reason']==request.reason
    assert db.get(bad['id'])['score']==1 and db.get(bad['id'])['answer']=='原稿保留'
    assert db.get(bad['id']).get('memory_enabled',True)
    assert db.get('mastery-'+concept['id'])['score']==.3
    assert db.get('review-'+concept['id'])['evidence_id']==valid['id']
    assert db.get(mistake['id'])['status']=='pending_practice'
    assert db.get(session['id'])['current_task_voided']
    snapshot=db.all('assessment_action')
    assert assessments.act(bad['id'],request)==result
    assert db.all('assessment_action')==snapshot
    assert not memory.visible(bad['id'])
    # Re-enabling memory cannot silently restore an explicitly withdrawn score.
    memory.visibility(bad['id'],True)
    assert db.get('mastery-'+concept['id'])['score']==.3 and not memory.visible(bad['id'])


def test_void_original_error_removes_its_false_mistake_and_review():
    concept,session,event=records();bad=add_attempt(concept,session,score=0,event_id=event['id'])
    mistake=db.put('mistake', {'space_id':'automation','concept_id':concept['id'],'attempt_id':bad['id'],'status':'pending_analysis'})
    learning.recompute(concept['id'])
    assessments.act(bad['id'],action(bad))
    assert db.get(mistake['id'])['status']=='voided'
    assert db.get('mastery-'+concept['id'])['state']=='unverified'
    assert not db.all('review')
    assert db.get(bad['id'])['score']==0


def test_explicit_restore_recalculates_but_does_not_invent_a_review():
    concept,session,event=records();bad=add_attempt(concept,session,event_id=event['id'])
    mistake=db.put('mistake', {'space_id':'automation','concept_id':concept['id'],'status':'pending_practice','practice_attempts':[bad['id']]})
    assessments.act(bad['id'],action(bad));assert db.get(mistake['id'])['status']=='pending_practice'
    result=assessments.act(bad['id'],action(bad,request='restore-0001',reason='已核对题目真实有效'),'restore')
    assert not result['voided'] and db.get(mistake['id'])['status']=='corrected_pending_review'
    assert db.get('mastery-'+concept['id'])['score']==.3
    assert not assessments.task_voided(db.get(session['id']))
    assert db.all('assessment_action')[0]['status']=='restored'


def test_revision_conflict_and_cross_space_have_no_side_effects(client):
    concept,session,event=records();bad=add_attempt(concept,session)
    original=db.all('attempt')
    payload=action(bad).model_dump();payload['space_id']='inbox'
    assert client.post('/api/assessments/'+bad['id']+'/void',json=payload).status_code==404
    payload['space_id']='automation';payload['revision']+=1
    assert client.post('/api/assessments/'+bad['id']+'/void',json=payload).status_code==409
    assert db.all('attempt')==original and not db.all('assessment_action')


def test_error_rolls_back_all_linked_grades(monkeypatch):
    concept,session,event=records();bad=add_attempt(concept,session,event_id=event['id'])
    before=db.all('attempt');before_events=db.all('event');epoch=memory.epoch()
    def fail(*args):raise RuntimeError('simulated transaction failure')
    monkeypatch.setattr(learning,'recompute',fail)
    with pytest.raises(RuntimeError): assessments.act(bad['id'],action(bad))
    assert db.all('attempt')==before and db.all('event')==before_events
    assert not db.all('assessment_action') and memory.epoch()==epoch


def test_unanswered_question_can_be_voided_and_cannot_be_submitted(client):
    concept,session,event=records()
    response=client.post('/api/assessments/'+event['id']+'/void',json=action(event,'question').model_dump())
    assert response.status_code==200 and response.json()['voided']
    response=client.post('/api/skill-sessions/'+session['id']+'/events',json={'action':'answer','answer':'尝试回答','request_id':'submit-001'})
    assert response.status_code==409 and '作废' in response.json()['detail']
    assert not db.all('attempt') and not db.all('review')
    with pytest.raises(HTTPException):assessments.run_task_scope(session['id'],session['current_task_id'],'assertions')


def test_answer_event_new_question_is_not_the_previous_assessment():
    concept,session,event=records()
    grade=db.put('event', {'space_id':'automation','concept_id':concept['id'],'session_id':session['id'],'action':'answer','output':{'question':'下一题：金额边界','rubric':'新题要求','score':1},'output_task_id':'task-new'})
    bad=add_attempt(concept,session,event_id=grade['id'])
    db.update(session['id'], {'current_task':{'question':'下一题：金额边界'},'current_task_id':'task-new','current_task_event_id':grade['id']})
    assessments.act(grade['id'],action(grade))
    assert db.get(grade['id'])['assessment_voided']
    assert not db.get(grade['id']).get('question_voided')
    assert not assessments.task_voided(db.get(session['id']))
    # The new question has its own explicit operation, without overwriting the
    # first action's provenance or treating the previous score as its own.
    second=assessments.act(grade['id'],action(grade,'question',request='question-0002'))
    assert second['voided'] and db.get(grade['id'])['question_voided']
    assert assessments.task_voided(db.get(session['id']))
    assert len(db.all('assessment_action'))==2


def test_void_new_question_keeps_previous_valid_assessment():
    concept,session,event=records()
    grade=db.put('event', {'space_id':'automation','concept_id':concept['id'],'session_id':session['id'],'action':'answer','output':{'question':'下一题有错','score':1}})
    valid=add_attempt(concept,session,question='上一题合法',event_id=grade['id'])
    learning.recompute(concept['id'])
    assessments.act(grade['id'],action(grade,'question'))
    assert db.get(grade['id'])['question_voided']
    assert not db.get(valid['id']).get('assessment_voided')
    assert db.get('mastery-'+concept['id'])['score']==.3


def test_linked_run_withdraws_its_own_score_but_not_unlinked_session_run():
    concept,session,event=records();bad=add_attempt(concept,session,event_id=event['id'])
    linked=db.put('run', {'space_id':'automation','session_id':session['id'],'task_id':'task-bad','task_binding':'explicit_user','concept_id':concept['id'],'counts':{'tests':1},'exit_code':0})
    counted=add_attempt(concept,session,1,run_id=linked['id'],question='内置练习独立题干')
    unrelated=db.put('run', {'space_id':'automation','session_id':session['id'],'concept_id':concept['id'],'counts':{'tests':1},'exit_code':0})
    other=add_attempt(concept,session,2,run_id=unrelated['id'],question='独立练习',task_id=None)
    assessments.act(bad['id'],action(bad))
    assert db.get(linked['id'])['assessment_voided'] and db.get(counted['id'])['assessment_voided']
    assert not db.get(unrelated['id']).get('assessment_voided') and not db.get(other['id']).get('assessment_voided')
    assert db.get(linked['id'])['exit_code']==0 and db.get('mastery-'+concept['id'])['score']==.3


def test_frozen_run_identity_survives_navigation_and_inflight_withdrawal():
    concept,session,event=records();frozen=assessments.run_task_scope(session['id'],session['current_task_id'],'assertions')
    db.update(session['id'], {'current_task':{'question':'新的合法题'},'current_task_id':'task-new'})
    assessments.act(event['id'],action(event,'question'))
    with db.connect() as con:
        assert assessments.run_flags({**frozen,'space_id':'automation','concept_id':None},con)['assessment_voided']
        assert assessments.run_flags({'space_id':'automation','session_id':session['id']},con)=={}
    assert not assessments.task_voided(db.get(session['id']))


def test_model_cannot_regenerate_withdrawn_question(monkeypatch):
    source=ingest.import_bytes('intro.md',b'# Pytest\nAssert the expected value.');jobs.tick();chunk=db.all('chunk')[0]
    concept=db.put('concept',{'space_id':'automation','title':'Assert','source_ids':[source['id']],'citation_ids':[chunk['id']]})
    question='错误题：负折扣必须报错'
    def fake(system,ctx,schema,profile_id='default',validate=None):
        result=schema.model_validate({'explanation':'解释','question':question,'rubric':'错误要求','citations':[chunk['id']]})
        if validate:validate(result)
        return result.model_dump()
    monkeypatch.setattr(models,'structured',fake)
    session=learning.start('explain',concept_id=concept['id']);jobs.tick()
    event=db.all('event')[-1]
    assessments.act(event['id'],action(event,'question'))
    job=jobs.enqueue('session',{'session_id':session['id'],'action':'continue'});jobs.tick()
    assert db.get(job['id'])['status']=='failed' and '作废' in db.get(job['id'])['error']
    assert len(db.all('event'))==1 and not db.all('attempt')
    question='根据真实需求检查负数金额'
    job=jobs.enqueue('session',{'session_id':session['id'],'action':'continue'});jobs.tick()
    assert db.get(job['id'])['status']=='completed'
    assert not db.get(session['id'])['current_task_voided']


def test_withdrawal_invalidates_summaries_profile_and_context():
    concept,session,event=records();bad=add_attempt(concept,session,event_id=event['id'])
    summary=db.put('summary',{'space_id':'automation','session_id':session['id'],'evidence_ids':[bad['id']],'status':'ready','output':{'text':'已掌握'}})
    fact=db.put('learner_fact',{'space_id':'automation','text':'已掌握','evidence_ids':[bad['id']]})
    context=db.put('context_snapshot',{'space_id':'automation','session_id':session['id'],'evidence_ids':[bad['id']]})
    assessments.act(bad['id'],action(bad))
    assert db.get(summary['id'])['status']=='stale'
    assert db.get(fact['id'])['assessment_evidence_invalid']
    assert db.get(context['id'])['stale']


def test_same_title_and_task_in_another_space_are_untouched():
    concept,session,event=records();bad=add_attempt(concept,session,event_id=event['id'])
    other_concept=db.put('concept', {'space_id':'inbox','title':'断言','citation_ids':[],'source_ids':[]})
    other_session=db.put('session', {'space_id':'inbox','concept_id':other_concept['id'],'current_task':session['current_task']})
    other_attempt=db.put('attempt', {'space_id':'inbox','concept_id':other_concept['id'],'session_id':other_session['id'],
        'question':bad['question'],'answer':'其他空间答案','score':1,'error_type':'unknown'})
    learning.recompute(other_concept['id']);other_mastery=db.get('mastery-'+other_concept['id'])
    assessments.act(bad['id'],action(bad))
    assert db.get(other_attempt['id'])==other_attempt
    assert db.get('mastery-'+other_concept['id'])==other_mastery
    assert not assessments.task_voided(other_session)


def test_run_api_freezes_task_but_null_concept_stays_ungraded(client):
    concept,session,event=records()
    response=client.post('/api/exercises/assertions/runs',json={'space_id':'automation','session_id':session['id'],'concept_id':None,'code':'def test_ok(): assert True','request_id':'run-free-0001'})
    assert response.status_code==200,response.text
    payload=response.json()['payload']
    assert payload['concept_id'] is None and payload['session_id']==session['id']
    assert payload['task_binding']=='session_context_only'
    assert 'task_id' not in payload and 'task_concept_id' not in payload
    assessments.act(event['id'],action(event,'question'))
    denied=client.post('/api/exercises/assertions/runs',json={'space_id':'automation','session_id':session['id'],'code':'def test_ok(): assert True','request_id':'run-free-0002','task_id':session['current_task_id']})
    assert denied.status_code==409 and len(db.all('job'))==1


def test_inflight_run_result_is_real_but_withdrawn_and_can_be_explicitly_restored(monkeypatch):
    concept,session,event=records()
    payload={'space_id':'automation','domain_id':'automation-testing','session_id':session['id'],'concept_id':concept['id'],
        'exercise_id':'assertions','code':'from app_under_test import discount\nimport pytest\ndef test_discount():\n    assert discount(100,.2)==80\n    with pytest.raises(ValueError): discount(-1,.2)',**assessments.run_task_scope(session['id'],session['current_task_id'],'assertions')}
    job=jobs.enqueue('run',payload)
    monkeypatch.setattr(runner,'status',lambda:{'ready':True})
    monkeypatch.setattr(runner.shutil,'which',lambda name:'/fake/docker')
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:None)
    def complete(*args):
        assessments.act(event['id'],action(event,'question'))
        return {'passed':True,'counts':{'tests':1,'failures':0,'errors':0,'skipped':0},'exit_code':0,'stop_reason':None,
                'error_type':'unknown','duration_ms':1,'stdout':'1 passed','stderr':'','junit':'<testsuite tests="1"/>'}
    monkeypatch.setattr(runner,'_run_case',complete)
    result=runner.execute(job)
    assert result['exit_code']==0 and result['stdout']=='1 passed'
    assert result['assessment_voided'] and result['assessment_void_id']
    attempt=db.get('attempt-'+job['id'])
    assert attempt['score']==1 and attempt['assessment_voided']
    assert db.get('mastery-'+concept['id'])['evidence_count']==0 and not db.all('review')
    assessments.act(event['id'],action(db.get(event['id']),'question',request='restore-late-run'),'restore')
    assert not db.get(attempt['id'])['assessment_voided']
    assert db.get('mastery-'+concept['id'])['evidence_count']==1


def test_voided_original_mistake_cannot_reenter_review_or_recommendations(monkeypatch,client):
    from server import study
    concept,session,event=records();bad=add_attempt(concept,session,score=0,event_id=event['id'])
    mistake=db.put('mistake', {'space_id':'automation','concept_id':concept['id'],'attempt_id':bad['id'],'status':'pending_analysis','question':'错误原题'})
    old_review=db.put('session', {'space_id':'automation','concept_id':concept['id'],'mistake_id':mistake['id'],'status':'active','skill_id':'review','current_task':{'question':'旧补练'},'step':1})
    assessments.act(bad['id'],action(bad))
    denied=client.post('/api/skill-sessions',json={'space_id':'automation','skill_id':'review','mistake_id':mistake['id']})
    assert denied.status_code==400 and '作废' in denied.json()['detail']
    called=[];monkeypatch.setattr(models,'structured',lambda *a,**k:called.append(True))
    job=jobs.enqueue('session',{'session_id':old_review['id'],'action':'answer','answer':'旧会话尝试继续'})
    jobs.tick()
    assert db.get(job['id'])['status']=='failed' and '作废' in db.get(job['id'])['error']
    assert not called and db.get(mistake['id'])['status']=='voided'
    assert len(db.all('attempt'))==1
    state=study.state('automation')
    assert not state['mistakes'] and (state['session'] or {}).get('id')!=old_review['id']


def test_derived_dialogue_is_removed_immediately_when_its_evidence_is_voided():
    concept,session,event=records();bad=add_attempt(concept,session,event_id=event['id'])
    context=db.put('context_snapshot', {'space_id':'automation','session_id':session['id'],'evidence_ids':[bad['id']]})
    followup=db.put('event', {'space_id':'automation','concept_id':concept['id'],'session_id':session['id'],'context_id':context['id'],
        'action':'continue','output':{'question':'基于错误评分的下一题','explanation':'错误依赖'}})
    conversation=db.put('conversation',{'space_id':'automation','context_id':context['id'],'answer':'依据旧评分推断掌握'})
    summary=db.put('summary',{'space_id':'automation','session_id':session['id'],'status':'ready','evidence_ids':[followup['id']],
        'output':{'learned':[],'unresolved':[],'next_step':''}})
    assert memory.visible(followup['id']) and memory.visible(conversation['id'])
    assessments.act(bad['id'],action(bad))
    assert not memory.visible(followup['id']) and not memory.visible(conversation['id'])
    assert db.get(summary['id'])['status']=='stale'
    assert db.get(session['id'])['current_task_stale']


def test_unrelated_fixed_exercise_is_context_only_and_survives_ai_question_void(client):
    concept,session,event=records()
    # This run belongs to the same conversation, but tests discount, not the AI's
    # invalid question. Older automatically frozen IDs are not reliable evidence.
    unrelated=db.put('run', {'space_id':'automation','session_id':session['id'],'concept_id':concept['id'],
        'task_id':'task-bad','task_event_id':event['id'],'task_question':session['current_task']['question'],
        'exercise_id':'assertions','exit_code':0,'counts':{'tests':1}})
    fixed=add_attempt(concept,session,run_id=unrelated['id'],question='验证 discount 的固定要求',task_id='task-bad',is_transfer=False)
    learning.recompute(concept['id'])
    assessments.act(event['id'],action(event,'question'))
    assert not db.get(unrelated['id']).get('assessment_voided')
    assert not db.get(fixed['id']).get('assessment_voided')
    assert db.get('mastery-'+concept['id'])['score']==.3
    # An invalid AI question doesn't prevent independent use of the fixed lab.
    response=client.post('/api/exercises/assertions/runs',json={'space_id':'automation','session_id':session['id'],
        'concept_id':None,'code':'def test_ok(): assert True','request_id':'independent-fixed-001'})
    assert response.status_code==200 and response.json()['payload']['task_binding']=='session_context_only'
    assert 'task_id' not in response.json()['payload']


@pytest.mark.parametrize('explicit',[False,True])
def test_fixed_rubric_pass_does_not_complete_ai_review_transfer(monkeypatch,explicit):
    concept,session,event=records()
    original=add_attempt(concept,session,question='原始的断言错题',score=0)
    mistake=db.put('mistake', {'space_id':'automation','concept_id':concept['id'],'attempt_id':original['id'],
        'status':'pending_practice','practice_attempts':[]})
    session=db.update(session['id'], {'skill_id':'review','mistake_id':mistake['id'],
        'current_task':{'question':'用 apply_tax 完成另一道迁移题','is_transfer':True}})
    scope=assessments.run_task_scope(session['id'],session['current_task_id'] if explicit else None,'assertions')
    payload={'space_id':'automation','domain_id':'automation-testing','session_id':session['id'],'concept_id':concept['id'],
        'exercise_id':'assertions','code':'from app_under_test import discount\nimport pytest\ndef test_discount():\n    assert discount(100,.2)==80\n    with pytest.raises(ValueError): discount(-1,.2)',**scope}
    job=jobs.enqueue('run',payload)
    monkeypatch.setattr(runner,'status',lambda:{'ready':True})
    monkeypatch.setattr(runner.shutil,'which',lambda name:'/fake/docker')
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:None)
    monkeypatch.setattr(runner,'_run_case',lambda *a:{'passed':True,'counts':{'tests':1,'failures':0,'errors':0,'skipped':0},
        'exit_code':0,'stop_reason':None,'error_type':'unknown','duration_ms':1,'stdout':'1 passed','stderr':'','junit':'<testsuite/>'})
    result=runner.execute(job)
    attempt=db.get('attempt-'+job['id'])
    assert attempt['score']==1 and attempt['grading_scope']=='fixed_exercise_only'
    assert not attempt['is_transfer'] and 'discount' in attempt['question'] and 'apply_tax' not in attempt['question']
    assert not learning.task_answered(db.get(session['id']))
    assert db.get(mistake['id'])['status']=='pending_practice' and db.get(mistake['id'])['practice_attempts']==[]
    assert db.get('mastery-'+concept['id'])['transfer_verified'] is False
    assert result['session_id']==session['id']
    if not explicit:assert 'task_id' not in result
    from server import practice_memory
    assert result['id'] in {x['id'] for x in practice_memory.evidence(session)}


def test_explicit_link_checks_current_task_revision_and_requires_session():
    concept,session,event=records()
    with pytest.raises(HTTPException) as err:
        assessments.run_task_scope(session['id'],'previous-task','assertions')
    assert err.value.status_code==409
    with pytest.raises(ValueError):assessments.run_task_scope(None,'task-bad','assertions')
    linked=assessments.run_task_scope(session['id'],session['current_task_id'],'assertions')
    assert linked['task_binding']=='explicit_user' and linked['grading_scope']=='fixed_exercise_only'


def test_legacy_current_question_is_not_answered_by_a_fixed_run_grade():
    concept,session,event=records()
    run=db.put('run', {'space_id':'automation','session_id':session['id'],'exercise_id':'assertions','exit_code':0})
    add_attempt(concept,session,run_id=run['id'],question=session['current_task']['question'])
    assert not learning.task_answered(db.get(session['id']))
