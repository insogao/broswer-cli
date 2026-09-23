#!/usr/bin/env bash
# Browser CLI Gate A evidence: web page window.open() under BACKGROUND.
#
# Covers PROJECT-PROGRESS B item "网页 window.open()：后台创建且不 Show/Activate":
# one isolated run of the signed app opens a real opener page, the page's own JS
# `window.open()` creates a child popup/tab, and the harness proves:
#   1. the returned child WindowProxy is live (not popup-blocked/closed);
#   2. a NEW target/WebContents exists with the child URL and correct DOM content;
#   3. opener relationship from both sides (parent sees the child WindowProxy;
#      child's window.opener points back to the parent) plus the structural
#      TargetInfo.openerId == parent target id;
#   4. authoritative OS evidence: CGWindowList on-screen window samples for the
#      browser PID stay 0, and native policy logs deny show-and-activate with no
#      internal allow while BACKGROUND.
# document.hasFocus()/visibilityState are recorded as INFORMATIONAL WebContents
# state only (a background page legitimately stays internally active) and are not
# failure conditions; macOS native focus/activation is denied by the policy.
#
# Safety / hygiene (mandatory, see AGENTS.md): default is dry-run; `--run`
# performs exactly ONE isolated invocation; never uses open/Dock/Launchpad;
# never shows/hides/reveals a window; never touches the real keychain.
#
# Usage: tests/phase2-window-open-popup.sh [--run] [--keep-tmp]
#          [--timeout SECONDS] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'WINDOW-OPEN-CHECK: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

CDP_HARNESS="$SCRIPT_DIR/tools/window-open-cdp-check.py"
PAGE_SERVER="$SCRIPT_DIR/tools/page-test-server.py"

RUN=0
KEEP_TMP=0
TIMEOUT_SECONDS="${WINDOW_OPEN_CHECK_TIMEOUT_SECONDS:-600}"

usage() {
  cat <<'EOF'
Usage: tests/phase2-window-open-popup.sh [options]

Gate A evidence: page-initiated window.open() creates an internal child
popup/tab under BACKGROUND, without native Show/Activate. Default is dry-run;
--run performs ONE isolated invocation.

Options:
  --run             actually launch the signed browser (no visible window)
  --keep-tmp        keep the temp dir (profile/logs) for inspection
  --timeout SECONDS per-CDP-command timeout (default 600; the first command
                    waits for the cold-started/re-signed app)
  --help            this message

Requires a Developer-ID-signed out/Dev/Browser CLI.app. Uses the shared CDP
pipe launcher (mock keychain + fresh mktemp profile); never touches the real
keychain and never shows a window.
EOF
}

die() { printf 'WINDOW-OPEN-CHECK: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[window-open-check] %s\n' "$*"; }

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

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-windowopen.XXXXXX")"
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
[dry-run] local page server: $PAGE_SERVER (127.0.0.1, /opener?name=parent&child=child)
[dry-run] one isolated launch through the shared CDP pipe launcher (fd 3/4),
          --remote-debugging-pipe, with --use-mock-keychain and a fresh
          --user-data-dir=<mktemp -d>
[dry-run] steps: load the /opener page; trigger the page's own JS window.open()
          (user gesture); expect a NEW child target with
          /page?name=child, verify parent<->child opener relations and
          TargetInfo.openerId; record DOM focus/visibility as informational only
[dry-run] authoritative OS assertions: PID on-screen window samples stay 0,
          policy suppress + deny show-and-activate, no internal allow in BACKGROUND
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

note "running the page window.open() CDP harness (one isolated launch)"
HARNESS_RC=0
PYTHONPATH="$SCRIPT_DIR/tools" "$PYTHON" "$CDP_HARNESS" \
  --app "$BIN" \
  --helper "$HELPER" \
  --server-url "http://127.0.0.1:$SERVER_PORT" \
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

print("P_ID=" + sh(get("parent", "targetId")))
print("P_WINDOW=" + sh(get("parent", "window", "windowId")))
print("P_TITLE=" + sh(get("parent", "inspect", "title")))
print("P_BODY=" + sh(get("parent", "inspect", "body_marker")))
print("P_HREF=" + sh(get("parent", "inspect", "href")))
print("P_FOCUS=" + sh(get("parent", "inspect", "has_focus")))
print("P_VIS=" + sh(get("parent", "inspect", "visibility_state")))
print("OPEN_RETURN=" + sh(get("parent", "open_return")))
print("P_CHILD_HREF=" + sh(get("parent", "child_href")))
print("P_CHILD_CLOSED=" + sh(get("parent", "child_closed")))
print("C_ID=" + sh(get("child", "targetId")))
print("C_TYPE=" + sh(get("child", "type")))
print("C_URL=" + sh(get("child", "url")))
print("C_OPENER_ID=" + sh(get("child", "openerId")))
print("C_WINDOW=" + sh(get("child", "window", "windowId")))
print("C_TITLE=" + sh(get("child", "inspect", "title")))
print("C_BODY=" + sh(get("child", "inspect", "body_marker")))
print("C_HREF=" + sh(get("child", "inspect", "href")))
print("C_FOCUS=" + sh(get("child", "inspect", "has_focus")))
print("C_VIS=" + sh(get("child", "inspect", "visibility_state")))
print("C_NAME=" + sh(get("child", "window_name")))
print("C_OPENER_HREF=" + sh(get("child", "opener_href")))
print("SAMPLES_MAX=" + sh(get("window_samples", "max")))
print("CMDLINE=" + sh(get("cmdline")))
print("ERRORS=" + sh(",".join(data.get("errors", []))))
print("EXIT_CODE=" + sh(get("exit_code")))
PY
)"

FAILED=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then
    printf 'window-open.check name=%s result=PASS detail=%s\n' "$1" "$3" | tee -a "$CHECK_LOG"
  else
    printf 'window-open.check name=%s result=FAIL expected=%s actual=%s\n' "$1" "$2" "$3" | tee -a "$CHECK_LOG"
    FAILED=1
  fi
}
info() { printf 'window-open.info %s\n' "$*" | tee -a "$CHECK_LOG"; }

PARENT_URL="http://127.0.0.1:$SERVER_PORT/opener?name=parent&child=child"
CHILD_URL="http://127.0.0.1:$SERVER_PORT/page?name=child"

check "app-developer-id-signed" "1" "$APP_SIG_OK"
check "cmdline-has-mock-keychain" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "cmdline-has-isolated-user-data-dir" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -qF -- "--user-data-dir=$PROFILE_DIR"; then echo yes; else echo no; fi)"
check "harness-no-fatal-errors" "" "$ERRORS"
check "parent-window-created" "yes" "$([ -n "$P_ID" ] && echo yes || echo no)"
check "parent-real-content" "BCLI_PAGE_opener_parent" "$P_TITLE"
check "parent-body-content" "BCLI_BODY_opener_parent" "$P_BODY"
check "parent-url-is-opener-page" "yes" \
  "$(if printf '%s' "$P_HREF" | grep -qF "$PARENT_URL"; then echo yes; else echo no; fi)"
check "window-open-returned-live-child" "yes" \
  "$([ "$OPEN_RETURN" = "True" ] && [ "$P_CHILD_CLOSED" = "False" ] && echo yes || echo no)"
check "child-target-created" "yes" \
  "$([ -n "$C_ID" ] && [ "$C_ID" != "$P_ID" ] && echo yes || echo no)"
check "child-is-new-webcontents" "page" "$C_TYPE"
check "child-real-content" "BCLI_PAGE_child" "$C_TITLE"
check "child-body-content" "BCLI_BODY_child" "$C_BODY"
check "child-url-is-child-page" "yes" \
  "$(if printf '%s' "$C_HREF" | grep -qF "$CHILD_URL"; then echo yes; else echo no; fi)"
check "child-opener-id-structural" "yes" \
  "$([ -n "$C_OPENER_ID" ] && [ "$C_OPENER_ID" = "$P_ID" ] && echo yes || echo no)"
check "child-window-opener-relation" "yes" \
  "$([ -n "$C_OPENER_HREF" ] && [ "$C_OPENER_HREF" = "$P_HREF" ] && echo yes || echo no)"
check "parent-sees-child-relation" "yes" \
  "$([ -n "$P_CHILD_HREF" ] && [ "$P_CHILD_HREF" = "$C_HREF" ] && echo yes || echo no)"
check "desktop-windows-always-zero" "0" "$SAMPLES_MAX"
check "policy-host-suppress" "yes" \
  "$(if grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$LOG" 2>/dev/null; then echo yes; else echo no; fi)"
check "policy-denied-show-and-activate" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*action=show-and-activate' "$LOG" 2>/dev/null; then echo yes; else echo no; fi)"
check "no-internal-allow-in-background" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$LOG" 2>/dev/null; then echo no; else echo yes; fi)"

# Informational only: WebContents-internal active/render state; OS authority is
# CGWindowList=0 plus the native policy deny/no-allow checks above.
info "dom-focus-informational parent=$P_FOCUS child=$C_FOCUS"
info "dom-visibility-informational parent=$P_VIS child=$C_VIS"
info "window-ids parent=$P_WINDOW child=$C_WINDOW window-name=$C_NAME"
info "os-focus-authority on-screen-window-max=$SAMPLES_MAX; policy deny show-and-activate + no internal allow"

# ---------------------------------------------------------------- evidence
EVIDENCE_DIR="$REPO_ROOT/build-logs/phase2-window-open-$(date +%Y%m%d-%H%M%S)"
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
  echo "parent target=$P_ID window=$P_WINDOW title=$P_TITLE open_return=$OPEN_RETURN focus=$P_FOCUS visibility=$P_VIS"
  echo "child  target=$C_ID type=$C_TYPE opener_id=$C_OPENER_ID window=$C_WINDOW title=$C_TITLE focus=$C_FOCUS visibility=$C_VIS"
  echo "relations parent_sees_child_href=$P_CHILD_HREF child_window_opener_href=$C_OPENER_HREF"
  echo "window_samples_max=$SAMPLES_MAX"
  echo "policy_decisions:"
  grep 'BROWSER_CLI_PRESENTATION' "$LOG" 2>/dev/null || true
} > "$EVIDENCE_DIR/summary.txt"
shasum -a 256 "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/sha256.txt" 2>/dev/null || true

if [ "$FAILED" = "1" ] || [ "$HARNESS_RC" -ne 0 ]; then
  printf 'WINDOW-OPEN-CHECK: FAIL (evidence kept: %s)\n' "$EVIDENCE_DIR"
  KEEP_TMP=1
  exit 1
fi

printf 'WINDOW-OPEN-CHECK: PASS (evidence: %s)\n' "$EVIDENCE_DIR"
