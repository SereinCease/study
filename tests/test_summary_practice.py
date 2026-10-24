"""Actual execution facts remain scoped, versioned, and separate from AI assessment."""
import json
from server import db, memory, models, jobs


def session(**changes):
    concept=db.put('concept',{'space_id':'automation','title':'断言','citation_ids':[]})
    return db.put('session',{'space_id':'automation','goal_id':'goal-a','concept_id':concept['id'],
        'skill_id':'explain','domain_id':'automation-testing','memory_enabled':True,
        'definition':{'profile_id':'default','version':1},**changes})


def run(s,**changes):
    return db.put('run',{'space_id':s['space_id'],'goal_id':s.get('goal_id'),
        'concept_id':s.get('concept_id'),'exercise_id':'assertions','code':'assert discount(100,.2)==80',
        'exit_code':0,'counts':{'tests':1,'failures':0,'errors':0},'requirements_met':True,
        'report_origin':'learning_code','rubric_checks':[{'requirement':'正确断言','met':True}],**changes})


def fake_model(monkeypatch):
    seen=[]
    def generate(system,content,*args,**kwargs):
        ctx=json.loads(content);seen.append(ctx)
        return json.dumps({'next_step':'继续复测，不宣称已掌握','practice':['真实运行已完成'] if ctx['practice_evidence'] else [],
            'evidence_ids':ctx['allowed_evidence_ids']})
    monkeypatch.setattr(models,'generate',generate)
    return seen


def summarize(s):
    job=memory.schedule_summary(s['id'],True)
    assert job
    jobs.tick()
    assert db.get(job['id'])['status']=='completed',db.get(job['id'])
    return db.get(job['id'])['result']


def test_practice_scope_requires_exact_goal_space_and_explicit_link(client):
    s=session();a=run(s);b=run(s,concept_id=None,session_id=s['id'])
    for changes in ({'space_id':'foreign'}, {'goal_id':'foreign'}, {'goal_id':None},
                    {'concept_id':'other'}, {'concept_id':None},
                    {'goal_id':'foreign','session_id':s['id']},
                    {'space_id':'foreign','session_id':s['id']}):run(s,**changes)
    ctx,manifest=memory.context(s)
    assert {x['id'] for x in ctx['practice_evidence']}=={a['id'],b['id']}
    assert set(manifest['practice_ids'])=={a['id'],b['id']}
    assert manifest['practice_revisions']=={a['id']:1,b['id']:1}
    assert manifest['source_revisions'][b['id']]==1
    api=client.get('/api/skill-sessions/'+s['id']+'/memory').json()
    assert {x['id'] for x in api['practice_evidence']}=={a['id'],b['id']}
    assert api['summary_needs_update'] is True
    assert next(x for x in api['practice_evidence'] if x['id']==b['id'])['association']=='explicit_session'
    assert all('静态源码结构检查' in x['rubric_scope'] and '失败后' in x['rubric_scope'] for x in api['practice_evidence'])
    assert not db.all('attempt') and not db.all('mastery')


def test_run_only_summary_is_incremental_and_never_scores(client,monkeypatch):
    s=session();a=run(s);seen=fake_model(monkeypatch)
    first=summarize(s)
    assert seen[-1]['events']==[] and seen[-1]['assessments']==[]
    assert seen[-1]['allowed_evidence_ids']==[a['id']]
    summary=db.get(first['summary_ids'][0])
    assert summary['practice_ids']==[a['id']] and summary['source_revisions']=={a['id']:1}
    assert memory.schedule_summary(s['id'],True) is None
    b=run(s,concept_id=None,session_id=s['id'],exit_code=1)
    assert client.get('/api/skill-sessions/'+s['id']+'/memory').json()['summary_needs_update']
    summarize(s)
    assert [x['id'] for x in seen[-1]['practice_evidence']]==[b['id']]
    assert len(seen)==2
    assert client.get('/api/skill-sessions/'+s['id']+'/memory').json()['summary_needs_update'] is False
    assert not db.all('attempt') and not db.all('mastery')


def test_same_run_can_be_summarized_for_two_sessions_without_id_collision(monkeypatch):
    s=session();other=session(concept_id=s['concept_id']);run(s);fake_model(monkeypatch)
    a=summarize(s);b=summarize(other)
    assert a['summary_ids']!=b['summary_ids']
    assert len(db.all('summary'))==2


def test_changed_or_disabled_run_invalidates_summary_and_is_not_resurrected(monkeypatch):
    s=session();a=run(s);fake_model(monkeypatch);result=summarize(s)
    db.update(a['id'],{'assessment_voided':True,'assessment_void_id':'void-1'})
    assert db.get(result['summary_ids'][0])['status']=='stale'
    assert memory.visible(a['id'])  # execution fact survives, old assessment does not
    result=summarize(s)
    assert db.get(result['summary_ids'][0])['source_revisions'][a['id']]==2
    ctx,_=memory.context(s)
    assert ctx['practice_evidence'][0]['assessment_voided'] is True
    memory.visibility(a['id'],False)
    assert not memory.context(s)[0]['practice_evidence']
    assert memory.schedule_summary(s['id'],True) is None
    db.init()
    assert not memory.context(s)[0]['practice_evidence']


def test_summary_rejects_run_changed_during_generation(monkeypatch):
    s=session();a=run(s)
    def generate(system,content,*args,**kwargs):
        db.update(a['id'],{'exit_code':1})
        return json.dumps({'next_step':'重试','evidence_ids':[a['id']]})
    monkeypatch.setattr(models,'generate',generate)
    job=memory.schedule_summary(s['id'],True);jobs.tick()
    assert db.get(job['id'])['status']=='failed'
    assert not db.all('summary')


def test_practice_batches_bound_context_without_losing_unsummarized_runs(monkeypatch):
    s=session();runs=[run(s) for _ in range(11)];seen=fake_model(monkeypatch)
    assert len(memory.context(s)[0]['practice_evidence'])==6
    result=summarize(s)
    assert len(result['summary_ids'])==3
    assert [len(x['practice_evidence']) for x in seen]==[4,4,3]
    assert {r['id'] for x in seen for r in x['practice_evidence']}=={r['id'] for r in runs}
    assert memory.schedule_summary(s['id'],True) is None


def test_void_question_keeps_previous_valid_grade_but_removes_invalid_prompt(monkeypatch):
    s=session()
    e=db.put('event',{'session_id':s['id'],'goal_id':s['goal_id'],'concept_id':s['concept_id'],
        'action':'answer','answer':'有效上一题回答','output':{'question':'错误新题','rubric':'虚构标准','feedback':'上一题正确','score':1}})
    a=db.put('attempt',{'session_id':s['id'],'goal_id':s['goal_id'],'concept_id':s['concept_id'],'event_id':e['id'],'score':1})
    db.update(e['id'],{'question_voided':True})
    assert memory.visible(e['id']) and memory.visible(a['id'])
    ctx,_=memory.context(s)
    assert 'question' not in ctx['history'][0]['output']
    assert ctx['history'][0]['output']['score']==1
    seen=fake_model(monkeypatch);summarize(s)
    assert 'question' not in seen[0]['events'][0]['output']
    assert seen[0]['assessments'][0]['id']==a['id']
    db.update(a['id'],{'assessment_voided':True,'assessment_void_id':'void-1'})
    assert not memory.visible(a['id']) and not memory.visible(e['id'])
    assert not memory.context(s)[0]['history']
    assert all(x['status']=='stale' for x in db.all('summary'))


def test_invalid_assessment_fact_exits_profile_context_and_index_after_restart():
    s=session()
    fact=memory.save_fact({'text':'据错误题评分推测已经掌握断言','category':'self_report','space_id':s['space_id'],'domain_id':'automation-testing'})
    assert fact['id'] in {x['id'] for x in memory.context(s)[0]['learner_facts']}
    db.update(fact['id'],{'assessment_evidence_invalid':True,'needs_review':True})
    db.init()
    assert fact['id'] not in {x['id'] for x in memory.profile(space_id=s['space_id'])['facts']}
    ctx,manifest=memory.context(s)
    assert fact['id'] not in {x['id'] for x in ctx['learner_facts']}
    assert fact['id'] not in manifest['fact_ids']
    assert not memory.visible(fact['id'])  # Learner facts are never indexed as success evidence.
    assert not any(x['id']==fact['id'] for x in memory.search('错误题评分',semantic=False,space_id=s['space_id'])['items'])
    assert db.get(fact['id'])['needs_review'] is True  # Preserve audit, do not silently restore.


def test_practice_projection_preserves_binding_but_does_not_claim_ai_task_passed():
    s=session()
    actual=run(s,session_id=s['id'],task_binding='session_context_only',grading_scope='fixed_exercise_only',linked_exercise_id='assertions')
    projected=memory.practice_memory.evidence(s)[0]
    assert projected['id']==actual['id']
    assert projected['task_binding']=='session_context_only'
    assert projected['grading_scope']=='fixed_exercise_only'
    assert projected['linked_exercise_id']=='assertions'
    assert '不能证明' in ''.join(projected['practice_rules'])
    assert '不能据此完成或评分该 AI 题' in ''.join(projected['practice_rules'])
    assert projected['requirements_met'] is True
    assert not db.all('attempt') and not db.all('mastery')
