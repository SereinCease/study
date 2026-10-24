from scripts.live30_acceptance import Harness,REPORT,write_json
with Harness(REPORT) as h:
    events=[e for e in h.records('event',h.require_session()) if e['action']=='answer']
    target=events[15] # This event generated the invalid question answered at round 17.
    base='/api/assessments/'+target['id']
    current=h.api('GET',base,params={'space_id':h.state['space_id'],'subject':'question'})
    reason='验收核对：题17的late_fee中0不满足days_late<0，(0,gold)应抛LookupError，原raises(ValueError)会失败；删除max仍失败，不能写成仍绿/双违反。rubric与实现矛盾；六个收集项也不能由片段4 passed推成完整运行。作答自身亦有矛盾，撤回该题评价而非补造满分。'
    before=h.records('mastery')
    result=h.api('POST',base+'/void',json={'space_id':h.state['space_id'],'revision':current['revision'],'subject':'question','request_id':'live30-invalid-question17','reason':reason})
    write_json(REPORT/'invalid-question17.json',{'reason':reason,'result':result,'before_mastery':before,'after_mastery':h.records('mastery')})
    attempt=next(a for a in h.records('attempt',h.require_session()) if a.get('event_id')==events[22]['id'])
    correction=h.api('PATCH','/api/attempts/'+attempt['id'],json={'score':1.0,'reason':'验收核对：第23轮作答中 ValueError 与 RuntimeError 均直接继承 Exception 的陈述正确。AI反馈误改谈 LookupError，无该题事实依据，不得视为用户缺口；三项评分仍为1.0。'})
    write_json(REPORT/'round23-feedback-correction.json',correction)
    a26=next(a for a in h.records('attempt',h.require_session()) if a.get('event_id')==events[25]['id'])
    url='/api/assessments/'+a26['id']
    described=h.api('GET',url,params={'space_id':h.state['space_id'],'subject':'assessment'})
    r26=h.api('POST',url+'/void',json={'space_id':h.state['space_id'],'revision':described['revision'],'subject':'assessment','request_id':'live30-assessment26-review','reason':'验收核对：作答把test_x不存在的match加入分析，AI未识别该错误；test_y红绿自相矛盾。test_w在本机Python表达式1000*1.01**2==1020.1实际为True，不能教成必红。评分理由未准确区分这些缺口，撤回本次评价而非猜测更正分；原题原答保留。'})
    write_json(REPORT/'round26-assessment-void.json',r26)
    h.summary();h.finish(h.api('POST','/api/evidence/reindex')['id']);h.drain()
