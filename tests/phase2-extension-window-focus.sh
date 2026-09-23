#!/usr/bin/env bash
# Browser CLI Gate A evidence: Extension window focus requests under BACKGROUND.
#
# Covers two adjacent PROJECT-PROGRESS B items in ONE isolated run:
#   * "Extension chrome.windows.create({focused:true})：创建成功但 focus 被拒绝"
#   * "Extension windows.update({focused:true})：focus 被拒绝"
#
# The signed app is launched with ONLY this test's unpacked extension loaded
# (`--disable-extensions` + `--disable-extensions-except=<dir>` +
# `--load-extension=<dir>`), then the extension's own test page uses the real
# Extension API:
#   __bcliRunWindowFocusTest(url)
#     -> chrome.windows.create({url, focused: true})  (createResult)
#     -> chrome.windows.update(id, {focused: true})   (updateResult)
# and the harness additionally proves over CDP that the created
# window/WebContents exists (target + Browser.getWindowForTarget).
#
# Authoritative OS evidence (macOS native focus/activation is denied):
#   * CGWindowList on-screen window samples for the browser PID stay 0;
#   * policy logs: managed-mode suppress at creation, deny show-and-activate,
#     and no internal allow while BACKGROUND.
# The extension API's focusedFlag and DOM hasFocus/visibilityState are recorded
# as INFORMATIONAL model state only.
#
# Safety / hygiene (mandatory, see AGENTS.md): default is dry-run; `--run`
# performs exactly ONE isolated invocation; never uses open/Dock/Launchpad;
# never shows/hides/reveals a window; never touches the real keychain; only the
# test extension is loaded.
#
# Usage: tests/phase2-extension-window-focus.sh [--run] [--keep-tmp]
#          [--timeout SECONDS] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'EXTENSION-FOCUS-CHECK: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

CDP_HARNESS="$SCRIPT_DIR/tools/extension-window-focus-cdp-check.py"
PAGE_SERVER="$SCRIPT_DIR/tools/page-test-server.py"
EXTENSION_DIR="$SCRIPT_DIR/tools/extensions/browser-cli-window-focus"

RUN=0
KEEP_TMP=0
TIMEOUT_SECONDS="${EXTENSION_FOCUS_CHECK_TIMEOUT_SECONDS:-600}"

usage() {
  cat <<'EOF'
Usage: tests/phase2-extension-window-focus.sh [options]

Gate A evidence for two Extension items: windows.create({focused:true}) must
create a managed background window (native focus denied) and
windows.update({focused:true}) must not grant native focus. Default is dry-run;
--run performs ONE isolated invocation.

Options:
  --run             actually launch the signed browser (no visible window)
  --keep-tmp        keep the temp dir (profile/logs) for inspection
  --timeout SECONDS per-CDP-command timeout (default 600; the first command
                    waits for the cold-started/re-signed app)
  --help            this message

Requires a Developer-ID-signed out/Dev/Browser CLI.app. Uses the shared CDP
pipe launcher (--remote-debugging-pipe, --use-mock-keychain, fresh mktemp
profile) and loads only the test unpacked extension.
EOF
}

die() { printf 'EXTENSION-FOCUS-CHECK: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[extension-focus-check] %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --run) RUN=1 ;;
    --keep-tmp) KEEP_TMP=1 ;;
    --timeout) shift; [ $# -gt 0 ] || die "--timeout needs a value"; TIMEOUT_SECONDS="$1" ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

[ -d "$EXTENSION_DIR" ] || die "missing extension fixture: $EXTENSION_DIR"
[ -f "$EXTENSION_DIR/manifest.json" ] || die "missing extension manifest: $EXTENSION_DIR/manifest.json"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-extfocus.XXXXXX")"
PROFILE_DIR="$TMP_DIR/profile"
LOG="$TMP_DIR/browser.log"
RESULTS="$TMP_DIR/cdp-results.json"
SERVER_LOG="$TMP_DIR/page-server.log"
SERVER_PORT_FILE="$TMP_DIR/page-server.port"
CHECK_LOG="$TMP_DIR/check-results.txt"
HELPER_SRC="$SCRIPT_DIR/tools/onscreen-window-count.m"
HELPER="$TMP_DIR/onscreen-window-count"
: > "$CHECK_LOG"
: > "$SERVER_LOG"

case "$PROFILE_DIR" in
  "$TMP_DIR"/*) : ;;
  *) die "refusing to run: profile dir '$PROFILE_DIR' is not inside '$TMP_DIR'" ;;
esac

if [ "$RUN" != "1" ]; then
  cat <<EOF
[dry-run] product identity: '$BROWSER_CLI_PRODUCT_NAME' (team $BROWSER_CLI_TEAM_ID)
[dry-run] app path: $APP_DIR (must be Developer-ID signed)
[dry-run] extension fixture: $EXTENSION_DIR (only this unpacked extension is loaded)
[dry-run] one isolated launch through the shared CDP pipe launcher
          (--remote-debugging-pipe), with --use-mock-keychain and a fresh
          --user-data-dir=<mktemp -d>; the launcher deliberately does NOT add
          --disable-extensions here; only-except/load select the fixture:
          --disable-extensions-except=<fixture> --load-extension=<fixture>
[dry-run] extension id: deterministic unpacked id verified by loading its
          test.html first; only on failure a bounded (<=10s) service-worker
          discovery window is used (MV3 workers may not stay resident)
[dry-run] steps: load chrome-extension://<id>/test.html and evaluate
          __bcliRunWindowFocusTest(url) with awaitPromise:
            chrome.windows.create({url, focused:true}) -> createResult
            chrome.windows.update(id, {focused:true})  -> updateResult
          then independently prove the created target/WebContents over CDP
          (Target.getTargets + Browser.getWindowForTarget)
[dry-run] authoritative OS assertions: PID on-screen window samples stay 0,
          policy suppress + deny show-and-activate, no internal allow in BACKGROUND;
          extension focusedFlag and DOM focus/visibility are informational only
[dry-run] nothing executed; pass --run to execute (one isolated invocation).
EOF
  rm -rf "$TMP_DIR"
  exit 0
fi

[ -x "$BIN" ] || die "browser binary not found/executable: $BIN (set CHROMIUM_ROOT/OUT_NAME)"
[ -d "$APP_DIR" ] || die "product app not found: $APP_DIR"
[ -f "$CDP_HARNESS" ] || die "missing CDP harness: $CDP_HARNESS"

# No ad-hoc build may be launched or tested (read-only codesign query).
APP_SIG_OK=0
APP_SIG="$(codesign -dv --verbose=4 "$APP_DIR" 2>&1 || true)"
case "$APP_SIG" in
  *"TeamIdentifier=$BROWSER_CLI_TEAM_ID"*) APP_SIG_OK=1; note "app signature OK: Developer ID (team $BROWSER_CLI_TEAM_ID)" ;;
  *"Signature=adhoc"*) die "refusing to test an ad-hoc build; the app must be Developer-ID signed" ;;
  *) die "refusing to test: $APP_DIR is not signed with the pinned Developer ID identity" ;;
esac

pick_python() {
  if [ -x "$DEPOT_TOOLS/python-bin/python3" ]; then
    printf '%s\n' "$DEPOT_TOOLS/python-bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    die "python3 not found (needed for the CDP harness)"
  fi
}
PYTHON="$(pick_python)"
PYTHON_REAL="$("$PYTHON" -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
if [ -n "$PYTHON_REAL" ] && [ -x "$PYTHON_REAL" ]; then
  PYTHON="$PYTHON_REAL"
fi

note "compiling on-screen window counter"
xcrun clang -fobjc-arc -framework Foundation -framework CoreGraphics \
  -o "$HELPER" "$HELPER_SRC"
[ -x "$HELPER" ] || die "failed to compile window counter"

note "starting local page server"
"$PYTHON" "$PAGE_SERVER" "$SERVER_PORT_FILE" "$SERVER_LOG" >/dev/null 2>&1 &
SERVER_PID=$!
SERVER_WAITED=0
while [ ! -s "$SERVER_PORT_FILE" ]; do
  kill -0 "$SERVER_PID" 2>/dev/null || die "page server exited early"
  [ "$SERVER_WAITED" -lt 50 ] || die "page server did not report a port"
  sleep 0.2
  SERVER_WAITED=$((SERVER_WAITED + 1))
done
SERVER_PORT="$(head -1 "$SERVER_PORT_FILE" | tr -d '[:space:]')"
note "page server port=$SERVER_PORT"

cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    pkill -TERM -P "$SERVER_PID" 2>/dev/null || true
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    local i=0
    while kill -0 "$SERVER_PID" 2>/dev/null && [ "$i" -lt 20 ]; do
      sleep 0.2
      i=$((i + 1))
    done
    pkill -KILL -P "$SERVER_PID" 2>/dev/null || true
    kill -KILL "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$KEEP_TMP" = "1" ]; then
    note "keeping temp dir: $TMP_DIR"
  else
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

note "running extension window-focus CDP harness (one isolated launch)"
HARNESS_RC=0
PYTHONPATH="$SCRIPT_DIR/tools" "$PYTHON" "$CDP_HARNESS" \
  --app "$BIN" \
  --helper "$HELPER" \
  --server-url "http://127.0.0.1:$SERVER_PORT" \
  --extension-dir "$EXTENSION_DIR" \
  --profile "$PROFILE_DIR" \
  --log "$LOG" \
  --result-json "$RESULTS" \
  --timeout "$TIMEOUT_SECONDS" || HARNESS_RC=$?

[ -f "$RESULTS" ] || die "CDP harness produced no results (rc=$HARNESS_RC)"

eval "$("$PYTHON" - "$RESULTS" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))

def get(*path, default=""):
    cur = data
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur

def sh(value):
    return "'" + str(value).replace("'", "'\\''") + "'"

create = get("api", "createResult") or {}
update = get("api", "updateResult") or {}
xcheck = get("window_id_cross_check") or {}
created = get("created_target") or {}

create_ok = isinstance(create, dict) and create.get("ok") is True
create_window_id = create.get("windowId") if isinstance(create, dict) else None
create_tab_ids = create.get("tabIds") if isinstance(create, dict) else None
update_ok = isinstance(update, dict) and update.get("ok") is True
update_error = update.get("error", "") if isinstance(update, dict) else ""

update_recorded = "yes" if (
    (update_ok and update.get("windowId") is not None)
    or (not update_ok and bool(update_error))
) else "no"
update_targets_created = "yes" if (
    update_ok
    and update.get("windowId") is not None
    and str(update.get("windowId")) == str(create_window_id)
) or (not update_ok and bool(update_error)) else "no"

print("EXT_ID=" + sh(get("extension", "id")))
print("EXT_ID_SOURCE=" + sh(get("extension", "id_source")))
print("EXT_ID_VERIFIED=" + sh(get("extension", "id_verified")))
print("EXT_DISCOVERY_SECONDS=" + sh(get("extension", "sw_discovery_seconds")))
print("EXT_SW_URL=" + sh(get("extension", "sw_target", "url")))
print("EXT_PAGE_ID=" + sh(get("ext_page", "targetId")))
print("EXT_PAGE_TITLE=" + sh(get("ext_page", "inspect", "title")))
print("EXT_PAGE_BODY=" + sh(get("ext_page", "inspect", "body_marker")))
print("EXT_PAGE_FOCUS=" + sh(get("ext_page", "inspect", "has_focus")))
print("EXT_PAGE_VIS=" + sh(get("ext_page", "inspect", "visibility_state")))
print("CREATE_OK=" + sh("True" if create_ok else "False"))
print("CREATE_WINDOW_ID=" + sh("" if create_window_id is None else create_window_id))
print("CREATE_TAB_IDS=" + sh(",".join(str(t) for t in (create_tab_ids or []))))
print("CREATE_FOCUSED_FLAG=" + sh(create.get("focusedFlag") if isinstance(create, dict) else ""))
print("UPDATE_RECORDED=" + sh(update_recorded))
print("UPDATE_TARGETS_CREATED=" + sh(update_targets_created))
print("UPDATE_ERROR=" + sh(update_error))
print("CREATED_TARGET_ID=" + sh(created.get("targetId", "")))
print("CREATED_TARGET_TYPE=" + sh(created.get("type", "")))
print("CREATED_TARGET_URL=" + sh(created.get("url", "")))
print("CREATED_WINDOW_ID=" + sh(get("created_window", "windowId")))
print("XCHECK_MATCH=" + sh(xcheck.get("match")))
print("SAMPLES_MAX=" + sh(get("window_samples", "max")))
print("CMDLINE=" + sh(get("cmdline")))
print("ERRORS=" + sh(",".join(data.get("errors", []))))
print("EXIT_CODE=" + sh(get("exit_code")))
PY
)"

FAILED=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then
    printf 'extension-focus.check name=%s result=PASS detail=%s\n' "$1" "$3" | tee -a "$CHECK_LOG"
  else
    printf 'extension-focus.check name=%s result=FAIL expected=%s actual=%s\n' "$1" "$2" "$3" | tee -a "$CHECK_LOG"
    FAILED=1
  fi
}
info() { printf 'extension-focus.info %s\n' "$*" | tee -a "$CHECK_LOG"; }

check "app-developer-id-signed" "1" "$APP_SIG_OK"
check "cmdline-has-mock-keychain" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "cmdline-has-isolated-user-data-dir" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -qF -- "--user-data-dir=$PROFILE_DIR"; then echo yes; else echo no; fi)"
check "cmdline-loads-only-this-extension" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -qF -- "--load-extension=$EXTENSION_DIR" \
        && printf '%s' "$CMDLINE" | grep -qF -- "--disable-extensions-except=$EXTENSION_DIR" \
        && ! printf '%s' "$CMDLINE" | grep -Eq -- '--disable-extensions([^-]|$)'; then echo yes; else echo no; fi)"
check "harness-no-fatal-errors" "" "$ERRORS"
check "extension-id-resolved" "yes" "$([ -n "$EXT_ID" ] && echo yes || echo no)"
check "extension-id-verified-by-load" "True" "$EXT_ID_VERIFIED"
check "extension-test-page-loaded" "yes" \
  "$([ "$EXT_PAGE_TITLE" = "BCLI_EXT_PAGE" ] && [ "$EXT_PAGE_BODY" = "BCLI_EXT_READY" ] && echo yes || echo no)"
check "windows-create-succeeded" "yes" \
  "$([ "$CREATE_OK" = "True" ] && [ -n "$CREATE_WINDOW_ID" ] && echo yes || echo no)"
check "windows-create-returned-tab-ids" "yes" \
  "$([ -n "$CREATE_TAB_IDS" ] && echo yes || echo no)"
check "created-target-exists" "yes" \
  "$([ -n "$CREATED_TARGET_ID" ] && [ "$CREATED_TARGET_TYPE" = "page" ] && echo yes || echo no)"
check "created-target-is-child-page" "yes" \
  "$(if printf '%s' "$CREATED_TARGET_URL" | grep -qF "/page?name=ext_child"; then echo yes; else echo no; fi)"
check "windows-update-recorded" "yes" "$UPDATE_RECORDED"
check "windows-update-targets-created-window" "yes" "$UPDATE_TARGETS_CREATED"
check "desktop-windows-always-zero" "0" "$SAMPLES_MAX"
check "policy-host-suppress" "yes" \
  "$(if grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$LOG" 2>/dev/null; then echo yes; else echo no; fi)"
check "policy-denied-show-and-activate" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*action=show-and-activate' "$LOG" 2>/dev/null; then echo yes; else echo no; fi)"
check "no-internal-allow-in-background" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$LOG" 2>/dev/null; then echo no; else echo yes; fi)"

# Informational only: extension model state and DOM state, not OS focus evidence.
info "extension-id-source=$EXT_ID_SOURCE verified=$EXT_ID_VERIFIED sw-discovery-seconds=$EXT_DISCOVERY_SECONDS sw-target=${EXT_SW_URL:-none}"
info "create windowId=$CREATE_WINDOW_ID focusedFlag=$CREATE_FOCUSED_FLAG tabIds=$CREATE_TAB_IDS"
info "update recorded=$UPDATE_RECORDED error='$UPDATE_ERROR'"
info "window-id-cross-check extension=$CREATE_WINDOW_ID cdp=$CREATED_WINDOW_ID match=$XCHECK_MATCH"
info "dom-focus-informational ext-page=$EXT_PAGE_FOCUS visibility=$EXT_PAGE_VIS"
info "os-focus-authority on-screen-window-max=$SAMPLES_MAX; policy deny show-and-activate + no internal allow"

# ---------------------------------------------------------------- evidence
EVIDENCE_DIR="$REPO_ROOT/build-logs/phase2-extension-window-focus-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$EVIDENCE_DIR"
cp "$CHECK_LOG" "$EVIDENCE_DIR/check-results.txt" 2>/dev/null || true
cp "$RESULTS" "$EVIDENCE_DIR/cdp-results.json" 2>/dev/null || true
cp "$LOG" "$EVIDENCE_DIR/browser.log" 2>/dev/null || true
cp "$SERVER_LOG" "$EVIDENCE_DIR/page-server.log" 2>/dev/null || true
{
  echo "profile=$PROFILE_DIR (throwaway mktemp; removed unless --keep-tmp)"
  echo "extension_dir=$EXTENSION_DIR"
  echo "server_port=$SERVER_PORT"
  echo "harness_rc=$HARNESS_RC"
  echo "cmdline=$CMDLINE"
  echo "extension id=$EXT_ID source=$EXT_ID_SOURCE"
  echo "create ok=$CREATE_OK windowId=$CREATE_WINDOW_ID tabIds=$CREATE_TAB_IDS focusedFlag=$CREATE_FOCUSED_FLAG"
  echo "update recorded=$UPDATE_RECORDED targets_created_window=$UPDATE_TARGETS_CREATED error='$UPDATE_ERROR'"
  echo "created target=$CREATED_TARGET_ID type=$CREATED_TARGET_TYPE url=$CREATED_TARGET_URL cdp_window=$CREATED_WINDOW_ID"
  echo "window_samples_max=$SAMPLES_MAX"
  echo "policy_decisions:"
  grep 'BROWSER_CLI_PRESENTATION' "$LOG" 2>/dev/null || true
} > "$EVIDENCE_DIR/summary.txt"
shasum -a 256 "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/sha256.txt" 2>/dev/null || true

if [ "$FAILED" = "1" ] || [ "$HARNESS_RC" -ne 0 ]; then
  printf 'EXTENSION-FOCUS-CHECK: FAIL (evidence kept: %s)\n' "$EVIDENCE_DIR"
  KEEP_TMP=1
  exit 1
fi

printf 'EXTENSION-FOCUS-CHECK: PASS (evidence: %s)\n' "$EVIDENCE_DIR"
