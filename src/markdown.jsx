import React,{lazy,Suspense,Component} from 'react';
const MarkdownContent=lazy(()=>import('./MarkdownContent'));
class MarkdownBoundary extends Component{
 state={failed:false};static getDerivedStateFromError(){return{failed:true}}
 render(){return this.state.failed?<div><p className="hint" role="status">阅读排版组件更新了，正文和草稿仍保留。<button className="link" onClick={()=>location.reload()}>刷新以载入新版</button></p><div className="markdown-body" style={{whiteSpace:'pre-wrap'}}>{String(this.props.text)}</div></div>:this.props.children}
}
export function Markdown({children=''}){return <MarkdownBoundary text={children}><Suspense fallback={<div className="markdown-body" style={{whiteSpace:'pre-wrap'}}>{String(children)}</div>}><MarkdownContent>{children}</MarkdownContent></Suspense></MarkdownBoundary>}
