import pytest
from server import db, ingest, jobs, learning, models


def setup_session(monkeypatch):
    source=ingest.import_bytes('assertions.md',b'# Assertions\nAssert expected values.')
    jobs.tick();chunk=db.all('chunk')[0]
    concept=db.put('concept',{'title':'断言','definition':'核对预期','source_ids':[source['id']],'citation_ids':[chunk['id']]})
    calls=[];response={'question':'如何验证 total == 100？'}
    def generate(system,ctx,schema,profile_id='default',validate=None):
        calls.append(ctx)
        assert '必须提出与当前题不同的新题' in system
        result=schema.model_validate({'explanation':'解释','question':response['question'],'rubric':'检查预期值','citations':[chunk['id']],
            'score':1 if ctx['action']=='answer' else None,'is_transfer':True})
        if validate:validate(result)
        return result.model_dump()
    monkeypatch.setattr(models,'structured',generate)
    session=learning.start('explain',concept_id=concept['id']);jobs.tick()
    return session,concept,response,calls


def step(session,action):
    job=jobs.enqueue('session',{'session_id':session['id'],'action':action,'answer':'验证预期'});jobs.tick()
    return db.get(job['id'])


def test_same_question_is_scored_once_and_job_retry_stays_idempotent(monkeypatch):
    session,concept,response,calls=setup_session(monkeypatch)
    response['question']='  如何验证  total == 100?  '
    answer_job=step(session,'answer')
    assert answer_job['status']=='completed'
    assert learning.task_answered(db.get(session['id']))
    assert len(db.all('attempt'))==1
    score=db.get('mastery-'+concept['id'])['score']
    count=len(calls)
    # Recovery of the committed job must succeed before the already-answered guard.
    learning.session_step(answer_job)
    assert len(calls)==count and len(db.all('attempt'))==1
    assert step(session,'answer')['status']=='failed'
    assert len(calls)==count and len(db.all('attempt'))==1
    assert db.get('mastery-'+concept['id'])['score']==score


def test_hint_reveal_and_repeated_continue_cannot_unlock_a_scored_question(monkeypatch):
    session,concept,response,calls=setup_session(monkeypatch)
    step(session,'answer')
    for action in ('hint','reveal','continue'):
        assert step(session,action)['status']=='completed'
        assert calls[-1]['current_task_answered'] is True
        assert learning.task_answered(db.get(session['id']))
    assert len(db.all('attempt'))==1
    response['question']='如何验证 total != 100？'
    assert step(session,'continue')['status']=='completed'
    assert learning.task_answered(db.get(session['id'])) is False
    assert step(session,'answer')['status']=='completed'
    assert len(db.all('attempt'))==2
    last=db.select('attempt',filters={'session_id':session['id']},limit=1,descending=True)[0]
    assert last['answer_seen'] is False and last['hint_level']==0


def test_legacy_session_checks_latest_attempt_in_the_same_session():
    session=db.put('session',{'current_task':{'question':'验证结果  是否为 True？'}})
    db.put('attempt',{'session_id':'other','question':'验证结果 是否为 True?','score':1})
    assert learning.task_answered(session) is False
    db.put('attempt',{'session_id':session['id'],'question':'验证结果 是否为 True?','score':0})
    assert learning.task_answered(session) is True
    assert learning.task_answered({**session,'current_task':{'question':'设计一个边界情况？'}}) is False
    # Explicit state from a later, genuinely new task takes precedence over legacy lookup.
    assert learning.task_answered({**session,'current_task_answered':False}) is False


def test_a_different_question_returned_by_scoring_is_immediately_answerable(monkeypatch):
    session,concept,response,calls=setup_session(monkeypatch)
    response['question']='如何验证负数边界？'
    step(session,'answer')
    assert learning.task_answered(db.get(session['id'])) is False
    assert step(session,'answer')['status']=='completed'
    assert len(db.all('attempt'))==2
    assert learning.task_answered(db.get(session['id'])) is True


def test_blank_question_after_grading_does_not_unlock_repeat_submissions(monkeypatch):
    session,concept,response,calls=setup_session(monkeypatch)
    response['question']=''
    step(session,'answer')
    assert learning.task_answered(db.get(session['id'])) is True
    assert step(session,'answer')['status']=='failed'
    assert len(db.all('attempt'))==1
