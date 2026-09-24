# 24D / 24F / 24H 产品决策备忘（2026-09-24，待用户拍板）

> 这三项是 `PROJECT-PROGRESS.md` C 区中长期 BLOCKED 的**产品/信任模型决策**，不是代码缺陷。
> 每项给出选项、后果与当前负责人建议；用户选定前不做任何 fail-open 实现。

## 24D：外部程序 openURL 请求（`application:openURLs:`）是否可获前台

**问题**：Finder/其它本地程序可以请求 Browser CLI 打开 URL（`.webloc`、`open -a` 等）。
AppKit 的 `application:openURLs:` 契约**不承诺**该请求来自人类手势；来源不可证明。
当前行为（007 之后）：这类请求落入 `kUnknown` → fail-closed → 不上前台、不开窗（仅在已
HUMAN 的窗口内作为普通导航）。

| 选项 | 后果 |
| --- | --- |
| D1（建议，维持现状）| 外部 openURL 永不触发前台展示。安全性最强；代价是"双击 .webloc / 其它 app 唤起浏览器"只后台加载，想看需用户点 Dock/菜单。符合"AI/外部程序不得替用户决定前台"的产品原则 |
| D2 白名单式放行 | 仅对可证明人类来源的调用方放行。现状证据：macOS 公开 API 无法区分人类与程序化 openURL；除非引入用户确认 UI（每次弹原生对话框，违反"不弹窗"精神）或 Launch Services 私有标记（不可依赖），否则不可实现 |
| D3 全部放行 | 任一本地程序可使窗口上前台，等同 fail-open，破坏 Gate B"AI 不偷弹"的对外承诺；仅列出供完整性，不建议 |

**建议**：D1。将"外部 openURL 不上前台"记为**明确产品语义**写入开发计划 §3，关闭 24D。

## 24F：是否增加仅诊断用的 `kWebPage` producer 标签

**问题**：只读审计确认网页无法取得 `USER_EXPLICIT`/`INTERNAL`（安全上 fail-closed 已成立），
但页面发起的展示请求（`window.open` focus、`window.focus()` 等）目前落入 `kUnknown`，
日志无法区分"网页请求"与"完全未归因请求"。

| 选项 | 后果 |
| --- | --- |
| F1（建议）维持 `kUnknown` 合并 | 零改动。日志语义略粗：分析 deny 时需要结合 action/调用链判断。当前 Gate B/C 验收没有任何用例需要这个区分 |
| F2 增加 `kWebPage` producer | 在 `WebContentsDelegate`/`window.open` 路径 scoped 标注。收益是 deny 日志可归因；代价是多一个必须维护的信任边界（producer 标错=把网页请求误标为可信），且与"未归因即 automation"的简化模型相悖 |

**建议**：F1。若 Gate B 人工矩阵中 `kUnknown` deny 数量多到影响归因分析，再升级为 F2
（那是有证据触发的改动，符合"先证据后补丁"）。

## 24H：异步 origin 传播的信任模型

**问题**：thread-local origin 不跨 startup queue / ThreadPool / Profile callback。技术上**可以**
按值传播（010/012 已示范 `RunWithForegroundOrigin` seam），但"能传播"不等于"该信任"：
AppKit delegate 契约本身无法证明异步回调的来源仍是当初的用户手势。

**已达成的事实**（007/010/012 之后）：
- 默认 `kUnknown` fail-closed：传播链上任何断点都退回拒绝，不会 fail-open；
- 已证明的用户入口（AppKit 菜单 ×3、History 菜单、TabRestorer 延迟恢复、Omnibox
  context-menu）用**按值捕获 + 共享 seam** 精确传播，不会把 scope 泄给无关回调。

| 选项 | 后果 |
| --- | --- |
| H1（建议）维持"逐入口证明、按值传播" | 每个想获得强 origin 的异步入口单独审查调用链后加 seam；默认拒绝。安全边界清晰，进度可增量推进 |
| H2 全局 async origin 框架 | 建一套跨 TaskRunner 的 provenance 传递框架。工程量大、审核面广，且大部分异步入口根本不该被信任，收益有限 |
| H3 放弃异步归因 | 已实现的 010/012 语义回退，History/TabRestorer 用户入口重新被拒，违背"用户永远能打开" |

**建议**：H1，即维持现状路线。24H 从"BLOCKED 待决策"改判为"按 H1 模式逐入口推进"，
剩余工作变为普通的逐入口审计任务（如 `ActivateContents`、session restore、远程 app-shim）。

---

## 与本次构建的关系

以上三项**不阻塞**本轮统一构建与 Gate B 人工矩阵：007 的 `kUnknown` 默认拒绝正是三项
建议选项共同的安全基座。矩阵运行中若发现 D1/F1 导致真实用户路径被误拒，以证据回填
重开对应决策。
