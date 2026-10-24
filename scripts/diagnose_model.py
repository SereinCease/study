"""Read-only, credential-redacted diagnosis of the configured gateway.

Sends only a short test prompt. Never probes another host or follows redirects.
"""
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values
from server import db,models,transport

def main():
    db.init()
    profile=models.profile();secret=models.key();disk=dotenv_values(db.ROOT/'.env')
    report={'checked_at':db.now(),'endpoint':profile['endpoint'],'model':profile['model'],
        'configuration':{'has_key':bool(secret),'key_source':models.public_profile()['key_source'],
            'matches_project_env':secret==disk.get('LEARNLOOP_LLM_API_KEY'),
            'has_outer_whitespace':secret!=secret.strip(),'file_mode':oct((db.ROOT/'.env').stat().st_mode & 0o777)},'checks':[]}
    if not secret:raise SystemExit('No configured API key; no requests sent.')
    for route in ('configured_proxy','direct'):
        try:
            with (transport.client(profile['endpoint'],30) if route=='configured_proxy' else httpx.Client(timeout=30,trust_env=False,follow_redirects=False)) as client:
                response=client.post(profile['endpoint'],headers={'Authorization':f'Bearer {secret}'},json={'model':profile['model'],'messages':[{'role':'user','content':'Reply OK.'}],'max_tokens':32})
            result={'route':route,'payload':'minimal; no thinking, reasoning_effort or response_format','http_status':response.status_code}
            if response.status_code>=300:
                error=models.response_error(response,secret)
                result.update(code=error.code,message=str(error),diagnostics=error.diagnostics)
            else:result['message']='Gateway accepted this minimal request; structured generation still requires a separate test.'
        except httpx.RequestError as error:result={'route':route,'code':'connection','exception_type':type(error).__name__}
        report['checks'].append(result)
    output=db.ROOT/'reports/model-diagnosis-v3.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
