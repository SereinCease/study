import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';
import {Button,Field,Badge,Citation,Empty} from './components';
import {Markdown} from './markdown';

export function ArtifactReview({artifact,perform,busy,open}){
 const [draft,setDraft]=useState(artifact.output),[review,setReview]=useState(null),[decisions,setDecisions]=useState({}),[error,setError]=useState(''),[loading,setLoading]=useState(false),[edited,setEdited]=useState(false),[working,setWorking]=useState(false);
 const currentId=useRef(artifact.id),loadedId=useRef(null),loadSequence=useRef(0),actionSequence=useRef(0),actionRunning=useRef(false),dirty=useRef(false),baseRevision=useRef(artifact.revision);
 currentId.current=artifact.id;
 async function load(replaceDraft=false){
  const id=artifact.id,sequence=++loadSequence.current;
  setLoading(true);setError('');
  try{
   const r=await api('/artifacts/'+id+'/review');
   if(sequence!==loadSequence.current||currentId.current!==id)return;
   setReview(r);setDecisions(Object.fromEntries(r.items.map(item=>[item.item_id,{item_id:item.item_id,action:item.requires_decision||item.kind==='suggestion'?'': 'create',target_id:'',reason:''}])));
   if(replaceDraft||!dirty.current){setDraft(r.output||artifact.output);baseRevision.current=r.revision;dirty.current=false;setEdited(false)}
   else if(baseRevision.current!==r.revision)setError('服务器草稿已有新版本。本机修改仍保留，请复制需要保留的内容，再决定是否载入服务器版本。');
  }catch(e){if(sequence===loadSequence.current&&currentId.current===id)setError(e.message)}finally{if(sequence===loadSequence.current&&currentId.current===id)setLoading(false)}
 }
 useEffect(()=>{
  if(loadedId.current!==artifact.id){loadedId.current=artifact.id;dirty.current=false;setEdited(false);setDraft(artifact.output);baseRevision.current=artifact.revision;setReview(null);actionSequence.current++;actionRunning.current=false;setWorking(false)}
  else if(!dirty.current){setDraft(artifact.output);baseRevision.current=artifact.revision}
  load();return()=>{loadSequence.current++};
 },[artifact.id,artifact.revision,artifact.status]);
 const choose=(item,changes)=>{if(actionRunning.current)return;setDecisions(all=>({...all,[item.item_id]:{...all[item.item_id],...changes}}))};
 const edit=(i,changes)=>{if(actionRunning.current)return;dirty.current=true;setDraft(old=>({...old,cards:old.cards.map((c,k)=>k===i?{...c,...changes}:c)}));setEdited(true)};
 async function save(){
  if(actionRunning.current||loading)return;actionRunning.current=true;const id=artifact.id,sequence=++actionSequence.current;setWorking(true);setError('');
  try{const r=await api('/artifacts/'+id,{output:draft,revision:baseRevision.current},'PATCH');
   if(sequence!==actionSequence.current||currentId.current!==id)return;
   setDraft(r.output);baseRevision.current=r.revision;dirty.current=false;setEdited(false);await load();if(perform)await perform(async()=>r,'草稿已保存，请根据最新候选核对。');
  }catch(e){if(sequence===actionSequence.current&&currentId.current===id)setError(e.message)}finally{if(sequence===actionSequence.current&&currentId.current===id){actionRunning.current=false;setWorking(false)}}
 }
 async function accept(){
  if(actionRunning.current||loading||edited||!ready)return;actionRunning.current=true;const id=artifact.id,sequence=++actionSequence.current;setWorking(true);setError('');
  try{const r=await api('/artifacts/'+id,{output:draft,accept:true,review_revision:review.revision,decisions:Object.values(decisions)},'PATCH');
   if(sequence!==actionSequence.current||currentId.current!==id)return;
   await load();if(perform)await perform(async()=>r,'核对结果已保存，逐项处理记录见本批下方。');
  }catch(e){if(sequence===actionSequence.current&&currentId.current===id)setError(e.message)}finally{if(sequence===actionSequence.current&&currentId.current===id){actionRunning.current=false;setWorking(false)}}
 }
 const ready=review?.items.every(item=>{const d=decisions[item.item_id];return d?.action&&(d.action!=='merge'||d.target_id)&&(!(d.action==='create'&&item.requires_decision)||d.reason?.trim().length>=4)});
 const accepted=artifact.status==='accepted'||review?.status==='accepted',records=review?.decision_results||[],counts=records.reduce((all,row)=>({...all,[row.action]:(all[row.action]||0)+1}),{});
 return <article className="panel draft-artifact">
  <div className="section-heading"><div><span className="eyebrow">KNOWLEDGE REVIEW</span><h3>知识卡核对 · 第 {artifact.batch+1} 批</h3></div><Badge value={accepted?'已采纳':'逐条核对'}/></div>
  {error&&<div className="alert error" role="alert">{error}<button type="button" disabled={working||loading} onClick={()=>load()}>重新核对</button>{edited&&review?.revision!==baseRevision.current&&<button type="button" disabled={working||loading} onClick={()=>load(true)}>放弃本机修改并载入服务器版本</button>}</div>}
  {accepted?<><p className="hint">这一批已完成核对，重复提交不会再创建知识卡。</p>
   {edited&&<details open><summary>尚未保存的本机草稿（未覆盖服务器）</summary><textarea readOnly rows={8} aria-label="保留的本机知识卡草稿" value={JSON.stringify(draft,null,2)}/></details>}
   {loading?<Empty>正在读取已保存的核对记录…</Empty>:review?.decision_history_available?<>
    <p className="hint" role="status">{review.decision_records_complete?'本批处理结果':'可核实的部分结果'}：新建 {counts.create||0} 项 · 合入 {counts.merge||0} 项 · 忽略 {counts.ignore||0} 项。</p>
    {!review.decision_records_complete&&<p className="alert error">部分核对记录暂不可用，未计入以上数量；请重新核对。</p>}
    {records.map(row=><details key={row.id}><summary>{row.card?.title||row.item_id} · {{create:'已新建',merge:'已合入',ignore:'已忽略'}[row.action]||row.action}</summary>{row.action==='merge'&&<p>合入：{row.target_title||row.target_id}。原定义保留，差异需继续核对。</p>}<p>{row.reason}</p><Markdown>{row.card?.definition||''}</Markdown><Citation ids={row.card?.citation_ids||[]} open={open}/></details>)}
   </>:<p className="hint">历史批次未保存逐项处理记录，无法还原新建、合入和忽略数量。</p>}
  </>:loading?<Empty>正在比对同空间的已有知识卡…</Empty>:review?<>
   <p className="muted">相似只用于推荐，不代表两个概念相同。合入会保留已有定义，并记录新的引用或定义差异。</p>
   {review.coverage?.scope_note&&<p className="hint">{review.coverage.scope_note}</p>}
   {review.items.map((item,index)=>{
    const cardIndex=item.kind==='card'?Number(item.item_id.split(':')[1]):-1,card=cardIndex>=0?draft.cards[cardIndex]:item.card||item.suggestion||{},d=decisions[item.item_id]||{};
    const selected=item.candidates?.find(c=>c.concept_id===d.target_id);
    return <section className="knowledge-review-item" key={item.item_id}>
     <div className="section-heading"><span className="eyebrow">{String(index+1).padStart(2,'0')} / {item.kind==='suggestion'?'合并建议':'候选知识卡'}</span><span className="badge">{item.candidates?.length?`${item.candidates.length} 个可能相关概念`:'待核对的新概念'}</span></div>
     {cardIndex>=0?<><Field label="知识点名称"><input disabled={working} value={card.title} onChange={e=>edit(cardIndex,{title:e.target.value})}/></Field><Field label="定义"><textarea disabled={working} rows={3} value={card.definition} onChange={e=>edit(cardIndex,{definition:e.target.value})}/></Field></>:<><h3>{item.title||card.title}</h3><Markdown>{card.definition||item.suggestion?.reason||card.reason||item.reason||''}</Markdown></>}
     <Citation ids={card.citation_ids||item.citation_ids||[]} open={open}/>
     {card.points?.length>0&&<details><summary>展开要点、示例与自测</summary><ul>{card.points.map((p,i)=><li key={i}>{p}</li>)}</ul><Markdown>{card.example?.includes('```')?card.example:'```\n'+(card.example||'')+'\n```'}</Markdown><p>{card.question}</p></details>}
     {!!item.candidates?.length&&<div className="candidate-comparison">{item.candidates.map(c=><details key={c.concept_id} open={selected?.concept_id===c.concept_id}><summary>{c.title}</summary><p>{c.definition_excerpt}</p><ul>{(c.reasons||[]).map((r,i)=><li key={i}>{r}</li>)}</ul><Citation ids={c.citation_ids} open={open}/><small>仅为候选排序，不表示关联概率 · v{c.revision}</small></details>)}</div>}
     {item.blocked_merge_reason&&<p className="hint">{item.blocked_merge_reason}</p>}
     <Field label={'处理方式 '+(item.title||card.title||index+1)}><select disabled={working} value={d.action||''} onChange={e=>choose(item,{action:e.target.value,target_id:'',target_revision:undefined})}><option value="">请选择处理方式</option>{item.can_create&&<option value="create">作为独立概念新建</option>}{item.candidates?.length>0&&<option value="merge">合入已有知识卡</option>}<option value="ignore">忽略本项</option></select></Field>
     {d.action==='merge'&&<><Field label="合入目标"><select disabled={working} value={d.target_id||''} onChange={e=>{const target=item.candidates.find(c=>c.concept_id===e.target.value);choose(item,{target_id:target?.concept_id||'',target_revision:target?.revision})}}><option value="">请选择已有知识卡</option>{item.candidates.map(c=><option key={c.concept_id} value={c.concept_id}>{c.title}</option>)}</select></Field><p className="hint">已有定义不会被覆盖；差异将保留为待核对版本，引用随合并记录保存。</p></>}
     {(d.action==='merge'||d.action==='ignore'||d.action==='create'&&item.requires_decision)&&<Field label={d.action==='create'?'作为独立概念的理由（必填）':'核对理由'}><textarea disabled={working} rows={2} value={d.reason||''} maxLength={2000} onChange={e=>choose(item,{reason:e.target.value})} placeholder={d.action==='create'?'说明它与已有概念的区别':'保留这次选择的依据'}/></Field>}
    </section>;
   })}
   {!review.items.length&&<Empty>这批没有需要处理的新卡片；相同资料中已忽略的建议不会重复打扰。</Empty>}
   {draft.gaps?.length>0&&<details className="review-gaps"><summary>资料覆盖与待核对问题</summary><ul>{draft.gaps.map((g,i)=><li key={i}>{g}</li>)}</ul></details>}
   <div className="review-submit"><small>{edited?'草稿已修改，请先保存并重新比对候选。':'所有有候选的条目均需明确选择；未确认的关系不会自动成为学习前置条件。'}</small><div className="actions"><Button secondary busy={busy||working} disabled={!edited} onClick={save}>保存修改并重新比对</Button><Button busy={busy||working} disabled={!ready||edited} onClick={accept}>应用核对结果</Button></div></div>
  </>:null}
 </article>;
}
