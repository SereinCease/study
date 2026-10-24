"""Live memory checks after scripts.acceptance, using the same isolated data dir."""
import json,os,subprocess,sys
from pathlib import Path
from fastapi.testclient import TestClient
from server import db,jobs,memory,models
from server.app import app

def main():
    if not os.environ.get('LEARNLOOP_DATA_DIR'):raise SystemExit('An isolated LEARNLOOP_DATA_DIR is required.')
    db.init();report={'data_directory':str(db.DATA),'model':models.profile()['model'],'checks':[]}
    def check(name,condition):
        report['checks'].append({'name':name,'passed':bool(condition)})
        print(name+': '+('PASS' if condition else 'FAIL'),flush=True)
        if not condition:raise RuntimeError(name)
    def finish(job):
        while db.get(job['id'])['status'] in ('queued','running'):
            if not jobs.tick():raise RuntimeError('No runnable job')
        result=db.get(job['id']);check('真实任务 '+result['task'],result['status']=='completed')
        return result['result']
    try:
        session=next(s for s in db.all('session') if s['skill_id']=='explain')
        attempt=next(a for a in db.all('attempt') if a.get('session_id')==session['id'])
        with TestClient(app) as client:
            client.headers['x-learnloop-token']=client.get('/api/session').json()['token']
            fact=memory.save_fact({'text':'验收偏好：先给一个断言例子，再解释原理。','category':'preference'})
            before=(db.count('attempt'),json.dumps(db.all('mastery'),sort_keys=True))
            finish(memory.schedule_summary(session['id'],True))
            summaries=[s for s in db.all('summary') if s['session_id']==session['id'] and s['status']=='ready']
            check('摘要有真实来源且不新增成绩',bool(summaries) and all(db.get(i) for s in summaries for i in s['output']['evidence_ids']) and before==(db.count('attempt'),json.dumps(db.all('mastery'),sort_keys=True)))
            finish(jobs.enqueue('memory_index',{}))
            hits=memory.search('不用任何断言',concept_id=session['concept_id'],limit=30)
            check('历史混合检索找回原始错误作答',any(x['id']==attempt['id'] or x.get('id')==attempt.get('event_id') for x in hits['items']) and '本地语义' in hits['modes'])
            ctx,manifest=memory.context(db.get(session['id']),action='continue')
            check('统一上下文包含档案与真实摘要',fact['id'] in manifest['fact_ids'] and bool(manifest['summary_ids']) and bool(ctx['current_task']))
            draft_url='/api/skill-sessions/'+session['id']+'/draft'
            current=client.get(draft_url).json()
            saved=client.put(draft_url,json={'text':'验收草稿：下一步补充边界条件断言。','revision':current['revision']})
            check('草稿保存与并发版本保护',saved.status_code==200 and client.put(draft_url,json={'text':'过期覆盖','revision':current['revision']}).status_code==409)
            child=subprocess.run([sys.executable,'-c',"from server import db; import json; db.init(); print(json.dumps({'events':db.count('event'),'summaries':db.count('summary'),'draft':db.get('answer-'+"+repr(session['id'])+")['text']}))"],capture_output=True,text=True,check=True)
            restored=json.loads(child.stdout)
            check('新进程恢复摘要与草稿',restored['summaries']>=1 and restored['events']==db.count('event') and restored['draft']==saved.json()['text'])
            check('导出不包含当前密钥',models.key().encode() not in client.get('/api/export').content)
            report['summary_ids']=[s['id'] for s in summaries];report['event_count']=db.count('event')
            report['context_manifest']=manifest
            report['status']='passed'
    except Exception as e:report.update(status='failed',error=str(e))
    report['finished_at']=db.now()
    (db.ROOT/'reports/memory-acceptance-v3.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('Memory acceptance:',report['status'],report.get('error',''),flush=True)
    if report['status']!='passed':raise SystemExit(1)

if __name__=='__main__':main()
