import json
from contextlib import contextmanager
import httpx,pytest
from server import models,db

@pytest.mark.parametrize('status,code,calls',[(401,'authentication',1),(403,'authentication',1),(429,'quota',3),(404,'model_or_request',1),(503,'upstream',3)])
def test_error_classification_no_secret_logs(monkeypatch,status,code,calls):
    count=[]
    class Client:
        def post(self,*args,**kwargs):
            count.append(1);return httpx.Response(status,json={'error':'secret-test-key'})
    @contextmanager
    def transport(*a,**k):yield Client()
    monkeypatch.setattr(models.transport,'client',transport)
    monkeypatch.setattr(models,'key',lambda *a:'secret-test-key')
    monkeypatch.setattr(models.time,'sleep',lambda *a:None)
    with pytest.raises(models.ModelError) as e:models.generate('system','user')
    assert e.value.code==code and len(count)==calls
    assert 'secret-test-key' not in json.dumps(db.all('model_call'))

def test_timeout_classification(monkeypatch):
    class Client:
        def post(self,*a,**k):raise httpx.ReadTimeout('private details')
    @contextmanager
    def transport(*a,**k):yield Client()
    monkeypatch.setattr(models.transport,'client',transport);monkeypatch.setattr(models,'key',lambda *a:'test');monkeypatch.setattr(models.time,'sleep',lambda *a:None)
    with pytest.raises(models.ModelError,match='超时'):models.generate('a','b')

def test_public_profile_never_returns_key(client,monkeypatch,tmp_path):
    monkeypatch.setattr(db,'ROOT',tmp_path)
    (tmp_path/'.env').write_text('LEARNLOOP_LLM_API_KEY=hidden-private-key\n')
    profiles=client.get('/api/model-profiles').json()
    assert profiles[0]['has_key'] and 'hidden-private-key' not in json.dumps(profiles)
    result=client.put('/api/model-profiles/default',json={'provider':'gemini','endpoint':models.DEFAULT['endpoint'],'model':'test-model','timeout':60,'api_key':'replacement-secret'})
    assert result.status_code==200 and 'replacement-secret' not in result.text
    assert (tmp_path/'.env').stat().st_mode & 0o777==0o600
    assert 'replacement-secret' not in json.dumps(db.all('profile'))

def test_invalid_json_and_reference_failure_retry(monkeypatch):
    from server.learning import Answer
    count=[]
    monkeypatch.setattr(models,'generate',lambda *a,**k:count.append(1) or 'not json')
    with pytest.raises(models.ModelError,match='结构'):models.structured('system',{},Answer)
    assert len(count)==2

def test_provider_specific_parameters(monkeypatch):
    bodies=[]
    class Client:
        def post(self,*a,**kw):
            bodies.append(kw['json'])
            return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}],'usage':{'total_tokens':1}})
    @contextmanager
    def transport(*a,**k):yield Client()
    monkeypatch.setattr(models.transport,'client',transport)
    monkeypatch.setattr(models,'key',lambda *a:'test-key')
    for provider in ('gemini','deepseek','custom'):
        monkeypatch.setattr(models,'profile',lambda *a,p=provider:{'provider':p,'model':'test','endpoint':'https://example.com','timeout':5})
        models.generate('JSON','{}',structured=True)
    assert 'thinking' not in bodies[0] and bodies[0]['response_format']['type']=='json_object'
    assert bodies[1]['thinking']=={'type':'disabled'}
    assert 'response_format' not in bodies[2]

@pytest.mark.parametrize('mode,effort,budget',[('fast',None,4096),('standard','low',16384),('deep','high',16384),('max','max',32768)])
def test_reasoning_budget_exact(monkeypatch,mode,effort,budget):
    bodies=[]
    class Client:
        def post(self,*a,**kw):
            bodies.append(kw['json']);return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]})
    @contextmanager
    def transport(*a,**k):yield Client()
    monkeypatch.setattr(models.transport,'client',transport);monkeypatch.setattr(models,'key',lambda *a:'test')
    with models.options(reasoning=mode):models.generate('system','text')
    assert bodies[0]['max_tokens']==budget and bodies[0].get('reasoning_effort')==effort
    assert bodies[0]['thinking']['type']==('disabled' if mode=='fast' else 'enabled')


def test_network_and_structure_share_three_requests(monkeypatch):
    from server.learning import Answer
    calls=[]
    class Client:
        def post(self,*a,**kw):
            calls.append(1)
            if len(calls)==1:return httpx.Response(503,json={})
            return httpx.Response(200,json={'choices':[{'message':{'content':'invalid'}}]})
    @contextmanager
    def transport(*a,**k):yield Client()
    monkeypatch.setattr(models.transport,'client',transport);monkeypatch.setattr(models,'key',lambda *a:'test');monkeypatch.setattr(models.time,'sleep',lambda *a:None)
    with pytest.raises(models.ModelError):models.structured('system',{},Answer)
    assert len(calls)==3


def test_domain_switch_requires_new_key(monkeypatch,tmp_path):
    monkeypatch.setattr(db,'ROOT',tmp_path);(tmp_path/'.env').write_text('LEARNLOOP_LLM_API_KEY=secret\n')
    with pytest.raises(ValueError,match='新密钥'):models.save({'endpoint':'https://other.example/chat/completions'})


def test_truncation_not_success(monkeypatch):
    class Client:
        def post(self,*a,**kw):return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'partial'}}]})
    @contextmanager
    def transport(*a,**k):yield Client()
    monkeypatch.setattr(models.transport,'client',transport);monkeypatch.setattr(models,'key',lambda *a:'test')
    with pytest.raises(models.ModelError) as e:models.generate('a','b')
    assert e.value.code=='truncated' and db.all('model_call')[0]['status']=='failed'

@pytest.mark.parametrize('status,payload,content_type,expected',[
    (401,{'code':'INVALID_API_KEY','message':'secret-test-key'},'application/json','authentication'),
    (403,'<html>secret-test-key</html>','text/html','gateway_access'),
    (302,'','text/html','endpoint'),
    (422,{'error':{'code':'unsupported_parameter'}},'application/json','model_or_request'),
])
def test_gateway_diagnostics_are_specific_and_redacted(monkeypatch,status,payload,content_type,expected):
    class Client:
        def post(self,*a,**kw):
            return httpx.Response(status,content=json.dumps(payload) if isinstance(payload,dict) else payload,headers={'content-type':content_type,'x-request-id':'trace-123'})
    @contextmanager
    def transport(*a,**k):yield Client()
    monkeypatch.setattr(models.transport,'client',transport);monkeypatch.setattr(models,'key',lambda *a:'secret-test-key')
    with pytest.raises(models.ModelError) as exc:models.test_connection()
    assert exc.value.code==expected
    assert exc.value.diagnostics['http_status']==status
    assert exc.value.diagnostics['request_id']=='trace-123'
    assert 'secret-test-key' not in str(exc.value)+json.dumps(db.all('model_call')+db.all('model_test'))
    if status==401:
        assert 'INVALID_API_KEY' in str(exc.value)
        assert db.all('model_test')[0]['diagnostics']['upstream_code']=='INVALID_API_KEY'

def test_gateway_does_not_echo_unknown_error_codes_or_secret_ids():
    response=httpx.Response(401,json={'error':{'code':'Bearer secret-key','message':'secret-key'}},headers={'x-request-id':'secret-key'})
    error=models.response_error(response,'secret-key')
    assert 'upstream_code' not in error.diagnostics and 'request_id' not in error.diagnostics
    assert 'secret-key' not in str(error)+json.dumps(error.diagnostics)

def test_effective_config_source_and_env_precedence(monkeypatch,tmp_path):
    monkeypatch.setattr(db,'ROOT',tmp_path)
    (tmp_path/'.env').write_text('LEARNLOOP_LLM_API_KEY=disk-secret\nLEARNLOOP_LLM_BASE_URL=https://example.com/v1/chat/completions\n')
    p=models.public_profile();assert p['key_source']=='project_env'
    assert p['field_sources']['endpoint']=='project_env' and models.key()=='disk-secret'
    monkeypatch.setenv('LEARNLOOP_LLM_API_KEY','process-secret')
    p=models.public_profile();assert p['key_source']=='process_environment'
    assert models.key()=='process-secret' and 'process-secret' not in json.dumps(p)

def test_structure_repair_reports_field_errors_without_echoing_output(monkeypatch):
    from server.learning import Answer
    prompts=[]
    def generate(system,*a,**kw):
        prompts.append(system)
        return json.dumps({'answer':['private-output'],'citation_ids':[]}) if len(prompts)==1 else json.dumps({'answer':'已修正','citation_ids':[],'insufficient':True})
    monkeypatch.setattr(models,'generate',generate)
    result=models.structured('依据资料回答',{},Answer)
    assert result['answer']=='已修正' and len(prompts)==2
    assert 'string_type' in prompts[1] and 'answer' in prompts[1]
    assert 'private-output' not in prompts[1]
