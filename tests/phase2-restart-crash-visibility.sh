#!/usr/bin/env bash
# Browser CLI Gate A evidence: repeated start/exit + crash recovery without any
# transient on-screen window.
#
# Covers PROJECT-PROGRESS B item "多次启动、退出和崩溃恢复均无短暂上屏".
#
# Design (single driver invocation, ONE fresh mktemp profile):
#   * BEFORE the first launch the driver compiles and starts
#     tests/tools/onscreen-window-watch.m, which continuously enumerates
#     on-screen windows and matches them by APP IDENTITY (owner executable path
#     via proc_pidpath), covering the spawn-time blind spot of a PID sampler
#     started after Popen. It runs until the scenario ends.
#   * N clean cycles: signed app + --use-mock-keychain + the same isolated
#     profile; a real local page (cycle_N) is created and verified, then the
#     browser exits cleanly via CDP Browser.close.
#   * One abnormal termination: after the crash-phase page is verified, the
#     exact spawned PID is killed with SIGKILL ONLY after its command line is
#     verified to contain the expected app binary and this run's
#     --user-data-dir. No broad pkill is used for it.
#   * Crash recovery: relaunch the same profile with `--restore-last-session`
#     `--hide-crash-restore-bubble` and without `--no-startup-window`; verify the
#     crash-phase page is restored with real content.
#   * Acceptance: the pre-started watcher's max on-screen window count is 0 over
#     the whole timeline; policy logs suppress/deny presentation with no
#     internal allow while BACKGROUND; DOM focus/visibility are informational.
#
# Safety / hygiene (mandatory, see AGENTS.md): default is dry-run; `--run`
# performs exactly ONE isolated invocation; never uses open/Dock/Launchpad;
# never shows/hides/reveals a window; never touches the real keychain; the
# abnormal kill targets only the verified exact PID.
#
# Usage: tests/phase2-restart-crash-visibility.sh [--run] [--keep-tmp]
#          [--cycles N] [--timeout SECONDS] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'RESTART-CRASH-CHECK: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

CDP_HARNESS="$SCRIPT_DIR/tools/restart-cycles-cdp-check.py"
WATCHER_SRC="$SCRIPT_DIR/tools/onscreen-window-watch.m"
PAGE_SERVER="$SCRIPT_DIR/tools/page-test-server.py"

RUN=0
KEEP_TMP=0
CYCLES="${RESTART_CYCLES:-2}"
TIMEOUT_SECONDS="${RESTART_CRASH_CHECK_TIMEOUT_SECONDS:-600}"

usage() {
  cat <<'EOF'
Usage: tests/phase2-restart-crash-visibility.sh [options]

Gate A evidence: several clean start/close cycles plus one exact-PID crash and
crash recovery, all with a pre-started app-identity on-screen window watcher
(max must stay 0). Default is dry-run; --run performs ONE isolated invocation.

Options:
  --run             actually launch the signed browser (no visible window)
  --keep-tmp        keep the temp dir (profile/logs) for inspection
  --cycles N        clean start/close cycles (default 2)
  --timeout SECONDS per-CDP-command timeout (default 600; the first command
                    waits for the cold-started/re-signed app)
  --help            this message

Requires a Developer-ID-signed out/Dev/Browser CLI.app. Uses the shared CDP
pipe launcher (--remote-debugging-pipe, --use-mock-keychain, fresh mktemp
profile); the crash kill is restricted to the verified exact PID.
EOF
}

die() { printf 'RESTART-CRASH-CHECK: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[restart-crash-check] %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --run) RUN=1 ;;
    --keep-tmp) KEEP_TMP=1 ;;
    --cycles) shift; [ $# -gt 0 ] || die "--cycles needs a value"; CYCLES="$1" ;;
    --timeout) shift; [ $# -gt 0 ] || die "--timeout needs a value"; TIMEOUT_SECONDS="$1" ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

case "$CYCLES" in
  ''|*[!0-9]*) die "--cycles must be a positive integer (got '$CYCLES')" ;;
esac
[ "$CYCLES" -ge 1 ] || die "--cycles must be >= 1 (got '$CYCLES')"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-restart.XXXXXX")"
PROFILE_DIR="$TMP_DIR/profile"
RESULTS="$TMP_DIR/cdp-results.json"
SERVER_LOG="$TMP_DIR/page-server.log"
SERVER_PORT_FILE="$TMP_DIR/page-server.port"
CHECK_LOG="$TMP_DIR/check-results.txt"
WATCHER="$TMP_DIR/onscreen-window-watch"
: > "$CHECK_LOG"
: > "$SERVER_LOG"

case "$PROFILE_DIR" in
  "$TMP_DIR"/*) : ;;
  *) die "refusing to run: profile dir '$PROFILE_DIR' is not inside '$TMP_DIR'" ;;
esac

# Pre-flight: no Browser CLI process may be running before the watcher starts.
PREEXISTING="$(pgrep -f "$BIN" 2>/dev/null || true)"
PREEXISTING_X="$(pgrep -x "$BROWSER_CLI_PRODUCT_NAME" 2>/dev/null || true)"
if [ -n "$PREEXISTING" ] || [ -n "$PREEXISTING_X" ]; then
  PREEXISTING_OK=0
  note "preexisting Browser CLI processes: ${PREEXISTING:-} ${PREEXISTING_X:-}"
else
  PREEXISTING_OK=1
fi

if [ "$RUN" != "1" ]; then
  cat <<EOF
[dry-run] product identity: '$BROWSER_CLI_PRODUCT_NAME' (team $BROWSER_CLI_TEAM_ID)
[dry-run] app path: $APP_DIR (must be Developer-ID signed)
[dry-run] pre-flight: no Browser CLI process running before the watcher starts
[dry-run] watcher: $WATCHER_SRC started BEFORE any launch, sampling on-screen
          windows every 10ms (<=20ms gate; interval + sample counts recorded) by
          app identity (owner executable path via proc_pidpath), covering
          spawn -> exit -> crash -> recovery with multiple baseline samples
          before the first launch
[dry-run] one fresh mktemp profile for all phases; shared CDP pipe launcher
          (--remote-debugging-pipe, --use-mock-keychain, --user-data-dir=<mktemp>)
[dry-run] phases: $CYCLES clean cycles (create real page cycle_N, verify,
          clean exit via CDP Browser.close); one crash phase (verify page,
          then require Sessions files to show a clear update + stable window
          [timing-only persistence evidence, no URL parsing], verify exact PID
          identity from cmdline incl. app path + profile, SIGKILL that PID only);
          crash recovery (same profile, --restore-last-session
          --hide-crash-restore-bubble, WITHOUT --no-startup-window) verifying the
          crash page is restored
[dry-run] acceptance: watcher max on-screen windows == 0 across the whole
          timeline; policy suppress + deny show-and-activate, no internal allow
          in BACKGROUND; DOM focus/visibility informational only
[dry-run] nothing executed; pass --run to execute (one isolated invocation).
EOF
  rm -rf "$TMP_DIR"
  exit 0
fi

[ -x "$BIN" ] || die "browser binary not found/executable: $BIN (set CHROMIUM_ROOT/OUT_NAME)"
[ -d "$APP_DIR" ] || die "product app not found: $APP_DIR"
[ -f "$CDP_HARNESS" ] || die "missing CDP harness: $CDP_HARNESS"
[ -f "$WATCHER_SRC" ] || die "missing watcher source: $WATCHER_SRC"

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

note "compiling continuous app-identity window watcher"
xcrun clang -fobjc-arc -framework Foundation -framework CoreGraphics \
  -o "$WATCHER" "$WATCHER_SRC"
[ -x "$WATCHER" ] || die "failed to compile window watcher"

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

note "running restart/crash CDP harness ($CYCLES clean cycles + crash recovery)"
HARNESS_RC=0
PYTHONPATH="$SCRIPT_DIR/tools" "$PYTHON" "$CDP_HARNESS" \
  --app "$BIN" \
  --watcher "$WATCHER" \
  --server-url "http://127.0.0.1:$SERVER_PORT" \
  --profile "$PROFILE_DIR" \
  --log-dir "$TMP_DIR" \
  --result-json "$RESULTS" \
  --cycles "$CYCLES" \
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
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return default
    return cur

def sh(value):
    return "'" + str(value).replace("'", "'\\''") + "'"

cycles = data.get("cycles") or []
crash = data.get("crash") or {}
recovery = data.get("recovery") or {}
watcher = data.get("watcher") or {}

cmdl = [entry.get("cmdline", "") for entry in cycles]
cmdl.append(crash.get("cmdline", ""))
cmdl.append(recovery.get("cmdline", ""))
nonempty = [line for line in cmdl if line]

print("WATCHER_STARTED=" + sh(watcher.get("started")))
print("WATCHER_INTERVAL=" + sh(watcher.get("interval_ms")))
print("WATCHER_SAMPLES=" + sh(watcher.get("samples")))
print("WATCHER_BEFORE_FIRST=" + sh(watcher.get("samples_before_first_launch")))
print("WATCHER_MAX=" + sh(watcher.get("max")))
print("WATCHER_NONZERO=" + sh(watcher.get("nonzero_samples")))
print("WATCHER_DETAILS=" + sh(len(watcher.get("nonzero_details") or [])))
print("CYCLES_COUNT=" + sh(len(cycles)))
for index, entry in enumerate(cycles, start=1):
    print("CYCLE%d_TITLE=" % index + sh(get("cycles", index - 1, "inspect", "title")))
    print("CYCLE%d_BODY=" % index + sh(get("cycles", index - 1, "inspect", "body_marker")))
    print("CYCLE%d_EXIT=" % index + sh(entry.get("exit_code")))
print("CRASH_TITLE=" + sh(get("crash", "inspect", "title")))
print("CRASH_BODY=" + sh(get("crash", "inspect", "body_marker")))
print("CRASH_IDENTITY_OK=" + sh(
    "yes" if get("crash", "pid_identity", "contains_app_binary") is True
    and get("crash", "pid_identity", "contains_profile") is True else "no"))
print("CRASH_SIGNAL=" + sh(crash.get("kill_signal")))
print("CRASH_EXIT=" + sh(crash.get("exit_code")))
print("CRASH_SESS_UPDATE=" + sh(crash.get("sessions_update_detected")))
print("CRASH_SESS_STABLE=" + sh(crash.get("sessions_stable")))
print("CRASH_SESS_WAIT=" + sh(crash.get("sessions_update_wait_seconds")))
print("CRASH_SESS_BEFORE_COUNT=" + sh(len(crash.get("sessions_before") or {})))
print("CRASH_SESS_AFTER_COUNT=" + sh(len(crash.get("sessions_update_snapshot") or {})))
print("CRASH_SESS_BASIS=" + sh(crash.get("sessions_persistence_basis")))
print("RECOVERY_TITLE=" + sh(get("recovery", "inspect", "title")))
print("RECOVERY_BODY=" + sh(get("recovery", "inspect", "body_marker")))
print("RECOVERY_EXIT=" + sh(recovery.get("exit_code")))
print("ALL_MOCK_KEYCHAIN=" + sh(
    "yes" if nonempty and all("--use-mock-keychain" in line for line in nonempty)
    else "no"))
print("ALL_SAME_PROFILE=" + sh(
    "yes" if nonempty and all(("--user-data-dir=" + data.get("profile", "")) in line
                              for line in nonempty) else "no"))
print("RECOVERY_FLAGS_OK=" + sh(
    "yes" if "--restore-last-session" in recovery.get("cmdline", "")
    and "--hide-crash-restore-bubble" in recovery.get("cmdline", "")
    and "--no-startup-window" not in recovery.get("cmdline", "") else "no"))
print("ERRORS=" + sh(",".join(data.get("errors", []))))
PY
)"

FAILED=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then
    printf 'restart-crash.check name=%s result=PASS detail=%s\n' "$1" "$3" | tee -a "$CHECK_LOG"
  else
    printf 'restart-crash.check name=%s result=FAIL expected=%s actual=%s\n' "$1" "$2" "$3" | tee -a "$CHECK_LOG"
    FAILED=1
  fi
}
info() { printf 'restart-crash.info %s\n' "$*" | tee -a "$CHECK_LOG"; }

LOG_PATHS=()
for index in $(seq 1 "$CYCLES"); do
  LOG_PATHS+=("$TMP_DIR/browser-cycle${index}.log")
done
LOG_PATHS+=("$TMP_DIR/browser-crash.log" "$TMP_DIR/browser-recovery.log")
suppress_ok=yes
deny_ok=yes
allow_bad=no
for log in "${LOG_PATHS[@]}"; do
  [ -f "$log" ] || { suppress_ok=no; deny_ok=no; continue; }
  grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$log" || suppress_ok=no
  grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*action=show-and-activate' "$log" || deny_ok=no
  if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$log"; then
    allow_bad=yes
  fi
done

CYCLES_CONTENT_OK=yes
CYCLES_EXIT_OK=yes
for index in $(seq 1 "$CYCLES"); do
  title_var="CYCLE${index}_TITLE"
  body_var="CYCLE${index}_BODY"
  exit_var="CYCLE${index}_EXIT"
  [ "${!title_var}" = "BCLI_PAGE_cycle_$index" ] && [ "${!body_var}" = "BCLI_BODY_cycle_$index" ] || CYCLES_CONTENT_OK=no
  [ "${!exit_var}" = "0" ] || CYCLES_EXIT_OK=no
done

check "app-developer-id-signed" "1" "$APP_SIG_OK"
check "no-preexisting-browser-process" "1" "$PREEXISTING_OK"
check "watcher-started-before-first-launch" "True" "$WATCHER_STARTED"
check "watcher-interval-within-20ms" "yes" \
  "$([ "${WATCHER_INTERVAL:-999}" -le 20 ] 2>/dev/null && echo yes || echo no)"
check "watcher-baseline-samples" "yes" \
  "$([ "${WATCHER_BEFORE_FIRST:-0}" -ge 10 ] 2>/dev/null && echo yes || echo no)"
check "clean-cycles-completed" "$CYCLES" "$CYCLES_COUNT"
check "clean-cycles-real-content" "yes" "$CYCLES_CONTENT_OK"
check "clean-cycles-exit-zero" "yes" "$CYCLES_EXIT_OK"
check "crash-page-real-content" "yes" \
  "$([ "$CRASH_TITLE" = "BCLI_PAGE_crash_marker" ] && [ "$CRASH_BODY" = "BCLI_BODY_crash_marker" ] && echo yes || echo no)"
check "crash-sessions-updated-before-kill" "True" "$CRASH_SESS_UPDATE"
check "crash-sessions-stable-before-kill" "True" "$CRASH_SESS_STABLE"
check "crash-sessions-evidence-is-timing-only" "yes" \
  "$(if printf '%s' "$CRASH_SESS_BASIS" | grep -q 'timing-only'; then echo yes; else echo no; fi)"
check "crash-pid-identity-verified" "yes" "$CRASH_IDENTITY_OK"
check "crash-killed-exact-pid" "yes" \
  "$([ "$CRASH_SIGNAL" = "SIGKILL" ] && [ "$CRASH_EXIT" != "0" ] && echo yes || echo no)"
check "recovery-restored-crash-page" "yes" \
  "$([ "$RECOVERY_TITLE" = "BCLI_PAGE_crash_marker" ] && [ "$RECOVERY_BODY" = "BCLI_BODY_crash_marker" ] && echo yes || echo no)"
check "recovery-exit-zero" "0" "$RECOVERY_EXIT"
check "all-cmdlines-mock-keychain" "yes" "$ALL_MOCK_KEYCHAIN"
check "all-cmdlines-same-profile" "yes" "$ALL_SAME_PROFILE"
check "recovery-cmdline-restore-flags" "yes" "$RECOVERY_FLAGS_OK"
check "watcher-max-zero" "0" "$WATCHER_MAX"
check "policy-suppress-all-phases" "yes" "$suppress_ok"
check "policy-denied-show-and-activate" "yes" "$deny_ok"
check "no-internal-allow-in-background" "yes" "$([ "$allow_bad" = "no" ] && echo yes || echo no)"

info "watcher interval=${WATCHER_INTERVAL}ms samples=$WATCHER_SAMPLES before_first_launch=$WATCHER_BEFORE_FIRST nonzero=$WATCHER_NONZERO details=$WATCHER_DETAILS"
CYCLE_EXITS=""
for index in $(seq 1 "$CYCLES"); do
  exit_var="CYCLE${index}_EXIT"
  CYCLE_EXITS="$CYCLE_EXITS c$index=${!exit_var}"
done
info "cycle exits:$CYCLE_EXITS crash=$CRASH_EXIT recovery=$RECOVERY_EXIT"
info "crash sessions: update=$CRASH_SESS_UPDATE stable=$CRASH_SESS_STABLE wait=${CRASH_SESS_WAIT}s files ${CRASH_SESS_BEFORE_COUNT}->${CRASH_SESS_AFTER_COUNT} basis='$CRASH_SESS_BASIS'"
info "crash pid identity: $CRASH_IDENTITY_OK signal=$CRASH_SIGNAL"
info "dom-focus/visibility informational (not asserted as OS focus)"
info "os-focus-authority watcher max=$WATCHER_MAX; policy deny + no internal allow"

# ---------------------------------------------------------------- evidence
EVIDENCE_DIR="$REPO_ROOT/build-logs/phase2-restart-crash-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$EVIDENCE_DIR"
cp "$CHECK_LOG" "$EVIDENCE_DIR/check-results.txt" 2>/dev/null || true
cp "$RESULTS" "$EVIDENCE_DIR/cdp-results.json" 2>/dev/null || true
cp "$TMP_DIR/watcher-samples.txt" "$EVIDENCE_DIR/watcher-samples.txt" 2>/dev/null || true
for name in browser-cycle1.log browser-cycle2.log browser-crash.log browser-recovery.log; do
  cp "$TMP_DIR/$name" "$EVIDENCE_DIR/$name" 2>/dev/null || true
done
cp "$SERVER_LOG" "$EVIDENCE_DIR/page-server.log" 2>/dev/null || true
{
  echo "profile=$PROFILE_DIR (throwaway mktemp; removed unless --keep-tmp)"
  echo "server_port=$SERVER_PORT"
  echo "harness_rc=$HARNESS_RC"
  echo "watcher: started=$WATCHER_STARTED interval=${WATCHER_INTERVAL}ms samples=$WATCHER_SAMPLES before_first_launch=$WATCHER_BEFORE_FIRST max=$WATCHER_MAX nonzero=$WATCHER_NONZERO"
  echo "cycles=$CYCLES_COUNT exits:$CYCLE_EXITS"
  echo "crash: identity=$CRASH_IDENTITY_OK signal=$CRASH_SIGNAL exit=$CRASH_EXIT"
  echo "crash sessions: update=$CRASH_SESS_UPDATE stable=$CRASH_SESS_STABLE wait=${CRASH_SESS_WAIT}s files ${CRASH_SESS_BEFORE_COUNT}->${CRASH_SESS_AFTER_COUNT} basis='$CRASH_SESS_BASIS'"
  echo "recovery: title=$RECOVERY_TITLE body=$RECOVERY_BODY exit=$RECOVERY_EXIT"
  echo "policy_decisions:"
  for log in "${LOG_PATHS[@]}"; do
    echo "-- $(basename "$log")"
    grep 'BROWSER_CLI_PRESENTATION' "$log" 2>/dev/null || true
  done
} > "$EVIDENCE_DIR/summary.txt"
shasum -a 256 "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/sha256.txt" 2>/dev/null || true

if [ "$FAILED" = "1" ] || [ "$HARNESS_RC" -ne 0 ]; then
  printf 'RESTART-CRASH-CHECK: FAIL (evidence kept: %s)\n' "$EVIDENCE_DIR"
  KEEP_TMP=1
  exit 1
fi

printf 'RESTART-CRASH-CHECK: PASS (evidence: %s)\n' "$EVIDENCE_DIR"
