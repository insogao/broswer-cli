# chromium/patches

Browser CLI 对 Chromium 的修改按设计 §10 保持窄、小、清晰、可重放。

## 已入库 patch

| patch | 对应阶段 | 内容 | 状态 |
| --- | --- | --- | --- |
| `001-managed-window-mode.patch` | Phase 2 Gate A + Gate B（非 branding 全量切片） | BACKGROUND_PRESENTATION 三层拦截（policy + host + AppKit/bridge 兜底）+ 结构化日志；per-window `USER_EXPLICIT`/HUMAN 所有权与 origin 归因（Dock/Launchpad/app reopen 复用既有窗口、Extension/Tabs API、延迟 show/activate 归因延续）；程序化窗口变更门禁（fullscreen/bounds/z-order/minimize/restore/maximize/deactivate）；reopen 窗口选择器；3 个单测目标 | 与主负责人验证通过的 checkout 完全一致；从固定 revision 用临时 index 顺序重放 001→002→003 通过；增量构建与单测已复核；未 commit |
| `002-browser-cli-branding.patch` | 稳定签名/产品身份 + Logo | 新增 `chrome/app/theme/browser_cli/BRANDING`（Browser CLI / com.jiangao.browsercli / team RAXC5D3A3S）；新增 `mac_app_icon_dir` GN 参数并让 `chrome_app_icon`/`chrome_asset_catalog` 使用 Browser CLI 图标目录（图标文件由 `scripts/generate-icons.sh` 从已批准源图生成，不进入 patch） | 保持 2026-09-22 版本不变（sha256 未变）；与 checkout 反向校验一致；未 commit |
| `003-user-explicit.patch` | Phase 3（CLI 显式 `show` 生产者切片） | 纯函数分类器（`--browser-cli-user-explicit-show` 仅在无 URL/`--app`/`--app-id`/`--incognito`/`--enable-automation`/`--headless` 时才是 pure show）+ 14 个单测；macOS ObjC++ 共享展示单元 `chrome/browser/browser_cli/user_explicit_presentation.{h,mm}`（把 001 中放在 `app_controller_mac.mm` 的 Dock/Launchpad/reopen 展示 helper 迁移进来，保持唯一 per-window USER_EXPLICIT 路径）；接冷启动（`PreMainMessageLoopRunImpl`）与 ProcessSingleton 转发（`ProcessSingletonNotificationCallbackImpl`）两条入口；失败/拒绝 fail closed 并输出 `BROWSER_CLI_USER_EXPLICIT_SHOW stage=producer` 结构化日志 | 静态重放 001→002→003 与当前已验证 checkout 逐字节一致（见下）；主负责人证据：`user_explicit_show_unittests` 14/14 PASS、policy 12/12、AppKit 2/2、reopen 8/8、`autoninja -C out/Dev chrome` exit 0；**可见 runtime/人工 Gate B 未执行（REVIEW）**；未 commit |
| `004-foreground-lease.patch` | Phase 4（Foreground Assist Slice 1：仅策略层 lease，非 runtime） | `ForegroundLease` 数据结构 + `login`/`captcha`/`confirmation`/`other` 原因枚举与稳定日志字符串；policy 单实例 per-window lease（`RequestForegroundAssist` 以**显式 requester origin** 校验且仅 `kAgentAssist` 可获批、busy/invalid/HUMAN-owned 拒绝、同窗口幂等不续期；`ReleaseForegroundAssist`；惰性 TTL `ReleaseExpiredForegroundAssistLeases`；`IsForegroundAssistLeased`）；`AllowPresentation` 追加 leased 分支（agent 仅 `show-and-activate`/`show-inactive`/`set-bounds`，internal 允许，extension/web-page/session-restore 拒绝），kUserExplicit 接管**先释放 lease 再授 HUMAN**；日志 `state=assist leased=yes`（`owned=yes` 仍仅表示 HUMAN）；新增 2 个单测目标 | 静态重放 001→002→003→004 与当前已验证 checkout 逐字节一致（见下）；主负责人证据：lease **8/8**、policy **27/27**（既有 12 项无回归），TTL 半开区间 `[granted_at, expires_at)` 已修正；**无 producer/host 接入/计时器/runtime 证据，未构建 chrome、未 commit（REVIEW）** |
| `005-foreground-assist-integration.patch` | Phase 4（Foreground Assist Slice 2：producer + host 接入 + 超时计时器，非 runtime） | 纯函数 CLI 分类器 `chrome/browser/browser_cli/foreground_assist.{h,cc}`（request/release 双开关、固定 reason 词表、严格十进制 uint64 target、**任何 tab switch 一律拒绝（不支持 tab 级目标）**、launch-args/automation/headless/与 USER_EXPLICIT 冲突全部 fail closed；**所有 assist 意图一律 consumed，绝不回落为普通 URL/app launch**；**冷启动 sanitizer `CommandLineForColdStartStartup()`：assist intent 时去掉 URL/app/incognito/automation/headless 并加 `--no-startup-window`，普通启动字节不变**）；超时控制器 `foreground_assist_controller.{h,cc}`（唯一 `OneShotTimer`，超时只回退**被跟踪的单个窗口**，HUMAN-owned / 已销毁窗口绝不 hide；**ASSIST→HUMAN 接管显式取消跟踪与计时器**，agent release 亦按窗口匹配取消）；macOS 集成 `foreground_assist_presentation.{h,mm}`（冷启动 + ProcessSingleton 两入口共用分类器与消费语义；`target_window` 必须是**顶层 BrowserWindow**：host 顶层性 + `FindBrowserWithWindow` 双校验；grant→present、release→hide 前双重校验非 HUMAN；`GetForegroundAssistController()` 供 USER_EXPLICIT 路径**成功后**取消 assist 计时器）；host 新增 `GetFromWidgetId`/`IsEligibleForegroundAssistTarget`/`RequestForegroundAssistLease`/`IsForegroundAssistLeased`/`ReleaseForegroundAssistLease`/`PresentForAgentAssistAction`/`HideForForegroundAssistRelease`（hide 前重新武装 AppKit 抑制；析构释放 lease）；`ui/views/BUILD.gn` 暴露 lease 头给 chrome；新增 2 个单测目标 | 静态重放 001→005 与 worktree 逐字节一致；单测：assist **17/17**、assist_controller **10/10**、lease **8/8**、policy **27/27**、回归 reopen **8/8**、CLI show **14/14**、AppKit **2/2**；新增静态门禁 `tests/check-foreground-assist-cold-start-order.sh` **7/7 PASS**；`ninja -C out/Dev chrome` **exit 0**（增量编译并重链接 `libchrome_dll.dylib`）；**未做浏览器 runtime/可见 UI 测试、未签名、未 commit（REVIEW）**；tab 级目标明确不支持 |

`001` 校验：sha256 `5e1bd1e47a7a180dddfce28aca43eea2b801dd56c0f184fa2a717c8cec99aafb`；
共 23 个文件（14 改 + 9 新增），1698 insertions / 8 deletions；应用基线为
`chromium/VERSION` 记录的 revision `7d0f28c30d1c0fadbce17ecec0ee2565aef9a940`。
范围与 002 严格不重叠：001 不含 `chrome/BUILD.gn` 与 `chrome/app/theme/browser_cli/**`
（BRANDING 与图标属于 002/生成脚本 scope）；`mac/{app.icns,Assets.car}` 为
`scripts/generate-icons.sh` 的生成物，不进入任何 patch。
001 中 `ui/views/cocoa/native_widget_mac_ns_window_host.mm` 含一处仅格式变化
（`accessibilitySetPresenterProcessIdentifier` 选择器换行，无逻辑变化），系对验证通过状态的
原样打包。

`002` 校验：sha256 `acd496a18a0c2d1c2133b2cf1848974e6d40332bc4f959a5e989e19a45cccf61`；
共 2 个文件（`chrome/BUILD.gn` 修改 + `chrome/app/theme/browser_cli/BRANDING` 新增），
25 insertions / 2 deletions；应用基线同上。

`003` 校验：sha256 `9852de6570056b0f4d5bc72749e9dc17d54fb42d7e50f03017db54f8d8192e43`；
共 9 个文件（4 改 + 5 新增），597 insertions / 78 deletions；应用基线为 `001`+`002` 之后的
worktree。新增文件为 `chrome/browser/browser_cli/user_explicit_{presentation.h,presentation.mm,
show.cc,show.h,show_unittest.cc}`；修改 `chrome/browser/{BUILD.gn,chrome_browser_main.cc,
app_controller_mac.mm}` 与 `chrome/browser/browser_cli/BUILD.gn`。
003 是**从固定 revision + 001 + 002 到当前 CLI-show 源码的精确 delta**：除新增 CLI `show`
生产者外，还把 001 位于 `app_controller_mac.mm` 的 Dock/Launchpad/reopen 展示 helper
（`PresentBrowserWindowUserExplicitly` / `PresentExistingBrowserWindowForUserExplicitReopen`）
整体迁入新 ObjC++ 单元 `user_explicit_presentation.mm`（保留唯一 per-window USER_EXPLICIT 路径，
因此 003 含 78 行删除）。开发过程中该展示单元由 `.cc` 改名为 `.mm`（Objective-C++），并补上
`ui/base/base_window.h`（`ui::BaseWindow`）include，之后 `autoninja -C out/Dev chrome` exit 0。

`004` 校验：sha256 `4fb2bd2a9b66f93d3e47b623ba729d67b2d2ab9cb10e0e8adf67114799c838d4`；
共 7 个文件（3 新增 + 4 修改），1070 insertions / 13 deletions；应用基线为 `001`+`002`+`003`
之后的 worktree。新增 `ui/views/widget/browser_cli_foreground_lease.{h,cc}` 与
`browser_cli_foreground_lease_unittest.cc`；修改 `ui/views/widget/browser_cli_presentation_policy.{h,cc}`、
`browser_cli_presentation_policy_unittest.cc` 与 `ui/views/BUILD.gn`。范围严格限于设计 §6 的
**策略层 Slice 1**：不含 `requestForeground` 生产者、`NativeWidgetMacNSWindowHost` 展示/释放接入、
超时计时器、自动 hide/展示副作用与任何 runtime 证据（这些留给后续切片）。提取方式与 003 相同：
从 `001+002+003` 的临时 index 与当前 worktree 生成 `git diff`（新增文件先 `git add -N`），
两次独立提取逐字节一致。

`005` 校验：sha256 `7517c427aa346a71f091f7d3c13b2d072aa76b1c9e5452d5ce2645a39faf6e66`；
共 15 个文件（8 新增 + 7 修改），1490 insertions / 6 deletions；应用基线为
`001`+`002`+`003`+`004` 之后的 worktree。历史版本（已作废，记录保留）：
首版 `0dc439aa6c4be39ffb5319833ad9ef10111c05e425ba35c94035a9cb7be11e5d`（1134/2）、
复核修正版 `3665b71dedde4d9d83270518e594081fa865e2b5ea1d06b2edbade9ffa48c164`（1332/2）。
新增 `chrome/browser/browser_cli/foreground_assist.{h,cc}`、
`foreground_assist_controller.{h,cc}`、`foreground_assist_controller_unittest.cc`、
`foreground_assist_unittest.cc`、`foreground_assist_presentation.{h,mm}`；修改
`chrome/browser/{BUILD.gn,chrome_browser_main.cc}`、`chrome/browser/browser_cli/BUILD.gn`、
`ui/views/BUILD.gn`、`ui/views/cocoa/native_widget_mac_ns_window_host.{h,mm}` 与 003 的
`chrome/browser/browser_cli/user_explicit_presentation.mm`。范围严格限于
**producer + host 接入 + 超时计时器**：`target_tab` 明确不支持（任何 tab switch 被
`kBlockedByUnsupportedTabTarget` 拒绝）；不含浏览器 runtime/可见 UI 测试、签名，与
Extension/WebPage/SessionRestore/AgentAssist 其余来源的生产者（见设计 §6 与 PROJECT-PROGRESS D 区）。
提取方式同 003/004，两次独立提取 sha256 一致；`git apply --check -R` 反向校验通过。
**两次复核修正均已包含在上述 sha256 中**：(1) 接管取消计时器 / blocked assist 强制 consumed /
tab 目标拒绝 / target 限顶层 BrowserWindow；(2) 冷启动 assist intent 在 `Start` 前改用
`CommandLineForColdStartStartup()` 构造无 URL/app 副作用（并加 `--no-startup-window`）的启动命令行、
USER_EXPLICIT 接管改为呈现成功后取消计时器。详见
`docs/phase2-background-presentation-2026-09-22.md` §追加 7 及其两个修正小节。

复现与校验（顺序必须 001 → 002 → 003 → 004 → 005）：

```sh
cd ~/dev/chromium/src
git rev-parse HEAD   # 7d0f28c30d1c0fadbce17ecec0ee2565aef9a940

# 无 checkout / 无构建校验（临时 index；不触碰工作区与真实 index）
export GIT_INDEX_FILE="$(mktemp)"
git read-tree HEAD
for p in 001-managed-window-mode 002-browser-cli-branding 003-user-explicit 004-foreground-lease 005-foreground-assist-integration; do
  git apply --cached --check /path/to/browser-cli/chromium/patches/$p.patch
  git apply --cached       /path/to/browser-cli/chromium/patches/$p.patch
done
git diff --quiet && echo "index == validated source state"
unset GIT_INDEX_FILE

# 在未打 patch 的 checkout 上重放（顺序执行）
git apply /path/to/browser-cli/chromium/patches/001-managed-window-mode.patch
git apply /path/to/browser-cli/chromium/patches/002-browser-cli-branding.patch
git apply /path/to/browser-cli/chromium/patches/003-user-explicit.patch
git apply /path/to/browser-cli/chromium/patches/004-foreground-lease.patch
git apply /path/to/browser-cli/chromium/patches/005-foreground-assist-integration.patch

# 已应用的 checkout 反向校验（001/002/003/004/005 均可直接 -R）
git apply --check -R /path/to/browser-cli/chromium/patches/005-foreground-assist-integration.patch
```

003/004 的提取/校验不依赖 checkout 或构建：从上一序号累积的临时 index 与当前 worktree 生成
`git diff`（新增文件先 `git add -N`），重复生成逐字节一致；再把 001→002→003→004 应用到新的
临时 index，`git diff --quiet` 证明与已验证源码的所有 tracked 文件一致（仅
`chrome/app/theme/browser_cli/mac/{app.icns,Assets.car}` 为生成物、按设计不入 patch）。

注意：`002` 生效后产品改名为 `Browser CLI.app`，`chromium/args.gn` 会把它作为
`branding_file_path` 的选择条件；必须重新构建，旧 `Chromium.app` 为过期 ad-hoc 产物。

验证与结果记录见 `docs/phase2-background-presentation-2026-09-22.md` 与
`docs/signing-and-product-identity-2026-09-22.md`。

## 约束

- patch 相对 `chromium/VERSION` 的 revision 生成（`git diff`），必须能用 `git apply` 顺序重放；升级 Chromium 时只允许少数 patch 冲突。
- 禁止把 Chromium 源码或大文件复制进本仓库。
- 编号 002 已被 branding、003 已被 CLI `show` 生产者、004 已被 Foreground Assist Slice 1、
  005 已被 Foreground Assist Slice 2 集成占用；后续新 patch 从 006 起；设计 §10 的建议名依次顺延
  （原 `005-background-compositor`、`006-background-capture`、`007-popup-policy`）。
- `003` 的 runtime/人工 Gate B（可见 Dock/Launchpad/CLI show）尚未执行：当前证据只到
  单测、静态重放与完整构建；相关 runbook 见
  [`../tests/phase3-human-ownership-manual.sh`](../tests/phase3-human-ownership-manual.sh)。
- `004` 无 producer / host 接入 / 计时器 / runtime 证据：lease 只能由 policy API 建立，过期仅
  惰性 sweep，没有自动 hide；不得据此宣称 Foreground Assist 可用。后续切片见设计 §6。
- `005` 只到静态重放 + 单测 + 静态门禁 + `ninja chrome` exit 0：**没有任何浏览器 runtime/可见 UI
  证据**（未启动 App、未签名），`target_tab` 明确不支持（tab switch 一律拒绝）、agent 身份未与
  native host 会话绑定；`target_window` 已限制为顶层 BrowserWindow（host 顶层性 +
  `FindBrowserWithWindow`），非顶层 widget/dialog fail closed。冷启动"assist 不回落到普通 launch"
  目前由 `CommandLineForColdStartStartup()` 单测 + `tests/check-foreground-assist-cold-start-order.sh`
  静态门禁（含 upstream `--no-startup-window` 顺序断言）证明；**未 runtime 验证**，因此不得宣称
  blocked assist 已在真实运行中 fail-closed。运行时验收前必须用签名 App 走一次隔离 assist 流程
  （grant → present → timeout/release → 用户接管后旧 deadline 不 hide；冷启动带 URL/app 的
  blocked 负例不产生任何可见窗口）。
