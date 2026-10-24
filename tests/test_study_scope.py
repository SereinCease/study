from server import db,ingest,jobs,learning,memory,spaces,study


def source(space,name='evidence.md',body='# 资源隔离\nfixture 负责隔离资源并在 yield 后清理。'):
    s=ingest.import_bytes(name,body.encode(),space_id=space);jobs.tick()
    ch=db.select('chunk',filters={'source_id':s['id']})[0]
    c=db.put('concept',{'space_id':space,'domain_id':spaces.resolve(space)['domain_id'],'title':'资源隔离','definition':'fixture 清理','source_ids':[s['id']],'citation_ids':[ch['id']]})
    return s,c,ch


def test_shared_content_keeps_separate_scope_and_search(client):
    other=spaces.create({'title':'个人备考','domain_id':'postgraduate','mode':'learn'})
    s,c,ch=source('automation');same=ingest.import_bytes(s['name'],'# 资源隔离\nfixture 负责隔离资源并在 yield 后清理。'.encode(),space_id=other['id'])
    assert same['id']==s['id'] and s['id'] in spaces.source_ids(other['id'])
    assert ingest.search('fixture',space_id='inbox')==[]
    assert ingest.search('fixture',space_id=other['id'])
    assert client.post('/api/skill-sessions',json={'space_id':other['id'],'skill_id':'explain','concept_id':c['id']}).status_code==400
    assert client.get('/api/records/concept?space_id='+other['id']).json()['items']==[]
    reindex=client.post('/api/sources/'+s['id']+'/reindex',json={'space_id':other['id']})
    assert reindex.status_code==200 and reindex.json()['space_id']==other['id']
    assert client.post('/api/sources/'+s['id']+'/reindex',json={'space_id':'inbox'}).status_code==400


def test_goal_run_and_draft_scope(client):
    s,c,ch=source('automation')
    goals=[client.post('/api/goals',json={'space_id':'automation','title':str(i),'source_ids':[s['id']]}).json() for i in range(2)]
    for g in goals:
        r=client.put('/api/exercises/assertions/draft',json={'space_id':'automation','goal_id':g['id'],'revision':0,'code':g['title']});assert r.status_code==200
    assert len(db.all('draft'))==2
    run=client.post('/api/exercises/assertions/runs',json={'space_id':'automation','goal_id':goals[0]['id'],'concept_id':c['id'],'code':'assert 1','request_id':'scope-run-1'}).json()
    assert run['payload']['goal_id']==goals[0]['id']
    assert client.get('/api/study-state?space_id=automation&goal_id='+goals[0]['id']).json()['next_action']['kind']=='learn'


def test_draft_restores_selected_concept_and_explicit_ungraded_scope(client):
    s,c,_=source('automation')
    g=db.put('goal',{'space_id':'automation','domain_id':'automation-testing','title':'Draft scope','source_ids':[s['id']]})
    session=db.put('session',{'space_id':'automation','goal_id':g['id'],'concept_id':c['id']})
    endpoint='/api/exercises/assertions/draft'
    payload={'space_id':'automation','goal_id':g['id'],'concept_id':c['id'],'session_id':session['id'],'revision':0,'code':'assert True'}
    saved=client.put(endpoint,json=payload)
    assert saved.status_code==200
    restored=client.get('/api/drafts',params={'space_id':'automation','goal_id':g['id']}).json()[0]
    assert restored['concept_id']==c['id'] and restored['session_id']==session['id']
    # An explicit ungraded choice survives reload; stale tabs cannot restore scoring.
    ungraded=client.put(endpoint,json={**payload,'revision':restored['revision'],'concept_id':None})
    assert ungraded.status_code==200 and ungraded.json()['concept_id'] is None
    assert ungraded.json()['session_id']==session['id']
    assert client.put(endpoint,json={**payload,'revision':restored['revision']}).status_code==409
    assert db.get(restored['id'],'draft')['concept_id'] is None
    other=db.put('concept',{**c,'id':db.uid(),'title':'Another concept'})
    mismatch=client.put(endpoint,json={**payload,'revision':ungraded.json()['revision'],'concept_id':other['id']})
    assert mismatch.status_code==400
    assert not db.all('attempt')


def test_relation_cycles_rejection_and_unknown_confidence(client):
    s,a,ch=source('automation');b=db.put('concept',{**a,'id':db.uid(),'title':'后置知识'})
    r=client.post('/api/relations',json={'source':a['id'],'target':b['id'],'type':'prerequisite'});assert r.status_code==200
    assert r.json()['confidence'] is None
    assert client.post('/api/relations',json={'source':b['id'],'target':a['id'],'type':'prerequisite'}).status_code==400
    assert client.patch('/api/relation-suggestions/'+r.json()['id'],json={'status':'rejected'}).json()['status']=='rejected'
    assert not client.get('/api/graph?space_id=automation&center_id='+a['id']).json()['relations']


def test_notes_opt_in_and_evidence_scope(client):
    s,c,ch=source('automation');g=db.put('goal',{'space_id':'automation','domain_id':'automation-testing','title':'学习','source_ids':[s['id']]})
    entry=client.post('/api/entries',json={'space_id':'automation','title':'自己的理解','body':'仅本地秘密笔记','goal_id':g['id']}).json()
    session={'space_id':'automation','domain_id':'automation-testing','goal_id':g['id'],'concept_id':c['id'],'goal':'学习','memory_enabled':True}
    assert memory.context(session)[0]['notes']==[]
    client.patch('/api/entries/'+entry['id'],json={'revision':entry['revision'],'memory_enabled':True})
    assert memory.context(session)[0]['notes'][0]['id']==entry['id']
    assert not db.all('attempt') and not db.all('mastery')
    db.put('event',{'space_id':'automation','domain_id':'automation-testing','answer':'fixture独有证据','output':{}})
    assert memory.search('fixture',space_id='inbox')['items']==[]
    assert memory.search('fixture',space_id='automation')['items']


def test_graph_paginates_beyond_first_thousand(client):
    with db.connect() as c:
        for i in range(1020):db.put('concept',{'space_id':'automation','title':f'节点{i}','definition':'分页检查','source_ids':[]},con=c)
    page=client.get('/api/graph?space_id=automation&limit=20&offset=1000').json()
    assert page['total']==1020 and len(page['concepts'])==20 and not page['truncated']
    assert all(n['assessment_label']=='暂无评估' for n in page['concepts'])
