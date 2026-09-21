# scripts

可复现入口。所有脚本：`bash -n` 可校验、支持 `--help`、支持 `--dry-run`（只打印命令，不下载/不编译/不启动浏览器）。

| 脚本 | 作用 | 关键默认 |
| --- | --- | --- |
| `preflight.sh` | 只读环境检查：macOS/arm64、完整 Xcode、SDK、Metal Toolchain、depot_tools python、checkout/gn/ninja、**两种磁盘门禁**、代理、单一 out、并发 fetch | `--check-network` 才做网络探测 |
| `checkout.sh` | **默认固定 revision**：fetch → `git fetch --depth 1` + `checkout --detach`（经代理）→ `gclient sync --no-history --revision src@<rev>`（经代理）；`--tip` 显式 opt-out（不可复现）；`--resume` 只重跑 revision 匹配依赖同步；`--repin` 把已有 tip checkout 重新固定 | 只建一个 checkout；发现第二个 fetch 立即报错 |
| `configure.sh` | 把 `chromium/args.gn` 落到唯一 `out/Dev/args.gn` 并 `gn gen out/Dev` | 其他 out 目录存在即报错；`--force` 才覆盖 args.gn |
| `build.sh` | `ninja -C out/Dev -j 6 chrome` | 构建前运行时门禁 + **始终开启**的 25 GiB 熔断（无绕过选项）；`--jobs >6` 需显式 `--allow-more-jobs` |
| `verify-phase1.sh` | Phase 1 无 UI 验收：**HEAD 必须等于 chromium/VERSION revision（默认 FAIL）** + 产物检查 + `--version` smoke + 空跑增量 + 真实 C++ 增量（探针→精确恢复→重编） | `--check-only` 只做只读 revision/args 检查；`--allow-tip` 仅限非验收 tip 构建；不使用 `open`；不使用 `git reset --hard`/`git checkout --` |

## 磁盘门禁（两个概念，不可互相替代）

| 变量 | 默认 | 适用场景 |
| --- | --- | --- |
| `DISK_MIN_FRESH_GIB` | 65 GiB | 全新 checkout 开始前（`checkout.sh` 默认路径与 `--tip`）。源码 ~31 GiB + `out/Dev` ~8 GiB + 25 GiB 运行保留。可调高，**不得低于 `DISK_MIN_GIB`**（脚本会在加载时直接报错）。 |
| `DISK_MIN_GIB` | 25 GiB | 运行时熔断：`build.sh` 构建前检查 + 构建中每 60s 检查，低于阈值立即终止 ninja；`--resume`/`--repin` 也用它。**没有绕过开关。** |

`preflight.sh` 会按当前是否有 checkout 选择对应门禁：新机器看 65 GiB，已有 checkout 看 25 GiB（并提示新机器需求）。

## 环境变量覆盖

```text
CHROMIUM_ROOT=~/dev/chromium     # checkout 根（其下是 src/ 与 out/Dev）
DEPOT_TOOLS=~/dev/depot_tools
OUT_NAME=Dev                     # 唯一 out 目录名
BUILD_JOBS=6  MAX_JOBS=6
DISK_MIN_GIB=25  DISK_MIN_FRESH_GIB=65  DISK_PATH=/System/Volumes/Data
PROXY_URL=http://127.0.0.1:10808
BROWSER_CLI_LOG_DIR=<repo>/build-logs
```

## 典型流程

```sh
scripts/preflight.sh
scripts/checkout.sh            # 默认固定到 chromium/VERSION；已有 checkout 用 --resume
scripts/configure.sh
scripts/build.sh
scripts/verify-phase1.sh
```

## 只读自检（不下载/不编译/不启动）

```sh
for f in scripts/*.sh scripts/lib/*.sh; do bash -n "$f" || echo "syntax error: $f"; done
scripts/preflight.sh --dry-run
scripts/checkout.sh --dry-run           # 默认路径：显示 revision 固定 + revision-aware gclient sync
scripts/checkout.sh --tip --dry-run     # 显式 tip（不可复现）
scripts/checkout.sh --resume --dry-run
scripts/checkout.sh --repin --dry-run
scripts/configure.sh --dry-run
scripts/build.sh --dry-run
scripts/verify-phase1.sh --dry-run      # 显示 HEAD mismatch 默认 FAIL
scripts/verify-phase1.sh --check-only   # 真实只读检查：args.gn 一致性 + HEAD == VERSION
```
