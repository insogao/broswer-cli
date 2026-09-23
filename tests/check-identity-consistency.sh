#!/usr/bin/env bash
# Static consistency gate: the single versioned identity source
# (config/product-identity.env) must agree with the Chromium-side branding
# configuration:
#   * chromium/args.gn selects chrome/app/theme/browser_cli/BRANDING;
#   * that BRANDING file carries the same product name / bundle id / team id.
#
# Prevents identity drift between scripts, GN args and the Chromium patch.
# No keychain access, no network, no browser launch.
#
# Usage: tests/check-identity-consistency.sh [--root DIR] [--chromium-src DIR] [--help]
# Exit:  0 = consistent (or Chromium checkout/branding absent -> SKIP),
#        1 = mismatch, 2 = usage error.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SELF_DIR/.." && pwd)"
CHROMIUM_SRC="${CHROMIUM_SRC:-$HOME/dev/chromium/src}"
BRANDING_REL="chrome/app/theme/browser_cli/BRANDING"

usage() {
  cat <<'EOF'
Usage: tests/check-identity-consistency.sh [options]

Compares config/product-identity.env with chromium/args.gn and the Chromium
checkout's browser_cli BRANDING file.

Options:
  --root DIR          repository root (default: this repo)
  --chromium-src DIR  Chromium src dir (default: $HOME/dev/chromium/src)
  --help              this message
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --root) shift; [ $# -gt 0 ] || { echo "check-identity-consistency: --root needs a value" >&2; exit 2; }; ROOT="$(cd "$1" && pwd)" ;;
    --chromium-src) shift; [ $# -gt 0 ] || { echo "check-identity-consistency: --chromium-src needs a value" >&2; exit 2; }; CHROMIUM_SRC="$1" ;;
    *) echo "check-identity-consistency: unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

CONFIG="$ROOT/config/product-identity.env"
[ -f "$CONFIG" ] || { echo "FAIL  missing identity config: $CONFIG" >&2; exit 1; }
# shellcheck disable=SC1090
. "$CONFIG"

FAILS=0
ok()   { printf 'OK    %s\n' "$*"; }
bad()  { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }

: "${BROWSER_CLI_PRODUCT_NAME:-}"
: "${BROWSER_CLI_BUNDLE_ID:-}"
: "${BROWSER_CLI_TEAM_ID:-}"
: "${BROWSER_CLI_CODESIGN_IDENTITY_SHA1:-}"
: "${BROWSER_CLI_CODESIGN_IDENTITY_NAME:-}"
if [ -z "$BROWSER_CLI_PRODUCT_NAME" ] || [ -z "$BROWSER_CLI_BUNDLE_ID" ] \
   || [ -z "$BROWSER_CLI_TEAM_ID" ] || [ -z "$BROWSER_CLI_CODESIGN_IDENTITY_SHA1" ] \
   || [ -z "$BROWSER_CLI_CODESIGN_IDENTITY_NAME" ]; then
  bad "identity config has empty required values"
fi
case "$BROWSER_CLI_CODESIGN_IDENTITY_SHA1" in
  [0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]) ok "identity SHA-1 format" ;;
  *) bad "identity SHA-1 is not 40 uppercase hex chars: '$BROWSER_CLI_CODESIGN_IDENTITY_SHA1'" ;;
esac
case "$BROWSER_CLI_CODESIGN_IDENTITY_NAME" in
  *"($BROWSER_CLI_TEAM_ID)") ok "identity name contains team ID ($BROWSER_CLI_TEAM_ID)" ;;
  *) bad "identity name does not contain '($BROWSER_CLI_TEAM_ID)'" ;;
esac

# args.gn must select the browser_cli branding file.
ARGS_GN="$ROOT/chromium/args.gn"
EXPECTED_SELECTOR="branding_file_path = \"//$BRANDING_REL\""
if [ -f "$ARGS_GN" ] && grep -qF "$EXPECTED_SELECTOR" "$ARGS_GN"; then
  ok "chromium/args.gn selects //$BRANDING_REL"
elif [ -f "$ARGS_GN" ]; then
  bad "chromium/args.gn does not select //$BRANDING_REL (expected: $EXPECTED_SELECTOR)"
else
  bad "missing $ARGS_GN"
fi

# args.gn must select the generated icon assets directory.
EXPECTED_ICON_SELECTOR='mac_app_icon_dir = "app/theme/browser_cli/mac"'
if [ -f "$ARGS_GN" ] && grep -qF "$EXPECTED_ICON_SELECTOR" "$ARGS_GN"; then
  ok "chromium/args.gn selects the Browser CLI icon dir"
elif [ -f "$ARGS_GN" ]; then
  bad "chromium/args.gn does not select the Browser CLI icon dir (expected: $EXPECTED_ICON_SELECTOR)"
fi

# The checkout's branding file, when present, must match the identity config.
BRANDING="$CHROMIUM_SRC/$BRANDING_REL"
if [ ! -f "$BRANDING" ]; then
  printf 'SKIP  branding file not present at %s (checkout not patched; apply chromium/patches/002-*.patch)\n' "$BRANDING"
else
  branding_value() { awk -F= -v k="$1" '$1 == k {sub(/^[^=]*=/, ""); print}' "$BRANDING"; }
  b_name="$(branding_value PRODUCT_FULLNAME)"
  b_id="$(branding_value MAC_BUNDLE_ID)"
  b_team="$(branding_value MAC_TEAM_ID)"
  [ "$b_name" = "$BROWSER_CLI_PRODUCT_NAME" ] && ok "BRANDING PRODUCT_FULLNAME=$b_name" \
    || bad "BRANDING PRODUCT_FULLNAME='$b_name' != '$BROWSER_CLI_PRODUCT_NAME'"
  [ "$b_id" = "$BROWSER_CLI_BUNDLE_ID" ] && ok "BRANDING MAC_BUNDLE_ID=$b_id" \
    || bad "BRANDING MAC_BUNDLE_ID='$b_id' != '$BROWSER_CLI_BUNDLE_ID'"
  [ "$b_team" = "$BROWSER_CLI_TEAM_ID" ] && ok "BRANDING MAC_TEAM_ID=$b_team" \
    || bad "BRANDING MAC_TEAM_ID='$b_team' != '$BROWSER_CLI_TEAM_ID'"
fi

# Generated icon assets (reproducible from the approved source; optional until
# scripts/generate-icons.sh has run).
if [ -f "$CHROMIUM_SRC/chrome/app/theme/browser_cli/mac/app.icns" ] \
   && [ -f "$CHROMIUM_SRC/chrome/app/theme/browser_cli/mac/Assets.car" ]; then
  ok "generated icon assets present (app.icns + Assets.car)"
else
  printf 'SKIP  icon assets not generated yet; run scripts/generate-icons.sh\n'
fi

echo
if [ "$FAILS" -gt 0 ]; then
  printf 'identity consistency: FAIL (%d mismatch(es))\n' "$FAILS"
  exit 1
fi
printf 'identity consistency: OK\n'
