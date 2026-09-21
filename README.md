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

前置：完整 Xcode（非仅 Command Line Tools）、`depot_tools`、本机 HTTP 代理（默认 `http://127.0.0.1:10808`，可用 `PROXY_URL` 覆盖）、**至少 65 GiB 可用磁盘**（见下节）。

```sh
git clone <本仓库地址> ~/dev/browser-cli
cd ~/dev/browser-cli

scripts/preflight.sh                 # 只读检查：Xcode/SDK/Metal/depot_tools/磁盘/代理/并发 fetch
scripts/checkout.sh                  # 默认固定到 chromium/VERSION 的 revision，并同步同 revision 的依赖
scripts/configure.sh                 # 用 chromium/args.gn 生成唯一 out/Dev
scripts/build.sh                     # ninja -C out/Dev -j 6 chrome（25 GiB 熔断始终生效）
scripts/verify-phase1.sh             # 无 UI 验收：revision 检查 + 产物 + --version + 空跑 + 真实 C++ 增量
```

所有脚本支持 `--help` 与 `--dry-run`（只打印将执行的命令，不下载/不编译/不启动浏览器）。

### 可复现的 revision 固定（默认行为）

`scripts/checkout.sh` 默认执行：

```text
fetch --no-history chromium                        # 建立唯一 checkout
git fetch --depth 1 origin <chromium/VERSION rev>  # 经代理
git checkout --detach <chromium/VERSION rev>
gclient sync --no-history --revision src@<rev>     # 经代理；DEPS 依赖与该 revision 匹配
```

如需上游 tip（**不可复现，不得用于验收**）必须显式：

```sh
scripts/checkout.sh --tip        # 明确 opt-out；输出会警告 NOT reproducible
scripts/checkout.sh --repin      # 把已有 tip checkout 重新固定到 chromium/VERSION
scripts/checkout.sh --resume     # 已有固定 checkout：只重跑 revision 匹配的依赖同步
```

`scripts/verify-phase1.sh` 默认要求 **checkout HEAD == chromium/VERSION revision**，不一致即 FAIL；只有显式 `--allow-tip`（同样不可用于验收）才降级为警告。`--check-only` 只做这一项只读检查。

### 磁盘门禁：两个不同概念，不可互相替代

| 门禁 | 默认 | 适用 | 说明 |
| --- | --- | --- | --- |
| **新机器启动门禁** `DISK_MIN_FRESH_GIB` | 65 GiB | 全新 checkout 开始前（`checkout.sh` 默认/`--tip`） | 源码 ~31 GiB + `out/Dev` ~8 GiB + 25 GiB 运行保留；可调高，**不得低于 `DISK_MIN_GIB`** |
| **运行时熔断** `DISK_MIN_GIB` | 25 GiB | 构建前 + 构建中每 60s | **硬下限 25 GiB：只能调高、不能调低**（低于 25 或试图设为 0 会在任何脚本动作前直接报错）；`build.sh` 无任何绕过开关 |

## 安全默认值（脚本强制）

- **默认固定 revision**：`checkout.sh` 默认固定到 `chromium/VERSION` 并做 revision 匹配的依赖同步；tip 必须显式 `--tip`，且不可用于验收。
- **单一 `out/Dev`**：发现其他 out 目录即报错，不并发维护多份构建。
- **`--no-history`**：shallow checkout，不维护完整 Git history/cache。
- **代理显式注入**：所有网络 git/gclient/fetch/curl 调用都经 `with_proxy`，不改全局 Git/`.zshrc`。
- **`-j 6`**：照顾 16 GiB RAM；默认拒绝更高并行度（`--allow-more-jobs` 可显式覆盖）。
- **25 GiB 运行时熔断（不可绕过）**：构建前检查，构建中每 60s 检查，低于阈值自动终止 ninja；硬下限 25 GiB，环境变量只能调高。
- **HEAD 必须等于 pinned revision**：`verify-phase1.sh` 默认对不一致判 FAIL；`checkout.sh --resume` 也会在任何网络操作前拒绝 HEAD != VERSION 的 checkout（tip checkout 必须先用 `--repin`）。
- **depot_tools Python 优先**：规避 Xcode 27 `/usr/bin/python3` shim 与相对 `SDKROOT` 冲突。
- **不启动可见 UI**：`verify-phase1.sh` 只用二进制的 `--version` 做有界 smoke，不使用 `open`/Dock/Launchpad，不创建窗口。
- **回归测试**：`tests/run-script-tests.sh` 覆盖语法/help/dry-run、磁盘硬下限负例、watchdog 绕过负例、`--resume` 一致性负例与代理静态审计；不联网、不编译、不启动浏览器。

## 边界

- 不修改 Chromium/Backlight/BrowserPilot 产品代码；本仓库不含这些源码。
- 不创建远端、不 push（远端归属与仓库托管方式待用户决定）。
- 真实可见 App 启动、签名/打包、Branding、launcher 均属后续阶段（Phase 9）。
