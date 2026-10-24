"""Explainable, local card review. Similarity proposes; only a user decision merges."""
import hashlib
import json
import re
import unicodedata
from array import array
from math import sqrt
from fastapi import HTTPException
from . import db, ingest


def normalize(text):
    return ''.join(re.findall(r'[\w]+',unicodedata.normalize('NFKC',str(text)).casefold()))


def names(card):
    title=card.get('title','')
    values=[title,*card.get('aliases',[])]
    # A parenthesized translation is a useful explicit alias, never a claim of mastery.
    values.extend(re.findall(r'[（(]([^()（）]+)[）)]',title))
    values.append(re.sub(r'[（(][^()（）]+[）)]','',title))
    return {normalize(x) for x in values if normalize(x)}


def active_concepts(space_id,con):
    return [json.loads(row[0]) for row in con.execute(
        "SELECT data FROM entities WHERE kind='concept' AND json_extract(data,'$.space_id') IS ?",
        (space_id,)) if not (json.loads(row[0]).get('deleted') or json.loads(row[0]).get('merged_into'))]


def _terms(card):
    text=' '.join([card.get('title',''),*card.get('aliases',[]),card.get('definition',''),*card.get('points',[])])
    stop={'的','是','与','和','了','用于','通过','一个','可以','使用','进行','测试','知识','学习','the','a','is','to','and','of'}
    # Tokenizers retain underscores and one-letter code variables. Neither is a
    # useful explanation of conceptual overlap; exact title/alias matching above
    # still preserves meaningful short names such as the language C.
    return {x for x in ingest.tokens(text).split() if x not in stop and sum(c.isalnum() for c in x)>1}


def _vector(card,con,cache):
    """Reuse already-published chunk vectors; never initialize/download an encoder."""
    vectors=[]
    for cid in card.get('citation_ids',[]):
        if cid not in cache:
            row=con.execute('SELECT vector FROM vectors WHERE id=? AND model=?',(cid,ingest.MODEL)).fetchone()
            cache[cid]=list(array('f',row[0])) if row and isinstance(row[0],bytes) else json.loads(row[0]) if row else None
        if cache[cid]:vectors.append(cache[cid])
    if not vectors or len({len(v) for v in vectors})!=1:return None
    values=[sum(row[i] for row in vectors)/len(vectors) for i in range(len(vectors[0]))]
    length=sqrt(sum(v*v for v in values))
    return [v/length for v in values] if length else None


def candidates(card,concepts,con,preferred=None,limit=5,cache=None):
    cache=cache if cache is not None else {}
    aliases=names(card);terms=_terms(card);vector=_vector(card,con,cache);matches=[]
    for concept in concepts:
        reasons=[];score=0.0
        if aliases & names(concept):score+=4;reasons.append('标题或已记录别名一致')
        other=_terms(concept);overlap=terms&other
        similarity=len(overlap)/max(1,min(len(terms),len(other)))
        if len(overlap)>=3 and similarity>=.4:
            score+=similarity;reasons.append('定义与要点共有关键词：'+'、'.join(sorted(overlap)[:6]))
        if normalize(card.get('definition')) and normalize(card.get('definition'))==normalize(concept.get('definition')):
            score+=3;reasons.append('定义文本一致')
        other_vector=_vector(concept,con,cache)
        # Shared broad chapters alone do not establish equivalence. Keep a semantic
        # suggestion only with a lexical anchor, unless vectors are especially close.
        if vector and other_vector and len(vector)==len(other_vector):
            cosine=sum(a*b for a,b in zip(vector,other_vector))
            if cosine>=.86 and (overlap or cosine>=.95):
                score+=cosine;reasons.append('已存本地原文向量接近；同章节不代表同一概念')
        if concept['id']==preferred:
            score+=2;reasons.append('AI 建议的目标，仍需核对定义差异')
        if reasons:
            matches.append({'concept_id':concept['id'],'title':concept.get('title',''),'revision':concept.get('revision',1),
                            'definition_excerpt':concept.get('definition','')[:600], 'citation_ids':concept.get('citation_ids',[]),
                            'reasons':reasons,'rank_score':round(score,6), 'needs_review':bool(concept.get('needs_review'))})
    return sorted(matches,key=lambda c:(-c['rank_score'],c['concept_id']))[:limit]


def evidence(citation_ids,allowed_sources,con):
    result=[]
    for cid in dict.fromkeys(citation_ids):
        chunk=db.get(cid,'chunk',con);source=db.get(chunk['source_id'],'source',con)
        if source['id'] not in allowed_sources or source.get('deleted') or chunk.get('deleted'):
            raise ValueError('知识卡引用不属于当前有效资料')
        result.append({'chunk_id':cid,'chunk_revision':chunk.get('revision',1),'source_id':source['id'],
                       'source_version':source.get('version',1),'source_hash':source.get('hash'),
                       'location':chunk.get('location',{}),'path':chunk.get('path','')})
    return result


def _preferred(ref,con):
    if ref.startswith('concept:'):return ref[8:]
    if ref.startswith('artifact:'):
        try:
            _,aid,index=ref.split(':');parent=db.get(aid,'artifact',con)
            # Older artifacts used a positional concept_ids list; new ones keep it
            # explicit because ignore decisions must not shift relation indices.
            mapping=parent.get('card_concept_ids')
            return mapping.get(index) if mapping is not None else parent.get('concept_ids',[])[int(index)]
        except (ValueError,IndexError):return None
    return None


def fingerprint(a,item,con):
    versions=[]
    for sid in sorted(a.get('source_ids',[])):
        source=db.get(sid,'source',con)
        versions.append((sid,source.get('version',1),source.get('hash')))
    # Draft artifact IDs and model wording change between runs. A rejection refers
    # to the same title/material version, not that ephemeral suggestion ID.
    raw=[a.get('space_id'),normalize(item['card'].get('title','')),versions]
    return hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def review(a,con=None):
    if con is None:
        with db.connect() as c:return review(a,c)
    concepts=active_concepts(a.get('space_id'),con);items=[];cache={}
    for index,card in enumerate(a['output'].get('cards',[])):
        found=candidates(card,concepts,con,cache=cache)
        items.append({'item_id':f'card:{index}','kind':'card','title':card['title'],'card':card,
                      'candidates':found,'requires_decision':bool(found),'can_create':True})
    for index,suggestion in enumerate(a['output'].get('merge_suggestions',[])):
        card=suggestion.get('card') or {'title':suggestion['title'],'definition':'','citation_ids':suggestion['citation_ids']}
        ref=suggestion.get('target_ref','');preferred=_preferred(ref,con)
        found=candidates(card,concepts,con,preferred,cache=cache)
        local_index=ref.split(':',1)[1] if ref.startswith('current:') else None
        if local_index and local_index.isdigit() and int(local_index)<len(a['output'].get('cards',[])):
            local=a['output']['cards'][int(local_index)]
            found.insert(0,{'concept_id':'draft:card:'+local_index,'target_item_id':'card:'+local_index,
                'title':local['title'],'revision':a.get('revision',1),'definition_excerpt':local.get('definition',''),
                'citation_ids':local.get('citation_ids',[]),'reasons':['同批草稿；采纳该卡后将在同一事务合入，原定义仍保留'], 'rank_score':5,'needs_review':True})
        items.append({'item_id':f'suggestion:{index}','kind':'suggestion','title':suggestion['title'],'card':card,
                      'suggestion':suggestion,'proposed_target_ref':ref,
                      'candidates':found,
                      'requires_decision':True,'can_create':bool(suggestion.get('card')),
                      'blocked_merge_reason':'建议的目标卡仍是前批未采纳草稿；请先核对采纳该卡后刷新，再合入。当前建议与完整内容会保留。' if not preferred and ref.startswith('artifact:') else ''})
    for item in items:
        item['fingerprint']=fingerprint(a,item,con)
        item['evidence']=evidence(item['card'].get('citation_ids',[]),a.get('source_ids',[]),con)
    decisions=[]
    for decision_id in dict.fromkeys(a.get('decision_ids',[])):
        try:decision=db.get(decision_id,'card_decision',con)
        except ValueError:continue
        if decision.get('artifact_id')!=a['id'] or decision.get('space_id')!=a.get('space_id'):continue
        if decision.get('target_id'):
            try:
                target=db.get(decision['target_id'],'concept',con)
                if target.get('space_id')==a.get('space_id'):decision=dict(decision,target_title=target.get('title',''))
            except ValueError:pass
        decisions.append(decision)
    return {'artifact_id':a['id'],'revision':a.get('revision',1),'status':a['status'],'items':items,'output':a['output'],
            'decision_results':decisions,'decision_history_available':'decision_ids' in a,
            'decision_records_complete':len(decisions)==len(a.get('decision_ids',[])),
            'coverage':a.get('coverage',{'scope_note':'历史草稿未记录完整处理范围，不能据此判断整份资料的知识缺口。'}),
            'score_note':'排序分仅用于候选排序，不是概念等同概率。定义与来源必须人工核对。'}


def suppressed(a,con):
    return {row[0] for row in con.execute("SELECT json_extract(data,'$.fingerprint') FROM entities WHERE kind='card_decision' AND json_extract(data,'$.space_id') IS ? AND json_extract(data,'$.action')='ignore'",(a.get('space_id'),))}


def filter_rejected(a,con):
    ignored=suppressed(a,con);kept=[];hidden=[];cards=[];mapping={}
    for index,card in enumerate(a['output'].get('cards',[])):
        if fingerprint(a,{'card':card},con) in ignored:hidden.append({'title':card['title'],'reason':'相同来源版本的候选已忽略'})
        else:mapping[index]=len(cards);cards.append(card)
    a['output']['cards']=cards
    a['output']['relations']=[{**edge,'source_index':mapping[edge['source_index']],'target_index':mapping[edge['target_index']]} for edge in a['output'].get('relations',[]) if edge['source_index'] in mapping and edge['target_index'] in mapping]
    for suggestion in a['output'].get('merge_suggestions',[]):
        item={'card':suggestion.get('card') or {'title':suggestion['title']},'proposed_target_ref':suggestion.get('target_ref','')}
        if fingerprint(a,item,con) in ignored:hidden.append({'title':suggestion['title'],'reason':'相同来源版本的建议已忽略'})
        else:kept.append(suggestion)
    a['output']['merge_suggestions']=kept
    a['suppressed_suggestions']=hidden
    return a


def apply(a,value,decisions,review_revision,con):
    if review_revision is not None and review_revision!=a.get('revision',1):
        raise HTTPException(409,detail='草稿已被修改，请重新核对后采纳')
    for previous in a.get('coverage',{}).get('sources',[]):
        current=db.get(previous['id'],'source',con)
        if any(previous.get(field)!=current.get(field) for field in ('hash','version')):
            raise HTTPException(409,detail='原文版本已改变，请重新拆解核对，旧草稿不会覆盖现有知识卡')
    for suggestion in value.get('merge_suggestions',[]):
        evidence(suggestion.get('citation_ids',[]),a.get('source_ids',[]),con)
    a=dict(a,output=value);checked=review(a,con)
    supplied=decisions or []
    if not isinstance(supplied,list) or len(supplied)>50:
        raise ValueError('知识卡决定应为最多 50 项的数组')
    for decision in supplied:
        if not isinstance(decision,dict) or not isinstance(decision.get('item_id'),str):raise ValueError('知识卡决定缺少有效项目标识')
        if not set(decision)<={'item_id','action','target_id','target_revision','reason'}:raise ValueError('知识卡决定包含未知字段')
        if 'reason' in decision and (not isinstance(decision['reason'],str) or len(decision['reason'])>2000):raise ValueError('核对理由应为不超过 2000 字的文本')
        if 'target_id' in decision and not isinstance(decision['target_id'],str):raise ValueError('合并目标标识无效')
        if 'target_revision' in decision and (type(decision['target_revision']) is not int or decision['target_revision']<1):raise ValueError('合并目标版本无效')
    decision_map={d.get('item_id'):d for d in supplied}
    if len(decision_map)!=len(supplied) or set(decision_map)-{i['item_id'] for i in checked['items']}:
        raise ValueError('存在重复或未知的知识卡决定')
    ids=[];mapping={};logs=[];initial_revisions={c['id']:c.get('revision',1) for c in active_concepts(a.get('space_id'),con)}
    for item in checked['items']:
        decision=decision_map.get(item['item_id'])
        if decision is None:
            if item['requires_decision']:raise HTTPException(409,detail={'message':'请逐条核对重复知识候选与合并建议','item_id':item['item_id']})
            decision={'action':'create'}
        action=decision.get('action');card=item['card'];target=None;refs=item['evidence']
        if action not in ('create','ignore','merge'):raise ValueError('未知知识卡核对操作')
        if action=='create':
            if not item['can_create']:raise ValueError('此建议没有完整知识卡，不能直接新建')
            if (item['candidates'] or item['kind']=='suggestion') and len(str(decision.get('reason','')).strip())<4:
                raise ValueError('另建有重复候选的知识卡时，请填写至少 4 字的区别说明')
            target=db.put('concept',dict(card,space_id=a.get('space_id'),domain_id=a.get('domain_id'),
                source_ids=list(dict.fromkeys(r['source_id'] for r in refs)),evidence_refs=refs,mastery=0,needs_review=False),con=con)
        elif action=='merge':
            requested_target=decision.get('target_id','')
            if requested_target.startswith('draft:card:'):
                target_index=requested_target.split(':')[-1]
                if decision.get('target_revision')!=a.get('revision',1) or not mapping.get(target_index):
                    raise HTTPException(409,detail='同批目标知识卡尚未采纳；请先为目标卡选择新建或合入，不能合入已忽略项')
                target=db.get(mapping[target_index],'concept',con)
            else:
                target=db.get(requested_target,'concept',con)
            if target.get('space_id')!=a.get('space_id') or target.get('deleted') or target.get('merged_into'):
                raise ValueError('合并目标不属于当前空间或已失效')
            if not requested_target.startswith('draft:card:') and decision.get('target_revision')!=initial_revisions.get(target['id']):
                raise HTTPException(409,detail='目标知识卡已更新，请重新核对后合并')
            if not any(c['concept_id']==requested_target for c in item['candidates']):
                raise ValueError('合并目标不在本次核对候选中')
            variants=list(target.get('definition_variants',[]))
            variant={'artifact_id':a['id'],'item_id':item['item_id'],'card':card,'evidence_refs':refs,
                     'reason':str(decision.get('reason') or item.get('suggestion',{}).get('reason') or '用户核对后保留为补充候选'),
                     'status':'pending_review','created':db.now()}
            # A different definition remains a pending variant, even after merge;
            # we never replace the original just because semantic scores are high.
            different=normalize(card.get('definition'))!=normalize(target.get('definition'))
            if different or item['kind']=='suggestion':variants.append(variant)
            aliases=list(dict.fromkeys([*target.get('aliases',[]),*card.get('aliases',[]),card['title']]))
            evidence_refs=list(target.get('evidence_refs',[]))
            if not evidence_refs:
                evidence_refs=evidence(target.get('citation_ids',[]),target.get('source_ids',[]),con)
            ref_keys={(r['chunk_id'],r['source_version'],r.get('source_hash')) for r in evidence_refs}
            evidence_refs.extend(r for r in refs if (r['chunk_id'],r['source_version'],r.get('source_hash')) not in ref_keys)
            target=db.update(target['id'],{'citation_ids':list(dict.fromkeys(target.get('citation_ids',[])+card.get('citation_ids',[]))),
                'source_ids':list(dict.fromkeys(target.get('source_ids',[])+[r['source_id'] for r in refs])),
                'aliases':aliases,'evidence_refs':evidence_refs,'definition_variants':variants,
                'needs_review':bool(target.get('needs_review') or different or item['kind']=='suggestion')},con=con)
        if target:
            ids.append(target['id'])
            if item['kind']=='card':mapping[item['item_id'].split(':')[1]]=target['id']
            for ref in refs:db.link(target['id'],ref['chunk_id'],'evidence',con)
        logs.append(db.put('card_decision',{'space_id':a.get('space_id'),'artifact_id':a['id'],'item_id':item['item_id'],
            'action':action,'target_id':target['id'] if target else None,'target_revision_before':initial_revisions.get(target['id']) if target else None,
            'reason':str(decision.get('reason') or ('用户忽略此候选' if action=='ignore' else '用户核对采纳')),
            'fingerprint':item['fingerprint'],'evidence_refs':refs,'card':card},con=con))
    return list(dict.fromkeys(ids)),mapping,logs
