import hashlib
import json
from . import db

SITE_URL='http://127.0.0.1:8765'
PAGE_SETUP='练习站点为 http://127.0.0.1:8765（仅在隔离实训环境内可访问）。pytest 已提供 page: Page fixture，无需自行启动浏览器或服务。页面有名称为「提交订单」的 button，以及 role="status" 的状态区：初始为「等待提交」，点击后变为「订单已确认」。'

SITE='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>Checkout lab</title><h1>结算</h1><label>商品数量 <input aria-label="商品数量" type="number" value="1"></label><button onclick="document.querySelector('[role=status]').textContent='订单已确认'">提交订单</button><p role="status">等待提交</p></html>'''
# Fixed instructor defects. Browser requests cannot supply another site or command.
CHECKOUT_MUTATIONS=(
    {'id':'initial-confirmed','title':'初始状态被错误地设为已确认',
     'site':SITE.replace('<p role="status">等待提交</p>','<p role="status">订单已确认</p>')},
    {'id':'submit-no-update','title':'点击提交后状态没有更新',
     'site':SITE.replace("document.querySelector('[role=status]').textContent='订单已确认'",'void(0)')},
)

def mutations(exercise):
    return CHECKOUT_MUTATIONS if exercise['id']=='checkout-project' else ()

EXERCISES=[
 {'id':'assertions','title':'断言与边界条件','type':'pytest','concept':'断言','prompt':'被测函数已由 app_under_test.py 提供，请使用 from app_under_test import discount；不要重新定义它。编写 test_discount：验证 discount(100, 0.2) 等于 80，并验证负数金额抛出 ValueError。当前实现只检查 amount < 0，不校验 rate 的范围；其余数值按 amount * (1-rate) 返回。','starter':'import pytest\nfrom app_under_test import discount\n\ndef test_discount():\n    # 同时覆盖正常值和错误边界\n    pass\n','files':{'app_under_test.py':'def discount(amount, rate):\n    if amount < 0:\n        raise ValueError("amount must be nonnegative")\n    return amount * (1-rate)\n'},'requirements':['assert','pytest.raises']},
 {'id':'fixtures','title':'fixture 的生命周期','type':'pytest','concept':'Fixture','prompt':'本练习不提供额外被测模块，请在当前测试文件中自行定义 cart fixture。用 @pytest.fixture 定义 cart，返回空列表。在两个独立测试中通过函数参数 cart 使用 fixture，验证每次取得的是空列表，再添加商品。','starter':'import pytest\n\n# 定义 cart fixture，然后编写两个独立测试\n','files':{},'requirements':['pytest.fixture','assert','append'],'min_tests':2},
 {'id':'locators','title':'稳定的角色定位器','type':'playwright','concept':'定位器','prompt':PAGE_SETUP+'使用 get_by_role 点击「提交订单」，使用 expect 检查 status 为「订单已确认」。','starter':'from playwright.sync_api import Page, expect\n\ndef test_checkout(page: Page):\n    page.goto("http://127.0.0.1:8765")\n    # 补全角色定位器和断言\n','files':{},'requirements':['get_by_role','expect','to_have_text']},
 {'id':'waiting','title':'用重试断言等待结果','type':'playwright','concept':'等待','prompt':PAGE_SETUP+'提交订单并用 expect 的重试断言检查状态，不使用 time.sleep。当前样例同步更新状态；重试断言用于稳定地验证结果，不假设额外的异步接口或延迟。','starter':'from playwright.sync_api import Page, expect\n\ndef test_status(page: Page):\n    page.goto("http://127.0.0.1:8765")\n','files':{},'requirements':['expect','to_have_text'],'forbidden':['time.sleep']},
 {'id':'checkout-project','title':'Checkout 项目验收','type':'playwright','concept':'Page Object','prompt':PAGE_SETUP+'在同一文件中定义 CheckoutPage 类，封装页面访问和提交行为；至少写两个测试，分别检查初始状态「等待提交」和提交后状态「订单已确认」。使用角色定位器和 expect。正常实现通过后，系统会在两个固定缺陷实现上重跑，验证测试能发现初始状态错误和提交后不更新；三次运行分别隔离。','starter':'from playwright.sync_api import Page, expect\n\nclass CheckoutPage:\n    def __init__(self, page: Page):\n        self.page = page\n\n# 完成页面对象与两个测试\n','files':{},'requirements':['CheckoutPage','get_by_role','expect','to_have_text'],'min_tests':2},
]

def get(id):
    return next((x for x in EXERCISES if x['id']==id),None)

def public():
    return [{k:v for k,v in x.items() if k!='files'} for x in EXERCISES]


def contract(id):
    """Current fixed runner inputs, not an AI-generated specification or run result."""
    exercise=get(id)
    if not exercise:return None
    value={
        'exercise_id':exercise['id'],'title':exercise['title'],'type':exercise['type'],
        'prompt':exercise['prompt'],'provided_files':dict(exercise['files']),
        'requirements':list(exercise['requirements']),'forbidden':list(exercise.get('forbidden',[])),
        'min_tests':exercise.get('min_tests',1),'learner_file':'test_exercise.py',
        'editable_scope':'只能编辑测试文件；被测模块、站点与执行命令由系统固定提供。',
        'provenance':'current_builtin_runner_definition',
        'execution_claim':'当前可运行环境说明，不代表补练已执行或历史运行环境完全相同。',
    }
    if exercise['type']=='playwright':
        value['site']={'url':SITE_URL,'html':SITE,'fixtures':{'page':'pytest-playwright 提供的 playwright.sync_api.Page'},'network':'仅容器内练习站点，无外网'}
    value['mutation_cases']=[{'id':case['id'],'title':case['title']} for case in mutations(exercise)]
    value['version']=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return value
