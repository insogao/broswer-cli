#!/usr/bin/env bash
# Browser CLI Gate A evidence: AI-created normal Tab vs independent BrowserWindow.
#
# Covers two PROJECT-PROGRESS B items in ONE isolated run:
#   * "AI 创建普通 Tab：后台成功且不聚焦"
#   * "AI 创建新 Window：内部存在且桌面不可见"
#
# The run (design; executed only when the shared Chromium/verification window
# allows it) launches the signed app once with `--remote-debugging-pipe`,
# `--use-mock-keychain` and a fresh mktemp profile, then:
#   1. Target.createTarget({newWindow:true})  -> independent BrowserWindow W1
#   2. Target.createTarget({newWindow:false}) -> Tab that must join W1
#      (Browser.getWindowForTarget windowId equality)
#   3. Target.createTarget({newWindow:true})  -> independent BrowserWindow W2
#      (windowId differs from W1)
#   4. Runtime.evaluate per target: real HTTP page title/body and location.href.
#      document.hasFocus()/visibilityState are recorded as INFORMATIONAL evidence
#      only: they describe WebContents-internal active/render state (a background
#      tab legitimately stays internally active for continued running) and are
#      NOT the authority for "OS does not focus/activate".
#   5. Authoritative OS evidence: CGWindowList sampling for the browser PID must
#      stay 0 throughout, plus native policy logs (suppress + deny
#      show-and-activate, no internal allow while BACKGROUND).
#   6. structured policy log assertions (see 5)
#
# Safety / hygiene (mandatory, see AGENTS.md): default is dry-run; `--run`
# performs exactly ONE isolated invocation; never uses open/Dock/Launchpad;
# never shows/hides/reveals a window; never touches the real keychain.
#
# Usage: tests/phase2-tab-window-creation.sh [--run] [--keep-tmp]
#          [--timeout SECONDS] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'TAB-WINDOW-CHECK: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

CDP_HARNESS="$SCRIPT_DIR/tools/tab-window-cdp-check.py"
PAGE_SERVER="$SCRIPT_DIR/tools/page-test-server.py"

RUN=0
KEEP_TMP=0
# Per-CDP-command timeout. Generous by default: after (re-)signing, the first
# launch can spend minutes in dyld/amfid signature validation before main();
# with --remote-debugging-pipe the first CDP command is the only readiness wait.
TIMEOUT_SECONDS="${TAB_WINDOW_CHECK_TIMEOUT_SECONDS:-600}"

usage() {
  cat <<'EOF'
Usage: tests/phase2-tab-window-creation.sh [options]

Gate A evidence for two B items at once: AI-created normal Tab (background,
no focus) and AI-created independent BrowserWindow (internal, not on screen).
Default is dry-run; --run performs ONE isolated invocation.

Options:
  --run             actually launch the signed browser (no visible window)
  --keep-tmp        keep the temp dir (profile/logs) for inspection
  --timeout SECONDS per-CDP-command timeout (default 600; the first command
                    waits for the cold-started/re-signed app)
  --help            this message

Requires a Developer-ID-signed out/Dev/Browser CLI.app. Uses
--remote-debugging-pipe, --use-mock-keychain and a fresh mktemp profile;
never touches the real keychain and never shows a window.
EOF
}

die() { printf 'TAB-WINDOW-CHECK: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[tab-window-check] %s\n' "$*"; }

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

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-tabwindow.XXXXXX")"
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
[dry-run] local page server: $PAGE_SERVER (127.0.0.1, /page?name=<id>)
[dry-run] one isolated launch: --no-startup-window --use-mock-keychain
          --user-data-dir=<mktemp -d> --remote-debugging-pipe
          --browser-cli-background-presentation
[dry-run] CDP steps: createTarget(newWindow=true) -> W1;
          createTarget(newWindow=false) -> Tab must join W1;
          createTarget(newWindow=true) -> W2 (windowId != W1);
          Runtime.evaluate title/body/href per target
[dry-run] assert: real page content per target, PID on-screen window samples stay 0,
          policy suppress + deny show-and-activate, no internal allow in BACKGROUND.
          document.hasFocus()/visibilityState are recorded as informational only
          (WebContents internal state != macOS native focus/activation)
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

note "running CDP-over-pipe harness (one isolated launch)"
HARNESS_RC=0
"$PYTHON" "$CDP_HARNESS" \
  --app "$BIN" \
  --helper "$HELPER" \
  --server-url "http://127.0.0.1:$SERVER_PORT" \
  --profile "$PROFILE_DIR" \
  --log "$LOG" \
  --result-json "$RESULTS" \
  --timeout "$TIMEOUT_SECONDS" || HARNESS_RC=$?

[ -f "$RESULTS" ] || die "CDP harness produced no results (rc=$HARNESS_RC)"

# Extract JSON fields as shell-safe KEY='value' assignments.
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

print("W1_ID=" + sh(get("window1", "targetId")))
print("W1_WINDOW=" + sh(get("window1", "window", "windowId")))
print("W1_TITLE=" + sh(get("window1", "inspect", "title")))
print("W1_HREF=" + sh(get("window1", "inspect", "href")))
print("W1_BODY=" + sh(get("window1", "inspect", "body_marker")))
print("W1_FOCUS=" + sh(get("window1", "inspect", "has_focus")))
print("W1_VIS=" + sh(get("window1", "inspect", "visibility_state")))
print("TAB_ID=" + sh(get("tab", "targetId")))
print("TAB_WINDOW=" + sh(get("tab", "window", "windowId")))
print("TAB_TITLE=" + sh(get("tab", "inspect", "title")))
print("TAB_HREF=" + sh(get("tab", "inspect", "href")))
print("TAB_BODY=" + sh(get("tab", "inspect", "body_marker")))
print("TAB_FOCUS=" + sh(get("tab", "inspect", "has_focus")))
print("TAB_VIS=" + sh(get("tab", "inspect", "visibility_state")))
print("W2_ID=" + sh(get("window2", "targetId")))
print("W2_WINDOW=" + sh(get("window2", "window", "windowId")))
print("W2_TITLE=" + sh(get("window2", "inspect", "title")))
print("W2_FOCUS=" + sh(get("window2", "inspect", "has_focus")))
print("SAMPLES_MAX=" + sh(get("window_samples", "max")))
print("CMDLINE=" + sh(get("cmdline")))
print("ERRORS=" + sh(",".join(data.get("errors", []))))
print("EXIT_CODE=" + sh(get("exit_code")))
PY
)"

FAILED=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then
    printf 'tab-window.check name=%s result=PASS detail=%s\n' "$1" "$3" | tee -a "$CHECK_LOG"
  else
    printf 'tab-window.check name=%s result=FAIL expected=%s actual=%s\n' "$1" "$2" "$3" | tee -a "$CHECK_LOG"
    FAILED=1
  fi
}
info() { printf 'tab-window.info %s\n' "$*" | tee -a "$CHECK_LOG"; }

check "app-developer-id-signed" "1" "$APP_SIG_OK"
check "cmdline-has-mock-keychain" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "cmdline-has-isolated-user-data-dir" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -qF -- "--user-data-dir=$PROFILE_DIR"; then echo yes; else echo no; fi)"
check "harness-no-fatal-errors" "" "$ERRORS"
check "window-created" "yes" "$([ -n "$W1_ID" ] && echo yes || echo no)"
check "window-independent" "yes" \
  "$([ -n "$W1_WINDOW" ] && [ -n "$W2_WINDOW" ] && [ "$W1_WINDOW" != "$W2_WINDOW" ] && echo yes || echo no)"
check "window-real-content" "BCLI_PAGE_window1" "$W1_TITLE"
check "window-body-content" "BCLI_BODY_window1" "$W1_BODY"
check "window-url-is-local-page" "yes" \
  "$(if printf '%s' "$W1_HREF" | grep -qF "http://127.0.0.1:$SERVER_PORT/page?name=window1"; then echo yes; else echo no; fi)"
check "tab-created" "yes" "$([ -n "$TAB_ID" ] && echo yes || echo no)"
check "tab-joined-window" "yes" \
  "$([ -n "$TAB_WINDOW" ] && [ "$TAB_WINDOW" = "$W1_WINDOW" ] && echo yes || echo no)"
check "tab-real-content" "BCLI_PAGE_tab" "$TAB_TITLE"
check "tab-body-content" "BCLI_BODY_tab" "$TAB_BODY"
check "tab-url-is-local-page" "yes" \
  "$(if printf '%s' "$TAB_HREF" | grep -qF "http://127.0.0.1:$SERVER_PORT/page?name=tab"; then echo yes; else echo no; fi)"
check "desktop-windows-always-zero" "0" "$SAMPLES_MAX"
check "policy-host-suppress" "yes" \
  "$(if grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$LOG" 2>/dev/null; then echo yes; else echo no; fi)"
check "policy-denied-show-and-activate" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*action=show-and-activate' "$LOG" 2>/dev/null; then echo yes; else echo no; fi)"
check "no-internal-allow-in-background" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$LOG" 2>/dev/null; then echo no; else echo yes; fi)"

# Informational only (not gating): WebContents-internal active/render state.
# A background tab/window legitimately keeps its document active for continued
# running; the OS-level authority is CGWindowList=0 plus the native policy
# deny/no-allow evidence asserted above.
info "dom-focus-informational window1=$W1_FOCUS tab=$TAB_FOCUS window2=$W2_FOCUS"
info "dom-visibility-informational window1=$W1_VIS tab=$TAB_VIS"
info "os-focus-authority on-screen-window-max=$SAMPLES_MAX; policy deny show-and-activate + no internal allow"

# ---------------------------------------------------------------- evidence
EVIDENCE_DIR="$REPO_ROOT/build-logs/phase2-tab-window-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$EVIDENCE_DIR"
cp "$CHECK_LOG" "$EVIDENCE_DIR/check-results.txt" 2>/dev/null || true
cp "$RESULTS" "$EVIDENCE_DIR/cdp-results.json" 2>/dev/null || true
cp "$LOG" "$EVIDENCE_DIR/browser.log" 2>/dev/null || true
cp "$SERVER_LOG" "$EVIDENCE_DIR/page-server.log" 2>/dev/null || true
{
  echo "profile=$PROFILE_DIR (throwaway mktemp; removed unless --keep-tmp)"
  echo "server_port=$SERVER_PORT"
  echo "harness_rc=$HARNESS_RC"
  echo "cmdline=$CMDLINE"
  echo "window1_target=$W1_ID window=$W1_WINDOW title=$W1_TITLE focus=$W1_FOCUS visibility=$W1_VIS"
  echo "tab_target=$TAB_ID window=$TAB_WINDOW title=$TAB_TITLE focus=$TAB_FOCUS visibility=$TAB_VIS"
  echo "window2_target=$W2_ID window=$W2_WINDOW title=$W2_TITLE focus=$W2_FOCUS"
  echo "window_samples_max=$SAMPLES_MAX"
  echo "policy_decisions:"
  grep 'BROWSER_CLI_PRESENTATION' "$LOG" 2>/dev/null || true
} > "$EVIDENCE_DIR/summary.txt"
shasum -a 256 "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/sha256.txt" 2>/dev/null || true

if [ "$FAILED" = "1" ] || [ "$HARNESS_RC" -ne 0 ]; then
  printf 'TAB-WINDOW-CHECK: FAIL (evidence kept: %s)\n' "$EVIDENCE_DIR"
  KEEP_TMP=1
  exit 1
fi

printf 'TAB-WINDOW-CHECK: PASS (evidence: %s)\n' "$EVIDENCE_DIR"
