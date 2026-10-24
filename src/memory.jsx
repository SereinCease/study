import React,{useState,useEffect} from 'react';
import {api,date,labels} from './api';
import {Button,Empty,Field,Modal,Jobs} from './components';
import {AssessmentAction} from './learning-quality';
import {Markdown} from './markdown';

export function EvidenceViewer({id,onClose,perform}){
 const [record,setRecord]=useState(null),[error,setError]=useState(''),[saving,setSaving]=useState(false);
 async function update(action){setSaving(true);setError('');try{const r=await action();setRecord(r);if(perform)await perform(async()=>r)}catch(e){setError(e.message)}finally{setSaving(false)}}
 useEffect(()=>{api('/evidence/'+id).then(setRecord).catch(e=>setError(e.message))},[id]);
 return <Modal title="历史证据原件" onClose={onClose}>{error&&<p className="alert error" role="alert">{error}</p>}{!record?<Empty>正在读取…</Empty>:<>
  <p className="muted">{date(record.created)} · {record.kind} · 版本 {record.revision}</p>
  {['event','conversation','summary'].includes(record.kind)&&<p className="hint">这里包含 AI 对话或摘要，不代表独立掌握。当前评价以作答证据中的纠正为准。</p>}
  {record.question&&<Markdown>{record.question}</Markdown>}{record.assessment_voided&&<p className="assessment-void">评价已作废：{record.assessment_void_reason||record.void_reason||"见作废记录"}。原始记录保留。</p>}{record.answer&&<div className="my-answer"><strong>原始作答</strong><pre>{record.answer}</pre></div>}
  {record.output?.explanation&&<Markdown>{record.output.explanation}</Markdown>}{record.output?.feedback&&<p>{record.output.feedback}</p>}{record.output?.question&&<div className="question"><strong>当时的问题</strong><p>{record.output.question}</p><small>{record.output.rubric}</small></div>}{record.output&&<details><summary>查看完整结构化记录</summary><pre>{JSON.stringify(record.output,null,2)}</pre></details>}
  {record.feedback&&<p>{record.feedback}</p>}{record.correction_reason&&<p className="hint">纠正：{record.correction_reason} · 分数 {record.corrected_score}</p>}
  {record.assessment_correction&&<pre>当前评价纠正：{JSON.stringify(record.assessment_correction,null,2)}</pre>}
  {record.kind==='run'&&<section className="practice-evidence"><h3>实际运行事实</h3><p>退出码 {record.exit_code??'—'} · 测试 {record.counts?.tests??'—'} · 失败 {record.counts?.failures??'—'}</p>{record.mutation_summary?.required&&<p>缺陷检出 {record.mutation_summary.detected}/{record.mutation_summary.total}</p>}<p className="muted">此报告来自实际容器运行；原始结果保留，评价是否有效另行标记。</p></section>}{record.stdout&&<pre>{record.stdout}</pre>}{record.stderr&&<pre>{record.stderr}</pre>}
  {['attempt','event'].includes(record.kind)&&<AssessmentAction id={record.id} spaceId={record.space_id} perform={perform} voided={record.assessment_voided} onDone={()=>api('/evidence/'+id).then(setRecord)}/>}
  <div className="actions"><Button secondary busy={saving} disabled={record.deleted} onClick={()=>update(()=>api('/evidence/'+id,{memory_enabled:record.memory_enabled===false},'PATCH'))}>{record.memory_enabled===false?'恢复用于教学':'不再用于教学'}</Button>
  <Button secondary busy={saving} disabled={record.deleted} onClick={()=>{if(window.confirm('删除该学习记录？原文会退出检索，关联摘要失效，掌握度重新计算。'))update(()=>api('/evidence/'+id,{},'DELETE'))}}>删除记录</Button></div>
 </>}</Modal>
}

export function ProfilePage({perform,busy,refreshKey,start,spaceId,space}){
 const [profile,setProfile]=useState(null),[text,setText]=useState(''),[category,setCategory]=useState('preference'),[edit,setEdit]=useState(null),[error,setError]=useState(''),[evidence,setEvidence]=useState(null);
 const load=()=>api('/learner-profile?space_id='+spaceId+'&domain='+encodeURIComponent(space?.domain_id||'general')).then(setProfile).catch(e=>setError(e.message));
 useEffect(()=>{load()},[refreshKey,spaceId]);
 async function save(e){e.preventDefault();const r=await perform(()=>api('/learner-profile/facts'+(edit?'/'+edit.id:''),{text,category,space_id:spaceId,domain_id:edit?.domain_id||space?.domain_id||'general',revision:edit?.revision,memory_enabled:edit?.memory_enabled??true},edit?'PATCH':'POST'));if(r){setEdit(null);setText('');load()}}
 return <div className="stack"><div className="profile-intro"><span className="eyebrow">LEARNING DOSSIER / 学习档案</span><h2>每一个判断，都能找到依据。</h2><p>你的偏好由你决定。能力状态由练习积累，随新的证据重新评估。</p></div>{error&&<p className="error">{error}</p>}
 <div className="two-col"><section className="panel"><h2>我希望教练记住</h2><form onSubmit={save}><Field label="记录类型"><select value={category} onChange={e=>setCategory(e.target.value)}><option value="preference">教学偏好</option><option value="self_report">自述基础（不计作掌握）</option><option value="goal">长期目标</option></select></Field><Field label="内容"><textarea rows={4} value={text} maxLength={2000} onChange={e=>setText(e.target.value)} placeholder="例如：先给一个最小示例，再解释原理。" required/></Field><div className="actions"><Button busy={busy}>{edit?'保存纠正':'加入学习档案'}</Button>{edit&&<Button type="button" secondary onClick={()=>{setEdit(null);setText('')}}>取消</Button>}</div></form></section>
 <section className="panel"><h2>已记录的偏好与自述</h2>{profile?.facts.length?profile.facts.map(f=><article className="fact" key={f.id}><small>用户声明 · {f.domain_id==='global'?'全局':f.domain_id} · v{f.revision}</small><p>{f.text}</p><div className="actions"><button className="link" onClick={()=>{setEdit(f);setText(f.text);setCategory(f.category)}}>编辑</button><button className="link" onClick={async()=>{await perform(()=>api('/learner-profile/facts/'+f.id,{...f,memory_enabled:f.memory_enabled===false},'PATCH'));load()}}>{f.memory_enabled===false?'启用记忆':'停用记忆'}</button><button className="link" onClick={async()=>{await perform(()=>api('/learner-profile/facts/'+f.id,{},'DELETE'));load()}}>删除</button></div>{f.memory_enabled===false&&<small>已停用，不会提供给模型</small>}</article>):<Empty>尚无档案条目。从一条解释偏好开始。</Empty>}</section></div>
 <section className="panel"><h2>能力状态 · 估计值</h2><p className="muted">{profile?.note}</p>{profile?.mastery.length?profile.mastery.map(m=><article className="mastery-row" key={m.id}><div><strong>{m.title}</strong><small>{m.evidence_count||0} 条有效证据 · {m.independent_dates||0} 个独立成功日期</small></div><div className="mastery-meter" aria-label={'掌握度估计 '+Math.round(m.score*100)+'%'}><span style={{width:(m.score*100)+'%'}}/></div><strong>{Math.round(m.score*100)}%</strong><span className="badge">{m.state==='strong'?'掌握较好':m.state==='learning'?'学习中':'待验证'}</span>{m.last_attempt&&<button className="link" onClick={()=>setEvidence(m.last_attempt)}>最近证据</button>}<Button secondary onClick={()=>start('explain',{concept_id:m.concept_id,goal:'独立复测，不先展示答案'})}>复测</Button></article>):<Empty>完成一次有效作答后，能力状态会出现在这里。</Empty>}</section>
 {evidence&&<EvidenceViewer id={evidence} onClose={()=>setEvidence(null)} perform={perform}/>}</div>
}

export function StoragePanel({perform,busy,refreshKey}){
 const [data,setData]=useState(null),[preview,setPreview]=useState(null),[error,setError]=useState('');
 useEffect(()=>{api('/storage').then(setData).catch(e=>setError(e.message))},[refreshKey]);
 const mb=n=>(n/1024/1024).toFixed(2)+' MB';
 return <section className="panel"><span className="eyebrow">LOCAL ARCHIVE</span><h2>存储与维护</h2>{error&&<p className="error">{error}</p>}{data&&<><div className="storage-stats">{[['学习数据库',data.database_bytes],['原始资料',data.original_bytes],['本地模型',data.model_bytes],['缓存',data.cache_bytes]].map(([label,n])=><div key={label}><small>{label}</small><strong>{mb(n)}</strong></div>)}</div><p className="muted">历史证据 {data.evidence_count} 条 · 已建立向量 {data.vector_count} 条。全文索引实时更新，语义索引可重建。</p><div className="actions"><Button secondary busy={busy} onClick={async()=>{const p=await perform(()=>api('/storage/preview'));if(p)setPreview(p)}}>预览可清理内容</Button><Button secondary busy={busy} onClick={()=>perform(()=>api('/evidence/reindex',{}),'历史语义索引重建已加入后台任务。')}>重建历史语义索引</Button></div>{preview&&<div className="maintenance-preview"><p>可回收 {mb(preview.bytes)} · {preview.files.length} 个文件项 · {preview.obsolete_vectors} 个无效向量</p><p className="muted">{preview.protected}</p>{preview.files.map(x=><small key={x.path}>{x.path} · {mb(x.bytes)}</small>)}<Button busy={busy} onClick={async()=>{await perform(()=>api('/storage/cleanup',{}),'清理已加入后台任务。');setPreview(null)}}>执行安全清理</Button></div>}<details><summary>自动清理规则与记录</summary><p>异常临时文件 24 小时；缓存及旧索引 7 天；日志 30 天；任务明细 90 天后压缩。启动和后台空闲时检查，保留原始学习证据。</p>{data.last_runs.map(r=><p key={r.id}>{date(r.created)} · 已回收 {mb(r.reclaimed_bytes)} · 压缩 {r.compacted_jobs} 个任务</p>)}</details></>}</section>
}
