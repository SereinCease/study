"""Live provider regression with synthetic student answers in an isolated data directory.
This checks persistence and context, not human learning effectiveness or cross-day retention.
"""
import os,json,time
from pathlib import Path
from server import db,jobs,ingest,learning,memory,models,spaces


def complete(id):
    for _ in range(100):
        j=db.get(id,'job')
        if j['status']=='completed':return j
        if j['status'] in ('failed','cancelled'):raise RuntimeError(j.get('error') or j['status'])
        jobs.tick()
    raise RuntimeError('任务队列未完成')


def main():
    if 'acceptance-v4' not in str(db.DATA):raise RuntimeError('必须使用独立 acceptance-v4 数据目录')
    db.init();report={'started':db.now(),'synthetic_student':True,'real_provider':True,'human_effectiveness':'not_tested','cross_day':'pending_real_next_day','failures':[]}
    try:
        with models.options(reasoning='fast'):
            report['connection']=models.test_connection('default','fast')
        source=ingest.import_bytes('fixture验收.md','# fixture 资源隔离\n\npytest fixture 使用函数作用域，每个测试创建独立列表。yield 前准备资源，yield 后清理；清理避免测试间状态污染。\n\n## 断言\nassert 验证实际结果和预期结果，程序没有异常不意味着业务正确。边界负数应使用 pytest.raises 验证 ValueError。\n\n## Checkout\n初始状态为等待提交。提交后状态是订单已确认。使用角色定位器与 expect 的重试断言，不要固定 sleep。'.encode(),space_id='automation')
        complete(source['job_id'])
        s=learning.start('decompose',[source['id']],space_id='automation',reasoning='fast');complete(s['job_id'])
        for a in db.all('artifact'):learning.accept_artifact(a['id'])
        concepts=db.all('concept');assert concepts
        concept=next((c for c in concepts if 'fixture' in (c['title']+c.get('definition','')).lower()),concepts[0])
        goal=db.put('goal',{'space_id':'automation','domain_id':'automation-testing','title':'Checkout 真实模型验收','source_ids':[source['id']],'minutes':20})
        s=learning.start('explain',concept_id=concept['id'],space_id='automation',goal_id=goal['id'],reasoning='fast');complete(s['job_id'])
        for i in range(30):
            answer='早期误解：fixture 无需清理，测试间可以共享可变列表，没有报错就是测试通过。' if i==0 else f'第{i+1}轮独立尝试：fixture 每个测试使用独立数据，yield 后执行清理。assert 应检查实际结果是否符合预期；不能用没有异常代替业务断言。对于资源释放，使用 try/finally 或 fixture teardown，提交订单用角色定位与 expect 重试断言。'
            j=jobs.enqueue('session',{'session_id':s['id'],'action':'answer','answer':answer,'reasoning':'fast'},f'accept-v4-{i}')
            complete(j['id']);print(json.dumps({'round':i+1,'status':'completed'}),flush=True)
        summary=memory.schedule_summary(s['id'],True)
        if summary:complete(summary['id'])
        report['session_id']=s['id'];report['events']=len(memory.events(s['id']));report['attempts']=len(db.all('attempt'));report['summaries']=len([x for x in db.all('summary') if x.get('status')=='ready'])
        report['early_error_search']=[x['id'] for x in memory.search('早期误解 无需清理',space_id='automation',semantic=False)['items']]
        report['corrected_knowledge_search']=[x['id'] for x in memory.search('yield 清理 独立数据',space_id='automation',semantic=False)['items']]
        report['inbox_isolated']=not memory.search('fixture',space_id='inbox',semantic=False)['items']
        last=db.get(s['id'],'session');ctx,manifest=memory.context(last);report['context']={k:manifest[k] for k in ('estimated_tokens','budget','event_ids','summary_ids')}
        before=len(db.all('attempt'));db.init();report['restart_persistence']=len(db.all('attempt'))==before and db.get(s['id'])['current_task']==last['current_task']
        report['status']='passed' if report['events']>=31 and report['summaries'] and report['early_error_search'] and report['inbox_isolated'] and report['restart_persistence'] else 'failed'
    except Exception as e:report['status']='failed';report['failures'].append(str(e))
    report['finished']=db.now();calls=db.all('model_call');report['model_calls']=[{k:c.get(k) for k in ('model','endpoint','reasoning','status','usage','duration_ms','error_code')} for c in calls]
    out=db.ROOT/'reports/acceptance-v4-live.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'status':report['status'],'failures':report['failures'],'calls':len(calls),'report':str(out)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
