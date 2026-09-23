# docs

| 文档 | 内容 |
| --- | --- |
| [`browser-cli-development-plan.md`](browser-cli-development-plan.md) | 宏观设计：状态机、ForegroundOrigin、行为矩阵、内核不变量、ForegroundLease、Phase 0–9、Gate A–C。**版本化副本**，2026-09-21 从 `~/work/browser/browser-cli-development-plan.md` 原样复制（sha256 `6f9e56b2ff238a1df06596ae1964ef2b835581c1cd8f379bf116132083ddd697`）。 |
| [`phase1-environment-2026-09-21.md`](phase1-environment-2026-09-21.md) | Phase 1 环境与验收快照：revision、工具链、构建/增量数据、磁盘、Gate 1–4 结果、未完成项。 |
| [`phase2-background-presentation-2026-09-22.md`](phase2-background-presentation-2026-09-22.md) | Phase 2 第一切片：BACKGROUND_PRESENTATION 策略、三层拦截、结构化日志、USER_EXPLICIT 保护、patch 001、构建/单测/增量/无窗口 runtime smoke 结果、Gate A 未证明项与风险；追加 5 记录 patch 003 CLI 显式 `show` 生产者的非 runtime 集成证据与已知限制（REVIEW）；追加 6 记录 Foreground Assist Slice 1（策略层 lease + patch 004，lease 8/8、policy 27/27、顺序重放逐字节一致）的 REVIEW 状态与无 producer/host/timer/runtime 限制。 |
| [`signing-and-product-identity-2026-09-22.md`](signing-and-product-identity-2026-09-22.md) | 稳定签名与产品身份：唯一配置源、证书精确匹配、patch 002 branding、inside-out 签名脚本、签名验收脚本、构建流程强制签名、未做 notarization/Hardened Runtime、尚需复核项。 |
| [`pitfalls-xcode27.md`](pitfalls-xcode27.md) | Xcode 27 的两个阻塞（`/usr/bin/python3` shim + 相对 `SDKROOT`；Metal Toolchain 缺失）：错误原文、原因、修复与验证命令。 |

相关但不入库的现场记录：`~/work/browser/BROWSER-CLI-ENV-STATUS.md`（环境状态）、`~/work/browser/BROWSER-CLI-LOCAL-REPO-STATUS.md`（本仓库的建立与验收记录）。
