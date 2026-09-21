#!/usr/bin/env bash
# Browser CLI checkout: shallow-fetch upstream Chromium exactly once.
#
# - uses `--no-history` (no full Git cache)
# - injects the local proxy explicitly
# - refuses to run a second fetch against an existing checkout
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/checkout.sh [--dry-run] [--resume] [--pin] [--help]

Shallow-fetch Chromium (--no-history) into CHROMIUM_ROOT (default ~/dev/chromium)
through the local proxy. Creates exactly one checkout.

Options:
  --dry-run  print commands without running them
  --resume   run `gclient sync --no-history` in an existing checkout instead of fetch
  --pin      after fetching, pin the checkout to the revision in chromium/VERSION
             (git fetch --depth 1 origin <revision> && git checkout --detach <revision>)
  --help     this message

Environment overrides: CHROMIUM_ROOT, DEPOT_TOOLS, PROXY_URL, DISK_MIN_GIB, DISK_PATH
EOF
}

RESUME=0
PIN=0
for arg in "$@"; do
  case "$arg" in
    --help|-h) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --resume) RESUME=1 ;;
    --pin) PIN=1 ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done

if [ "$DRY_RUN" = "1" ]; then
  printf '[dry-run] chromium_root=%s src=%s\n' "$CHROMIUM_ROOT" "$CHROMIUM_SRC"
  if [ "$RESUME" = "1" ]; then
    printf '[dry-run] (cd %q && with_proxy %q sync --no-history)\n' "$CHROMIUM_ROOT" "$DEPOT_TOOLS/gclient.py"
  else
    printf '[dry-run] (cd %q && with_proxy %q --no-history chromium)\n' "$CHROMIUM_ROOT" "$DEPOT_TOOLS/fetch.py"
  fi
  if [ "$PIN" = "1" ]; then
    printf '[dry-run] pin to %s (git fetch --depth 1 + git checkout --detach)\n' "$(pinned_revision || echo '<chromium/VERSION revision>')"
  fi
  printf '[dry-run] proxy=%s disk_now=%s GiB (min %s GiB)\n' "$PROXY_URL" "$(disk_avail_gib)" "$DISK_MIN_GIB"
  printf '[dry-run] nothing executed\n'
  exit 0
fi

require_disk
assert_no_concurrent_fetch

FETCH_BIN="$DEPOT_TOOLS/fetch"
[ -x "$FETCH_BIN" ] || FETCH_BIN="$DEPOT_TOOLS/fetch.py"
[ -f "$FETCH_BIN" ] || die "depot_tools fetch not found under $DEPOT_TOOLS"
GCLIENT_BIN="$DEPOT_TOOLS/gclient"
[ -x "$GCLIENT_BIN" ] || GCLIENT_BIN="$DEPOT_TOOLS/gclient.py"
[ -f "$GCLIENT_BIN" ] || die "depot_tools gclient not found under $DEPOT_TOOLS"
export PATH="$DEPOT_TOOLS:$PATH"

if [ "$RESUME" = "1" ]; then
  [ -d "$CHROMIUM_SRC" ] || die "--resume needs an existing checkout at $CHROMIUM_SRC"
  log "resuming: gclient sync --no-history in $CHROMIUM_ROOT"
  ( cd "$CHROMIUM_ROOT" && run with_proxy "$GCLIENT_BIN" sync --no-history )
else
  if [ -d "$CHROMIUM_SRC" ] && [ -n "$(ls -A "$CHROMIUM_SRC" 2>/dev/null)" ]; then
    die "checkout already exists at $CHROMIUM_SRC; use --resume (never run a second fetch)"
  fi
  log "fetching Chromium (--no-history) into $CHROMIUM_ROOT"
  run mkdir -p "$CHROMIUM_ROOT"
  ( cd "$CHROMIUM_ROOT" && run with_proxy "$FETCH_BIN" --no-history chromium )
fi

if [ "$PIN" = "1" ]; then
  REV="$(pinned_revision || true)"
  [ -n "$REV" ] || die "--pin requested but no revision= line in chromium/VERSION"
  log "pinning checkout to $REV"
  run git -C "$CHROMIUM_SRC" fetch --depth 1 origin "$REV"
  run git -C "$CHROMIUM_SRC" checkout --detach "$REV"
fi

log "checkout revision: $(checkout_revision)"
log "pinned revision (chromium/VERSION): $(pinned_revision || echo n/a)"
log "next: scripts/configure.sh"
