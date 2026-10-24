import pytest
from server import db,spaces,exams,models


def exam(kind='single_choice',**values):
    data={'space_id':'inbox','title':'例题','prompt':'选择正确答案','question_type':kind,'options':[{'id':'A','text':'甲'},{'id':'B','text':'乙'}] if kind.endswith('choice') else [],'answer':'A','max_score':100,**values}
    q=exams.save(data)
    return exams.verify(q['id'],q['revision'])

@pytest.mark.parametrize('kind,expected,right,wrong',[
 ('single_choice','A','A','B'),('multiple_choice',['A','B'],['B','A'],['A']),
 ('true_false',True,True,False),('fill_blank',['Apple','banana'],[' apple ','BANANA'],['pear','orange'])])
def test_objective_grading_is_deterministic_and_idempotent(kind,expected,right,wrong):
    q=exam(kind,answer=expected)
    a=exams.submit(q['id'],{'answer':right,'request_id':'correct-request'})
    b=exams.submit(q['id'],{'answer':wrong,'request_id':'wrong-request'})
    assert a['score']==1 and b['score']==0 and a['evaluator']=='verified_answer'
    again=exams.submit(q['id'],{'answer':wrong,'request_id':'correct-request'})
    assert again['id']==a['id'] and db.count('attempt')==2
    assert db.count('mistake')==1 and not db.all('mastery')


def test_unverified_rejects_scoring_and_edit_revokes_verification():
    q=exams.save({'space_id':'inbox','prompt':'题目','question_type':'true_false','answer':True})
    with pytest.raises(ValueError,match='核对'):exams.submit(q['id'],{'answer':True,'request_id':'request-1'})
    q=exams.verify(q['id'],q['revision']);q=exams.save({'revision':q['revision'],'answer':False},q['id'])
    assert not q['verified']
    with pytest.raises(ValueError):exams.submit(q['id'],{'answer':True,'request_id':'request-2'})


def test_reveal_cannot_be_passed_as_independent_success():
    concept=db.put('concept',{'space_id':'inbox','domain_id':'general','title':'真值','source_ids':[],'citation_ids':[]})
    q=exam('true_false',answer=True,concept_id=concept['id'])
    assert 'answer' not in exams.practice(q['id']) and 'rubric' not in exams.practice(q['id'])
    exams.reveal(q['id'])
    a=exams.submit(q['id'],{'answer':True,'request_id':'revealed-answer','answer_seen':False})
    assert a['answer_seen'] and db.get('mastery-'+concept['id'])['evidence_count']==0


def test_cross_space_concept_rejected():
    concept=db.put('concept',{'space_id':'automation','title':'自动化'})
    with pytest.raises(ValueError,match='空间'):exam('true_false',answer=True,concept_id=concept['id'])


def test_subjective_scores_require_rubric_and_are_ai_estimates(monkeypatch):
    with pytest.raises(ValueError):exam('essay',answer='参考',rubric=[])
    q=exam('essay',answer='参考',rubric=[{'id':'logic','label':'论证逻辑','points':60},{'id':'examples','label':'论据','points':40}])
    def generated(system,context,schema,validate=None,**kwargs):
        output={'criteria':[{'id':'logic','points':42,'evidence':'第一段论点','feedback':'补充推导'},{'id':'examples','points':20,'evidence':'第二段案例','feedback':'证据不足'}],'feedback':'按维度练习','uncertainty':'AI估计，非官方阅卷'}
        validate(schema.model_validate(output));return output
    monkeypatch.setattr(models,'structured',generated)
    a=exams.submit(q['id'],{'answer':'我的回答','request_id':'essay-request'})
    assert a['points']==62 and a['score']==.62 and a['evaluator']=='ai_estimate' and a['graded_status']=='estimated'
    assert a['question_snapshot']['rubric']==q['rubric']


def test_subjective_out_of_bounds_never_writes_attempt(monkeypatch):
    q=exam('essay',answer='参考',rubric=[{'id':'logic','label':'论证','points':100}])
    def generated(system,context,schema,validate=None,**kwargs):
        output={'criteria':[{'id':'logic','points':101,'evidence':'原文','feedback':'内容'}],'feedback':'内容','uncertainty':'AI估计'}
        validate(schema.model_validate(output));return output
    monkeypatch.setattr(models,'structured',generated)
    with pytest.raises(ValueError,match='超出'):exams.submit(q['id'],{'answer':'作答','request_id':'bad-score-request'})
    assert not db.all('attempt')


def test_fill_blank_accepts_verified_alternatives():
    q=exam('fill_blank',answer=[['颜色','色彩'],['红','红色']])
    a=exams.submit(q['id'],{'answer':['色彩','红色'],'request_id':'aliases-request'})
    assert a['score']==1
    b=exams.submit(q['id'],{'answer':['颜色','蓝色'],'request_id':'partial-request'})
    assert b['score']==.5


def test_transfer_status_cannot_be_claimed_by_submission():
    q=exam('true_false',answer=True)
    attempt=exams.submit(q['id'],{'answer':True,'request_id':'claim-transfer','is_transfer':True})
    assert not attempt['is_transfer']
    q=exam('true_false',answer=True,is_transfer=True)
    attempt=exams.submit(q['id'],{'answer':True,'request_id':'curated-transfer','is_transfer':False})
    assert attempt['is_transfer']


def test_server_practice_clock_resume_overtime_and_session_idempotency(monkeypatch):
    q=exam('true_false',answer=True,time_limit_seconds=60)
    monkeypatch.setattr(db,'now',lambda:'2026-10-23T10:00:00+00:00')
    p=exams.start_practice(q['id'])
    assert p['deadline']=='2026-10-23T10:01:00+00:00'
    assert exams.start_practice(q['id'])['id']==p['id']
    assert exams.practice(q['id'])['active_practice']['id']==p['id']
    monkeypatch.setattr(db,'now',lambda:'2026-10-23T10:01:10+00:00')
    a=exams.submit(q['id'],{'answer':True,'request_id':'timed-request-1','practice_session_id':p['id']})
    assert a['elapsed_seconds']==70 and a['overtime'] and a['score']==1
    repeated=exams.submit(q['id'],{'answer':False,'request_id':'other-tab-new-key','practice_session_id':p['id']})
    assert repeated['id']==a['id'] and db.count('attempt')==1
    assert exams.practice(q['id'])['active_practice'] is None


def test_practice_clock_rejects_wrong_question_and_old_revision():
    q=exam('true_false',answer=True);other=exam('true_false',answer=False)
    p=exams.start_practice(q['id'])
    with pytest.raises(ValueError,match='不属于'):exams.submit(other['id'],{'answer':False,'request_id':'wrong-clock','practice_session_id':p['id']})
    updated=exams.save({'revision':q['revision'],'prompt':'新题面'},q['id']);exams.verify(q['id'],updated['revision'])
    with pytest.raises(ValueError,match='更新'):exams.submit(q['id'],{'answer':True,'request_id':'outdated-clock','practice_session_id':p['id']})
    assert exams.start_practice(q['id'])['id']!=p['id']


def test_question_goal_and_concept_membership():
    concept=db.put('concept',{'space_id':'inbox','title':'月相','source_ids':[]})
    goal=db.put('goal',{'space_id':'inbox','title':'另一个目标','source_ids':[],'concept_ids':[]})
    with pytest.raises(ValueError,match='纳入'):exam('true_false',answer=True,concept_id=concept['id'],goal_id=goal['id'])


def test_false_answer_is_preserved_in_mistake_record():
    q=exam('true_false',answer=False)
    exams.submit(q['id'],{'answer':True,'request_id':'false-standard-request'})
    assert db.all('mistake')[0]['expected'] is False
