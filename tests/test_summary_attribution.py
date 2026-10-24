"""A corrected AI grade must not reappear as a learner deficit in summaries."""
import json
from server import db, jobs, memory, models


def setup_records():
    concept=db.put('concept',{'space_id':'automation','domain_id':'automation-testing','title':'契约断言','citation_ids':[]})
    session=db.put('session',{'space_id':'automation','domain_id':'automation-testing','concept_id':concept['id'],
        'skill_id':'explain','memory_enabled':True,'definition':{'profile_id':'default','version':1}})
    event=db.put('event',{'space_id':'automation','session_id':session['id'],'concept_id':concept['id'],
        'action':'answer','answer':'参数化一个函数生成四个case，不能证明全部边界。',
        'output':{'question':'下一题：分析负金额异常','rubric':'按已知实现判断',
            'score':.95,'feedback':'缺少case数量与函数数量的区别。','explanation':'用户没有解释case数量。',
            'inference':'推测尚未理解参数化'}})
    attempt=db.put('attempt',{'space_id':'automation','session_id':session['id'],'concept_id':concept['id'],
        'event_id':event['id'],'score':.95,'feedback':event['output']['feedback'],'answer':event['answer'],
        'error_type':'unknown','hint_level':0,'answer_seen':False})
    return session,event,attempt


def test_api_grade_correction_reaches_summary_without_overwriting_original(client,monkeypatch):
    s,e,a=setup_records();seen=[]
    old=db.put('summary',{'session_id':s['id'],'status':'ready','event_ids':[e['id']],
        'evidence_ids':[a['id']],'output':{'unresolved':['用户尚未区分case与函数']}})
    response=client.patch('/api/attempts/'+a['id'],json={'score':1,'reason':'答案已明确一个函数生成四个case；原AI扣分不成立。'})
    assert response.status_code==200
    assert db.get(old['id'])['status']=='stale'
    before=(db.count('attempt'),db.count('event'))

    def generate(system,content,*args,**kwargs):
        ctx=json.loads(content);seen.append((system,ctx))
        return json.dumps({'corrections':['AI旧评分说必须写match，这是误写的旧扣分转述。','用户订正了契约范围。'],
            'next_step':'截至本批，下一题尚未作答','evidence_ids':[e['id']]})
    monkeypatch.setattr(models,'generate',generate)
    job=memory.schedule_summary(s['id'],True);jobs.tick()
    assert db.get(job['id'])['status']=='completed'
    system,ctx=seen[0];assessment=ctx['assessments'][0];event=ctx['events'][0]
    assert 'score' not in assessment and assessment['effective_score']==1
    assert assessment['assessment_status']=='user_corrected'
    assert assessment['feedback']==response.json()['correction_reason']
    assert 'original_ai_feedback' not in assessment
    assert event['current_assessment']['score']==1 and event['superseded_ai_evaluation']
    assert 'feedback' not in event['output'] and 'explanation' not in event['output'] and 'inference' not in event['output']
    assert event['output']['question']==e['output']['question']
    assert event['answer']==e['answer'] and event['speaker_attribution']=={'answer':'learner','output':'ai'}
    assert 'AI 自身错误' in system and '发言主体' in system and '可选优化建议' in system
    assert db.get(a['id'])['score']==.95 and db.get(e['id'])['output']==e['output']
    assert before==(db.count('attempt'),db.count('event'))
    summary=db.get(db.get(job['id'])['result']['summary_ids'][0])['output']
    assert summary['corrections'][0]==f"用户纠正评价 {a['id']}：原 AI 分 0.95 → 当前有效分 1.0。依据：答案已明确一个函数生成四个case；原AI扣分不成立。"
    assert summary['corrections'][1:]==['用户订正了契约范围。']
    assert a['id'] in summary['evidence_ids']


def test_uncorrected_evaluation_keeps_provenance_and_pending_hypothesis(monkeypatch):
    s,e,a=setup_records();seen=[]
    def generate(system,content,*args,**kwargs):
        ctx=json.loads(content);seen.append(ctx)
        return json.dumps({'next_step':'待核对评价','evidence_ids':[a['id']]})
    monkeypatch.setattr(models,'generate',generate)
    job=memory.schedule_summary(s['id'],True);jobs.tick()
    assert db.get(job['id'])['status']=='completed'
    assert seen[0]['assessments'][0]['assessment_status']=='ai_estimate'
    assert seen[0]['assessments'][0]['effective_score']==.95
    assert seen[0]['events'][0]['output']==e['output']
    assert seen[0]['events'][0]['speaker_attribution']['output']=='ai'
    assert not db.all('mastery')


def test_legacy_summary_output_remains_readable():
    legacy={'learned':['旧摘要'],'unresolved':[],'corrections':[], 'next_step':'继续','evidence_ids':['event-id']}
    parsed=memory.SummaryOutput.model_validate(legacy)
    assert parsed.learned==['旧摘要'] and parsed.practice==[]


def test_recent_corrected_dialogue_uses_current_feedback_without_mutating_original():
    original={'answer':'原答案','assessment_correction':{'corrected_score':1,'correction_reason':'原扣分不成立'},
              'output':{'question':'下一题','rubric':'下一题标准','score':.5,'feedback':'旧扣分','explanation':'旧指责','inference':'旧推断'}}
    projected=memory.safe_event(original)
    assert projected['output']=={'question':'下一题','rubric':'下一题标准','score':1,'feedback':'原扣分不成立'}
    assert original['output']['score']==.5 and original['output']['explanation']=='旧指责'
    assert projected['answer']==original['answer']


def test_historical_corrected_evidence_projects_effective_grade(client):
    s,e,a=setup_records()
    client.patch('/api/attempts/'+a['id'],json={'score':1,'reason':'原扣分不成立，原答案已覆盖'})
    for ident in (a['id'],e['id']):
        hit=memory.model_evidence({'id':ident,'excerpt':'旧扣分','revision':1})
        assert '原扣分不成立，原答案已覆盖' in hit['excerpt']
        assert '缺少case数量与函数数量的区别' not in hit['excerpt']
    assert db.get(a['id'])['score']==.95


def test_long_code_dialogues_are_summarized_in_bounded_batches(monkeypatch):
    s,e,a=setup_records();sizes=[]
    for i in range(8):
        db.put('event',{'space_id':s['space_id'],'session_id':s['id'],'concept_id':s['concept_id'],
            'action':'answer','answer':'代码说明'*1200,'output':{'question':'下一题','rubric':'标准'}})
    def generate(system,content,*args,**kwargs):
        sizes.append(len(content));ctx=json.loads(content)
        return json.dumps({'next_step':'继续','evidence_ids':[ctx['allowed_evidence_ids'][0]]})
    monkeypatch.setattr(models,'generate',generate)
    job=memory.schedule_summary(s['id'],True);jobs.tick()
    assert db.get(job['id'])['status']=='completed'
    assert len(sizes)>1 and max(sizes)<50000
    assert len({i for x in db.all('summary') for i in x['event_ids']})==9
    assert db.count('attempt')==1
