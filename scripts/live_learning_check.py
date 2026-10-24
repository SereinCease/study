"""Explicit live Gemini acceptance. Uses an isolated data directory, never prints credentials."""
import json
from pathlib import Path
from server import db,ingest,jobs,learning,models

def completed(id):
    jobs.tick()
    job=db.get(id,'job')
    if job['status']!='completed':raise RuntimeError(job.get('error','task failed'))
    return job

def main():
    db.init()
    report={}
    try:
        report['connection']=models.generate('Reply only OK.','Connection test; no materials.')[:50]
        source=ingest.import_bytes('自动化测试入门.md',(db.ROOT/'samples/自动化测试入门.md').read_bytes())
        completed(source['job_id'])
        report['source']=db.get(source['id'])
        session=learning.start('decompose',[source['id']],goal='完成 Checkout 自动化测试')
        completed(session['job_id'])
        for a in db.all('artifact'):learning.accept_artifact(a['id'])
        report['concepts']=len(db.all('concept'))
        if not report['concepts']:raise RuntimeError('没有生成知识卡')
        concept=db.all('concept')[0]
        report['qa']=learning.ask(concept['title'])
        goal=db.put('goal',{'title':'完成 Checkout 自动化测试','source_ids':[source['id']],'minutes':30})
        report['path']=learning.goal_path(goal)
        s=learning.start('explain',concept_id=concept['id'],goal=goal['title'])
        completed(s['job_id'])
        job=jobs.enqueue('session',{'session_id':s['id'],'action':'answer','answer':'不用断言，只要测试不报异常就说明业务结果完全正确。'})
        completed(job['id'])
        report['attempts']=db.all('attempt');report['mistakes']=db.all('mistake')
        if report['mistakes']:
            review=learning.start('review',mistake_id=report['mistakes'][0]['id'])
            completed(review['job_id']);report['review_session']=db.get(review['id'])['id']
        report['mastery']=db.all('mastery');report['reviews']=db.all('review')
        db.init();report['persistence']=len(db.all('event'))>=2
        report['model_calls']=db.all('model_call')
        report['status']='passed'
    except Exception as e:
        report['status']='failed';report['error']=str(e)
    output=db.ROOT/'reports/live-learning.json';output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'status':report['status'],'error':report.get('error'),'concept_count':report.get('concepts'),'report':str(output)},ensure_ascii=False))
    if report['status']!='passed':raise SystemExit(1)

if __name__=='__main__':main()
