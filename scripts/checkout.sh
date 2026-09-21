#!/usr/bin/env bash
# Browser CLI checkout: reproducible shallow Chromium checkout pinned to
# chromium/VERSION, with revision-matched dependency sync.
#
# Default (reproducible, pinned):
#   1. fetch --no-history chromium                       # creates .gclient + checkout
#   2. git fetch --depth 1 origin <revision>             # via proxy
#      git checkout --detach <revision>
#   3. gclient sync --no-history --revision src@<rev>    # via proxy; DEPS match the pin
#
# --tip opts out of pinning: it fetches the current upstream tip and is
# explicitly NOT valid for reproducibility acceptance.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/checkout.sh [--dry-run] [--tip] [--resume] [--repin] [--help]

Reproducible shallow Chromium checkout. Default pins the main repo to the
chromium/VERSION revision and then runs a revision-matched dependency sync:

  fetch --no-history chromium
  git fetch --depth 1 origin <revision> && git checkout --detach <revision>
  gclient sync --no-history --revision src@<revision>

Options:
  --dry-run  print the exact commands (pinned by default) without executing
  --tip      fetch the current upstream tip instead of chromium/VERSION.
             Explicit opt-out: NOT reproducible, NOT valid for acceptance.
  --resume   existing checkout: re-run the revision-matched dependency sync
             (gclient sync --no-history --revision src@<revision>)
  --repin    existing tip checkout: fetch + detach the chromium/VERSION
             revision, then run the revision-matched dependency sync
  --help     this message

Disk gates:
  fresh checkout (default/--tip): DISK_MIN_FRESH_GIB (default 65 GiB)
  resume/repin (existing):        DISK_MIN_GIB (default 25 GiB runtime breaker)

Environment overrides: CHROMIUM_ROOT, DEPOT_TOOLS, PROXY_URL, DISK_MIN_GIB,
DISK_MIN_FRESH_GIB, DISK_PATH
EOF
}

TIP=0
RESUME=0
REPIN=0
for arg in "$@"; do
  case "$arg" in
    --help|-h) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --tip) TIP=1 ;;
    --resume) RESUME=1 ;;
    --repin) REPIN=1 ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done

MODES=$((TIP + RESUME + REPIN))
if [ "$MODES" -gt 1 ]; then
  die "--tip, --resume and --repin are mutually exclusive"
fi

REV="$(pinned_revision || true)"
if [ "$TIP" != "1" ] && [ -z "$REV" ]; then
  die "no revision= line in chromium/VERSION; use --tip only if you really want an unpinned tip checkout"
fi

FETCH_BIN="$DEPOT_TOOLS/fetch"
[ -x "$FETCH_BIN" ] || FETCH_BIN="$DEPOT_TOOLS/fetch.py"
GCLIENT_BIN="$DEPOT_TOOLS/gclient"
[ -x "$GCLIENT_BIN" ] || GCLIENT_BIN="$DEPOT_TOOLS/gclient.py"

print_pinned_steps() {
  printf '[dry-run] 1/3 (cd %q && with_proxy %q --no-history chromium)\n' "$CHROMIUM_ROOT" "$FETCH_BIN"
  printf '[dry-run] 2/3 with_proxy git -C %q fetch --depth 1 origin %s && git -C %q checkout --detach %s\n' \
    "$CHROMIUM_SRC" "$REV" "$CHROMIUM_SRC" "$REV"
  printf '[dry-run] 3/3 (cd %q && with_proxy %q sync --no-history --revision src@%s)\n' "$CHROMIUM_ROOT" "$GCLIENT_BIN" "$REV"
}

if [ "$DRY_RUN" = "1" ]; then
  printf '[dry-run] chromium_root=%s src=%s\n' "$CHROMIUM_ROOT" "$CHROMIUM_SRC"
  if [ "$TIP" = "1" ]; then
    printf '[dry-run] TIP MODE (explicit opt-out; NOT reproducible):\n'
    printf '[dry-run] (cd %q && with_proxy %q --no-history chromium)\n' "$CHROMIUM_ROOT" "$FETCH_BIN"
    printf '[dry-run] disk gate: fresh %s GiB (min %s GiB)\n' "$(disk_avail_gib)" "$DISK_MIN_FRESH_GIB"
  elif [ "$RESUME" = "1" ]; then
    printf '[dry-run] RESUME (existing checkout, pinned deps):\n'
    printf '[dry-run] (cd %q && with_proxy %q sync --no-history --revision src@%s)\n' "$CHROMIUM_ROOT" "$GCLIENT_BIN" "$REV"
    printf '[dry-run] disk gate: runtime %s GiB (min %s GiB)\n' "$(disk_avail_gib)" "$DISK_MIN_GIB"
  elif [ "$REPIN" = "1" ]; then
    printf '[dry-run] REPIN (existing checkout -> %s):\n' "$REV"
    printf '[dry-run] with_proxy git -C %q fetch --depth 1 origin %s && git -C %q checkout --detach %s\n' \
      "$CHROMIUM_SRC" "$REV" "$CHROMIUM_SRC" "$REV"
    printf '[dry-run] (cd %q && with_proxy %q sync --no-history --revision src@%s)\n' "$CHROMIUM_ROOT" "$GCLIENT_BIN" "$REV"
    printf '[dry-run] disk gate: runtime %s GiB (min %s GiB)\n' "$(disk_avail_gib)" "$DISK_MIN_GIB"
  else
    printf '[dry-run] DEFAULT (pinned to chromium/VERSION revision %s):\n' "$REV"
    print_pinned_steps
    printf '[dry-run] disk gate: fresh %s GiB (min %s GiB); proxy=%s\n' "$(disk_avail_gib)" "$DISK_MIN_FRESH_GIB" "$PROXY_URL"
  fi
  printf '[dry-run] nothing executed\n'
  exit 0
fi

[ -f "$FETCH_BIN" ] || die "depot_tools fetch not found under $DEPOT_TOOLS"
[ -f "$GCLIENT_BIN" ] || die "depot_tools gclient not found under $DEPOT_TOOLS"
export PATH="$DEPOT_TOOLS:$PATH"
assert_no_concurrent_fetch

checkout_exists_nonempty() {
  [ -d "$CHROMIUM_SRC" ] && [ -n "$(ls -A "$CHROMIUM_SRC" 2>/dev/null)" ]
}

if [ "$TIP" = "1" ]; then
  warn "TIP MODE: fetching upstream tip; NOT reproducible and NOT valid for acceptance"
  require_fresh_disk
  if checkout_exists_nonempty; then
    die "checkout already exists at $CHROMIUM_SRC; use --repin (pin it) or --resume (re-sync pinned deps)"
  fi
  log "fetching upstream tip (--no-history) into $CHROMIUM_ROOT"
  run mkdir -p "$CHROMIUM_ROOT"
  ( cd "$CHROMIUM_ROOT" && run with_proxy "$FETCH_BIN" --no-history chromium )
  log "tip checkout revision: $(checkout_revision)"
  warn "NOT reproducible. To pin it: scripts/checkout.sh --repin"
  exit 0
fi

if [ "$RESUME" = "1" ] || [ "$REPIN" = "1" ]; then
  require_disk
  [ -d "$CHROMIUM_SRC/.git" ] || die "--resume/--repin need an existing checkout at $CHROMIUM_SRC"
  [ -f "$CHROMIUM_ROOT/.gclient" ] || die "--resume/--repin need $CHROMIUM_ROOT/.gclient (created by scripts/checkout.sh)"
  if [ "$REPIN" = "1" ]; then
    log "pinning existing checkout to $REV"
    run with_proxy git -C "$CHROMIUM_SRC" fetch --depth 1 origin "$REV"
    run git -C "$CHROMIUM_SRC" checkout --detach "$REV"
  fi
  log "revision-matched dependency sync: gclient sync --no-history --revision src@$REV"
  ( cd "$CHROMIUM_ROOT" && run with_proxy "$GCLIENT_BIN" sync --no-history --revision "src@$REV" )
  log "checkout revision: $(checkout_revision)"
  log "next: scripts/configure.sh"
  exit 0
fi

require_fresh_disk
if checkout_exists_nonempty; then
  die "checkout already exists at $CHROMIUM_SRC; use --repin (pin it) or --resume (re-sync pinned deps)"
fi

log "1/3 fetch --no-history chromium (creates .gclient and the single checkout)"
run mkdir -p "$CHROMIUM_ROOT"
( cd "$CHROMIUM_ROOT" && run with_proxy "$FETCH_BIN" --no-history chromium )

log "2/3 pin main repo to chromium/VERSION revision $REV"
run with_proxy git -C "$CHROMIUM_SRC" fetch --depth 1 origin "$REV"
run git -C "$CHROMIUM_SRC" checkout --detach "$REV"

log "3/3 revision-matched dependency sync: gclient sync --no-history --revision src@$REV"
( cd "$CHROMIUM_ROOT" && run with_proxy "$GCLIENT_BIN" sync --no-history --revision "src@$REV" )

log "checkout revision: $(checkout_revision)"
if [ "$(checkout_revision)" = "$REV" ]; then
  log "PASS  checkout pinned to chromium/VERSION revision"
else
  die "checkout revision does not match chromium/VERSION ($REV)"
fi
log "next: scripts/configure.sh"
