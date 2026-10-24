import io,json,socket,stat,zipfile
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject,DictionaryObject
from server import db,ingest,jobs

def pdf(text=None,encrypt=False):
    w=PdfWriter();page=w.add_blank_page(300,300)
    if text:
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject();stream.set_data(f'BT /F1 12 Tf 10 100 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')]=w._add_object(stream)
    if encrypt:w.encrypt('secret')
    out=io.BytesIO();w.write(out);return out.getvalue()

def test_sources_real_chunks_search_and_version(client):
    s=client.post('/api/sources',json={'name':'notes.md','content':'# fixture\n夹具隔离测试数据。\n```python\nassert 1 == 1\n```'}).json()
    assert s['status']=='uploaded'
    assert jobs.tick()
    stored=db.get(s['id']);assert stored['status']=='ready' and stored['warning']
    chunks=client.get('/api/sources/'+s['id']+'/chunks').json()
    assert chunks[0]['location']['line_start']==1
    result=client.get('/api/search',params={'q':'测试数据'}).json();assert result[0]['id']==chunks[0]['id']
    assert client.get('/api/search',params={'q':'" OR - * ( )'}).status_code==200
    again=client.post('/api/sources',json={'name':'notes.md','content':'# fixture\n夹具隔离测试数据。\n```python\nassert 1 == 1\n```'}).json()
    assert again['id']==s['id']
    concept=db.put('concept',{'title':'fixture','source_ids':[s['id']]})
    update=client.post('/api/sources',json={'name':'notes.md','content':'更新后的资料'}).json()
    assert update['version']==2 and db.get(concept['id'])['needs_review']

def test_pdf_page_location_and_bad_files(client):
    p=ingest.documents('tutorial.pdf',pdf('pytest fixture'))
    assert p[0][2]['page']==1 and 'fixture' in p[0][1]
    for raw,reason in [(pdf(),'OCR'),(pdf('text',True),'加密'),(b'broken','损坏')]:
        with pytest.raises(ValueError,match=reason):ingest.documents('x.pdf',raw)
    r=client.post('/api/sources/import',files={'file':('bad.pdf',b'broken')}).json()
    jobs.tick();assert db.get(r['id'])['status']=='failed'
    assert db.get(r['job_id'])['status']=='failed'
    assert client.post('/api/sources/import',files={'file':('empty.md',b'')}).status_code==400

def test_html_and_code_preserve_source():
    docs=ingest.documents('page.html',b'<h1>Guide</h1><h2>Fixture</h2><script>secret()</script><p>isolation</p>')
    assert 'secret' not in docs[0][1] and 'Fixture' in docs[0][1]
    chunks=ingest.split_document('tests/test_app.py','import pytest\n\ndef test_x():\n    assert True',{})
    assert chunks[0]['path']=='tests/test_app.py' and chunks[-1]['location']['line_end']==4

def zipdata(entries):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,value in entries:z.writestr(name,value)
    return out.getvalue()

def test_zip_traversal_secret_and_symlink():
    for name in ['../escape.py','/root/file.py','foo\\evil.py']:
        with pytest.raises(ValueError,match='路径'):ingest.documents('repo.zip',zipdata([(name,b'bad')]))
    sym=zipfile.ZipInfo('link.py');sym.external_attr=(stat.S_IFLNK|0o777)<<16
    with pytest.raises(ValueError,match='符号'):ingest.documents('repo.zip',zipdata([(sym,b'/etc/passwd')]))
    doc=ingest.documents('repo.zip',zipdata([('project/test.py',b'assert True'),('.env',b'KEY=secret'),('.secrets.json',b'private-key'),('node_modules/a.py',b'bad')]))
    assert len(doc)==1 and doc[0][0]=='project/test.py'
    entries=[(f'{i}.py',b'x') for i in range(501)]
    with pytest.raises(ValueError,match='500'):ingest.documents('repo.zip',zipdata(entries))

@pytest.mark.parametrize('url',['http://127.0.0.1','http://169.254.169.254','http://[::1]','file:///etc/passwd','http://localhost:8000','http://a:b@example.com'])
def test_ssrf(url):
    with pytest.raises(ValueError):ingest.public_address(url)

def test_dns_mixed_addresses_rejected(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):ingest.public_address('https://example.com')

def test_request_origin_csrf_and_export(client):
    assert client.post('/api/sample',json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/sample',json={},headers={'x-learnloop-token':'wrong'}).status_code==403
    assert client.get('/api/session',headers={'Host':'attacker.com'}).status_code==403
    client.post('/api/sample',json={});jobs.tick()
    export=client.get('/api/export').json()
    assert 'profile' not in export['data'] and 'chunk' in export['data']
    zipped=client.get('/api/export/archive')
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as z:
        assert 'learning.json' in z.namelist()
        assert any(n.startswith('originals/') for n in z.namelist())
        assert '.env' not in z.namelist()

def test_hybrid_search_uses_semantic_candidates(monkeypatch):
    source=db.put('source',{'name':'different.md','status':'ready'})
    a=db.put('chunk',{'source_id':source['id'],'text':'a','path':'different.md','location':{'line_start':1}})
    b=db.put('chunk',{'source_id':source['id'],'text':'b','path':'different.md','location':{'line_start':2}})
    with db.connect() as c:
        c.execute('INSERT INTO vectors VALUES(?,?,?)',(a['id'],ingest.MODEL,json.dumps([1.,0.])))
        c.execute('INSERT INTO vectors VALUES(?,?,?)',(b['id'],ingest.MODEL,json.dumps([0.,1.])))
    monkeypatch.setattr(ingest,'vector',lambda text:[1.,0.])
    result=ingest.search('语义查询')
    assert [r['id'] for r in result]==[a['id']]

def test_markdown_heading_and_long_line_locations():
    chunks=ingest.split_document('doc.md','# 一\n定义\n# 二\n'+('x'*4000),{})
    assert chunks[0]['location']['section']=='一'
    assert chunks[-1]['location']['section']=='二'
    assert chunks[-1]['location']['line_start']==4
    assert ''.join(c['text'] for c in chunks if c['text'].startswith('x'))=='x'*4000
