from server import db,spaces,entries


def cards():
    source=db.put('source',{'name':'断言.md','status':'ready','version':3,'hash':'original'})
    spaces.attach_source('automation',source['id'])
    chunk=db.put('chunk',{'source_id':source['id'],'path':'断言.md','text':'检查边界'})
    concept=db.put('concept',{'space_id':'automation','title':'边界断言','citation_ids':[chunk['id'],chunk['id']],
                              'source_ids':[source['id']]})
    return source,chunk,concept


def test_card_note_canonical_scope_sources_and_idempotency(client):
    source,chunk,concept=cards()
    payload={'space_id':'automation','card_kind':'concept','card_id':concept['id'],'request_id':'card-create-01'}
    response=client.post('/api/entries/from-card',json=payload)
    assert response.status_code==200,response.text
    note=response.json()
    assert note['concept_id']==concept['id'] and note['space_id']=='automation'
    assert {(r['kind'],r['id']) for r in note['references']}=={('source',source['id']),('chunk',chunk['id'])}
    assert all(r['source_version']==3 and r['content_hash']=='original' for r in note['references'])
    assert not note['memory_enabled'] and not db.all('attempt') and not db.all('mastery')
    updated=client.patch('/api/entries/'+note['id'],json={'revision':note['revision'],'body':'我的理解'}).json()
    replay=client.post('/api/entries/from-card',json=payload).json()
    assert replay['id']==note['id'] and replay['body']=='我的理解' and db.count('entry')==1
    assert client.patch('/api/entries/'+note['id'],json={'revision':note['revision'],'body':'另一标签'}).status_code==409
    assert client.post('/api/entries/from-card',json={**payload,'card_kind':'source','card_id':source['id']}).status_code==409
    assert len(entries.revisions(note['id'])['items'])==1
    assert updated['concept_id']==concept['id']


def test_source_card_never_guesses_concept_and_cross_space_rejected(client):
    source,chunk,concept=cards()
    payload={'space_id':'automation','card_kind':'source','card_id':source['id'],'request_id':'source-note-01'}
    note=client.post('/api/entries/from-card',json=payload).json()
    assert note['concept_id'] is None and note['references'][0]['id']==source['id']
    for kind,id in [('source',source['id']),('concept',concept['id'])]:
        response=client.post('/api/entries/from-card',json={**payload,'space_id':'inbox','card_kind':kind,'card_id':id})
        assert response.status_code==400,response.text
    assert db.count('entry')==1


def test_unavailable_citations_explicit_and_deleted_cards_rejected(client):
    source,chunk,concept=cards()
    db.update(chunk['id'],{'deleted':True})
    payload={'space_id':'automation','card_kind':'concept','card_id':concept['id'],'request_id':'missing-citation'}
    note=client.post('/api/entries/from-card',json=payload).json()
    assert note['capture_warnings'] and [r['kind'] for r in note['references']]==['source']
    db.update(source['id'],{'version':4})
    assert client.get('/api/entries/'+note['id']).json()['needs_review']
    db.update(concept['id'],{'merged_into':'new-concept'})
    assert client.post('/api/entries/from-card',json={**payload,'request_id':'another-request'}).status_code==400
    db.update(concept['id'],{'deleted':True})
    assert client.post('/api/entries/from-card',json={**payload,'request_id':'deleted-request'}).status_code==400
