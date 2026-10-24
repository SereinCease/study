from pathlib import Path
from pydantic import BaseModel,Field
from scripts.live30_acceptance import Harness,REPORT,write_json
from server import models,memory
OUT=Path('reports/score-budget-fix')
class Grade(BaseModel):
    score:float=Field(ge=0,le=1)
    feedback:str=Field(min_length=1)
with Harness(REPORT) as h:
    calls=h.call_ids();events=[e for e in h.records('event',h.require_session()) if e['action']=='answer'];e=events[29]
    snapshot=h.db.get(e['context_id'],'context_snapshot');scope=snapshot['assessment_scope']
    with models.options(reasoning='standard'):
        grade=models.structured('只按原题和rubric核对实际作答。静态分析不是执行。全部满足为1，不为可选建议扣分。严谨核对比较运算、异常类型和match；feedback逐项写明理由，不出新题。',{'question':scope['question'],'rubric':scope['rubric'],'answer':e['answer']},Grade)
    write_json(OUT/'round30-real-check.json',{'event_id':e['id'],'assessment_scope':scope,'grade':grade,'model_call_id':models.last_call()})
    if grade['score']!=1.0:raise ValueError('真实核对与预先逐项审查不一致，请人工核对，不自动补分')
    before=h.records('mastery');question=h.db.get(h.require_session(),'session')['current_task']
    reason='验收代理逐项核对原题：余额不足与limit消息不匹配判断正确；kind和余额单违规输入及match正确；urgent前置条件、2000边界返回8000均正确。标准档真实复核同为1.0；只补静态作答评价，不增加执行证据。'
    payload={'space_id':h.state['space_id'],'revision':e['revision'],'request_id':'recover-real-round30-20261024','score':1.0,'reason':reason}
    a=h.api('POST','/api/assessments/'+e['id']+'/recover-missing',json=payload)
    replay=h.api('POST','/api/assessments/'+e['id']+'/recover-missing',json=payload)
    write_json(OUT/'recovery.json',{'before_mastery':before,'after_mastery':h.records('mastery'),'assessment':a,'replay_same_id':replay['id']==a['id'],'original_output_unchanged':h.db.get(e['id'],'event')['output']==e['output'],'current_question_unchanged':h.db.get(h.require_session(),'session')['current_task']==question,'summaries_after_recovery':[{'id':s['id'],'status':s['status']} for s in h.records('summary',h.require_session())]})
    h.api('PATCH','/api/skill-sessions/'+h.require_session(),json={'reasoning':'standard'})
    h.summary();h.finish(h.api('POST','/api/evidence/reindex')['id']);h.drain()
    ctx,manifest=memory.context(h.db.get(h.require_session(),'session'),'continue')
    write_json(OUT/'current-context.json',{'context':ctx,'manifest':manifest})
    write_json(OUT/'calls.json',[c for c in h.records('model_call') if c['id'] not in calls])
