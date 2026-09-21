#!/usr/bin/env bash
# Browser CLI shared helpers.
#
# Usage:  . "$(cd "$(dirname "$0")" && pwd)/lib/common.sh"
#
# All locations are overridable via environment variables; no machine-specific
# absolute paths are hardcoded in this repository.
#
# Disk policy (two distinct gates, never interchangeable):
#   DISK_MIN_FRESH_GIB  fresh checkout start gate (default 65 GiB):
#                       source ~31 GiB + out/Dev ~8 GiB + 25 GiB reserve.
#                       May be raised, never lowered below DISK_MIN_GIB.
#   DISK_MIN_GIB        runtime circuit breaker (default 25 GiB):
#                       always enforced before/during builds; no bypass.

set -euo pipefail

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BROWSER_CLI_ROOT="${BROWSER_CLI_ROOT:-$(cd "$COMMON_DIR/../.." && pwd)}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"
CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
CHROMIUM_SRC="$CHROMIUM_ROOT/src"
OUT_NAME="${OUT_NAME:-Dev}"
OUT_DIR="$CHROMIUM_SRC/out/$OUT_NAME"
LOG_DIR="${BROWSER_CLI_LOG_DIR:-$BROWSER_CLI_ROOT/build-logs}"

# Policy defaults (see docs/browser-cli-development-plan.md section 8).
BUILD_JOBS="${BUILD_JOBS:-6}"
MAX_JOBS="${MAX_JOBS:-6}"
DISK_MIN_GIB="${DISK_MIN_GIB:-25}"
DISK_MIN_FRESH_GIB="${DISK_MIN_FRESH_GIB:-65}"
DISK_PATH="${DISK_PATH:-/System/Volumes/Data}"
PROXY_URL="${PROXY_URL:-http://127.0.0.1:10808}"
NO_PROXY_LIST="${NO_PROXY_LIST:-localhost,127.0.0.1}"

NINJA="$CHROMIUM_SRC/third_party/ninja/ninja"
GN="$CHROMIUM_SRC/buildtools/mac/gn"

DRY_RUN="${DRY_RUN:-0}"

log()  { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; exit 1; }

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '[dry-run]'; printf ' %q' "$@"; printf '\n'
    return 0
  fi
  "$@"
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

case "$DISK_MIN_GIB" in
  ''|*[!0-9]*) die "DISK_MIN_GIB must be an integer number of GiB (got '$DISK_MIN_GIB')" ;;
esac
case "$DISK_MIN_FRESH_GIB" in
  ''|*[!0-9]*) die "DISK_MIN_FRESH_GIB must be an integer number of GiB (got '$DISK_MIN_FRESH_GIB')" ;;
esac
if [ "$DISK_MIN_FRESH_GIB" -lt "$DISK_MIN_GIB" ]; then
  die "DISK_MIN_FRESH_GIB ($DISK_MIN_FRESH_GIB) must be >= DISK_MIN_GIB ($DISK_MIN_GIB); the runtime circuit breaker is never bypassed"
fi

disk_avail_gib() {
  local target="$DISK_PATH"
  [ -d "$target" ] || target="/"
  df -k "$target" | awk 'NR==2 {printf "%d", $4 / 1048576}'
}

require_disk() {
  local avail
  avail="$(disk_avail_gib)"
  if [ "$avail" -lt "$DISK_MIN_GIB" ]; then
    die "runtime disk gate: ${avail} GiB available (< ${DISK_MIN_GIB} GiB). Stop and report; do not delete user data."
  fi
  log "runtime disk gate OK: ${avail} GiB available (min ${DISK_MIN_GIB} GiB)"
}

require_fresh_disk() {
  local avail
  avail="$(disk_avail_gib)"
  if [ "$avail" -lt "$DISK_MIN_FRESH_GIB" ]; then
    die "fresh-checkout disk gate: ${avail} GiB available (< ${DISK_MIN_FRESH_GIB} GiB). A fresh checkout needs ~31 GiB source + ~8 GiB out/Dev + ${DISK_MIN_GIB} GiB runtime reserve; do not delete user data."
  fi
  log "fresh-checkout disk gate OK: ${avail} GiB available (min ${DISK_MIN_FRESH_GIB} GiB)"
}

use_depot_tools_python() {
  if [ -x "$DEPOT_TOOLS/python-bin/python3" ]; then
    export PATH="$DEPOT_TOOLS/python-bin:$DEPOT_TOOLS:$PATH"
    log "python3 = $(command -v python3) ($(python3 --version 2>&1))"
  else
    warn "depot_tools python3 not found at $DEPOT_TOOLS/python-bin/python3; PATH unchanged"
  fi
}

# Run a network command with explicit proxy injection (never modifies global
# shell or Git configuration). Every network git/gclient/fetch/curl call in this
# repository must go through this wrapper.
with_proxy() {
  env \
    HTTPS_PROXY="$PROXY_URL" HTTP_PROXY="$PROXY_URL" \
    https_proxy="$PROXY_URL" http_proxy="$PROXY_URL" \
    NO_PROXY="$NO_PROXY_LIST" no_proxy="$NO_PROXY_LIST" \
    "$@"
}

pinned_revision() {
  local version_file="$BROWSER_CLI_ROOT/chromium/VERSION"
  [ -f "$version_file" ] || return 1
  awk -F= '$1 == "revision" {print $2}' "$version_file"
}

checkout_revision() {
  [ -d "$CHROMIUM_SRC/.git" ] || return 1
  git -C "$CHROMIUM_SRC" rev-parse HEAD 2>/dev/null
}

assert_no_concurrent_fetch() {
  local pids
  pids="$(pgrep -f 'fetch --no-history|gclient.py sync|gclient sync' || true)"
  if [ -n "$pids" ]; then
    die "another fetch/gclient sync is running (pids: $pids); never run two syncs on one checkout"
  fi
}

assert_single_out() {
  local out_root="$CHROMIUM_SRC/out"
  [ -d "$out_root" ] || return 0
  local others
  others="$(find "$out_root" -mindepth 1 -maxdepth 1 -type d ! -name "$OUT_NAME" | tr '\n' ' ')"
  if [ -n "$others" ]; then
    die "multiple out dirs found (policy: single $OUT_NAME): $others"
  fi
}
