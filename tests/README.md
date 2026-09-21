# tests

当前包含**脚本回归测试**；Gate A/B/C 的浏览器行为测试仍属后续阶段。

## `run-script-tests.sh`

轻量、离线、可随时运行：不联网、不编译、不启动浏览器/UI。

```sh
tests/run-script-tests.sh     # 0 = 全部通过；1 = 有失败
```

覆盖内容（37 项）：

| 组 | 内容 |
| --- | --- |
| 1 | 所有脚本 `bash -n` |
| 2 | 5 个入口 `--help` rc=0 |
| 3 | 5 个入口 `--dry-run`（在 `mktemp -d` 模拟新机器上）rc=0 |
| 4 | `checkout.sh` dry-run 显示 pinned revision、`checkout --detach <rev>`、`with_proxy` 与 `gclient sync --revision src@<rev>` |
| 5 | `verify-phase1.sh` dry-run 明示 “HEAD mismatch is FAIL by default” |
| 6 | **磁盘硬下限负例**：`DISK_MIN_GIB=24`、`DISK_MIN_GIB=0 DISK_MIN_FRESH_GIB=0`、非整数、`DISK_MIN_FRESH_GIB < DISK_MIN_GIB` 全部 rc!=0；25/65、30/65、80/120 合法 |
| 7 | `build.sh --no-watchdog` 被拒绝，help 不再宣传绕过 |
| 8 | **`checkout.sh --resume` 一致性负例**：伪造 tip checkout（HEAD != VERSION）必须在任何依赖同步之前 rc!=0 并提示 `--repin` |
| 9 | **`verify-phase1.sh` 严格 HEAD 负例**：伪造 tip checkout 上 `--check-only` 必须 FAIL（`reproducibility FAIL`）；`--allow-tip` 作为显式 opt-out 可通过 |
| 10 | 真实 checkout 只读检查：`verify-phase1.sh --check-only`（无 checkout 时自动 SKIP） |
| 11 | 静态审计：所有 `fetch`/`gclient sync`/`git fetch`/`curl` 调用必须经 `with_proxy` |

新增脚本或安全门禁时，请在此套件中补充对应负例。

## 边界

- 不在此目录实现产品功能，不引入第二套窗口可见性逻辑。
- 后续 Gate A/B/C 测试必须能判定「全程零可见」；禁止把 watchdog 事后隐藏当作成功（设计 §5.1、§14）。
