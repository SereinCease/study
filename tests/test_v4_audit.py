"""Cross-module regressions for spaces, drafts, memory opt-out and exports.
No real credentials or cloud model calls are used by this suite.
"""
import io,json,zipfile
import pytest
from server import db,spaces,entries,exams,learning,memory,jobs,models,ingest


def concept(space='automation',title='同名概念'):
    return db.put('concept',{'space_id':space,'domain_id':spaces.resolve(space)['domain_id'],'title':title,'source_ids':[],'citation_ids':[],'definition':'示例定义'})


def goal(space='automation',concept_ids=None):
    return db.put('goal',{'space_id':space,'domain_id':spaces.resolve(space)['domain_id'],'title':'阶段目标','source_ids':[],'concept_ids':concept_ids or [],'minutes':30})


def session(c=None,g=None,space='automation',mode='learn'):
    return db.put('session',{'space_id':space,'domain_id':spaces.resolve(space)['domain_id'],'goal_id':g['id'] if g else None,'concept_id':c['id'] if c else None,'goal':g['title'] if g else '', 'skill_id':'explain','status':'active','mode':mode,'definition':{'version':1,'profile_id':'default','prompt':'教学'},'memory_enabled':True,'hint_level':0,'answer_seen':False,'step':0,'current_task':{'question':'用自己的话回答','rubric':'描述概念','is_transfer':False}})


def mock_turn(monkeypatch,score=1):
    monkeypatch.setattr(models,'profile',lambda *a,**k:{'model':'mock','reasoning':'standard'})
    def structured(system,context,schema,*args,**kwargs):
        output={'explanation':'AI 讲解','citations':[],'supplement':'','inference':'','question':'下一题','rubric':'独立作答','feedback':'评价','score':score,'error_type':'concept','reason_kind':'hypothesis','is_transfer':True,'evidence_ids':[]}
        obj=schema.model_validate(output)
        validator=kwargs.get('validate') or (args[1] if len(args)>1 else None)
        if validator:validator(obj)
        return obj.model_dump()
    monkeypatch.setattr(models,'structured',structured)


def test_goal_inferred_scope_cannot_import_other_space_source(client):
    source=db.put('source',{'space_id':'automation','name':'独立资料.md','status':'ready'})
    spaces.attach_source('automation',source['id'])
    target=goal('inbox')
    response=client.post('/api/skill-sessions',json={'skill_id':'decompose','goal_id':target['id'],'source_ids':[source['id']]})
    assert response.status_code==400
    assert not db.all('session') and not db.all('artifact')


def test_same_name_concepts_never_share_mastery_or_merge(client):
    first=concept();second=concept('inbox')
    db.put('attempt',{'space_id':'automation','concept_id':first['id'],'score':1,'hint_level':0,'answer_seen':False,'error_type':'unknown'})
    learning.recompute(first['id']);learning.recompute(second['id'])
    assert db.get('mastery-'+first['id'])['score']==.3
    assert db.get('mastery-'+second['id'])['score']==0
    assert client.post('/api/concepts/'+first['id']+'/merge',json={'target_id':second['id']}).status_code==400
    assert memory.profile(domain='general',space_id='inbox')['mastery'][0]['concept_id']==second['id']


def test_session_only_code_drafts_keep_two_goal_versions_separate(client):
    c=concept();g1=goal(concept_ids=[c['id']]);g2=goal(concept_ids=[c['id']]);s1=session(c,g1);s2=session(c,g2)
    one=client.put('/api/exercises/assertions/draft',json={'session_id':s1['id'],'code':'# first goal','revision':0})
    two=client.put('/api/exercises/assertions/draft',json={'session_id':s2['id'],'code':'# second goal','revision':0})
    assert one.status_code==two.status_code==200
    assert one.json()['id']!=two.json()['id']
    assert one.json()['goal_id']==g1['id'] and two.json()['goal_id']==g2['id']
    conflict=client.put('/api/exercises/assertions/draft',json={'session_id':s1['id'],'code':'stale overwrite','revision':0})
    assert conflict.status_code==409
    assert db.get(one.json()['id'])['code']=='# first goal'


def test_answer_drafts_restore_with_scope_and_conflict(client):
    c=concept();g=goal(concept_ids=[c['id']]);s=session(c,g)
    saved=client.put(f'/api/skill-sessions/{s["id"]}/draft',json={'text':'未提交答案','revision':0})
    assert saved.status_code==200 and saved.json()['space_id']=='automation'
    db.init()
    restored=client.get(f'/api/skill-sessions/{s["id"]}/draft').json()
    assert restored['text']=='未提交答案'
    assert client.put(f'/api/skill-sessions/{s["id"]}/draft',json={'text':'旧标签覆盖','revision':0}).status_code==409
    assert not db.all('attempt')


def test_goal_only_context_does_not_select_other_goal_note():
    g1=goal('inbox');g2=goal('inbox');s=session(g=g1,space='inbox')
    wanted=entries.save({'space_id':'inbox','title':'当前目标','body':'应该使用','goal_id':g1['id'],'memory_enabled':True})
    entries.save({'space_id':'inbox','title':'其他目标','body':'不应混入','goal_id':g2['id'],'memory_enabled':True})
    entries.save({'space_id':'inbox','title':'未关联','body':'不应因 None 匹配混入','memory_enabled':True})
    ctx,_=memory.context(s)
    assert [n['id'] for n in ctx['notes']]==[wanted['id']]


def test_disabling_note_invalidates_summary_context_and_rebuild(monkeypatch):
    c=concept('inbox');s=session(c,space='inbox')
    note=entries.save({'space_id':'inbox','title':'临时推测','body':'不可再次召回的笔记线索','concept_id':c['id'],'memory_enabled':True})
    _,manifest=memory.context(s);snapshot=db.put('context_snapshot',manifest)
    event=db.put('event',{'space_id':'inbox','domain_id':'general','concept_id':c['id'],'session_id':s['id'],'context_id':snapshot['id'],'action':'hint','answer':'','output':{'explanation':note['body']}})
    summary=db.put('summary',{'space_id':'inbox','domain_id':'general','session_id':s['id'],'entry_ids':[note['id']],'event_ids':[event['id']],'evidence_ids':[event['id']],'material_versions':{note['id']:note['revision']},'status':'ready','output':{'learned':[note['body']],'next_step':'复习'}})
    assert memory.visible(event['id']) and memory.visible(summary['id'])
    entries.save({'revision':note['revision'],'memory_enabled':False},note['id'])
    assert db.get(snapshot['id'])['stale'] and db.get(summary['id'])['status']=='stale'
    assert not memory.visible(event['id']) and not memory.visible(summary['id'])
    assert memory.schedule_summary(s['id'],force=True) is None
    monkeypatch.setattr(ingest,'vector',lambda _: [1.0,0.0])
    job=jobs.enqueue('memory_index',{});memory.rebuild(job)
    ctx,_=memory.context(s)
    assert not ctx['notes'] and not ctx['history'] and not ctx['summaries']
    assert not memory.search('不可再次召回',domain='general',space_id='inbox')['items']
    assert db.get(event['id'])['output']['explanation']==note['body']  # audit history retained


def test_exploration_and_hint_ai_scores_cannot_create_success(monkeypatch):
    mock_turn(monkeypatch)
    c=concept();g=goal(concept_ids=[c['id']]);s=session(c,g,mode='explore')
    job=jobs.enqueue('session',{'session_id':s['id'],'action':'answer','answer':'随便讨论'})
    learning.session_step(job)
    assert not db.all('attempt')
    event=db.select('event',filters={'job_id':job['id']})[0]
    assert event['output']['score'] is None
    s2=session(c,g)
    job=jobs.enqueue('session',{'session_id':s2['id'],'action':'hint'})
    learning.session_step(job)
    assert not db.all('attempt') and not db.all('review')


def test_ai_organized_note_never_becomes_attempt(monkeypatch):
    note=entries.save({'space_id':'inbox','title':'想法','body':'我的理解'})
    monkeypatch.setattr(models,'structured',lambda *a,**k:{'title':'整理','body':'AI 整理的笔记','tags':[],'changes':'结构调整'})
    suggestion=entries.organize(note['id']);entries.accept_suggestion(note['id'],suggestion['id'],note['revision'])
    assert not db.all('attempt') and not db.all('mastery') and not db.all('review')
    assert entries.detail(note['id'])['last_edit_attribution']=='ai_suggestion_accepted'


def test_export_includes_attachment_and_omits_config_credentials(client):
    marker='SYNTHETIC-NOT-A-REAL-CREDENTIAL-ONLY-TEST'
    db.put('profile',{'provider':'custom','api_key':marker},id='audit-model-profile')
    db.put('model_call',{'internal_test_only':marker})
    note=entries.save({'space_id':'inbox','title':'导出','body':'公开笔记'})
    note=entries.add_attachment(note['id'],'reference.txt',b'attachment bytes',note['revision'])
    response=client.get('/api/export/archive');assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        paths=archive.namelist()
        attachment_id=note['attachments'][0]['id']
        assert archive.read('attachments/'+attachment_id)==b'attachment bytes'
        assert all(not name.endswith(('.env','.secrets.json')) for name in paths)
        for name in paths:
            assert marker.encode() not in archive.read(name)
        exported=json.loads(archive.read('learning.json'))
        assert 'profile' not in exported['data'] and 'model_call' not in exported['data']


def test_mastery_excludes_inconsistent_legacy_cross_space_attempt():
    c=concept('automation')
    db.put('attempt',{'space_id':'automation','concept_id':c['id'],'score':1,'hint_level':0,'answer_seen':False})
    # Legacy/corrupt linkage must not let an unassigned or another-space grade raise this concept.
    db.put('attempt',{'space_id':'inbox','concept_id':c['id'],'score':1,'hint_level':0,'answer_seen':False})
    db.put('attempt',{'space_id':'unassigned','concept_id':c['id'],'score':1,'hint_level':0,'answer_seen':False})
    result=learning.recompute(c['id'])
    assert result['score']==.3 and result['evidence_count']==1


def test_disabled_note_does_not_survive_in_current_question_and_draft_is_kept(client):
    c=concept('inbox');s=session(c,space='inbox')
    phrase='已经停用的笔记中独有的秘密推测'
    note=entries.save({'space_id':'inbox','title':'临时笔记','body':phrase,'concept_id':c['id'],'memory_enabled':True})
    _,manifest=memory.context(s);snapshot=db.put('context_snapshot',manifest)
    task={'question':'解释：'+phrase,'rubric':phrase,'is_transfer':False}
    db.put('event',{'space_id':'inbox','domain_id':'general','session_id':s['id'],'concept_id':c['id'],'context_id':snapshot['id'],'action':'start','answer':'','output':{'explanation':phrase,**task}})
    db.update(s['id'],{'current_task':task})
    draft=client.put(f'/api/skill-sessions/{s["id"]}/draft',json={'text':'我正在写的答案，不应丢失','revision':0})
    assert draft.status_code==200
    entries.save({'revision':note['revision'],'memory_enabled':False},note['id'])
    ctx,_=memory.context(db.get(s['id']))
    assert phrase not in json.dumps(ctx,ensure_ascii=False)
    assert client.get(f'/api/skill-sessions/{s["id"]}/draft').json()['text']=='我正在写的答案，不应丢失'
    response=client.post(f'/api/skill-sessions/{s["id"]}/events',json={'action':'answer','answer':'尚未核对的旧题作答','request_id':'stale-current-question'})
    assert response.status_code in (400,409)
    assert not db.all('attempt')
