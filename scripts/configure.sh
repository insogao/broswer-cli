#!/usr/bin/env bash
# Browser CLI GN configure: materialize the single out/Dev from chromium/args.gn.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: scripts/configure.sh [--dry-run] [--force] [--help]

Regenerates the macOS app icon assets from the approved source artwork
(scripts/generate-icons.sh), copies chromium/args.gn to out/Dev/args.gn and
runs `gn gen out/Dev`.

Options:
  --dry-run  print commands without running them
  --force    overwrite out/Dev/args.gn if it differs from chromium/args.gn
  --help     this message

Policy: exactly one out dir (OUT_NAME, default Dev). Refuses to configure when
other out dirs exist. Environment overrides: CHROMIUM_ROOT, OUT_NAME, DEPOT_TOOLS
EOF
}

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --help|-h) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    *) die "unknown argument: $arg (see --help)" ;;
  esac
done

ARGS_SRC="$BROWSER_CLI_ROOT/chromium/args.gn"
[ -f "$ARGS_SRC" ] || die "missing $ARGS_SRC"

if [ "$DRY_RUN" = "1" ]; then
  printf '[dry-run] icons: %s --dry-run (regenerates chrome/app/theme/browser_cli/mac/{app.icns,Assets.car})\n' "$SCRIPT_DIR/generate-icons.sh"
  printf '[dry-run] args: %s -> %s/args.gn (force=%s)\n' "$ARGS_SRC" "$OUT_DIR" "$FORCE"
  printf '[dry-run] (cd %q && %q gen %q)\n' "$CHROMIUM_SRC" "$GN" "out/$OUT_NAME"
  printf '[dry-run] disk_now=%s GiB (min %s GiB); nothing executed\n' "$(disk_avail_gib)" "$DISK_MIN_GIB"
  exit 0
fi

require_disk
assert_single_out

if [ ! -x "$GN" ]; then
  die "gn not found at $GN (run scripts/checkout.sh first)"
fi

# The branding patch selects chrome/app/theme/browser_cli/mac/{app.icns,Assets.car};
# generate them from the approved source before gn gen checks for their existence.
"$SCRIPT_DIR/generate-icons.sh"

if [ -f "$OUT_DIR/args.gn" ] && ! diff -q "$ARGS_SRC" "$OUT_DIR/args.gn" >/dev/null 2>&1; then
  if [ "$FORCE" != "1" ]; then
    die "out/$OUT_NAME/args.gn differs from chromium/args.gn; review then re-run with --force"
  fi
  warn "overwriting out/$OUT_NAME/args.gn (--force)"
fi

use_depot_tools_python

run mkdir -p "$OUT_DIR"
run cp "$ARGS_SRC" "$OUT_DIR/args.gn"

log "args.gn:"
sed 's/^/  /' "$ARGS_SRC"

( cd "$CHROMIUM_SRC" && run "$GN" gen "out/$OUT_NAME" )

log "GN configure OK ($(grep -c . "$OUT_DIR/args.gn") args lines)"
log "next: scripts/build.sh"
