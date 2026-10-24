import React,{useEffect,useState} from 'react';
import {api,date,requestId} from './api';
import {Button,Field,Modal,Citation,Empty} from './components';
import {Markdown} from './markdown';

export function AssessmentAction({id,spaceId,subject='assessment',perform,onDone,voided=false}){
 const [shown,setShown]=useState(false),[record,setRecord]=useState(null),[error,setError]=useState(''),[saving,setSaving]=useState(false);
 const [reason,setReason]=useState('题目要求与资料或实训契约不一致'),[operation,setOperation]=useState('void');
 const load=()=>api('/assessments/'+id+'?space_id='+encodeURIComponent(spaceId)+'&subject='+subject).then(r=>{setRecord(r);setOperation(r.voided?'restore':'void');if(r.voided)setReason('已重新核对题目与评价依据，恢复此评价');}).catch(e=>setError(e.message));
 useEffect(()=>{if(shown){setRecord(null);setError('');setReason('题目要求与资料或实训契约不一致');load()}},[shown,id,subject]);
 async function submit(){
  setSaving(true);setError('');
  try{
   const result=await api('/assessments/'+id+'/'+operation,{space_id:spaceId,subject,revision:record.revision,request_id:requestId(),reason});
   if(perform)await perform(async()=>result,operation==='void'?'评价已作废，掌握度、错题与复习安排已同步重算。原记录保留。':'已恢复评价，并重新计算相关学习状态。');
   setShown(false);onDone?.(result);
  }catch(e){setError(e.message)}finally{setSaving(false)}
 }
 return <><button type="button" className="link assessment-action" onClick={()=>setShown(true)}>{voided?'查看作废与恢复':subject==='question'?'题目有误 · 作废本题':'题目有误 · 作废评价'}</button>{shown&&<Modal title={record?.voided?'已作废的题目与评价':'作废错误题目及评价'} onClose={()=>!saving&&setShown(false)}>
  {error&&<div className="alert error" role="alert">{error}<button type="button" onClick={()=>{setError('');load()}}>重新核对最新版本</button></div>}
  {!record?<Empty>正在核对题目及关联证据…</Empty>:<div className="assessment-review">
   <p className="hint">保留原题、答案和运行报告。作废会撤销相关评分证据并同步重算，不会把这次作答改成零分。</p>
   {record.voided&&<p className="assessment-void">已作废：{record.reason||record.target?.assessment_void_reason||'已记录原因'}。恢复必须由你主动提交。</p>}
   <details><summary>核对原题与关联记录</summary><Markdown>{record.question||record.target?.question||record.target?.output?.question||'查看关联评价'}</Markdown><p>{record.attempts?.length||0} 条关联作答；仅处理具有明确题目关联的记录。</p>{record.target?.answer&&<pre>{record.target.answer}</pre>}</details>
   <Field label={record.voided?'恢复依据':'作废原因'}><textarea rows={3} maxLength={2000} value={reason} onChange={e=>setReason(e.target.value)}/></Field>
   <p className="muted">相关摘要与引用会退出使用或等待重建。原始证据仍能追溯，不会删除学习历史。</p>
   <div className="actions"><Button busy={saving} disabled={!reason.trim()||!!error} onClick={submit}>{record.voided?'恢复并同步重算':'作废并同步重算'}</Button><Button secondary disabled={saving} onClick={()=>setShown(false)}>返回</Button></div>
  </div>}
 </Modal>}</>;
}

export function TeachingTurn({event:e,index,current,session,spaceId,goalId,perform,open,onEvidence}){
 const voided=!!e.assessment_voided;
 const detail=<>
  {e.question_voided&&<p className="assessment-void">本轮生成的旧题已作废；以下讲解仅保留原文用于追溯，请以新题与核对后的依据为准。</p>}
  {e.answer&&<section className="learner-response"><h4>我的作答</h4><pre>{e.answer}</pre></section>}
  {voided&&<p className="assessment-void">本次评价已作废，原始内容仅供追溯，不计入掌握度。</p>}
  {e.assessment_correction?.corrected_score!=null&&!voided&&<section className="teaching-feedback"><h4>{e.output.score==null?'补记评价':'已纠正评价'} · {Math.round(e.assessment_correction.corrected_score*100)}%</h4><Markdown>{e.assessment_correction.correction_reason}</Markdown><p className="muted">当前有效评价；以下原始模型内容保留供核对。</p></section>}
  {e.output.feedback&&<section className="teaching-feedback"><div className="section-heading"><h4>本轮反馈</h4><span className="badge">{voided?'已作废':'AI 估计'}</span></div><Markdown>{e.output.feedback}</Markdown></section>}
  {e.answer&&e.output.score!=null&&e.assessment_correction?.corrected_score==null&&!e.output.feedback?.trim()&&<p className="muted" role="status">模型未单列反馈；请核对下方“讲解与依据”中的评价说明。评分为 AI 估计，可纠正或作废。</p>}
  <section className="lesson-explanation"><h4>讲解与依据</h4><Markdown>{e.output.explanation}</Markdown><Citation ids={e.output.citations} open={open}/></section>
  {(e.output.inference||e.output.supplement)&&<details className="lesson-extra"><summary>补充说明与推断</summary>{e.output.inference&&<><h4>AI 推断</h4><Markdown>{e.output.inference}</Markdown></>}{e.output.supplement&&<><h4>补充解释</h4><Markdown>{e.output.supplement}</Markdown></>}</details>}
  {e.output.evidence_ids?.length>0&&<div className="actions">{e.output.evidence_ids.map((id,i)=><button className="citation" key={id} onClick={()=>onEvidence(id)}>证据 {i+1} ↗</button>)}</div>}
  <div className="actions">{!current&&<button className="link" onClick={()=>onEvidence(e.id)}>查看本轮原始记录</button>}{e.answer&&<AssessmentAction id={e.id} spaceId={spaceId} perform={perform} voided={voided}/>}
   <button className="link" onClick={()=>perform(()=>api('/entries',{space_id:spaceId,entry_type:'ai_answer',title:e.output.question?.slice(0,100)||'教学摘录',body:e.output.explanation,goal_id:goalId||null,concept_id:session.concept_id,session_id:session.id,memory_enabled:false,references:(e.output.citations||[]).map(id=>({kind:'chunk',id}))}),'已保存到随手记；AI 回答不计作资料事实。')}>保存为笔记</button>
  </div>
 </>;
 return <article className={'panel teaching-turn '+(current?'current-turn':'previous-turn')}>
  <div className="turn-meta"><span>第 {index+1} 轮 · {date(e.created)}</span><span>{e.model} · {({fast:'快速',standard:'标准',deep:'深入',max:'高强度'})[e.reasoning]||'默认'}</span></div>
  {e.deleted?<p className="muted">该记录已删除。</p>:current?detail:<details><summary>查看{e.answer?'作答与反馈':'讲解记录'}{voided?' · 评价已作废':''}</summary>{detail}</details>}
  {current&&!e.deleted&&e.output.question&&<section className={'question current-question '+(session.current_task_voided?'void-question':'')} aria-label="当前学习问题">
   <div className="section-heading"><div><span className="eyebrow">YOUR NEXT STEP</span><h3>{session.current_task_voided?'本题已作废':session.current_task_stale?'题目依据待重核':e.output.is_transfer?'变形练习':'试着回答'}</h3></div><AssessmentAction id={session.current_task_event_id||e.id} subject="question" spaceId={spaceId} perform={perform} voided={session.current_task_voided}/></div>
   <Markdown>{e.output.question}</Markdown><p className="muted">{session.current_task_voided?'请生成新题；原题与草稿保留，不再接受本题评分。':session.current_task_stale?'相关依据已改变，请继续生成新题；原草稿仍保留。':'先独立尝试；提交后提供评价反馈。需要参考答案时，可主动使用“看答案”。'}</p>
  </section>}
 </article>;
}

export function PracticeEvidence({items=[],usedIds=[],onEvidence}){
 return <section className="practice-evidence"><h3>当前可用实训证据</h3><p className="muted">与当前会话或目标知识点明确关联。标有“本轮已参考”的报告才进入了刚才的 AI 请求；新报告可更新摘要后使用。</p>{items.length?items.map(r=><button className="practice-proof" key={r.id} onClick={()=>onEvidence(r.id)}>
  <span className="proof-heading"><strong>{r.exercise_title||r.exercise_id}</strong><span className="badge">{r.assessment_voided?'评价已作废':r.exit_code===0?'运行通过':'运行未通过'}</span></span>
  <small>{usedIds.includes(r.id)?"本轮已参考":"可供下轮参考"}</small><span>测试 {r.counts?.tests??'—'} · 失败 {r.counts?.failures??'—'} · 退出码 {r.exit_code??'—'}</span>
  {r.mutation_summary?.required&&<span>缺陷检出 {r.mutation_summary.detected}/{r.mutation_summary.total}</span>}
  <small>{date(r.created)} · {r.association==='explicit_session'?'本会话':'同目标与知识点'} · v{r.revision}</small><small>打开原始报告 ↗</small>
 </button>):<p className="muted">尚无明确关联的运行报告。代码文字或静态评价不能代替实际执行。</p>}</section>;
}
