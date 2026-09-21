#!/usr/bin/env bash
# Browser CLI macOS preflight (read-only).
#
# Checks that a host can reproduce the Phase 1 environment. Never downloads,
# never builds, never launches a browser.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/preflight.sh [--dry-run] [--check-network] [--help]

Read-only checks for a reproducible Browser CLI build host:
  1. macOS on Apple Silicon
  2. xcode-select points at a full Xcode.app (not only Command Line Tools)
  3. xcodebuild -version and macOS SDK
  4. Metal Toolchain installed (Xcode 27 no longer bundles it)
  5. depot_tools + hermetic python3 (python-bin/python3)
  6. Chromium checkout presence (informational on a fresh host) + gn/ninja
  7. disk gates: fresh-checkout start gate (DISK_MIN_FRESH_GIB, default 65 GiB)
     vs runtime circuit breaker (DISK_MIN_GIB, default 25 GiB)
  8. proxy configuration (PROXY_URL) and optional network probe
  9. single checkout / single out policy
 10. no concurrent fetch/gclient sync

Options:
  --dry-run        print the checks without executing them
  --check-network  also probe PROXY_URL reachability (HEAD request, no download)
  --help           this message

Environment overrides: DEPOT_TOOLS, CHROMIUM_ROOT, OUT_NAME, DISK_MIN_GIB,
DISK_MIN_FRESH_GIB, DISK_PATH, PROXY_URL, NO_PROXY_LIST
EOF
}

CHECK_NETWORK=0
for arg in "$@"; do
  case "$arg" in
    --help|-h) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --check-network) CHECK_NETWORK=1 ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done

if [ "$DRY_RUN" = "1" ]; then
  cat <<EOF
[dry-run] preflight would check:
  1. macOS + Apple Silicon arm64
  2. xcode-select points at full Xcode.app
  3. xcodebuild -version and macOS SDK
  4. Metal Toolchain via \`xcrun -f metal\`
  5. depot_tools + python-bin/python3
  6. Chromium checkout presence (info) + gn/ninja presence
  7. disk gates: fresh checkout start gate (min ${DISK_MIN_FRESH_GIB} GiB) or
     runtime circuit breaker (min ${DISK_MIN_GIB} GiB) depending on checkout state;
     currently $(disk_avail_gib) GiB available
  8. proxy: PROXY_URL=${PROXY_URL} (network probe: ${CHECK_NETWORK})
  9. single checkout / single out policy (OUT_NAME=${OUT_NAME})
 10. no concurrent fetch/gclient sync
[dry-run] nothing executed
EOF
  exit 0
fi

FAILURES=0
pass() { log "PASS  $1"; }
fail() { warn "FAIL  $1"; FAILURES=$((FAILURES + 1)); }
info() { log "INFO  $1"; }

log "preflight start (chromium_src=$CHROMIUM_SRC, out=$OUT_NAME)"

if [ "$(uname -s)" = "Darwin" ]; then pass "macOS"; else fail "macOS (uname -s = $(uname -s))"; fi
if [ "$(uname -m)" = "arm64" ]; then pass "Apple Silicon arm64"; else fail "Apple Silicon arm64 (uname -m = $(uname -m))"; fi

DEV_DIR="$(xcode-select -p 2>/dev/null || true)"
case "$DEV_DIR" in
  /Applications/Xcode*.app/Contents/Developer) pass "full Xcode selected ($DEV_DIR)" ;;
  *) fail "xcode-select -p = '${DEV_DIR:-none}' (need full Xcode.app, not Command Line Tools)" ;;
esac

if xcodebuild -version >/dev/null 2>&1; then
  pass "xcodebuild: $(xcodebuild -version 2>/dev/null | tr '\n' ' ')"
else
  fail "xcodebuild -version (open Xcode once and accept the license)"
fi

SDK_VER="$(xcrun --show-sdk-version 2>/dev/null || true)"
if [ -n "$SDK_VER" ]; then pass "macOS SDK $SDK_VER"; else fail "macOS SDK (xcrun --show-sdk-version)"; fi

if xcrun -f metal >/dev/null 2>&1 && xcrun metal --version >/dev/null 2>&1; then
  pass "Metal Toolchain: $(xcrun metal --version 2>&1 | head -1)"
else
  fail "Metal Toolchain missing (run: xcodebuild -downloadComponent MetalToolchain && xcrun --kill-cache)"
fi

if [ -x "$DEPOT_TOOLS/python-bin/python3" ]; then
  pass "depot_tools python3 ($("$DEPOT_TOOLS/python-bin/python3" --version 2>&1))"
else
  fail "depot_tools python3 missing ($DEPOT_TOOLS/python-bin/python3)"
fi
if [ -x "$DEPOT_TOOLS/gclient.py" ] || [ -x "$DEPOT_TOOLS/gclient" ]; then
  pass "depot_tools gclient present"
else
  fail "depot_tools gclient missing under $DEPOT_TOOLS"
fi

if [ "$(command -v python3 2>/dev/null || true)" = "/usr/bin/python3" ]; then
  info "python3 resolves to /usr/bin/python3 (Xcode 27 shim); scripts prepend depot_tools python-bin"
fi

if [ -d "$CHROMIUM_SRC/.git" ]; then
  pass "checkout present ($(checkout_revision 2>/dev/null | cut -c1-12))"
  if [ -x "$GN" ]; then pass "gn present ($("$GN" --version 2>&1 | head -1))"; else fail "gn missing at $GN"; fi
  if [ -x "$NINJA" ]; then pass "ninja present"; else fail "ninja missing at $NINJA"; fi
else
  info "no checkout yet at $CHROMIUM_SRC (expected on a fresh host; run scripts/checkout.sh)"
fi

AVAIL="$(disk_avail_gib)"
if [ -d "$CHROMIUM_SRC/.git" ]; then
  # Existing checkout: the runtime circuit breaker applies to incremental work.
  if [ "$AVAIL" -ge "$DISK_MIN_GIB" ]; then
    pass "runtime disk gate: ${AVAIL} GiB available on ${DISK_PATH} (min ${DISK_MIN_GIB} GiB circuit breaker)"
  else
    fail "runtime disk: ${AVAIL} GiB available on ${DISK_PATH} (< ${DISK_MIN_GIB} GiB)"
  fi
  info "fresh checkout on a new machine would additionally require DISK_MIN_FRESH_GIB=${DISK_MIN_FRESH_GIB} GiB (not needed for this existing checkout)"
else
  # Fresh host: the higher fresh-checkout start gate applies.
  if [ "$AVAIL" -ge "$DISK_MIN_FRESH_GIB" ]; then
    pass "fresh-checkout disk gate: ${AVAIL} GiB available on ${DISK_PATH} (min ${DISK_MIN_FRESH_GIB} GiB)"
  else
    fail "fresh-checkout disk: ${AVAIL} GiB available on ${DISK_PATH} (< ${DISK_MIN_FRESH_GIB} GiB; source ~31 GiB + out/Dev ~8 GiB + ${DISK_MIN_GIB} GiB reserve)"
  fi
  info "runtime circuit breaker stays at ${DISK_MIN_GIB} GiB after checkout"
fi

info "proxy: scripts inject PROXY_URL=${PROXY_URL} (ambient proxy env is ignored by design)"
if [ "$CHECK_NETWORK" = "1" ]; then
  if with_proxy curl -sS -I --max-time 15 https://chromium.googlesource.com/chromium/src.git >/dev/null 2>&1; then
    pass "network: chromium.googlesource.com reachable via proxy"
  else
    fail "network: cannot reach chromium.googlesource.com via $PROXY_URL"
  fi
fi

if pgrep -f 'fetch --no-history|gclient.py sync|gclient sync' >/dev/null 2>&1; then
  fail "another fetch/gclient sync appears to be running"
else
  pass "no concurrent fetch/gclient sync"
fi

if [ -d "$CHROMIUM_SRC/out" ]; then
  OTHERS="$(find "$CHROMIUM_SRC/out" -mindepth 1 -maxdepth 1 -type d ! -name "$OUT_NAME" | tr '\n' ' ')"
  if [ -n "$OTHERS" ]; then
    fail "multiple out dirs: $OTHERS (policy: single $OUT_NAME)"
  else
    pass "single out dir policy (only $OUT_NAME)"
  fi
else
  info "no out dir yet (run scripts/configure.sh)"
fi

if [ "$FAILURES" -gt 0 ]; then
  die "preflight failed with ${FAILURES} problem(s)"
fi
log "preflight OK"
