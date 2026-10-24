import io,json,zipfile
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from fastapi import HTTPException
from server import db,spaces,entries,domains
from server.knowledge_api import router

@pytest.fixture
def knowledge_client():
    app=FastAPI();app.include_router(router)
    @app.exception_handler(ValueError)
    async def bad(request,exc):return JSONResponse({'detail':str(exc)},400)
    with TestClient(app) as client:yield client


def note(space='inbox',**changes):
    return entries.save({'space_id':space,'title':'随手记','body':'有趣的知识','entry_type':'note',**changes})


def test_default_spaces_domain_registry_and_explicit_resolution():
    assert spaces.resolve()['id']=='inbox'
    assert spaces.resolve(domain_id='automation-testing')['id']=='automation'
    assert len(domains.packages())==4
    with pytest.raises(ValueError):spaces.resolve(domain_id='postgraduate')
    with pytest.raises(ValueError):spaces.resolve('inbox','automation-testing')
    before=db.count('space');db.init();assert db.count('space')==before


def test_notes_isolated_default_not_memory_and_no_mastery():
    a=note();b=note('automation')
    assert not a['memory_enabled']
    assert [x['id'] for x in entries.page('entry','inbox')['items']]==[a['id']]
    assert not db.all('attempt') and not db.all('mastery') and not db.all('review')
    with pytest.raises(ValueError):entries.detail(b['id'],space_id='inbox')
    with pytest.raises(ValueError):note(references=[{'kind':'entry','id':b['id']}])


def test_revision_conflict_history_soft_delete_restore():
    a=note();b=entries.save({'revision':a['revision'],'body':'新版本'},a['id'])
    with pytest.raises(HTTPException) as conflict:entries.save({'revision':a['revision'],'body':'覆盖'},a['id'])
    assert conflict.value.status_code==409 and conflict.value.detail['current']['body']=='新版本'
    assert entries.revisions(a['id'])['items'][0]['body']=='有趣的知识'
    deleted=entries.trash(a['id'],b['revision']);assert not entries.page('entry','inbox')['items']
    assert entries.page('entry','inbox',deleted=True)['items'][0]['id']==a['id']
    restored=entries.trash(a['id'],deleted['revision'],restore=True)
    old=entries.restore_revision(a['id'],1,restored['revision'])
    assert old['body']=='有趣的知识' and old['revision']>restored['revision']


def test_shared_sources_preserve_scope_and_origins():
    source=db.put('source',{'name':'书卷.md','hash':'same','status':'ready'})
    first=spaces.attach_source('inbox',source['id'],'第一份','https://example.com/a')
    same=spaces.attach_source('inbox',source['id'],'第二份','https://example.com/b')
    other=spaces.attach_source('automation',source['id'])
    assert same['id']==first['id'] and len(same['origins'])==2
    assert other['id']!=first['id'] and db.count('source')==1
    assert spaces.allowed_source_ids('inbox')=={source['id']}
    assert spaces.assert_member(source,'automation')['id']==source['id']
    detached=db.update(first['id'],{'deleted':True})
    assert not spaces.allowed_source_ids('inbox') and spaces.allowed_source_ids('automation')=={source['id']}


def test_collections_links_do_not_duplicate_or_delete_entries():
    a=note();b=note('automation')
    collection=entries.save({'space_id':'inbox','title':'读书摘录','entry_ids':[a['id'],a['id']]},kind='collection')
    assert collection['entry_ids']==[a['id']] and db.count('entry')==2
    with pytest.raises(ValueError):entries.save({'revision':collection['revision'],'entry_ids':[b['id']]},collection['id'],'collection')
    entries.trash(collection['id'],collection['revision'],kind='collection')
    assert not entries.detail(a['id']).get('deleted')


def test_attachments_revision_restoration_and_export():
    a=note();a=entries.add_attachment(a['id'],'../../image.txt',b'local file',a['revision'])
    attachment=a['attachments'][0]
    assert attachment['name']=='image.txt'
    old_revision=a['revision'];a=entries.detach_attachment(a['id'],attachment['id'],a['revision'])
    assert not a['attachments'] and entries.attachment(a['id'],attachment['id'])[1].read_bytes()==b'local file'
    a=entries.restore_revision(a['id'],old_revision,a['revision'])
    assert a['attachments'][0]['id']==attachment['id']
    with zipfile.ZipFile(io.BytesIO(entries.export_zip(a['id']))) as z:
        assert 'note.md' in z.namelist() and 'revisions.json' in z.namelist()
        assert z.read('attachments/'+attachment['id']+'-image.txt')==b'local file'
        assert all(not name.startswith('/') and '..' not in name for name in z.namelist())
    b=note()
    with pytest.raises(ValueError):entries.attachment(b['id'],attachment['id'])


def test_pagination_query_literals_and_scope_cursor():
    values=[note(title=f'知识 {i}') for i in range(5)]
    first=entries.page('entry','inbox',limit=2);second=entries.page('entry','inbox',limit=2,cursor=first['next_cursor'])
    assert len(first['items'])==2 and len(second['items'])==2
    assert not ({x['id'] for x in first['items']}&{x['id'] for x in second['items']})
    assert entries.page('entry','inbox',query='%')['items']==[]
    other=note('automation')
    with pytest.raises(ValueError):entries.page('entry','inbox',cursor=other['id'])


def test_opt_in_and_deleted_entry_invalidates_derived_summary():
    a=note(memory_enabled=True)
    # Summary insertion need not create learning attempts.
    summary=db.put('summary',{'space_id':'inbox','domain_id':'general','entry_ids':[a['id']],'status':'ready','output':{},'evidence_ids':[]})
    entries.save({'revision':a['revision'],'memory_enabled':False},a['id'])
    assert db.get(summary['id'])['status']=='stale' and not db.all('mastery')


def test_legacy_migration_assigns_only_explicit_provenance():
    records=[('legacy-goal','goal',{'domain_id':'automation-testing','source_ids':['legacy-source']}),
             ('legacy-source','source',{'name':'old'}),('legacy-unknown','concept',{'title':'pytest 同名不足以判断'}),
             ('legacy-session','session',{'domain_id':'automation-testing','concept_id':'legacy-concept'}),
             ('legacy-concept','concept',{'title':'fixture'}),('legacy-attempt','attempt',{'session_id':'legacy-session'})]
    with db.connect() as c:
        for id,kind,data in records:
            value={**data,'id':id,'kind':kind,'created':db.now(),'revision':1}
            c.execute('INSERT INTO entities VALUES(?,?,?,?)',(id,kind,json.dumps(value),value['created']))
        spaces.migrate_legacy(c)
    assert db.get('legacy-source')['space_id']=='automation'
    assert db.get('legacy-concept')['space_id']=='automation' and db.get('legacy-attempt')['space_id']=='automation'
    assert db.get('legacy-unknown')['space_id']=='unassigned'


def test_api_revision_and_attachment_contract(knowledge_client):
    client=knowledge_client
    a=client.post('/api/entries',json={'title':'API 笔记'}).json()
    assert a['space_id']=='inbox'
    response=client.patch('/api/entries/'+a['id'],json={'revision':a['revision'],'body':'changed'})
    assert response.status_code==200
    assert client.patch('/api/entries/'+a['id'],json={'revision':a['revision'],'body':'lost'}).status_code==409
    a=response.json()
    attached=client.post('/api/entries/'+a['id']+'/attachments',files={'file':('a.txt',b'hello')},data={'revision':a['revision']})
    assert attached.status_code==200
    a=attached.json();asset=a['attachments'][0]['id']
    download=client.get(f'/api/entries/{a["id"]}/attachments/{asset}')
    assert download.content==b'hello' and 'attachment' in download.headers['content-disposition']
    assert client.delete('/api/entries/'+a['id'],params={'revision':a['revision']}).status_code==200


def test_new_records_inherit_explicit_scope_without_title_guessing():
    parent=db.put('concept',{'space_id':'automation','title':'fixture'})
    child=db.put('draft',{'concept_id':parent['id']})
    assert child['space_id']=='automation'
    unknown=db.put('concept',{'title':'pytest 自动化测试'})
    assert unknown['space_id']=='unassigned'
    other=db.put('goal',{'space_id':'inbox','title':'兴趣'})
    ambiguous=db.put('draft',{'concept_id':parent['id'],'goal_id':other['id']})
    assert ambiguous['space_id']=='unassigned'


def test_empty_title_uses_body_heading(knowledge_client):
    response=knowledge_client.post('/api/entries',json={'body':'# 天文随记\n\n今晚看见月亮。'})
    assert response.status_code==200 and response.json()['title']=='天文随记'
    response=knowledge_client.post('/api/spaces',json={'title':'兴趣','domain_id':'general','default_mode':'explore'})
    assert response.status_code==200 and response.json()['mode']=='explore'


def test_reference_snapshot_warns_on_source_change_without_overwriting_excerpt():
    source=db.put('source',{'name':'读书.md','hash':'v1','version':1})
    spaces.attach_source('inbox',source['id'])
    a=note(entry_type='excerpt',references=[{'kind':'source','id':source['id'],'quote':'原句','location':'第2行'}])
    assert a['references'][0]['revision']==1
    assert not entries.with_reference_status(a)['needs_review']
    # Simulate an updated source while retaining the exact original quote.
    with db.connect() as c:
        value=dict(source,revision=2,hash='v2',version=2)
        c.execute('UPDATE entities SET data=? WHERE id=?',(json.dumps(value),source['id']))
    result=entries.with_reference_status(a)
    assert result['needs_review'] and result['references'][0]['quote']=='原句'


def test_ai_organize_is_draft_until_accept_and_rejects_stale_source(monkeypatch):
    from server import models
    a=note()
    monkeypatch.setattr(models,'structured',lambda *args,**kw:{'title':'整理标题','body':'整理后的正文','tags':['读书'],'changes':'增加标题'})
    suggestion=entries.organize(a['id'])
    assert entries.detail(a['id'])['body']=='有趣的知识' and suggestion['status']=='draft'
    accepted=entries.accept_suggestion(a['id'],suggestion['id'],a['revision'])
    assert accepted['body']=='整理后的正文' and accepted['revision']==a['revision']+1
    assert entries.revisions(a['id'])['items'][0]['body']=='有趣的知识'
    stale=entries.organize(a['id']);new=entries.save({'revision':accepted['revision'],'body':'用户新写'},a['id'])
    with pytest.raises(HTTPException) as error:entries.accept_suggestion(a['id'],stale['id'],new['revision'])
    assert error.value.status_code==409 and entries.detail(a['id'])['body']=='用户新写'


def test_existing_excerpt_survives_deleted_source_and_remains_editable():
    source=db.put('source',{'name':'original.md','hash':'v1','version':1})
    spaces.attach_source('inbox',source['id'])
    a=note(references=[{'kind':'source','id':source['id'],'quote':'旧摘录'}])
    db.update(source['id'],{'deleted':True})
    edited=entries.save({'revision':a['revision'],'body':'继续补充个人理解'},a['id'])
    assert entries.with_reference_status(edited)['needs_review']
    assert edited['references'][0]['quote']=='旧摘录'
    with pytest.raises(ValueError):note(references=[{'kind':'source','id':source['id']}])


def test_entries_goal_filter_happens_before_pagination(knowledge_client):
    goal1=db.put('goal',{'space_id':'inbox','title':'目标一'});goal2=db.put('goal',{'space_id':'inbox','title':'目标二'})
    wanted=note(goal_id=goal1['id'])
    for i in range(35):note(title=f'其他目标 {i}',goal_id=goal2['id'])
    response=knowledge_client.get('/api/entries',params={'space_id':'inbox','goal_id':goal1['id'],'limit':30})
    assert response.status_code==200 and [x['id'] for x in response.json()['items']]==[wanted['id']]
