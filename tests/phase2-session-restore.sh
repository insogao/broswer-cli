#!/usr/bin/env bash
# Browser CLI Gate A evidence: Session restore of internal structure, no native show.
#
# Covers PROJECT-PROGRESS B item "Session restore：恢复内部结构但不得 native show":
# ONE driver invocation, ONE fresh mktemp profile, TWO phases:
#   phase 1: CDP builds a uniquely identifiable structure (Window A with tabs
#            restore_a1 + restore_a2, Window B with tab restore_b1, real local
#            pages), verifies content/grouping, waits for the profile's
#            Default/Sessions/Session_* file, then exits CLEANLY via CDP
#            Browser.close (-> ExitIgnoreUnloadHandlers -> MarkAsCleanShutdown);
#   phase 2: relaunches the SAME profile with `--restore-last-session`
#            (+ `--hide-crash-restore-bubble`) and WITHOUT `--no-startup-window`
#            (StartupBrowserCreator skips window/restore startup when that
#            switch is present). The profile is not "new" anymore, which is what
#            makes the restore switch effective.
# Acceptance: restored targets exist with real readable content and the
# original grouping; on-screen window samples stay 0 in BOTH phases; policy
# logs suppress/deny presentation with no internal allow while BACKGROUND.
# DOM hasFocus/visibilityState are informational only.
#
# Safety / hygiene (mandatory, see AGENTS.md): default is dry-run; `--run`
# performs exactly ONE isolated invocation; never uses open/Dock/Launchpad;
# never shows/hides/reveals a window; never touches the real keychain.
#
# Usage: tests/phase2-session-restore.sh [--run] [--keep-tmp]
#          [--timeout SECONDS] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'SESSION-RESTORE-CHECK: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

CDP_HARNESS="$SCRIPT_DIR/tools/session-restore-cdp-check.py"
PAGE_SERVER="$SCRIPT_DIR/tools/page-test-server.py"

RUN=0
KEEP_TMP=0
TIMEOUT_SECONDS="${SESSION_RESTORE_CHECK_TIMEOUT_SECONDS:-600}"

usage() {
  cat <<'EOF'
Usage: tests/phase2-session-restore.sh [options]

Gate A evidence: session restore rebuilds the internal window/tab structure
without any native show. Default is dry-run; --run performs ONE isolated
invocation with two phases sharing one fresh mktemp profile.

Options:
  --run             actually launch the signed browser (no visible window)
  --keep-tmp        keep the temp dir (profile/logs) for inspection
  --timeout SECONDS per-CDP-command timeout (default 600; the first command
                    waits for the cold-started/re-signed app)
  --help            this message

Requires a Developer-ID-signed out/Dev/Browser CLI.app. Uses the shared CDP
pipe launcher (--remote-debugging-pipe, --use-mock-keychain, fresh mktemp
profile); the restore phase uses --restore-last-session and must NOT use
--no-startup-window.
EOF
}

die() { printf 'SESSION-RESTORE-CHECK: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[session-restore-check] %s\n' "$*"; }

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

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-restore.XXXXXX")"
PROFILE_DIR="$TMP_DIR/profile"
LOG_P1="$TMP_DIR/browser-phase1.log"
LOG_P2="$TMP_DIR/browser-phase2.log"
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
[dry-run] one fresh mktemp profile shared by both phases
[dry-run] phase 1: shared CDP pipe launcher (--remote-debugging-pipe,
          --use-mock-keychain, --user-data-dir=<mktemp -d>) builds Window A with
          tabs restore_a1/restore_a2 and Window B with restore_b1 (real local
          pages), verifies content + Browser.getWindowForTarget grouping, waits
          for Default/Sessions/Session_* then exits cleanly via CDP Browser.close
[dry-run] phase 2: relaunch SAME profile with --restore-last-session and
          --hide-crash-restore-bubble, and WITHOUT --no-startup-window
          (that switch skips window/restore startup); verify the restored
          targets, real content and original grouping
[dry-run] authoritative OS assertions: PID on-screen window samples stay 0 in
          BOTH phases; policy suppress + deny show-and-activate, no internal
          allow in BACKGROUND; DOM focus/visibility are informational only
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

note "running session-restore CDP harness (two phases, one profile)"
HARNESS_RC=0
PYTHONPATH="$SCRIPT_DIR/tools" "$PYTHON" "$CDP_HARNESS" \
  --app "$BIN" \
  --helper "$HELPER" \
  --server-url "http://127.0.0.1:$SERVER_PORT" \
  --profile "$PROFILE_DIR" \
  --log-p1 "$LOG_P1" \
  --log-p2 "$LOG_P2" \
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

p1 = data.get("phase1") or {}
p2 = data.get("phase2") or {}
struct = p1.get("structure") or {}
restored = p2.get("restored") or {}

sessions = p1.get("sessions_files") or []
sessions_ok = "yes" if any(
    str(item.get("path", "")).startswith("Session_") for item in sessions
) else "no"

print("P1_CMDLINE=" + sh(p1.get("cmdline", "")))
print("P1_A1_WINDOW=" + sh(get("phase1", "structure", "restore_a1", "window", "windowId")))
print("P1_A2_WINDOW=" + sh(get("phase1", "structure", "restore_a2", "window", "windowId")))
print("P1_B1_WINDOW=" + sh(get("phase1", "structure", "restore_b1", "window", "windowId")))
for name in ("restore_a1", "restore_a2", "restore_b1"):
    print("P1_%s_TITLE=" % name.upper() + sh(get("phase1", "structure", name, "inspect", "title")))
    print("P1_%s_BODY=" % name.upper() + sh(get("phase1", "structure", name, "inspect", "body_marker")))
print("P1_SESSIONS_OK=" + sh(sessions_ok))
print("P1_SESSIONS_FILES=" + sh(",".join(str(item.get("path")) for item in sessions)))
print("P1_EXIT=" + sh(p1.get("exit_code")))
print("P1_SAMPLES_MAX=" + sh(p1.get("window_samples_max")))
print("P2_CMDLINE=" + sh(p2.get("cmdline", "")))
print("P2_RESTORED_COUNT=" + sh(len(restored)))
for name in ("restore_a1", "restore_a2", "restore_b1"):
    print("P2_%s_TITLE=" % name.upper() + sh(get("phase2", "restored", name, "inspect", "title")))
    print("P2_%s_BODY=" % name.upper() + sh(get("phase2", "restored", name, "inspect", "body_marker")))
    print("P2_%s_WINDOW=" % name.upper() + sh(get("phase2", "restored", name, "window", "windowId")))
    print("P2_%s_FOCUS=" % name.upper() + sh(get("phase2", "restored", name, "inspect", "has_focus")))
    print("P2_%s_VIS=" % name.upper() + sh(get("phase2", "restored", name, "inspect", "visibility_state")))
print("P2_EXIT=" + sh(p2.get("exit_code")))
print("P2_SAMPLES_MAX=" + sh(p2.get("window_samples_max")))
print("ORIGINS_SEEN=" + sh(",".join(data.get("origins_seen", []))))
print("ERRORS=" + sh(",".join(data.get("errors", []))))
PY
)"

FAILED=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then
    printf 'session-restore.check name=%s result=PASS detail=%s\n' "$1" "$3" | tee -a "$CHECK_LOG"
  else
    printf 'session-restore.check name=%s result=FAIL expected=%s actual=%s\n' "$1" "$2" "$3" | tee -a "$CHECK_LOG"
    FAILED=1
  fi
}
info() { printf 'session-restore.info %s\n' "$*" | tee -a "$CHECK_LOG"; }

phase1_content_ok() {
  [ "$P1_RESTORE_A1_TITLE" = "BCLI_PAGE_restore_a1" ] \
    && [ "$P1_RESTORE_A1_BODY" = "BCLI_BODY_restore_a1" ] \
    && [ "$P1_RESTORE_A2_TITLE" = "BCLI_PAGE_restore_a2" ] \
    && [ "$P1_RESTORE_A2_BODY" = "BCLI_BODY_restore_a2" ] \
    && [ "$P1_RESTORE_B1_TITLE" = "BCLI_PAGE_restore_b1" ] \
    && [ "$P1_RESTORE_B1_BODY" = "BCLI_BODY_restore_b1" ] && echo yes || echo no
}
phase2_content_ok() {
  [ "$P2_RESTORE_A1_TITLE" = "BCLI_PAGE_restore_a1" ] \
    && [ "$P2_RESTORE_A1_BODY" = "BCLI_BODY_restore_a1" ] \
    && [ "$P2_RESTORE_A2_TITLE" = "BCLI_PAGE_restore_a2" ] \
    && [ "$P2_RESTORE_A2_BODY" = "BCLI_BODY_restore_a2" ] \
    && [ "$P2_RESTORE_B1_TITLE" = "BCLI_PAGE_restore_b1" ] \
    && [ "$P2_RESTORE_B1_BODY" = "BCLI_BODY_restore_b1" ] && echo yes || echo no
}

check "app-developer-id-signed" "1" "$APP_SIG_OK"
check "phase1-cmdline-mock-keychain" "yes" \
  "$(if printf '%s' "$P1_CMDLINE" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "phase1-cmdline-isolated-profile" "yes" \
  "$(if printf '%s' "$P1_CMDLINE" | grep -qF -- "--user-data-dir=$PROFILE_DIR"; then echo yes; else echo no; fi)"
check "phase2-cmdline-mock-keychain" "yes" \
  "$(if printf '%s' "$P2_CMDLINE" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "phase2-cmdline-same-profile" "yes" \
  "$(if printf '%s' "$P2_CMDLINE" | grep -qF -- "--user-data-dir=$PROFILE_DIR"; then echo yes; else echo no; fi)"
check "phase2-cmdline-has-restore-last-session" "yes" \
  "$(if printf '%s' "$P2_CMDLINE" | grep -q -- '--restore-last-session'; then echo yes; else echo no; fi)"
check "phase2-cmdline-without-no-startup-window" "yes" \
  "$(if printf '%s' "$P2_CMDLINE" | grep -q -- '--no-startup-window'; then echo no; else echo yes; fi)"
check "harness-no-fatal-errors" "" "$ERRORS"
check "phase1-structure-created" "yes" \
  "$([ -n "$P1_A1_WINDOW" ] && [ "$P1_A1_WINDOW" = "$P1_A2_WINDOW" ] \
     && [ "$P1_B1_WINDOW" != "$P1_A1_WINDOW" ] && echo yes || echo no)"
check "phase1-real-content" "yes" "$(phase1_content_ok)"
check "phase1-sessions-file-written" "yes" "$P1_SESSIONS_OK"
check "phase1-clean-exit" "0" "$P1_EXIT"
check "phase2-restored-all-targets" "3" "$P2_RESTORED_COUNT"
check "phase2-restored-real-content" "yes" "$(phase2_content_ok)"
check "phase2-restored-grouping" "yes" \
  "$([ -n "$P2_RESTORE_A1_WINDOW" ] && [ "$P2_RESTORE_A1_WINDOW" = "$P2_RESTORE_A2_WINDOW" ] \
     && [ "$P2_RESTORE_B1_WINDOW" != "$P2_RESTORE_A1_WINDOW" ] && echo yes || echo no)"
check "desktop-windows-zero-phase1" "0" "$P1_SAMPLES_MAX"
check "desktop-windows-zero-phase2" "0" "$P2_SAMPLES_MAX"
check "policy-suppress-both-phases" "yes" \
  "$(if grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$LOG_P1" 2>/dev/null \
        && grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$LOG_P2" 2>/dev/null; then echo yes; else echo no; fi)"
check "policy-denied-show-and-activate" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*action=show-and-activate' "$LOG_P1" 2>/dev/null \
        && grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*action=show-and-activate' "$LOG_P2" 2>/dev/null; then echo yes; else echo no; fi)"
check "no-internal-allow-in-background" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$LOG_P1" 2>/dev/null \
        || grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$LOG_P2" 2>/dev/null; then echo no; else echo yes; fi)"

# Informational only: policy origins actually emitted, DOM state, session files.
info "origins-seen=${ORIGINS_SEEN:-none} (kSessionRestore enum may exist without producer wiring)"
info "phase1 window ids a1=$P1_A1_WINDOW a2=$P1_A2_WINDOW b1=$P1_B1_WINDOW"
info "phase2 window ids a1=$P2_RESTORE_A1_WINDOW a2=$P2_RESTORE_A2_WINDOW b1=$P2_RESTORE_B1_WINDOW"
info "sessions files: ${P1_SESSIONS_FILES:-none}"
info "dom-focus-informational a1=$P2_RESTORE_A1_FOCUS/$P2_RESTORE_A1_VIS b1=$P2_RESTORE_B1_FOCUS/$P2_RESTORE_B1_VIS"
info "os-focus-authority on-screen max p1=$P1_SAMPLES_MAX p2=$P2_SAMPLES_MAX; policy deny + no internal allow"

# ---------------------------------------------------------------- evidence
EVIDENCE_DIR="$REPO_ROOT/build-logs/phase2-session-restore-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$EVIDENCE_DIR"
cp "$CHECK_LOG" "$EVIDENCE_DIR/check-results.txt" 2>/dev/null || true
cp "$RESULTS" "$EVIDENCE_DIR/cdp-results.json" 2>/dev/null || true
cp "$LOG_P1" "$EVIDENCE_DIR/browser-phase1.log" 2>/dev/null || true
cp "$LOG_P2" "$EVIDENCE_DIR/browser-phase2.log" 2>/dev/null || true
cp "$SERVER_LOG" "$EVIDENCE_DIR/page-server.log" 2>/dev/null || true
{
  echo "profile=$PROFILE_DIR (throwaway mktemp; removed unless --keep-tmp)"
  echo "server_port=$SERVER_PORT"
  echo "harness_rc=$HARNESS_RC"
  echo "phase1 cmdline=$P1_CMDLINE"
  echo "phase2 cmdline=$P2_CMDLINE"
  echo "phase1 structure windows: a1=$P1_A1_WINDOW a2=$P1_A2_WINDOW b1=$P1_B1_WINDOW"
  echo "phase1 sessions files: $P1_SESSIONS_FILES"
  echo "phase1 exit=$P1_EXIT samples_max=$P1_SAMPLES_MAX"
  echo "phase2 restored=$P2_RESTORED_COUNT windows: a1=$P2_RESTORE_A1_WINDOW a2=$P2_RESTORE_A2_WINDOW b1=$P2_RESTORE_B1_WINDOW"
  echo "phase2 exit=$P2_EXIT samples_max=$P2_SAMPLES_MAX"
  echo "origins_seen=${ORIGINS_SEEN:-none}"
  echo "policy_decisions_phase1:"
  grep 'BROWSER_CLI_PRESENTATION' "$LOG_P1" 2>/dev/null || true
  echo "policy_decisions_phase2:"
  grep 'BROWSER_CLI_PRESENTATION' "$LOG_P2" 2>/dev/null || true
} > "$EVIDENCE_DIR/summary.txt"
shasum -a 256 "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/sha256.txt" 2>/dev/null || true

if [ "$FAILED" = "1" ] || [ "$HARNESS_RC" -ne 0 ]; then
  printf 'SESSION-RESTORE-CHECK: FAIL (evidence kept: %s)\n' "$EVIDENCE_DIR"
  KEEP_TMP=1
  exit 1
fi

printf 'SESSION-RESTORE-CHECK: PASS (evidence: %s)\n' "$EVIDENCE_DIR"
