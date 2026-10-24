import {freshTimer, remaining, reconcile, toggleTimer, restoreTimer, timeLabel} from './timer-core.js?v=4';
const KEY='learnloop.autumn.timer.v1';
const VIEW_KEY='learnloop.autumn.focusView.v1';
const $=s=>document.querySelector(s);
const safeRead=key=>{try{return localStorage.getItem(key)}catch{return null}};
const safeWrite=(key,value)=>{try{localStorage.setItem(key,value)}catch{$('#timer-storage').textContent='浏览器未允许本地保存，本次计时仅保留到页面关闭。'}};
const lerp=(a,b,t)=>a+(b-a)*t;
const ease=t=>t*t*t*(t*(t*6-15)+10);

export function createFocus({stage,getRegion,setRegion,wake,allowMotion,beforeEnter}) {
 let timer=restoreTimer(safeRead(KEY),Date.now());
 let active=false,progress=0,transition=null,originRect=null,originRegion=null,returnFocus=null,placeholder=null;
 let priorOverflow='',lastStatus='',lastDisplay='',savedInert=[];
 const panel=$('#focus-ui');
 const task=$('#focus-task');task.value=timer.task;
 $('#timer-duration').value=String(timer.durationMs/60000);
 const save=()=>safeWrite(KEY,JSON.stringify(timer));
 function updateTimer(){
  const next=reconcile(timer,Date.now());
  if(next!==timer){timer=next;save();}
  const ms=remaining(timer,Date.now()),label=timeLabel(ms);
  if(label!==lastDisplay){$('#timer-time').textContent=label;lastDisplay=label;}
  $('#timer-time').dataset.remaining=String(Math.ceil(ms/1000));
  $('#timer-time').dataset.state=timer.status;
  $('#focus-ui').style.setProperty('--remaining',String(ms/timer.durationMs));
  $('#timer-toggle').textContent=({idle:'开始专注',running:'暂停',paused:'继续',complete:'再来一轮'})[timer.status];
  $('#timer-duration').disabled=['running','paused'].includes(timer.status);
  $('#timer-config').hidden=timer.status==='running';
  $('#focus-enter').textContent=timer.status==='running'?`回到专注 · ${label}`:'窗边专注';
  $('#focus-exit-note').textContent=timer.status==='running'?'返回全景后，计时继续':'Esc 返回全景';
  if(lastStatus!==timer.status){
   $('#timer-status').textContent=({idle:'读一卷书，理清一个问题。',running:'这一刻，只做这一件事。',paused:'稍作停留，书卷为你留在这里。',complete:'本轮专注已结束。起身活动一下吧。'})[timer.status];
   lastStatus=timer.status;
  }
 }
 function viewport(){return {x:0,y:0,width:innerWidth,height:innerHeight};}
 function targetRegion(aspect){
  const width=Math.min(.70,.60*aspect/1.5),height=width*1.5/aspect;
  // In portrait view favour the face/book. In landscape leave quiet room at left for the timer.
  const center=aspect<1?.70:.65;
  return [Math.min(1-width,center-width/2),Math.min(1-height,.40),width,height];
 }
 function place(){
  if(!originRect)return;
  const target=viewport();
  const r={x:lerp(originRect.x,target.x,progress),y:lerp(originRect.y,target.y,progress),width:lerp(originRect.width,target.width,progress),height:lerp(originRect.height,target.height,progress)};
  Object.assign(stage.style,{left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px'});
  const dest=targetRegion(target.width/target.height);
  const width=lerp(originRegion[2],dest[2],progress),height=width*1.5/(r.width/r.height);
  setRegion([lerp(originRegion[0],dest[0],progress),lerp(originRegion[1],dest[1],progress),width,height]);
  stage.style.setProperty('--focus',String(progress));
  stage.dataset.focusProgress=progress.toFixed(3);
 }
 function end(){
  transition=null;
  if(!active){
   stage.classList.remove('is-focused');stage.style.cssText='';stage.removeAttribute('role');stage.removeAttribute('aria-modal');stage.removeAttribute('aria-label');
   panel.hidden=true;placeholder?.remove();placeholder=null;
   document.body.style.overflow=priorOverflow;
   for(const [el,wasInert] of savedInert)el.inert=wasInert;
   savedInert=[];setRegion(originRegion);stage.dataset.focus='overview';stage.dataset.focusProgress='0';
   returnFocus?.focus({preventScroll:true});
  }else stage.dataset.focus='focused';
 }
 function tick(now){
  if(!transition)return;
  const t=transition.duration?Math.min(1,Math.max(0,(now-transition.start)/transition.duration)):1;
  progress=lerp(transition.from,transition.to,ease(t));place();
  if(t===1)end();
 }
 function enter(){
  if(active)return;
  beforeEnter?.();
  if(!originRect||!stage.classList.contains('is-focused')){
   returnFocus=document.activeElement;
   originRegion=getRegion();originRect=stage.getBoundingClientRect();
   placeholder=document.createElement('div');placeholder.style.height=originRect.height+'px';placeholder.setAttribute('aria-hidden','true');stage.before(placeholder);
   priorOverflow=document.body.style.overflow;document.body.style.overflow='hidden';
   const nodes=document.querySelectorAll('body>header,.intro,.toolbar,.motionbar,.motion-note,.caption,.controls,.scope,.materials,.notes,body>footer');
   savedInert=[...nodes].map(el=>[el,el.inert]);for(const [el] of savedInert)el.inert=true;
   stage.classList.add('is-focused');stage.setAttribute('role','dialog');stage.setAttribute('aria-modal','true');stage.setAttribute('aria-label','窗边番茄钟专注模式');
  }
  active=true;panel.hidden=false;stage.dataset.focus='entering';
  safeWrite(VIEW_KEY,'true');
  transition={from:progress,to:1,start:performance.now(),duration:allowMotion()?1100*(1-progress):0};
  tick(performance.now());wake();$('#focus-exit').focus({preventScroll:true});
 }
 function exit(){
  if(!active)return;
  active=false;safeWrite(VIEW_KEY,'false');
  if(placeholder){const r=placeholder.getBoundingClientRect();originRect={x:r.x,y:r.y,width:r.width,height:r.height};}
  stage.dataset.focus='leaving';
  transition={from:progress,to:0,start:performance.now(),duration:allowMotion()?850*progress:0};
  tick(performance.now());wake();
 }
 $('#focus-enter').addEventListener('click',enter);$('#focus-exit').addEventListener('click',exit);
 $('#timer-toggle').addEventListener('click',()=>{timer=toggleTimer(timer,Date.now());save();updateTimer();});
 $('#timer-reset').addEventListener('click',()=>{timer={...freshTimer(timer.durationMs),task:timer.task};save();updateTimer();});
 $('#timer-duration').addEventListener('change',event=>{
  const minutes=Number(event.target.value);
  if(![1,5,15,25,45].includes(minutes)||['running','paused'].includes(timer.status))return;
  timer={...freshTimer(minutes*60000),task:timer.task};save();updateTimer();
 });
 task.addEventListener('input',()=>{timer={...timer,task:task.value.slice(0,160)};save();});
 const onKey=event=>{
  if(!stage.classList.contains('is-focused'))return;
  if(event.key==='Escape'){event.preventDefault();exit();return;}
  if(event.key==='Tab'){
   const all=[...panel.querySelectorAll('button,input,select')].filter(el=>!el.disabled&&el.getClientRects().length);
   const first=all[0],last=all.at(-1);
   if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
   else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
 };
 const onResize=()=>{if(stage.classList.contains('is-focused')){place();wake();}};
 const onStorage=event=>{if(event.key===KEY){timer=restoreTimer(event.newValue,Date.now());task.value=timer.task;$('#timer-duration').value=String(timer.durationMs/60000);updateTimer();}};
 const onVisibility=()=>{if(!document.hidden)updateTimer();};
 document.addEventListener('keydown',onKey);window.addEventListener('resize',onResize);window.addEventListener('storage',onStorage);document.addEventListener('visibilitychange',onVisibility);
 const interval=setInterval(()=>{if(!document.hidden)updateTimer();},500);updateTimer();
 return {
  tick,enter,exit,isActive:()=>active||!!transition,isTransitioning:()=>!!transition,
  settle:()=>{if(transition){progress=transition.to;place();end();}},
  restoreView:()=>{if(safeRead(VIEW_KEY)==='true')enter();},
  dispose:()=>{clearInterval(interval);document.removeEventListener('keydown',onKey);window.removeEventListener('resize',onResize);window.removeEventListener('storage',onStorage);document.removeEventListener('visibilitychange',onVisibility);}
 };
}
