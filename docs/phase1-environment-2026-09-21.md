# Phase 1 环境与验收快照（2026-09-21）

本文件记录已验证的一次真实构建与验收数据，供另一台 Apple Silicon Mac 复现时对照。

## 主机与工具链

| 项目 | 实测值 |
| --- | --- |
| 硬件/系统 | Apple M4 `arm64`、16 GiB RAM、macOS 27.0 |
| Xcode | Xcode 27.0 (27A266a)，`xcode-select -p` 指向完整 `Xcode.app`，macOS SDK 27.0 |
| Metal Toolchain | Apple metal version 32023.921（Xcode 27 需单独下载，见 `pitfalls-xcode27.md`） |
| depot_tools | HEAD `ca054941f756b50e1a3d83727270d879bec1f331`；构建使用其 hermetic CPython 3.11.8 |
| 网络 | 终端直连 `chromium.googlesource.com:443` 超时；经本机 HTTP 代理 `127.0.0.1:10808` 可访问（git 子进程已实测继承代理） |

## Chromium checkout

```text
revision  7d0f28c30d1c0fadbce17ecec0ee2565aef9a940  (2026-09-20 19:37:49 -0700)
version   156.0.8067.0
方式      fetch --no-history chromium（shallow，.git/shallow 存在；不维护完整 Git cache）
复现      scripts/checkout.sh 默认固定到本 revision：fetch → git fetch --depth 1 + checkout --detach（经代理）→ gclient sync --no-history --revision src@<rev>（经代理）；HEAD != VERSION 时 verify-phase1 默认 FAIL
状态      最终 git status --porcelain 干净
```

## GN 配置（`chromium/args.gn`）

```gn
is_debug = false
is_component_build = true
symbol_level = 0
target_cpu = "arm64"
use_lld = false
```

`gn gen out/Dev` → `Done. Made 36251 targets from 5016 files`。

## 构建数据

| 项目 | 数据 |
| --- | --- |
| 命令 | `ninja -C out/Dev -j 6 chrome`（16 GiB RAM，swap 已占用 4.8–5.5 GiB，故 `-j 6`） |
| 收尾轮 | 2026-09-21 15:22:31 → **18:15:02，Ninja exit 0**（2h52m31s，24682 edges） |
| 全流程 | 14:05 首次尝试 → 18:15 完成 ≈ 4h10m（含两次环境阻塞的修复与重跑） |
| 产物 | `out/Dev` 8.2 GiB；`Chromium.app` 114 MiB（component build：518 个 dylib 在 `out/Dev` 顶层） |
| 磁盘 | 完成时 `/System/Volumes/Data` 可用 51 GiB（>25 GiB 门禁内） |

## 验收结果（无可见 UI）

| Gate | 结果 | 数据 |
| --- | --- | --- |
| 1 构建证据 | PASS | `NINJA EXIT rc=0`；`MacOS/Chromium` 34,392 B arm64、Framework 16,752 B arm64、8 个 Helper arm64；`Info.plist` `CFBundleShortVersionString=156.0.8067.0`；codesign `adhoc,linker-signed` 结构可读（`codesign --verify` 返回 1，属未封装开发构建预期） |
| 2 命令行 smoke | PASS | `Chromium --version` → `Chromium 156.0.8067.0`，exit 0；无残留进程；Profile 目录未被触碰；**未使用 `open`/Dock/Launchpad，无窗口**。耗时：首次冷启动 13–30s（dyld 一次性校验约 1600 个 dylib），warm ≈1.16s，重链接后首跑 ≈5.5s |
| 3 空跑增量 | PASS | `ninja: no work to do.`，exit 0，17s |
| 4 真实 C++ 增量 | PASS | 探针 `base/debug/alias.cc` 追加 `static_assert(true, "browser-cli-phase1-incremental-probe");` → exit 0，16s，实际 **2 edges**（`CXX alias.o` + `SOLINK libbase.dylib`）；用 `git cat-file blob` 精确恢复（sha256 逐字节一致、`git status` 干净）；恢复后重编 exit 0，21s，2 edges；最终 `no work to do`，再跑 `--version` 仍 rc=0 |

## 已知无害现象

- 每次 SOLINK：`ld: warning: ignoring -lto_library '.../libLTO.dylib', file does not exist`（`use_lld=false` + `symbol_level=0` 组合下出现，不影响产物）。
- 运行 stderr 有 component build 常见的 objc 重复类告警（如 `ANGLESwapCGLLayer`），无害。
- component build 的 dylib 位于 `out/Dev/` 顶层，App 通过 rpath `@loader_path/../../../../../..` 查找，因此**必须从 `out/Dev` 内运行**，不能单独拷走 `Chromium.app`。

## 未完成 / 边界

- **真实可见 App 启动未验收**（本阶段禁止桌面 UI，需用户明确授权的独立任务）。
- 签名/封装/分发、Branding、launcher 均未做（Phase 9）。
- 未开始 Phase 2；`chromium/patches/` 仍为空占位。

## 更正（2026-09-22）：测试启动 Chrome 必须带 keychain/profile 隔离

本文档记录的 `Chromium --version` smoke 在运行时**没有**加 `--use-mock-keychain`。
`--version` 在 profile/钥匙串初始化之前就退出，当时未观察到弹窗，但这不符合 2026-09-22 起生效的
强制测试卫生规则：

- 任何测试/自动化启动 Chromium/Backlight 必须同时带 `--use-mock-keychain` 与
  `mktemp -d` 的独立 `--user-data-dir`；
- 禁止读写用户真实钥匙串、禁止任何系统对话框（flash 根因与完整规则见 `AGENTS.md` 与
  `docs/phase2-background-presentation-2026-09-22.md` §9）；
- `scripts/verify-phase1.sh` 的 smoke 已按此修正；生产运行不得使用 mock keychain。

本更正不改变 Phase 1 的其他验收结论。
