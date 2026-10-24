import os,sys,subprocess,threading,time
from pathlib import Path
import pytest
import tempfile
from server import db,jobs,runner


def test_bounded_output_and_timeout():
    r=runner.bounded([sys.executable,'-c','import time;time.sleep(5)'],.2)
    assert r['stop_reason']=='timeout'
    r=runner.bounded([sys.executable,'-c','import os\nwhile True:os.write(1,b"x"*4096)'],3,limit=8192)
    assert r['stop_reason']=='output_limit' and len(r['stdout'])<=8192

@pytest.mark.parametrize('text,code,stop,category', [('SyntaxError: invalid',1,None,'syntax'),('assert 1 == 2',1,None,'assertion'),('ImportError',2,None,'environment'),('',137,None,'environment'),('',-9,'timeout','environment')])
def test_error_classification(text,code,stop,category):assert runner.classify(text,code,stop)==category

@pytest.fixture
def sandbox(monkeypatch):
    if os.environ.get('RUN_SANDBOX_TESTS')!='1':pytest.skip('Set RUN_SANDBOX_TESTS=1 for actual Docker checks')
    state=runner.status()
    assert state['ready'],state['reason']
    with tempfile.TemporaryDirectory(prefix='sandbox-check-',dir=db.ROOT/'server/data') as workspaces:
        monkeypatch.setattr(runner,'WORKSPACES',Path(workspaces))
        yield state

def run(code,exercise='assertions'):
    job=jobs.enqueue('run',{'code':code,'exercise_id':exercise})
    jobs.tick()
    finished=db.get(job['id'])
    assert finished['status']=='completed',finished.get('error')
    assert not list(runner.WORKSPACES.glob('learnloop-*'))
    containers=subprocess.run(['docker','ps','-a','--filter','name=learnloop-'+job['id'],'--format','{{.Names}}'],capture_output=True,text=True)
    assert not containers.stdout.strip()
    return finished['result']

def test_actual_pass_fail_no_fake_success(sandbox):
    good=run('import pytest\nfrom app_under_test import discount\ndef test_discount():\n    assert discount(100, .2)==80\n    with pytest.raises(ValueError): discount(-1,.2)\n')
    assert good['passed'] and good['requirements_met'] and good['counts']['tests']==1,good
    before=len(db.all('run'))
    assert runner.execute(db.get(good['id'][4:],'job'))['id']==good['id']
    assert len(db.all('run'))==before
    bad=run('def test_wrong():\n    assert 1 == 2\n')
    assert not bad['passed'] and bad['counts']['failures']==1
    empty=run('def test_empty():\n    """assert pytest.raises"""\n    pass\n')
    assert empty['passed'] and not empty['requirements_met']

def test_actual_network_host_files_write_and_user_limits(sandbox,tmp_path):
    sentinel=tmp_path/'host-only.txt';sentinel.write_text('private-host-sentinel')
    code=f'''import os,socket,pytest
from pathlib import Path

def test_isolation():
    assert os.getuid()!=0
    assert not Path({str(sentinel)!r}).exists()
    assert not Path('/var/run/docker.sock').exists()
    with pytest.raises(OSError):Path('/workspace/tamper.py').write_text('bad')
    with pytest.raises(OSError):Path('/etc/tamper').write_text('bad')
    with pytest.raises(OSError):socket.create_connection(('1.1.1.1',443),timeout=1)
    assert 'eth0' not in Path('/proc/net/dev').read_text()
    assert Path('/sys/fs/cgroup/memory.max').read_text().strip()=='805306368'
    assert Path('/sys/fs/cgroup/pids.max').read_text().strip()=='128'
    assert Path('/sys/fs/cgroup/cpu.max').read_text().split()[0]=='100000'
    import resource
    assert resource.getrlimit(resource.RLIMIT_FSIZE)==(8388608,8388608)
    fs=os.statvfs('/tmp')
    assert fs.f_blocks*fs.f_frsize<=134217728
'''
    result=run(code);assert result['passed'],result['stdout']+result['stderr']
    assert sentinel.read_text()=='private-host-sentinel'

def test_actual_timeout_and_cleanup(sandbox,monkeypatch):
    monkeypatch.setattr(runner,'TIMEOUT',4)
    r=run('def test_loop():\n    while True: pass\n')
    assert not r['passed'] and r['stop_reason']=='timeout'

def test_actual_output_flood(sandbox):
    code='import os\ndef test_output():\n    for i in range(1000000): os.write(1,b"x"*4096)\n'
    # Pytest captures output by default; direct /proc/1/fd/1 exercises the attached output bound.
    code='import os\ndef test_output():\n    fd=os.open("/proc/1/fd/1",os.O_WRONLY)\n    while True:os.write(fd,b"x"*4096)\n'
    r=run(code);assert r['stop_reason']=='output_limit'
    assert len(r['stdout'].encode())+len(r['stderr'].encode())<=runner.LIMIT

def test_actual_memory_limit(sandbox):
    r=run('def test_memory():\n    x=bytearray(2*1024**3)\n')
    assert not r['passed']
    assert r['error_type']=='environment',r

def test_actual_cancel(sandbox):
    job=jobs.enqueue('run',{'exercise_id':'assertions','code':'import time\ndef test_wait(): time.sleep(100)'})
    thread=threading.Thread(target=jobs.tick);thread.start()
    deadline=time.monotonic()+20
    active=False
    while time.monotonic()<deadline:
        name=db.get(job['id']).get('container_name')
        if name:
            state=subprocess.run(['docker','inspect','--format','{{.State.Running}}',name],capture_output=True,text=True)
            processes=subprocess.run(['docker','top',name,'-eo','pid,args'],capture_output=True,text=True)
            if state.stdout.strip()=='true' and '-m pytest' in processes.stdout:
                active=True;break
        time.sleep(.1)
    db.update(job['id'],{'cancel_requested':True})
    thread.join(20);assert not thread.is_alive()
    # Cancellation must interrupt an executing test, not merely remove a created container.
    assert active,'The test process never started before cancellation'
    assert db.get(job['id'])['status']=='cancelled'
    assert not list(runner.WORKSPACES.glob('learnloop-*'))
    names=subprocess.run(['docker','ps','-a','--filter','name=learnloop-'+job['id'],'--format','{{.Names}}'],capture_output=True,text=True).stdout
    assert not names.strip()

def test_actual_playwright_checkout_project(sandbox):
    code='''from playwright.sync_api import Page, expect
class CheckoutPage:
    def __init__(self, page: Page): self.page=page
    def open(self): self.page.goto('http://127.0.0.1:8765')
    def submit(self): self.page.get_by_role('button', name='提交订单').click()
def test_initial(page: Page):
    checkout=CheckoutPage(page);checkout.open()
    expect(page.get_by_role('status')).to_have_text('等待提交')
def test_submit(page: Page):
    checkout=CheckoutPage(page);checkout.open();checkout.submit()
    expect(page.get_by_role('status')).to_have_text('订单已确认')
'''
    r=run(code,'checkout-project');assert r['requirements_met'],r['stdout']+r['stderr']

CHECKOUT_COMPLETE='''from playwright.sync_api import Page, expect
class CheckoutPage:
    def __init__(self, page: Page): self.page=page
    def open(self): self.page.goto('http://127.0.0.1:8765')
    def submit(self): self.page.get_by_role('button', name='提交订单').click()
def test_initial(page: Page):
    checkout=CheckoutPage(page);checkout.open()
    expect(page.get_by_role('status')).to_have_text('等待提交')
def test_submit(page: Page):
    checkout=CheckoutPage(page);checkout.open();checkout.submit()
    expect(page.get_by_role('status')).to_have_text('订单已确认')
'''
CHECKOUT_FINAL_ONLY=CHECKOUT_COMPLETE.replace("expect(page.get_by_role('status')).to_have_text('等待提交')", "checkout.submit();expect(page.get_by_role('status')).to_have_text('订单已确认')")


def test_actual_checkout_passing_tests_must_detect_both_defects(sandbox):
    result=run(CHECKOUT_FINAL_ONLY,'checkout-project')
    assert result['passed'] and not result['requirements_met']
    assert result['error_type']=='design'
    assert result['mutation_summary']=={'required':True,'total':2,'detected':1,'status':'missed'}
    assert [(x['id'],x['status']) for x in result['mutation_checks']]==[('initial-confirmed','missed'),('submit-no-update','detected')]
    assert result['mutation_checks'][0]['exit_code']==0
    assert result['mutation_checks'][1]['counts']['failures']==2


def _case_result(passed=True,environment=False):
    return {'command':['python','-m','pytest','test_exercise.py'],'exit_code':137 if environment else 0 if passed else 1,
        'stdout':'AssertionError' if not passed else '', 'stderr':'','stop_reason':'timeout' if environment else None,'duration_ms':5,
        'junit':'<testsuites/>','counts':{'tests':2,'failures':0 if passed else 1,'errors':0,'skipped':0},
        'passed':passed,'report_origin':'learner_generated','error_type':'environment' if environment else 'unknown' if passed else 'assertion'}


def test_mutation_environment_failure_preserves_mastery(monkeypatch):
    concept=db.put('concept',{'title':'Checkout Page Object','citation_ids':[],'source_ids':[]})
    prior=db.put('attempt',{'concept_id':concept['id'],'score':1,'hint_level':0,'answer_seen':False,'error_type':'unknown'})
    from server import learning
    before=learning.recompute(concept['id'])
    monkeypatch.setattr(runner,'status',lambda:{'ready':True})
    monkeypatch.setattr(runner.shutil,'which',lambda name:'docker')
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:None)
    cases=[]
    def fake_case(job,exercise,code,case_id,site,progress):
        cases.append(case_id)
        return _case_result() if case_id=='baseline' else _case_result(False,True)
    monkeypatch.setattr(runner,'_run_case',fake_case)
    job=jobs.enqueue('run',{'code':CHECKOUT_COMPLETE,'exercise_id':'checkout-project','concept_id':concept['id']})
    result=runner.execute(job)
    assert cases==['baseline','initial-confirmed']
    assert result['passed'] and not result['requirements_met'] and result['error_type']=='environment'
    assert result['mutation_summary']['status']=='environment'
    assert result['mutation_checks'][1]['status']=='not_run'
    after=db.get('mastery-'+concept['id'])
    assert before['score']==after['score'] and before['evidence_count']==after['evidence_count']
    assert not db.all('mistake')
    runner.execute(job);assert len(cases)==2 and db.count('attempt')==2


def test_baseline_failure_never_runs_mutations(monkeypatch):
    monkeypatch.setattr(runner,'status',lambda:{'ready':True})
    monkeypatch.setattr(runner.shutil,'which',lambda name:'docker')
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:None)
    cases=[]
    def fake_case(job,exercise,code,case_id,site,progress):
        cases.append(case_id);return _case_result(False)
    monkeypatch.setattr(runner,'_run_case',fake_case)
    result=runner.execute(jobs.enqueue('run',{'code':CHECKOUT_COMPLETE,'exercise_id':'checkout-project'}))
    assert cases==['baseline'] and result['mutation_checks']==[] and result['mutation_summary']['status']=='not_run'


@pytest.mark.parametrize('changes',[
 {'exit_code':2}, {'error_type':'environment'}, {'stop_reason':'timeout'},
 {'counts':{'tests':2,'failures':0,'errors':1,'skipped':0}},
 {'counts':{'tests':2,'failures':1,'errors':0,'skipped':1}},
])
def test_mutation_failure_requires_executed_assertion_evidence(changes):
    from server import exercises
    result={**_case_result(False),**changes}
    assert not runner._mutation_detected(result,exercises.get('checkout-project'))
