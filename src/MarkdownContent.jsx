import React,{useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

function CodeBlock({children}){
 const [copied,setCopied]=useState(false),[error,setError]=useState('');
 const child=React.Children.toArray(children)[0];
 const language=/language-([\w+-]+)/.exec(child?.props?.className||'')?.[1]||'代码';
 const value=String(child?.props?.children||'');
 return <section className="lesson-code"><div className="code-caption"><span>{language}</span><button type="button" onClick={async()=>{try{await navigator.clipboard.writeText(value);setCopied(true);setError('')}catch{setError('请选中代码复制')}}}>{copied?'已复制':'复制代码'}</button></div><pre tabIndex={0} aria-label={language+' 代码'}>{children}</pre>{error&&<small role="status">{error}</small>}</section>;
}

export default function Markdown({children=''}) {
 return <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[[rehypeKatex,{trust:false,strict:'ignore'}]]} skipHtml components={{pre:CodeBlock,a:({children,node,...props})=>props.href?.startsWith('#notes?entry=')?<a {...props}>{children} ↗</a>:<a {...props} target="_blank" rel="noopener noreferrer">{children} ↗</a>,img:({alt,src})=>/^\/api\/entries\/[a-zA-Z0-9_-]+\/attachments\/[a-zA-Z0-9_-]+$/.test(src||'')?<img src={src} alt={alt||'笔记附件'} loading="lazy" className="note-inline-image"/>:<span className="muted">[图片：{alt||'外部图片，未自动加载'}]</span>}}>{String(children)}</ReactMarkdown></div>;
}
