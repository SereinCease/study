import hashlib
import http.client
import io
import ipaddress
import json
import re
import socket
import ssl
import stat
import threading
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from pypdf import PdfReader
import jieba
from . import db, jobs

MAX_BYTES=20*1024*1024
MAX_EXPANDED=50*1024*1024
EXTENSIONS={'.md','.markdown','.txt','.py','.pdf','.html','.htm','.toml','.ini','.cfg','.json','.yaml','.yml'}
EXCLUDED={'.git','node_modules','.venv','venv','__pycache__','dist','build','.ssh'}
MODEL='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
_encoder=None
_encoder_lock=threading.Lock()

def tokens(text):
    return ' '.join(w.lower() for w in jieba.cut(text) if re.search(r'\w',w))

def filename_allowed(name):
    p=PurePosixPath(name)
    return not (set(p.parts)&EXCLUDED) and not any(x.startswith('.env') or x.lower().endswith(('.pem','.key')) or x.lower() in ('credentials.json','secrets.json','.secrets.json') for x in p.parts)

def public_address(url):
    u=urlparse(url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
        raise ValueError('只接受公开 HTTP(S) 网页')
    port=u.port or (443 if u.scheme=='https' else 80)
    if port not in (80,443):
        raise ValueError('网页导入仅支持 80/443 端口')
    try: addresses=socket.getaddrinfo(u.hostname,port,type=socket.SOCK_STREAM)
    except OSError: raise ValueError('无法解析网页域名')
    ips={r[4][0] for r in addresses}
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError('禁止访问本机、内网或保留地址')
    return u, sorted(ips)[0],port

def fetch_web(url):
    for _ in range(5):
        u,ip,port=public_address(url)
        # Connect to the validated address, preserving Host and TLS server_name.
        class PinnedHTTPS(http.client.HTTPSConnection):
            def connect(self):
                sock=socket.create_connection((ip,port),timeout=20)
                self.sock=ssl.create_default_context().wrap_socket(sock,server_hostname=u.hostname)
        con=PinnedHTTPS(u.hostname,port,timeout=20) if u.scheme=='https' else http.client.HTTPConnection(ip,port,timeout=20)
        try:
            con.request('GET',(u.path or '/')+('?' + u.query if u.query else ''),headers={'Host':u.hostname,'User-Agent':'Learnloop/1.0','Accept-Encoding':'identity'})
            response=con.getresponse()
            if response.status in (301,302,303,307,308):
                url=urljoin(url,response.getheader('Location',''))
                continue
            if response.status!=200:
                raise ValueError(f'网页返回 HTTP {response.status}')
            data=response.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:
                raise ValueError('网页超过 20MB 限制')
            return data,url
        except (OSError,http.client.HTTPException):
            raise ValueError('无法抓取网页，请检查地址与网络连接')
        finally:
            con.close()
    raise ValueError('网页重定向过多')

def documents(name, data):
    suffix=Path(name).suffix.lower()
    if suffix=='.zip':
        total=0
        entries=[]
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist())>500:
                raise ValueError('压缩包最多包含 500 个条目')
            for info in archive.infolist():
                p=PurePosixPath(info.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in info.filename or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError('压缩包含不安全路径或符号链接')
                total+=info.file_size
                if total>MAX_EXPANDED:
                    raise ValueError('解压后内容超过 50MB')
                if info.is_dir() or not filename_allowed(info.filename) or p.suffix.lower() not in EXTENSIONS:
                    continue
                raw=archive.read(info)
                if len(raw)>MAX_BYTES:
                    raise ValueError('单个文件超过 20MB')
                entries.extend(documents(info.filename,raw))
        if not entries:
            raise ValueError('项目内没有可解析的文本文件')
        return entries
    if suffix not in EXTENSIONS:
        raise ValueError('不支持的文件格式')
    if suffix=='.pdf':
        try:
            reader=PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ValueError('加密 PDF 暂不支持，请先解密')
            if len(reader.pages)>500:
                raise ValueError('PDF 超过 500 页限制')
            pages=[(name,p.extract_text() or '',{'page':i+1}) for i,p in enumerate(reader.pages)]
        except ValueError:
            raise
        except Exception:
            raise ValueError('PDF 损坏或无法解析')
        if not any(t.strip() for _,t,_ in pages):
            raise ValueError('PDF 无可提取文本，扫描件需要 OCR')
        return pages
    try:
        text=data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('文本必须为 UTF-8 编码')
    if '\x00' in text:
        raise ValueError('不支持二进制文件')
    if suffix in ('.html','.htm'):
        soup=BeautifulSoup(text,'html.parser')
        for el in soup(['script','style','nav','footer','noscript']):
            el.decompose()
        for heading in soup.find_all(re.compile('^h[1-6]$')):
            heading.replace_with('\n'+'#'*int(heading.name[1])+' '+heading.get_text(' ',strip=True)+'\n')
        for link in soup.find_all('a',href=True):
            link.replace_with(link.get_text(' ',strip=True)+' ('+link['href']+')')
        for row in soup.find_all('tr'):
            row.replace_with(' | '.join(cell.get_text(' ',strip=True) for cell in row.find_all(['td','th']))+'\n')
        text=soup.get_text('\n',strip=True)
    return [(name,text,{})]

def split_document(name,text,location):
    chunks=[]
    lines=text.splitlines()
    group=[]
    group_start=0
    section=''
    fenced=False
    def flush():
        if not group: return
        value='\n'.join(group)
        for offset in range(0,len(value),1800):
            piece=value[offset:offset+1800]
            if not piece.strip(): continue
            prefix=value[:offset]
            start_line=group_start+1+prefix.count('\n')
            chunks.append({'text':piece,'path':name,'location':dict(location,section=section,line_start=start_line,line_end=start_line+piece.count('\n'),character_offset=offset,column_start=len(prefix.rsplit('\n',1)[-1]))})
    for i,line in enumerate(lines):
        heading=re.match(r'^(#{1,6})\s+(.+)',line) if not fenced else None
        code_heading=re.match(r'^(?:async )?(?:def|class) (\w+)',line) if name.endswith('.py') else None
        if (heading or code_heading) and group:
            flush();group=[]
        if group and sum(len(x)+1 for x in group)+len(line)>1800:
            flush();group=[]
        if not group: group_start=i
        if heading:section=heading.group(2)
        elif code_heading:section=code_heading.group(1)
        group.append(line)
        if line.lstrip().startswith(('```','~~~')):fenced=not fenced
    flush()
    return chunks

def encoder():
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                snapshots=db.DATA/'models'/('models--'+MODEL.replace('/','--'))/'snapshots'
                if not any(p.is_file() for pattern in ('*/model.safetensors','*/pytorch_model.bin') for p in snapshots.glob(pattern)):
                    raise RuntimeError('本地模型尚未下载完整')
                from sentence_transformers import SentenceTransformer
                import torch
                torch.set_num_threads(2)
                _encoder=SentenceTransformer(MODEL,cache_folder=str(db.DATA/'models'),local_files_only=True,device='cpu')
    return _encoder

def vector(text):
    import numpy as np
    model=encoder()
    ids=model.tokenizer.encode(text,add_special_tokens=False)
    width=max(16,model.max_seq_length-4)
    pieces=[model.tokenizer.decode(ids[i:i+width]) for i in range(0,len(ids),width)] or [' ']
    arr=model.encode(pieces,normalize_embeddings=True).mean(axis=0)
    arr=arr/(np.linalg.norm(arr) or 1)
    return arr.tolist()

def process(job):
    source=db.get(job['payload']['source_id'],'source')
    if source.get('status')=='ready':
        return source
    db.update(source['id'],{'status':'parsing','error':None})
    try:
        raw=(db.DATA/'originals'/source['id']).read_bytes()
        chunks=[]
        for name,text,location in documents(source['name'],raw):
            chunks.extend(split_document(name,text,dict(location,url=source.get('url'),fetched_at=source.get('fetched_at'))))
        if sum(len(x['text']) for x in chunks)>10*1024*1024:
            raise ValueError('解析文本超过 10MB，请拆分资料')
        if not chunks:
            raise ValueError('资料为空，没有可学习的内容')
        jobs.step(job['id'],25)
        with db.connect() as c:
            existing=[x for x in db.all('chunk',c) if x['source_id']==source['id']]
            if not existing:
                for item in chunks:
                    chunk=db.put('chunk',dict(item,source_id=source['id']),con=c)
                    db.link(chunk['id'],source['id'],'source',c)
                    c.execute('INSERT INTO chunks_fts(id,tokens) VALUES(?,?)',(chunk['id'],tokens(source['name']+' '+chunk['text'])))
        db.update(source['id'],{'status':'indexing'})
        embedded=0
        warning=None
        for index,chunk in enumerate([x for x in db.all('chunk') if x['source_id']==source['id']]):
            jobs.step(job['id'],30+int(60*index/max(1,len(chunks))))
            try:
                v=vector(chunk['text'])
                with db.connect() as c:
                    c.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?)',(chunk['id'],MODEL,__import__('array').array('f',v).tobytes()))
                embedded+=1
            except Exception:
                warning='本地向量模型未准备好，当前使用全文检索；运行模型下载命令后可重建索引'
                break
        return db.update(source['id'],{'status':'ready','chunk_count':len(chunks),'vector_count':embedded,'warning':warning})
    except Exception as e:
        db.update(source['id'],{'status':'failed','error':str(e) if isinstance(e,ValueError) else '资料处理失败，可重试'})
        raise

def import_bytes(name,data,url=None,space_id=None):
    from . import spaces
    sid=spaces.resolve(space_id or "automation")["id"]
    if len(data)>MAX_BYTES or not data:
        raise ValueError('文件为空或超过 20MB')
    if not filename_allowed(name):
        raise ValueError('不导入密钥、配置秘密或依赖目录')
    digest=hashlib.sha256(data).hexdigest()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        allowed=spaces.source_ids(sid,c)
        previous=[s for s in db.all('source',c) if s.get('name')==name and s['id'] in allowed]
        duplicate=next((s for s in db.all('source',c) if s.get('hash')==digest),None)
        if duplicate:
            spaces.attach_source(sid,duplicate["id"],name,url,c)
            return duplicate
        source=db.put('source',{'space_id':sid,'name':name,'hash':digest,'url':url,'version':len(previous)+1,'status':'uploaded','chunk_count':0,'fetched_at':db.now() if url else None},con=c)
        spaces.attach_source(sid,source["id"],name,url,c)
        if previous:
            old_ids={s['id'] for s in previous}
            for concept in db.all('concept',c):
                if old_ids.intersection(concept.get('source_ids',[])):
                    db.update(concept['id'],{'needs_review':True},con=c)
    folder=db.DATA/'originals'
    folder.mkdir(exist_ok=True,mode=0o700)
    path=folder/source['id']
    path.write_bytes(data)
    path.chmod(0o600)
    job=jobs.enqueue('import',{'source_id':source['id']},f'import:{source["id"]}')
    return db.update(source['id'],{'job_id':job['id']})

def search(q,limit=6,space_id=None):
    from . import spaces
    allowed=spaces.source_ids(space_id) if space_id else None
    if allowed is not None and not allowed:return []
    if not q.strip():
        return []
    words=tokens(q).split()[:30]
    expr=' OR '.join('"'+w.replace('"','""')+'"' for w in words)
    rank={}
    with db.connect() as c:
        clause=" AND json_extract(e.data,'$.source_id') IN ("+','.join('?'*len(allowed))+')' if allowed is not None else ''
        args=list(allowed or [])
        if expr:
            rows=c.execute('SELECT chunks_fts.id FROM chunks_fts JOIN entities e ON e.id=chunks_fts.id WHERE chunks_fts MATCH ?'+clause+' ORDER BY rank LIMIT 30',[expr,*args]).fetchall()
            for i,r in enumerate(rows):
                rank[r[0]]=1/(60+i)
        try:
            import numpy as np,heapq
            v=np.asarray(vector(q),dtype=np.float32);scored=[]
            cursor=c.execute('SELECT v.id,v.vector FROM vectors v JOIN entities e ON e.id=v.id WHERE v.model=?'+clause,[MODEL,*args])
            while batch:=cursor.fetchmany(256):
                # v3 stores float32 blobs; legacy JSON remains readable during migration.
                arr=[np.frombuffer(r[1],dtype=np.float32) if isinstance(r[1],bytes) else np.asarray(json.loads(r[1]),dtype=np.float32) for r in batch]
                for row,score in zip(batch,np.stack(arr)@v):
                    if score>.25:
                        heapq.heappush(scored,(float(score),row[0]))
                        if len(scored)>30:heapq.heappop(scored)
            for i,(_,id) in enumerate(sorted(scored,reverse=True)):
                rank[id]=rank.get(id,0)+1/(60+i)
        except Exception:
            pass
        result=[]
        for id in sorted(rank,key=rank.get,reverse=True)[:limit]:
            chunk=db.get(id,'chunk',c)
            chunk['source_name']=db.get(chunk['source_id'],'source',c)['name']
            chunk['score']=rank[id]
            result.append(chunk)
        return result
