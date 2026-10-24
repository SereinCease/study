"""Explicit UI-only fixture. Refuses the personal data directory; no cloud calls."""
import os
from pathlib import Path
from server import db,ingest,jobs,learning

def main():
    if not os.environ.get('LEARNLOOP_UI_FIXTURE') or db.DATA.resolve()==(db.ROOT/'server/data').resolve():raise SystemExit('Use an explicitly isolated UI fixture directory')
    db.init()
    if db.count('session'):return
    source=ingest.import_bytes('界面验收教程.md','# fixture：用好前置与清理\npytest fixture 将测试需要的准备与清理放在一起。yield 前准备，yield 后释放资源。每个测试应该独立，不依赖执行顺序。\n\n# 断言：验证预期结果\n断言将实际结果与明确预期比较。对异常场景使用 pytest.raises。'.encode())
    jobs.tick();chunks=db.all('chunk')
    c=db.put('concept',{'title':'fixture 生命周期','definition':'集中管理准备与资源释放，让测试彼此独立。','purpose':'隔离测试状态','example':'@pytest.fixture\ndef resource():\n    value = open_resource()\n    yield value\n    value.close()','source_ids':[source['id']],'citation_ids':[chunks[0]['id']],'points':['使用 yield 后清理'],'pitfalls':['共享可变状态'],'prerequisites':[]})
    d=db.put('concept',{'title':'异常断言','definition':'用 pytest.raises 验证预期的异常。','source_ids':[source['id']],'citation_ids':[chunks[-1]['id']]})
    db.put('relation',{'source':c['id'],'target':d['id'],'type':'application','confirmed':False,'confidence':.8,'reason':'界面验收关系','citation_ids':[chunks[0]['id']]})
    g=db.put('goal',{'title':'UI 验收样例 · Checkout 测试','minutes':30,'source_ids':[source['id']]})
    s=db.put('session',{'skill_id':'explain','concept_id':c['id'],'goal':'UI 验收样例（非真实学习记录）','definition':learning.skill('explain'),'memory_enabled':True,'status':'active','current_task':{'question':'如果测试抛出异常，yield 后的清理仍然需要执行吗？','rubric':'解释资源释放和测试隔离'},'step':2})
    e=db.put('event',{'session_id':s['id'],'concept_id':c['id'],'action':'answer','answer':'只要测试能运行通过，就可以共享文件句柄。','model':'UI fixture · 本地验收数据','reasoning':'standard','output':{'explanation':'将资源准备与释放成对管理。fixture 的作用不仅是复用代码，也包含测试隔离。','feedback':'这段答案遗漏了资源释放和测试间状态隔离，需要继续验证。','question':s['current_task']['question'],'rubric':s['current_task']['rubric'],'citations':[chunks[0]['id']],'inference':'界面验收文本，不是模型调用结果。'}})
    a=db.put('attempt',{'session_id':s['id'],'event_id':e['id'],'concept_id':c['id'],'answer':e['answer'],'question':s['current_task']['question'],'feedback':e['output']['feedback'],'score':0,'error_type':'concept','answer_seen':False,'hint_level':0})
    db.put('mistake',{'session_id':s['id'],'concept_id':c['id'],'attempt_id':a['id'],'question':a['question'],'answer':a['answer'],'error_type':'concept','status':'pending_practice','practice_attempts':[]})
    learning.recompute(c['id'])
    db.put('learner_fact',{'text':'界面验收偏好：先给最小例子，再解释原理。','category':'preference','source':'user','domain_id':'global','memory_enabled':True})
    db.put('summary',{'session_id':s['id'],'concept_id':c['id'],'event_ids':[e['id']],'evidence_ids':[e['id'],a['id']],'status':'ready','output':{'learned':['讨论了 fixture 的准备与清理'],'unresolved':['尚未验证异常时的清理'],'corrections':[],'next_step':'用自己的话解释资源释放','evidence_ids':[e['id']]}})
    db.put('context_snapshot',{'session_id':s['id'],'estimated_tokens':1400,'budget':12000,'source_ids':[chunks[0]['id']],'evidence_ids':[a['id']],'summary_ids':[db.all('summary')[0]['id']],'fact_ids':[db.all('learner_fact')[0]['id']]})
    print('UI fixture ready:',s['id'])
if __name__=='__main__':main()
