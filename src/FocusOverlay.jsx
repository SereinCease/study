import React,{useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {api,requestId} from './api';
import {AutumnScene} from './AutumnScene';
import './focus.css';

const CACHE='learnloop.focus.server.v1';
function readCache(){try{return JSON.parse(localStorage.getItem(CACHE)||'null');}catch{return null;}}
function keepCache(value){try{localStorage.setItem(CACHE,JSON.stringify(value));}catch{/* Server remains authoritative. */}}
export function focusRemaining(session,stamp=Date.now()/1000){
 if(!session)return 0;
 const value=session.status==='running'?Number(session.deadline)-stamp:Number(session.remaining_seconds);
 return Number.isFinite(value)?Math.max(0,Math.min(Number(session.duration_seconds)||0,value)):0;
}
const clockLabel=value=>`${String(Math.floor(Math.ceil(value)/60)).padStart(2,'0')}:${String(Math.ceil(value)%60).padStart(2,'0')}`;
export function FocusOverlay({open,onClose,spaceId,goalId,sessionId,taskTitle='',preferences={}}){
 const dialog=useRef(null),current=useRef(null),syncing=useRef(null),generation=useRef(0),offset=useRef(0),createKey=useRef(null);
 const [value,setValue]=useState(null),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[online,setOnline]=useState(false),[error,setError]=useState('');
 const [minutes,setMinutes]=useState(25),[task,setTask]=useState(taskTitle),[now,setNow]=useState(Date.now()/1000),[fresh,setFresh]=useState(false);
 function receive(result){
  offset.current=Number(result.server_time)-Date.now()/1000;current.current=result.session;
  setValue(result.session);if(['ready','running','paused'].includes(result.session?.status))setFresh(false);setNow(Date.now()/1000+offset.current);setOnline(true);keepCache(result);
 }
 async function refresh(){
  const run=generation.current;if(syncing.current===run)return;syncing.current=run;
  try{const result=await api('/focus-sessions');if(run===generation.current)receive(result);}
  catch(e){if(run===generation.current){setOnline(false);setError('暂时无法同步计时。已显示本机恢复副本，联网后以服务端状态为准。');}}
  finally{if(run===generation.current)setLoading(false);if(syncing.current===run)syncing.current=null;}
 }
 useEffect(()=>{
  if(!open)return;
  const previouslyFocused=document.activeElement;setTask(taskTitle);setFresh(false);setError('');setLoading(true);setOnline(false);
  const cached=readCache();current.current=cached?.session||null;setValue(cached?.session||null);
  dialog.current?.showModal();dialog.current?.querySelector('button')?.focus();refresh();
  const tick=setInterval(()=>setNow(Date.now()/1000+offset.current),500);
  const poll=setInterval(()=>{if(!document.hidden&&['ready','running','paused'].includes(current.current?.status))refresh();},5000);
  const foreground=()=>{if(!document.hidden)refresh();};
  window.addEventListener('focus',foreground);window.addEventListener('online',foreground);document.addEventListener('visibilitychange',foreground);
  return()=>{generation.current++;clearInterval(tick);clearInterval(poll);window.removeEventListener('focus',foreground);window.removeEventListener('online',foreground);document.removeEventListener('visibilitychange',foreground);dialog.current?.close();if(previouslyFocused?.isConnected)previouslyFocused.focus();};
 },[open]);
 const remaining=focusRemaining(value,now),active=!fresh&&value&&['ready','running','paused'].includes(value.status),completed=!fresh&&value?.status==='completed';
 useEffect(()=>{if(open&&value?.status==='running'&&remaining===0&&online)refresh();},[open,value?.status,remaining===0,online]);
 async function change(action){
  if(busy||!online||!value)return;setBusy(true);setError('');
  try{receive(await api('/focus-sessions/'+value.id,{revision:value.revision,action},'PATCH'));}
  catch(e){setError(e.message);await refresh();}
  finally{setBusy(false);}
 }
 async function begin(){
  if(busy||!online||!spaceId)return;setBusy(true);setError('');
  const payload={space_id:spaceId,goal_id:goalId||null,session_id:sessionId||null,task_title:task.trim(),duration_minutes:Number(minutes)};
  const signature=JSON.stringify(payload);
  if(createKey.current?.signature!==signature)createKey.current={signature,key:requestId()};
  try{
   const result=await api('/focus-sessions',{...payload,request_id:createKey.current.key});receive(result);setFresh(false);
   if(result.session.status==='ready')receive(await api('/focus-sessions/'+result.session.id,{revision:result.session.revision,action:'start'},'PATCH'));
   createKey.current=null;
  }catch(e){setFresh(false);setError(e.message);await refresh();}finally{setBusy(false);}
 }
 if(!open)return null;
 const display=active||completed?remaining:minutes*60;
 const labels={ready:'准备开始',running:'沉心读一卷',paused:'计时已暂停',completed:value?.finished_reason==='elapsed'?'这一轮时间已到':'这一轮已结束'};
 return createPortal(<dialog className="focus-overlay" ref={dialog} onCancel={e=>{e.preventDefault();onClose();}} aria-labelledby="focus-title">
  <AutumnScene focus preferences={preferences}/>
  <div className="focus-shade" aria-hidden="true"/>
  <header className="focus-heading"><span>墨染星穹 <i/> 秋 · 月夕观星</span><button type="button" className="focus-return" onClick={onClose}>返回学习 <span aria-hidden="true">↗</span></button></header>
  <section className="focus-reading">
   <p className="focus-eyebrow">留一段时间，与知识相伴</p><h1 id="focus-title">窗边伴读</h1>
   <output className="focus-clock" role="timer" aria-live="off" aria-label="剩余时间">{clockLabel(display)}</output>
   <div className="focus-incense" aria-hidden="true"><span style={{height:(value?remaining/Math.max(1,value.duration_seconds):1)*100+'%'}}/></div>
   <p className="focus-state" role="status">{loading?'正在恢复计时…':active||completed?labels[value.status]:'择一件事，慢慢完成'}</p>
   {!active&&!completed?<div className="focus-config">
    <label>本轮待办<input value={task} maxLength={160} placeholder="例如：读懂 fixture 的作用域" onChange={e=>{setTask(e.target.value);createKey.current=null;}}/></label>
    <label>伴读时长<select value={minutes} onChange={e=>{setMinutes(Number(e.target.value));createKey.current=null;}}>{[1,5,15,25,45].map(m=><option key={m} value={m}>{m} 分钟{m===1?' · 体验':''}</option>)}</select></label>
   </div>:<p className="focus-task">{value.task_title||'一段安静的学习时间'}</p>}
   {active&&value.space_id!==spaceId&&<p className="focus-notice">这是另一知识空间尚未结束的计时。继续或结束后，可开始当前任务。</p>}
   <div className="focus-actions">
    {!active&&!completed&&<button type="button" disabled={loading||busy||!online||!spaceId} onClick={begin}>开始伴读</button>}
    {active&&<><button type="button" disabled={busy||!online} onClick={()=>change(value.status==='running'?'pause':value.status==='paused'?'resume':'start')}>{value.status==='running'?'暂停计时':value.status==='paused'?'继续计时':'开始计时'}</button><button type="button" className="quiet" disabled={busy||!online} onClick={()=>change('finish')}>结束本轮</button></>}
    {completed&&<><button type="button" onClick={onClose}>返回当前任务</button><button type="button" className="quiet" disabled={busy||!online} onClick={()=>{setFresh(true);createKey.current=null;}}>再开始一轮</button></>}
   </div>
   {error&&<p className="focus-sync-error" role="alert">{error} <button type="button" disabled={busy} onClick={()=>{setError('');refresh();}}>重新同步</button></p>}
   {!spaceId&&!active&&<p className="focus-notice">请返回并选择一个知识空间后开始计时。</p>}
   <p className="focus-footnote">{value?.status==='running'?'返回学习后计时继续，暂停动效不会暂停计时。':value?.status==='paused'?'计时已暂停，返回后可继续；场景动效独立控制。':'计时只记录时间，不评价知识掌握。'}</p>
   {!online&&!loading&&<small className="focus-offline">恢复副本 · 操作暂不可用</small>}
  </section>
 </dialog>,document.body);
}
export default FocusOverlay;
