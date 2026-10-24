"""Explicit fixed-runner evidence, after teaching driver releases its lock."""
from scripts.live30_acceptance import Harness,REPORT,write_json
with Harness(REPORT) as h:
    before=len(h.records('attempt'));s=h.api('GET','/api/skill-sessions/'+h.require_session());results=[]
    for label,expected in [('wrong',90),('correct',80)]:
        code=f'''import pytest
from app_under_test import discount

def test_discount():
    assert discount(100,.2) == {expected}

def test_negative():
    with pytest.raises(ValueError,match="nonnegative"):
        discount(-1,.2)
'''
        job=h.api('POST','/api/exercises/assertions/runs',json={'space_id':h.state['space_id'],'goal_id':h.state['goal_id'],'concept_id':None,'session_id':h.require_session(),'request_id':'live30-practice-'+label,'code':code})
        h.finish(job['id']);results.append(h.db.get('run-'+job['id'],'run'))
    after=h.api('GET','/api/skill-sessions/'+h.require_session())
    write_json(REPORT/'practice-check.json',{'runs':results,'attempt_count_before':before,'attempt_count_after':len(h.records('attempt')),'current_task_unchanged':s['current_task']==after['current_task'],'task_answered_unchanged':s.get('current_task_answered')==after.get('current_task_answered')})
    h.summary();h.finish(h.api('POST','/api/evidence/reindex')['id']);h.drain()
