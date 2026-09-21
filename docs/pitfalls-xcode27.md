# Xcode 27 踩坑与修复

以下两个问题都会让原版 Chromium 构建在 macOS 27 + Xcode 27 上失败；已在本机验证修复。

## 1. `/usr/bin/python3` shim 与 Chromium 的相对 `SDKROOT`

### 现象

```
python3: error: sh -c '/Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild -sdk /…/out/Dev/sdk/xcode_links/MacOSX27.0.sdk -find python3 2> /dev/null' failed with exit code 16384: (null) (errno=No such file or directory)
xcode-select: Failed to locate 'python3', requesting installation of command line developer tools.
```

失败目标是 Rust 链接步骤；`build/toolchain/apple/linker_driver.py` 的 shebang 是 `#!/usr/bin/env python3`。

### 原因

- PATH 中 `python3` 命中 Xcode 27 自带的 Apple Python 3.9 shim；
- Chromium 的 ninja 目标给该进程传的是**相对** `SDKROOT=sdk/xcode_links/MacOSX27.0.sdk`（仅当 cwd 为 `out/Dev` 时有效）；
- shim 内部执行 `xcodebuild -sdk <相对路径> -find python3`，xcodebuild 无法定位该 SDK → 报错退出。

### 修复

构建时让 `python3` 优先解析到 depot_tools 的 hermetic CPython（3.11.8），绕开 shim：

```sh
export PATH="$HOME/dev/depot_tools/python-bin:$PATH"
python3 --version   # Python 3.11.8
```

本仓库所有脚本都通过 `scripts/lib/common.sh` 的 `use_depot_tools_python` 完成这一步，不修改全局 `.zshrc` 或 Git 配置。

## 2. Xcode 27 不再自带 Metal Toolchain（ANGLE 需要）

### 现象

```
FAILED: gen/angle/mtl_internal_shaders_autogen.air
error: error: cannot execute tool 'metal' due to missing Metal Toolchain; use: xcodebuild -downloadComponent MetalToolchain
```

`xcrun -f metal` 指向的只是一个报错 stub；`xcodebuild -showComponent MetalToolchain` 显示 `Status: uninstalled`。

### 修复

```sh
xcodebuild -downloadComponent MetalToolchain   # 约 839 MB，无需 sudo
xcrun --kill-cache                             # 关键：刷新 xcrun 缓存
xcrun metal --version                          # Apple metal version 32023.921 …
xcodebuild -showComponent MetalToolchain       # Status: installed
```

注意：只下载不 `xcrun --kill-cache` 时，`metal` 仍会报同样的错误。

## 3. 其他已知无害现象

- `ld: warning: ignoring -lto_library '…/libLTO.dylib', file does not exist`：每次 SOLINK 出现，`use_lld=false` + `symbol_level=0` 下的噪声，不影响产物。
- 运行期 stderr 的 objc 重复类告警（component build 常见），无害。
- 首次运行 `Chromium --version` 可能 13–30s 无输出：dyld 正在对约 1600 个 component dylib 做一次性签名校验，不是死锁；校验缓存后 warm 约 1.2s。
- component build 必须从 `out/Dev` 内运行（rpath 指向 `out/Dev` 顶层 dylib）。
