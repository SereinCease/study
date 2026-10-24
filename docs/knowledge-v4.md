# Learnloop v4：知识空间、笔记与领域模板

本文对应本轮可运行实现；主应用数据默认保存在本机 `server/data`。模型辅助整理及主观题评价会调用设置中的模型，不是离线模型。没有附带完整考研、考公题库，也不声称自动获取最新考纲。

## 使用方式与边界

知识空间用于限定资料、目标、会话、证据与检索范围。领域模板决定适用的知识类型、练习类型和评价方式；空间的默认模式分为 `collect`（收藏）、`explore`（探索）、`learn`（学习）。三者不是同一个概念。

初始空间是 `inbox/general`（知识收件箱、收藏）、`automation/automation-testing`（自动化测试、学习）；`unassigned` 单独保留不能可靠确定归属的历史记录。空间使用稳定 ID，标题不是关联主键。跨空间共享一份原文通过 `source_ref` 引用，不拷贝正文；各空间收藏位置与同内容不同 URL 保存在引用的 `origins` 中。

收藏和笔记保存不会创建 Attempt、Mastery 或 Review。探索对话即使模型返回分数，也不会因此创建有效作答。只有进入学习流程、实际提交题目答案或执行练习，才形成评价记录；查看答案和环境故障继续受已有掌握度规则约束。AI 整理过的笔记保留编辑来源标记，不变成独立掌握证据。

### 四类内置模板

| 模板 ID | 用途 | 内容与评价 |
|---|---|---|
| `general` | 兴趣、知识收藏、读书及自由探索 | 用户资料与笔记；自由回答评价为 AI 估计 |
| `automation-testing` | Python / pytest / Playwright | 复用已有教学、隔离运行及项目任务 |
| `postgraduate` | 考研学习 | 用户导入科目资料、已核对题目与评分维度 |
| `civil-service` | 考公学习 | 用户导入行测/申论资料；客观题标准答案、主观题维度反馈 |

模板由后端可信注册表提供，导入资料不能安装插件、授予命令执行权限或自动获得 `isolated_runner` 工具。考试年份、地区和考纲版本字段用于记录适用范围，不会自动验证其是否为最新政策。

## 空间和笔记 API

所有路径以下均省略 `/api` 前缀。主应用继续验证本地请求来源，写请求需要 `x-learnloop-token`。默认前端 API 客户端处理会话令牌。

| 路径 | 行为 |
|---|---|
| `GET /spaces` | 返回空间数组 |
| `POST /spaces` | `{title, domain_id, mode, description}`；兼容 `default_mode` 名称 |
| `PATCH /spaces/{id}` | 带 `revision` 修改标题、描述或模式；不修改稳定 ID/领域 |
| `GET/POST /spaces/{id}/sources` | 列出/创建共享原文引用 |
| `GET /entries?space_id=&query=&deleted=&limit=&cursor=&goal_id=&session_id=` | `{items, next_cursor}`；查询当前空间及回收站状态，可按目标或会话筛选，过滤先于分页 |
| `POST /entries` | 创建笔记，完整字段见下文 |
| `GET /entries/{id}` | 当前版本、引用警告与附件元数据 |
| `PATCH /entries/{id}` | 发送变化字段及当前 `revision` |
| `DELETE /entries/{id}?revision=N` | 软删除；也可发送 JSON `{revision:N}` |
| `POST /entries/{id}/restore` | `{revision}` 从回收站恢复 |
| `GET /entries/{id}/revisions` | `{items, current_revision}`，历史保留完整 Markdown |
| `POST /entries/{id}/revisions/{N}/restore` | `{revision:当前版本}` 将历史内容写为新版本 |

创建笔记字段：`space_id`、`entry_type`（`note/excerpt/bookmark/ai_answer`）、`title`、`body`、`tags`、`goal_id`、`concept_id`、`session_id`、`references`、`memory_enabled`。标题可空，自动取正文首行，空白笔记使用“未命名笔记”。正文最多 200000 字、标签最多 30 个；关联记录必须属于同一空间。

`memory_enabled` 默认为 `false`。启用表示允许在相关目标或知识点的教学上下文参考笔记，不表示笔记属于权威资料或掌握证据。停用后，受影响的上下文与摘要退出检索，原始本地审计记录保留。已用于出题的内容失效后需要重新生成/核对当前题目，原作答草稿不应丢失。

编辑冲突返回 HTTP 409，响应 `detail` 包含 `message` 和 `current`。客户端约 900ms 防抖保存，必须等待上次保存结果更新 revision；网络失败时保留本机草稿，不能显示“已保存”。不要自动覆盖服务端新版本。

引用格式：`{kind:'source'|'chunk'|'entry'|'url', id?, url?, quote?, location?}`。内部引用保存 revision；资料引用同时保存 source_version 与内容哈希。来源变更或删除后，详情返回 `needs_review/reference_warnings`，原摘录保留且可继续编辑。不得用新建引用重新引入已删除或另一空间的内容。纯 URL 书签只记录链接，不会主动抓取正文。

### AI 整理、附件、集合

- `POST /entries/{id}/organize` 调模型产生独立 `entry_suggestion` 草稿，返回 `output.title/body/tags/changes`。正文不会自动覆盖。
- `GET /entries/{id}/suggestions` 返回建议记录；`POST /entries/{id}/suggestions/{suggestion_id}/accept {revision}` 明确采纳并生成新版本。笔记在生成后被改动时返回 409。
- `POST /entries/{id}/attachments` 使用 multipart `file` 和 `revision`。单文件不超过 10MB、每篇最多 30 个。文件按内部 ID 保存，PNG/JPEG/GIF/WebP 按固定文件头识别后允许图片预览；其余类型强制附件下载，SVG 不内联，不当成 HTML 页面执行。
- `GET /entries/{id}/attachments/{attachment_id}` 下载；`DELETE` 同路径加 revision 从当前版本移除。为历史恢复保留字节，删除记录不等于物理擦除所有历史及备份。
- `GET /entries/{id}/export` 输出 Markdown、元数据、历史与附件 ZIP。
- `/collections` 提供相同的分页、版本、软删除、恢复、历史接口；内容为 `{space_id,title,description,entry_ids,source_ids}`。集合只保存成员关联，删除集合不会删除笔记或共享原文。
- `GET /collections/{id}/export` 导出集合、成员笔记 ZIP 与资料元数据。整库导出 `/export/archive` 包含规范学习记录、原始资料和附件，不包含模型凭据配置。

## 试题、评分与用时

`/exams/questions` 支持创建、分页列表和详情；`PATCH /exams/questions/{id}` 带 revision。字段包括题型、题面、选项、参考答案、评分维度、满分、关联目标/知识点、引用、考试年份/地区/考纲版本、`is_transfer`、可选 `time_limit_seconds`。

题型为 `single_choice/multiple_choice/true_false/fill_blank/short_answer/essay`。客观题标准答案分别为选项 ID、选项 ID 数组、布尔值和填空答案。填空可表示为 `[['颜色','色彩'],['红','红色']]`：每个数组对应一个空的可接受答案。填空忽略大小写及连续空白；多选需答案集合完全一致，不自动给部分分。

题目保存或编辑后为草稿。`POST /exams/questions/{id}/verify {revision}` 表示用户已经核对答案或评分依据。未经核对不评分；主观题必须有维度，维度分数之和等于满分。改变题目会取消核对状态。`is_transfer` 来源于已核对题目定义，客户端提交不能自行宣布为变形题。

- `GET /exams/questions/{id}/practice` 返回不含答案和评分维度的练习题面，附 `active_practice`。
- `POST /exams/questions/{id}/start` 由后端创建或恢复同题同版本的活动练习，返回 `id/started_at/deadline/status`。刷新保留服务器开始时间。
- `POST /exams/questions/{id}/reveal` 显示答案并记录查看行为。
- `POST /exams/questions/{id}/attempts` 接受 `{answer,request_id,hint_level,answer_seen,practice_session_id?}`。同 request_id 幂等；同 practice_session 即使从多个标签用不同 request_id 提交，也只计一次。
- 客观题使用已核对标准确定性评分；主观题调用模型逐维度给依据、反馈与不确定性，标为 `ai_estimate`。无效模型维度或超界分数不会写入成功作答。
- 使用练习计时会记录 `elapsed_seconds/overtime/timing_source=server`。模型评分等待时间不计入作答用时。超时不自动交卷，不直接加减正确性分数；不使用计时的旧调用仍兼容。

此版本没有完整的套卷编排、考试报名/日程同步、自动收集真题、排名、录取预测或官方阅卷能力。考试与兴趣扩展是可用的资料及练习框架，不等同于现成课程和正版题库。

## 迁移 5、备份与恢复

启动 `db.init()` 幂等执行迁移 5：创建空间及目标查询索引、给 evidence 增加 space_id；建立默认空间；明确带自动化领域或能够追溯到已归属目标/会话的历史记录迁入 automation；其他记录保留 unassigned。不会根据中文标题、同名概念或成绩猜测领域。后续写入只有明确父关联才继承作用域。

实施前快照位于项目 `.core/backup-v4-20261023-205622`。已确认包含 `data/learnloop.sqlite3`、`data/originals/` 和配套源码；本说明没有读取私有密钥内容。该目录属于私有恢复材料，不进入对外分享包。

恢复应使用独立目录演练：

1. 停止 API 与 worker，先保留当前 v4 数据、原始资料、附件和私有配置的新备份。活动 SQLite 使用 backup API 或停止服务后整体复制，不能漏掉未落盘 WAL。
2. 回滚时配套使用快照中的旧源码和 `data/learnloop.sqlite3`，恢复 `data/originals/`；不要让旧代码直接打开升级后的数据库。
3. 恢复后端私有配置并保持 0600，安装对应依赖锁文件；本轮新增附件在旧快照中不存在，应单独保留，不能期待旧备份包含未来数据。
4. 设置独立 `LEARNLOOP_DATA_DIR` 和端口启动，核对原文、作答、引用、草稿、掌握度与复习日期，再决定切换日常目录。
5. 只有派生索引损坏时优先重建索引，不回滚原始学习证据。源码升级的空间迁移可重复执行，不会重复创建默认空间。

`/export/archive` 是可读的学习数据导出，不是当前版本的一键全系统导入/恢复入口。物理永久删除、跨设备自动同步及批量重新归属的完整管理流程尚未提供；软删除和停用不删除用户自己的旧导出或外部备份。

## 验证记录

新增测试覆盖作用域继承、引用与来源版本、草稿并发、回收站、历史/附件恢复、AI 建议稿采纳、客观题与主观题结构验证、服务端计时、多标签幂等及导出凭据排除。`tests/test_v4_audit.py` 跨模块覆盖目标推断空间、同名概念隔离、仅传会话的多目标草稿、停用笔记后的摘要/索引退出与当前题目失效、AI 内容不计作成功、附件导出及配置排除。

测试使用合成数据和模拟模型，不读取真实凭据。真实接口、Docker 与跨日复测结果应查本轮总验收报告；不能用这些单元测试代替真实跨日或真实隔离执行结论。
