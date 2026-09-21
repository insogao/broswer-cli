# chromium/patches

当前为空占位。按设计 §10，Browser CLI 对 Chromium 的修改必须保持窄、小、清晰、可重放。

- 本目录目前**不得**出现产品 patch：Phase 2 之前不存在任何 Chromium 修改。
- Phase 2 起按编号递增：
  `001-managed-window-mode.patch`、`002-foreground-origin.patch`、`003-user-explicit.patch`、
  `004-foreground-lease.patch`、`005-background-compositor.patch`、`006-background-capture.patch`、
  `007-popup-policy.patch`。
- patch 必须相对 `chromium/VERSION` 记录的 revision 生成（`git diff` / `git format-patch`），
  并能用 `git apply` 顺序重放；升级 Chromium 时只允许少数 patch 冲突。
- 禁止把 Chromium 源码或大文件复制进本仓库。
