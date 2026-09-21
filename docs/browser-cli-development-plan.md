# Browser CLI 开发规划

## 0. 项目目标

将现有 Backlight 收敛为 **Browser CLI**：一个面向 AI Agent 的 Chromium 浏览器运行时。

Browser CLI 的核心目标不是“给 Chrome 外面套一层自动化”，而是在 Chromium 内部提供普通 Chrome 难以可靠实现的能力：

- 后台多窗口并行
- 后台窗口持续运行与绘制
- 后台直接截图，不因截图弹到前台
- AI 后台操作绝不抢焦点、绝不擅自弹窗
- AI 在确实需要人工登录、验证码或确认时，可以申请一次前台辅助
- 用户主动点击 App / Dock / Launchpad / 菜单栏时，永远可以正常打开浏览器
- 用户接管后，AI 不得擅自把窗口收回后台
- BrowserPilot 继续作为通用 Chrome 插件，同时兼容普通 Chrome 和 Browser CLI

整体产品关系：

```text
BrowserPilot                 Browser CLI
通用 Chrome 插件              自定义 Chromium 浏览器
─────────────                ─────────────────────
普通 Chrome 可运行            BrowserPilot 可直接运行
基础自动化                    后台多窗口
Space / Tab                   后台持续渲染
模板                          后台直接截图
网站适配                      硬禁止非授权弹窗
Native Messaging             AI 辅助登录前台申请
                             用户随时主动接管
```

BrowserPilot 不需要为 Browser CLI 维护第二套插件，也不应该被 Chromium 动态修改或替换代码。

---

# 1. 核心设计原则

## 1.1 不再把“后台”理解成“浏览器永远不能显示”

这是当前架构中最容易反复产生 bug 的地方。

错误逻辑：

```text
currentState == BACKGROUND
→ 禁止所有 show
```

这会导致：

```text
用户点击 App
→ 仍然被 BACKGROUND 规则拦截
→ 用户自己都打不开浏览器
```

正确逻辑必须同时判断：

```text
STATE + ORIGIN + ACTION
```

即：

- 当前浏览器是什么状态
- 谁发起了动作
- 想执行什么动作

最高优先级规则：

> **任何 AI 后台策略，都永远不能阻止用户的显式操作。**

---

# 2. Browser CLI 核心状态机

第一版只允许四个状态，避免再次产生过多窗口状态。

| 状态 | 桌面显示 | 页面运行 | 截图 | AI 可使其前台 | 用户可打开 |
|---|---:|---:|---:|---:|---:|
| `BACKGROUND` | 否 | 是 | 是 | 否 | 是 |
| `ASSIST` | 是 | 是 | 是 | 仅已授权目标 | 是 |
| `HUMAN` | 是 | 是 | 是 | 不得抢用户控制权 | 是 |
| `SUSPENDED` | 否 | 降速/冻结 | 否或旧帧 | 否 | 是 |

状态含义：

### BACKGROUND

AI 正常工作状态。

要求：

- BrowserWindow 可以存在
- Tab 可以存在
- WebContents 可以存在
- JS / 网络 / Cookie / 登录状态正常
- compositor 保持可用于后台截图
- OS 桌面上不得显示窗口
- 不允许 Activate / Raise / Focus

但：

> 用户主动操作仍然可以直接从 BACKGROUND 进入 HUMAN。

### ASSIST

AI 判断需要人工协助，例如：

- 登录
- 验证码
- 二次确认
- 需要用户肉眼判断

AI 不能直接 focus，而是申请 foreground lease。

### HUMAN

用户主动接管。

例如：

- 点击 Dock
- 点击 Launchpad
- 点击菜单栏“打开浏览器”
- CLI 显式 `show`
- 在 ASSIST 状态下开始主动操作

HUMAN 状态优先级最高。

AI 不得自动隐藏、最小化或夺回窗口。

### SUSPENDED

长期不用的 Space。

用于未来资源控制：

- renderer 可冻结
- compositor 可停
- 不保证即时截图
- 用户重新打开时恢复

第一版不必重点实现。

---

# 3. 事件来源模型

建议 Chromium 内部定义明确来源，例如：

```cpp
enum class ForegroundOrigin {
    USER_EXPLICIT,
    AGENT_ASSIST,
    EXTENSION,
    WEB_PAGE,
    INTERNAL,
    SESSION_RESTORE
};
```

必要时可继续细分，但第一版不要过度复杂。

核心优先级：

```text
USER_EXPLICIT
    >
HUMAN ownership
    >
AGENT_ASSIST
    >
BACKGROUND automation
```

注意：

> `applicationDidBecomeActive` 不能直接等同于 `USER_EXPLICIT`。

因为程序自身的 `activate()` 也可能触发类似系统事件。

用户显式入口最好通过 Browser CLI 自己的 launcher / Dock / 菜单栏 / CLI 明确带入 `USER_EXPLICIT` intent。

---

# 4. 核心行为矩阵

| 事件 / 请求 | 来源 | 当前状态 | 浏览器行为 | 返回/状态反馈 |
|---|---|---|---|---|
| AI 新建普通 Tab | Agent | BACKGROUND | 后台创建，不显示，不聚焦 | `success, background=true` |
| AI 新建 Window | Agent | BACKGROUND | 创建 managed BrowserWindow，但不得上屏 | `success, visible=false` |
| 网页 `window.open()` | WEB_PAGE | BACKGROUND | 创建后台 popup/tab，禁止 Show/Activate | background popup |
| 插件 `chrome.windows.create({focused:true})` | EXTENSION | BACKGROUND | 可创建窗口，但禁止真正 focus | `focusDenied: background-policy` |
| 插件 `windows.update({focused:true})` | EXTENSION | BACKGROUND | 不得上屏 | `focusDenied` |
| Session restore 恢复窗口 | SESSION_RESTORE | BACKGROUND | 恢复内部窗口结构，但不得 native show | internal event |
| AI 截图 | INTERNAL / Agent | BACKGROUND | 从 compositor/surface 截图，不改变显示状态 | `captureSuccess` |
| Screenshot surface 暂不可用 | INTERNAL | BACKGROUND | 内部 redraw/recreate surface，绝不能弹窗 | `captureUnavailable` |
| AI 只是想“看一下页面” | Agent | BACKGROUND | 不允许因此显示窗口 | `foregroundDenied` |
| AI 判断需要登录 | AGENT_ASSIST | BACKGROUND | 请求 foreground lease | `pending / granted / denied` |
| AI 遇到验证码 | AGENT_ASSIST | BACKGROUND | 请求 foreground lease | `pending / granted / denied` |
| AI 需要用户确认 | AGENT_ASSIST | BACKGROUND | 请求 foreground lease | `pending / granted / denied` |
| Assist 获批 | AGENT_ASSIST | BACKGROUND | 进入 ASSIST，只显示指定 Window/Tab | `foregroundGranted` |
| 用户点击 Dock/App | USER_EXPLICIT | BACKGROUND | 无条件进入 HUMAN，显示并激活 | `humanTakeover` |
| 用户点击 Launchpad | USER_EXPLICIT | BACKGROUND | 无条件进入 HUMAN | `humanTakeover` |
| 用户菜单栏打开 | USER_EXPLICIT | BACKGROUND | 无条件进入 HUMAN | `humanTakeover` |
| CLI 显式 `show` | USER_EXPLICIT | BACKGROUND | 进入 HUMAN | `humanTakeover` |
| 用户正在操作 | USER_EXPLICIT | HUMAN | AI 不得隐藏窗口 | `denied: human-owner` |
| 用户操作 ASSIST 页面 | USER_EXPLICIT | ASSIST | 升级为 HUMAN | `humanTakeover` |
| Assist 完成且用户没有接管 | AGENT_ASSIST | ASSIST | 回 BACKGROUND | `foregroundReleased` |
| 用户点击“交还 AI” | USER_EXPLICIT | HUMAN | 回 BACKGROUND | `backgroundRestored` |
| AI 请求另一个后台 Window | Agent | HUMAN | 可继续后台运行，不影响当前用户 Window | `success` |
| 一个 Assist 已占前台 | AGENT_ASSIST | ASSIST | 其他 Assist 默认排队或 busy | `foregroundBusy` |
| Agent 崩溃 | INTERNAL | ASSIST | 如果用户未接管，回 BACKGROUND | safe recovery |
| BACKGROUND 下出现真实 OS 窗口 | 任意非 USER_EXPLICIT | BACKGROUND | 视为内核不变量违反 | `fatal: unexpected-visible-window` |

---

# 5. 最重要的内核不变量

## 5.1 BACKGROUND 不变量

> **BACKGROUND 状态下，任何非用户显式请求、非已授权 Assist 请求，都不得让 Browser CLI Window 出现在桌面。**

包括：

- `window.open`
- Extension API
- session restore
- screenshot
- capture helper
- renderer recovery
- popup
- internal maintenance
- crash recovery
- AI focus 请求

如果出现：

```text
窗口先弹出来
→ watchdog 发现
→ 再隐藏
```

这必须判定为：

```text
FAIL
```

不能再算“恢复成功”。

---

## 5.2 USER_EXPLICIT 不变量

> **用户主动打开 Browser CLI 的行为永远拥有最高前台权限。**

BACKGROUND 不能阻止：

- Dock 点击
- App 点击
- Launchpad
- 菜单栏
- 用户快捷键
- CLI 显式 show

这条专门防止当前反复出现的：

> 为了禁止 AI 弹窗，结果用户自己也打不开 App。

---

## 5.3 HUMAN 不变量

> **用户进入 HUMAN 后，AI 无权自动隐藏、最小化、切走或夺回当前窗口。**

只有用户明确执行“交还 AI”，才能回 BACKGROUND。

---

## 5.4 Screenshot 不变量

> **截图永远不能成为显示窗口的理由。**

如果后台 surface 不可用：

```text
返回 CAPTURE_UNAVAILABLE
```

也比：

```text
show → capture → hide
```

正确。

---

# 6. Foreground Assist 设计

BrowserPilot 不能直接通过：

```js
chrome.windows.update(id, { focused: true })
```

获得前台权限。

正确路径：

```text
BrowserPilot
    ↓
requestForeground({
    targetId,
    reason: "login"
})
    ↓
Browser CLI
    ↓
检查 state / ownership / policy
    ↓
grantForegroundLease()
    ↓
BACKGROUND → ASSIST
```

ForegroundLease 至少包含：

```text
targetWindow
targetTab
reason
requestedBy
grantedAt
expiresAt
```

建议原因第一版固定枚举：

```text
login
captcha
confirmation
other
```

ASSIST 与 HUMAN 必须严格区分：

```text
ASSIST
- AI 发起
- 目的明确
- 可以自动结束
- 只授权指定目标

HUMAN
- 用户主动
- 用户拥有控制权
- AI 不能自动结束
```

状态转换：

```text
BACKGROUND
   ├─ AI requestForeground → ASSIST
   └─ USER_EXPLICIT        → HUMAN

ASSIST
   ├─ assist finished      → BACKGROUND
   └─ USER_EXPLICIT        → HUMAN

HUMAN
   └─ user releases        → BACKGROUND
```

---

# 7. Chromium 修改策略

Browser CLI 不应该修改 BrowserPilot 插件代码，也不应该运行时替换 Extension JS。

正确思路是：

> 修改 Chrome API 和 BrowserWindow 底层执行之后的浏览器行为。

例如 BrowserPilot：

```js
chrome.windows.create({
    url,
    focused: true
})
```

普通 Chrome：

```text
BrowserWindow
→ Show
→ Activate
```

Browser CLI：

```text
BrowserWindow
→ WebContents
→ Tab
→ Renderer
→ Compositor

但当前 state = BACKGROUND
且 origin != USER_EXPLICIT / authorized ASSIST

因此：
→ native presentation blocked
→ activation blocked
```

BrowserPilot 最好主动配合，不主动乱发 focus。

但 Browser CLI 内核仍然必须作为最终硬保护层。

这保证：

- BrowserPilot 出 bug 也不能弹窗
- 第三方扩展也不能绕过
- 网页自身 popup 不能绕过
- session restore 不能绕过

---

# 8. Chromium 开发环境策略

设备：

- Apple Silicon M4 Mac
- 当前约 100GB 剩余空间

开发阶段采用精简配置：

```gn
is_debug = false
is_component_build = true
symbol_level = 0
target_cpu = "arm64"
use_lld = false
```

原则：

- 使用 `--no-history`
- 不维护完整 Chromium Git cache
- 只维护一个 `out/Dev`
- 不同时构建 x64
- 不保留大型 Debug symbols
- 正式 Release 构建最后再做

磁盘安全线：

```text
剩余 > 25GB
正常开发

剩余 < 20GB
停止新增 build 目录并清理

剩余 < 15GB
禁止继续构建
```

Browser CLI 最终 App 本身仍然只是正常浏览器量级。

几十 GB 主要来自：

- Chromium 源码
- third_party
- object files
- component libraries
- Ninja 中间产物
- 可选 Git history/cache

---

# 9. 推荐开发阶段

## Phase 0：冻结协议和不变量

先不写 Chromium。

产出：

- 本文状态机
- Origin 定义
- 行为矩阵
- ForegroundLease 结构
- Browser CLI capability schema
- 自动化验收标准

禁止继续向旧 Backlight 窗口系统增加新 workaround，除非是阻塞性 bug。

---

## Phase 1：原版 Chromium 构建验证

目标：

```text
M4
↓
checkout Chromium
↓
成功编译
↓
成功启动
↓
修改一处小 C++
↓
增量重新编译
```

这一阶段不开发 Browser CLI 功能。

需要验证：

- 实际磁盘占用
- 首次构建
- 增量构建
- App 启动
- 测试循环是否可接受

---

## Phase 2：实现 BACKGROUND_PRESENTATION

这是第一个真正 Chromium Patch。

目标：

```text
BrowserWindow exists
WebContents exists
Tab exists
Renderer works
Compositor works

BUT

native Window never appears on desktop
```

第一阶段只验证：

- 创建 Window
- 创建 Tab
- JS 执行
- 网络请求
- Cookie
- Extension API
- `window.open`

但以下行为全部不得上屏：

- Show
- Activate
- Raise
- extension focus
- popup
- session restore

### Gate 2

如果不能可靠做到：

```text
正常 BrowserWindow 存在
+
桌面零显示
```

停止后续开发，先解决该基础问题。

---

## Phase 3：实现 USER_EXPLICIT

专门解决：

> 用户点击 App 也打不开。

实现统一的用户显式前台入口。

覆盖：

- Dock
- Launchpad
- 菜单栏
- App reopen
- CLI show
- 已运行 Browser CLI 再次点击

状态：

```text
BACKGROUND
+
USER_EXPLICIT
→
HUMAN
→
Show + Activate
```

### Gate 3

必须证明：

```text
AI 永远不能偷偷 Show
```

同时：

```text
用户永远可以主动 Show
```

二者都成立后才能继续。

---

## Phase 4：实现 Foreground Assist

添加：

```text
requestForeground
ForegroundLease
ASSIST state
```

先只支持：

- login
- captcha
- confirmation
- other

需要覆盖：

- Assist 成功
- Assist 拒绝
- Assist busy
- Assist timeout
- 用户接管 Assist
- 用户接管后 AI 无权收回

---

## Phase 5：后台 compositor + screenshot

目标：

```text
BACKGROUND
+
window never shown
+
captureScreenshot
=
有效图片
```

禁止：

```text
show
activate
reveal
temporary normal
minimize cycle
```

截图失败允许：

```text
CAPTURE_UNAVAILABLE
```

不允许用显示窗口换截图成功。

测试：

- 静态 HTML
- 动态页面
- CSS animation
- Canvas
- 视频页面
- 长页面
- 多次连续截图

### Gate 5

如果后台 compositor/surface 方案需要非常深的 Chromium 改动或成本异常高，再评估截图架构。

---

## Phase 6：后台多窗口并行

恢复自然模型：

```text
Space A → Window A
Space B → Window B
Space C → Window C
```

每个 Window：

- 可以拥有 active tab
- 可以独立加载
- 可以独立截图
- 不出现在 Desktop

示例：

```text
Window A
└─ ChatGPT active

Window B
└─ DeepSeek active

Window C
└─ Gemini active
```

Desktop：

```text
0 Browser CLI windows
```

先保证语义正确，再做资源优化。

后续可增加：

```text
FULL
THROTTLED
SUSPENDED
```

三档 render policy。

---

## Phase 7：BrowserPilot capability 接入

BrowserPilot 启动后主动握手。

Browser CLI：

```json
{
  "browser": "browser-cli",
  "protocol": 1,
  "capabilities": {
    "backgroundWindows": true,
    "backgroundCapture": true,
    "parallelWindows": true,
    "foregroundAssist": true,
    "hardFocusIsolation": true
  }
}
```

普通 Chrome：

```text
没有 Browser CLI capability
→ Generic Chrome 路径
```

BrowserPilot 保持一个插件：

```text
                BrowserPilot
                 /        \
                /          \
          Chrome         Browser CLI
          普通能力        完整能力
```

禁止拆成：

```text
BrowserPilot-for-Chrome
BrowserPilot-for-BrowserCLI
```

---

## Phase 8：删除旧 Backlight workaround

当 Chromium 内核能力成熟后，主动删除：

- WindowPopWatchdog 的“恢复”职责
- 2px corner
- capture reveal
- normal → minimized 循环
- AppKit hide 重试
- screenshot display lease
- background show 后再 hide
- delayed restore compensation
- 多套 visibility owner

watchdog 可以保留，但职责变为：

```text
BACKGROUND 下检测到 native visible window
→ FATAL
→ 保存证据
→ 测试失败
```

不能再自动修好后算成功。

---

## Phase 9：正式 Browser CLI App

最后才做：

- Branding
- Browser CLI.app
- 正式 Release build
- codesign
- launcher
- update
- packaging
- user profile migration

不要提前花时间在这些外围工作上。

---

# 10. 推荐代码组织

不要把完整 Chromium 源码直接塞进 Browser CLI Git 仓库。

建议：

```text
~/dev/
├── browser-cli/
│   ├── browserpilot-integration/
│   ├── cli/
│   ├── launcher/
│   ├── tests/
│   ├── chromium/
│   │   ├── patches/
│   │   └── VERSION
│   └── docs/
│
└── chromium/
    └── src/
        └── out/Dev/
```

Browser CLI Git 仓库只保存：

```text
Chromium revision
+
Browser CLI patches
+
GN config
+
tests
+
scripts
```

推荐 patch series：

```text
patches/
001-managed-window-mode.patch
002-foreground-origin.patch
003-user-explicit.patch
004-foreground-lease.patch
005-background-compositor.patch
006-background-capture.patch
007-popup-policy.patch
```

目标：

> Browser CLI 对 Chromium 的修改保持窄、小、清晰、可重放。

升级 Chromium：

```text
new upstream
↓
apply patch series
↓
解决发生冲突的少数 patch
↓
run Browser CLI invariants
```

不要维护一个已经无法解释差异来源的大型长期 fork。

---

# 11. 现有 Backlight 的处理方式

现有 Backlight 不立刻删除。

暂时定义为：

```text
Browser CLI Legacy Backend
```

用途：

1. Chromium Patch 尚未成熟时继续开发 BrowserPilot
2. 保留已有功能
3. 做新旧方案 A/B 验证
4. 提供行为基线

但从现在开始：

> 除阻塞性问题外，不再向旧架构增加新的复杂窗口 workaround。

原因：

未来很可能删除这些代码。

---

# 12. 长期目标架构

当前：

```text
BrowserPilot
   ↓
Native Host
   ↓
daemon
   ↓
CDP
   ↓
capture manager
   ↓
window manager
   ↓
watchdog
   ↓
AppKit helper
   ↓
Chromium
```

目标：

```text
BrowserPilot
     ↓
Browser CLI API
     ↓
Chromium
 ┌─────────────────┐
 │ Window Policy   │
 │ Render Policy   │
 │ Capture Policy  │
 │ Assist Policy   │
 └─────────────────┘
```

外围 daemon 可以继续存在，但只负责：

- CLI API
- 进程生命周期
- profile
- 日志
- 升级
- capability handshake

它不再负责：

- 猜窗口该不该显示
- popup 后再隐藏
- screenshot 前 reveal
- 多套 show/hide compensation
- 与 AppKit 争夺 visibility ownership

---

# 13. BrowserPilot 与 Browser CLI 的长期关系

BrowserPilot：

> 通用浏览器 Agent 插件。

Browser CLI：

> 对 BrowserPilot 提供增强能力的 Chromium Runtime。

BrowserPilot 在普通 Chrome 中：

- 基础 Space / Tab
- DOM / Extension / debugger
- 通用模板
- best-effort background

BrowserPilot 在 Browser CLI 中：

- 真正后台 Window
- 后台渲染
- 后台截图
- hard focus isolation
- parallel windows
- foreground assist

因此 BrowserPilot 的环境探测 / capability handshake 是长期设计，不是临时 workaround。

---

# 14. 明确禁止的开发方向

后续 AI / 开发者不要再采用以下方案：

1. 不要为了截图临时显示 Window
2. 不要把 watchdog recovery 当做“零弹窗成功”
3. 不要用最终隐藏状态代替全过程零可见验收
4. 不要让 BACKGROUND 一刀切阻止用户打开 App
5. 不要把 `applicationDidBecomeActive` 直接视为用户点击
6. 不要让 BrowserPilot 有两套插件代码
7. 不要运行时 patch BrowserPilot JavaScript
8. 不要让多个模块都拥有 show/hide/activate 权限
9. 不要继续堆 `minimize → hide → restore → retry` 补偿逻辑
10. 不要第一阶段就优化 CPU/GPU/内存
11. 不要第一阶段就做 Branding / installer / updater
12. 不要把 Chromium 完整源码直接提交到 Browser CLI 主仓库

---

# 15. 三个核心技术 Gate

整个项目必须按 Gate 推进。

## Gate A：BACKGROUND_PRESENTATION

证明：

```text
真实 Chrome BrowserWindow / WebContents 存在
+
Renderer 正常
+
桌面永远不显示窗口
```

失败则停止。

---

## Gate B：USER_EXPLICIT

证明：

```text
AI 不允许弹窗
```

同时：

```text
用户点击 App 永远能打开
```

二者必须同时成立。

---

## Gate C：BACKGROUND_CAPTURE

证明：

```text
窗口从未 Show
+
后台页面持续得到有效 surface
+
Screenshot 正常
```

如果失败，不允许退回：

```text
reveal → capture → hide
```

---

# 16. 推荐执行顺序

```text
0. 冻结状态机 / Origin / 不变量
        ↓
1. 原版 Chromium 本地编译
        ↓
2. BACKGROUND_PRESENTATION
        ↓
3. USER_EXPLICIT
        ↓
4. Foreground Assist
        ↓
5. Background compositor + screenshot
        ↓
6. 后台多 BrowserWindow 并行
        ↓
7. BrowserPilot capability 接入
        ↓
8. 删除 Legacy workaround
        ↓
9. Browser CLI 正式发行
```

---

# 17. 最终验收目标

Browser CLI 第一版完成时至少满足：

```text
1. 启动 Browser CLI，AI 后台运行时桌面 0 窗口
2. AI 可以创建多个 BrowserWindow
3. 多 Window 可以并行运行
4. 每个 Window 可以拥有自己的 active tab
5. 后台页面 JS / 网络正常
6. 后台页面可以截图
7. 截图绝不导致显示窗口
8. window.open 不导致窗口上屏
9. Extension focus 不导致窗口上屏
10. Session restore 不导致窗口上屏
11. AI 无权直接把后台窗口弹前台
12. AI 可以申请 login/captcha Assist
13. 用户点击 App/Dock 时永远可以打开
14. 用户接管后 AI 无权收回
15. 用户主动交还后恢复 BACKGROUND
16. BrowserPilot 同一份插件同时兼容普通 Chrome
17. BACKGROUND 出现真实 Window 视为 fatal bug
```

---

# 18. 给后续 AI 的最高优先级说明

在实现任何功能之前，先遵守以下四条：

> **A. BACKGROUND 并不意味着用户不能打开浏览器。**

> **B. USER_EXPLICIT 永远高于所有 AI 后台策略。**

> **C. Screenshot / Popup / Extension / Session Restore 都不能成为窗口上屏的理由。**

> **D. Window 一旦在 BACKGROUND 状态真实出现在桌面，即为失败；不能通过随后隐藏来判定成功。**

如果实现方案违反以上任意一条，应优先修改设计，而不是添加新的 watchdog / retry / hide compensation。
