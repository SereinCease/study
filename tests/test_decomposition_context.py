import pytest
from server import db, ingest, jobs, learning, memory, models


def make_source(count=7, long=False):
    source=ingest.import_bytes('Python-pytest-Playwright.md',b'# Python pytest Playwright\nCheckout learning guide.')
    jobs.tick()
    first=db.all('chunk')[0]
    parts=[]
    for i in range(count):
        value={'source_id':source['id'],'path':source['name'],'location':{'section':f'chapter {i+1}','line_start':i*3+1},
               'text':('Python + pytest + Playwright. ' if i==0 else '')+('章节原文内容。'*240 if long else '定位器用于寻找元素；重试断言等待结果。')}
        parts.append(db.update(first['id'],value) if i==0 else db.put('chunk',value))
    return source,parts


def fake_structured(monkeypatch,callback):
    def generate(system,context,schema,profile_id='default',validate=None):
        result=schema.model_validate(callback(system,context))
        if validate:validate(result)
        return result.model_dump()
    monkeypatch.setattr(models,'structured',generate)


def test_short_document_keeps_cross_section_context_in_one_batch(monkeypatch):
    source,parts=make_source()
    seen=[]
    def generate(system,ctx):
        seen.append(ctx)
        assert '不等于整份资料没有' in system
        assert 'pytest' in ctx['document_overview'][0]['opening_excerpt']
        return {'cards':[{'title':'角色定位器','definition':'按角色找元素','purpose':'测试','question':'如何找到按钮？','citation_ids':[ctx['chunks'][0]['id']]}]}
    fake_structured(monkeypatch,generate)
    session=learning.start('decompose',[source['id']]);jobs.tick()
    assert db.get(session['job_id'])['status']=='completed'
    assert len(seen)==1 and len(seen[0]['chunks'])==7
    assert db.get(session['job_id'])['checkpoint']['grouping']=='token-v2'


def test_later_batches_receive_prior_drafts_and_repeated_cards_become_suggestions(monkeypatch):
    source,parts=make_source(long=True)
    seen=[]
    def generate(system,ctx):
        seen.append(ctx)
        if len(seen)>1:
            assert ctx['known_cards'][0]['title']=='角色定位器'
            assert ctx['known_cards'][0]['status']=='draft'
            assert 'pytest' in ctx['document_overview'][0]['title']
        return {'cards':[{'title':'角色定位器','definition':'本批有补充说明','purpose':'测试','question':'怎样定位？','citation_ids':[ctx['chunks'][0]['id']]}]}
    fake_structured(monkeypatch,generate)
    session=learning.start('decompose',[source['id']]);jobs.tick()
    assert db.get(session['job_id'])['status']=='completed'
    artifacts=db.select('artifact',filters={'session_id':session['id']},limit=20)
    assert len(artifacts)>1 and len(seen)>1
    assert len(artifacts[0]['output']['cards'])==1
    for artifact in artifacts[1:]:
        assert artifact['output']['cards']==[]
        assert artifact['output']['merge_suggestions'][0]['target_ref'].startswith('artifact:')
        learning.accept_artifact(artifact['id'],decisions=[{'item_id':'suggestion:0','action':'ignore','reason':'前批草稿已覆盖'}])
    assert db.all('concept')==[] # A suggestion alone never creates or overwrites knowledge.


def test_directory_budget_and_existing_concepts_are_not_treated_as_facts():
    source,parts=make_source()
    for i in range(80):
        db.put('concept',{'space_id':'automation','source_ids':[source['id']],'title':f'知识点{i}','definition':'未经核对的推断。'*80,'needs_review':True})
    session=learning.start('decompose',[source['id']])
    ctx,manifest=learning._decompose_context(session,parts[:3],parts,0,3,[])
    assert memory.estimate(ctx['known_cards'])<=1300
    assert memory.estimate(ctx['document_overview'])<=900
    assert memory.estimate(ctx)<=10000 and manifest['estimated_tokens']==memory.estimate(ctx)
    assert ctx['batch_scope']['directory_truncated']
    assert all(c['status']=='accepted_needs_review' for c in ctx['known_cards'])
    assert all(c['id'] in {p['id'] for p in parts[:3]} for c in ctx['chunks'])


def test_merge_suggestion_must_reference_an_existing_directory_entry(monkeypatch):
    source,parts=make_source()
    fake_structured(monkeypatch,lambda system,ctx:{'cards':[],'merge_suggestions':[{'target_ref':'concept:invented','title':'定位器','reason':'同义','citation_ids':[ctx['chunks'][0]['id']]}]})
    session=learning.start('decompose',[source['id']]);jobs.tick()
    assert db.get(session['job_id'])['status']=='failed'
    assert not db.all('artifact')


def test_resumed_legacy_checkpoint_keeps_original_boundaries(monkeypatch):
    source,parts=make_source()
    session=learning.start('decompose',[source['id']])
    prior=db.put('artifact',{'space_id':'automation','session_id':session['id'],'source_ids':[source['id']],'batch':0,'status':'draft','output':{'cards':[{'title':'前批知识','definition':'定义','purpose':'学习','question':'问题','citation_ids':[parts[0]['id']]}]}})
    db.update(session['job_id'],{'checkpoint':{'next':1,'artifacts':[prior['id']]}})
    seen=[]
    def generate(system,ctx):
        seen.append(ctx)
        return {'cards':[],'gaps':['本批待核对：仅为小结，没有新增概念。']}
    fake_structured(monkeypatch,generate)
    jobs.tick()
    assert db.get(session['job_id'])['status']=='completed'
    assert len(seen)==2 and seen[0]['batch_scope']['index']==2
    assert seen[0]['known_cards'][0]['ref'].startswith('artifact:'+prior['id'])
    assert {p['id'] for p in seen[0]['chunks']}=={p['id'] for p in parts[3:6]}
