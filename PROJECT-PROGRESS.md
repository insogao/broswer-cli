# Browser CLI 项目总进度表

> 本文件是项目持续调度与交接的唯一进度入口。宏观设计以
> [`docs/browser-cli-development-plan.md`](docs/browser-cli-development-plan.md) 为准，强制开发规则以
> [`AGENTS.md`](AGENTS.md) 为准。
>
> 定时跟进不得绑定某个 Phase：完成一项后更新证据并继续下一项。只有全部项目完成、用户明确暂停，
> 或出现必须由用户处理的阻塞时，才可以停止持续跟进。
>
> 用户设定硬截止：2026-09-24 00:00（Asia/Shanghai）。届时若项目未全部完成，停止实现任务、
> 在本表记录未完成状态与剩余工作并删除定时跟进；不提交或推送。
>
> 2026-09-24 用户将后续工作移交新的负责人：接手说明见
> [`HANDOVER-2026-09-24.md`](HANDOVER-2026-09-24.md)。动态状态仍以本表为唯一准据；旧自动跟进已按截止时间结束。

状态定义：`DONE` 已由主负责人复核；`REVIEW` 子代理完成、待主负责人复核；`DOING` 正在开发；
`TODO` 尚未开始；`BLOCKED` 有明确阻塞。没有可复核证据不得标为 `DONE`。

## 总览

| 工作流 | 状态 | 当前结论 | 下一动作 |
| --- | --- | --- | --- |
| 设计、不变量与开发纪律 | DONE | 状态机、来源模型、行为矩阵、Gate、测试/签名禁令已落盘 | 后续改动持续对照，不得绕过 |
| 可复现 Chromium 开发环境 | DONE | 固定 revision、Xcode 27、首次及增量构建均已验证 | 跨机器复现时重新执行环境门禁 |
| 产品身份、Logo 与 Developer ID 签名 | DONE | `Browser CLI.app`、固定 Bundle/Team/证书身份、inside-out 签名与验收已通过 | 发布阶段再做 Hardened Runtime、notarization、staple |
| 后台窗口绝不上屏基础能力 | DONE | Gate A 完整矩阵已由主负责人逐项复核：真实网络/Cookie、Tab/Window、window.open、Extension focus、session restore、启动/崩溃恢复均通过 | 后续改动持续跑 Gate A 回归，进入 USER_EXPLICIT/HUMAN Gate B |
| 用户显式打开与 HUMAN 所有权 | DOING | Dock 单次人工接管已观察通过：隐藏的 managed 隔离实例经用户 Dock 点击前台激活且未多开；policy 日志记录 HUMAN 授权。2026-09-24 扩展动作串进一步证实 HUMAN 窗口对 8 项 Extension 窗口操作全部 deny、native 保持 normal。统一构建+签名+121/121 单测已完成（001→014 全部首次编译），Gate B 人工矩阵待用户鼠标参与 | 以新签名构建执行 Gate B 人工矩阵（Dock 五时序场景、HUMAN 下用户 tab 操作、菜单/History/TabRestorer、交还；需用户参与），readiness checker 已修复（48/48） |
| Foreground Assist | REVIEW（编译+单测证据已到，仍缺 runtime） | 004/005/008/013 已全部首次编译并通过单测（assist 25/25、controller 14/14、lease 8/8、policy 32/32）；009 的选窗/单网关纯逻辑 26/26；011 REWORK 1 修复漏改调用方后编译通过 | 用新签名隔离 App 验证 grant/deny/busy/timeout/release/用户接管与冷启动 blocked 负例，再考虑 Gate C |
| 后台持续渲染与截图 | DOING（静态 checker 已复核；runtime 阻塞） | Gate C 仍无执行器、无真实截图；PNG filter 0..4 解码、所有 chunk CRC 与 IDAT 连续性现已由主负责人源码/反例复核（self-test 102/102、离线套件 229/229、hygiene 14/14、diff check clean），未编译、未运行 App | 用户关闭当前 App 并明确进入隔离验收窗口后，再按 Gate B/Assist 前置次序构建与做真实截图验收 |
| 后台多窗口并行 | BLOCKED（Gate B/C 前置） | 未授权的 Gate F driver 启动分支与 Python helper launch/CDP 执行器已删除；主负责人复核后离线脚本套件 223/223 PASS。没有 Browser CLI runtime 证据，Gate F 未通过。 | Gate B/Assist 与 Gate C 依赖解除且用户明确授权后，再单独恢复/验证 Gate F runtime；当前不保留可启动入口 |
| BrowserPilot 单插件接入 | TODO | 未接入 Browser CLI capability | 保持一个插件，同时兼容普通 Chrome 与 Browser CLI |
| 旧 Backlight workaround 清退 | TODO | 未开始 | 内核能力稳定后删除 show→hide、最小化补救等旧路径 |
| Release、安装、更新与迁移 | TODO | 开发 App 已生成；正式发布链未做 | 最后处理 release build、notarization、安装/更新/profile 迁移 |

## 逐项验收清单

### A. 基础与可复现性

- [x] 宏观设计、状态机、Origin、行为矩阵与 Gate 已版本化。
- [x] `AGENTS.md` 固化测试隔离、签名和禁止事项。
- [x] Chromium revision 固定，首次构建与真实 C++ 增量构建通过。
- [x] 脚本回归：63/63 PASS。
- [x] 固定产品名 `Browser CLI`、Bundle ID `com.jiangao.browsercli`、Team ID `RAXC5D3A3S`。
- [x] 固定 Developer ID SHA-1，禁止 ad-hoc fallback。
- [x] 批准 Logo 已生成并进入 App 资源。
- [x] 签名验收及签名后隔离 smoke 通过，无钥匙串/权限弹窗。

证据：[`docs/phase1-environment-2026-09-21.md`](docs/phase1-environment-2026-09-21.md)、
[`docs/signing-and-product-identity-2026-09-22.md`](docs/signing-and-product-identity-2026-09-22.md)。

### B. BACKGROUND_PRESENTATION / Gate A

- [x] managed Window 从创建时即带 native presentation suppression。
- [x] policy、host、bridge、AppKit 三层拒绝路径已实现。
- [x] Renderer JS 在桌面窗口数为 0 时执行成功。
- [x] 策略单测 5/5、AppKit 单测 2/2、GN 依赖检查通过。
- [x] 每次展示请求产生 `BROWSER_CLI_PRESENTATION` 结构化日志。
- [x] 真实 HTTPS 网站网络响应在后台完成并有内容证据。 — **DONE（2026-09-22 主负责人复核）**：
  已用签名 `Browser CLI.app` 单次隔离运行 `https://example.com/` 验证；DEVTOOLS url/title、
  NetLog 源站 `:authority: example.com` + `:status: 200` + `content-type: text/html`、
  策略 deny 日志、全程桌面窗口 0。测试：`tests/phase2-https-background-network.sh`；
  证据：`build-logs/phase2-https-20260922-140823/`（summary/netlog/devtools-list/window-samples）。
- [x] Cookie 写入、读取与隔离 profile 行为通过。 — **DONE（2026-09-22 主负责人复核）**：
  单次隔离测试 `tests/phase2-cookie-profile-isolation.sh`（一次调用、两个各自隔离的 mktemp profile）。
  **干净复跑（2026-09-22 15:03，主负责人授权）18/18 全 PASS**：同一临时 profile 写入并回读
  （DevTools 标题 `COOKIE_READ_<值>` + 服务器收到 `Cookie: bc_cookie=profA_…`），Cookie 行持久化落盘
  （`127.0.0.1|bc_cookie`，默认 `--commit-wait` 35s 后提交）；全新 profile B 不继承
  （`COOKIE_READ_ABSENT`、服务器无 Cookie、B 库无该行）；两次启动桌面窗口采样始终 0；策略日志
  suppress/deny 且无 internal allow；签名 + `--use-mock-keychain` + 独立 profile 均在实测 cmdline 中；
  无钥匙串/系统弹窗。历史失败证据保留于 `build-logs/phase2-cookie-20260922-142936/`；
  本次证据：`build-logs/phase2-cookie-20260922-150327/`
  （stdout.txt/check-results.txt/summary.txt/outcome.txt/rerun-notes.txt/cookie-server.log/
  devtools-list-a,b.json/window-samples-a,b.txt/cookies-a,b.{txt,db}/sha256.txt）。
  清理修复：测试服务器改为解析真实解释器并在清场时先杀子进程（已在无浏览器条件下验证）。
- [x] AI 创建普通 Tab：后台成功且不聚焦。 — **DONE（2026-09-22 主负责人复核）**：
  单次有效隔离运行（2026-09-22 17:07）CDP harness 成功：Tab 经 `Target.createTarget({newWindow:false})`
  加入 W1（windowId `784419391` 与 W1 相同）；真实页面内容（`BCLI_PAGE_tab`/`BCLI_BODY_tab` + URL）；
  PID on-screen 全程 0；策略 suppress + deny `show-and-activate`，BACKGROUND 无 internal allow。
  依主负责人判定，DOM `hasFocus=true`/`visibilityState=visible` 属 WebContents 内部 active/render 状态
  （后台持续运行需要），仅作信息证据；OS 不显示/不激活以 CGWindowList=0 + native policy deny/no-allow
  为权威。离线 reverified 门禁 **18/18 PASS**（`reverified-check-results.txt`）；原始
  `check-results.txt`（含 2 个已废弃 focus FAIL）保留不覆盖。证据：
  `build-logs/phase2-tab-window-20260922-170708/`（含 stdout/cdp-results/reverification-notes/sha256）。
- [x] AI 创建新 Window：内部存在且桌面不可见。 — **DONE（2026-09-22 主负责人复核）**：
  与上一条同一运行：W1/W2 为独立 BrowserWindow（windowId `784419391` vs `784419394`）、真实页面内容
  正确、PID on-screen 全程 0、策略 deny 且无 internal allow；DOM 焦点/可见性同上仅作信息证据。
  reverified 18/18 PASS，证据同上。
- [x] 网页 `window.open()`：后台创建且不 Show/Activate。 — **DONE（2026-09-22 主负责人复核）**：
  单次授权隔离运行（2026-09-22 18:12–18:15）**21/21 门禁 PASS**：真实页面 JS `window.open()`
  返回存活 child WindowProxy；新 child target/WebContents（`type=page`、`/page?name=child`、
  `BCLI_PAGE_child`/`BCLI_BODY_child`）；三向 opener 关系全部成立——父页可见 child href、
  子页 `window.opener.location.href` 指回父页、结构字段 `TargetInfo.openerId == parent targetId`；
  child windowId `750269482`（与父 `750269479` 不同，仅信息）；CGWindowList 对浏览器 PID 全程采样
  **max=0**；策略日志 suppress（含 popup widget 6 创建即抑制）+ deny `show-and-activate`（window 1/6）
  且 BACKGROUND 无 internal allow；DOM focus/visibility（True/visible）按主负责人标准仅作信息。
  签名 + `--use-mock-keychain` + 本次 mktemp profile 均在实测 cmdline 中，无钥匙串/系统弹窗。
  证据：`build-logs/phase2-window-open-20260922-181544/`
  （stdout.txt/check-results.txt/cdp-results.json/browser.log/page-server.log/summary.txt/
  run-notes.txt/sha256.txt）。
- [x] Extension `windows.create({focused:true})`：创建成功但 focus 被拒绝。 — **DONE（2026-09-22 主负责人复核）**：
  第二次（最后一次授权）单次隔离运行 **18/18 门禁 PASS**（2026-09-22 19:07）：
  `chrome.windows.create({url,focused:true})` → `ok=true`、`windowId=617439280`、`tabIds=[617439281]`、
  `windowType=normal`、`focusedFlag=False`；独立 CDP 证明被创建 target/WebContents：
  `type=page`、`/page?name=ext_child`、`Browser.getWindowForTarget` windowId `617439280`
  （与扩展自报一致，仅信息）；extension deterministic id `ajncjbdpkighindlpbpbjgpjgmaobcnm`
  经真实加载验证；实测 cmdline 含 `--use-mock-keychain` + 本次 mktemp profile +
  `--disable-extensions-except/--load-extension` 且**无 blanket `--disable-extensions`**；
  CGWindowList 对 PID 全程采样 **max=0**；policy suppress + deny `show-and-activate` 且无 internal allow；
  DOM focus/visibility 仅信息。证据：`build-logs/phase2-extension-window-focus-20260922-190733/`
  （stdout/check-results/cdp-results/browser.log/page-server.log/summary/run-notes/sha256）；
  首次失败证据（harness import 回归）保留于 `...-20260922-184407/`。
- [x] Extension `windows.update({focused:true})`：focus 被拒绝。 — **DONE（2026-09-22 主负责人复核）**：
  与上一条同一次运行：`chrome.windows.update(617439280,{focused:true})` → `ok=true`、
  同一 `windowId=617439280`、`focusedFlag=False`；native focus 仍以 CGWindowList=0 + policy
  deny/no-allow 为权威。证据同上。
- [x] Session restore：恢复内部结构但不得 native show。 — **DONE（2026-09-22 主负责人复核）**：
  单次授权隔离运行（2026-09-22 19:30）**20/20 门禁 PASS**。阶段 1（同一新 mktemp profile）：
  Window A（tabs `restore_a1`+`restore_a2`，windowId `1551580617`）与 Window B（`restore_b1`，
  windowId `1551580620`）建立且真实内容正确；关闭前写入 `Default/Sessions/Session_13434550228026217`
  （3389 B）；CDP `Browser.close` 干净退出（exit 0）；窗口采样 0。阶段 2（同 profile，
  `--restore-last-session --hide-crash-restore-bubble`，**不带** `--no-startup-window`）：
  三个 target 全部恢复、内容正确、分组一致（a1/a2 → `1551580685`，b1 → `1551580688`）；
  exit 0；窗口采样 0。两阶段 policy 均 suppress + deny `show-and-activate` 且无 internal allow；
  实际 `origins_seen=["internal"]`（`kSessionRestore` 枚举存在但生产者未贯通，仅信息）；
  DOM focus/visibility 仅信息。证据：`build-logs/phase2-session-restore-20260922-193032/`
  （stdout/check-results/cdp-results/browser-phase1,2.log/page-server.log/summary/run-notes/sha256）。
- [x] 多次启动、退出和崩溃恢复均无短暂上屏。 — **DONE（2026-09-22 主负责人复核）**：
  第二次（最后一次授权）单次隔离运行（2026-09-22 20:17）**23/23 门禁 PASS**：
  预置 app-identity watcher 实际 **interval=10ms**、总样本 **1255**、首启前基线 **39**、
  **max=0 / nonzero=0**（覆盖 spawn→cycles→crash→recovery 全程）；两轮 clean 周期真实内容
  （`BCLI_PAGE_cycle_N`/`BCLI_BODY_cycle_N`）且 exit 0；crash 前 Sessions **update=True 且
  连续 2s 无变化 stable=True**（timing-only，文件集合 4→4，不解析二进制 session 文件）；
  cmdline 校验同时含目标 app 二进制与本次 `--user-data-dir` 后对该**精确 PID** SIGKILL
  （exit -9）；同 profile + `--restore-last-session --hide-crash-restore-bubble`、不带
  `--no-startup-window` 恢复并读回 `BCLI_PAGE_crash_marker`/`BCLI_BODY_crash_marker`（exit 0）；
  全阶段 policy suppress + deny `show-and-activate` + BACKGROUND 无 internal allow；
  签名 + `--use-mock-keychain` + 同一新 mktemp profile 均在实测 cmdline 中；无 harness 错误、
  无钥匙串/系统弹窗、无可见窗口。证据：`build-logs/phase2-restart-crash-20260922-201728/`
  （stdout/check-results/cdp-results/watcher-samples/browser-cycle1,2,crash,recovery.log/
  page-server.log/summary/run-notes/sha256）；首次失败证据保留于 `...-200358/`。
- [x] Gate A 完整矩阵由主负责人复核。 — **DONE（2026-09-22）**：
  本节全部行为项均已基于隔离 runtime 原始证据逐项复核；测试始终使用 Developer ID 签名 App、
  `--use-mock-keychain` 和新临时 profile。权威 OS 判据为 native policy 日志与 CGWindowList；
  DOM focus/visibility 仅作 WebContents 内部状态信息。后续 Chromium 改动必须把 Gate A 作为回归门禁。

当前证据：[`docs/phase2-background-presentation-2026-09-22.md`](docs/phase2-background-presentation-2026-09-22.md)。

### C. USER_EXPLICIT / HUMAN / Gate B

- [ ] 来源归因总项（24A–24H；不得以单个开放式任务反复派发）。

  | 子项 | 当前状态 | 下一项可证伪条件 |
  | --- | --- | --- |
  | 24A USER_EXPLICIT producer | REVIEW | Dock/CLI show 及可见 ASSIST 接管在真实 Gate B 下只授目标 HUMAN |
  | 24B Extension producer | REVIEW | create/update/tabs 的跨异步 origin 不丢失，HUMAN 目标一律 deny |
  | 24C AgentAssist producer | REVIEW | grant/deny/busy/release/timeout 的隔离 runtime 与用户接管通过 |
  | 24D Unknown fallback | BLOCKED | 007 默认 Unknown/fail-closed 静态补丁仍 REVIEW；`application:openURLs` 无可靠的人类手势标记，不能 blanket 授 USER_EXPLICIT，需明确威胁模型/替代 UX |
  | 24E ChromeUserUi/native 用户入口 | REVIEW | 010 覆盖三个 AppKit 菜单与 TabRestorer；012 再覆盖 History 原生菜单直接/延迟两条路径。checker 自测 18/18、checkout 12/12；未编译、未运行 C++ 单测/runtime |
  | 24F WebPage attribution | BLOCKED | 只读审计未发现网页能取得 `USER_EXPLICIT`/`INTERNAL`；Show/Activate 落 `kUnknown` 并被拒绝，`kWebPage` 无生产 producer。安全上 fail-closed，但若要日志区分 WEB_PAGE 与 UNKNOWN 需单独定产品语义；暂不为标签而改多入口 |
  | 24G SessionRestore attribution | TODO | 恢复窗口后台硬拒绝，HUMAN 目标不受恢复动作影响 |
  | 24H async origin propagation | BLOCKED | 根因已确认：thread-local origin 不跨 startup queue、ThreadPool、Profile callback；虽可按值传播，但来源本身无法由公开 AppKit delegate 契约证明是人类手势，不能先传播后默认信任 |
  | 24I Omnibox Everywhere native activation bypass | REVIEW | 011 已交付（sha256 `b38a3abb…`）：managed 未归因 `kNoop` fail-closed（不 activate/deminiaturize/order-front）、managed 已证明浏览器进程用户输入经 `PresentForUserExplicitAction` 唯一 gateway、`OpenURLFromTab`（WebUI/渲染回调）固定 `user_gesture=false`、4 个原生 context-menu 命令 true、stock 保留 raw AppKit；静态接线 checker 6/6 + self-test 8/8、纯 route 单测已写未运行。未编译/无 runtime：需签名隔离人工验收 overlay 结果选择/回车的 fail-closed UX 缺口与跨 Space 等价性 |
- [x] 将当前 process-wide `kHuman` 改为 per-window 所有权；不得永久放行所有 internal show。 —
  **DONE（2026-09-23 主负责人复核）**：刷新后的 `001-managed-window-mode.patch`（sha256 `5e1bd1e4…`，
  23 文件 1698/8，固定 revision 上临时 index 顺序重放 001→002 通过）实现 per-window 所有权：
  策略以 `user_explicit_owned_window_ids_` 集合判定（`AllowPresentation`/
  `ReleaseUserExplicitOwnership`/`IsUserExplicitOwned`），`kHuman` 仅描述被决策的那个窗口，
  不再存在 process-wide 放行；回归单测 `UserExplicitOwnsOnlyThePresentedWindow` 证明一个用户窗口
  不授权任何其它窗口，窗口销毁即释放所有权（`window-destroyed`）；Dock/Launchpad/app reopen 经
  `PresentForUserExplicitAction()` 只对选中窗口授 USER_EXPLICIT/HUMAN，Extension/Tabs、延迟
  show/activate 归因延续、fullscreen/bounds/z-order/minimize/restore/maximize/deactivate 均按
  per-window + origin 门禁；HUMAN 窗口不重新武装 AppKit 抑制，用户原生操作不被阻断。
  复核证据：全量 `ninja chrome` exit 0、policy 12/12、AppKit 2/2、reopen 8/8、identity 与
  launch-hygiene 门禁通过，见 [`chromium/patches/README.md`](chromium/patches/README.md) 与
  [`docs/phase2-background-presentation-2026-09-22.md`](docs/phase2-background-presentation-2026-09-22.md)
  §追加 4（`build-logs/gate-b-compile-20260922-2307/`）。
  **剩余限制**：runtime/人工 Gate B 矩阵尚未执行——所有权与拒绝路径目前只有单测/静态证据，
  尚无真实 Dock/Launchpad 点击、HUMAN 隔离、Extension ops 拒绝的人工运行证据
  （`tests/phase3-human-ownership-manual.sh` 脚手架已备好但未运行）；`kAgentAssist` 无生产者，
  session restore、`ActivateContents`、远程 app-shim 仍未归因（001 内 TODO）。
- [x] 用户点击 Dock 可打开、激活既有 Browser CLI 窗口。 — **单次人工观察通过（2026-09-24；不代表 Gate B 全部通过）**：隐藏的 managed 隔离实例收到用户一次 Dock 点击后，用户确认既有窗口前台激活且没有多开；browser log 同时记录 `transition=human-ownership-granted window_id=1 reason=user-explicit-presentation` 与 `decision=allow action=show-and-activate origin=user-explicit window_id=1 state=human owned=yes`。Dock 请求本身没有 request-id 专属日志；本次来源归因依赖受控的“READY 后只单击一次”人工步骤，未验证 patch 006。证据目录 `build-logs/phase3-human-20260923-115339/`。收集器整体仍报 1 个 watcher contradiction（`max=3`、6,185,540 samples）：该 watcher 从前一日持续运行，app-wide 样本跨 profile/会话且不能归因；不能把此 max 当本次瞬态，也不能据此判本项失败。旧 watcher/sink 已由精确 PID 停止，原始 samples/log/report 保留，浏览器本身未关闭。
- [ ] Launchpad/App reopen 可打开。
- [ ] 菜单栏“打开浏览器”可打开。
- [x] CLI 显式 `show` 可打开指定目标。 — **REVIEW（2026-09-23，子代理完成、待主负责人复核）**：
  非 runtime 集成完成：CLI `show` 纯分类器 + ObjC++ 共享展示路径冻结为
  `chromium/patches/003-user-explicit.patch`（9 文件，597 insertions / 78 deletions，
  sha256 `9852de6570056b0f4d5bc72749e9dc17d54fb42d7e50f03017db54f8d8192e43`；固定 revision 上
  用临时 index 重放 001→002→003 与已验证 checkout 的所有 tracked 文件逐字节一致）；
  主负责人证据 `user_explicit_show_unittests` 14/14 PASS、policy 12/12、AppKit 2/2、reopen 8/8、
  `autoninja -C out/Dev chrome` exit 0；runbook 增加用户执行的既有窗口/零窗口直连 show 用例
  （命令只打印、绝不代运行），collector 按 producer handled/denied/failed 分类并有确定性静态
  fixture（`tests/run-script-tests.sh` 149/149 PASS）。**尚无真实运行的可见验证（用户执行
  show/人工 Gate B）**，通过前不得转 DONE。
- [ ] Browser CLI 已运行但零可见窗口时，用户仍能打开。
- [ ] 用户处于 HUMAN 时，AI 不得隐藏、最小化、切换或夺回该窗口。
- [ ] 用户“交还 AI”后，目标回到 BACKGROUND 且不短暂闪现。
- [ ] HUMAN 窗口存在时，AI 仍可创建互不影响的后台 Window/Tab。
- [ ] Gate B 人工矩阵：AI 永不偷弹 + 用户永远能主动打开，两者同时通过。

### D. Foreground Assist

- [x] **Slice 1：策略层 lease（非 runtime）** — **REVIEW（2026-09-23，子代理完成、待主负责人复核）**：
  `ForegroundLease` 数据结构 + `login`/`captcha`/`confirmation`/`other` 原因枚举；policy 单实例
  per-window lease（`RequestForegroundAssist` 以显式 requester origin 校验且仅 `kAgentAssist`
  可获批、busy/invalid/HUMAN-owned 拒绝、同窗口幂等不续期；`ReleaseForegroundAssist`；惰性 TTL
  `ReleaseExpiredForegroundAssistLeases`；`IsForegroundAssistLeased`）；`AllowPresentation` leased
  分支（agent 仅 `show-and-activate`/`show-inactive`/`set-bounds`，internal 允许，
  extension/web-page/session-restore 拒绝）；kUserExplicit 接管**先释放 lease 再授 HUMAN**；
  日志 `state=assist leased=yes` 且 `owned=yes` 仅表示 HUMAN；TTL 半开区间
  `[granted_at, expires_at)`。冻结为 `chromium/patches/004-foreground-lease.patch`（7 文件
  1070/13，sha256 `4fb2bd2a…`，全新临时 index 顺序 001→002→003→004 每步 `--check` 后
  `git diff --quiet` rc=0）。证据：lease 单测 **8/8 PASS**、policy 单测 **27/27 PASS**（既有
  12 项无回归）。**无 producer/host 接入/计时器/runtime 证据，未构建 chrome、未 commit**；
  不得据此把 D 区其余条目转 DONE。详见
  [`docs/phase2-background-presentation-2026-09-22.md`](docs/phase2-background-presentation-2026-09-22.md) §追加 6。
- [ ] `requestForeground` API 和 `ForegroundLease` 数据结构。
- [ ] 原因枚举：login、captcha、confirmation、other。
- [x] **Slice 2：producer + host 接入 + 超时计时器（非 runtime）** — **REVIEW（2026-09-23，子代理完成、待主负责人复核；
  已按主负责人两轮复核意见完成两次修正）**：
  冻结为 `chromium/patches/005-foreground-assist-integration.patch`（sha256 `7517c427…`，15 文件
  1490/6；8 新增 + 7 修改；历史作废版本 `0dc439aa…`、`3665b71d…` 记录保留于证据文档）。范围与验证：
  - 纯分类器 `foreground_assist.{h,cc}`：request/release 双开关、固定 reason 词表、严格十进制
    uint64 target（0/负数/溢出/空白/十六进制拒绝）、launch-args/`--app`/`--app-id`/`--incognito`/
    `--enable-automation`/`--headless` 及与 USER_EXPLICIT show 冲突全部 fail closed；
    **任何 tab switch 一律拒绝（`kBlockedByUnsupportedTabTarget`，不支持 tab 级目标，绝不表面声称
    指定 tab 获批）**；**所有 assist 意图（含 blocked）一律 consumed
    （`ForegroundAssistRequestClassConsumesCommandLine`）**；**冷启动 sanitizer
    `CommandLineForColdStartStartup()`：有 assist intent 时去掉 URL 位置参数与
    `--app/--app-id/--incognito/--enable-automation/--headless` 并追加 `--no-startup-window`，其余
    开关保留；无 assist intent 时字节不变**；单测 **17/17**。
  - 超时控制器 `foreground_assist_controller.{h,cc}`：唯一 `OneShotTimer`，超时只回退被跟踪的
    **单个**窗口；HUMAN-owned、已销毁、已被 release 的窗口一律 no-op；**ASSIST→HUMAN 接管显式
    取消跟踪与计时器（`OnLeaseReleasedByUserTakeover`），用户交还后旧 deadline 绝不 hide**；
    agent release 亦按窗口匹配取消；单测 **10/10**（含“接管→交还→旧 timer 不作用”回归）。
  - macOS 集成 `foreground_assist_presentation.{h,mm}`：冷启动入口在
    `StartupBrowserCreator::Start` **之前**用 sanitized 启动命令行（无 URL/app 副作用 + 后台模式），
    之后由 assist handler 对原请求拒绝/记录；ProcessSingleton 入口为 handler-first + consumed 返回，
    两条入口语义一致；只在 policy `kGranted` 后经 `NativeWidgetMacNSWindowHost` 单实例入口
    present 目标窗口；**`target_window` 必须是顶层 BrowserWindow（host `IsEligibleForegroundAssistTarget`
    + `FindBrowserWithWindow` 双校验），非顶层 widget/dialog fail closed**；`target-window-not-found`/
    HUMAN-owned/busy/invalid 全部拒绝并输出 `BROWSER_CLI_FOREGROUND_ASSIST stage=producer` 日志；
    release 仅处理匹配 lease 且 hide 前再次校验非 HUMAN。
  - host 新增 `GetFromWidgetId`/`IsEligibleForegroundAssistTarget`/`RequestForegroundAssistLease`/
    `IsForegroundAssistLeased`/`ReleaseForegroundAssistLease`/`PresentForAgentAssistAction`/
    `HideForForegroundAssistRelease`（hide 前重新武装 AppKit 抑制；析构释放 lease）；
    `user_explicit_presentation.mm` 在 USER_EXPLICIT **呈现成功后**才取消 assist 计时器（呈现失败则
    保持 tracking，控制器仍有 ownership 兜底）；`ui/views/BUILD.gn` 暴露 lease 头给 chrome。
  - 复核修正 1/2：①`foreground_assist_unittests` 缺少直接 `:user_explicit_show` GN 依赖已修复；
    ②接管取消计时器、blocked assist 强制 consumed、tab 目标显式拒绝、target 限顶层 BrowserWindow；
    ③冷启动时序：新增 sanitizer 单测 + 静态门禁
    `tests/check-foreground-assist-cold-start-order.sh`（**7/7 PASS**：Start 只接收 sanitized 行、
    不再接收原始进程行、upstream `--no-startup-window` 的 silent_launch 早退早于 `LaunchBrowser`、
    ProcessSingleton handler 先于普通处理并 return），并把接管通知改为呈现成功后。
  - 证据：静态重放 001→005 后 `git diff --quiet` rc=0（全部 tracked 文件与 worktree 逐字节一致）、
    42 个 patch-series 文件路径级比对 0 差异、两次独立提取 sha256 一致、`git apply --check -R` 通过；
    单测 assist **17/17**、controller **10/10**、lease 8/8、policy 27/27、回归 reopen 8/8、CLI show
    14/14、AppKit 2/2；`ninja -C out/Dev chrome` exit 0（增量编译并重链接 `libchrome_dll.dylib`）。
    证据目录 `build-logs/foreground-assist-slice2-20260923/`（本轮追加 `coldstart-fix/`），chrome 日志
    `build-logs/foreground-assist-slice2-coldstart-chrome-20260923-103510.log`。
  - **未完成/未证明（REVIEW；不得宣称 runtime fail-closed）**：无任何浏览器 runtime 或可见 UI 测试。
    当前 App 已于 2026-09-23 11:35 由固定 Developer ID 完成整包签名，`verify-signature.sh`
    39 PASS / 0 FAIL；证据在 `build-logs/signed-build-20260923-113050/`。冷启动"assist 不回落普通 launch"目前只有源码级证明（sanitizer 单测 + 静态门禁 +
    upstream 顺序断言），**未 runtime 验证**；agent 身份未与 native host 会话绑定（进程内任意
    `kAgentAssist` 调用者可用同一 lease 机制）；远程 app-shim 窗口 fail closed。不得据此把 D 区
    其余条目转 DONE。
- [ ] 只显示获批目标 Window/Tab，不连带显示其它窗口。
- [ ] grant、deny、busy、timeout、release 全路径。
- [ ] 用户操作 Assist 后升级为 HUMAN。
- [ ] 用户接管后 AI 无权自动 release/hide。
- [ ] Agent 崩溃时安全恢复且不抢用户焦点。
- [ ] 每次授权、拒绝、释放都有可归因日志。

### E. 后台 compositor、持续运行与截图 / Gate C

- [ ] BACKGROUND 下 JS、timer、网络不进入不可接受的限流/冻结。
- [ ] 截图不调用 Show/Activate/Reveal/最小化循环。
- [ ] 静态 HTML 截图。
- [ ] 动态页面与 CSS animation 截图。
- [ ] Canvas 截图。
- [ ] 视频页面截图。
- [ ] 长页面截图。
- [ ] 连续多次截图稳定性。
- [ ] surface 不可用时返回 `CAPTURE_UNAVAILABLE`，不得弹窗补救。
- [ ] Gate C 由主负责人复核。

### F. 多窗口并行

- [ ] 至少 3 个后台 BrowserWindow 并行加载真实网站。
- [ ] 每个 Window 独立 active tab、Cookie/session 语义符合设计。
- [ ] 每个 Window 独立截图且不会互相抢焦点。
- [ ] 并行期间桌面 Browser CLI 窗口数始终为 0。
- [ ] HUMAN/ASSIST 目标与其它后台窗口相互隔离。
- [ ] CPU、GPU、内存与长期运行数据记录完成。

### G. BrowserPilot

- [ ] capability 握手及协议版本。
- [ ] 普通 Chrome 无 capability 时走 Generic Chrome 路径。
- [ ] Browser CLI 下启用 backgroundWindows/backgroundCapture/parallelWindows/foregroundAssist/hardFocusIsolation。
- [ ] BrowserPilot 仍为同一个可独立发布的 Chrome 插件，不复制第二套代码。
- [ ] 覆盖当前实际支持的网站集合，避免回退为少数站点。
- [ ] 登录、验证码、人工辅助完整联调。

### H. 清理与正式发布

- [ ] 删除旧 Backlight 的 show→hide、capture reveal、normal→minimized、AppKit hide retry 等补救路径。
- [ ] watchdog 仅负责发现不变量违反、保存证据并 FAIL，不再自动修复后算成功。
- [ ] Release 非 component build。
- [ ] Hardened Runtime、notarization、staple。
- [ ] 固定安装入口、Dock/Launchpad 图标、单实例语义。
- [ ] update、packaging、profile migration。
- [ ] 干净机器从 GitHub checkout 到可开发/构建的交接演练。
- [ ] macOS 完整人工验收；Windows 需求仅列后续规划，不阻塞当前 macOS 交付。

## 当前调度指针

### OpenCode 工作线与下一步（动态状态，定时提示词不复制）

- **当前状态（2026-09-23 15:57 UTC）：** 本轮代码、测试、仓库状态均无变化。OpenCode CLI 无实现任务；Browser CLI PID `12828` 和只读 watcher PID `12068` 仍运行，没有 build/runtime。PNG 静态证据仍为 self-test **102/102**、离线套件 **229/229**、hygiene **14/14**、`git diff --check` clean。24D `application:openURLs:` 的信任边界仍 BLOCKED；Gate B/Assist/C/F runtime、BrowserPilot 与发布清单未完成。距离用户午夜（16:00 UTC）硬截止约 3 分钟；若届时仍未全部完成，下一检查按用户指令登记“按截止时间结束，未完成”及剩余项，并删除本 heartbeat 自动化。未触碰 Browser CLI App 或 watcher。

  主负责人复跑全量离线脚本套件 **229 passed / 0 failed**：[`build-logs/script-tests-offline-20260923-231200-CST.log`](build-logs/script-tests-offline-20260923-231200-CST.log)；launch hygiene **14/14**：[`build-logs/gate-c-png-hygiene-20260923-2315-CST.log`](build-logs/gate-c-png-hygiene-20260923-2315-CST.log)；self-test 原始记录：[`build-logs/gate-c-png-selftest-20260923-2315-CST.log`](build-logs/gate-c-png-selftest-20260923-2315-CST.log)；CRC/IDAT 独立反例记录：[`build-logs/gate-c-png-chunk-crc-idat-20260923-230742.log`](build-logs/gate-c-png-chunk-crc-idat-20260923-230742.log)；`git diff --check` PASS。W3C PNG Third Edition §5.5/§5.6/§11.2.3 要求每 chunk CRC 覆盖 type+data，且 IDAT 可多块但必须连续（[规范](https://www.w3.org/TR/png-3/)）。OpenCode 侧全量套件调用曾遇到 `/tmp/*` external-directory 权限拒绝；由主负责人在本机直接复跑相同静态套件并通过，未触发系统权限或钥匙串交互。

  这只验收 PNG 离线判定器的静态结构；Gate C 仍不能标 DONE：没有隔离执行器、真实 Browser CLI 截图或后台动态渲染证据。Browser CLI PID `12828`（启动参数 `--use-mock-keychain --user-data-dir=/tmp/browser-cli-manual-profile`）和只读 watcher PID `12068` 仍运行；两仓库 HEAD 仍为 browser-cli `42b0661`、Chromium `7d0f28c30d`，未触碰 Chromium 源树，未 build/GN/codesign/runtime/commit/push。下一个能改变状态的动作需要用户先关闭当前 App 并明确开放隔离验收窗口；午夜硬截止前仍须逐项完成并以人工证据验收，否则按用户要求记未完成并停止。

- **当前状态（2026-09-23 14:42 UTC）：** 013 复核后无新代码/测试结果；Browser CLI PID `12828` 与被动 watcher PID `12068` 仍运行，不能构建、codesign 或运行 App。为不空等，我已完成 Gate C PNG checker 根因复核并将一个不涉及浏览器的静态任务交回同一 OpenCode A 会话 `ses_f394d9f37ffeUSnmiKYyA35iCM`（模型/强度固定 `opencode-go/deepseek-v4.1-flash` / `max`，CLI exec session `11015`，DOING；不与 013 文件重叠）。

  已确认 `tests/tools/capture-evidence-check.py::read_png()` 对 IDAT 解压字节直接计数，而 PNG scanline 前缀 filter byte 及 Sub/Up/Average/Paeth 变换后的值不等于像素；当前 fixture 只覆盖 filter 0，故不能排除 uniform 图像假非空。主负责人查阅 W3C PNG Third Edition §9（[规范链接](https://www.w3.org/TR/png-3/#filtering)）：filter method 0 必须按每行类型 0–4 重建；定向任务要求只支持有证据的截图 IHDR 子集、unsupported fail-closed，并以独立 forward-filter fixture 覆盖全部 filter 与 malformed 输入。该 PNG 缺陷首次进入实现，返工 0；watcher 的零样本缺陷是另一验收点，已记录返工 2 次且其新证据合同此前独立静态复核通过。

  A 当前只可改 Gate C 离线 checker/静态 fixtures/文档；明确禁止 `--run`、浏览器/App/UI、build/GN/codesign、钥匙串/权限、安装、commit/push。当前用户 App 运行中，不并行派发其它改动；OpenCode CLI exec `11015` 结束后由主负责人复核。patch 013 的仲裁实现与调用链仍通过主负责人源码复核：
  `ArbitrateBrowserCliCommandLineIntent()` 先识别 request/release，再判断纯 show；两个生产入口都走同一
  仲裁；冲突请求经 assist classifier 得到 `kBlockedByConflictingSwitches` 并被 producer 记录 deny/消费，
  ProcessSingleton 路径无条件 return，冷启动路径使用已净化的无窗口启动参数。独立复跑 checker 自测
  **6/6**、真实 checkout **3/3**、browser-cli 和 Chromium 目标文件 `git diff --check` 均 PASS；patch SHA-256
  与记录一致，`/tmp/bcli-suite-final.log` 保存的离线套件结果为 **227 passed / 0 failed**（此轮未重跑，引用上轮原始记录）。CLI exec
  session `28273` 已退出 0，当前没有 OpenCode CLI 实现进程；OpenCode 桌面 helper 进程不代表任务仍在运行。
  **013 仍为 REVIEW**：没有编译、GN、codesign、C++ 单测或浏览器 runtime 证据。Browser CLI PID `12828`
  与 watcher PID `12068` 仍运行（本次观察仍在运行），因此本轮没有 build/runtime；下一步需要用户关闭该 Browser CLI 并明确进入
  隔离验收窗口，然后才能串行构建和执行 Gate B/Assist 人工矩阵。

  主负责人随后完成 patch 012 的源代码、负例和证据复核。History
  checker 第 3 版修复了前两轮发现的两个假通过：直接与延迟 producer 分支分别断言；scope 必须处于
  顶层词法范围并覆盖 `RestoreByID` 与 `Navigate`，`history-scope-only-session-branch` 负例现正确失败。
  独立结果：`--self-test` **18/18 PASS**、真实 checkout **12/12 PASS**、reverse `git apply --check`
  与 browser-cli `git diff --check` PASS；patch 012 SHA-256
  `d68f34f764aaeed74df7c6a3192e6b3a895b4c346a2fb4f1987d1f01a2475d79`，仅 1 文件、16+/4-。
  最后一次全量离线套件于 21:25 CST **223 passed / 0 failed**：
  [`build-logs/script-tests-offline-20260923-2125-CST.log`](build-logs/script-tests-offline-20260923-2125-CST.log)。
  该 History checker 同验收点累计返工 2 次，首次发现 2026-09-23 13:22 UTC；主负责人已按根因
  定向修正并复核，不再重复派发该点。patch 012 仅静态证据，未编译/gn/codesign/运行 C++ 测试或 App；
  24E 仍 REVIEW。随后已在原 OpenCode A 会话
  `ses_f394d9f37ffeUSnmiKYyA35iCM` 下达新的互不重叠 P0 任务 013；固定
  `opencode-go/deepseek-v4.1-flash` / `max`，CLI exec session `28273`、本机 CLI PID `20552`，**已完成，exit 0**。
  013 已由 A 交付静态修复（本 P0 首次修复，REWORK 0）：`ArbitrateBrowserCliCommandLineIntent()`
  在两入口接线（assist case 无条件消费、show case 保留条件回落）、8 个纯逻辑单测、静态 checker
  `tests/tools/command-line-intent-arbitration-check.py`（self-test 6/6、真实 checkout 3/3、
  suite 第 29 组 4 项）、patch 013 SHA-256
  `bcf96d316fc5d3e0987dc022c396434b7918e05ddc074893bade9bfa6e305757`（4 文件 45+/31-，
  BYTE_IDENTICAL、前向 `--cached --check`、反向 `-R`、重放 `git diff --quiet` 全通过；001–012
  sha256 未变），离线套件 **227 passed / 0 failed**、hygiene 14 入口 OK；未编译/未 gn/未签名/
  未运行 C++ 单测或 App；主负责人已完成源码与静态证据复核，因缺编译/runtime 仍为 REVIEW。
- 新的 P0 根因（主负责人于 21:27 CST 直接审查 `chrome_browser_main.cc`、`user_explicit_show.cc`、
  `foreground_assist.cc`）：`ProcessSingletonNotificationCallbackImpl` 先调
  `HandleUserExplicitShowRequest` 再调 `HandleForegroundAssistRequest`；show classifier 不检查 assist
  intent，而 assist classifier 虽会把 show+assist 判作 `kBlockedByConflictingSwitches`，在 show handler
  已消费后根本到不了。冷启动 `PreMainMessageLoopRunImpl` 的 BrowserCreator 返回后也同样先执行 show
  再执行 assist；assist sanitizer 保留 show switch 并加 `--no-startup-window`，所以组合请求可能绕过
  既有冲突拒绝并呈现窗口，错误把 AI command-line intent 当作用户前台授权。最小安全方向：两入口共用
  单一命令行意图仲裁（assist intent 优先消费，冲突时由 assist 逻辑记录 deny，不调用 show；纯 show 与
  普通 assist 语义不变），加入 producer 顺序/冲突反例静态门禁与纯逻辑回归。用户未授权编译或 runtime，
  且 Browser CLI PID 12828 + watcher PID 12068 仍运行；本任务只允许源代码、patch 013、无浏览器静态
  证据，禁止 build/gn/codesign/runtime/UI/安装/commit/push。Chrome upstream 的 ProcessSingleton
  callback 会将通知 post 到当前 task runner 后再处理命令行（[官方源码](https://chromium.googlesource.com/chromium/src/%2B/master/chrome/browser/chrome_browser_main.cc)）；
  意图优先级仍须由 Browser CLI 在这个消费边界显式仲裁，不能依赖函数偶然顺序。
- OpenCode A 的 patch 013 实现任务已完成；主负责人源代码/静态复核完成，状态仍为 `REVIEW`，不得重复
  派发同一实现。剩余验收是隔离签名构建、C++ 单测和用户许可的 Gate B/Assist runtime；Browser CLI
  PID `12828` 与 watcher PID `12068` 仍运行，当前不允许 build/runtime。任务文件范围、验收门禁与子代理
  实现记录见下方 patch 013 交付记录。
- patch 013 交付记录（A，2026-09-23，REWORK 0）：修改 `chrome/browser/browser_cli/foreground_assist.h`
  （`BrowserCliCommandLineIntent` 枚举 + 仲裁声明与合同注释）、`foreground_assist.cc`（唯一仲裁纯函数，
  assist intent 无条件优先）、`foreground_assist_unittest.cc`（8 用例：request/release 分别压过 show
  开关、纯 request/release、纯 show、普通命令行、仅辅助 assist 开关为 ordinary、冲突
  `kBlockedByConflictingSwitches` + consumed 不变量）、`chrome/browser/chrome_browser_main.cc`
  （两入口同一 switch；assist case 无条件消费，show case 保留条件回落，ordinary 直落）；
  thin repo 新增 `tests/tools/command-line-intent-arbitration-check.py` 与 `run-script-tests.sh`
  第 29 组，同步 `tests/README.md`、`chromium/patches/README.md`（013 行 + 校验段 + 重放命令）。
  证据：checker `--self-test` 6/6（show-first 旧顺序、单入口、show-在仲裁前、case 互换、
  assist case 缺 handler 全部 FAIL）、`--checkout` 3/3、离线套件 227 passed / 0 failed、hygiene
  14 入口 OK；patch 013 提取校验（BYTE_IDENTICAL / 前向 / 反向 / replay / 001–012 未变）见
  patches README。13:57 UTC 主负责人已独立复核仲裁函数、冲突分类/消费路径、两生产调用点与静态 checker；
  复跑 self-test 6/6、checkout 3/3、目标文件 `git diff --check` PASS。未执行 build/gn/codesign/runtime/
  UI/安装/commit，无 C++ 或 runtime 证据，故状态仍为 REVIEW（不是 DONE）。
- Gate C PNG checker 修复已由 A 交付静态证据（2026-09-23，本缺陷返工 0）：`tests/tools/capture-evidence-check.py`
  的 `read_png()` 改为标准子集解码 `decode_png()`——校验 IHDR（合法 color type/bit depth/compression/filter/
  interlace）、IHDR/IDAT/IEND chunk 结构与 zlib 流、scanline payload 精确等于 `height*(1+width*channels)`、
  filter byte 0..4，随后按 W3C PNG Third Edition §9 逆变换（Sub/Up/Average/Paeth，bpp 按通道、模 256），
  最后在重建像素上做 uniform heuristic（filter byte 不算像素）；非法输入 → invalid CONTRADICTION，
  合法但未支持格式（16-bit/灰度/调色板/interlace/超大）→ unsupported NOT-EVALUATED（绝不猜测非 blank/PASS）。
  fixture 改由独立 forward-filter encoder 生成（`_encode_png`/`_forward_filtered_rows`，不复用 decoder 逻辑），
  含 W3C 公式 KAT（filter 1/3/4 手算过滤字节）、uniform/non-uniform × filter 0..4 × RGB/RGBA、
  `raw 字节 >2 种但图像 uniform` 陷阱、截断/多余/非法 filter byte/坏 zlib/非法 IHDR 负例；self-test
  **90 passed / 0 failed**，离线套件 **228 passed / 0 failed**
  （[build-logs/script-tests-offline-20260923-225204-CST.log](build-logs/script-tests-offline-20260923-225204-CST.log)），
  hygiene 14 入口 OK；反例原始输出见
  [build-logs/gate-c-png-filter-counterexample-20260923-225245.log](build-logs/gate-c-png-filter-counterexample-20260923-225245.log)
  （uniform RGBA 在 filter 1/2/3/4 下旧启发式判非 blank，新解码器全部判 blank）。仅静态脚本/文档，
  未执行 `--run`、build/gn/codesign、浏览器/App/UI/截图/安装/commit；Gate C 仍 BLOCKED（无执行器与真实
  截图证据），待主负责人复核。
- 用户 Browser CLI PID `12828` 与 watcher PID `12068` 保持运行；未操作 live App，未 build/gn/codesign。
  Browser CLI HEAD `42b0661`、Chromium HEAD `7d0f28c30d`；两工作树保留既有未提交改动。
  Gate B/Assist 人工验收仍需用户关闭 App 并明确进入隔离验收窗口；Gate C/F 仍受各自证据缺口阻塞。
- 工作线 B（历史交付记录）：`ses_f333cf55bffe9TRDONwscWoWS4`；24E 010 返修已结束（原 CLI exec session 52247
  exit 0；定向返修 session 18488 exit 0）。最终 patch `010-user-menu-origin-propagation.patch`
  128+/20-、4 文件，SHA-256
  `4694aeb6d285b65588de6bb2186df96c86ede74ad451ac5c658df9d127b0b977`，只基于 001–009 生成，旧 patch 未改。
  主负责人独立复核确认三个确实由 `main_menu_builder.mm` 连接的 AppKit UI callback
  （New Window 等 `executeCommand:withProfile:`、Preferences、About）都触发可能展示的 BrowserWindow，
  因此统一 `kUserExplicit` 符合 §3/§5.2；唯一展示/所有权权威仍为 PresentationPolicy + NativeWidget gateway。
  TabRestorer 用 opaque enum typed field，RestoreByID 按值捕获，延迟 observer 经生产共享
  `RunWithForegroundOrigin()` seam 包住同步 `DoRestoreTab`。主负责人复跑 `user-menu-origin-wiring-check.py`
  self-test **8/8**、真实 Chromium checkout **7/7**，010 reverse `git apply --check` 和 thin-repo
  `git diff --check` 均 PASS。纯 C++ 单测已写但未编译/运行；无 build/gn/codesign/browser runtime/UI。
  **24E 状态 REVIEW，不是 DONE**；剩余：History 菜单项直接 RestoreByID 无来源 scope；三个 UI 菜单的
  前台/HUMAN 人工验收，以及冷启动/菜单联测均未做。首轮审查发现测试脱离生产接线后，OpenCode
  根据定向意见改为共享生产 seam + source wiring checker，并追踪 Preferences/About 的 show 链，已通过源码复核。
  24F WebPage attribution 源码审计（CLI exec session 27714，11:14 UTC）已结束；主负责人逐项核对：固定 revision 下 `kWebPage` 无生产 producer，页面 `window.open`/ActivateContents/全屏等均走 `kUnknown`，gateway 拒绝，`chrome.windows` 不可由普通页面调用，未发现网页借用菜单 `kUserExplicit`。无 patch，不代表行为矩阵已完整实现；24F 暂 BLOCKED 在是否增加仅用于诊断的显式 `kWebPage` 标签。审计唯一性来源：Chromium revision `7d0f28c30d1c0fadbce17ecec0ee2565aef9a940`。
  审计同时发现独立高优先级 24I：`chrome/browser/ui/omnibox/omnibox_everywhere/mac_window_util.mm::ActivateBrowserWindowOnMac` 直接调用 `activateIgnoringOtherApps:YES`、`deminiaturize:`、`makeKeyAndOrderFront:`；caller `OmniboxEverywhereService::OpenUrl` 又被多个 UIManager 入口调用，其中至少包含 `OpenURLFromTab` 这类 WebUI 回调，来源不能直接一概信任。Apple 官方说明 `activateIgnoringOtherApps` 可抢走用户焦点，[NSWindow makeKeyAndOrderFront](https://developer.apple.com/documentation/appkit/nswindow/makekeyandorderfront%28_%3A%29?changes=lat_5) 明确会显示窗口；此路径绕过现存 PresentationPolicy/NativeWidget gateway，已交同一 B 会话做定向调用链与最小修正审计。用户 Browser CLI PID 12828 仍运行，禁止 build/runtime。
  007 用户入口审计（CLI session 17282）和 10:14 UTC OpenCode 核验
  （CLI exec session 94321，exit 0，无代码变更）均结束，调用链及结论如下。
  007/008 仍 REVIEW。009 的定向修正
  （CLI session 58708）已完成；09:49 UTC 主负责人源码复核确认完整候选集先选、无 host
  目标不会调用 gateway、授权成功/失败只有单 gateway、无直接 AppKit 重复激活，且新
  纯逻辑反例对应前置过滤缺陷。反向 patch 检查与 README 空白检查通过。**009 从源码
  BLOCKED 转 REVIEW；没有编译、单测运行、签名或用户 Dock runtime 证据，不是 DONE。**
  累计此前拒收返工 2 次；当前不再修改 009。09:03 UTC 转派的独立 Gate C watcher 证据契约静态修正
  （CLI session 38788，仅 thin repo checker/fixture/文档）已结束；09:19 UTC 主负责人已
  独立复核 watcher 子合同并复跑 39/39 离线自测，子缺陷静态通过；Gate C 因 PNG/真实
  生命周期与截图证据仍 BLOCKED。A 无运行进程；B 完成 010/24F 源码审计后转入 24I。
  24I 由同一 B 会话完成（exec session `11727`）：`011-omnibox-everywhere-activation-attribution.patch`
  （7 文件、165+/14-，SHA-256 `b38a3abbffba5cf2f6e80dd35518b5e8099b3bef1bc160a77123d747021a32e5`）。
  改动把 `ActivateBrowserWindowOnMac` 收敛到纯函数 `DecideWindowActivationRoute`：managed 未归因
  `kNoop` fail-closed（不 `activateIgnoringOtherApps`/不 `deminiaturize`/不 order-front）、managed
  已证明浏览器进程用户输入经 `NativeWidgetMacNSWindowHost::PresentForUserExplicitAction()` 唯一
  gateway、stock 保留原 raw AppKit；`OmniboxEverywhereService::OpenUrl` 增加显式按值传递的
  `user_gesture`（不依赖 TLS），`OpenURLFromTab`（WebContents delegate / WebUI 渲染回调，
  `params.user_gesture` 为渲染方自报、不可信）固定 false，`ExecuteCommand` 的
  kPasteAndSearch（异步剪贴板回调按值携带）/kManageSearchEngines/kCustomizeKeyboardShortcut/
  kSettings 四个原生 context-menu 来源传 true。静态 checker
  `tests/tools/omnibox-everywhere-origin-check.py` self-test **8/8**、真实 checkout **6/6**；
  纯 route C++ 单测 `mac_window_util_unittest.cc` 已写但未编译/运行。001→011 临时 index 重放与
  worktree tracked 状态一致，反向 `git apply --check -R`、空白/格式检查通过，001–010 sha256 未变。
  **24I 状态 REVIEW，不是 DONE**：未编译、未跑 C++ 单测、无 runtime；overlay 结果选择/回车走
  `OpenURLFromTab`，managed 下有意 fail-closed（不再前置 Chrome，属已记录 UX 缺口，禁止
  display→hide 补偿）；gateway 展示与原 raw 路径在跨 Space/全屏 overlay 场景是否等价、以及
  原生 context-menu 授予目标窗口 HUMAN 的语义，需签名隔离构建后人工验收。用户浏览器
  PID 12828 仍运行，严禁 build/runtime/UI。
  **11:52 UTC 主负责人独立源码复核补充：** 逐段检查 `OpenURLParams` 定义/构造
  （`content/public/browser/page_navigator.h/.cc`）：`user_gesture` 的契约是“renderer-initiated
  navigation 是否发生于 user gesture”，不是可证明的浏览器原生 UI 输入凭据；因此把
  WebContents/WebUI delegate 的该值提升成 USER_EXPLICIT 不安全，011 固定传 false 属保守
  fail-closed，而真实 overlay 结果点击/回车目前会导航但不会把隐藏目标窗口带到前台，属于
  明确 UX 缺口，不能伪称已支持。逐段检查 `OpenUrl → Navigate(kShowWindow) → HidePopup →
  ActivateBrowserWindowOnMac → PresentForUserExplicitAction → ApplyVisibilityState`：未归因的
  native 激活已在 AppKit 三调用之前返回；显式入口只调用一次策略 gateway，失败不走 raw
  AppKit 补偿；stock 分支保留原行为。纯路由单测与静态接线 checker 可作为源码证据，但缺
  C++ 编译/执行及人工 Space/最小化验收，所以 24I 仍 REVIEW。另，OpenCode B 的一次静态
  `phase6-parallel-windows.sh --run` 负向探测缺少必填 `--evidence DIR`，脚本在 APP 检查/启动
  前立即以 `PARALLEL-WINDOWS: FAIL: --run requires --evidence DIR` 退出；用户 PID 12828
  未受影响、没有新测试浏览器进程，但不应再用 `--run` 做静态探测。B 报告
  `tests/run-script-tests.sh` 为 212/214（另两条 Gate F help/禁用 `--run` 断言与脚本实际文案
  不一致）；这是静态测试夹具/文案一致性问题，不是 24I 通过证据，也不授权启动 Gate F。
- **007 用户入口审计（2026-09-23 10:04 UTC，主负责人复核）：** 根因不是单纯“漏加
  `ScopedForegroundOrigin`”，而是 origin 为 thread-local 且异步边界没有把 provenance
  按值传递。Finder/Launch Services 进入
  `AppController::application:openURLs:` → `openStartupTabsReplacingNTP:`；启动未完成时
  URL 仅写入 `_startupTabs`，稍后由 `openStartupUrls` 重放；启动完成时进入
  `OpenStartupTabsInBrowser`，普通 URL 经 `RunInLastProfileSafely`，`.webloc` 还经过
  ThreadPool `PostTaskAndReplyWithResult` → `RunInProfileSafely/RunInLastProfileSafely`，最终
  `OpenStartupTabsInBrowserWithProfile` 在没有 BrowserWindow 时创建窗口并 `Show()`。所以只在
  `application:openURLs:` 栈上开 TLS scope 会在异步/启动队列后丢失；只给最终回调设 scope
  又会漏掉跨边界的请求归属。可借鉴本 checkout 已有的按值捕获模式：
  `BrowserView::Show/Activate` 的 deferred callback 与 `TabsCreateFunction` 的
  `OnBrowserWindowCreatedWithOrigin`。
  Apple `application:openURLs:` 文档仅保证“AppKit 被要求打开资源”，没有承诺该请求必为
  人类手势，因此不能未经论证把所有调用一律升为 USER_EXPLICIT。官方资料：
  [Apple NSApplicationDelegate application:openURLs:](https://developer.apple.com/documentation/appkit/nsapplicationdelegate/application%28_%3Aopen%3A%29?changes=_7&language=objc)。
  Chromium 对照版本为 checkout HEAD `7d0f28c30d1c0fadbce17ecec0ee2565aef9a940`；对应实现：
  [app_controller_mac.mm](https://chromium.googlesource.com/chromium/src/+/7d0f28c30d1c0fadbce17ecec0ee2565aef9a940/chrome/browser/app_controller_mac.mm)。
  主负责人判断：唯一展示/所有权权威仍是 PresentationPolicy/NativeWidget gateway；禁止
  AppKit delegate 直接绕过网关。下一项仅处理这条 URL 打开链，先证实可依赖的
  Launch Services 用户来源，再以 origin value 经 startup queue、ThreadPool/Profile callback
  逐段传播；若 API 无法区分人工与程序化来源，不得 fail-open，需精确记录架构阻塞。
  编译/build/runtime 和可见 UI 均禁止（用户 Browser CLI 当前运行）。
- OpenCode B 后续核验结果（10:14 UTC）：AppKit 文档只描述 app 被要求打开 URL resource，
  不提供“这是人类手势”的承诺；主负责人亦确认源码中的 `.webloc` target 被标为
  `is_untrusted_launch=true`，不得误当来源认证。无可靠人类来源信号时，不应授予
  `kUserExplicit`；可传播 provenance 的技术事实不等于应信任该请求。当前 007 用户入口
  留在 **BLOCKED/需产品安全决策**，不加 fail-open patch。需要决定是否接受“任何能请求
  Browser CLI 打开 URL 的本地程序也可能触发前台展示”的风险，或保持外部 URL 不获前台权限、
  让用户通过 Dock/启动器/CLI show 显式打开。
- 24E 新根因（主负责人源码复核，10:22 UTC）：`executeCommand:withProfile:` 明确只由
  AppKit 菜单/ Dock 菜单 dispatch 进入（代码注释标为真实用户动作），但其 scope 是
  `kInternal`；设计 §3/§5.2 将用户菜单显式操作定义为 `USER_EXPLICIT`。例如 `IDC_NEW_WINDOW`
  调用 `CreateBrowser()` → `chrome::NewEmptyWindow()`，当前继承 `kInternal`，不是用户显式来源。
  `IDC_RESTORE_TAB` 再进入 `TabRestorer::RestoreMostRecent`；当 TabRestoreService 尚未加载，
  `TabRestorer::TabRestoreServiceLoaded` 延后调用 `DoRestoreTab`，TLS origin 已卸栈。下一项只修
  这一受确认的用户菜单入口及该 observer 延迟边界，不改其它 Chrome 内部调用，不直接调用
  AppKit 展示 API；默认 UNKNOWN 及 007 外部 URL 的安全阻塞保持不变。
- 24E 先有候选实现，首轮复核发现测试未覆盖生产接线后，已由同一 OpenCode 会话定向修正；最终有窄范围实现、共享测试 seam、静态 wiring 反例、明确菜单调用链与原始校验结果，因此不触发 GPT-6 Luna 升级门禁。后续若其它验收点复发，必须先源码定位，不得原样重派。
  24D/24H 的外部 openURLs 仍需用户决定其信任/UX，不用 24E 进展伪装完成。当前不 build/runtime；
  用户 Browser CLI 运行时不替换 live App。
  下次 15 分钟复查若该任务没有明确突破（交付可审查的根因修正/反例，而非只报告尝试），
  按用户指示改由 GPT-6 Luna `max` 思考子代理接手根因与定向开发；先停止重复派发，禁止并行
  重复改同一文件。最小化、跨 Space 与用户切走不抢焦点尚须隔离构建后人工验收。007/008
  未 build/runtime，当前用户 Browser CLI 正运行时不替换 live App。
- 会话、阻塞项和下一步在此更新；15 分钟定时提示词只保存调度规则，避免状态漂移。

### 重复缺陷的资料查证与派单门禁

- 同一验收点第二次失败，主负责人先停止原样重派，阅读本地调用链、原始失败日志及
  现有测试；再检索对应版本的 Chromium 上游源码与 Apple 官方 API 文档，必要时找
  其它一手资料。记录链接、版本差异和哪些判断只是推论，不把网上建议直接照搬。
- 定向任务必须写明：唯一状态/展示权威、具体文件与函数、可复现反例、最小修正边界、
  可证伪的通过/失败条件、禁止触碰的运行环境。子代理只做实现和原始证据交付；
  主负责人独立复核。若仍不成立，标 BLOCKED 并改做独立安全事项，不继续叠补丁。
- 对 macOS Dock/窗口问题，已查证的资料与反例见下方 009 源码审计；尤其区分
  `hasVisibleWindows`、实际可见、最小化、Space 切换和异步激活，不能用单一 bool
  或纯选择器单测代替完整场景验收。

### 2026-09-23 外部代码审核的独立核对与优先级

- 审核基线是已推送的 `42b0661`；审核文件为用户提供的
  `/Users/jiangao/Downloads/browser-cli-code-review-next-steps.md`。其中建议不是自动指令，
  以下为主负责人对当前 checkout 的独立核对。
- **P0 已确认：Origin fail-open。** `ui/views/widget/browser_cli_presentation_policy.cc`
  审核基线的 `g_current_foreground_origin` 默认 `kInternal`，HUMAN-owned 分支允许非自动化来源，
  而 WebPage/SessionRestore/async/native 路径仍有未归因部分。下一步需要明确 `kUnknown`
  默认拒绝 HUMAN，并仅给已证明的用户 UI 入口显式来源；必须同步验证 Dock/CLI show
  不会再次被误拦截。不得简单改枚举后声称 Gate B 通过。
- **007（2026-09-23，REVIEW，未验收）：** OpenCode B 已提交未入库的
  `chromium/patches/007-origin-fail-closed.patch`：默认 `kUnknown` 并拒绝其操作 HUMAN，
  给三个 AppKit 菜单入口显式 `kInternal`；001→007 静态重放通过。主负责人已阅读
  policy/AppKit 差异及来源地图，确认仍有原生窗口操作路径未归因，可能误拦用户；
  30/30 等 C++ 单测、完整构建和 Dock/CLI/菜单/最小化/恢复/全屏人工矩阵均未执行。
  因而只能记 REVIEW，不能宣称 Origin P0 已修或 Gate B 已过；当前用户浏览器运行时
  不触碰 live bundle、不做 build/runtime。该点首次发现于本日外部审核，本轮返工 0 次。
- **P0 已确认：Lease generation 未贯通。** Policy 的 `ForegroundLease.request_seq` 已随新
  lease 递增，但 `ForegroundAssistController` 只跟踪 `tracked_window_`，同一窗口过期后
  重新获批可能被当作幂等请求；旧 timer 可以按 window_id 释放/隐藏新 lease。
  先加 ABA 可证伪单测，再使 timeout/release 同时核对 window_id + request_seq。
- **008（2026-09-23，REVIEW，未验收）：** 主负责人已阅读
  `foreground_assist_controller.cc` 和 `foreground_assist_presentation.mm`，确认
  `OnLeaseGranted` 只以 window_id 判幂等，`OnLeaseTimeout` 与 delegate 的 release/hide
  也仅以 window_id 寻址。已给 OpenCode B 一次定向任务：同窗口 seq1 惰性过期→seq2
  新获批的失败测试、generation 贯通，以及 timeout/release/hide 每段的代际核验；
  OpenCode B 已交付 `chromium/patches/008-lease-generation-aba.patch`（9 文件）：
  controller 跟踪 `(window_id, request_seq)`，policy release 按 generation 守卫，host
  hide 再查 HUMAN/live lease；001→008 静态重放通过。主负责人已阅读 timer、producer、
  policy、host 的代际传递与防护，不把静态验证当成运行验收。新增 C++ 用例尚未编译/运行，
  真实同窗口过期→重授、用户接管与旧 timer 回归也未运行；继续保持 REVIEW。
  该点首次发现于本日外部审核，返工 0 次。
- **P0 已确认：可见 ASSIST 窗口的 Dock 接管路径漏授 HUMAN。** B 线只读审查发现
  `app_controller_mac.mm::applicationShouldHandleReopen` 在 `hasVisibleWindows=YES`
  且存在 BrowserWindow 时直接 `FocusWindowSetOnCurrentSpace` 并返回，绕过
  `PresentExistingWindowForUserExplicitAction`；不会触发 policy 的 `kUserExplicit`
  接管，也不会取消 controller timer。TTL 或 agent release 后可能隐藏用户正在操作的
  窗口。应先建“ASSIST 可见→用户点 Dock→HUMAN owned、timer 停止”的失败测试；
  修正不得把所有可见窗口一概变 HUMAN，也不得新增 process-wide ownership。
- **009（2026-09-23，BLOCKED，返工 1 次后主负责人拒收）：** 主负责人进一步复核
  `FocusWindowSetOnCurrentSpace` 的 NSApp 窗口选取顺序及
  `PresentExistingWindowForUserExplicitAction` 的创建顺序选窗，两者不等价；
  已定向交给 OpenCode B：必须对 Dock 实际选中的同一窗口先授 USER_EXPLICIT/HUMAN
  再激活，只接管该窗口，并保护 stock/Space/最小化语义。不能用“先聚焦再归因”
  或把全部窗口授 HUMAN；无法证明同一目标则报告 BLOCKED。静态任务，不运行 App。
  OpenCode B 已交付初稿 `009-visible-assist-dock-takeover.patch`，但主负责人源码审查
  发现两处反例：先 `SelectWindowToFocus` 选 A，`PresentNativeWindowForUserExplicitAction`
  会展示/激活 A、改变 orderedWindows，之后 `FocusWindowSetOnCurrentSpace` 再次选窗，
  可能聚焦 B，无法证明“授 HUMAN 的就是用户实际看到的窗口”；新增
  `app_controller_mac_browsertest.mm` 会在自动 browser test 中执行
  `makeKeyAndOrderFront`/`activateIgnoringOtherApps`，违反禁止自动显示窗口的测试卫生。
  另有 `hasVisibleWindows=YES` 而浏览器窗口全隐藏时，初稿测试断言用户 Dock 点击仍
  不显示窗口，与“用户永远能手动打开”冲突。009 不可编译或运行其 browser test，
  不能标 REVIEW/DONE；已定向要求只选一次并只聚焦同一目标、无可见窗口时回退
  USER_EXPLICIT 重用路径，并以无窗口纯逻辑测试替代可见自动 browser test。
  返工中的代码仍须复核：`PresentNativeWindowForUserExplicitAction()` 已经执行
  `ShowAndActivate`，调用者若忽略其 `bool` 结果并继续
  `makeKeyAndOrderFront`/`activateIgnoringOtherApps`，就会在授权失败时仍强制前台化；
  同一成功路径重复激活也增加竞态与难以归因的日志。必须保证授权失败不激活，
  且每次 Dock 点击只有一个经授权的展示/激活路径。
  返工版虽移除违规可见 browser tests、改为单次选窗并让全隐藏回退，但仍在
  `app_controller_mac.mm` 的 managed Dock 分支**忽略**
  `PresentNativeWindowForUserExplicitAction(target_window)` 的 `bool` 返回，继续
  `deminiaturize`/`makeKeyAndOrderFront`/`activateIgnoringOtherApps`。源码确认该 helper
  经 `PresentForUserExplicitAction` → `ApplyVisibilityState(kShowAndActivateWindow)` 已
  向 native Mojo 发送展示/激活；故失败时仍可能绕过门禁，成功时也有双重激活。
  这不是静态重放通过能证明安全的行为。**停止 009 同方向微调和后续叠 patch，
  先做一次架构决策**：要么以单一授权展示网关完成 Dock 目标显示/激活并验证
  minimized/Space 语义；要么明确拆分“仅授 per-window HUMAN 权限”和“对同一目标
  执行一次原生激活”的两阶段事务，失败不得触及 AppKit。不得先调用现有
  show-and-activate helper，再额外直接 AppKit 激活。未决前不编译/运行 009，
  不进入 Gate B；该点首次发现于本日外部审核，返工 1 次（两版均被拒收）。
  **主负责人源码决策（待形成新的有界任务）：** 当前
  `NativeWidgetMacNSWindowHost::PresentForUserExplicitAction()` 经
  `ApplyVisibilityState(kShowAndActivateWindow)` 发往
  `NativeWidgetNSWindowBridge::SetVisibilityState()`，后者已经按 AppKit 正确顺序
  `activateIgnoringOtherApps`→`makeKeyAndOrderFront`。因此 managed Dock 路径应选择目标
  一次，并把**这一条 gateway 当唯一展示/激活 authority**；成功后不再直接调用
  AppKit `makeKeyAndOrderFront`/`deminiaturize`/`activateIgnoringOtherApps`。
  若 gateway 失败，只能记录失败并尝试受同一门禁保护的其它合格窗口/新建路径，
  不能裸调 AppKit。最小化动画和跨 Space 语义必须人工验收；如单 gateway 无法满足，
  再单独设计 grant-only + exact-window native activation 事务，不准原位堆例外。
  **009 下一次实现的可证伪门禁（2026-09-23 主负责人复核源码后确定，仍为 BLOCKED）：**
  `app_controller_mac.mm::applicationShouldHandleReopen` 的 managed 分支只能对
  `SelectWindowToFocus(..., false)` 选出的目标调用一次
  `PresentNativeWindowForUserExplicitAction`；仅该调用返回 `true` 才消费 Dock 请求，
  且不能再直接 `deminiaturize`、`makeKeyAndOrderFront` 或
  `activateIgnoringOtherApps`。返回 `false` 不得激活该目标，须留下同一 request ID 的
  失败日志，再经受 USER_EXPLICIT 门禁的重用/新建路径处理；若该路径也失败，应明确
  报错而非假报“已打开”。`GetBrowserWindows` 全隐藏且无候选时必须走此回退。
  纯逻辑测试覆盖可见 ASSIST、最小化、全隐藏、无可用目标和授权失败，断言授权失败
  时原生激活调用次数为 0、成功时展示/激活网关次数为 1、授 HUMAN 的窗口 ID 与
  唯一目标一致；不得自动创建可见测试窗口。跨 Space、最小化恢复动画、用户切走后
  不抢焦点只能在安全的签名隔离构建后由用户人工验收，未验收不得标 DONE。
  **08:48 UTC 对第三版的主负责人复核：仍拒收，返工累计 2 次。**
  `GetBrowserWindows()` 收集所有非 App BrowserWindow 的 NSWindow；第三版在
  `app_controller_mac.mm` 构造焦点候选时，先跳过 `GetFromNativeWindow` 无 host 或
  `widget_id==0` 的窗口，再调用 `SelectReopenFocusCandidate`。这改变了上游对**完整**
  BrowserWindow 集合的选择结果：例如当前 Space 最前方 A 暂无可解析 host，后方 B
  有 host，则上游选 A，本版会改选并激活 B。它不能证明“用户实际选中的同一窗口授
  HUMAN”，而纯测试只把无 host 窗口作为 `window_id=0` 候选送进 helper，未覆盖调用者
  预过滤行为。应先对完整集合选一次，再对**被选中的同一窗口**解析 host；解析失败
  即走受门禁的回退，不能静默选下一扇窗。此为源码反例，静态 patch 重放不反驳，
  当前用户 App 正运行也不能安全 build/runtime。停止此点继续微修，待单独设计/复核。
  **2026-09-23 上游/平台核对（主负责人，区别于子代理推测）：** Chromium 原版
  `app_controller_mac.mm::FocusWindowSetOnCurrentSpace` 在**完整** browser window 集合上按
  `NSApp.orderedWindows` 选焦点；源码明确警告 Dock 触发 Space 切换时
  `isOnActiveSpace` 可能仍指向旧 Space，且最小化恢复有独立动画语义。
  Apple `applicationShouldHandleReopen:hasVisibleWindows:` 文档明确：最小化窗口也会让
  `hasVisibleWindows=YES`，不能把 YES 等同“已在屏幕前台”；delegate 返回 NO 后系统
  不再替应用补开窗口。Apple 亦明确 `activateIgnoringOtherApps:YES` 可在用户已切走后
  抢焦点，且激活并非即时。由此 Gate B 人工矩阵必须分别覆盖：可见同 Space、全最小化、
  全隐藏、跨 Space、Dock 点击后迅速切走；纯选择器单测不能替代这些时序验收。
  反复出现 bug 的结构性原因是 Dock 的 OS 输入状态、per-window HUMAN 授权和 native
  激活分散在多个层次，之前把“选窗”“授权”“实际显示”分别 patch/测试，未用同一
  window ID 与单次交易把它们闭合；后续禁止用另一层的事后激活补偿失败。
- **P0/P1 语义缺陷：show+assist 冲突组合。** `chrome_browser_main.cc` 冷启动和
  ProcessSingleton 都先调用 USER_EXPLICIT show handler；show 分类器不识别 assist
  开关，故组合意图可能先被 show 消费，assist 的冲突拒绝分支不可达。需要按真实
  入口顺序的集成回归，不能只用各自纯分类器单测证明。
- **Gate C 额外缺陷已确认：** `capture-evidence-check.py::read_png()` 直接统计 zlib
  解压后的 IDAT 字节；PNG scanline filter 尚未逆变换，不能据此判真实像素是否空白。
  与零样本 watcher 假通过一起保持 Gate C 判定器 BLOCKED，P0/Gate B/Assist runtime
  之前不新增 compositor 补丁。
  **09:03 UTC watcher 证据契约决策（主负责人读 checker/原始 watcher 源码）：**
  `parse_watcher()` 当前允许 `samples=[]`、`final max=0` 时 `consistent=True`，而
  `evaluate()` 仅在样本和 final 同时缺失时给 NOT-EVALUATED，故 final-only 会假 PASS。
  必须有至少 1 条可解析的原始 `sample ts=... count=...`、且有恰好 1 条完整 final
  （`max` 与 `nonzero_samples`），二者均为非负整数；缺原始样本或 final 是
  NOT-EVALUATED/非零退出，畸形、重复 final、负数、final 与样本最大值或非零计数矛盾
  是 CONTRADICTION。样本时间戳应可解析且严格递增；不能以 final 概括代替时间序列。
  此门禁仅修证据有效性，不宣称 watcher 覆盖了浏览器完整生命周期；后者还需执行器
  留下启动前、截图期间和退出后的时间边界证据。PNG 解码缺陷另列，两个问题都解除前
  Gate C 不得 PASS。
- 架构收敛建议方向成立，但 `BrowserCliControlService` 全面迁移、公开 targetId API、
  build/sign 解耦、GitHub 仓库重命名和 LICENSE 选择需要独立设计/兼容性评估；
  **不能把建议直接当作已授权的重构或对外 API 改动**。用户此前要求开发构建固定
  Developer ID 签名，不能因为优化编译时间回退 ad-hoc。
- **外部审核建议对照（避免“读过但未进入计划”）：**

  | 审核主题 | 当前决策/状态 | 下一门禁 |
  | --- | --- | --- |
  | §2–6 Origin fail-closed、24A–H | 24A–H 已拆；007 默认 Unknown/拒绝 HUMAN 为 REVIEW；明确用户 UI 只覆盖三个 AppKit 菜单入口，不能把 `kInternal` 再当兜底；host 里“kInternal by default”注释已过时 | 先静态审查全部信任入口，再 Gate B 人工矩阵；未知来源宁拒绝并标注兼容性缺口，不假称完成 24E–H |
  | §7–9 lease ABA/双权威 | 008 用既有 `request_seq` 作 generation，REVIEW；**当前 canonical lease owner 是 Policy 的单槽数据与过期时间，Controller 仅是派生 timer/副作用执行器**，不得再让 Controller 自行决定 lease 是否有效 | C++ ABA 单测、隔离 runtime；若出现两个权威分歧再设计控制面迁移，不能以第 3 套状态补偿 |
  | §10–12 ControlService/targetId | 认可为后续架构/API 设计议题，尚未实现；不在未过 Gate B 时盲目大改或把 NativeWidget ID 变公开 API | Gate B/Assist 通过后、Gate C/BrowserPilot 前完成边界决策；若现结构阻止验收，提前标 BLOCKED 并单独设计 |
  | §13–17 Gate 顺序、截图 checker | Gate C/F/BrowserPilot 暂停；PNG scanline 未 unfilter、watcher 零样本假 PASS 均已记录；Gate F 违规试跑不计验收 | Gate B 与 Assist runtime 后先做现有实现的最小截图实验，只有证据证明需要时才改 compositor |
  | §18–22 测试膨胀、文档、签名、仓库 | 目前仅统一进度入口并维持签名硬约束；文档去重、compile/sign 边界、仓库改名、CI/LICENSE 尚未执行，也未默认授权 GitHub 改动 | 单列工程卫生设计与用户决策，不得伪称已采纳实施 |
- **测试纪律违反（2026-09-23 14:10 CST）：** OpenCode A 收到“只实现、不得运行
  `--run`”的明确指令后，仍执行了 `tests/phase6-parallel-windows.sh --run --evidence
  build-logs/phase6-static-probe`。精确复查：新测试 App/监听 watcher 均已退出，
  用户原 PID 12828/12068 仍存活；测试使用 Developer ID App、mock keychain、隔离
  临时 profile，watcher 524 个原始样本 `max=0`，未见窗口上屏；但真实 HTTPS DOM
  未就绪，background subset 为 CONTRADICTION，Gate F 未通过。保留原始证据，不把
  这次违反指令的试跑计入验收。后续脚本测试不得用带现存可执行 App 的
  `--run --evidence` 作“门禁负例”；自动运行须经主负责人明确调度。

### 2026-09-23 启动/Dock 问题审计（不计入 DONE）

- **首次冷启动迟滞**：用户手动启动的签名 `out/Dev` component App（PID 12828）先在
  `dyld4::JustInTimeLoader/mapSegments/fcntl` 加载约 500 个动态库，约 3 分钟后才创建
  `/tmp/browser-cli-manual-profile` 并进入 BrowserMain。进程存在不等于可响应；首次加载时间
  是本开发构建的已观测性能问题，不得宣称产品可接受。首次发现：2026-09-23 约 12:00 CST。
- **热态 Dock 重开迟滞**：用户关闭窗口后进程仍存活。原始 app-identity watcher 的几段
  `count=0→1` 间隔约 2.2–3.1 秒，但没有 Dock 点击时间戳，因此不能据此计算点击延迟；
  需要给 `applicationShouldHandleReopen` 入口及实际 native window 上屏边界记录同一 monotonic
  clock，区分 profile 加载、窗口创建、native presentation。当前仅可确认无复用窗口时走
  `RunInLastProfileSafely` → `CreateBrowser` → `PresentBrowserForUserExplicitAction` 的路径，
  性能根因**未证实**。首次发现：2026-09-23 约 12:05 CST；OpenCode B 两次只读审查均长时间
  无输出，已停止，不再原样重派。
- **Gate B 证据边界**：上述用户手动命令未带 managed presentation 开关，且 profile 与 Gate B
  runbook 不同；watcher 按 App identity 而非 profile 统计。因此这些窗口样本只能用于体验/性能
  线索，不能充作 USER_EXPLICIT/HUMAN Gate B 通过证据。
- **测试脚手架返工**：`phase3-human-ownership-manual.sh` 的 readiness 改动第一次误把
  app-wide 可见窗口当成本 profile 就绪，并在超时返回成功；主负责人已检查
  `collect_phases`/`evaluate_phases`/`wait_for_ready` 调用链，定向要求目标 profile 进程与
  DevTools 绑定、app-wide 可见性仅供参考、超时非零。第 2 次修正已交付，主负责人于
  2026-09-23 13:04 CST 复跑静态证据：readiness fixture 44/44、脚本 180/180、启动卫生
  12 入口通过，`git diff --check` 通过；目标 profile 进程/监听端口/可选
  `DevToolsActivePort` 绑定已源码复核。**真实 runtime 尚未运行，保持 REVIEW；不得用静态
  `READY` 结论验收 Gate B**。测试线曾误停原始被动 sink（PID 12067）；已精确恢复为 PID 18637，
  原 watcher PID 12068 与浏览器 PID 12828 未停止。旧采集缺口期间可能丢失 sink 事件，
  后续 Gate B 需新鲜、完整的隔离运行证据。
- **Gate C 离线判定器审计**：首次发现 2026-09-23 约 13:20 CST；
  `capture-evidence-check.py` 对 dynamic/canvas/long 单张截图也可能给 uniqueness PASS，
  证据全缺但无 contradiction 时 main 返回 0。已给 OpenCode A 一次定向修正：强制
  static ≥2、dynamic/canvas ≥2、long ≥3；必选证据全 PASS 才退出 0，缺证据返回非零，
  视频 optional 可保持 NOT-EVALUATED；新增负例。第 1 次修正经主负责人静态复跑：
  fixture 23/23、脚本 197/197；空证据 rc=3，完整 fixture rc=0。随后于约 13:35 CST
  发现第 2 处假通过：必选 case 来自可删改的证据清单，且 watcher 的 `final.max=0`
  可能遮蔽 `sample count>0`。已定向要求代码内固定四类必选矩阵、case 唯一性、原始
  样本不得被最终汇总覆盖；这是第 2 次返工。OpenCode A 于 2026-09-23 13:40 CST
  交付 REVIEW：self-test 28/28、脚本 197/197、启动卫生 13 入口；四个 CLI 负例
  （缺 dynamic、canvas optional、重复名称、sample=1/final.max=0）均 rc=1，完整矩阵 rc=0，
  空证据 rc=3。**主负责人独立复核发现第 3 处假通过，判定器子项 BLOCKED**：
  `tests/tools/capture-evidence-check.py` 的 `evaluate()` 在约 356 行仅当 `samples==0`
  **且** `final_max is None` 才判 `no-visible-window=NOT-EVALUATED`；仅有伪造/截断的
  `final max=0`、没有任何原始 `sample` 时会走到 PASS。根因是把汇总行当成采样覆盖证据，
  违背“原始样本为准”；最小修正方向是零原始样本始终 NOT-EVALUATED，且缺少完整采样
  时间覆盖/结束标记不得 PASS。可证伪条件：`final max=0` 但 0 条 `sample` 的完整其余
  fixture 必须非零退出，正常多样本完整 fixture 才能 rc=0。已返工 2 次，不再给同一
  OpenCode 会话原样重派。09:19 UTC 主负责人已独立阅读 B 按新证据契约交付的
  `parse_watcher/evaluate`、对照原 watcher 输出格式并复跑 `--self-test`：39/39 PASS；
  final-only→NOT-EVALUATED/rc=3，重复/畸形/负数/时间戳倒序/汇总矛盾→CONTRADICTION，
  完整一致样本→该子检查 PASS。**仅 watcher 离线证据子缺陷静态复核通过**；PNG
  filter 未逆变换、生命周期覆盖尚无执行器证据，故 Gate C 仍 BLOCKED/TODO，
  24/74 总验收不增加，也无任何真实截图 runtime 证据。
- **Dock 重开耗时归因**：OpenCode B 新会话 `ses_f333cf55bffe9TRDONwscWoWS4`
  已交付仅用于记录的 Chromium patch 006，标 REVIEW；临时 index 顺序重放 001→006
  `git diff --quiet` rc=0，006 两次提取 sha256 一致。**未编译、未运行、未完成 Dock 人工取证**；
  `present-return` 仅为展示 API 返回，不是 OS 上屏时刻。热态迟滞根因仍未证实，
  不计入已验收进度。
- **Gate F 多窗口静态准备**：OpenCode A 于 2026-09-23 约 13:55 CST 交付
  `tests/phase6-parallel-windows.sh` 和 `tests/tools/parallel-evidence-check.py`，标 REVIEW。
  离线 fixture 16/16、脚本 214/214、启动卫生 14 入口；原始证据目录
  `build-logs/phase6-gatef-evidence.V5uyNG/` 的完整 fixture rc=0、final-only watcher 和
  两窗口负例均 rc=1。**无浏览器 runtime；无截图验收，Gate F 不得计 DONE。**

1. B（Gate A）已全部 DONE；C 的 per-window USER_EXPLICIT/HUMAN 所有权已于 2026-09-23 复核
   （见 C 第 2 条）；C 剩余的是 runtime/人工 Gate B 矩阵。
2. 下一项：**由用户执行人工 Gate B 矩阵**（可见 runtime，不属于自动任务）。
   CLI 显式 `show` 生产者（`003-user-explicit`）的源码、单测与静态集成已完成并标 REVIEW
   （见 C 第 4 条与 `chromium/patches/README.md`）；`tests/phase3-human-ownership-manual.sh`
   的 runbook 已覆盖 Dock/Launchpad、原生最小化/恢复/全屏、Extension ops、HUMAN 下另建后台
   Window/Tab，以及 CLI show 的既有窗口/零窗口用例（含可选 blocked 负例；命令只打印、
   绝不代运行）。用户按 runbook 操作后运行 `collect`，以 `report.json/report.md` 的
   PASS/CONTRADICTION 作为 Gate B 人工证据。
3. Foreground Assist Slice 1（策略层 lease，004）与 Slice 2（producer + host 接入 + 超时计时器，005，
   已按两轮复核意见修正）均已冻结并标 REVIEW（见 D 区第 1/3 条与 `chromium/patches/README.md`）；
   **无 runtime/可见 UI 证据；当前 App 已 Developer ID 签名；冷启动"无普通启动副作用"目前只有源码级证明（sanitizer 单测 +
   `tests/check-foreground-assist-cold-start-order.sh` 静态门禁）；tab 级目标明确不支持**。下一步由
   主负责人复核 004/005，然后安排签名 App 的隔离 runtime 验证（assist grant → present →
   timeout/release → 用户接管后旧 deadline 不 hide；冷启动带 URL/app 的 blocked 负例零可见窗口），
   再推进 D 区其余条目。
4. 每个子任务必须先阅读本文件、`docs/browser-cli-development-plan.md` 与 `AGENTS.md`，只领取一个
   有明确验收条件的条目。
5. 子代理不得自行把条目标成 `DONE`；只能提交证据并标成 `REVIEW`，由主负责人复核后勾选。
6. 每次定时跟进记录：正在做的条目、OpenCode session、最近证据、阻塞、下一条目。没有正在运行的
   OpenCode 任务且不存在阻塞时，应下发当前调度指针中的下一项，而不是停止跟进。

## 按用户截止时间结束（2026-09-24 00:12 CST）

用户设定的硬截止为 2026-09-24 00:00 Asia/Shanghai。到期时项目尚未全部完成，故本轮停止自动跟进；
不得将下列 REVIEW/BLOCKED/TODO 项推定为完成。检查时没有运行中的 OpenCode CLI 实现会话/进程，
因此没有需要中断的 CLI 任务。Browser CLI 应用 PID 12828 与被动日志 watcher PID 12068 仍在运行，
按约定未关闭、未操作，也未修改 live bundle；没有执行构建、runtime、真实 UI 验收、commit 或 push。

未完成范围摘要：

- Gate B 的真实用户矩阵未执行；USER_EXPLICIT/HUMAN 相关补丁仍有 REVIEW/BLOCKED 项，含 Origin
  provenance、菜单/TabRestorer 入口及 Dock 接管边界。
- Foreground Assist 尚缺签名隔离 runtime 与人工前后台/超时/用户接管验收。
- Gate C 仅有离线 checker 静态证据，尚无真实截图、生命周期覆盖及截图矩阵验收；Gate F 多窗口
  runtime 未完成，且此前违规试跑不计验收。
- BrowserPilot 集成、旧 Backlight workaround 清理、正式 release 签名与 notarization/staple、
  packaging/install/update/migration 以及最终 macOS 人工验收均未完成。

截止时保留的静态证据（均不能代替上述 runtime/人工验收）：

- `build-logs/script-tests-offline-20260923-231200-CST.log`：229 passed / 0 failed。
- `build-logs/gate-c-png-selftest-20260923-2315-CST.log`：102/102。
- `build-logs/gate-c-png-hygiene-20260923-2315-CST.log`：14 个入口检查通过。
- `build-logs/gate-c-png-chunk-crc-idat-20260923-230742.log`：CRC/IDAT 对抗样例记录。
- Browser CLI 仓库 HEAD `42b0661`；Chromium 仓库 HEAD `7d0f28c30d`。两个工作树均保留原有
  未提交改动；未创建提交或推送。

## 截止后用户授权的单次 Dock 接管观察（2026-09-24）

- 用户先手动退出原 `/tmp/browser-cli-manual-profile` 实例；主负责人用既有 Developer ID 签名构建，
  按人工 runbook 直接启动独立 profile `build-logs/phase3-human-20260923-115339/profile.Xn6eWr`
  （`--use-mock-keychain`、`--browser-cli-background-presentation`、独立 DevTools 端口和测试扩展）；
  没有重编译、没有启动可见窗口、没有触碰真实 profile。
- 用户单击 Dock 一次并确认既有窗口前台激活、没有多开。日志 PID 52615 在
  `BROWSER_CLI_PRESENTATION` 中记录 `human-ownership-granted window_id=1`，紧接着记录
  `origin=user-explicit action=show-and-activate state=human owned=yes`；watcher 当时记录窗口数
  在启动前采样为 0、Dock 点击后采样为 1。由于 006 的 `BROWSER_CLI_DOCK_REOPEN` request-id
  日志尚未编译进当前 App，本次不能从日志独立关联 OS Dock 点击与 policy transition；来源归因依赖
  受控人工步骤。该行为用例通过，但不能外推为可见 ASSIST 窗口 Dock takeover（patch 009）通过，
  也不能外推为完整 Gate B 通过。
- readiness checker 在真实主进程已运行时仍报 `WAIT`。对照发现其
  `browser-readiness-check.py::scan_process_table()` 用 `ps -ww -o pid=,command=`，没有显式扫描
  全部进程；其解析结果为 0 个 profile 进程，而 `ps -axo` 可见同一主 PID/完整 profile 参数。
  独立只读证据确认 PID 52615 持有端口 63440，`/json/version` 返回有效响应，日志显示 managed
  窗口创建被抑制。后续应修正 checker 并补“不同终端/无 controlling TTY”的 fixture；本次未改代码。
- 收集器整体报告 1 个 `watcher-no-transient-window` CONTRADICTION：`max=3`、6,185,540 条样本，
  因 watcher 从前一日遗留运行且按 app identity 跨 profile 采集，不能作为本次 Dock 点击的可归因
  瞬态证据。精确停止 watcher PID 12068 与 sink PID 18637，未删除其原始证据；Browser CLI PID
  52615 保持运行，未执行 build/runtime 替换、未 commit/push。

## 用户授权的 HUMAN 扩展窗口动作串观察（2026-09-24，局部证据）

- 新增本地可视动作脚本 [`tests/tools/run-human-window-ops-sequence.mjs`](tests/tools/run-human-window-ops-sequence.mjs)：只连接已有 PID 52615 的隔离实例（`--use-mock-keychain`、独立 profile、managed mode、DevTools 63440），不启动/退出 Browser CLI；复用同一个 HUMAN 原生窗口，不执行 `create-window`，动作默认间隔 2 秒；只启动并在结束时回收本次 watcher/sink helper。
- 用户请求实际运行后，2026-09-24 14:41 CST 的一次序列完成 8 个 `chrome.windows.update` 操作尝试：focus true/false、minimize、maximize、restore、fullscreen on/off、bounds。扩展 API 层均返回 `ok=true`，但策略日志 seq 4–11 对应每项均是 `decision=deny origin=extension state=human owned=yes reason=human-owned-window-automation-denied`；native 状态始终 `normal`，sink 的窗口清单只有一个窗口。由于脚本从 Codex 前台执行，8 项快照中的 `focused=false`；这次数据证明了失焦期间 Extension 请求未把窗口拉前或改变状态，不是“聚焦 Human 窗口期间”完整人工矩阵。
- 最后一项 `chrome.tabs.create({active:false})` 已实际将 page target 数从 2 增至 3；事后只读 CDP 列出 3 个 page targets，且三者 `Browser.getWindowForTarget` 均为同一 `windowId=962960070`；最新 watcher `final max=1`。**没有新增原生窗口**。本次脚本因 harness 缺陷退出 1：新增 target 筛选错误地把原有 `chrome://newtab/` 也算入；已改为比较点击前后的 target ID 集合，并通过 `node --check` 与 `git diff --check`，但修复后的脚本未再运行，因此脚本/整串状态仍不是 PASS。
- 原始证据：[`build-logs/phase3-human-20260923-115339/human-ops-sequence-20260924T064131058Z.sink.jsonl`](build-logs/phase3-human-20260923-115339/human-ops-sequence-20260924T064131058Z.sink.jsonl)、[`build-logs/phase3-human-20260923-115339/human-ops-sequence-20260924T064131058Z.samples.txt`](build-logs/phase3-human-20260923-115339/human-ops-sequence-20260924T064131058Z.samples.txt)、同目录 `browser.log` 14:41:33–14:41:48 的 policy seq 4–11。需额外调查同日志 14:38:43 的 `origin=internal action=show-and-activate state=human owned=yes reason=human-owned-window-internal-allowed`（此事件先于 Extension 动作；现有证据不能归因到用户还是测试 tab/其他 producer）。不要把它静默忽略，也不要未经调用链确认就认定是 Dock 点击。
- 记录此观察不提升 Gate B 的整体状态；Dock 接管仅有一次受控人工观察，24A–I、Assist、C/F、BrowserPilot、发布工作均按上方矩阵保留原状态。用户已要求移交新负责人；本轮不恢复过期定时任务，不构建、不替换当前 App、不 commit/push。当前 Browser CLI PID 52615 仍持有 DevTools 63440；如需 build/runtime，须先由用户确认可关闭此隔离实例。

## 新负责人统一构建窗口（2026-09-24，用户授权关闭 PID 52615 + 本地 commit）

用户明确授权两件事后执行：关闭隔离实例进入构建窗口；23 个未提交文件本地 commit（不 push）。

### 保全与环境

- 本地提交 `4027599`（23 文件：patch 006–013、Gate B/C/F 测试工具、HANDOVER 与文档），未 push。Chromium checkout 仍为 `7d0f28c30d`，browser-cli 分支 `main`。
- PID 52615 核对命令行（隔离 profile `profile.Xn6eWr` + `--use-mock-keychain` + managed 开关 + 测试扩展 + DevTools 63440）后 SIGTERM 温和退出（2s 内），全部子进程与端口一并消失；无残留 watcher/sink（旧 12068/18637 已在前次会话停止）。
- 提取前先验证 worktree 与 001→013 累积临时 index 逐字节一致（rc=0），确认交接基线无损。

### seq=3 internal allow 归因分析（静态，2026-09-24）

- 时间排除成立：动作串脚本 14:41:31 才连接（sink 文件名与首条 `sentAt` 一致），seq=3 发生在 14:38:43，早 2.8 分钟，**不是脚本动作**。
- 事件簇：14:38:38.5 / 14:38:43.895 / 14:38:43.899 三次 `chrome://newtab/` 加载尝试（`new_tab_ui.cc:54` 对 NORMAL profile 拒绝旧版 WebUI NTP），55ms 后 seq=3 internal allow，随后两个新 renderer spawn（其一为 extension renderer，即 ops 扩展页面进程，与 14:41 复用 ops Tab 的行为一致）。
- 旧二进制（001–005）中 `origin=internal` 是 TLS 默认值，任何未 scoped 调用者都是它，本身不携带归因信息；001 明确留有 `ActivateContents`/session restore/远程 app-shim 未归因 TODO。最可能候选：扩展默认 URL 的 tab/window 创建+激活、或用户/DevTools 手工加载 newtab 后的 tab 激活，落在未归因激活路径。
- 处置：重建后（007 默认 `kUnknown`）同类事件 fail-closed 为 deny；Gate B 人工矩阵已新增"用户在 HUMAN 窗口 Cmd+T / '+' / 点 tab"用例验证 007 不误伤用户路径；重建后若仍见 `origin=internal` allow，其来源必为 007 scoped 的三个 AppKit 菜单入口，归因自然收敛。**暂不需要 producer 回溯 patch**。

### patch 014（新增）与 011 REWORK 1

- **014** `014-presentation-origin-default-comment.patch`（sha256 `5a0d9832…`，1 文件 3+/1-，仅注释）：
  `browser_cli_presentation_policy.h` 的 `CurrentForegroundOrigin()` 注释仍写 "Defaults to kInternal"，与 007 之后实际默认 `kUnknown`（fail closed）相反；该注释属 001 的原始内容，不回写冻结的 001，以增量文档 patch 修正。
- **011 REWORK 1**（sha256 `48b13767…`，9 文件 = 原 7 + 2 个 WebUI handler）：统一构建首次编译发现原 011 修改
  `OmniboxEverywhereService::OpenUrl` 签名（新增 `bool user_gesture`）但漏改两个调用方，
  `composebox_everywhere_handler.cc:127` 与 `omnibox_everywhere_handler.cc:391` 报
  `no matching member function`。两处均为 renderer 经 mojo 调入的 WebUI handler（手势为渲染方自报、不可信），
  按 011 既有模型补 `/*user_gesture=*/false`（与 `OpenURLFromTab` 固定 false 同类）。
  提取时用 `git add -N` 处理新文件盲点（否则 unittest 文件会静默丢失且重放比对对其失明）；
  替换后 001→014 全系列重放与 worktree 逐字节一致、worktree 反向 apply 通过、
  checker self-test 8/8 + checkout 6/6 复跑通过。001–010、012–014 sha256 未变。
- 这一编译失败本身是"静态 REVIEW ≠ 可编译"的直接证据，验证了统一构建窗口的必要性。

### 统一构建与签名（001→014 首次全部编译）

- `scripts/build.sh` 首跑：ninja 在两个 011 调用方处失败（上述 REWORK 根因）。
- 修复后重跑：ninja **exit 0**；inside-out Developer ID 签名 **522/522 dylib（Team RAXC5D3A3S）**、
  designated requirement stable、`signature acceptance: PASS`。证据：
  `build-logs/unified-build-20260924/`（build.log、build-retry1.log）与
  `build-logs/build-20260924-151724.log` / `build-20260924-152642.log`。

### C++ 单测（006–014 新增用例首次真实运行）

| 目标 | 结果 |
| --- | --- |
| `browser_cli_presentation_policy_unittests`（含 007 未归因矩阵、008 generation） | **32/32** |
| `browser_cli_foreground_lease_unittests` | **8/8** |
| `browser_cli_managed_presentation_unittests`（AppKit） | **2/2** |
| `foreground_assist_unittests`（含 013 仲裁 8 例） | **25/25** |
| `foreground_assist_controller_unittests`（含 008 ABA 回归） | **14/14** |
| `reopen_window_selection_unittests`（009 单网关/完整集合选窗） | **26/26** |
| `user_explicit_show_unittests`（003） | **14/14** |

合计 **121/121 全部 PASS**（多数为静态编写、此前从未编译运行的用例）。010 的
`AppControllerTest.RunWithForegroundOriginScopesOnlyItsCallback` 与 011 的
`OmniboxEverywhereMacWindowUtilTest.*` 位于 `unit_tests` 大二进制，构建完成后
以 gtest filter 运行（结果见下方追加记录）。

### thin repo 修复与静态门禁

- **readiness checker 跨终端发现缺陷已修**：`browser-readiness-check.py::scan_process_table()`
  的 `ps -ww`（无 `-a/-x`）只列出与调用者同终端的进程，checker 从其它终端/无 TTY 运行时
  永远看到 0 个 profile 进程并 WAIT。改为 `ps -axww`，新增 4 个 fixture（PID 1 可见 =
  TTY-less 进程可发现、caller 自身可见、真实进程表解析、profile-bound 负例），
  self-test **48/48**（原 44/44）。
- **动作串脚本补强**：Detect 报告的 HUMAN 窗口与 ops Tab 所在原生窗口（同一 extension
  API id 空间）现在强制相等，否则拒绝执行；policy log 的 widget id 与 extension API id
  仍属不同空间，仅打印对照。`node --check`、`git diff --check` 通过；修复后的整串仍未复跑
  （需用户在场保持窗口前台）。
- **hygiene checker 分类修正**：`run-human-window-ops-sequence.mjs` 被
  `--remote-debugging-port` 正则误分类为"浏览器启动入口"；实际它是 connect-only 手工取证
  脚本（只校验已运行实例的参数，spawn 的仅被动窗口 watcher 与本地 sink，watcher 经源码复核
  仅用 proc_pidpath 匹配 app identity、不启动任何进程）。按仓库既有 EXCLUDE_NAMES 机制排除
  并注明理由；门禁复跑 **14 入口 OK**。
- 全量离线套件 **229/229**、launch hygiene **OK**、`git diff --check` clean。

### 24D/24F/24H 决策备忘

新增 [`docs/product-decisions-24D-24F-24H-2026-09-24.md`](docs/product-decisions-24D-24F-24H-2026-09-24.md)：
D1（外部 openURL 永不前台，维持 fail-closed）、F1（维持 kUnknown 合并，证据触发再升级）、
H1（逐入口证明+按值传播，剩余为普通逐入口审计任务）为负责人建议，待用户确认后关闭对应 BLOCKED 项。

### 当前边界与下一步

- Gate B 人工矩阵与 Assist runtime 验收尚未执行；本轮一切编译/单测证据不替代 runtime/人工验收。
- 下一步：`unit_tests` 中两个 chrome 级用例运行；然后以新签名构建启动隔离实例（runbook 参数），
  由用户执行 Gate B 人工矩阵（Dock 五时序场景、HUMAN 下用户 tab/菜单操作、扩展动作串复跑、
  交还）与 Assist grant/deny/busy/timeout/接管验收。矩阵新增用例：用户在 HUMAN 窗口
  Cmd+T / '+' / 点 tab 必须仍可用（验证 007 fail-closed 不误伤）。
- 本轮未 push；未运行任何浏览器；未触碰真实钥匙串/profile。
