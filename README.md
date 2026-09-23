# Browser CLI

项目持续状态与下一项调度统一查看 [`PROJECT-PROGRESS.md`](PROJECT-PROGRESS.md)。任何代理开始工作前，
必须同时阅读该进度表、宏观设计和 `AGENTS.md`；完成单个阶段不代表停止整个项目跟进。

面向 AI Agent 的 Chromium 浏览器运行时。宏观设计见 [`docs/browser-cli-development-plan.md`](docs/browser-cli-development-plan.md)。

本仓库**只保存**：

```text
Chromium revision + Browser CLI patches + GN 配置 + scripts + tests
```

**不包含 Chromium 源码。** 在另一台 Apple Silicon Mac 上 clone 本仓库后，必须自行重新下载并编译 Chromium（源码约 30+ GiB、构建产物约 8+ GiB），仓库不会、也不应携带这些内容。

## 当前状态（2026-09-22）

| 阶段 | 状态 |
| --- | --- |
| Phase 0 冻结设计 | 完成（设计文档已版本化收录） |
| Phase 1 原版 Chromium 构建验证 | 首次编译 exit 0、无 UI CLI smoke、空跑增量、真实 C++ 增量均通过；真实可见 App 启动**未做**（需用户明确授权） |
| Phase 2 BACKGROUND_PRESENTATION | **第一切片完成**：`chromium/patches/001-managed-window-mode.patch`（策略 + host/AppKit/bridge 三层拦截 + 结构化日志 + USER_EXPLICIT 最小保护 + 单测/增量/无窗口 runtime smoke 全通过）；Gate A 仅在该切片范围成立，未证明项见快照 |
| 稳定签名 / 产品身份 | 已完成实际构建+签名+验收：`Browser CLI.app`（`com.jiangao.browsercli`，Team `RAXC5D3A3S`，Developer ID，非 ad-hoc，DR 稳定，deep-strict 通过）；构建流程强制签名（60s 停滞熔断、component dylib 开发期签名）；见 [`docs/signing-and-product-identity-2026-09-22.md`](docs/signing-and-product-identity-2026-09-22.md) §8 |
| Branding / Logo | 已接入：源图经 `scripts/generate-icons.sh` 生成 `app.icns` + `Assets.car`（`mac_app_icon_dir`），主 App 与 Alert Helper 使用同一图标，图标 sha256 与生成物一致；源图未修改 |

环境与验收快照见 [`docs/phase1-environment-2026-09-21.md`](docs/phase1-environment-2026-09-21.md)，Phase 2 第一切片见 [`docs/phase2-background-presentation-2026-09-22.md`](docs/phase2-background-presentation-2026-09-22.md)，Xcode 27 踩坑见 [`docs/pitfalls-xcode27.md`](docs/pitfalls-xcode27.md)。

## 目录结构

```text
browser-cli/
├── README.md
├── AGENTS.md                # 后续 AI/自动化强制规则（测试卫生、签名、patch 约束）
├── .gitignore
├── assets/branding/         # 用户确认的品牌源资产；尚未接入应用包
├── config/product-identity.env # 唯一产品/签名身份配置源（名称、Bundle ID、Team ID、证书 SHA-1/名称）
├── chromium/
│   ├── VERSION              # 已验证的 Chromium revision / 版本
│   ├── args.gn              # 与已验收 out/Dev/args.gn 一致（含 branding_file_path）
│   └── patches/             # 编号 patch；001-managed-window-mode、002-browser-cli-branding 已入库
├── scripts/                 # preflight/checkout/configure/build/verify-phase1 + sign/verify-signature
├── docs/                    # 设计版本化副本 + Phase 1/2 快照 + 签名/产品身份 + 踩坑
├── tests/                   # 脚本回归 + 卫生门禁 + 身份一致性 + Phase 2 无窗口 smoke
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

`scripts/build.sh` 成功后会**强制**调用 `scripts/sign-browser-cli.sh`（inside-out，固定 Developer ID
身份）与 `scripts/verify-signature.sh`；任一失败即整体失败，不存在 ad-hoc 降级。`verify-phase1.sh`
在所有增量构建后用同样的签名步骤收尾。

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
- **测试钥匙串/权限隔离（强制）**：任何测试/自动化启动 Chromium/Backlight 必须同时带 `--use-mock-keychain` 与 `mktemp -d` 的独立 `--user-data-dir`；不得读写用户真实钥匙串，不得触发任何系统对话框。静态门禁：`tests/check-browser-launch-hygiene.sh`。生产运行**不得**使用 mock keychain（正式版依靠稳定签名 + 稳定安装路径，Phase 9）。根因与规则见 [`AGENTS.md`](AGENTS.md)。
- **产品签名（强制，无 ad-hoc 降级）**：产品身份固定在 [`config/product-identity.env`](config/product-identity.env)；`scripts/build.sh` 每次 `chrome` 构建后自动执行 inside-out Developer ID 签名与签名验收，失败即整体失败。测试入口拒绝启动 ad-hoc App。仅允许只读 `security find-identity` 与 `codesign`，不创建/导入/删除/导出/吊销钥匙串证书，不读取私钥。
- **回归测试**：`tests/run-script-tests.sh` 覆盖语法/help/dry-run、磁盘硬下限负例、watchdog 绕过负例、`--resume` 一致性负例、代理静态审计、浏览器启动卫生门禁与产品身份/签名工具静态检查；不联网、不编译、不启动浏览器。

## 边界

- 不修改 Backlight/BrowserPilot 产品代码；本仓库不含这些源码。
- Chromium 修改只以 `chromium/patches/` 的编号 patch 形式入库（`001-managed-window-mode.patch` 起），不把 Chromium 源码或大文件复制进本仓库。
- 不创建远端、不 push（远端归属与仓库托管方式待用户决定）。
- 真实可见 App 启动、签名/打包、Branding、launcher 均属后续阶段（Phase 9）。
