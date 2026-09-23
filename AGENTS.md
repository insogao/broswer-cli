# AGENTS.md — Browser CLI 仓库强制规则

面向所有后续 AI / 自动化贡献者。以下规则优先于个人偏好；违反即视为**阻塞缺陷**，必须 fail closed、立即停止并报告，不得继续“先跑起来再说”。

## 1. 测试卫生（强制，2026-09-22 起）

1. 任何自动化 / smoke / 测试启动 Chromium 或 Backlight 时，macOS 必须**同时**显式使用：
   - `--use-mock-keychain`
   - `--user-data-dir="$(mktemp -d ...)"`（独立临时目录；**禁止**复用用户真实 profile）
2. **禁止**测试读取、写入、删除或修改用户真实钥匙串（login keychain、`Chromium Safe Storage` 等任何条目）。
3. **禁止**要求用户点击“允许”、输入密码或处理任何系统对话框。一旦出现一次弹窗/系统提示，立即停止、判定 FAIL 并向用户报告，不得继续。
4. 不得使用 `open` / Dock / Launchpad 启动测试；不得为了测试打开可见窗口；不得用“先显示再隐藏”作为通过条件。
5. **生产 Browser CLI 不得使用 `--use-mock-keychain`**：正式版依靠稳定代码签名 + 稳定安装路径获得钥匙串 ACL（Phase 9），mock keychain 仅限测试。
6. 任何会启动浏览器的脚本改动，必须先跑静态门禁：
   `tests/check-browser-launch-hygiene.sh` 与 `tests/run-script-tests.sh`；
   通过后如需运行 smoke，最多运行一次已隔离 smoke（见 `tests/README.md`）。

## 2. 根因（为什么必须这样）

开发构建是 ad-hoc / linker-signed，代码签名不稳定，macOS 无法把进程与既有钥匙串 ACL 匹配。不带 `--use-mock-keychain` 时，Chromium 的 OSCrypt 会访问真实 login keychain 并弹出 “Chromium Safe Storage” 密码框；这对编译/测试毫无意义，只会打断开发者。

历史事实：旧测试曾从随机路径 `/private/var/.../T/bl-wstate-*/apps/Backlight.app` 访问真实钥匙串（钥匙串 ACL 中留有证据）。这种做法已被永久禁止；任何人不得继续沿用、也不得去操作那些既有钥匙串项。

## 3. 产品签名（强制，2026-09-22 起）

1. 产品/签名身份的**唯一来源**是 `config/product-identity.env`；禁止在脚本、文档、args.gn 或
   Chromium 补丁里复制/改写这些值（`tests/check-identity-consistency.sh` 会校验一致性）。
2. 固定值：产品名 `Browser CLI`、Bundle ID `com.jiangao.browsercli`、Team ID `RAXC5D3A3S`、
   Developer ID Application 证书 SHA-1 `E4F416DE4CD44E214B61053F2542512A0B33AF52`。
   签名必须按 **SHA-1 精确选择**，禁止按名称模糊选择。
3. **禁止 ad-hoc 签名（`-`）与静默 fallback**：`scripts/build.sh` 对 `chrome` 目标强制
   `scripts/sign-browser-cli.sh` + `scripts/verify-signature.sh`，任一失败即整体失败。
4. 不得保留或宣称 ad-hoc 构建可供测试；测试入口（smoke / verify-phase1）在启动 App 前必须
   断言 Developer ID + Team ID 并拒绝 ad-hoc。
5. 不得创建、导入、删除、导出、修改或吊销任何钥匙串证书；不得读取私钥内容；不得运行
   `security import/delete-*`。对钥匙串只允许只读的 `security find-identity -v -p codesigning`，
   以及对代码调用 `codesign`。
6. 签名与验证脚本不得启动 App，不得产生窗口或系统对话框。每次 `codesign` 调用必须有硬停滞
   熔断（默认 60s）；一旦停滞/疑似钥匙串授权对话框，立即终止并 fail closed 报告，
   **不得去点击对话框**、不得要求用户交互。
7. 产品图标只能由 `scripts/generate-icons.sh` 从已批准源图
   `assets/branding/browser-cli-logo-source.png` 生成；源图不得修改，生成步骤必须可复现。
8. 本轮不做 Hardened Runtime / notarization / staple，使用 `--timestamp=none`；这些只在 Phase 9
   正式发布加入（见 `docs/signing-and-product-identity-2026-09-22.md`）。

## 4. 其他硬性约束

- 不修改 Backlight / BrowserPilot 产品代码，不修改其它 worktree（`parallel-browser` 等）。
- Chromium 修改只以 `chromium/patches/` 中的编号 patch 形式入库；禁止把 Chromium 源码或大文件复制进本仓库。
- 测试不得把 watchdog / 事后隐藏 / 最小化补救当作“全程零可见”的成功（设计 §5.1、§14）。
- 不得为了通过测试而让窗口短暂上屏、移到屏幕外或弹出任何权限对话框。
- 除非用户明确要求，不要创建 Git commit / push。
