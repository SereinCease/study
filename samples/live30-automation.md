# 自动化测试长会话验收教材（自编示例）

这是用于软件验收的自编教材，不是真题、认证题或学习者能力证明。所有函数行为均以本教材的明确实现为准；未声明的校验不能当成已有契约。

## 契约驱动断言与测试设计

断言比较实际行为与预期行为。预期来自需求或明确实现，不能把被测函数结果再赋给 expected 后自我比较。没有抛异常只说明调用没有异常，不等于业务结果正确。一个测试里前面的断言失败后，后续语句不会执行；重要边界最好拆成独立测试。

### discount 的完整契约

```python
def discount(amount, rate):
    if amount < 0:
        raise ValueError("amount must be non-negative")
    return amount * (1 - rate)
```

只校验负金额，不校验折扣率范围，不进行货币舍入。例如 discount(100,0.2) 是 80，discount(200,0.25) 是 150，discount(0,0.5) 是 0，discount(100,-0.2) 是 120，discount(100,1.2) 数学值为 -20。负金额不论折扣率是多少都抛出 ValueError。正常返回的二进制浮点值可能有微小误差。

```python
import pytest
from app_under_test import discount

def test_normal():
    assert discount(100, 0.2) == pytest.approx(80)

def test_fractional():
    assert discount(99.99, 0.15) == pytest.approx(84.9915)

def test_negative_amount():
    with pytest.raises(ValueError, match="non-negative"):
        discount(-1, 0.2)
```

单独出现 pytest.raises 字样不能证明异常分支已执行。静态代码点评不等于真实运行。确切执行结果需要测试报告、退出码和执行数量。固定样例通过也不能证明所有边界或未知实现都正确。

### 浮点比较与容差

数学期望为整数也不意味着实际二进制浮点结果恰好相等：Python 的 200 * 1.1 通常为 220.00000000000003；0.1 + 0.2 通常为 0.30000000000000004。适合使用 pytest.approx 或依据业务选择显式绝对/相对容差。不能通过无限放大容差掩盖错误；金额精确计算也可在业务实现明确采用 Decimal 后使用其契约，不能私自改变被测实现。

### 独立边界与参数化

等价类和边界包括正常金额、零金额、负金额、零折扣、全额折扣、未限制的负折扣或超过 1 的折扣。不要给当前函数凭空增加 TypeError、自定义范围限制或四舍五入规则。pytest.mark.parametrize 可以以多个独立 case 运行同类输入；报告数量是 case 数量而不一定是函数数量。失败 case 不应使其他独立 case 消失。

```python
@pytest.mark.parametrize("amount, rate, expected", [(100, .2, 80), (200, .25, 150), (0, .5, 0)])
def test_discount_cases(amount, rate, expected):
    assert discount(amount, rate) == pytest.approx(expected)
```

## fixture 资源隔离与清理

pytest fixture 默认作用域是 function。每个测试取得独立可变列表，防止上一测试污染下一测试。yield 前准备资源，yield 后执行清理。若共享 session 作用域的可变对象，测试间可能互相影响；共享不是任何情况下都错误，但需要明确只读约束或重置策略。不能仅为提速盲目扩大作用域。

```python
@pytest.fixture
def items():
    result = []
    yield result
    result.clear()
```

yield 尚未到达前初始化失败时，yield 后那一段清理不一定执行；已获取的资源应及时注册清理，或使用 try/finally/context manager 处理部分初始化失败。

## Playwright 定位、等待与断言

固定 Checkout 练习站点只有名称为“提交订单”的按钮和 role="status" 的状态区。初始文案“等待提交”，点击提交后“订单已确认”。使用 page.get_by_role("button", name="提交订单") 与 page.get_by_role("status")。定位器需唯一；使用 first() 掩盖重复元素可能点错对象，应该通过容器或可访问名称约束。

```python
from playwright.sync_api import expect
expect(page.get_by_role("status")).to_have_text("等待提交")
page.get_by_role("button", name="提交订单").click()
expect(page.get_by_role("status")).to_have_text("订单已确认")
```

expect 的重试断言等待可观察状态，不是固定 sleep。固定 sleep 很短可能失败，太长浪费时间。自动等待动作可操作并不等于业务状态断言正确；click 后仍需检查业务结果。

## Page Object 与缺陷检出

Page Object 封装页面访问、定位与操作，测试仍应表达预期行为。不能在操作方法中 catch 所有异常后吞掉失败。正常站点上通过只是必要条件；应检查测试能否发现“初始状态已经错误确认”和“点击后不更新”这两种固定缺陷。

```python
class CheckoutPage:
    def __init__(self, page):
        self.page = page
        self.status = page.get_by_role("status")
    def open(self):
        self.page.goto("http://127.0.0.1:8765")
    def submit(self):
        self.page.get_by_role("button", name="提交订单").click()
```

至少两个独立测试：初始状态测试与提交结果测试。只检查按钮存在不能证明订单提交成功。两个固定缺陷被检出也不等于发现所有潜在缺陷。

## 证据和评价边界

阅读、笔记、摘要、看答案与重复提交不产生独立成功证据。环境故障与业务断言错误必须分开。错题原答案、订正答案与变形补练分别保存。对模型出错的题目或评分，可以撤回其评价，保留原件和原因，再按有效证据重算。单日多次通过不等于跨日记忆保持；真实跨日复测应在自然日期改变后进行，不能推进时钟冒充。
