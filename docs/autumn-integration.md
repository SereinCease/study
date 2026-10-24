# 秋夜场景与正式伴读计时

## 组件接入

主应用 `AutumnScene` 使用已认可的四张本地素材，不依赖 iframe、远程模型或生图服务。`preferences` 接收现有 motion、scene3d 开关；`focus` 控制窗边构图。默认桌面动态、手机静态，手机用户可以主动启用。系统减少动态效果或用户关闭场景/动效时，Three.js 模块不加载。

`FocusOverlay` 使用原生 dialog，传入 open、onClose、spaceId、goalId、sessionId、taskTitle 和 preferences。目标与会话必须属于同一空间；若都提供，教学会话必须关联该目标。关闭弹窗恢复原焦点，不改变工作区草稿，也不暂停计时。

视觉采用黛蓝场景、暖白计时与暗金强调。页面使用的墨染星穹主题将正文放在静止的宣纸色面板上，不让阅读内容承受动态背景。

## 渲染与资源约束

- Three.js 与 Shader 按需独立加载。GPU 使用背景、人物、法线三张纹理，全屏合成一次 draw call；静态全景仅用于 HTML 回退。
- 维持原版人物 registration、腰部锚定、书卷/手掌保护与近似冷暖受光。坐垫接触阴影固定，不随胸肩呼吸漂移。
- 使用共享人物变形 UV 采样颜色、法线和透明度，避免边缘错位。环境层在角色合成之前绘制，因此落叶不会穿过人物。
- 持续动画以最多约 30Hz 调度，DPR 不超过 1.5。不承诺所有设备的实际帧率；没有增加后处理或粒子网格。
- 视口隐藏、场景离屏时取消 RAF；切回后从暂停的氛围时间继续。倒计时独立采用实际截止时间，不依赖渲染帧。
- 暂停保留当前人物姿态，轻柔档使用 0.45 倍幅度。减少动态效果使用静态稿。
- ResizeObserver 保持图像 3:2 像素比例，窗边构图变化采用 UV 区域插值；这仍是 2.5D 运镜，不声称三维空间推镜。
- 卸载和加载中取消会停止 RAF，移除观察器/监听器，销毁材质、几何体、纹理及 renderer。仍在返回的纹理加载完成后立即销毁。
- 纹理失败、WebGL context lost 或 Shader 编译失败回退静态；学习和计时仍可使用。

## 服务端计时

`GET /api/focus-sessions` 返回 `{session, server_time}`：优先活动计时，否则最近一条；没有记录时 session=null。

`POST /api/focus-sessions` 接收 `space_id`、可选 `goal_id/session_id`、`task_title`、`duration_minutes`（1–180）与幂等 `request_id`，创建 ready 状态。同一用户最多一条 ready/running/paused 记录，使用 SQLite BEGIN IMMEDIATE 串行化创建与修改。

`PATCH /api/focus-sessions/{id}` 接收 `revision` 和 action（start、pause、resume、reset、finish）。陈旧版本返回 409；已到时的暂停操作保留 completed，不意外重新启动。

running 保存 UTC deadline。暂停保存 remaining_seconds；恢复用当前时间加剩余秒数。计时结束记录 finished_reason=elapsed，用户提前结束记录 finished_reason=user 并保留真实剩余秒数。重开数据库不会重置计时。

前端仅在弹窗打开且存在活动计时时每 5 秒同步，并在窗口恢复可见时重新核对。离线显示本机恢复副本，但禁用状态修改，避免离线时从不同标签页创建两轮独立计时。旧样板 localStorage 不导入。

任何计时接口都不创建 attempt、assessment、mastery、review 或教学 event。退出、暂停、结束不能用作学习成绩。

## 验证记录

2026-10-23：`tests/test_focus.py` 6 项通过，覆盖实际截止时间恢复、暂停/继续、重开数据库、幂等创建与完成、并行唯一性、过期版本、多空间关联、恰好到时的暂停、提前结束和零学习证据写入。

JSX/运行时代码已通过 Vite/Oxc 解析。正式页面的移动/桌面浏览器与 WebGL 视觉验收应随主应用整合统一执行；本说明不以静态解析冒充浏览器验证。

整合构建补充：`npm run build` 已通过，场景 runtime 独立 chunk 约 11.30 kB（gzip 4.97 kB），共享 Three chunk 约 552.80 kB（gzip 138.32 kB）。纹理保持原始本地 PNG，不把 JS chunk 大小冒充全部场景资源传输量。
