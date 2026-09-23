# Phase 2 BACKGROUND_PRESENTATION 第一切片（2026-09-22）

本文件记录 Phase 2 第一个可审查里程碑：**在 Chromium 内核层实现 BACKGROUND_PRESENTATION 的最小纵向切片**。

状态：**代码完成，静态/单元/增量/无可见窗口 runtime smoke 全部通过；Gate A 在本切片范围内成立（见 §7 未证明项）。2026-09-22 按主负责人验收意见完成“测试钥匙串/权限隔离”修正（§9）：所有测试入口强制 `--use-mock-keychain` + 临时 `--user-data-dir`，并新增静态门禁；隔离 smoke 仅复跑一次，出现测试脚本自身缺陷，如实记录在 §9。未提交 Git commit，等待主负责人复核。**

## 1. 范围与非目标

### 范围（本切片）

1. 真实 `BrowserWindow` / `WebContents` / Renderer 可以创建和加载；
2. 「后台来源」的 native window 从诞生起**绝不** order on screen / Show / Activate / Raise / Focus；
3. 每次 visibility 决策都有结构化日志可归因；
4. 保留 USER_EXPLICIT 通道（Dock/Launchpad reopen 永远能打开），不得用全局禁止显示把用户挡在门外；
5. 可复现 patch + 无可见窗口的运行时验证。

### 非目标（明确留给后续 patch / Phase）

- Phase 3：完整的 per-window `USER_EXPLICIT` 来源归因与 `ForegroundLease`（本切片只提供最小枚举 + 显式 API + TODO）。
- Phase 4：login/captcha Assist 状态（`kAssist` 仅保留枚举位）。
- Phase 5：后台 compositor / screenshot（本切片不证明截图可用）。
- 远程 app shim（PWA）窗口与 `--headless` 组合不在本切片覆盖内（见风险）。
- 不改 Backlight，不改 BrowserPilot，不做 branding/launcher。

## 2. 关键调用链（macOS / MacViews）

Chromium 156 中，所有「让窗口上屏」的请求最终汇聚到：

```text
views::Widget::Show() / ShowInactive() / Activate()
  → ui::NativeWidgetMac::Show() / Activate()
    → ui::NativeWidgetMacNSWindowHost::SetVisibilityState(kShowAndActivateWindow
                                                         | kShowInactive
                                                         | kMinimizeWindow
                                                         | kHideWindow)
      → remote_cocoa::mojom::NativeWidgetNSWindow
        → (in-process) remote_cocoa::NativeWidgetNSWindowBridge::SetVisibilityState()
          → [NSApp activateIgnoringOtherApps:] / [window makeKeyAndOrderFront:]
          / [window orderWindow:NSWindowBelow ...] / [window orderFrontKeepWindowKeyState]
```

补充入口：

- `window.open` / Extension `chrome.windows.create({focused:true})` / session restore /
  `Browser::Show()` 都走 `Widget::Show()`，被同一条链覆盖；
- AppKit/外部框架（如 AuthenticationServicesCore）可能直接对 `NSWindow` 调
  `orderWindow:relativeTo:`、`orderFront:` 等，绕过 Views 层。

因此本切片采用三层防线：

```text
Layer 1  policy     ui/views/widget/browser_cli_presentation_policy.{h,cc}
                    state + origin + action，产生唯一的结构化决策日志
Layer 2  host       ui/views/cocoa/native_widget_mac_ns_window_host.mm
                    真正 native presentation 的汇合点；拒绝时直接 return，不触碰 NSWindow
Layer 3  appkit     components/remote_cocoa/app_shim/native_widget_mac_nswindow.{h,mm}
                    per-window 硬开关：order-in / key / miniaturize / fullscreen 全部吞掉
         bridge     components/remote_cocoa/app_shim/native_widget_ns_window_bridge.mm
                    兜底：被标记的窗口在 bridge 层直接拒绝非 hide 的 visibility 变化
```

## 3. 策略语义

开关（仅显式启用，默认对上游行为零影响）：

```text
--browser-cli-background-presentation
```

状态（`BrowserCliPresentationState`）：

| 状态 | 含义 | 内部来源 present | 用户显式 present |
| --- | --- | ---: | ---: |
| `kUnmanaged` | 未加开关（原版 Chromium 行为） | 是 | 是 |
| `kBackground` | 后台运行，桌面零显示 | 否 | 是 |
| `kHuman` | 用户已显式接管（Dock reopen 等） | 是（见已知缺口） | 是 |
| `kAssist` | 预留（Phase 4），本切片不存在 | — | — |

来源（`BrowserCliPresentationOrigin`，最小子集）：

```text
kInternal        # 本切片所有内部调用（含 Agent / Extension / WebPage / SessionRestore，尚未细分）
kUserExplicit    # Dock / Launchpad / 菜单栏 / CLI show（Phase 3 全面接管）
```

约束：

- `kUserExplicit` 永远 allow（设计 §5.2）；
- `kBackground` 下 `kInternal` 的 `kShowAndActivate` / `kShowInactive` / `kMinimize` 全部 deny；
- `kHuman` 下 allow（保护 Dock reopen），并留有明确 TODO：Phase 3 必须在 `kHuman`
  下继续拒绝 Agent 发起的展示（per-window lease）；
- `Hide()` 不做限制（G 层要求是「不得自动隐藏用户窗口」，需要来源归因，留给 Phase 3）。

## 4. 结构化日志

统一前缀 `BROWSER_CLI_PRESENTATION`，两类 stage：

```text
BROWSER_CLI_PRESENTATION stage=policy seq=… decision=allow|deny action=show-and-activate|show-inactive|minimize \
  origin=internal|user-explicit window_id=… state=unmanaged|background|human reason=…
BROWSER_CLI_PRESENTATION stage=host    decision=deny action=… window_id=… state=… reason=…
BROWSER_CLI_PRESENTATION stage=appkit  decision=deny action=order-front|order-back|order-window|make-key-and-order-front|make-key|miniaturize|fullscreen \
  widget_id=… window_number=…
BROWSER_CLI_PRESENTATION stage=bridge  decision=deny action=set-visibility window=… new_state=…
```

判定标准：BACKGROUND 期间，runtime log 中出现任何 `stage=* decision=allow`（`origin=internal`）
即视为 Gate A FAIL；出现真实 OS 窗口同样 FAIL（本次用 `CGWindowList` 独立验证）。

## 5. 修改点清单

> 注（2026-09-23）：`001-managed-window-mode.patch` 已刷新为 Gate A + Gate B 的非 branding
> 全量切片（见 §追加 4）；下表是 2026-09-22 第一切片的历史描述，命名（如
> `BeginUserExplicitTakeover`/`kAssist`）已被 per-window 所有权模型取代。

Chromium（对应 `chromium/patches/001-managed-window-mode.patch`）：

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `ui/views/widget/browser_cli_presentation_policy.{h,cc}` | 新增 | 策略单例：开关解析、origin/action/state、`AllowPresentation` 唯一决策点、结构化日志、`BeginUserExplicitTakeover`/`ReturnToBackground`、测试钩子 |
| `ui/views/cocoa/native_widget_mac_ns_window_host.{h,mm}` | 修改 | `SetVisibilityState` 改为返回 `bool`；show/show-inactive/minimize 在调用任何 AppKit 之前先过策略；拒绝时重新武装 per-window 抑制并直接返回（不触碰 NSWindow）；窗口创建时在 managed 模式下落抑制标记 |
| `ui/views/widget/native_widget_mac.mm` | 修改 | `Show()` 使用 gate 结果：被拒绝时不调用 `SetInitialFocus` |
| `components/remote_cocoa/app_shim/native_widget_mac_nswindow.{h,mm}` | 修改 | 新增 `managedPresentationSuppressed`；AppKit 层拦截 `orderWindow:relativeTo:`（仅 order-in）、`orderFront:`、`orderBack:`、`orderFrontRegardless`、`makeKeyAndOrderFront:`、`makeKeyWindow`、`miniaturize:`、`toggleFullScreen:`，全部记录 `stage=appkit` 日志 |
| `components/remote_cocoa/app_shim/native_widget_ns_window_bridge.mm` | 修改 | bridge 层兜底：被标记窗口的非 hide `SetVisibilityState` 直接拒绝并记录 `stage=bridge` |
| `chrome/browser/app_controller_mac.mm` | 修改 | Dock/Launchpad/App reopen 入口调用 `BeginUserExplicitTakeover`（USER_EXPLICIT），保留 TODO(phase3) 做 per-window lease；不把 `applicationDidBecomeActive` 视为用户点击 |
| `ui/views/BUILD.gn` | 修改 | 策略 source_set + 作为 `//ui/views` 组件依赖 + 策略单测目标 |
| `components/remote_cocoa/app_shim/BUILD.gn` | 修改 | 引入 `//testing/test.gni` + NSWindow 抑制单测目标 |
| `ui/views/widget/browser_cli_presentation_policy_unittest.cc` | 新增 | 策略状态机单测（5 项） |
| `components/remote_cocoa/app_shim/browser_cli_managed_presentation_unittests.mm` | 新增 | AppKit 层硬停止单测（2 项，离屏 frame，不会显示窗口） |

browser-cli 仓库：

| 文件 | 内容 |
| --- | --- |
| `tests/phase2-background-presentation-smoke.sh` | Gate A 运行时 smoke：`--no-startup-window` 启动 → DevTools HTTP `/json/new` 创建真实后台窗口 → 校验渲染器 JS 标题 → `CGWindowList` 校验该 PID 桌面零窗口 → 校验结构化日志 → 结束进程。默认 dry-run，`--run` 才执行 |
| `tests/tools/onscreen-window-count.m` | 按 PID 统计 on-screen 窗口的 CoreGraphics 小工具（smoke 时用 `xcrun clang` 临时编译） |

## 6. 构建与测试命令（实测）

环境：Apple M4 / macOS 27.0 / Xcode 27，Chromium revision `7d0f28c30d1c0fadbce17ecec0ee2565aef9a940`，唯一 `out/Dev`（component build）。

```sh
# 0) 生成构建文件（会校验 args.gn 与唯一 out 目录）
scripts/configure.sh
# → Done. Made 36256 targets from 5016 files（基线 36251，新增 5 个 browser-cli target）

# 1) 增量编译（含新增文件；disk watchdog 始终生效）
scripts/build.sh
scripts/build.sh --target browser_cli_presentation_policy_unittests
scripts/build.sh --target browser_cli_managed_presentation_unittests

# 2) 单元测试（不会创建任何可见窗口；NSWindow 单测使用离屏 frame 且全程保持抑制）
out/Dev/browser_cli_presentation_policy_unittests --gtest_color=no
out/Dev/browser_cli_managed_presentation_unittests --gtest_color=no

# 3) 静态检查
/Users/jiangao/dev/depot_tools/gn check out/Dev //ui/views:views
/Users/jiangao/dev/depot_tools/gn check out/Dev //components/remote_cocoa/app_shim:app_shim
git diff -U0 | python3 third_party/clang-format/script/clang-format-diff.py -p1 \
  -binary /Users/jiangao/dev/depot_tools/clang-format    # 无输出 = 无格式残留
bash -n ../browser-cli/tests/phase2-background-presentation-smoke.sh

# 4) Gate A runtime smoke（默认 dry-run；--run 才启动进程，全程不打开可见窗口）
tests/phase2-background-presentation-smoke.sh --run
```

### 实测结果

| 项目 | 结果 |
| --- | --- |
| `gn gen` | PASS，36256 targets |
| `ninja chrome` 全量增量 | PASS（exit 0；`libui_views` / `libchrome_dll` / `Chromium` 重新链接） |
| policy 单测 | 5/5 PASS（0s） |
| AppKit 抑制单测 | 2/2 PASS（<1s） |
| `gn check` × 2 目标 | PASS（Header dependency check OK） |
| 增量探针 | 在 `browser_cli_presentation_policy.cc` 追加注释 → `[1/41] CXX policy.o` + `[2/41] SOLINK libui_views.dylib`，TOC 未变化故下游 41 edges 全部 restat 跳过，exit 0（约 1 分钟）；用备份逐字节恢复（sha256 `d8f785a3…` 一致）后重建 exit 0，再跑 `ninja chrome` → `ninja: no work to do.` |
| Gate A smoke | **PASS**（历史日志 `build-logs/phase2-smoke-20260922-0945.log`；该次缺 `--use-mock-keychain`，隔离修正后的单次复跑见 §9） |
| `Chromium --version` smoke | PASS，`Chromium 156.0.8067.0`，无窗口、无 `open`/Dock（当时同样未加 mock keychain，已按 §9 修正） |

runtime smoke（2026-09-22 09:45；**历史记录：该次尚未加 `--use-mock-keychain`，已按 §9 修正并复跑**）关键取证：

```text
[smoke] on-screen windows before target creation: 0
[smoke] on-screen windows after target creation: 0
smoke.check name=desktop-onscreen-windows-zero result=PASS detail=0
smoke.check name=renderer-js-title result=PASS detail=yes
smoke.check name=host-suppressed-at-creation result=PASS detail=yes
smoke.check name=policy-denied-internal-show result=PASS detail=yes
smoke.check name=no-internal-allow-in-background result=PASS detail=yes

BROWSER_CLI_PRESENTATION stage=policy transition=background reason=command-line-switch
BROWSER_CLI_PRESENTATION stage=host decision=suppress action=window-created widget_id=1..5 reason=managed-mode-at-creation
BROWSER_CLI_PRESENTATION stage=policy seq=1 decision=deny action=show-and-activate origin=internal window_id=1 state=background reason=background-suppresses-native-presentation
BROWSER_CLI_PRESENTATION stage=host decision=deny action=1 widget_id=1 reason=policy-denied
```

渲染器确实执行了 JS：DevTools 目标标题为内联脚本计算出的
`BROWSER_CLI_SMOKE_RENDERER_OK_42`。

## 7. Gate A 证据与未证明项

### 已证明（本切片）

1. 真实 `BrowserWindow` / `WebContents` / Renderer 可以创建并加载页面、执行 JS（DevTools target + 内联脚本标题）。
2. 后台窗口进程**桌面零显示**：`CGWindowList` 统计该 PID 的 on-screen 窗口，建窗前/建窗后均为 0。
3. 后台内部 `show-and-activate` 请求被策略拒绝，且日志证明**拒绝发生在触碰 NSWindow 之前**（`stage=host` 后即返回）。
4. 没有「先显示再隐藏」「最小化补救」「移出屏幕」：拒绝即原样返回，smoke 中不存在任何可见窗口，也没有 watchdog/补偿代码。
5. USER_EXPLICIT 通道存在且不会被全局策略封死：Dock/Launchpad/App reopen 调用 `BeginUserExplicitTakeover`（单测覆盖状态转换），策略层对 `kUserExplicit` 永远 allow。
6. 三层防线可独立验证：policy 单测、AppKit 抑制单测（order-in/key/miniaturize/fullscreen 全部拒绝）、bridge 兜底。

### 未证明 / 明确留给后续

1. **未做真实 Dock/Launchpad 点击的 runtime 验收**（Phase 3 任务；需要用户在场）。本切片只保证入口存在、状态转换单测通过。
2. `window.open` / Extension `windows.create({focused:true})` / session restore 未逐一 runtime 取证；它们都汇聚到同一个 `NativeWidgetMacNSWindowHost::SetVisibilityState` gateway，但按设计应在 Phase 3 用自动化矩阵逐项验收。
3. **后台 compositor / screenshot（Gate C）未验证**：native 不可见时窗口处于 hidden 语义，renderer 可能被后台节流，Phase 5 处理；本切片不允许用显示窗口换取截图。
4. **远程 app shim（PWA）窗口**：浏览器进程侧的 host 策略与 bridge 兜底仍生效，但 AppKit 层的 `managedPresentationSuppressed` 标记没有经过 mojom 传到 app shim 进程，远程 NSWindow 未被标记（见风险 5）。
5. `--headless` 组合未测（headless 有自己的 NSWindow swizzler，未验证与本切片 override 的叠加行为）。
6. 多后台窗口并行、CPU/GPU/内存、真实用户可用性（菜单栏/Dock 行为）均未验证。
7. HUMAN 状态下对既有 suppressed 窗口的直接 AppKit 排序（如 `FocusWindowSetOnCurrentSpace`）仍会被 AppKit 层拦截，直到该窗口经 `SetVisibilityState` allow 解锁；Phase 3 per-window lease 会统一解决。

## 8. 剩余风险

1. **`kHuman` 仍粗粒度**：HUMAN 状态下 origin 仍是 `kInternal`，未归因的 Agent show 会被放行。这是 Phase 3（patch 002/003）必须收口的第一风险；policy 与 host 中均留有明确 TODO。
2. **managed 模式下所有 widget 一律抑制**（smoke 中建窗时 5 个 widget 均 suppress）。Phase 3 需要 per-window/per-purpose 粒度（例如 DevTools、权限提示、用户自己打开的窗口）。
3. **AppKit 未覆盖路径**：私有 API（如 `setIsVisible:`）、Accessibility 提升、或系统框架直接操作窗口仍可能绕过公开方法拦截。本切片刻意不做 watchdog；一旦发生，smoke 的 `CGWindowList` 计数与人工检查会直接暴露为 FAIL（fail closed），而不是事后隐藏。
4. **日志量**：managed 模式下每次决策都打 `LOG(INFO)`，尚未限流/采样（已在实现中留待后续处理）。
5. **远程 app shim 缺口**：若 Phase 4/7 需要 PWA 后台窗口，必须把 managed 标记加进 `CreateWindowParams` mojom 并让 app shim 侧 bridge 落标记。
6. patch 与工作区可复现性：`chrome/browser/app_controller_mac.mm` 的 USER_EXPLICIT 入口是刻意的提前保护（否则开关一开 Dock 就打不开），Phase 3 会用正式 origin/lease 替换；reviewer 若希望更窄，可将该 hunk 拆到 `003-user-explicit.patch`，其余保持不变。

## 9. 测试卫生修正（2026-09-22）：Chromium Safe Storage 弹窗根因与门禁

### 事实

主负责人验收时发现：Phase 2 runtime smoke（以及 Phase 1 的 `--version` smoke）启动的是本机
ad-hoc / linker-signed 的 Chromium，**没有加 `--use-mock-keychain`**，导致 macOS 弹出
“Chromium Safe Storage” 钥匙串密码框。编译本身不需要钥匙串，这个交互没有任何意义。

### 根因

- 开发构建的代码签名是 ad-hoc / linker-signed，签名每次重建都可能变化；
- macOS 钥匙串 ACL 绑定的是稳定签名与稳定路径，因此进程无法匹配既有 `Chromium Safe Storage` 条目；
- Chromium 的 OSCrypt 在初始化时会读取/创建该条目，于是系统弹出密码框要求用户授权——这是
  **测试行为的缺陷**，不是环境问题。

历史证据（仅记录，禁止操作）：既有钥匙串 ACL 显示旧测试曾从随机路径
`/private/var/.../T/bl-wstate-*/apps/Backlight.app` 访问真实钥匙串。该做法已被永久禁止：
不得读取、写入、删除或修改任何既有钥匙串条目，也不得去“清理”它们。

### 强制规则（对后续 AI 生效，见 `AGENTS.md`）

1. 任何测试/自动化/smoke 启动 Chromium/Backlight，必须同时：
   `--use-mock-keychain` + `--user-data-dir="$(mktemp -d ...)"`，禁止复用用户 profile。
2. 禁止要求用户点“允许”、输入密码或处理系统对话框；出现一次即立即停止并 FAIL。
3. **生产 Browser CLI 不得使用 `--use-mock-keychain`**：正式版依靠稳定代码签名 + 稳定安装路径换取
   钥匙串 ACL（Phase 9）。mock keychain 只用于测试。
4. 静态门禁 `tests/check-browser-launch-hygiene.sh` 扫描所有会启动浏览器的测试入口，缺少任一参数即 FAIL；
   `tests/run-script-tests.sh` 内置正/负例自测。

### 修正内容

- `tests/phase2-background-presentation-smoke.sh`：启动命令加入 `--use-mock-keychain`；profile 强制位于本次
  `mktemp -d` 临时目录内（硬 guard）；运行中抓取 `ps` cmdline 并断言两个隔离参数存在；文档写明规则。
- `scripts/verify-phase1.sh`：`--version` smoke 加入 `--use-mock-keychain` + 临时 `--user-data-dir`，
  退出时清理临时 profile。
- 新增 `tests/check-browser-launch-hygiene.sh`（静态门禁）与 `AGENTS.md`（后续 AI 强制规则）；
  `tests/run-script-tests.sh` 增加第 12/13 组门禁与正/负例（45 项）。
- `README.md` / `tests/README.md` / 本文件：记录规则、根因与生产约束。

### 隔离复跑（单次，2026-09-22 ~09:56）

按“最多一次已隔离 smoke”的预算，只运行了一次 `tests/phase2-background-presentation-smoke.sh --run --keep-tmp`。
结果分两部分，如实 fail-closed 报告：

**隔离与 Gate A 证据（PASS）：**

```text
[smoke] browser cmdline: …/Chromium --no-startup-window … --use-mock-keychain \
  --user-data-dir=/var/folders/.../browser-cli-smoke.NTTu7e/profile \
  --remote-debugging-port=0 --enable-logging=stderr --browser-cli-background-presentation
[smoke] on-screen windows before target creation: 0
[smoke] on-screen windows after target creation: 0
smoke.check desktop-onscreen-windows-zero            PASS
smoke.check renderer-js-title                        PASS
smoke.check host-suppressed-at-creation              PASS
smoke.check policy-denied-internal-show              PASS
smoke.check no-internal-allow-in-background          PASS
```

- 运行中 `ps` 抓取的 cmdline 同时包含 `--use-mock-keychain` 与本次 `mktemp -d` 的
  `--user-data-dir`；profile 目录随后被删除；
- chromium.log 中不存在 `keychain` / `OSCrypt` / `Safe Storage` 相关行；
- **本次运行未出现、也未要求任何钥匙串、权限或其它系统对话框**；一旦出现会立即停止并 FAIL；
- 证据日志：`build-logs/phase2-smoke-isolated-20260922.log`（chromium.log），
  `build-logs/phase2-smoke-isolated-20260922-run.txt`（运行 stdout 记录，附说明）。

**烟雾驱动判定（FAIL，测试脚本缺陷，非产品/隔离缺陷）：**

新增的两个 cmdline 断言用了 `$(case "$CMDLINE" in *--use-mock-keychain*) …)`；bash 会把
`*…*)` 中的 `)` 误判为命令替换的结束符，导致断言拿到垃圾字符串并报告 FAIL。修复为
`printf | grep -q`，离线验证了正例 + 3 个负例；由于“最多一次隔离 smoke”预算已用尽，
**没有再跑第二次浏览器**。因此该次 smoke 驱动的退出码是 FAIL，而隔离参数与桌面零窗口的事实
证据如上（可由 `run.txt` 复核）。建议主负责人复核后，再授权一次干净复跑以取得 PASS 横幅。

### 追加（2026-09-22）：产品身份与签名

产品名/Bundle ID/Team ID 与稳定签名已落到
[`signing-and-product-identity-2026-09-22.md`](signing-and-product-identity-2026-09-22.md)：
patch 002 把产品改名为 `Browser CLI`（`com.jiangao.browsercli`，team `RAXC5D3A3S`），
`build.sh` 之后强制签名并验收，测试入口拒绝 ad-hoc。改名后需重新构建；旧
`out/Dev/Chromium.app` 为过期 ad-hoc 产物，不得用于测试/验收，本文档 §6 的构建命令仍适用
（脚本已按产品身份自动解析新 App 路径）。

### 追加 2（2026-09-22）：签名后的 App Gate A 复验

`002-browser-cli-branding.patch` + Logo 图标 + Developer ID 实际签名完成后（见
`docs/signing-and-product-identity-2026-09-22.md` §8），用签名后的 `out/Dev/Browser CLI.app`
重跑本文件 §9 的 smoke：**7/7 检查 PASS**（桌面 on-screen 窗口 0、mock keychain 与独立临时
profile 均在运行 cmdline 中、renderer JS 正常、策略 suppress/deny 日志齐全），未出现任何
系统弹窗。首次冷启动因重新校验约 1600 个已签名二进制的签名而超过 180s 超时（非产品缺陷、
无弹窗），smoke 默认超时已提高到 600s 并新增提前退出/僵尸检测。证据：
`build-logs/phase2-smoke-signed-20260922.{log,run.txt}`。

### 追加 3（2026-09-22）：真实 HTTPS 后台网络响应证据（PROJECT-PROGRESS B 区，REVIEW）

条目：**“真实 HTTPS 网站网络响应在后台完成并有内容证据”**（未标 DONE，待主负责人复核）。

- 测试：`tests/phase2-https-background-network.sh`（默认 dry-run，`--run` 单次隔离运行）；
  启动前断言 Developer ID 签名、网络预检，运行中强制 `--use-mock-keychain` + 独立临时 profile。
- 单次运行结果（11/11 PASS）：签名 `Browser CLI.app` 后台加载 `https://example.com/`；
  DevTools `/json/list` 显示 `url=https://example.com/`、DOM 标题 `Example Domain`；
  NetLog 源站证据 `:authority: example.com` + `:scheme: https` 与
  `:status: 200` + `content-type: text/html`；全程 CGWindowList 采样该 PID on-screen 窗口数为 0；
  策略日志为 suppress/deny，BACKGROUND 下无 internal allow；无钥匙串/系统弹窗。
- 证据路径：`build-logs/phase2-https-20260922-140823/`
  （`summary.txt`、`netlog.json`、`netlog-origin-request.txt`、`netlog-origin-200-html.txt`、
  `devtools-list.json`、`window-samples.txt`、`chromium.log`、`post-run-notes.txt`、`sha256.txt`）。
- 说明：运行时通用 `HTTP/<n> 200` 断言可能被代理 CONNECT 行满足；事后已用同一份不可变 NetLog
  抽取源站 200+`text/html`，脚本门禁改为源站特异性模式。浏览器经系统代理 CONNECT 隧道访问，
  TLS 仍在 example.com 终止。

### 追加 4（2026-09-23）：Gate A + Gate B 源码状态打包为刷新后的 001（构建/单测证据）

主负责人验证通过的 checkout（Gate A + Gate B，非 branding）已原样打包进
`chromium/patches/001-managed-window-mode.patch`；`002-browser-cli-branding.patch` 保持不变。

**打包与重放校验（不改工作区、不构建）**

- 001：23 个文件（14 改 + 9 新增），1698 insertions / 8 deletions，
  sha256 `5e1bd1e47a7a180dddfce28aca43eea2b801dd56c0f184fa2a717c8cec99aafb`；
  002：sha256 未变 `acd496a18a0c2d1c2133b2cf1848974e6d40332bc4f959a5e989e19a45cccf61`。
- 以固定 revision `7d0f28c30d1c0fadbce17ecec0ee2565aef9a940` 为基线，用
  `GIT_INDEX_FILE=$(mktemp)` + `git read-tree HEAD` + `git apply --cached`（临时 index，
  不 checkout、不构建）顺序重放 001→002：两个 patch 的 `--check` 与 apply 均 exit 0，且
  `git diff --quiet` 证明临时 index 与验证通过的 checkout 对所有 tracked 文件逐字节一致；
  仅生成图标 `chrome/app/theme/browser_cli/mac/{app.icns,Assets.car}` 仍为未跟踪文件
  （按设计不进 patch）。
- 001 对 checkout 的反向校验 `git apply --check -R` 通过；002 反向校验同样通过。

**构建证据（主负责人复核）**

- 全量增量构建：`./third_party/ninja/ninja -C out/Dev chrome` 连续两次
  （2026-09-23 00:38:13 / 00:39:22）exit_code=0，第二次 `ninja: no work to do.`；
  日志 `build-logs/gate-b-compile-20260922-2307/final-build-full-product.log`。

**单测证据**

| 目标 | 结果 | 证据 |
| --- | --- | --- |
| `browser_cli_presentation_policy_unittests` | **12/12 PASS**（含 `BoundContinuationReattachesOriginWithoutLeaking`），EXIT_CODE=0；二进制重建于 2026-09-23 01:56:46，晚于全部源文件 | `build-logs/gate-b-compile-20260922-2307/final-run-browser_cli_presentation_policy_unittests-12tests.log` |
| `browser_cli_managed_presentation_unittests` | 2/2 PASS，EXIT_CODE=0（离屏 frame，不显示窗口、不启动浏览器） | `.../final-run-browser_cli_managed_presentation_unittests.log` |
| `reopen_window_selection_unittests` | 8/8 PASS，EXIT_CODE=0 | `.../final-run-reopen_window_selection_unittests.log` |

- policy/reopen 单测为纯进程内状态机（不创建 native window、不访问钥匙串）；本次打包时对
  policy 目标做了一次直接复跑取证（12/12，见上表）。
- clang-format / `git diff --check` 对全部改动与新增文件 CLEAN：
  `build-logs/gate-b-compile-20260922-2307/final-static-checks.log`。

**范围说明**：001 中 `ui/views/cocoa/native_widget_mac_ns_window_host.mm` 含一处仅格式变化
（`accessibilitySetPresenterProcessIdentifier` 选择器换行，无逻辑变化），按验证状态原样保留。

### 追加 5（2026-09-23）：CLI 显式 `show` 生产者并入 patch 003（REVIEW）

条目：**“CLI 显式 `show` 可打开指定目标”**（PROJECT-PROGRESS C 区）。本轮只做**非 runtime
集成**：把主负责人验证通过的 CLI-show 源码冻结为 `003-user-explicit.patch`，并补齐
runbook、collector 与静态门禁；**没有启动浏览器、没有使用真实 profile/钥匙串、没有签名或 commit**。

**patch 与重放证据**

- `chromium/patches/003-user-explicit.patch`：9 个文件（4 改 + 5 新增），597 insertions /
  78 deletions，sha256 `9852de6570056b0f4d5bc72749e9dc17d54fb42d7e50f03017db54f8d8192e43`。
  003 = 固定 revision `7d0f28c30d1c` + 001 + 002 → 当前 CLI-show 源码的精确 delta；除新增
  纯分类器（`user_explicit_show.{h,cc}` + 14 个单测）外，还把 001 里 `app_controller_mac.mm`
  的 Dock/Launchpad/reopen 展示 helper 迁入共享 ObjC++ 单元
  `chrome/browser/browser_cli/user_explicit_presentation.{h,mm}`（ObjC++ 改名 +
  `ui/base/base_window.h`（`ui::BaseWindow`）include 修正），所以 003 含 78 行删除。
- 提取可重复：同一临时 index 与 worktree 两次生成 `git diff` 逐字节一致。
- 无 checkout 重放：`GIT_INDEX_FILE=$(mktemp)` + `git read-tree HEAD` + `git apply --cached`
  顺序应用 001→002→003（每个先 `--check`）后 `git diff --quiet`，证明临时 index 与当前已验证
  checkout 的所有 tracked 文件一致；仅生成的
  `chrome/app/theme/browser_cli/mac/{app.icns,Assets.car}` 未跟踪（按设计不入 patch）。
- 入口接线（源码）：冷启动 `ChromeBrowserMainParts::PreMainMessageLoopRunImpl` 与第二次直连
  调用经 ProcessSingleton 的 `ProcessSingletonNotificationCallbackImpl` 共用
  `HandleUserExplicitShowRequest()`；`--enable-automation`/`--headless`/URL/app/incognito 一律
  判 `kBlockedByLaunchArguments`（fail closed，绝不进入 USER_EXPLICIT）。

**主负责人证据（本次非 runtime 集成沿用）**

- `user_explicit_show_unittests` **14/14 PASS**；`browser_cli_presentation_policy_unittests`
  12/12、`browser_cli_managed_presentation_unittests` 2/2、`reopen_window_selection_unittests`
  8/8；ObjC++ 改名与 `BaseWindow` include 修正后 `autoninja -C out/Dev chrome` exit 0。
- 本仓库静态门禁：`tests/run-script-tests.sh` **149/149 PASS**（第 23 组新增 CLI show runbook
  检查，第 24 组新增确定性 producer fixture 分类）、`tests/check-browser-launch-hygiene.sh`
  OK（11 入口）、`tests/check-identity-consistency.sh` OK。

**runbook 与 collector**

- `tests/phase3-human-ownership-manual.sh prepare` 生成的 runbook 追加两个**用户执行**的直连
  二进制用例（脚本只打印命令、从不执行）：第 8 步“运行中的既有窗口”（ProcessSingleton 转发，
  期望 `result=handled reason=existing-window-presented`）与第 9 步“零窗口 /
  `--no-startup-window`”（期望 `result=handled reason=window-created-and-presented`）；第 10 步
  为可选负例（show + URL，必须 `result=denied reason=launch-arguments-not-user-explicit`）。
- `tests/tools/human-ownership-collect.py` 解析
  `BROWSER_CLI_USER_EXPLICIT_SHOW stage=producer`，输出
  `cli-show-existing-window-presented` / `cli-show-zero-window-created-and-presented` /
  `cli-show-denied-launch-arguments` / `cli-show-producer-consistency`；`failed`、managed 不符、
  或 handled 但日志缺 `origin=user-explicit` allow 判 `CONTRADICTION`（rc=1）；无 producer 证据
  只判 `NOT-EVALUATED`。

**已知限制（保持 REVIEW，不得据此转 DONE）**

- **没有真实运行实例的可见验证**：CLI show 的既有窗口/零窗口呈现、Dock/Launchpad 点击、
  HUMAN 隔离等人工 Gate B 矩阵尚未执行，`phase3-human-ownership-manual.sh collect` 尚无真实
  会话报告；
- `kAgentAssist` 仍无生产者，session restore、`ActivateContents`、远程 app-shim 窗口仍未归因；
- 003 的 producer 只在 macOS（`BUILDFLAG(IS_MAC)`）路径生效。

### 追加 6（2026-09-23）：Foreground Assist Slice 1 策略层 lease 并入 patch 004（REVIEW）

条目：**“`requestForeground` API 和 `ForegroundLease` 数据结构”等 D 区条目的策略层切片**。
本轮只做**非 runtime 集成与证据归档**：把主负责人复测通过的 Slice 1 冻结为
`004-foreground-lease.patch`；**没有启动浏览器、没有 build/gn、没有使用真实 profile/钥匙串、
没有签名或 commit**。

**范围与契约**

- 7 个文件（3 新增 + 4 修改），1070 insertions / 13 deletions，sha256
  `4fb2bd2a9b66f93d3e47b623ba729d67b2d2ab9cb10e0e8adf67114799c838d4`；范围严格限于设计 §6 的
  策略层：`ui/views/widget/browser_cli_foreground_lease.{h,cc}`（`ForegroundLease`、
  login/captcha/confirmation/other 原因枚举、`RequestResult`/`ReleaseReason` 稳定日志字符串、
  半开区间 `[granted_at, expires_at)` 判定、有序纯判定 `EvaluateForegroundAssistRequest`）+
  policy 的 `RequestForegroundAssist`/`ReleaseForegroundAssist`/
  `ReleaseExpiredForegroundAssistLeases`/`IsForegroundAssistLeased` 与 `AllowPresentation`
  leased 分支 + 两个单测文件 + `ui/views/BUILD.gn`。
- 安全语义：requester origin 为**显式参数**，仅 `kAgentAssist` 可获批（其余 fail closed，不依赖
  ambient `ScopedForegroundOrigin`）；HUMAN-owned 目标永不可租；单实例 per-window（他窗口 busy、
  同窗口幂等不续期）；kUserExplicit 接管**先释放 lease 再授 HUMAN**，此后 release API 为 no-op；
  lease 绝不设置 `owned=yes`，extension/web-page/session-restore 不能借道；未租约窗口行为与
  001/003 完全一致（含既有 `kSetBounds` 例外）。
- TTL 默认 5 分钟，惰性过期（决策/查询/请求路径先 sweep）；Slice 1 **无计时器、无自动 hide、
  无任何展示副作用**。

**patch 与重放证据**

- 两次独立提取（`001+002+003` 临时 index + 当前 worktree，`git add -N` 新文件）逐字节一致。
- 全新临时 index 顺序应用 001→002→003→004（每个先 `--check`）后 `git diff --quiet` rc=0，
  证明临时 index 与当前已验证 checkout 的所有 tracked 文件一致；仅生成的
  `chrome/app/theme/browser_cli/mac/{app.icns,Assets.car}` 未跟踪（按设计不入 patch）。

**主负责人证据（本次非 runtime 集成沿用）**

- `browser_cli_foreground_lease_unittests` **8/8 PASS**（含 48 组合穷举与 granted_at/expires_at
  边界 ±1ms）；`browser_cli_presentation_policy_unittests` **27/27 PASS**（既有 12 项无回归）。
- 日志契约实测：`state=assist owned=no ... leased=yes`；`owned=yes` 与 `leased=yes` 从不同现；
  接管顺序为 `assist-lease-released(user-takeover)` → `human-ownership-granted` → 用户决策；
  lease-free 决策行与 001/003 逐字段一致（无 `leased=`）。

**已知限制（保持 REVIEW，不得据此转 DONE）**

- **无 producer**：agent/BrowserPilot 无法真实发起 `requestForeground`，`kAgentAssist` 仍无生产者；
- **无 host 接入**：`ReleaseForegroundAssist(kWindowDestroyed)` 未接
  `NativeWidgetMacNSWindowHost` 析构，ASSIST 展示入口（`PresentForAgentAssistAction`）不存在；
- **无计时器**：过期仅在决策/查询/请求路径被惰性 sweep，空闲时窗口不会自动回后台；
- **无 runtime/人工证据**：未构建 chrome、未启动浏览器；Gate B 人工矩阵仍待用户执行；
  Slice 2（producer/host/计时器）见设计 §6。

### 追加 7（2026-09-23）：Foreground Assist Slice 2 producer/host/计时器集成并入 patch 005（REVIEW）

条目：**Foreground Assist Slice 2 集成交付（producer + host 接入 + 超时计时器，非 runtime）**。
本轮把 worktree 中已有的 Slice 2 源码相对 004 的全部增量审查、修复一处 GN 依赖缺陷后冻结为
`005-foreground-assist-integration.patch`；**没有启动浏览器、没有可见 UI 测试、没有签名、
没有使用真实钥匙串/证书、没有 commit**。

**范围与契约（14 文件 = 8 新增 + 6 修改，1134 insertions / 2 deletions，sha256 `0dc439aa…`）**

- 纯分类器 `chrome/browser/browser_cli/foreground_assist.{h,cc}`：request/release 双开关、
  `login/captcha/confirmation/other` 固定词表、严格十进制 uint64（拒绝 0/符号/空白/溢出/十六进制）、
  launch-args（URL/`--app`/`--app-id`/`--incognito`/`--enable-automation`/`--headless`）与
  与 `--browser-cli-user-explicit-show` 冲突全部 fail closed。
- 超时控制器 `foreground_assist_controller.{h,cc}`：进程内唯一 `OneShotTimer`（TTL 与 policy
  `kDefaultForegroundAssistLeaseTtl` 同值）；超时只回退**被跟踪的单个窗口**；HUMAN-owned、
  已销毁、已被 agent release 的窗口一律 no-op；新 grant 先 finalize 旧窗口（同样校验非 HUMAN）。
- macOS 集成 `foreground_assist_presentation.{h,mm}`：冷启动（`PreMainMessageLoopRunImpl` 内、
  `browser_creator_->Start` 之后）与 ProcessSingleton 转发两个入口共用同一分类器；仅在 policy
  `kGranted` 后经 `NativeWidgetMacNSWindowHost` 单实例入口 present **目标窗口**；present 失败即
  释放 lease（`failed`）；release 只处理匹配 lease 且 hide 前再次校验非 HUMAN；全部结果输出
  `BROWSER_CLI_FOREGROUND_ASSIST stage=producer` 结构化日志（handled/denied/failed/noop）。
- host：`GetFromWidgetId`、`RequestForegroundAssistLease`、`IsForegroundAssistLeased`、
  `ReleaseForegroundAssistLease`、`PresentForAgentAssistAction`、`HideForForegroundAssistRelease`
  （hide 前重新武装 per-window AppKit 抑制，并双重校验 `IsUserExplicitOwned`）；析构新增
  `ReleaseForegroundAssist(kWindowDestroyed)`；`ui/views/BUILD.gn` 把 lease 头文件暴露给 chrome
  （避免 chrome 依赖 policy 实现 target，保持单实例）。
- 复核修复（本切片内）：`foreground_assist_unittests` 直接 include `user_explicit_show.h` 但缺
  `:user_explicit_show` 依赖，`gn check` FAIL；已补 dep，重跑 6 个 `gn check` 全部 PASS。

**检查与测试（原始命令与结果）**

```sh
# 静态
gn check out/Dev //chrome/browser/browser_cli:foreground_assist            # PASS
gn check out/Dev //chrome/browser/browser_cli:foreground_assist_unittests  # PASS（修复后）
gn check out/Dev //chrome/browser/browser_cli:foreground_assist_controller_unittests  # PASS
gn check out/Dev //chrome/browser/browser_cli:foreground_assist_presentation          # PASS
gn check out/Dev //ui/views:views ; gn check out/Dev //chrome/browser:core            # PASS
gn format --dry-run {chrome/browser,ui/views,chrome/browser/browser_cli}/BUILD.gn     # rc=0
clang-format（8 新增 + 6 修改源文件）                                                 # 零 diff

# 单测（全部 gtest 二进制，无浏览器）
out/Dev/foreground_assist_unittests                     # 13/13 PASS
out/Dev/foreground_assist_controller_unittests          # 7/7 PASS
out/Dev/browser_cli_foreground_lease_unittests          # 8/8 PASS
out/Dev/browser_cli_presentation_policy_unittests       # 27/27 PASS
out/Dev/reopen_window_selection_unittests               # 8/8 PASS（回归）
out/Dev/user_explicit_show_unittests                    # 14/14 PASS（回归）
out/Dev/browser_cli_managed_presentation_unittests      # 2/2 PASS（回归）

# 增量 chrome 编译（不签名；build.sh 会强制签名，故用 ninja）
ninja -C out/Dev chrome                                 # exit 0（树已含 Slice 2 对象，无重链接）

# patch 重放与比对
临时 index: git read-tree HEAD → 001..005 逐个 git apply --cached --check + apply
git diff --quiet                                        # rc=0（2m18s，全部 tracked 文件一致）
42 个 patch-series 文件路径级逐字节比对                   # 0 差异
第二次独立提取 005                                       # sha256 一致
git apply --check -R 005-foreground-assist-integration.patch  # OK
```

证据目录：`build-logs/foreground-assist-slice2-20260923/`（`check-results.txt`、
`test-output.txt`、`patch-sha256.txt`、`build-logs.txt`）；chrome 构建日志
`build-logs/foreground-assist-slice2-chrome-20260923-094451.log`。

**审查结论（针对主负责人指定核对点）**

- `target_tab` 仅审计：分类器解析后写入 lease 并进入日志，全切片无任何 tab 激活/选中调用；
- 用户接管后 timeout/release 绝不 hide HUMAN 窗口：controller delegate 双重校验
  （`WindowExists` + `WindowIsUserOwned`）、producer release 前 `IsUserExplicitOwned` 校验、
  host `HideForForegroundAssistRelease` 内再校验一次；接管时 policy 先释放 lease 再授 HUMAN；
- 零窗口/冷启动与 ProcessSingleton：冷启动 assist 处理器在 `browser_creator_->Start` 之后运行，
  此时无窗口即 `target-window-not-found` 拒绝；ProcessSingleton 转发在 `HandleUserExplicitShowRequest`
  之后调用，消费成功即 `return`（不再打开窗口）；
- blocked switches 不落入普通启动副作用：launch-args 组合返回 false（继续正常启动/打开 URL，
  assist 拒绝）；invalid/conflicting 组合返回 true（消费掉，不开窗）；cold-start 处返回值被忽略，
  assist 路径自身不创建窗口；
- 只允许目标 window 显示：policy per-window lease + producer 只对目标 host 调
  `PresentForAgentAssistAction()`，无任何连带展示。

**已知限制（保持 REVIEW，不得据此转 DONE）**

- **无 runtime/可见 UI 证据**：未启动 App、未做人工 assist 流程；当前 `out/Dev` dylib 已被重链接为
  **未签名**（本切片未签名），任何 runtime 验收前必须整 App 构建 + Developer ID 签名；
- `target_tab` 仅审计（不选中 tab）；agent 身份未与 native host 会话绑定（进程内 lease 可被同进程
  任意 `kAgentAssist` 调用者使用）；非顶层 widget（bubble/dialog）也可被命名为 target（无类型校验）；
- controller 不感知用户接管（所有副作用点均再校验 HUMAN，故不 hide；但未来"交还 AI"生产者若在
  原 deadline 前释放 ownership，stale timer 会在 deadline 时 hide 该窗口——需后续切片显式通知）；
- 远程 app-shim 窗口无 host（fail closed `target-window-not-found`）；冷启动无背景开关时按
  unmanaged noop，浏览器正常启动（stock 行为）。

#### 追加 7 修正（2026-09-23，主负责人复核拒绝后的同一交付内修正）

主负责人拒绝第一版 005 验收，指出四类正确性边界；本轮在同一 Slice 内一次性修复并回归，
patch 005 已重生成（sha256 `3665b71dedde4d9d83270518e594081fa865e2b5ea1d06b2edbade9ffa48c164`，
15 文件 1332/2；旧 `0dc439aa…` 作废）：

1. **ASSIST→HUMAN 显式取消计时器**：新增
   `ForegroundAssistController::OnLeaseReleasedByUserTakeover(window_id)`（按窗口精确取消；
   agent release 同样改为按窗口匹配的 `OnLeaseReleasedByAgent(window_id)`）；003 的
   `user_explicit_presentation.mm` 在调用 `PresentForUserExplicitAction()` 前显式取消 tracking。
   回归单测 `UserTakeoverCancelsThePendingTimeout` 覆盖“接管 → 用户交还（不再 owned）→ 旧 deadline
   不 release/hide”，`UserTakeoverForAnotherWindowKeepsTracking` / `AgentReleaseForAnotherWindowKeepsTracking`
   覆盖窗口不匹配不误取消。
2. **blocked assist 强制 consumed**：新增纯函数
   `ForegroundAssistRequestClassConsumesCommandLine()`（仅 `kNotRequested` 返回 false），producer
   对所有 assist 意图（含 launch-args blocked）返回 true —— ProcessSingleton 路径因此不再回落
   `ProcessCommandLineAlreadyRunning`，不会借普通 URL/app launch 改变 HUMAN 窗口或创建可见窗口；
   冷启动路径在 `browser_creator_->Start` 之后运行（返回值不被使用），managed 冷启动本身受 Gate A
   约束不可见，已在头文件/行内注释与单测 `OnlyNotRequestedFallsThrough` 中固定语义。
3. **tab 级目标显式拒绝**：新增 `kBlockedByUnsupportedTabTarget`；任何 tab switch 一律拒绝
   （格式错误/0 → `kBlockedByInvalidTarget`，合法非零 → `kBlockedByUnsupportedTabTarget`），
   `ForegroundAssistRequest::target_tab` 恒为 0；单测 `TabTargetIsRejectedAsUnsupported` 固定请求与
   release 两条路径，不再存在“审计字段被当成获批 tab”的表述。
4. **target 限顶层 BrowserWindow**：host 新增 `IsEligibleForegroundAssistTarget()`（`Widget::is_top_level()`，
   child/bubble/menu/tooltip fail closed）并在 `RequestForegroundAssistLease()` 内做 defense in depth；
   producer 追加 `GlobalBrowserCollection::FindBrowserWithWindow()` 校验（顶层但非 BrowserWindow 的
   dialog 等 fail closed，拒绝原因 `target-not-top-level-browser-window`）。

回归命令与结果：`gn check` 7 个目标全 PASS（含 `user_explicit_presentation`）；`gn format` rc=0；
clang-format 零 diff；单测 assist **14/14**、assist_controller **10/10**、lease 8/8、policy 27/27、
reopen 8/8、CLI show 14/14、AppKit 2/2；`ninja -C out/Dev chrome` **exit 0**（增量编译
`foreground_assist_presentation.o`/`user_explicit_presentation.o`/`chrome_browser_main.o` 并重链接
`libchrome_dll.dylib`，日志 `build-logs/foreground-assist-slice2-fix-chrome-20260923-101437.log`）。
静态重放 001→005 后 `git diff --quiet` rc=0、42 个 patch-series 文件 0 差异、两次提取 sha256 一致、
`git apply --check -R` 通过。**仍无浏览器 runtime/可见 UI 证据、未签名、未 commit（REVIEW）**；
尚不能静态证明的 runtime 问题：真实 OS 层不闪现/不 hide、CGWindowList 恒 0、ProcessSingleton
转发时的真实消费链路、取消失败时的兜底顺序；须在签名 App 的隔离 runtime 矩阵中验证。

#### 追加 7 修正 2（2026-09-23，第二轮复核：冷启动时序；旧 hash 保留）

主负责人直接审查代码后发现冷启动因果链漏洞并拒绝验收：`chrome_browser_main.cc` 约 2144 行先调用
`browser_creator_->Start(*base::CommandLine::ForCurrentProcess(), ...)`，约 2159 行才
`HandleForegroundAssistRequest()`；因此**带 assist switch + URL/app 的冷启动**即使分类器返回
blocked/consumed，`Start` 已可能处理 URL / 创建窗口；而 ProcessSingleton 路径（约 576 行）是
handler-first，二者语义不一致。本轮只针对该因果链做最小修正（不重复上一轮四项、不跑全套回归）：

1. **冷启动 sanitizer（纯函数，可单测）**：`foreground_assist.{h,cc}` 新增
   `HasForegroundAssistIntent()` 与 `CommandLineForColdStartStartup()`：有 assist intent 时复制原
   命令行但**丢弃全部位置 URL 参数**与 `--app/--app-id/--incognito/--enable-automation/--headless`，
   追加 `--no-startup-window`；其余开关（`user-data-dir`/`profile-directory`/
   `browser-cli-background-presentation` 等）保留；**无 assist intent 时字节不变**，原进程命令行
   永不被修改。
2. **冷启动调用顺序修正**：`chrome_browser_main.cc` 把 sanitized 命令行传给
   `StartupBrowserCreator::Start()`（`#if BUILDFLAG(IS_MAC)`，非 mac 保持原引用），随后 handler 仍
   对**原请求**做 denial/log（冷启动无窗口 → `target-window-not-found`；release → `no-matching-lease`）。
   非 assist 正常启动、USER_EXPLICIT、ProcessSingleton 既有行为不变。
3. **`--no-startup-window` 行为核实（upstream 源码级证明）**：
   `startup_browser_creator.cc` 的 `ProcessCmdLineImpl()` 中 `--no-startup-window` 于 1173-1175 置
   `silent_launch=true`，1178-1191 在**任何 URL/窗口/Profile Picker 启动之前** `return true`；唯一
   的 `LaunchBrowser(...)` 调用（1452）在其后，因此 sanitized 行只拉起进程、不会打开 URL/app/窗口
   （`ShouldLoadProfileWithoutWindow()` 同样对 `--no-startup-window` 返回 true）。
4. **USER_EXPLICIT 接管通知顺序**：`user_explicit_presentation.mm` 改为
   `PresentForUserExplicitAction()` **成功返回后**才调用
   `OnLeaseReleasedByUserTakeover()`；呈现失败时保持 controller tracking（policy 未释放 lease），
   控制器自身仍有 `WindowIsUserOwned` 兜底。选择“成功后通知”消除了“取消计时器但 lease 仍在”的
   窗口。
5. **可审查静态门禁**：新增 `tests/check-foreground-assist-cold-start-order.sh`（只读源码检查，
   7/7 PASS）：Start 只接收 sanitized 行且不再接收原始进程行；upstream `--no-startup-window`
   的 silent_launch 早退早于 `LaunchBrowser`；sanitizer 的 intent 门与
   `--no-startup-window` 追加存在；ProcessSingleton handler 先于 `ProcessCommandLineAlreadyRunning`
   并 return。

**本轮验证**：`gn check` 6 个受影响目标全 PASS（含 `//chrome/browser:core` 新增
`:foreground_assist` 依赖）；`gn format` rc=0；clang-format 零 diff；单测 assist **17/17**
（+`IntentDetectionMatchesProducerSwitches`、`ColdStartLaunchWithoutIntentIsUnchanged`、
`ColdStartLaunchDropsLaunchSideEffects`）、controller 10/10、CLI show 14/14；静态门禁 7/7 PASS；
`ninja -C out/Dev chrome` exit 0（编译 `foreground_assist_presentation.o`/
`user_explicit_presentation.o`/`chrome_browser_main.o` 并重链接 `libchrome_dll.dylib`，日志
`build-logs/foreground-assist-slice2-coldstart-chrome-20260923-103510.log`）；静态重放 001→005 后
`git diff --quiet` rc=0（2m16s）、42 文件 0 差异、两次提取 sha256 一致、`git apply --check -R` OK。

**patch 005 hash 历史（旧记录保留，不覆盖）**：
`0dc439aa6c4be39ffb5319833ad9ef10111c05e425ba35c94035a9cb7be11e5d`（首版，1134/2，已作废）→
`3665b71dedde4d9d83270518e594081fa865e2b5ea1d06b2edbade9ffa48c164`（修正 1，1332/2，已作废）→
`7517c427aa346a71f091f7d3c13b2d072aa76b1c9e5452d5ce2645a39faf6e66`（本轮，1490/6，当前）。

**状态与未证明项（REVIEW；不得宣称 blocked assist 已 runtime fail-closed）**：冷启动"assist 不
回落普通 launch"目前由 sanitizer 单测 + 静态门禁 + upstream 顺序断言证明，**没有 runtime 证据**
（未启动 App、未签名）；本机 `--no-startup-window`+真实 CLI 组合、Profile Picker/FirstRun 边界、
ProcessSingleton 真转发消费仍需签名 App 的隔离 runtime 矩阵验证；agent 身份未绑定 native host 会话；
远程 app-shim 窗口 fail closed。
