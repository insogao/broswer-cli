# browserpilot-integration

占位目录。**当前没有实现。**

边界（按设计 §6、§7、§13）：

- BrowserPilot 保持**同一份插件**同时兼容普通 Chrome 与 Browser CLI；禁止拆成两份插件代码（`BrowserPilot-for-Chrome` / `BrowserPilot-for-BrowserCLI`）。
- 后续此处放 capability 握手与前台协助请求（`requestForeground({targetId, reason})`）的集成说明/契约样例，例如：
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
- 禁止运行时 patch BrowserPilot 的 JavaScript；内核必须作为最终硬保护层，即使插件出 bug 也不能弹窗。
- 本目录不存放 Chromium 源码或插件构建产物。
