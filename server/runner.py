import ast
import base64
import os
import selectors
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from . import db,jobs,exercises

IMAGE=os.environ.get('LEARNLOOP_RUNNER_IMAGE','learnloop-runner:1')
WORKSPACES=Path(os.environ.get('LEARNLOOP_RUNNER_WORKSPACE_DIR',db.ROOT/'server/data/runner-workspaces'))
LIMIT=65536
TIMEOUT=60

class RunnerUnavailable(ValueError): pass

def status():
    docker=shutil.which('docker')
    if not docker:
        return {'ready':False,'reason':'Docker 未安装'}
    try:
        daemon=subprocess.run([docker,'info','--format','{{.ServerVersion}}'],capture_output=True,timeout=5)
        if daemon.returncode:
            return {'ready':False,'reason':'Docker 服务未启动'}
        image=subprocess.run([docker,'image','inspect',IMAGE],capture_output=True,timeout=5)
        return {'ready':image.returncode==0,'reason':'就绪' if image.returncode==0 else '请构建 learnloop-runner:1 镜像','image':IMAGE}
    except subprocess.TimeoutExpired:
        return {'ready':False,'reason':'Docker 响应超时'}

def bounded(command,timeout,limit=LIMIT,cancel=lambda:None):
    proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    sel=selectors.DefaultSelector()
    sel.register(proc.stdout,selectors.EVENT_READ,'stdout')
    sel.register(proc.stderr,selectors.EVENT_READ,'stderr')
    data={'stdout':bytearray(),'stderr':bytearray()}
    started=time.monotonic()
    reason=None
    try:
        while sel.get_map():
            cancel()
            if time.monotonic()-started>timeout:
                reason='timeout';break
            if sum(len(x) for x in data.values())>=limit:
                reason='output_limit';break
            for event,_ in sel.select(.1):
                chunk=os.read(event.fileobj.fileno(),4096)
                if not chunk:
                    sel.unregister(event.fileobj)
                else:
                    remaining=limit-sum(len(x) for x in data.values())
                    data[event.data].extend(chunk[:remaining])
        if reason:
            proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill();proc.wait()
        return {'exit_code':proc.returncode,'stdout':bytes(data['stdout']),'stderr':bytes(data['stderr']),'stop_reason':reason}
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait()
        sel.close()
        proc.stdout.close();proc.stderr.close()

def classify(output,exit_code,stop):
    if stop or exit_code in (125,126,127,137) or 'MemoryError' in output: return 'environment'
    if 'SyntaxError' in output or 'IndentationError' in output: return 'syntax'
    if 'AssertionError' in output or 'assert ' in output: return 'assertion'
    if 'ImportError' in output or 'ModuleNotFoundError' in output: return 'environment'
    return 'design' if exit_code else 'unknown'

def rubric_checks(code,exercise):
    try: tree=ast.parse(code)
    except SyntaxError: tree=ast.Module(body=[],type_ignores=[])
    nodes=list(ast.walk(tree))
    aliases={}
    for node in nodes:
        if isinstance(node,ast.Import):
            aliases.update({n.asname or n.name:n.name for n in node.names})
        elif isinstance(node,ast.ImportFrom):
            aliases.update({n.asname or n.name:(node.module or '')+'.'+n.name for n in node.names})
    def name(node):
        if isinstance(node,ast.Name): return aliases.get(node.id,node.id)
        if isinstance(node,ast.Attribute): return name(node.value)+'.'+node.attr
        if isinstance(node,ast.Call): return name(node.func)
        return ''
    calls=[name(n.func) for n in nodes if isinstance(n,ast.Call)]
    def matches(requirement):
        if requirement=='assert':return any(isinstance(n,ast.Assert) for n in nodes)
        if requirement=='CheckoutPage':return any(isinstance(n,ast.ClassDef) and n.name=='CheckoutPage' for n in nodes)
        if requirement=='pytest.fixture':return any(name(d)=='pytest.fixture' for n in nodes if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) for d in n.decorator_list)
        return any(c==requirement or ('.' not in requirement and c.endswith('.'+requirement)) for c in calls)
    return ([{'requirement':r,'met':matches(r)} for r in exercise['requirements']]+
            [{'requirement':'禁止 '+r,'met':not matches(r)} for r in exercise.get('forbidden',[])])

def _decode_result(result, exercise, started):
    junit=''
    stdout=result['stdout'].decode('utf-8','replace');stderr=result['stderr'].decode('utf-8','replace')
    marker='\nLEARNLOOP_REPORT_V1:'
    if marker in stdout:
        visible,encoded=stdout.rsplit(marker,1)
        try:
            raw=base64.b64decode(encoded.strip(),validate=True)
            if len(raw)<=16384:
                junit=raw.decode('utf-8','replace');stdout=visible
        except (ValueError,UnicodeError):pass
    counts={'tests':0,'failures':0,'errors':0,'skipped':0}
    try:
        root=ET.fromstring(junit)
        for suite in root.iter('testsuite'):
            for key in counts:counts[key]+=int(suite.attrib.get(key,0))
    except (ET.ParseError,ValueError):pass
    executed=counts['tests']-counts['skipped']
    passed=result['exit_code']==0 and not result['stop_reason'] and executed>=exercise.get('min_tests',1) and not counts['failures'] and not counts['errors']
    return {'command':['python','-m','pytest','test_exercise.py'],'exit_code':result['exit_code'],'stdout':stdout,'stderr':stderr,
        'stop_reason':result['stop_reason'],'duration_ms':int((time.monotonic()-started)*1000),'junit':junit,'counts':counts,
        'passed':passed,'report_origin':'learner_generated','error_type':classify(stdout+stderr,result['exit_code'],result['stop_reason'])}


def _run_case(job, exercise, code, case_id, site, progress):
    """One fixed evaluation case, one isolated container; never recursive."""
    docker=shutil.which('docker');name='learnloop-'+job['id']+'-'+case_id
    subprocess.run([docker,'rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    started=time.monotonic();WORKSPACES.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='learnloop-',dir=WORKSPACES) as directory:
        folder=Path(directory);folder.chmod(0o755)
        (folder/'test_exercise.py').write_text(code)
        (folder/'bootstrap.py').write_text((db.ROOT/'runner/bootstrap.py').read_text())
        (folder/'site').mkdir();(folder/'site/index.html').write_text(site)
        for filename,content in exercise['files'].items():(folder/filename).write_text(content)
        command=[docker,'create','--name',name,'--label','learnloop.runner=1','--log-driver=none','--network=none','--cpus=1','--memory=768m','--memory-swap=768m','--pids-limit=128','--read-only','--tmpfs','/tmp:rw,nosuid,size=128m','--shm-size=128m','--cap-drop=ALL','--security-opt','no-new-privileges','--user','65532:65532','--ulimit','fsize=8388608:8388608','--ulimit','nofile=256:256','--mount',f'type=bind,source={folder},target=/workspace,readonly','--workdir','/workspace',IMAGE,'python','/workspace/bootstrap.py']
        try:
            jobs.check(job['id']);created=bounded(command,15,cancel=lambda:jobs.check(job['id']))
            if created['exit_code'] or created['stop_reason']:
                if b'mount' in created['stderr'].lower():raise RunnerUnavailable('Docker 无法访问练习临时目录，请将项目放到 Docker 共享目录，或配置 LEARNLOOP_RUNNER_WORKSPACE_DIR')
                raise RunnerUnavailable('无法创建隔离容器，请检查镜像与 Docker')
            jobs.step(job['id'],progress,container_name=name,workspace=str(folder),evaluation_case=case_id)
            result=bounded([docker,'start','-a',name],TIMEOUT,cancel=lambda:jobs.check(job['id']))
            if result['stop_reason']:subprocess.run([docker,'kill',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
            jobs.check(job['id'])
            return _decode_result(result,exercise,started)
        finally:
            subprocess.run([docker,'rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)


def _mutation_detected(result, exercise):
    counts=result['counts']
    return (result['exit_code']==1 and not result['stop_reason'] and result['error_type']=='assertion'
        and counts['tests']-counts['skipped']>=exercise.get('min_tests',1) and counts['failures']>0 and counts['errors']==0)


def execute(job):
    try:
        previous=db.get('run-'+job['id'],'run')
        if job['payload'].get('concept_id'):
            from .learning import recompute
            recompute(job['payload']['concept_id'])
        return previous
    except ValueError:pass
    ready=status()
    if not ready['ready']:raise RunnerUnavailable(ready['reason'])
    exercise=exercises.get(job['payload']['exercise_id'])
    if not exercise:raise ValueError('未知练习')
    code=job['payload']['code'];scope={k:job['payload'][k] for k in ('space_id','domain_id','goal_id','session_id','concept_id','task_id','task_event_id','task_fingerprint','task_question','task_concept_id','task_binding','linked_exercise_id','grading_scope') if job['payload'].get(k) is not None}
    if scope.get('task_binding')!='explicit_user':
        for key in ('task_id','task_event_id','task_fingerprint','task_question','task_concept_id','linked_exercise_id'):
            scope.pop(key,None)
        scope['task_binding']='session_context_only' if scope.get('session_id') else 'unbound'
    scope['grading_scope']='fixed_exercise_only'
    scope.setdefault('domain_id','automation-testing')
    cases=exercises.mutations(exercise);docker=shutil.which('docker')
    # Recovery removes only this job's known containers, including an interrupted defect run.
    for suffix in ['', '-baseline', *['-'+x['id'] for x in cases]]:
        subprocess.run([docker,'rm','-f','learnloop-'+job['id']+suffix],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    started=time.monotonic();baseline=_run_case(job,exercise,code,'baseline',exercises.SITE,15)
    mutation_checks=[];blocked=False
    if baseline['passed']:
        for i,case in enumerate(cases):
            jobs.check(job['id'])
            if blocked:
                mutation_checks.append({'id':case['id'],'title':case['title'],'status':'not_run','detected':False,'reason':'前一缺陷验收遇到环境问题，等待重试'});continue
            try:result=_run_case(job,exercise,code,case['id'],case['site'],45+i*25)
            except (RunnerUnavailable,subprocess.TimeoutExpired):
                result={'command':['python','-m','pytest','test_exercise.py'],'exit_code':None,'stdout':'','stderr':'隔离环境暂不可用，缺陷验收未完成',
                    'stop_reason':'container_unavailable','duration_ms':0,'junit':'','counts':{'tests':0,'failures':0,'errors':0,'skipped':0},
                    'passed':False,'report_origin':'environment','error_type':'environment'}
            detected=_mutation_detected(result,exercise);blocked=result['error_type']=='environment'
            mutation_checks.append({**result,'id':case['id'],'title':case['title'],'detected':detected,
                'status':'environment' if blocked else 'detected' if detected else 'missed'})
    detected=sum(x['detected'] for x in mutation_checks)
    mutation_status='not_required' if not cases else 'not_run' if not baseline['passed'] else 'environment' if blocked else 'passed' if detected==len(cases) else 'missed'
    summary={'required':bool(cases),'total':len(cases),'detected':detected,'status':mutation_status}
    checks=rubric_checks(code,exercise)
    if cases:checks.append({'requirement':'发现两个固定 Checkout 缺陷（初始状态与提交更新）','met':detected==len(cases)})
    satisfied=baseline['passed'] and all(x['met'] for x in checks)
    error_type='environment' if blocked else 'design' if baseline['passed'] and not satisfied else baseline['error_type']
    with db.connect() as transaction:
        transaction.execute('BEGIN IMMEDIATE')
        from .assessments import run_flags
        assessment_flags=run_flags(job['payload'],transaction)
        record=db.put('run',{**scope,**assessment_flags,**baseline,'exercise_id':exercise['id'],'code':code,
            'duration_ms':int((time.monotonic()-started)*1000),'baseline_duration_ms':baseline['duration_ms'],
            'rubric_checks':checks,'requirements_met':satisfied,'error_type':error_type,
            'mutation_checks':mutation_checks,'mutation_summary':summary,'evaluation_version':'checkout-mutation-v1' if cases else 'execution-rubric-v1'},id='run-'+job['id'],con=transaction)
        concept_id=job['payload'].get('concept_id')
        if concept_id:
            db.get(concept_id,'concept',transaction)
            feedback='参考实际基准运行、固定缺陷检查与任务要求；报告由学习代码产生，不等同于能力认证'
            if blocked:feedback='缺陷验收遇到环境故障，本次不影响掌握度，修复环境后可重试'
            attempt=db.put('attempt',{**scope,**assessment_flags,'concept_id':concept_id,'exercise_id':exercise['id'],'question':exercise['prompt'],
                'rubric':exercise['requirements'],'answer':code,'score':1 if satisfied else 0,'hint_level':0,'answer_seen':False,
                'error_type':error_type,'feedback':feedback,'evaluator':'execution_and_rubric','is_transfer':exercise['id']=='checkout-project',
                'run_id':record['id']},id='attempt-'+job['id'],con=transaction)
            if not satisfied and error_type!='environment' and not assessment_flags.get('assessment_voided'):
                db.put('mistake',{**scope,'attempt_id':attempt['id'],'concept_id':concept_id,'question':exercise['prompt'],'answer':code,
                    'expected':str(exercise['requirements']),'run_id':record['id'],'status':'pending_analysis','error_type':error_type},id='mistake-'+job['id'],con=transaction)
    if concept_id:
        from .learning import recompute
        recompute(concept_id)
    return record
