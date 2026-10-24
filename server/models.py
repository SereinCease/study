import json
import os
import re
import threading
import time
from pathlib import Path
from contextvars import ContextVar
from contextlib import contextmanager
from urllib.parse import urlparse
import httpx
from pydantic import ValidationError
from dotenv import dotenv_values, set_key
from . import db, transport

KEY_FILE = db.ROOT / '.secrets.json'
LOCK = threading.Lock()
FIELDS = {'provider':'LEARNLOOP_LLM_PROVIDER','endpoint':'LEARNLOOP_LLM_BASE_URL','model':'LEARNLOOP_LLM_MODEL','timeout':'LEARNLOOP_LLM_TIMEOUT_SECONDS','reasoning':'LEARNLOOP_LLM_REASONING','max_output_tokens':'LEARNLOOP_LLM_MAX_OUTPUT_TOKENS'}
DEFAULT = {'provider':'deepseek','endpoint':'https://api.deepseek.com/chat/completions','model':'deepseek-flash','timeout':120,'reasoning':'standard','max_output_tokens':0}
MODES={'fast':4096,'standard':16384,'deep':16384,'max':32768}
_OPTIONS=ContextVar('model_options',default={})
_BUDGET=ContextVar('model_request_budget',default=None)
_LAST_CALL=ContextVar('last_model_call',default=None)

@contextmanager
def options(**values):
    token=_OPTIONS.set(values)
    try:yield
    finally:_OPTIONS.reset(token)

def last_call():return _LAST_CALL.get()

class ModelError(RuntimeError):
    def __init__(self, code, message, diagnostics=None):
        self.code = code
        self.diagnostics = diagnostics or {}
        super().__init__(message)

def response_error(response, secret):
    """Keep useful gateway diagnostics without persisting arbitrary response text."""
    status=response.status_code
    content_type=response.headers.get('content-type','').split(';')[0].lower()
    diagnostics={'http_status':status,'response_type':'json' if 'json' in content_type else 'html' if 'html' in content_type else 'other'}
    upstream=None
    if diagnostics['response_type']=='json' and len(response.content)<=65536:
        try:
            data=response.json()
            if isinstance(data,dict):
                error=data.get('error')
                upstream=error.get('code') or error.get('type') if isinstance(error,dict) else data.get('code')
        except ValueError:pass
    # Error bodies can echo keys, prompts or proxy HTML. Only known codes are retained.
    known={'INVALID_API_KEY','invalid_api_key','authentication_error','permission_denied','insufficient_quota','rate_limit_exceeded','model_not_found','invalid_request_error','unsupported_parameter'}
    if isinstance(upstream,str) and upstream in known and secret not in upstream:
        diagnostics['upstream_code']=upstream
    request_id=response.headers.get('x-request-id','')
    if re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}',request_id) and secret not in request_id and not request_id.lower().startswith(('sk-','bearer')):
        diagnostics['request_id']=request_id
    if 300<=status<400:
        code='endpoint';message='接口返回重定向；请核对完整 Chat Completions 地址，未转发密钥'
    elif status==403 and diagnostics['response_type']!='json':
        code='gateway_access';message='网关或访问策略拒绝请求，返回了非 JSON 页面；请检查网关或代理，不能据此判断密钥无效'
    elif status in (401,403):
        code='authentication'
        message='网关明确拒绝当前 API Key；请在此接口的控制台核对密钥状态并重新保存' if upstream in ('INVALID_API_KEY','invalid_api_key') else '模型认证失败或权限不足；请核对该接口的密钥与访问权限'
    elif status in (402,429):code='quota';message='模型余额、配额不足或请求受限'
    elif status in (400,404,422):code='model_or_request';message='模型不可用或请求参数不受支持'
    else:code='upstream';message='模型服务暂不可用'
    label=f"HTTP {status}"+(f" · {diagnostics['upstream_code']}" if diagnostics.get('upstream_code') else '')
    return ModelError(code,f'{message}（{label}）',diagnostics)

def env_values():
    return {**dotenv_values(db.ROOT / '.env'), **os.environ}

def profile(id='default'):
    values = DEFAULT.copy()
    try:
        values.update(db.get(id,'profile'))
    except ValueError:
        pass
    if id == 'default':
        env = env_values()
        values.update({field:env[name] for field,name in FIELDS.items() if env.get(name)})
    return dict(values,id=id,timeout=int(values['timeout']))

def key(id='default'):
    if id == 'default':
        return env_values().get('LEARNLOOP_LLM_API_KEY','')
    if KEY_FILE.exists():
        return json.loads(KEY_FILE.read_text()).get(id,'')
    return ''

def public_profile(id='default'):
    p = profile(id)
    p['effective_output_budget']=int(p.get('max_output_tokens',0)) or MODES[p['reasoning']]
    p['automatic_retry_budget_ceiling']=32768 if not int(p.get('max_output_tokens',0)) and p['reasoning']!='fast' else p['effective_output_budget']
    p['has_key'] = bool(key(id))
    p['key_mask'] = '••••••••' if p['has_key'] else ''
    p['environment_overrides'] = [f for f,n in FIELDS.items() if n in os.environ] if id=='default' else []
    if id == 'default' and 'LEARNLOOP_LLM_API_KEY' in os.environ:
        p['environment_overrides'].append('api_key')
    p['config_source'] = 'environment/.env' if id == 'default' else 'local profile'
    disk=dotenv_values(db.ROOT / '.env') if id=='default' else {}
    p['field_sources']={f:('process_environment' if n in os.environ else 'project_env' if disk.get(n) else 'local_profile_or_default') for f,n in FIELDS.items()} if id=='default' else {f:'local_profile' for f in FIELDS}
    p['key_source']=('process_environment' if 'LEARNLOOP_LLM_API_KEY' in os.environ else 'project_env' if disk.get('LEARNLOOP_LLM_API_KEY') else 'not_configured') if id=='default' else 'local_secrets'
    tests=[t for t in db.all('model_test') if t['profile_id']==id and t.get('model')==p['model'] and t.get('endpoint')==p['endpoint']]
    p['last_test']=tests[-1] if tests else None
    p['capabilities']={x.get('reasoning','standard'):x for x in tests}
    return p

def save(data, id='default'):
    try: existing=db.get(id)
    except ValueError: existing=None
    if existing and existing['kind']!='profile': raise ValueError('配置 ID 已被占用')
    p = profile(id)
    previous=p.copy()
    for f in FIELDS:
        if f in data:
            p[f] = data[f]
    url = urlparse(p['endpoint'])
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost','127.0.0.1')):
        raise ValueError('模型地址必须使用 HTTPS，本地模型可使用 HTTP')
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('接口地址不得包含密钥、查询参数或凭据')
    if not p['model'] or not 5 <= int(p['timeout']) <= 300:
        raise ValueError('模型名称必填，超时范围 5–300 秒')
    if p['reasoning'] not in MODES: raise ValueError('未知推理档位')
    if not 0<=int(p.get('max_output_tokens',0))<=32768: raise ValueError('生成预算范围为 0–32768，0 表示按档位')
    if (p['provider']!=previous['provider'] or url.hostname!=urlparse(previous['endpoint']).hostname) and key(id) and not data.get('api_key'):
        raise ValueError('供应商或域名改变，需要填写对应的新密钥')
    with LOCK:
        if id == 'default':
            for f,n in FIELDS.items():
                if f in data and n in os.environ:
                    raise ValueError(f'{f} 被启动环境覆盖，请修改启动环境')
            if 'api_key' in data and 'LEARNLOOP_LLM_API_KEY' in os.environ:
                raise ValueError('密钥被启动环境覆盖')
            envpath = db.ROOT / '.env'
            envpath.touch(mode=0o600,exist_ok=True)
            for f,n in FIELDS.items():
                if f in data:
                    set_key(str(envpath),n,str(data[f]))
            if 'api_key' in data:
                set_key(str(envpath),'LEARNLOOP_LLM_API_KEY',data['api_key'])
            envpath.chmod(0o600)
        elif 'api_key' in data:
            secrets = json.loads(KEY_FILE.read_text()) if KEY_FILE.exists() else {}
            secrets[id] = data['api_key']
            tmp = KEY_FILE.with_suffix('.tmp')
            fd = os.open(tmp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as out:
                json.dump(secrets,out)
            tmp.replace(KEY_FILE)
            KEY_FILE.chmod(0o600)
        db.put('profile',{k:v for k,v in p.items() if k != 'api_key'},id=id)
        with db.connect() as c:
            c.execute("DELETE FROM entities WHERE kind='model_test' AND json_extract(data,'$.profile_id')=?",(id,))
    return public_profile(id)

def generate(system, content, profile_id='default', structured=False):
    p = profile(profile_id)
    overrides=_OPTIONS.get()
    p.update({k:v for k,v in overrides.items() if k in ('reasoning','max_output_tokens') and v})
    secret = key(profile_id)
    if not secret:
        raise ModelError('missing_key','尚未配置模型密钥')
    body = {'model':p['model'],'messages':[{'role':'system','content':system},{'role':'user','content':content}], 'max_tokens':int(p.get('max_output_tokens',0)) or MODES.get(p.get('reasoning'),4096)}
    mode=p.get('reasoning','fast')
    if mode not in MODES:raise ModelError('parameters','未知推理档位')
    effort={'standard':'low','deep':'high','max':'max'}.get(mode)
    if p['provider']=='deepseek':
        body['thinking']={'type':'disabled' if mode=='fast' else 'enabled'}
        if effort:body['reasoning_effort']=effort
    elif p['provider']=='gemini':
        body['reasoning_effort']={'fast':'minimal','standard':'low','deep':'high','max':'high'}[mode]
    elif effort:
        body['reasoning_effort']=effort
    if structured and p['provider'] in ('gemini','deepseek'):
        body['response_format'] = {'type':'json_object'}
    last = None
    last_usage={}
    finish_reason=None
    auto_budget=not int(p.get('max_output_tokens',0))
    started=time.monotonic()
    budget=_BUDGET.get()
    if budget is None:budget={'remaining':3}
    for attempt in range(3):
        if budget['remaining']<=0:break
        budget['remaining']-=1
        last_usage={};finish_reason=None
        try:
            with transport.client(p['endpoint'],p['timeout']) as client:
                response = client.post(p['endpoint'],headers={'Authorization':f'Bearer {secret}'},json=body)
            if response.status_code >= 300:
                status = response.status_code
                last = response_error(response,secret)
                if status==429 or status>=500:
                    if attempt<2:
                        time.sleep(2 ** attempt)
                        continue
                raise last
            if len(response.content)>1024*1024: raise ModelError('invalid_output','模型响应超过大小限制')
            data = response.json()
            last_usage=data.get('usage',{})
            finish_reason=data['choices'][0].get('finish_reason')
            if finish_reason=='length':
                if auto_budget and mode!='fast' and body['max_tokens']<32768 and budget['remaining']>0 and attempt<2:
                    db.put('model_call',{'profile_id':profile_id,'model':p['model'],'endpoint':p['endpoint'],'reasoning':mode,'max_output_tokens':body['max_tokens'],'status':'failed','error_code':'truncated','finish_reason':'length','usage':last_usage,'duration_ms':round((time.monotonic()-started)*1000),'retry_action':'increase_budget','next_budget':min(32768,body['max_tokens']*2)})
                    body['max_tokens']=min(32768,body['max_tokens']*2)
                    continue
                raise ModelError('truncated','生成预算耗尽，未保存不完整回答；已达到预算或重试上限，请拆分任务或调整显式预算')
            result = data['choices'][0]['message']['content']
            if not isinstance(result,str) or not result.strip():
                raise ModelError('invalid_output','模型没有返回可用文本')
            call=db.put('model_call',{'profile_id':profile_id,'model':p['model'],'endpoint':p['endpoint'],'reasoning':mode,'max_output_tokens':body['max_tokens'],'status':'success','finish_reason':finish_reason,'usage':data.get('usage',{}),'duration_ms':round((time.monotonic()-started)*1000)})
            _LAST_CALL.set(call['id'])
            return result
        except httpx.TimeoutException:
            last = ModelError('timeout','模型调用超时，请重试或增加超时时间')
        except (httpx.RequestError,httpx.InvalidURL):
            last = ModelError('connection','无法连接模型服务，请检查网络或代理')
        except (KeyError,ValueError,IndexError,TypeError):
            last = ModelError('invalid_output','模型响应格式无效')
        except ModelError as e:
            last = e
            break
        if attempt<2:
            time.sleep(2 ** attempt)
    last=last or ModelError('retry_budget','本轮请求次数已达到上限')
    call=db.put('model_call',{'profile_id':profile_id,'model':p['model'],'endpoint':p['endpoint'],'reasoning':mode,'max_output_tokens':body['max_tokens'],'duration_ms':round((time.monotonic()-started)*1000),'status':'failed','usage':last_usage,'finish_reason':finish_reason,'error_code':last.code,'message':str(last),'diagnostics':last.diagnostics})
    _LAST_CALL.set(call['id'])
    raise last

def _structured(system, context, schema, profile_id='default', validate=None):
    prompt = system + '\n只返回符合以下 JSON Schema 的一个数据对象。字段名必须与 Schema 完全一致，不翻译、不包裹在 result/data 中，不返回 Schema 本身。\nJSON Schema：' + json.dumps(schema.model_json_schema(),ensure_ascii=False)
    serialized=json.dumps(context,ensure_ascii=False)
    if len(serialized)>50000:
        raise ModelError('context_limit','本轮内容超过限制，请缩小分析范围；原始记录保留')
    for attempt in range(2):
        text = generate(prompt, serialized,profile_id,True)
        try:
            clean = text.strip()
            if clean.startswith('```'):
                clean = '\n'.join(clean.splitlines()[1:-1])
            value = schema.model_validate_json(clean)
            if validate:
                validate(value)
            return value.model_dump()
        except (ValueError,TypeError) as error:
            # Only paths and validation codes go back to the model. Never echo an
            # arbitrary exception, input value or provider response in the retry.
            issues=[{'field':'.'.join(str(part) for part in item['loc']),'type':item['type']} for item in error.errors(include_input=False,include_context=False,include_url=False)[:8]] if isinstance(error,ValidationError) else [{'type':'citation_validation','rule':'只能原样引用输入中允许的 ID'}]
            if attempt:
                raise ModelError('validation','模型输出结构或引用无效，未保存成功结果',{'validation_errors':issues})
            prompt += '\n上一次结果未通过校验：'+json.dumps(issues,ensure_ascii=False)+'。重新生成完整对象，修正这些字段；字符串字段不得使用数组或对象。'


def structured(system, context, schema, profile_id='default', validate=None):
    token=_BUDGET.set({'remaining':3})
    try:return _structured(system,context,schema,profile_id,validate)
    finally:_BUDGET.reset(token)


def test_connection(id='default',reasoning=None):
    p=profile(id);mode=reasoning or p['reasoning']
    if mode not in MODES:raise ValueError('未知推理档位')
    class Check(__import__('pydantic').BaseModel):
        ok:bool
    record={'profile_id':id,'model':p['model'],'endpoint':p['endpoint'],'reasoning':mode,'max_output_tokens':int(p.get('max_output_tokens',0)) or MODES[mode]}
    try:
        with options(reasoning=mode):
            result=structured('This is a connection and JSON test. Return {"ok":true}.',{'test':'No learning materials.'},Check,id)
        if not result['ok']:raise ModelError('validation','连接输出未通过校验')
        call=db.get(last_call(),'model_call')
        return db.put('model_test',dict(record,status='success',message='JSON 生成成功',max_output_tokens=call.get('max_output_tokens'),usage=call.get('usage',{}),duration_ms=call.get('duration_ms')))
    except ModelError as e:
        db.put('model_test',dict(record,status='failed',error_code=e.code,message=str(e),diagnostics=e.diagnostics))
        raise
