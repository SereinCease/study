# Learnloop · 个人知识空间与 AI 学习教练

本地优先的个人知识库与 AI 学习教练。首发学习包：Python、pytest、Playwright。

真实流程为：导入资料 → 检索与引用 → 资料拆解草稿 → 采纳知识卡 → 诊断与精讲 → 实际代码运行 → 错题补练 → 间隔复习。默认个人数据为空，示例包需要点击导入。

## 快速启动

需要 Python 3.12、uv、Node.js 22.12+、npm；代码实训另需 Docker（macOS 可使用 Docker Desktop 或 Colima）。不使用 uv 时，也可用 `python3.12 -m venv .venv` 和 `.venv/bin/python -m pip install -r requirements.lock` 创建环境。

```bash
cd /path/to/learnloop
uv venv --python python3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock
npm ci
npm run build
# 新机器配置；已有 .env 时不要覆盖
cp -n .env.example .env
chmod 600 .env
.venv/bin/python -m scripts.start
```

打开 [本地工作台](http://127.0.0.1:8000)。`scripts.start` 同时启动 API 与持久化队列 worker，Ctrl-C 会关闭两者。仅启动 API 时任务会保持排队。

仓库不包含 API Key、个人学习数据和原始验收记录。请在本机 `.env` 或设置页配置自己的模型密钥，不要将 `.env` 复制到前端代码。开发依赖、模型缓存与数据库均由本机创建。

资料卡和知识卡支持右键或「⋯ → 添加关联笔记」，在右侧编辑器记录理解。笔记自动关联当前空间与真实来源，知识卡同时关联知识点；支持自动保存、本机草稿恢复和多标签页版本冲突检查。

### 开发模式

```bash
.venv/bin/python -m scripts.start
# 另一个终端；Vite 将 /api 代理到本机 8000
npm run dev
```

前端开发地址为 [127.0.0.1:5173](http://127.0.0.1:5173)。前端未硬编码 API 服务器地址。生产构建由 FastAPI 提供。

### 本地语义检索

```bash
.venv/bin/python -m scripts.download_model
```

下载 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 到本地数据目录。向量模型只在本机编码内容；按照模型分词窗口切分编码并聚合为归一化向量，原文保持完整用于引用。下载前全文检索可用，资料页会明确提示向量索引未就绪；下载后点击资料的「重建索引」。本机交付已下载并实测；共享源码包不包含约 476 MiB 的模型缓存。使用新的 `LEARNLOOP_DATA_DIR` 时需为该目录下载模型，或将其 `models` 目录链接到已有缓存。

### 构建实训镜像

```bash
docker build -t learnloop-runner:1 runner
```

交付验收镜像使用官方 Playwright **1.62.0** 浏览器镜像和同版本 Python 包。此机器已有 amd64 浏览器层，验证构建使用 `--platform linux/amd64`；新机器可按本机架构构建。不要混用不同版本的浏览器和 Python 包。

## 模型配置

在「设置」页面管理 Gemini、DeepSeek 或自定义兼容接口。当前按用户要求使用官方接口 `https://api.deepseek.com/chat/completions` 与模型 `deepseek-flash`。第三方网关的密钥和模型别名不能直接视为官方通用凭据。

官方四档真实 JSON 连接和完整学习闭环现已通过验收。认证诊断会显示 HTTP 状态、安全的上游错误码和请求编号，并区分密钥拒绝、非 JSON 网关拦截、地址重定向、配额和参数错误；不会记录原始错误正文。配置页显示密钥与地址的实际读取来源。当前真实验收状态见 `reports/verification-v3.md`，历史单次成功不代表当前凭据可用。

```dotenv
LEARNLOOP_LLM_PROVIDER=deepseek
LEARNLOOP_LLM_API_KEY=
LEARNLOOP_LLM_BASE_URL=https://api.deepseek.com/chat/completions
LEARNLOOP_LLM_MODEL=deepseek-flash
LEARNLOOP_LLM_TIMEOUT_SECONDS=120
LEARNLOOP_LLM_REASONING=standard
LEARNLOOP_LLM_MAX_OUTPUT_TOKENS=0
```

- 快速：关闭 thinking，4096 tokens；标准：low，8192；深入：high，16384；高强度：max，32768。预算包含推理消耗，`0` 表示采用档位默认。
- 优先级为本次请求 → 会话 → Skill → 全局。错题复盘未覆盖时默认深入；其他任务及摘要沿用全局，初始为标准。
- 环境变量优先于项目 `.env`；页面显示环境覆盖。密钥仅存 `.env` 或 `.secrets.json`，权限 `0600`，不返回浏览器。域名或供应商变更必须重新填写对应凭据。
- 每档连接测试使用短 JSON 请求，不含学习资料；记录参数、实际模型、接口、时长、用量及成功/失败。
- 网络重试和结构修复共用最多三次 HTTP 请求。认证失败不重试；截断、错误引用、无效结构不保存为成功教学记录。
- `.env` 保存后下次调用生效；启动环境变化需重启。接口地址使用完整 `/chat/completions` 路径。

官方参考：[Gemini 兼容接口](https://ai.google.dev/gemini-api/docs/openai)、[DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)、[Playwright Docker](https://playwright.dev/python/docs/docker)、[本地向量模型](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)。模型是否可用以当前账户实际连接测试为准。

## 三个教学 Skill

定义位于 `server/skills/`，不是可执行插件，不授予文件系统或工具权限。

1. **资料拆解**：按章节片段分批提取知识卡，校验引用，保存检查点。产物先为草稿；核对、编辑和采纳后才进入图谱。模型推断关系保留引用、置信度估计和待确认标记。同名定义有差异时保留版本供核对。
2. **概念精讲**：先诊断，再提供短讲解、例子和问题；记录原题、评分依据、回答、提示级别、模型和 Skill 版本。提示/看答案等动作保持当前题目，不单独计分。可从历史会话继续。
3. **错题复盘**：根据原题、原答案、预期与实际报告区分概念、应用、语法、断言、测试设计和环境问题。没有运行记录时只作静态分析。补练和后续复习是不同阶段；仅看答案不能纠正错题。

修改提示词会新增版本；已开始的会话保留原版本。模型配置可按 Skill 覆盖。

## 掌握度与复习

有效作答使用 `旧分数 × 0.7 + 本次表现 × 0.3` 更新；每级提示折减本次表现 0.08，最低为零。未作答、跳过、阅读、看完整答案、环境错误均不增加分数。接口请求 ID 用于幂等，worker 重试不会重复写入同一轮证据。

「掌握较好」还要求分数至少 0.8、至少两个不同日期的独立成功以及一次变形题成功。日期按 `LEARNLOOP_TIMEZONE`（默认 `Asia/Shanghai`）归属。复习默认首次有效练习后 1 天，跨日通过后为 3、7、14 天，失败重置到 1 天。纠正评价会从证据重新计算分数、复习安排和错题状态。

评分和 AI 分类是可解释的估计，不是能力认证。闪卡翻看不计分，可另外发起变形复测。

## 数据与结构

- `server/app.py`：本地 API、来源和会话令牌校验、生产静态页面。
- `server/db.py`：SQLite WAL、外键引用、FTS5、迁移版本；逻辑领域对象以带稳定 ID 的 JSON 实体保存，并通过引用表关联。
- `server/ingest.py`：Markdown、UTF-8 文本、文本 PDF、HTML、Python/ZIP 解析，章节/页码/行号引用、中文分词和混合检索。
- `server/models.py`：模型配置、调用、用量与结构化校验。
- `server/learning.py`：草稿采纳、教学状态、学习路径、掌握度和复习。
- `server/jobs.py`：SQLite 单 worker 队列、取消、重试与崩溃恢复。
- `server/runner.py`：独立 Docker 环境和实际报告解析。
- `server/domains.py`、`samples/domain.json`：可信领域包声明；只附带自动化测试包。
- `src/`：资料库、图谱、学习路径、教学会话、代码实训、复习、模型设置组件。
- `tests/`：单元/集成测试及实际容器安全验收。

默认数据位于 `server/data`；用 `LEARNLOOP_DATA_DIR` 指向其他目录。原文件使用内部 ID 保存，数据库保留原名、内容哈希和版本。升级迁移保留旧表，旧证据不完整时明确标记，不补造历史。默认不预填学习分数和学习记录。

主要接口：`/api/sources`、`/api/search`、`/api/ask`、`/api/concepts`、`/api/relations`、`/api/goals`、`/api/skills`、`/api/skill-sessions`、`/api/exercises`、`/api/jobs`、`/api/mistakes`、`/api/reviews`、`/api/model-profiles`。完整交互文档在本地 `/docs`。写接口需要先从 `/api/session` 获取本地会话令牌。

## 实训边界

每次执行由后端根据练习 ID 固定运行 pytest，不接受浏览器传入命令。临时练习目录默认位于项目的 `server/data/runner-workspaces`，确保 Colima 能挂载；可用 `LEARNLOOP_RUNNER_WORKSPACE_DIR` 指向 Docker 已共享的目录。容器非 root，无外网，输入只读；仅挂载练习目录，无 Docker socket、主数据库或 `.env`。

限制为 1 CPU、768 MiB 内存（无额外 swap）、128 个进程、60 秒、64 KiB 合并输出、128 MiB 临时目录、128 MiB 共享内存和 8 MiB 单文件。关闭容器日志持久化，限制输出洪泛。取消和超时会停止并删除容器，临时目录也会清理。Playwright 仅访问同容器内的 Checkout 练习站点。

JUnit 是学习代码生成的报告，不能当成可信的考试裁判。固定入口在退出前通过受限输出通道带回报告（最多 16 KiB，包含在整体输出限制内），避免 tmpfs 卸载后丢失报告。任务要求检查与实际运行结果分开显示，单纯退出码为零不等于任务通过。Docker 不可用时禁止回退宿主机运行。

网页导入限制公开 HTTP(S)，逐次校验重定向，连接固定在已验证公网 IP；ZIP 拒绝越界路径、符号链接和过大内容。扫描 PDF 明确要求 OCR，首版不包含 OCR 或视频转录。

## 验证与验收

```bash
.venv/bin/python -m pytest tests -q
RUN_SANDBOX_TESTS=1 .venv/bin/python -m pytest tests -q
# 下面会调用真实模型，请使用独立验收目录
LEARNLOOP_DATA_DIR=/tmp/learnloop-acceptance .venv/bin/python -m scripts.acceptance
npm run build
```

真实模型验收脚本使用自有示例教程，不读取私人资料；最新报告位于 `reports/acceptance.json`，后续运行同时保存带时间戳的 `reports/history/` 历史。容器测试覆盖正确/错误代码、超时、内存、输出洪泛、网络、宿主文件、只读输入、取消清理和完整 Checkout 项目。未启用实际容器测试时会显示 skipped，不把跳过当作通过。

本阶段最新验证见 [认证排查与设计交付报告](reports/verification-v3.md)；长期记忆与主题基础见 [V2 验收报告](reports/verification-v2.md)。`reports/verification.md` 等旧文件是上一阶段历史，不代表本次云端验收通过。

生成可分享源码包：

```bash
.venv/bin/python -m scripts.package
```

产物为上一级目录的 `learnloop-source.zip`，包含源码、锁文件、镜像定义、示例和测试报告。真实密钥与个人学习数据留在本机。

## 长期记忆与统一工作区

「学习档案」区分用户偏好、自述基础、长期目标和依据练习计算的能力状态。每条偏好有版本；历史版本保留用于解释当时上下文。自述、摘要和 AI 讲解不成为新的掌握证据。

教学上下文统一由 `server/memory.py` 组装：最近六轮、当前题目与评分依据、学习档案、有效会话摘要、相关资料及历史证据。输入约 12000 tokens（含约 2000 系统提示预算），显示保守估计值；先减少低相关检索和较早对话，当前作答与评分依据不会被静默截断。过大时明确拒绝并保留原文。

每新增八轮或待摘要内容估计达到 8400 tokens，后台生成摘要；也能手动更新或在「稍后继续」时整理。摘要带事件 ID、版本和覆盖范围；生成期间继续作答不会丢失。纠正评价、停用或删除证据会使相关摘要失效，失败保留原始对话和有效旧摘要。长期会话分批摘要，早期内容通过独立的历史 FTS/语义索引找回。

工作区在同一目标下提供讲解、作答、代码实训、错题与复习。桌面三栏，手机用目标/学习/证据标签切换。作答与代码草稿采用版本检查，冲突时保留本机输入并要求核对合并。浏览器本地恢复副本用于防止未完成保存时关闭页面；服务端草稿保存在 SQLite。

历史记录的「删除」为逻辑删除：立即退出教学检索、使摘要失效，删除作答会重算掌握度；原始记录仍留在本机审计数据及旧备份中，不是安全擦除。停用单条证据也不会再提供给模型。整个会话关闭长期记忆后仍能继续当前教学，但不进入跨会话检索。

## 主题与 3D

- **墨染星穹**：秋夜书斋与宣纸工作区，新用户默认。
- **案卷**：暖纸、暗红、衬线标题与档案入口，保留已有用户选择。
- **夜探**：炭灰、黄铜、局部台灯光影。
- **留白**：浅灰与白色平面工作区，不加载 3D 模块。

主题、字号、行距、密度、动效和 3D 开关分别保存；本机浏览器首屏先恢复主题，再读取服务端偏好。两套侦探主题共用 Three.js `0.186.1` 本地几何场景。 无生图调用、外部字体、远程贴图或模型下载。

档案夹继续学习，书本打开资料库，证据板打开图谱，笔记本打开复习，台灯切换专注。每个物件有等价 HTML 按钮。手机、关闭动效、系统减少动态效果或 WebGL 不可用时提供静态入口。只在交互、尺寸或可见性变化时绘制；没有空闲持续渲染循环。卸载释放场景、控制器、阴影、材质和 WebGL 上下文。动态模块 gzip 约 144 kB。

## 存储维护、迁移与恢复

详见 [存储与升级说明](docs/storage-and-recovery.md)。列表使用有索引的游标分页；只轮询活动任务。全文索引实时更新，已下载本地模型时 worker 每分钟检查一次待编码历史；也可手动重建。新历史向量先暂存、校验当前内容哈希与停用状态，再在事务中发布；失败保留旧有效索引。发布与清理通过文件锁互斥。

设置中的「存储与维护」支持统计、预览、执行及记录。自动清理缓存 7 天、日志 30 天、无活动引用异常工作区 24 小时、退役索引 7 天；90 天任务只压缩冗余请求字段。原始资料、作答、纠正、错题与运行报告不会被自动删除。不会执行全盘扫描或在线 VACUUM。

性能复测命令（需要已下载本地模型，独立临时数据库）：

```bash
.venv/bin/python -m scripts.benchmark
```

## 后续扩展

领域包描述知识分类、题型、评分方式和三个教学 Skill，可新增职业技能、认证、考试与兴趣内容。领域包不能扩大运行权限；新可执行题型需后端实现并经过独立安全验收。本版不包含考公考研题库、多人协作、计费、移动原生应用、任意网站测试或离线大模型。

## 水墨星空设计审阅

秋夜 2.5D 样板已整合到主应用首页与正式番茄钟；以下独立页面保留为设计审阅与资源迭代入口。四张随想模型实际生成的概念图与完整技术规范在 `design/ink-starlight/index.html`，包含六器物映射、可取消运镜、SVG 白描、伴学状态、抽屉与模态框、移动端及无动效回退。

```bash
.venv/bin/python -m http.server 4174 --bind 127.0.0.1 --directory design/ink-starlight
```

打开 [设计审阅页](http://127.0.0.1:4174/)。图中的课程与记录是视觉示例，不是个人学习档案。

安全诊断与独立验收：

```bash
.venv/bin/python -m scripts.diagnose_model
LEARNLOOP_DATA_DIR=.core/acceptance-live LEARNLOOP_ACCEPTANCE_REPORT=reports/acceptance-v3.json .venv/bin/python -m scripts.acceptance
# 使用同一个验收数据目录继续验证真实摘要与历史检索
LEARNLOOP_DATA_DIR=.core/acceptance-live .venv/bin/python -m scripts.memory_acceptance
```

诊断只向已配置域名发送简短测试，不跳转或尝试其他供应商；输出不含密钥和原始响应正文。验收需已配置有效密钥和可运行 Docker。


## v4 个人知识空间与日常学习

- 顶部选择空间，或新建自动化测试、考研、考公、通用兴趣空间。收件箱允许直接收藏；收藏与探索不会因为阅读、笔记或计时产生掌握度。
- 学习书桌与工作区共用当前目标和下一步推荐。目标、会话、代码草稿、运行报告与练习继承稳定归属，空间内复用知识点，空间间不合并成绩。
- 资料与收藏包含资料库、随手记、收藏夹。笔记支持 Markdown、公式、引用批注、900ms 自动保存、本机恢复副本、409 冲突合并、历史、回收站与附件导出。AI 整理先显示建议稿，采纳才修改。
- 知识图谱按局部邻居加载，默认20，上限80；AI关系仍需确认，前置环路拒绝，不把未知置信度显示为100%。
- 工作区「自有题目与自测」支持六题型、人工核对答案、评分规则版本、服务器计时。主观反馈标为AI估计，超时与正确性分开。
- 首页秋夜场景和窗边伴读共用本地素材；手机默认静态、桌面按需载入，退出销毁。正式番茄钟由服务器保存截止时间，只记录时间。
- Checkout 项目除正常实现外，要求测试发现两个固定缺陷。结果是有限任务验收，不是能力认证。

接口、领域包与迁移恢复见 [知识空间说明](docs/knowledge-v4.md)，场景见 [秋夜整合说明](docs/autumn-integration.md)，本轮实测见 [v4验收报告](reports/verification-v4.md)。

```bash
# 普通回归；默认跳过需要实际Docker的测试
.venv/bin/python -m pytest tests -q
# 本机Docker镜像就绪后，连同真实隔离测试一起运行
RUN_SANDBOX_TESTS=1 .venv/bin/python -m pytest tests -q
# 独立目录真实模型验收；使用测试资料和预设学习者回答，会消耗模型额度
LEARNLOOP_DATA_DIR=.core/acceptance-v4-new .venv/bin/python -m scripts.acceptance_v4
# 对指定实施前备份做临时目录恢复演练，不覆盖当前数据
.venv/bin/python -m scripts.verify_restore_v4
```

跨日保留效果需要真实后续使用才能验证，当前模拟时间规则测试和30轮模型回归不替代人的长期学习效果。
