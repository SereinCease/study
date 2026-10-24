import React,{createContext,useContext,useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {MoreHorizontal,NotebookPen,X,ArrowUpRight} from 'lucide-react';
import {api,requestId} from './api';
import {Notes} from './notes';
import {Button} from './components';
import './card-notes.css';

const CardNotes=createContext(null);
const read=key=>{try{return JSON.parse(localStorage.getItem(key)||'null')}catch{return null}};

export function CardNoteSurface({as:Tag='article',kind,card,children,menu=true,onKeyDown,className='',...props}){
 const note=useCardNote(kind,card);
 return <Tag {...props} className={className+(menu?' card-note-surface':'')} onContextMenu={note.onContextMenu} onKeyDown={e=>{note.onKeyDown(e);if(!e.defaultPrevented)onKeyDown?.(e)}}>{menu&&note.menu}{children}</Tag>;
}

// Cards keep native selection/editing context menus inside text inputs and selected text.
export function useCardNote(kind,card){
 const ctx=useContext(CardNotes);
 const show=(e,fromButton=false)=>{
  if(!ctx||!card)return;
  if(!fromButton&&(e.target.closest?.('input,textarea,[contenteditable="true"]')||window.getSelection()?.toString()))return;
  e.preventDefault();e.stopPropagation();
  const rect=e.currentTarget.getBoundingClientRect();
  ctx.show({kind,id:card.id,title:card.title||card.name},
   fromButton||!e.clientX&&!e.clientY?{x:rect.left,y:rect.bottom}:{x:e.clientX,y:e.clientY},e.currentTarget);
 };
 return {onContextMenu:show,onKeyDown:e=>{if(e.key==='ContextMenu'||e.shiftKey&&e.key==='F10')show(e,true)},
  menu:<button type="button" className="icon card-note-trigger" aria-label={'更多操作：'+(card?.title||card?.name||'知识卡')} aria-haspopup="menu" aria-expanded={ctx?.menuId===card?.id} onClick={e=>show(e,true)}><MoreHorizontal size={20}/></button>};
}

export function CardNotesProvider({spaceId,spaceTitle,open,view,go,onEntry,children}){
 const storageKey='learnloop-card-note-panel-'+spaceId;
 const [panel,setPanel]=useState(()=>read(storageKey)),[menu,setMenu]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const dialog=useRef(null),editor=useRef(null),menuRef=useRef(null),origin=useRef(null),working=useRef(false);
 function remember(value){localStorage.setItem(storageKey,JSON.stringify(value));setPanel(value)}
 function dismissMenu(){setMenu(null);origin.current?.focus?.()}
 useEffect(()=>{
  if(!menu)return;
  menuRef.current?.querySelector('button')?.focus();
  const close=e=>{if(!menuRef.current?.contains(e.target))dismissMenu()};
  const escape=e=>{if(['Escape','Tab'].includes(e.key)){e.preventDefault();dismissMenu()}};
  window.addEventListener('pointerdown',close);window.addEventListener('keydown',escape);
  window.addEventListener('resize',dismissMenu);window.addEventListener('scroll',dismissMenu,true);
  return()=>{window.removeEventListener('pointerdown',close);window.removeEventListener('keydown',escape);window.removeEventListener('resize',dismissMenu);window.removeEventListener('scroll',dismissMenu,true)};
 },[menu]);
 useEffect(()=>{
  if(!panel)return;
  const previous=document.activeElement;
  dialog.current?.showModal();
  return()=>previous?.isConnected&&previous.focus?.();
 },[!!panel]);
 async function prepare(value){
  if(working.current)return;working.current=true;setBusy(true);setError('');
  try{
   const entry=await api('/entries/from-card',{space_id:spaceId,card_kind:value.kind,card_id:value.id,request_id:value.requestId});
   remember({...value,entryId:entry.id,warnings:entry.capture_warnings||[]});
  }catch(e){setError('未能创建关联笔记：'+e.message+'。可以重试，不会重复创建。')}
  finally{working.current=false;setBusy(false)}
 }
 async function begin(){
  const value={...menu.card,requestId:requestId()};
  try{remember(value);setMenu(null);await prepare(value)}catch(e){setError('无法保存恢复位置：'+e.message)}
 }
 async function close(expand=false){
  if(working.current)return;
  if(panel?.entryId&&!(await editor.current?.flush())){setError('尚未同步，请重试保存或处理版本冲突；也可保留本机草稿后关闭。');return}
  const id=panel?.entryId;remember(null);setError('');origin.current?.focus?.();
  if(expand&&id){onEntry(id);go('notes?entry='+encodeURIComponent(id))}
 }
 return <CardNotes.Provider value={{menuId:menu?.card.id,show:(card,position,element)=>{origin.current=element;setMenu({card,position})}}}>{children}
 {menu&&createPortal(<div ref={menuRef} role="menu" aria-label="卡片操作" className="card-note-menu" style={{left:Math.max(8,Math.min(menu.position.x,window.innerWidth-232)),top:Math.max(8,Math.min(menu.position.y,window.innerHeight-92))}} onKeyDown={e=>{if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();menuRef.current.querySelector('button')?.focus()}}}><small>{menu.card.title}</small><button role="menuitem" onClick={begin}><NotebookPen size={17}/>添加关联笔记</button></div>,document.body)}
 {panel&&createPortal(<dialog ref={dialog} className="card-note-drawer" aria-labelledby="card-note-heading" onCancel={e=>{e.preventDefault();close()}}>
  <header><div><span className="eyebrow">随手记 / 保留理解</span><h2 id="card-note-heading">关联笔记</h2></div><div className="actions">{panel.entryId&&<button className="icon" aria-label="在主笔记页展开" onClick={()=>close(true)}><ArrowUpRight size={20}/></button>}<button className="icon" aria-label="关闭关联笔记" disabled={busy} onClick={()=>close()}><X size={20}/></button></div></header>
  <div className="card-note-context"><span>{spaceTitle||'当前空间'}</span><strong>{panel.kind==='source'?'资料':'知识点'} · {panel.title}</strong><small>{panel.kind==='source'?'已关联资料来源；不自动推断知识点。':'关联当前知识点及其可用的原始来源。'}笔记不会增加掌握度。</small>{panel.warnings?.map((w,i)=><p className="hint" key={i}>{w}</p>)}</div>
  {error&&<div className="alert error" role="alert">{error}{panel.entryId&&<button className="link" onClick={()=>{remember(null);setError('');origin.current?.focus?.()}}>保留本机草稿并关闭</button>}</div>}
  {panel.entryId?<Notes key={panel.entryId} compact editorOnly editorRef={editor} onSaved={()=>setError('')} spaceId={spaceId} entryId={panel.entryId} onEntry={id=>remember({...panel,entryId:id})} open={open} view={view}/>:<div className="card-note-pending"><p role="status">{busy?'正在关联卡片来源…':'创建尚未完成，可以继续。'}</p><Button busy={busy} onClick={()=>prepare(panel)}>继续创建</Button></div>}
 </dialog>,document.body)}
 </CardNotes.Provider>
}
