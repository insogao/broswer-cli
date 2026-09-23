#!/usr/bin/env bash
# Browser CLI signature acceptance (read-only).
#
# Checks, without launching the app and without any keychain modification
# (only `codesign` and `plutil` are used):
#   1. main CFBundleIdentifier == config/product-identity.env bundle id;
#   2. every nested bundle ID (.app/.framework/.xpc) is derived consistently
#      from the main ID (equal to it, or prefixed with "<main>.");
#   3. codesign --verify --deep --strict succeeds;
#   4. main app and every nested code item (bundles and Mach-O files) carry
#      TeamIdentifier=RAXC5D3A3S and are NOT ad-hoc ("Signature=adhoc");
#   5. the main app's designated requirement is stable (anchor apple generic,
#      identifier, leaf OU == team ID) and not a cdhash-only ad-hoc DR.
#
# Usage: scripts/verify-signature.sh [--app PATH] [--help]
# Exit:  0 = all checks pass; 1 = failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

APP_OVERRIDE=""
usage() {
  cat <<'EOF'
Usage: scripts/verify-signature.sh [options]

Read-only acceptance for a signed Browser CLI product app.

Options:
  --app PATH   verify PATH instead of out/$OUT_NAME/<Product Name>.app
  --help       this message

Never launches the app; never modifies the keychain.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --app) shift; [ $# -gt 0 ] || die "--app needs a value"; APP_OVERRIDE="$1" ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

load_product_identity
APP="${APP_OVERRIDE:-$PRODUCT_APP_DIR}"

FAILS=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }

[ -d "$APP" ] || die "product app not found: $APP (build + sign first)"

# --- 1. main bundle identifier ----------------------------------------------
MAIN_PLIST="$APP/Contents/Info.plist"
[ -f "$MAIN_PLIST" ] || die "missing $MAIN_PLIST"
MAIN_ID="$(plutil -extract CFBundleIdentifier raw -o - "$MAIN_PLIST" 2>/dev/null || true)"
if [ "$MAIN_ID" = "$BROWSER_CLI_BUNDLE_ID" ]; then
  pass "main CFBundleIdentifier=$MAIN_ID"
else
  fail "main CFBundleIdentifier='$MAIN_ID' != '$BROWSER_CLI_BUNDLE_ID'"
fi

# --- 2. nested bundle ID derivation -----------------------------------------
bundle_plist() { # <bundle dir>
  case "$1" in
    *.app) printf '%s/Contents/Info.plist\n' "$1" ;;
    *.xpc) printf '%s/Contents/Info.plist\n' "$1" ;;
    *.framework)
      if [ -f "$1/Resources/Info.plist" ]; then
        printf '%s/Resources/Info.plist\n' "$1"
      else
        find "$1/Versions" -mindepth 2 -maxdepth 2 -name Info.plist 2>/dev/null | head -1
      fi
      ;;
  esac
}

BUNDLES_LIST="$(mktemp)"
find "$APP" -type d \( -name '*.app' -o -name '*.framework' -o -name '*.xpc' \) \
  ! -path "$APP" | sort > "$BUNDLES_LIST"
while IFS= read -r bundle; do
  plist="$(bundle_plist "$bundle")"
  if [ -z "$plist" ] || [ ! -f "$plist" ]; then
    fail "nested bundle has no readable Info.plist: ${bundle#"$APP"/}"
    continue
  fi
  id="$(plutil -extract CFBundleIdentifier raw -o - "$plist" 2>/dev/null || true)"
  case "$id" in
    "$BROWSER_CLI_BUNDLE_ID"|"$BROWSER_CLI_BUNDLE_ID".*)
      pass "nested bundle ID derived: $id (${bundle#"$APP"/})"
      ;;
    *)
      fail "nested bundle ID not derived from '$BROWSER_CLI_BUNDLE_ID': $id (${bundle#"$APP"/})"
      ;;
  esac
done < "$BUNDLES_LIST"

# --- 3. deep strict verification --------------------------------------------
if codesign --verify --deep --strict --verbose=2 "$APP" >/dev/null 2>&1; then
  pass "codesign --verify --deep --strict"
else
  fail "codesign --verify --deep --strict (run manually for details)"
fi

# --- 4. TeamIdentifier + non-adhoc for main, bundles and Mach-O code --------
macho_files() {
  find "$APP" -type f -print | while IFS= read -r f; do
    if file -b "$f" 2>/dev/null | grep -q 'Mach-O'; then
      printf '%s\n' "$f"
    fi
  done
}

check_signed_item() { # <item> <label-kind> <expected-identifier-or-empty>
  local item="$1" kind="$2" expected_id="$3"
  local dv
  dv="$(codesign -dv --verbose=4 "$item" 2>&1 || true)"
  local label="${item#"$APP"/}"
  case "$dv" in
    *"Signature=adhoc"*)
      fail "$kind ad-hoc signature: $label"
      return
      ;;
  esac
  if ! printf '%s\n' "$dv" | grep -q "^TeamIdentifier=$BROWSER_CLI_TEAM_ID$"; then
    fail "$kind TeamIdentifier != $BROWSER_CLI_TEAM_ID: $label"
    return
  fi
  if ! printf '%s\n' "$dv" | grep -q "^Authority=Developer ID Application:"; then
    fail "$kind not signed with a Developer ID Application authority: $label"
    return
  fi
  if [ -n "$expected_id" ]; then
    if ! printf '%s\n' "$dv" | grep -q "^Identifier=$expected_id$"; then
      fail "$kind Identifier mismatch: $label"
      return
    fi
  fi
  pass "$kind signed: $label"
}

check_signed_item "$APP" "app" "$MAIN_ID"
while IFS= read -r bundle; do
  plist="$(bundle_plist "$bundle")"
  id=""
  if [ -n "$plist" ] && [ -f "$plist" ]; then
    id="$(plutil -extract CFBundleIdentifier raw -o - "$plist" 2>/dev/null || true)"
  fi
  check_signed_item "$bundle" "bundle" "$id"
done < "$BUNDLES_LIST"
while IFS= read -r f; do
  check_signed_item "$f" "code" ""
done < <(macho_files)

# --- 4b. component-build dylibs (dev-only; outside the app bundle seal) ------
# Component builds keep the ~500 dylibs next to the .app. They are not sealed
# by the app and are a development-only layout; they are still signed with the
# same identity so that no ad-hoc code is loaded during dev testing. Release
# builds are non-component and bundle everything inside the framework/app.
COMPONENT_LIST="$(mktemp)"
find "$OUT_DIR" -maxdepth 1 -type f -name '*.dylib' 2>/dev/null | sort > "$COMPONENT_LIST"
COMPONENT_TOTAL=0
COMPONENT_OK=0
while IFS= read -r f; do
  COMPONENT_TOTAL=$((COMPONENT_TOTAL + 1))
  dv="$(codesign -dv --verbose=4 "$f" 2>&1 || true)"
  case "$dv" in
    *"Signature=adhoc"*)
      fail "component dylib ad-hoc: $(basename "$f")"
      continue
      ;;
  esac
  if ! printf '%s\n' "$dv" | grep -q "^TeamIdentifier=$BROWSER_CLI_TEAM_ID$"; then
    fail "component dylib TeamIdentifier != $BROWSER_CLI_TEAM_ID: $(basename "$f")"
    continue
  fi
  if ! printf '%s\n' "$dv" | grep -q "^Authority=Developer ID Application:"; then
    fail "component dylib not Developer-ID signed: $(basename "$f")"
    continue
  fi
  if ! codesign --verify --strict "$f" >/dev/null 2>&1; then
    fail "component dylib signature invalid: $(basename "$f")"
    continue
  fi
  COMPONENT_OK=$((COMPONENT_OK + 1))
done < "$COMPONENT_LIST"
rm -f "$COMPONENT_LIST"
if [ "$COMPONENT_TOTAL" -eq 0 ]; then
  printf 'INFO  no component-build dylibs at %s (non-component or not built yet)\n' "$OUT_DIR"
elif [ "$COMPONENT_OK" -eq "$COMPONENT_TOTAL" ]; then
  pass "component-build dylibs signed with team $BROWSER_CLI_TEAM_ID and verified ($COMPONENT_OK/$COMPONENT_TOTAL)"
else
  fail "component-build dylibs: only $COMPONENT_OK/$COMPONENT_TOTAL signed and verified"
fi

# --- 5. designated requirement stability ------------------------------------
DR="$(codesign -d -r- "$APP" 2>&1 || true)"
case "$DR" in
  *'designated =>'*) : ;;
  *) fail "no designated requirement found"; DR="" ;;
esac
if [ -n "$DR" ]; then
  if printf '%s\n' "$DR" | grep -q "identifier \"$MAIN_ID\"" \
     && printf '%s\n' "$DR" | grep -q 'anchor apple generic' \
     && printf '%s\n' "$DR" | grep -Eq "certificate leaf\[subject\.OU\] = \"?$BROWSER_CLI_TEAM_ID\"?"; then
    pass "designated requirement stable (identifier + anchor apple generic + OU $BROWSER_CLI_TEAM_ID)"
  else
    fail "designated requirement is not stable: $(printf '%s' "$DR" | tail -1)"
  fi
fi

rm -f "$BUNDLES_LIST"
echo
if [ "$FAILS" -gt 0 ]; then
  printf 'signature acceptance: FAIL (%d check(s) failed)\n' "$FAILS"
  exit 1
fi
printf 'signature acceptance: PASS (app: %s)\n' "$APP"
