import json
from array import array
import pytest
from fastapi import HTTPException
from server import db, card_review, learning, ingest, jobs, models, spaces


def source(name='教程.md',space='automation',hash='h1'):
    value=db.put('source',{'space_id':space,'name':name,'hash':hash,'version':1,'status':'ready'})
    spaces.attach_source(space,value['id'])
    chunk=db.put('chunk',{'source_id':value['id'],'text':'断言检查实际结果和预期是否一致。','location':{'line_start':1,'line_end':2}})
    return value,chunk


def card(chunk,title='断言',**changes):
    return {'title':title,'definition':'检查实际结果与预期是否一致','purpose':'验证行为','question':'结果不一致会怎样？','citation_ids':[chunk['id']],**changes}


def draft(src,cards=None,suggestions=None):
    return db.put('artifact',{'space_id':src['space_id'],'source_ids':[src['id']],'status':'draft','output':{
        'cards':cards or [],'relations':[],'merge_suggestions':suggestions or []}})


def concept(src,chunk,title='断言',**changes):
    return db.put('concept',dict(card(chunk,title,**changes),space_id=src['space_id'],source_ids=[src['id']]))


def test_candidate_keywords_exclude_punctuation_and_single_character_noise(monkeypatch):
    monkeypatch.setattr(ingest,'tokens',lambda text:'_ __ ... x 1 c 的 expect have locator playwright sleep 断言 to_have_text')
    assert card_review._terms({'title':'关键词'})=={'expect','have','locator','playwright','sleep','断言','to_have_text'}
    # Filtering descriptive keywords must not change exact short-name matching.
    assert card_review.names({'title':'C'})=={'c'}


def test_alias_and_cross_source_candidates_are_scoped_without_remote_calls(monkeypatch):
    first,one=source();second,two=source('新教材.md',hash='h2');foreign,other=source('兴趣.md','inbox')
    existing=concept(first,one,'结果校验',aliases=['assert','断言'])
    hidden=concept(foreign,other,'断言')
    a=draft(second,[card(two,'assert')])
    def no_call(*args,**kwargs):raise AssertionError('candidate review must not encode/download/call AI')
    monkeypatch.setattr(ingest,'vector',no_call);monkeypatch.setattr(models,'structured',no_call)
    review=card_review.review(a);item=review['items'][0]
    assert item['requires_decision'] and item['can_create']
    assert {x['concept_id'] for x in item['candidates']}=={existing['id']}
    assert '标题或已记录别名一致' in item['candidates'][0]['reasons']
    assert hidden['id'] not in json.dumps(review)
    assert db.count('concept')==2
    with pytest.raises(HTTPException) as error:learning.accept_artifact(a['id'])
    assert error.value.status_code==409 and db.get(a['id'])['status']=='draft'


def test_cached_vector_and_lexical_signals_are_explained_without_probability():
    first,one=source();second,two=source('补充.md',hash='h2')
    existing=concept(first,one,'行为匹配',definition='结果应该符合需求')
    a=draft(second,[card(two,'预期核验',definition='对比需求中的结果')])
    with db.connect() as c:
        for chunk in [one,two]:c.execute('INSERT INTO vectors VALUES(?,?,?)',(chunk['id'],ingest.MODEL,array('f',[.5,.5]).tobytes()))
    candidate=card_review.review(a)['items'][0]['candidates'][0]
    assert candidate['concept_id']==existing['id']
    assert any('本地原文向量' in reason for reason in candidate['reasons'])
    assert 'probability' not in candidate and 'confidence' not in candidate


def test_explicit_merge_preserves_definition_conflict_source_versions_and_idempotency():
    first,one=source();second,two=source('新教材.md',hash='h2')
    existing=concept(first,one,'断言',definition='断言失败会终止当前测试')
    a=draft(second,[card(two,'断言',definition='断言失败只打印结果')])
    result=learning.accept_artifact(a['id'],decisions=[{'item_id':'card:0','action':'merge','target_id':existing['id'],
        'target_revision':existing['revision'],'reason':'存在定义冲突，先保留资料变体待核对'}],review_revision=a['revision'])
    merged=db.get(existing['id'])
    assert merged['definition']=='断言失败会终止当前测试' and merged['needs_review']
    variant=merged['definition_variants'][0]
    assert variant['card']['definition']=='断言失败只打印结果'
    assert variant['evidence_refs'][0]['source_hash']=='h2'
    assert set(merged['citation_ids'])=={one['id'],two['id']} and db.count('concept')==1
    assert len(merged['evidence_refs'])==2
    assert learning.accept_artifact(a['id'],decisions=[{'item_id':'card:0','action':'create'}])==result
    assert db.count('card_decision')==1 and db.count('concept')==1
    assert not db.all('attempt') and not db.all('mastery')


def test_changed_target_revision_or_foreign_merge_rolls_back_all_decisions():
    src,ch=source();foreign,other=source('别处.md','inbox')
    target=concept(src,ch);alien=concept(foreign,other)
    a=draft(src,[card(ch,'全新知识',definition='独立行为'),card(ch)])
    db.update(target['id'],{'definition':'已更新'})
    with pytest.raises(HTTPException):learning.accept_artifact(a['id'],decisions=[
        {'item_id':'card:0','action':'create'},
        {'item_id':'card:1','action':'merge','target_id':target['id'],'target_revision':target['revision']}])
    assert db.count('concept')==2 and not db.all('card_decision')
    with pytest.raises(ValueError):learning.accept_artifact(a['id'],decisions=[
        {'item_id':'card:0','action':'ignore'},
        {'item_id':'card:1','action':'merge','target_id':alien['id'],'target_revision':alien['revision']}])
    assert db.get(a['id'])['status']=='draft'


def test_rejected_candidates_stay_suppressed_until_source_changes():
    src,ch=source();target=concept(src,ch)
    a=draft(src,[],[{'title':'断言','target_ref':'concept:'+target['id'],'reason':'近似','citation_ids':[ch['id']],'card':card(ch)}])
    with pytest.raises(HTTPException):learning.accept_artifact(a['id'])
    learning.accept_artifact(a['id'],decisions=[{'item_id':'suggestion:0','action':'ignore','reason':'已有卡已覆盖，无新内容'}])
    with db.connect() as c:
        same=card_review.filter_rejected(draft(src,[card(ch)]),c)
        assert not same['output']['cards'] and same['suppressed_suggestions'][0]['title']=='断言'
    db.update(src['id'],{'hash':'changed','version':2})
    with db.connect() as c:
        changed=card_review.filter_rejected(draft(src,[card(ch)]),c)
        assert len(changed['output']['cards'])==1


def test_ignored_card_relations_do_not_shift_and_every_suggestion_needs_choice():
    src,ch=source();a=draft(src,[card(ch,'第一知识'),card(ch,'第二知识')],[
        {'title':'建议','target_ref':'artifact:old:0','reason':'补充','citation_ids':[ch['id']]}])
    a['output']['relations']=[{'source_index':0,'target_index':1,'relation':'prerequisite','reason':'先后'}]
    db.update(a['id'],{'output':a['output']})
    with pytest.raises(HTTPException):learning.accept_artifact(a['id'],decisions=[{'item_id':'card:0','action':'ignore'}])
    with pytest.raises(ValueError):learning.accept_artifact(a['id'],decisions=[{'item_id':'suggestion:0','action':'create'}])
    result=learning.accept_artifact(a['id'],decisions=[{'item_id':'card:0','action':'ignore'},
        {'item_id':'card:1','action':'create'},{'item_id':'suggestion:0','action':'ignore'}])
    assert set(result['card_concept_ids'])=={'1'} and not db.all('relation')
    assert db.all('concept')[0]['title']=='第二知识'


def test_review_api_and_source_change_conflict(client):
    src,ch=source();a=draft(src,[card(ch)])
    a=db.update(a['id'],{'coverage':{'sources':[{'id':src['id'],'hash':'h1','version':1}],'scope_note':'原文覆盖不代表知识覆盖'}})
    response=client.get('/api/artifacts/'+a['id']+'/review')
    assert response.status_code==200 and response.json()['items'][0]['evidence'][0]['location']['line_start']==1
    assert client.patch('/api/artifacts/'+a['id'],json={'accept':True,'review_revision':a['revision']-1}).status_code==409
    db.update(src['id'],{'hash':'h2','version':2})
    assert client.patch('/api/artifacts/'+a['id'],json={'accept':True,'review_revision':a['revision']}).status_code==409
    assert not db.all('concept')


def test_force_create_requires_reason_and_same_batch_can_merge_without_losing_content():
    src,ch=source();existing=concept(src,ch)
    a=draft(src,[card(ch)])
    with pytest.raises(ValueError,match='区别说明'):
        learning.accept_artifact(a['id'],decisions=[{'item_id':'card:0','action':'create'}])
    created=learning.accept_artifact(a['id'],decisions=[{'item_id':'card:0','action':'create','reason':'专门讲解另一种语义的断言'}])
    assert created['concept_ids']!=[existing['id']]
    output=learning._deduplicate_decomposition(learning.Decomposition.model_validate({'cards':[
        card(ch,'新概念',definition='第一次定义'),card(ch,'新概念',definition='第二次定义需要核对')]}).model_dump(),[])
    a=db.put('artifact',{'space_id':'automation','source_ids':[src['id']],'status':'draft','output':output})
    item=card_review.review(a)['items'][1]
    target=next(x for x in item['candidates'] if x['concept_id'].startswith('draft:'))
    choices=[{'item_id':'card:0','action':'create','reason':'资料新增独立知识点'},
             {'item_id':'suggestion:0','action':'merge','target_id':target['concept_id'],'target_revision':target['revision']}]
    result=learning.accept_artifact(a['id'],decisions=choices)
    assert len(result['concept_ids'])==1
    merged=db.get(result['concept_ids'][0])
    assert merged['definition']=='第一次定义'
    assert merged['definition_variants'][0]['card']['definition']=='第二次定义需要核对'


def test_accepted_review_only_reports_persisted_decisions_for_same_artifact_and_space():
    src,ch=source();target=concept(src,ch)
    a=draft(src,[card(ch),card(ch,'另项',definition='不相关知识')])
    result=learning.accept_artifact(a['id'],decisions=[
        {'item_id':'card:0','action':'merge','target_id':target['id'],'target_revision':target['revision']},
        {'item_id':'card:1','action':'ignore'}])
    review=card_review.review(result)
    assert [x['action'] for x in review['decision_results']]==['merge','ignore']
    assert review['decision_results'][0]['target_title']=='断言'
    assert review['decision_records_complete'] and review['decision_history_available']
    wrong_artifact=db.put('card_decision',{'space_id':'automation','artifact_id':'different','action':'create','card':{'title':'私有'}})
    wrong_space=db.put('card_decision',{'space_id':'inbox','artifact_id':a['id'],'action':'create','card':{'title':'另一空间'}})
    tampered=db.update(result['id'],{'decision_ids':result['decision_ids']+[wrong_artifact['id'],wrong_space['id']]})
    check=card_review.review(tampered)
    assert len(check['decision_results'])==2 and not check['decision_records_complete']
    assert wrong_space['id'] not in json.dumps(check['decision_results'])


def test_decomposition_directory_includes_other_sources_and_records_real_coverage(monkeypatch):
    src,ch=source();other,part=source('另一份.md',hash='h2');target=concept(other,part,'断言',aliases=['assert'])
    def generate(system,context,schema,profile='default',validate=None):
        assert any(x['ref']=='concept:'+target['id'] for x in context['known_cards'])
        result=schema.model_validate({'cards':[card(ch,'断言')]})
        if validate:validate(result)
        return result.model_dump()
    monkeypatch.setattr(models,'structured',generate)
    session=learning.start('decompose',[src['id']],space_id='automation');jobs.tick()
    assert db.get(session['job_id'])['status']=='completed'
    artifact=db.all('artifact')[0]
    assert artifact['output']['cards']==[]
    assert artifact['output']['merge_suggestions'][0]['card']['definition']=='检查实际结果与预期是否一致'
    assert artifact['coverage']['processed_chunk_ids']==[ch['id']]
    assert artifact['coverage']['source_chunk_counts']=={src['id']:1}
    assert artifact['coverage']['all_chunks_processed']
