# tests

占位目录。**当前没有可运行的测试代码。**

边界（按设计 §10、§15）：

- Phase 1 的验收脚本在 [`../scripts/verify-phase1.sh`](../scripts/verify-phase1.sh)（产物检查、无 UI smoke、空跑增量、真实 C++ 增量），不在本目录。
- 后续此处放 **Gate A/B/C 的自动化验收**：BACKGROUND_PRESENTATION（窗口存在但桌面零显示）、USER_EXPLICIT（AI 不能弹窗 + 用户永远能打开）、BACKGROUND_CAPTURE（从未 Show 且截图有效）。
- 测试必须能判定「全程零可见」；禁止把 watchdog 事后隐藏当作成功（设计 §5.1、§14）。
- 不在此目录实现产品功能，不引入第二套窗口可见性逻辑。
