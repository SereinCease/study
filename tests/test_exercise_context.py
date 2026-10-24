"""Review guidance must describe the fixed runnable task, not invented requirements."""
import json
import pytest
from server import db,exercises,memory,models,jobs,learning


def review_session(exercise_id=None,with_run=True):
    concept=db.put('concept',{'title':'断言','source_ids':[],'citation_ids':[]})
    scope={'space_id':concept['space_id'],'concept_id':concept['id'],'domain_id':'automation-testing'}
    attempt=db.put('attempt',{**scope,'exercise_id':exercise_id,'answer':'测试草稿','score':0})
    run=db.put('run',{**scope,'exercise_id':exercise_id,'exit_code':1,'stdout':'assertion failed','stderr':'','counts':{'tests':1,'failures':1},'requirements_met':False}) if with_run else None
    mistake=db.put('mistake',{**scope,'attempt_id':attempt['id'],'run_id':run['id'] if run else None,'question':'修复测试','answer':'测试草稿'})
    session=db.put('session',{**scope,'skill_id':'review','mistake_id':mistake['id'],'definition':{'profile_id':'default','version':1},'memory_enabled':True})
    return session,run,attempt


def test_review_context_keeps_true_discount_contract_and_run_identity():
    session,run,_=review_session('assertions')
    ctx,manifest=memory.context(session)
    contract=ctx['exercise_contract']
    assert contract['exercise_id']=='assertions'
    assert contract['provided_files']==exercises.get('assertions')['files']
    assert ctx['run']['id']==run['id'] and ctx['run']['exercise_id']=='assertions'
    assert ctx['review_constraints']['follow_up_mode']=='fixed_runner_contract'
    assert '静态练习' in ''.join(ctx['review_constraints']['rules'])
    assert manifest['exercise_contract']['version']==contract['version']
    # Execute only our fixed trusted fixture to guard the exact boundary at issue.
    namespace={}
    exec(contract['provided_files']['app_under_test.py'],namespace)
    assert namespace['discount'](100,-.1)==pytest.approx(110)
    with pytest.raises(ValueError):namespace['discount'](-1,.2)
    assert 'rate 的范围' in contract['prompt']
    assert not db.all('mastery')


def test_review_contract_follows_attempt_id_and_does_not_guess_from_concept():
    session,_,_=review_session('fixtures',with_run=False)
    ctx,_=memory.context(session)
    assert ctx['exercise_contract']['exercise_id']=='fixtures'
    unknown,_,_=review_session('unsupported-lab',with_run=False)
    ctx,_=memory.context(unknown)
    assert ctx['exercise_contract'] is None
    assert ctx['review_constraints']['follow_up_mode']=='static_only'
    assert ctx['review_constraints']['executable_exercise_id'] is None


@pytest.mark.parametrize('exercise_id',['locators','waiting','checkout-project'])
def test_browser_exercises_expose_minimum_runnable_environment(exercise_id):
    exercise=exercises.get(exercise_id);contract=exercises.contract(exercise_id)
    assert exercises.SITE_URL in exercise['prompt']
    assert 'page: Page fixture' in exercise['prompt']
    assert '提交订单' in exercise['prompt'] and 'role="status"' in exercise['prompt']
    assert contract['site']['html']==exercises.SITE
    assert contract['site']['url']==exercises.SITE_URL
    assert contract['provided_files']==exercise['files']
    assert contract['version']==exercises.contract(exercise_id)['version']


def test_review_prompt_distinguishes_static_hypothesis_from_runner():
    prompt=(learning.SKILL_DIR/'review.md').read_text()
    assert 'exercise_contract' in prompt and 'review_constraints' in prompt
    assert '静态练习（假设，当前实训环境不支持）' in prompt


def test_summary_explicit_allowlist_rejects_nested_source_and_run_ids(monkeypatch):
    session,run,_=review_session('assertions')
    event=db.put('event',{'session_id':session['id'],'concept_id':session['concept_id'],'action':'answer','answer':'边界分析','output':{'explanation':'日志需核对','citations':['foreign-chunk'],'evidence_ids':['foreign-run']}})
    attempt=db.put('attempt',{'session_id':session['id'],'concept_id':session['concept_id'],'event_id':event['id'],'score':0,'answer':'边界分析'})
    seen=[]
    def reject(system,content,*args,**kwargs):
        ctx=json.loads(content);seen.append(ctx)
        assert set(ctx['allowed_evidence_ids'])=={event['id'],attempt['id'],run['id']}
        assert '不得使用' in system and '嵌套' in system
        return json.dumps({'next_step':'复测','evidence_ids':['foreign-run']})
    monkeypatch.setattr(models,'generate',reject)
    job=memory.schedule_summary(session['id'],True);jobs.tick()
    assert db.get(job['id'])['status']=='failed' and not db.all('summary')
    assert len(seen)==2  # Existing strict validation and bounded repair remain.
    def accept(system,content,*args,**kwargs):
        ctx=json.loads(content)
        return json.dumps({'next_step':'复测','practice':['真实运行失败，按契约补练'],'evidence_ids':ctx['allowed_evidence_ids']})
    monkeypatch.setattr(models,'generate',accept)
    job=memory.schedule_summary(session['id'],True);jobs.tick()
    assert db.get(job['id'])['status']=='completed'
    assert set(db.all('summary')[0]['output']['evidence_ids'])=={event['id'],attempt['id'],run['id']}
