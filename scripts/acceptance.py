"""End-to-end acceptance against the configured live model and Docker, in LEARNLOOP_DATA_DIR.
Run explicitly: this sends the bundled sample lesson to the configured model.
"""
import io,json,zipfile,os
from pathlib import Path
from fastapi.testclient import TestClient
from server import db,ingest,jobs,learning,models,runner
from server.app import app

GOOD='''import pytest
from app_under_test import discount

def test_discount():
    assert discount(100, .2) == 80
    assert discount(200, .25) == 150
    with pytest.raises(ValueError):
        discount(-1, .2)
'''
TAX='''import pytest

def test_calculate_tax():
    assert calculate_tax(200, 0.1) == 20
    with pytest.raises(ValueError):
        calculate_tax(200, -0.1)
'''
PROJECT='''from playwright.sync_api import Page, expect

class CheckoutPage:
    def __init__(self, page: Page): self.page = page
    def open(self): self.page.goto('http://127.0.0.1:8765')
    def submit(self): self.page.get_by_role('button', name='提交订单').click()

def test_initial(page: Page):
    checkout = CheckoutPage(page)
    checkout.open()
    expect(page.get_by_role('status')).to_have_text('等待提交')

def test_submit(page: Page):
    checkout = CheckoutPage(page)
    checkout.open()
    checkout.submit()
    expect(page.get_by_role('status')).to_have_text('订单已确认')
'''

def main():
    db.init();report={'data_directory':str(db.DATA),'checks':[],'provider':models.profile()['provider'],'model':models.profile()['model']}
    output=Path(os.environ.get('LEARNLOOP_ACCEPTANCE_REPORT',str(db.ROOT/'reports/acceptance.json')))
    def check(name,condition,detail=''):
        report['checks'].append({'name':name,'passed':bool(condition),'detail':detail})
        print(name+': '+('PASS' if condition else 'FAIL'),flush=True)
        if not condition:raise RuntimeError(name+' failed: '+str(detail))
    def finish(id):
        while jobs.tick():
            if db.get(id)['status'] in ('completed','failed','cancelled'):break
        j=db.get(id);check('任务 '+j['task'],j['status']=='completed',j.get('error') or '')
        return j.get('result')
    try:
        with TestClient(app) as client:
            client.headers['x-learnloop-token']=client.get('/api/session').json()['token']
            if os.environ.get('LEARNLOOP_ACCEPTANCE_RESUME')=='1' and db.all('goal'):
                previous=json.loads(output.read_text())
                if previous.get('data_directory')!=str(db.DATA):
                    raise ValueError('恢复验收必须使用上一份报告对应的数据目录')
                report['checks']=[c for c in previous['checks'] if c['passed']]
                source=db.all('source')[0]
                concept=next((c for c in db.all('concept') if '断言' in c['title'] and '重试' not in c['title']),db.all('concept')[0])
                goal=db.all('goal')[0]
            else:
                test=client.post('/api/model-profiles/default/test',json={})
                check('已配置模型真实连接',test.status_code==200,test.json())
                source=client.post('/api/sample',json={}).json();finish(source['job_id'])
                archive=io.BytesIO()
                with zipfile.ZipFile(archive,'w') as z:
                    for p in (db.ROOT/'samples/checkout_project').rglob('*'):
                        if p.is_file() and '__pycache__' not in p.parts:z.write(p,str(p.relative_to(db.ROOT/'samples')))
                repo=client.post('/api/sources/import',files={'file':('checkout-project.zip',archive.getvalue())}).json();finish(repo['job_id'])
                check('Python 项目导入只解析代码',any(c['path'].endswith('checkout.py') for c in db.all('chunk')))
                check('章节原文定位',all(c['location'].get('section') for c in db.all('chunk') if c['source_id']==source['id']))
                s=learning.start('decompose',[source['id']],goal='完成 Checkout 自动化测试');finish(s['job_id'])
                for a in db.all('artifact'):
                    learning.accept_artifact(a['id'])
                concept=next((c for c in db.all('concept') if '断言' in c['title'] and '重试' not in c['title']),db.all('concept')[0])
                edit=client.patch('/api/concepts/'+concept['id'],json={'purpose':concept['purpose']+'（已核对）'})
                check('采纳与编辑知识卡',edit.status_code==200 and db.get(concept['id'])['purpose'].endswith('（已核对）'))
                check('可定位引用',all(db.get(id,'chunk') for c in db.all('concept') for id in c['citation_ids']))
                qa_response=client.post('/api/ask',json={'question':'为什么不能只验证测试没有抛异常？'})
                qa=qa_response.json()
                check('有来源问答',qa_response.status_code==200 and bool(qa.get('citations')),qa.get('answer') or qa)
                goal=client.post('/api/goals',json={'title':'完成 Checkout 自动化测试','source_ids':[source['id']],'minutes':30}).json()
                check('学习路径',bool(client.get('/api/goals/'+goal['id']+'/path').json()))
                teach=learning.start('explain',concept_id=concept['id'],goal=goal['title']);finish(teach['job_id'])
                event=client.post('/api/skill-sessions/'+teach['id']+'/events',json={'action':'answer','answer':'我认为不用任何断言，只要运行不抛异常就能保证业务功能完全正确。','request_id':db.uid()}).json();finish(event['id'])
                check('保留错误作答证据',bool(db.all('mistake')) and bool(db.all('attempt')))
            check('Docker 可执行',runner.status()['ready'],runner.status())
            run=client.post('/api/exercises/assertions/runs',json={'code':'from app_under_test import discount\ndef test_wrong():\n    assert discount(100,.2)==100\n','concept_id':concept['id'],'request_id':db.uid()}).json()
            bad=finish(run['id']);check('真实失败报告',bad['counts']['failures']==1 and not bad['passed'])
            mistake=next(m for m in reversed(db.all('mistake')) if m.get('run_id')==bad['id'])
            review=learning.start('review',mistake_id=mistake['id'],goal='针对原来的折扣断言错误，请出一题税额迁移题：要求定义 test_calculate_tax，断言 calculate_tax(200, 0.1) == 20，负数税率抛 ValueError。只提问不要先给答案，该新问题的 is_transfer 必须为 true。');finish(review['job_id'])
            response=client.post('/api/skill-sessions/'+review['id']+'/events',json={'action':'answer','answer':TAX,'request_id':db.uid()}).json();finish(response['id'])
            check('原答案与补练分别保存',len(db.get(mistake['id']).get('practice_attempts',[]))>=1)
            check('真实 AI 迁移补练后进入已纠正待复习',db.get(mistake['id'])['status']=='corrected_pending_review',db.get(mistake['id'])['status'])
            client.put('/api/exercises/assertions/draft',json={'code':GOOD})
            run=client.post('/api/exercises/assertions/runs',json={'code':GOOD,'concept_id':concept['id'],'request_id':db.uid()}).json()
            good=finish(run['id']);check('修正代码真实通过',good['requirements_met'])
            check('掌握度和复习持久化',db.get('mastery-'+concept['id'])['score']>0 and bool(db.get('review-'+concept['id'])['due']))
            project=client.post('/api/exercises/checkout-project/runs',json={'code':PROJECT,'concept_id':concept['id'],'request_id':db.uid()}).json()
            result=finish(project['id']);check('Checkout 浏览器项目验收',result['requirements_met'] and result['counts']['tests']==2,result['stderr'])
            before=(len(db.all('source')),len(db.all('event')),len(db.all('attempt')))
            db.init();check('重新初始化后记录仍在',before==(len(db.all('source')),len(db.all('event')),len(db.all('attempt'))))
            archive=client.get('/api/export/archive').content
            with zipfile.ZipFile(io.BytesIO(archive)) as z:
                check('学习档案导出',bool(z.read('learning.json')) and not any('.env' in n or '.secrets' in n for n in z.namelist()))
            check('密钥未进入 API 导出',models.key().encode() not in client.get('/api/export').content)
            report['summary']={'sources':len(db.all('source')),'concepts':len(db.all('concept')),'events':len(db.all('event')),'attempts':len(db.all('attempt')),'mistakes':len(db.all('mistake')),'runs':len(db.all('run'))}
            report['mastery']=db.all('mastery');report['reviews']=db.all('review');report['run_summaries']=[{k:r[k] for k in ('exercise_id','exit_code','counts','requirements_met','duration_ms')} for r in db.all('run')]
            report['status']='passed'
    except Exception as e:
        report['status']='failed';report['error']=str(e)
    report['finished_at']=db.now()
    output.parent.mkdir(parents=True,exist_ok=True)
    rendered=json.dumps(report,ensure_ascii=False,indent=2)
    output.write_text(rendered)
    history=output.parent/'history';history.mkdir(exist_ok=True)
    (history/('acceptance-'+report['finished_at'].replace(':','-')+'.json')).write_text(rendered)
    print('Acceptance:',report['status'],report.get('error',''),flush=True)
    if report['status']!='passed':raise SystemExit(1)
if __name__=='__main__':main()
