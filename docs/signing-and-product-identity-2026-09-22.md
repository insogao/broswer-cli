# Browser CLI 稳定签名与产品身份（2026-09-22）

本文件记录 Phase 2 后续的稳定签名/产品身份配置：单一配置源、Chromium branding patch、
inside-out 签名脚本、验收脚本与构建流程集成。

**本轮（2026-09-22）已完成实际构建 + 实际 Developer ID 签名 + 只读验收 + 一次通过签名 App 的
Gate A smoke；未提交 Git commit，等待主负责人复核。** 实测数据见 §8。

## 1. 单一版本化配置源

`config/product-identity.env`（唯一来源，禁止在别处复制这些值）：

| 键 | 值 |
| --- | --- |
| `BROWSER_CLI_PRODUCT_NAME` | `Browser CLI` |
| `BROWSER_CLI_BUNDLE_ID` | `com.jiangao.browsercli` |
| `BROWSER_CLI_TEAM_ID` | `RAXC5D3A3S` |
| `BROWSER_CLI_CODESIGN_IDENTITY_SHA1` | `E4F416DE4CD44E214B61053F2542512A0B33AF52` |
| `BROWSER_CLI_CODESIGN_IDENTITY_NAME` | `Developer ID Application: jian gao (RAXC5D3A3S)` |

`tests/check-identity-consistency.sh` 静态校验配置与 `chromium/args.gn`、Chromium checkout 中
`chrome/app/theme/browser_cli/BRANDING` 完全一致（不一致即 FAIL）。

## 2. 证书精确匹配（只读）

```text
$ security find-identity -v -p codesigning
  1) E4F416DE4CD44E214B61053F2542512A0B33AF52 "Developer ID Application: jian gao (RAXC5D3A3S)"
     1 valid identities found
```

- 只允许这一个只读 `security` 调用；**没有** import/delete/export/修改/吊销，未读取私钥内容。
- 签名时 `codesign --sign` 使用 **SHA-1**，不做名称模糊匹配；脚本要求
  `find-identity` 输出中恰好一条同时匹配 SHA-1 与完整名称，否则失败（exit 3，不签名）。

## 3. Chromium branding patch（`002-browser-cli-branding.patch`）

- 新增 `chrome/app/theme/browser_cli/BRANDING`（产品名/公司名/Bundle ID/Team ID）。
- `chromium/args.gn` 选择该文件：`branding_file_path = "//chrome/app/theme/browser_cli/BRANDING"`。
- 上游 `chrome/app/theme/chromium/BRANDING` 与任何 Google/Chromium 资产**未被修改**。
- 本轮**未接入 Logo**（纯资源配置也不做，避免额外风险；assets/branding 保持原状）。

派生规则（Chromium 原生逻辑，随主 ID 自动派生）：

| 产物 | Bundle ID |
| --- | --- |
| 主 App `Browser CLI.app` | `com.jiangao.browsercli` |
| Framework `Browser CLI Framework.framework` | `com.jiangao.browsercli.framework` |
| 默认/GPU/Aperitif Helper | `com.jiangao.browsercli.helper` |
| Renderer Helper | `com.jiangao.browsercli.helper.renderer` |
| Alert Helpers | `com.jiangao.browsercli.framework.AlertNotificationService` |

`gn gen` 静态验证（未编译）：

```text
//out/Dev/Browser CLI.app/...
//out/Dev/Browser CLI Helper (Renderer).app/Contents/MacOS/Browser CLI Helper (Renderer)
//out/Dev/Browser CLI Framework.framework/Versions/156.0.8067.0/Browser CLI Framework
```

注意：产品改名后必须用新 `args.gn` 重新构建；旧的 `out/Dev/Chromium.app`（ad-hoc）属于
过期产物，不得用于测试或验收。

## 4. 签名脚本 `scripts/sign-browser-cli.sh`

- 先做身份精确预检，再按 **inside-out** 顺序签名：
  1. `out/Dev` 顶层的 component-build dylib（开发布局；**不在 App seal 内**，见 §4.1）；
  2. App 内所有 Mach-O 文件（dylib、XPC/helper 可执行文件、Framework 主二进制、主可执行文件），
     路径由深到浅；
  3. 所有嵌套 bundle（`*.app` / `*.framework` / `*.xpc`），由深到浅；
  4. 最外层 `Browser CLI.app`。
- `codesign --force --timestamp=none`（开发构建不做安全时间戳；正式发布再加），
  有既有 entitlements 时 `--preserve-metadata=entitlements`。
- **禁止** ad-hoc `-`；**禁止**静默 fallback：任一条 codesign 失败即整体失败。
- **不把 `--deep` 当作签名手段**（只枚举并逐个签）；结尾仅做 `codesign --verify --strict` 轻校验。
- **60 秒停滞熔断（强制）**：每次 codesign 调用都有 `SIGN_STALL_TIMEOUT`（默认 60s）看门狗；
  超时立即终止该 codesign 并整体失败（exit 124）。钥匙串授权对话框会表现为停滞——
  **不要去点它**，按 fail closed 报告。脚本不等待任何用户交互。
- 每次运行写 `build-logs/sign-manifest-<时间>.txt`（逐项 `ok/failed` 清单）。
- `--dry-run`：只做身份预检并打印计划顺序（不执行 codesign、不启动 App、不访问钥匙串）。
- exit code：`0` 成功/dry-run；`1` 失败；`3` 本机缺少固定身份；`124` codesign 停滞被终止。

### 4.1 component-build 边界（明确）

- component build 把 ~500 个 dylib 放在 `out/Dev` 顶层，不在 `.app` 内，因此**不会被 App 的
  seal 覆盖**，`codesign --verify --deep --strict` 也检查不到它们；
- 开发期处理：签名脚本统一用同一 Developer ID 身份签这些顶层 dylib，验收脚本逐个校验
  `TeamIdentifier=RAXC5D3A3S`、非 ad-hoc 且 `codesign --verify --strict` 通过（聚合报告数量），
  保证开发运行时没有 ad-hoc 代码；
- **正式发布**使用非 component 构建，dylib 全部封装进 Framework/App，由第 2/3 步 inside-out
  签署，不再依赖这条开发期路径；
- 签名顺序保证不会“先签外层再签内层”破坏 seal：所有内层文件 → 嵌套 bundle → 最外层 App。

## 5. 验收脚本 `scripts/verify-signature.sh`

只读，不启动 App，不访问钥匙串（仅 `codesign` / `plutil`）：

1. 主 App `CFBundleIdentifier == com.jiangao.browsercli`；
2. 所有嵌套 bundle ID 派生一致（等于主 ID 或以 `com.jiangao.browsercli.` 开头）；
3. `codesign --verify --deep --strict` 成功；
4. 主 App 与每个嵌套 bundle/ Mach-O 代码：`TeamIdentifier=RAXC5D3A3S`、
   Authority 为 Developer ID Application、且**非** `Signature=adhoc`；
5. designated requirement 稳定：包含 `identifier "com.jiangao.browsercli"`、
   `anchor apple generic`、`certificate leaf[subject.OU] = "RAXC5D3A3S"`，且不是 cdhash-only ad-hoc DR；
6. 顶层 component-build dylib（开发布局）：逐个 Team ID/非 ad-hoc/`codesign --verify --strict`，
   聚合报告 `ok/total`。

## 5.1 产品图标（Logo）接入

- 已批准的源图 `assets/branding/browser-cli-logo-source.png`（1254×1254 PNG，带 alpha，**从不修改**）
  由 `scripts/generate-icons.sh` 生成：
  - `chrome/app/theme/browser_cli/mac/app.icns`（16/32/64/128/256/512/1024 全套，`iconutil`）；
  - `chrome/app/theme/browser_cli/mac/Assets.car`（`Assets.xcassets/AppIcon.appiconset`，
    经 `xcrun actool` 编译）。
- `chromium/args.gn` 的 `mac_app_icon_dir = "app/theme/browser_cli/mac"` 通过 patch 002 新增的
  GN 参数选择该目录；`chrome/BUILD.gn` 的 `chrome_app_icon` / `chrome_asset_catalog` 因此使用
  同一套图标，主 App 与 Alert Helper 共用；`CFBundleIconFile=app.icns`、`CFBundleIconName=AppIcon`
  由 App 的 `Contents/Resources` 提供，Dock/启动台/菜单栏使用同一图标。
- `scripts/configure.sh` 在 `gn gen` 之前自动运行 `generate-icons.sh`（可单独运行，支持 `--dry-run`）；
  生成产物不进入 patch（二进制、可复现生成），源图 sha256 在生成前后校验一致。
- 本轮未生成 macOS 26 Icon Composer（`.icon`）资产；`app.icns` + `Assets.car` 已覆盖 Dock/启动台
  所需尺寸。

## 6. 构建流程集成（强制签名）

- `scripts/build.sh`：`chrome` 构建成功后依次运行 `sign-browser-cli.sh` 与
  `verify-signature.sh`；任一失败 → 整体构建失败。**没有跳过签名的开关**。
  非 `chrome` 目标（单元测试二进制）不产出产品 App，跳过签名。
- `scripts/verify-phase1.sh`：第 1 步拒绝 ad-hoc App；所有增量构建结束后以第 5 步
  `sign + verify-signature` 收尾；smoke 继续强制 `--use-mock-keychain` + 临时 profile。
- `tests/phase2-background-presentation-smoke.sh`：启动前用 `codesign -dv` 断言 Team ID
  且拒绝 ad-hoc；不改测试卫生规则。

## 7. 测试卫生（不变）

所有会运行 Chromium 的测试仍强制 `--use-mock-keychain` + `mktemp -d` 的
`--user-data-dir`，由 `tests/check-browser-launch-hygiene.sh` 静态门禁保证；签名/验收
脚本本身不启动 App、不产生窗口/系统对话框，也不修改钥匙串。

## 8. 本轮实测结果（2026-09-22）

### 8.1 configure + 图标

- `scripts/generate-icons.sh`：源图 sha256 前后一致
  `4db942d1…951d`；生成 `app.icns`（1,579,090 B，sha256 `287cd14a…bb8cb`）与
  `Assets.car`（1,690,440 B，sha256 `2f6a1981…55e8`）。
- `scripts/configure.sh --force`：13 个 args 行，`gn gen` 成功；`gn desc` 静态确认
  `//chrome:chrome_app_icon` / `//chrome:chrome_asset_catalog` 指向
  `//chrome/app/theme/browser_cli/mac/*`，主 App/Helper/Framework 名称均为 `Browser CLI*`。

### 8.2 构建与签名

| 项目 | 数据 |
| --- | --- |
| 编译阶段 | `ninja chrome`：708 edges，11:22:06 → 约 11:26:06（约 4 分钟，`-j 6`） |
| 首次签名 | 11:26:06 → 11:35:54（约 9m48s，含 1s 轮询开销） |
| 首次验收 | 11:36:10 失败于 DR 校验误报（`subject.OU` 实际不带引号），仅验收脚本缺陷 |
| 修复后干净构建 | `scripts/build.sh` 11:37:39 → 11:43:13（**5m34s**：12 个 COPY/relink 边 + 签名 + 验收全 PASS） |
| 签名清单 | `build-logs/sign-manifest-20260922-112606.txt`：548 项全 `ok`（522 component dylib、16 App Mach-O、9 嵌套 bundle、1 App） |
| 实际身份 | `E4F416DE4CD44E214B61053F2542512A0B33AF52 "Developer ID Application: jian gao (RAXC5D3A3S)"` |
| 停滞/对话框 | **无**：全部 codesign 静默完成，无钥匙串授权对话框、无用户交互（60s 熔断未触发） |
| 退出状态 | `build.sh` 最终 `product signed and verified`，无 ad-hoc 回退 |

### 8.3 只读验收（证据：`build-logs/signature-evidence-20260922.txt`）

- 主 App：`out/Dev/Browser CLI.app`，`CFBundleIdentifier=com.jiangao.browsercli`，
  `CFBundleIconFile=app.icns`，`CFBundleIconName=AppIcon`；
- 签名：`Authority=Developer ID Application: jian gao (RAXC5D3A3S)`、
  `TeamIdentifier=RAXC5D3A3S`、非 ad-hoc；
- 嵌套派生 ID：`.framework` / `.helper` / `.helper.renderer` /
  `.framework.AlertNotificationService` 全部以主 ID 为前缀；
- `codesign --verify --deep --strict`：`valid on disk` + `satisfies its Designated Requirement`；
- DR：`identifier "com.jiangao.browsercli" and anchor apple generic ... certificate leaf[subject.OU] = RAXC5D3A3S`；
- component dylib：522/522 Team ID + 非 ad-hoc + `--verify --strict` 通过；
- 图标进入 App：`Contents/Resources/{app.icns,Assets.car}` 的 sha256 与 checkout 生成物完全一致。

### 8.4 Gate A smoke（签名后的 App）

- 第 1 次 `--run`：180s 超时。原因是签名后首启需重新校验约 1600 个二进制的签名（冷启动），
  **不是产品缺陷、也没有任何弹窗**；随后 `--version` 冷启动 21s 正常退出。
- 第 2 次（脚本默认超时提高到 600s，并新增僵尸/提前退出检测后）：**全部 7 项 PASS** ——
  `desktop-onscreen-windows-zero=0`、`cmdline-has-mock-keychain`、
  `cmdline-has-isolated-user-data-dir`、`renderer-js-title`、
  `host-suppressed-at-creation`、`policy-denied-internal-show`、
  `no-internal-allow-in-background`。
- 证据：`build-logs/phase2-smoke-signed-20260922.log`；
  `build-logs/phase2-smoke-signed-20260922-run.txt`。
- 未安装/覆盖 `/Applications` 任何内容，仅验证 `out/Dev/Browser CLI.app`。

## 9. 尚需主负责人复核 / 后续

1. 冷启动签名校验成本：签名后首次启动可长达数分钟（本机实测 >180s，缓存后 ~21s）；
   是否需要为后续启动路径预热或文档化等待时间。
2. component-build 开发期签名 522 个顶层 dylib（约增加 2–10 分钟构建时间）是否接受；
   正式发布构建（非 component）将不依赖此路径。
3. 是否接受 `--timestamp=none`（开发期）、“非 chrome 目标不签名”的边界，以及第 1 次 smoke
   超时后补跑一次（第二次）的处理方式。
4. **未做 Hardened Runtime / notarization / staple**：Phase 9 正式发布才加入。
5. 跨机器：身份固定为上述开发证书；换机需安装该身份或修改配置源（并重新跑一致性门禁）。
