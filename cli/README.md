# cli

占位目录。**当前没有实现。**

边界（按设计 §7、§12、§13）：

- 后续此处放 Browser CLI 的 CLI/API 入口（Agent 与 BrowserPilot 调用的命令面、capability 协商的客户端侧）。
- 窗口/焦点/截图策略**不属于这里**：必须实现在 Chromium 内部（Window/Render/Capture/Assist Policy），CLI 只是入口，不能自己猜窗口该不该显示，也不能做 popup 后再隐藏。
- 禁止在此实现 `show → capture → hide`、reveal、minimize 循环等补偿逻辑（设计 §14）。
- Phase 2 之前不得编写任何功能代码。
