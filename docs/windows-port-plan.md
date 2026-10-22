# Browser CLI macOS / Windows 共用代码规划

状态：**规划中，Windows 尚未构建或验收**。本文件描述移植边界，不把 macOS 测试结果算作 Windows 通过。动态进度以根目录 `PROJECT-PROGRESS.md` 为准。

## 目标与不变量

维护**同一 Chromium revision、同一组有序补丁和同一套产品语义**。`BACKGROUND`、`ASSIST`、`HUMAN`、`SUSPENDED` 的状态/来源/动作决策只能有一个实现；macOS 与 Windows 仅分别实现原生窗口展示、激活和用户入口适配。BrowserPilot 保持独立且只有一套插件。

两端都必须满足：后台页面继续网络/JS/渲染；自动化不擅自展示或抢焦点；用户显式打开可接管同一个窗口；HUMAN 窗口不受自动化隐藏、最小化或改焦点；新页面默认复用同一浏览器的 tab；窗口状态转移和来源可归因。禁止用“先显示再隐藏”、事后 watchdog 或单测结果代替全过程窗口观测。

## 现状（2026-10-22 源码核对）

| 层 | 可复用部分 | Windows 缺口 |
| --- | --- | --- |
| 策略与 lease | `ui/views/widget/browser_cli_presentation_policy.*`、`browser_cli_foreground_lease.*`、`browser_cli_foreground_origin.h` 为 C++；状态/来源/动作矩阵及 generation 已有单测 | 要确认所有 Windows 展示入口调用**同一个**策略，不允许另起一套 Windows 判定 |
| 命令意图与窗口选择 | `chrome/browser/browser_cli` 下的 `foreground_assist.*`、controller、`user_explicit_show.*`、`reopen_window_selection.*` 为 C++ | 目前生产展示实现 `*_presentation.mm` 仅在 `is_mac` 下构建；Windows 需薄适配器并复用同一意图/选择/lease |
| 原生窗口 | macOS 已在 AppKit / remote_cocoa / NativeWidget host 接入多层 gateway | Windows 原生 HWND / DesktopWindowTreeHost 展示、激活、最小化/恢复/全屏和用户 taskbar 入口尚无 Browser CLI gateway |
| 自动化来源 | 扩展 `chrome.windows` 操作已标记来源 | 现有关键分支仍是 `BUILDFLAG(IS_MAC)`；Windows 的扩展和其它 producer 需逐入口归因，默认 Unknown 拒绝，不能以进程激活倒推用户意图 |
| 构建与发布 | Chromium revision 与编号补丁已固定，macOS 构建可复现 | `preflight.sh` 明确只接受 macOS/arm64，`args.gn` 固定 arm64 和 macOS icon，产品身份/签名脚本仅适用于 Apple；没有 Windows 构建、打包、签名及测试结果 |

当前 Gate B 仍有未覆盖的人工时序与原生 overlay 旁路待归因，Gate C/F、BrowserPilot 集成和发布工作也未全部完成；不要因为开始 Windows 移植而把这些项目改成 DONE。

## 实现顺序与完成门禁

1. **固定共用合同。** 在现有 C++ 策略/lease/controller 单测中补齐跨平台反例：Unknown 不提权、每窗口 HUMAN、旧 generation 不影响新 lease、自动化创建 tab 不新开原生窗口、用户显式接管优先。只修改共用合同一次；不复制 macOS 策略到 Windows 文件。
2. **拆构建配置。** 共用 GN target 在 `is_mac || is_win` 构建；平台适配 target 分别在 `is_mac`、`is_win` 构建。提供独立的 Windows GN 参数、资源与构建入口；macOS 的 Developer ID 配置不进入 Windows 产物。要求两平台都能从同一补丁序列在固定 revision 上重放，并各自完成编译与共用单测。
3. **接 Windows 原生 gateway。** 查明该固定 Chromium revision 下每条 HWND show/activate/minimize/restore/fullscreen/z-order 路径，接到共用策略；明确 taskbar 点击、系统窗口操作、浏览器菜单和 CLI `show` 的可信用户来源。不能把 `WM_ACTIVATE` 或进程前台状态直接当 `USER_EXPLICIT`。所有 native bypass 必须有归因或明确的允许类，并用事件日志对齐窗口 ID。
4. **接后台运行与截图。** 在 Windows 实测隐藏/最小化/失焦时 HTTPS、Cookie、JS、timer、tab 与截图数据是否持续有效；若某状态受平台节流，先记录证据与妥协方案，不用短暂上屏掩盖。
5. **逐端验收与发布。** Windows 用隔离 profile 跑完整状态矩阵和长时间窗口 watcher，人工验证 taskbar/开始菜单接管；macOS 原有 Gate A/B 回归不能退化。Windows 安装、更新、签名由独立发布门禁负责，不复用 Apple 的签名脚本。任何一端未实际运行的项目只可标 REVIEW/BLOCKED，不可标 DONE。

## 最小跨平台验收矩阵

对 macOS 与 Windows **分别**留存原始日志、窗口采样和页面数据返回，至少覆盖：

- 零窗口冷启动、隐藏、最小化、可见但未激活、可见且激活；后台请求全过程不得自行抢前台。
- 同一浏览器中连续打开两个真实网页：保留并复用 tab，确认实际收到数据，不只检查 target 创建成功。
- 用户从 Dock/Launchpad 或 taskbar/开始菜单显式接管：恰好一个目标窗口前台，重复点击不多开；用户最小化或切走后自动化不得再激活。
- HUMAN 窗口与后台窗口并存：扩展/网页/agent 对 HUMAN 的 focus、hide、minimize、maximize、fullscreen、bounds、z-order 请求均不能改变它；后台窗口仍可工作。
- ASSIST 申请、拒绝、超时、用户接管、旧 lease ABA；每次转移记录 origin、window ID、generation、决策和 native 结果。
- 原生辅助窗口/overlay、会话恢复、崩溃重启；任何绕过 gateway 的可见窗口必须归因。不能以单测通过代替真实桌面采样。

## 机器与交接要求

现有 macOS 机器只能做共用 C++ 设计、补丁重放审查和 macOS 回归，**不能宣称 Windows 版本完成**。Windows 构建、原生窗口路径审计和真实 taskbar/桌面验收必须在 Windows 开发机或 Windows CI 上完成。移植者先读根目录 `AGENTS.md`、`PROJECT-PROGRESS.md`、本文件及 `chromium/patches/README.md`；所有 Chromium 修改仍作为编号补丁入库，不提交整个 Chromium checkout 或构建产物。
