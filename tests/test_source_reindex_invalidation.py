"""Rebuilding derived indexes must not invalidate unchanged source evidence."""
import pytest
from server import db,ingest,jobs,memory


def cited_source():
    source=ingest.import_bytes('fixture.md',b'# Fixture\nA fixture isolates test data.\n',space_id='automation')
    jobs.tick()
    source=db.get(source['id']);assert source['status']=='ready'
    chunk=next(x for x in db.all('chunk') if x['source_id']==source['id'])
    concept=db.put('concept',{'space_id':'automation','title':'Fixture','source_ids':[source['id']],'citation_ids':[chunk['id']]})
    session=db.put('session',{'space_id':'automation','concept_id':concept['id'],'skill_id':'explain','current_task':{'question':'如何隔离？','rubric':'独立数据'},'memory_enabled':True})
    snapshot=db.put('context_snapshot',{'space_id':'automation','session_id':session['id'],'source_ids':[chunk['id']],'concept_ids':[concept['id']],'source_revisions':{chunk['id']:chunk['revision']}})
    event=db.put('event',{'space_id':'automation','session_id':session['id'],'concept_id':concept['id'],'context_id':snapshot['id'],'action':'answer','answer':'每次创建独立数据','output':{}})
    summary=db.put('summary',{'space_id':'automation','session_id':session['id'],'status':'ready','event_ids':[event['id']],'evidence_ids':[event['id']],'material_versions':{chunk['id']:chunk['revision'],source['id']:source['revision']},'output':{'next_step':'验证清理'}})
    relation=db.put('relation',{'space_id':'automation','source':concept['id'],'target':'other-concept','type':'reference','inferred':True,'confirmed':True,'status':'confirmed','source_revisions':{chunk['id']:chunk['revision']}})
    return source,chunk,session,snapshot,event,summary,relation


def test_reindex_unchanged_source_keeps_cited_learning_state(client,monkeypatch):
    source,chunk,session,snapshot,event,summary,relation=cited_source()
    before_epoch=memory.epoch()
    monkeypatch.setattr(ingest,'vector',lambda text:[1.,0.])
    response=client.post('/api/sources/'+source['id']+'/reindex',json={'space_id':'automation'})
    assert response.status_code==200
    jobs.tick()
    assert db.get(response.json()['id'])['status']=='completed'
    updated=db.get(source['id'])
    assert updated['status']=='ready' and updated['vector_count']==1 and updated['warning'] is None
    assert updated['hash']==source['hash'] and updated['version']==source['version']
    assert db.get(chunk['id'])==chunk
    assert memory.epoch()==before_epoch
    assert not db.get(snapshot['id']).get('stale')
    assert db.get(summary['id'])['status']=='ready'
    assert db.get(relation['id'])['status']=='confirmed'
    assert not db.get(session['id']).get('current_task_stale')
    assert memory.visible(event['id'])


@pytest.mark.parametrize('changes',[{'hash':'different-content-hash'},{'deleted':True},{'memory_enabled':False},{'name':'renamed.md'},{'url':'https://example.com/new'},{'version':2}])
def test_material_or_visibility_changes_still_invalidate_dependents(changes):
    source,chunk,session,snapshot,event,summary,relation=cited_source()
    db.update(source['id'],changes)
    assert db.get(snapshot['id'])['stale']
    assert db.get(summary['id'])['status']=='stale'
    assert db.get(relation['id'])['status']=='stale'
    assert db.get(session['id'])['current_task_stale']
    assert not memory.visible(event['id'])
    assert db.get(event['id'])['answer']==event['answer']


def test_operational_metadata_and_unchanged_visibility_do_not_invalidate():
    source,chunk,session,snapshot,event,summary,relation=cited_source()
    before_epoch=memory.epoch()
    db.update(source['id'],{'status':'indexing','job_id':'another-job','vector_count':0,'chunk_count':1,'warning':'index pending','error':None,'deleted':False,'memory_enabled':True})
    assert memory.epoch()==before_epoch and not db.get(snapshot['id']).get('stale')
    assert db.get(summary['id'])['status']=='ready' and memory.visible(event['id'])
    # Chunk content and concept updates retain the existing invalidation policy.
    db.update(chunk['id'],{'text':'Changed explanation'})
    assert db.get(snapshot['id'])['stale'] and db.get(summary['id'])['status']=='stale'
