#!/usr/bin/env bash
# Browser CLI build: ninja -C out/Dev -j 6 chrome with a 25 GiB disk watchdog.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/build.sh [options]

Runs `ninja -C out/Dev -j 6 chrome` with:
  - a pre-build disk gate (DISK_MIN_GIB, default 25 GiB)
  - an always-on runtime watchdog that terminates ninja if free space drops
    below the gate (there is no bypass option)
  - depot_tools python3 first on PATH (Xcode 27 shim workaround)

Options:
  --dry-run         print the ninja command without running it
  --target TARGET   ninja target (default: chrome)
  --jobs N          parallel jobs (default 6; >6 requires --allow-more-jobs)
  --allow-more-jobs allow --jobs above MAX_JOBS (default 6)
  --keep-going      pass -k 0 (keep going after failures)
  --help            this message

Environment overrides: CHROMIUM_ROOT, OUT_NAME, BUILD_JOBS, MAX_JOBS,
DISK_MIN_GIB (hard floor 25 GiB, may only be raised), DISK_PATH,
BROWSER_CLI_LOG_DIR
EOF
}

TARGET="chrome"
JOBS="$BUILD_JOBS"
KEEP_GOING=0
ALLOW_MORE_JOBS=0

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --target) shift; [ $# -gt 0 ] || die "--target needs a value"; TARGET="$1" ;;
    --jobs) shift; [ $# -gt 0 ] || die "--jobs needs a value"; JOBS="$1" ;;
    --allow-more-jobs) ALLOW_MORE_JOBS=1 ;;
    --keep-going) KEEP_GOING=1 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

case "$JOBS" in
  ''|*[!0-9]*) die "--jobs must be a positive integer (got '$JOBS')" ;;
esac
if [ "$JOBS" -gt "$MAX_JOBS" ] && [ "$ALLOW_MORE_JOBS" != "1" ]; then
  die "--jobs $JOBS exceeds MAX_JOBS=$MAX_JOBS (16 GiB RAM host); use --allow-more-jobs to override"
fi

NINJA_ARGS=(-C "out/$OUT_NAME" -j "$JOBS")
if [ "$KEEP_GOING" = "1" ]; then
  NINJA_ARGS+=(-k 0)
fi

if [ "$DRY_RUN" = "1" ]; then
  printf '[dry-run] (cd %q && %q' "$CHROMIUM_SRC" "$NINJA"
  printf ' %q' "${NINJA_ARGS[@]}"
  printf ' %q)\n' "$TARGET"
  printf '[dry-run] log dir: %s ; watchdog: always-on (min %s GiB); disk_now=%s GiB\n' \
    "$LOG_DIR" "$DISK_MIN_GIB" "$(disk_avail_gib)"
  printf '[dry-run] nothing executed\n'
  exit 0
fi

require_disk

[ -x "$NINJA" ] || die "ninja not found at $NINJA (run scripts/checkout.sh)"
[ -f "$OUT_DIR/build.ninja" ] || die "out/$OUT_NAME/build.ninja missing (run scripts/configure.sh)"

use_depot_tools_python

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/build-$(date +%Y%m%d-%H%M%S).log"

cd "$CHROMIUM_SRC"
log "build start: ninja ${NINJA_ARGS[*]} $TARGET"
log "log file: $LOG"

"$NINJA" "${NINJA_ARGS[@]}" "$TARGET" >"$LOG" 2>&1 &
NINJA_PID=$!
log "ninja pid=$NINJA_PID (runtime watchdog always on, min ${DISK_MIN_GIB} GiB)"

AVAIL="$(disk_avail_gib)"
WATCHDOG_STOPPED=0
while kill -0 "$NINJA_PID" 2>/dev/null; do
  sleep 60
  AVAIL="$(disk_avail_gib)"
  if [ "$AVAIL" -lt "$DISK_MIN_GIB" ]; then
    warn "disk watchdog: ${AVAIL} GiB < ${DISK_MIN_GIB} GiB; terminating ninja"
    pkill -TERM -P "$NINJA_PID" 2>/dev/null || true
    kill -TERM "$NINJA_PID" 2>/dev/null || true
    sleep 15
    pkill -KILL -P "$NINJA_PID" 2>/dev/null || true
    kill -KILL "$NINJA_PID" 2>/dev/null || true
    WATCHDOG_STOPPED=1
    break
  fi
done

RC=0
wait "$NINJA_PID" || RC=$?

if [ "$WATCHDOG_STOPPED" = "1" ]; then
  die "build stopped by disk watchdog at ${AVAIL} GiB (ninja rc=$RC); log: $LOG"
fi
if [ "$RC" -ne 0 ]; then
  warn "ninja failed rc=$RC; last lines of $LOG:"
  tail -5 "$LOG" >&2 || true
  exit "$RC"
fi

log "build OK: ninja ${NINJA_ARGS[*]} $TARGET"
tail -1 "$LOG" 2>/dev/null || true
log "next: scripts/verify-phase1.sh"
