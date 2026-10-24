import React,{useEffect,useRef,useState} from 'react';
import './focus.css';

/** Reuses the approved autumn assets; mobile stays still until explicitly enabled. */
export function AutumnScene({preferences={},focus=false,className='',onReady}){
 const host=useRef(null),runtime=useRef(null),readyCallback=useRef(onReady);
 const [mobile,setMobile]=useState(()=>matchMedia('(max-width:767px)').matches);
 const [reduced,setReduced]=useState(()=>matchMedia('(prefers-reduced-motion:reduce)').matches);
 const [mobileEnabled,setMobileEnabled]=useState(false),[paused,setPaused]=useState(false),[intensity,setIntensity]=useState('visible'),[status,setStatus]=useState('static');
 readyCallback.current=onReady;
 const enabled=preferences.scene3d!==false&&preferences.motion!==false&&!reduced&&(!mobile||mobileEnabled);
 const options=useRef({});options.current={focus,motion:enabled&&!paused,intensity};
 useEffect(()=>{
  const phone=matchMedia('(max-width:767px)'),quiet=matchMedia('(prefers-reduced-motion:reduce)');
  const update=()=>{setMobile(phone.matches);setReduced(quiet.matches);};
  phone.addEventListener('change',update);quiet.addEventListener('change',update);
  return()=>{phone.removeEventListener('change',update);quiet.removeEventListener('change',update);};
 },[]);
 useEffect(()=>{
  if(!enabled){setStatus('static');return;}
  let cancelled=false;const controller=new AbortController();setStatus('loading');
  import('./autumn-scene-runtime').then(async({mountAutumnScene})=>{
   if(cancelled)return;
   const control=await mountAutumnScene(host.current,{...options.current,signal:controller.signal,onState:next=>{if(!cancelled){setStatus(next);if(next==='ready')readyCallback.current?.();}}});
   if(cancelled)control.dispose();else runtime.current=control;
  }).catch(()=>{if(!cancelled)setStatus('fallback');});
  return()=>{cancelled=true;controller.abort();runtime.current?.dispose();runtime.current=null;};
 },[enabled]);
 useEffect(()=>{runtime.current?.set(options.current);},[focus,paused,intensity,enabled]);
 return <div className={'autumn-scene '+(focus?'autumn-focus ':'')+className} data-scene-status={status} data-motion={enabled&&!paused&&status==='ready'?'on':'off'}>
  <img className="autumn-still" src="/autumn/autumn-master.png" alt="秋夜书斋，窗边女子安坐读卷，案头星图与窗外月色相映"/>
  <div className="autumn-webgl" ref={host} style={{visibility:status==='ready'?'visible':'hidden'}}/>
  <div className="autumn-scene-tools">
   <small>{status==='fallback'?'静态书斋 · 动效暂不可用':reduced?'静态书斋 · 遵循减少动态效果':status==='loading'?'正在展开秋夜书斋…':enabled&&status==='ready'?(paused?'动效已暂停':'呼吸 · 衣袖 · 落叶与星光'):'秋夜书斋 · 静态伴读'}</small>
   {enabled&&status==='ready'&&<><button type="button" onClick={()=>setPaused(x=>!x)} aria-pressed={paused}>{paused?'恢复动效':'暂停动效'}</button><select aria-label="场景动效强度" value={intensity} onChange={e=>setIntensity(e.target.value)}><option value="visible">清晰</option><option value="gentle">轻柔</option></select></>}
   {mobile&&!mobileEnabled&&!reduced&&preferences.motion!==false&&preferences.scene3d!==false&&<button type="button" onClick={()=>setMobileEnabled(true)}>启用场景动效</button>}
  </div>
 </div>;
}
export default AutumnScene;
