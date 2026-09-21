# Browser CLI

面向 AI Agent 的 Chromium 浏览器运行时。宏观设计见 [`docs/browser-cli-development-plan.md`](docs/browser-cli-development-plan.md)。

本仓库**只保存**：

```text
Chromium revision + Browser CLI patches + GN 配置 + scripts + tests
```

**不包含 Chromium 源码。** 在另一台 Apple Silicon Mac 上 clone 本仓库后，必须自行重新下载并编译 Chromium（源码约 30+ GiB、构建产物约 8+ GiB），仓库不会、也不应携带这些内容。

## 当前状态（2026-09-21）

| 阶段 | 状态 |
| --- | --- |
| Phase 0 冻结设计 | 完成（设计文档已版本化收录） |
| Phase 1 原版 Chromium 构建验证 | 首次编译 exit 0、无 UI CLI smoke、空跑增量、真实 C++ 增量均通过；真实可见 App 启动**未做**（需用户明确授权） |
| Phase 2 BACKGROUND_PRESENTATION | **未开始**；`chromium/patches/` 仅占位说明 |

环境与验收快照见 [`docs/phase1-environment-2026-09-21.md`](docs/phase1-environment-2026-09-21.md)，Xcode 27 踩坑见 [`docs/pitfalls-xcode27.md`](docs/pitfalls-xcode27.md)。

## 目录结构

```text
browser-cli/
├── README.md
├── .gitignore
├── chromium/
│   ├── VERSION              # 已验证的 Chromium revision / 版本
│   ├── args.gn              # 与已验收 out/Dev/args.gn 完全一致
│   └── patches/             # 占位；Phase 2 起放编号 patch
├── scripts/                 # 可复现入口：preflight/checkout/configure/build/verify-phase1
├── docs/                    # 设计版本化副本 + Phase 1 快照 + 踩坑
├── tests/                   # 占位（后续测试资产）
├── cli/                     # 占位（后续 CLI 入口，Phase 2+）
├── launcher/                # 占位（后续 launcher，Phase 9）
└── browserpilot-integration/ # 占位（后续 capability 握手，Phase 7）
```

## 快速开始（另一台 Apple Silicon Mac）

前置：完整 Xcode（非仅 Command Line Tools）、`depot_tools`、本机 HTTP 代理（默认 `http://127.0.0.1:10808`，可用 `PROXY_URL` 覆盖）、约 60 GiB 可用磁盘。

```sh
git clone <本仓库地址> ~/dev/browser-cli
cd ~/dev/browser-cli

scripts/preflight.sh                 # 只读检查：Xcode/SDK/Metal/depot_tools/磁盘/代理/并发 fetch
scripts/checkout.sh                  # fetch --no-history chromium（自动经代理；只建一个 checkout）
scripts/configure.sh                 # 用 chromium/args.gn 生成唯一 out/Dev
scripts/build.sh                     # ninja -C out/Dev -j 6 chrome（带 25 GiB 熔断）
scripts/verify-phase1.sh             # 无 UI 验收：产物检查 + --version + 空跑 + 真实 C++ 增量
```

所有脚本支持 `--help` 与 `--dry-run`（只打印将执行的命令，不下载/不编译/不启动浏览器）。

## 安全默认值（脚本强制）

- **单一 `out/Dev`**：发现其他 out 目录即报错，不并发维护多份构建。
- **`--no-history`**：shallow checkout，不维护完整 Git history/cache。
- **代理显式注入**：仅对网络命令注入 `HTTPS_PROXY/HTTP_PROXY`，不改全局 Git/`.zshrc`。
- **`-j 6`**：照顾 16 GiB RAM；默认拒绝更高并行度（`--allow-more-jobs` 可显式覆盖）。
- **25 GiB 磁盘门禁**：构建前检查，构建中每 60s 检查，低于阈值自动终止 ninja。
- **depot_tools Python 优先**：规避 Xcode 27 `/usr/bin/python3` shim 与相对 `SDKROOT` 冲突。
- **不启动可见 UI**：`verify-phase1.sh` 只用二进制的 `--version` 做有界 smoke，不使用 `open`/Dock/Launchpad，不创建窗口。

## 边界

- 不修改 Chromium/Backlight/BrowserPilot 产品代码；本仓库不含这些源码。
- 不创建远端、不 push（远端归属与仓库托管方式待用户决定）。
- 真实可见 App 启动、签名/打包、Branding、launcher 均属后续阶段（Phase 9）。
