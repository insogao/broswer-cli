# Browser CLI 项目总进度表

> 本文件是项目持续调度与交接的唯一进度入口。宏观设计以
> [`docs/browser-cli-development-plan.md`](docs/browser-cli-development-plan.md) 为准，强制开发规则以
> [`AGENTS.md`](AGENTS.md) 为准。
>
> 定时跟进不得绑定某个 Phase：完成一项后更新证据并继续下一项。只有全部项目完成、用户明确暂停，
> 或出现必须由用户处理的阻塞时，才可以停止持续跟进。

状态定义：`DONE` 已由主负责人复核；`REVIEW` 子代理完成、待主负责人复核；`DOING` 正在开发；
`TODO` 尚未开始；`BLOCKED` 有明确阻塞。没有可复核证据不得标为 `DONE`。

## 总览

| 工作流 | 状态 | 当前结论 | 下一动作 |
| --- | --- | --- | --- |
| 设计、不变量与开发纪律 | DONE | 状态机、来源模型、行为矩阵、Gate、测试/签名禁令已落盘 | 后续改动持续对照，不得绕过 |
| 可复现 Chromium 开发环境 | DONE | 固定 revision、Xcode 27、首次及增量构建均已验证 | 跨机器复现时重新执行环境门禁 |
| 产品身份、Logo 与 Developer ID 签名 | DONE | `Browser CLI.app`、固定 Bundle/Team/证书身份、inside-out 签名与验收已通过 | 发布阶段再做 Hardened Runtime、notarization、staple |
| 后台窗口绝不上屏基础能力 | DONE | Gate A 完整矩阵已由主负责人逐项复核：真实网络/Cookie、Tab/Window、window.open、Extension focus、session restore、启动/崩溃恢复均通过 | 后续改动持续跑 Gate A 回归，进入 USER_EXPLICIT/HUMAN Gate B |
| 用户显式打开与 HUMAN 所有权 | DOING | per-window `USER_EXPLICIT`/HUMAN 所有权（001）已由主负责人复核；CLI 显式 `show` 生产者（003 + 14 单测）已冻结入库并标 REVIEW；runtime/人工 Gate B 矩阵未执行 | 由用户执行人工 Gate B runbook（含 CLI show 既有窗口/零窗口用例）并 collect 取证 |
| Foreground Assist | REVIEW | Slice 1（策略层 lease）与 Slice 2（producer + host 接入 + 超时计时器，已按两轮复核意见修正）已冻结为 004/005 并标 REVIEW：单实例 per-window lease、显式 requester origin 校验、busy/invalid/HUMAN-owned 拒绝、TTL 半开区间惰性过期、用户接管先释放后 HUMAN；CLI 双入口语义一致（冷启动在 `Start` 前用 sanitizer 去掉 URL/app 副作用并加 `--no-startup-window`，ProcessSingleton handler-first + consumed），assist 意图一律 consumed，tab 目标显式拒绝，target 限顶层 BrowserWindow；grant→present、release/timeout→只回退目标窗口，ASSIST→HUMAN 显式取消计时器（呈现成功后才取消），HUMAN-owned 绝不 hide；**2026-09-23 当前 App 已完成 Developer ID 签名与验证，但无浏览器 runtime/可见 UI 证据；冷启动无副作用仅源码级证明** | 主负责人复核 004/005；随后用签名 App 做隔离 runtime 验证（grant/present/timeout/release/接管后旧 deadline 不 hide；冷启动 blocked 负例零可见窗口），再推进 D 区其余条目 |
| 后台持续渲染与截图 | TODO | Gate C 的 dry-run 计划与离线判定器已交付 REVIEW；无执行器、无真实截图。第 2 次返工已提交静态证据（固定必选矩阵、watcher 原始样本），待主负责人复核；禁止用显示窗口换截图 | 复核判定器，再实现隔离执行器，最后验收动态页面、Canvas、长页与连续截图；视频缺确定性素材时 NOT-EVALUATED |
| 后台多窗口并行 | TODO | 未实现完整矩阵 | 独立 Window/Tab/active tab/截图，桌面窗口数始终为 0 |
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

- [ ] 定义并贯通 `USER_EXPLICIT`、Agent、Extension、WebPage、Internal、SessionRestore 来源。
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
- [ ] 用户点击 Dock 可打开、激活既有 Browser CLI 窗口。
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
  空证据 rc=3。主负责人尚未完成独立复核；若再发现假通过则标 BLOCKED，不循环重派。
  Gate C 保持 TODO，且尚无任何真实截图 runtime 证据。
- **Dock 重开耗时归因**：OpenCode B 新会话 `ses_f333cf55bffe9TRDONwscWoWS4`
  正在制作仅用于记录的 Chromium patch 006；当前热态迟滞根因仍未证实，尚无点击到上屏
  的同钟证据。此项正在开发，不计入本次已验收进度。

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
