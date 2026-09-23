#!/usr/bin/env bash
# Browser CLI product signing (mandatory post-build step).
#
# Signs, inside-out and with the pinned Developer ID identity from
# config/product-identity.env:
#   1. component-build dylibs at the out dir top level (dev-only layout; they
#      are NOT sealed by the app bundle because component builds keep them
#      outside it -- signing them here keeps the whole dev runtime non-ad-hoc;
#      release builds are non-component and bundle them inside the framework);
#   2. every Mach-O file inside the app (dylibs, helper executables, framework
#      binary, main executable), deepest first;
#   3. every nested code bundle (*.app, *.framework, *.xpc), deepest first;
#   4. the outermost product .app.
#
# Rules:
#   * the identity is selected by SHA-1 (exact match), never by fuzzy name;
#   * ad-hoc signing ("-") is forbidden; no silent fallback;
#   * `--deep` is never used for signing (only acceptance verification uses it);
#   * every `codesign` invocation has a hard stall timeout (default 60s): if it
#     produces no result in that window it is terminated and the whole signing
#     fails. A keychain authorization dialog is expected to manifest as such a
#     stall; do NOT click it, report the failure instead;
#   * performs no keychain modification: the only `security` call is the
#     read-only `security find-identity -v -p codesigning`.
#
# Usage:
#   scripts/sign-browser-cli.sh [--dry-run] [--app PATH] [--help]
#
# Exit codes: 0 = signed/verified (or dry-run plan), 1 = failure,
#             3 = the pinned identity is not available on this machine,
#             124 = a codesign invocation stalled (killed).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

DRY_RUN=0
APP_OVERRIDE=""
SIGN_STALL_TIMEOUT="${SIGN_STALL_TIMEOUT:-60}"

usage() {
  cat <<'EOF'
Usage: scripts/sign-browser-cli.sh [options]

Mandatory inside-out codesign of the Browser CLI product app (and the
component-build dylibs next to it) with the pinned Developer ID identity.

Options:
  --dry-run     verify the identity and print the planned signing order only
                (no codesign execution, no app launch, no keychain access
                beyond the read-only `security find-identity`)
  --app PATH    sign PATH instead of out/$OUT_NAME/<Product Name>.app
                (component dylibs are still taken from out/$OUT_NAME)
  --help        this message

Environment: SIGN_STALL_TIMEOUT (seconds per codesign invocation, default 60).

Exit: 0 signed/verified or dry-run; 1 failure; 3 pinned identity unavailable;
      124 a codesign invocation stalled and was terminated.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --app) shift; [ $# -gt 0 ] || die "--app needs a value"; APP_OVERRIDE="$1" ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

load_product_identity
APP="${APP_OVERRIDE:-$PRODUCT_APP_DIR}"

# --- identity precheck: exact SHA-1 + full name, no fuzzy selection ----------
identity_check() {
  local identities matches count
  identities="$(security find-identity -v -p codesigning 2>/dev/null || true)"
  matches="$(printf '%s\n' "$identities" \
    | grep -F "$BROWSER_CLI_CODESIGN_IDENTITY_SHA1" \
    | grep -F "\"$BROWSER_CLI_CODESIGN_IDENTITY_NAME\"" || true)"
  count="$(printf '%s\n' "$matches" | grep -c . || true)"
  if [ "$count" != "1" ]; then
    warn "pinned codesigning identity not found exactly once:"
    warn "  SHA-1: $BROWSER_CLI_CODESIGN_IDENTITY_SHA1"
    warn "  name:  $BROWSER_CLI_CODESIGN_IDENTITY_NAME"
    warn "  'security find-identity -v -p codesigning' matches: ${count:-0}"
    return 3
  fi
  log "identity OK: $BROWSER_CLI_CODESIGN_IDENTITY_NAME"
  log "identity SHA-1: $BROWSER_CLI_CODESIGN_IDENTITY_SHA1 (team $BROWSER_CLI_TEAM_ID)"
  return 0
}

identity_check || exit 3

list_macho_files() { # <dir>
  find "$1" -type f -print | while IFS= read -r f; do
    if file -b "$f" 2>/dev/null | grep -q 'Mach-O'; then
      printf '%s\n' "$f"
    fi
  done
}

list_nested_bundles() { # <app>  (excludes the outermost app)
  find "$1" -type d \( -name '*.app' -o -name '*.framework' -o -name '*.xpc' \) \
    ! -path "$1"
}

list_component_dylibs() {
  find "$OUT_DIR" -maxdepth 1 -type f -name '*.dylib' 2>/dev/null | sort
}

sort_deepest_first() {
  awk -F/ '{print NF " " $0}' | sort -rn -k1,1 | cut -d' ' -f2-
}

has_entitlements() { # <item>
  codesign -d --entitlements :- "$1" 2>/dev/null | grep -q '<key>'
}

CODESIGN_LOG=""
run_codesign() { # <codesign args...>; 0 ok, 1 failure, 124 stalled
  : > "$CODESIGN_LOG"
  codesign "$@" >"$CODESIGN_LOG" 2>&1 &
  local pid=$!
  local waited=0
  local limit=$((SIGN_STALL_TIMEOUT * 5))  # 0.2s ticks
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$limit" ]; then
      warn "codesign produced no result within ${SIGN_STALL_TIMEOUT}s; terminating pid $pid"
      warn "if a keychain authorization dialog is visible, do NOT click it; report this failure."
      kill -TERM "$pid" 2>/dev/null || true
      sleep 2
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      [ -s "$CODESIGN_LOG" ] && cat "$CODESIGN_LOG" >&2
      return 124
    fi
    sleep 0.2
    waited=$((waited + 1))
  done
  if ! wait "$pid"; then
    [ -s "$CODESIGN_LOG" ] && cat "$CODESIGN_LOG" >&2
    return 1
  fi
  return 0
}

sign_item() { # <item>
  local item="$1"
  local rc=0
  if has_entitlements "$item"; then
    run_codesign --force --timestamp=none --preserve-metadata=entitlements \
      --sign "$BROWSER_CLI_CODESIGN_IDENTITY_SHA1" "$item" || rc=$?
  else
    run_codesign --force --timestamp=none \
      --sign "$BROWSER_CLI_CODESIGN_IDENTITY_SHA1" "$item" || rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    warn "codesign failed (rc=$rc): $item"
    return "$rc"
  fi
  return 0
}

if [ "$DRY_RUN" = "1" ]; then
  log "dry-run: no codesign execution, no app launch, no keychain changes"
  log "would sign app: $APP"
  log "planned inside-out order (deepest first, outer app last):"
  if [ -d "$OUT_DIR" ]; then
    DI=0
    while IFS= read -r item; do
      DI=$((DI + 1))
      printf '  sign component-dylib %s\n' "$(basename "$item")"
    done < <(list_component_dylibs)
    printf '  (%d component-build dylib(s) at %s; dev-only, outside the app seal)\n' "$DI" "$OUT_DIR"
  fi
  if [ -d "$APP" ]; then
    { list_macho_files "$APP" | sort_deepest_first
      list_nested_bundles "$APP" | sort_deepest_first
      printf '%s\n' "$APP"
    } | while IFS= read -r item; do
      printf '  sign %s\n' "${item#"$APP"/}"
    done
  else
    log "app not built yet at $APP; run scripts/build.sh first (target: chrome)"
  fi
  exit 0
fi

[ -d "$APP" ] || die "product app not found: $APP (run scripts/build.sh)"

if [ "$BROWSER_CLI_CODESIGN_IDENTITY_SHA1" = "-" ]; then
  die "refusing ad-hoc identity '-'"
fi

mkdir -p "$LOG_DIR"
MANIFEST="$LOG_DIR/sign-manifest-$(date +%Y%m%d-%H%M%S).txt"
: > "$MANIFEST"
CODESIGN_LOG="$(mktemp)"
trap 'rm -f "$CODESIGN_LOG"' EXIT

log "signing inside-out with SHA-1 $BROWSER_CLI_CODESIGN_IDENTITY_SHA1 (team $BROWSER_CLI_TEAM_ID)"
log "manifest: $MANIFEST"

sign_and_record() { # <kind> <item>
  local kind="$1" item="$2"
  local rc=0
  sign_item "$item" || rc=$?
  if [ "$rc" -eq 0 ]; then
    printf 'ok       %s %s\n' "$kind" "$item" >> "$MANIFEST"
  else
    printf 'failed   %s %s (rc=%s)\n' "$kind" "$item" "$rc" >> "$MANIFEST"
    if [ "$rc" -eq 124 ]; then
      warn "codesign stalled (${SIGN_STALL_TIMEOUT}s) on $kind '$item'; signing aborted (fail closed)"
      exit 124
    fi
    die "codesign failed (rc=$rc) on $kind '$item'; signing aborted (fail closed)"
  fi
}

COMPONENT_COUNT=0
while IFS= read -r item; do
  COMPONENT_COUNT=$((COMPONENT_COUNT + 1))
  log "sign dylib   $(basename "$item")"
  sign_and_record "component-dylib" "$item"
done < <(list_component_dylibs)
log "component-build dylibs signed: $COMPONENT_COUNT (dev-only; not sealed by the app)"

APP_FILE_COUNT=0
while IFS= read -r item; do
  APP_FILE_COUNT=$((APP_FILE_COUNT + 1))
  log "sign file    ${item#"$APP"/}"
  sign_and_record "app-macho" "$item"
done < <(list_macho_files "$APP" | sort_deepest_first)

APP_BUNDLE_COUNT=0
while IFS= read -r item; do
  APP_BUNDLE_COUNT=$((APP_BUNDLE_COUNT + 1))
  log "sign bundle  ${item#"$APP"/}"
  sign_and_record "app-bundle" "$item"
done < <(list_nested_bundles "$APP" | sort_deepest_first)

log "sign app     $(basename "$APP")"
sign_and_record "app" "$APP"

log "light verify (strict, no --deep): $(basename "$APP")"
run_codesign --verify --strict "$APP" || die "post-sign light verification failed: $APP"

log "signing complete: $COMPONENT_COUNT component dylibs, $APP_FILE_COUNT app Mach-O files, $APP_BUNDLE_COUNT nested bundles, 1 app"
log "run scripts/verify-signature.sh for full acceptance"
