# scripts

可复现入口。所有脚本：`bash -n` 可校验、支持 `--help`、支持 `--dry-run`（只打印命令，不下载/不编译/不启动浏览器）。

| 脚本 | 作用 | 关键默认 |
| --- | --- | --- |
| `preflight.sh` | 只读环境检查：macOS/arm64、完整 Xcode、SDK、Metal Toolchain、depot_tools python、checkout/gn/ninja、磁盘门禁、代理、单一 out、并发 fetch | `--check-network` 才做网络探测 |
| `checkout.sh` | `fetch --no-history chromium`（显式经代理）；`--resume` 续跑 `gclient sync`；`--pin` 固定到 `chromium/VERSION` | 只建一个 checkout；发现第二个 fetch 立即报错 |
| `configure.sh` | 把 `chromium/args.gn` 落到唯一 `out/Dev/args.gn` 并 `gn gen out/Dev` | 其他 out 目录存在即报错；`--force` 才覆盖 args.gn |
| `build.sh` | `ninja -C out/Dev -j 6 chrome` | 构建前磁盘门禁 + 运行时 25 GiB 熔断；`--jobs >6` 需显式 `--allow-more-jobs` |
| `verify-phase1.sh` | Phase 1 无 UI 验收：产物检查 + `--version` smoke + 空跑增量 + 真实 C++ 增量（探针→精确恢复→重编） | 不使用 `open`；不使用 `git reset --hard`/`git checkout --` |

## 环境变量覆盖

```text
CHROMIUM_ROOT=~/dev/chromium     # checkout 根（其下是 src/ 与 out/Dev）
DEPOT_TOOLS=~/dev/depot_tools
OUT_NAME=Dev                     # 唯一 out 目录名
BUILD_JOBS=6  MAX_JOBS=6
DISK_MIN_GIB=25  DISK_PATH=/System/Volumes/Data
PROXY_URL=http://127.0.0.1:10808
BROWSER_CLI_LOG_DIR=<repo>/build-logs
```

## 典型流程

```sh
scripts/preflight.sh
scripts/checkout.sh            # 首次；之后用 scripts/checkout.sh --resume
scripts/configure.sh
scripts/build.sh
scripts/verify-phase1.sh
```

## 只读自检（不下载/不编译/不启动）

```sh
for f in scripts/*.sh scripts/lib/*.sh; do bash -n "$f" || echo "syntax error: $f"; done
scripts/preflight.sh --dry-run
scripts/checkout.sh --dry-run
scripts/configure.sh --dry-run
scripts/build.sh --dry-run
scripts/verify-phase1.sh --dry-run
```
