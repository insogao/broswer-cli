#!/usr/bin/env bash
# Browser CLI Gate A evidence: cookie write/read and temporary-profile isolation.
#
# Proves, in ONE isolated test invocation (two short, fully isolated browser
# launches with different throwaway profiles), that:
#   1. a cookie written by a page in profile A is sent back by the browser on a
#      later request in the SAME profile (write + read), persisted to the
#      profile's Cookie store;
#   2. a brand-new mktemp profile B does not inherit that cookie: the same page
#      reports it ABSENT, the server sees no Cookie header, and profile B's
#      Cookie store has no such row;
#   3. desktop visible windows stay 0 for both launches and every presentation
#      attempt is denied by the policy (structured logs).
#
# The "different profile" launch is part of this single test invocation; each
# launch is individually isolated (`--use-mock-keychain` + its own
# `mktemp -d` --user-data-dir). No real keychain access, no system dialogs, no
# visible windows, no show-then-hide/offscreen/minimize tricks.
#
# Usage: tests/phase2-cookie-profile-isolation.sh [--run] [--keep-tmp]
#          [--timeout SECONDS] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'COOKIE-CHECK: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

SERVER_PY="$SCRIPT_DIR/tools/cookie-test-server.py"

RUN=0
KEEP_TMP=0
TIMEOUT_SECONDS="${COOKIE_CHECK_TIMEOUT_SECONDS:-300}"
# Chromium's persistent cookie store commits on a ~30s timer; keep the browser
# alive this long after the cookie change so the row reaches disk.
COMMIT_WAIT_SECONDS="${COOKIE_CHECK_COMMIT_WAIT:-35}"

usage() {
  cat <<'EOF'
Usage: tests/phase2-cookie-profile-isolation.sh [options]

Gate A evidence: cookie write/read in one temp profile + isolation from a
different temp profile, with desktop windows staying 0. Default is dry-run;
--run performs ONE isolated test invocation (two throwaway-profile launches).

Options:
  --run             actually launch the signed browser (no visible window)
  --keep-tmp        keep profile/temp dirs and evidence inputs
  --timeout SECONDS max wait per browser step (default 300)
  --commit-wait S   seconds to keep the browser alive after setting the cookie
                    so Chromium's ~30s cookie commit timer writes it to disk
                    (default 35)
  --help            this message

Requires a Developer-ID-signed out/Dev/Browser CLI.app (scripts/build.sh).
Never touches the real keychain; a local 127.0.0.1 cookie server is used.
EOF
}

die() { printf 'COOKIE-CHECK: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[cookie-check] %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --run) RUN=1 ;;
    --keep-tmp) KEEP_TMP=1 ;;
    --timeout) shift; [ $# -gt 0 ] || die "--timeout needs a value"; TIMEOUT_SECONDS="$1" ;;
    --commit-wait) shift; [ $# -gt 0 ] || die "--commit-wait needs a value"; COMMIT_WAIT_SECONDS="$1" ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

case "$COMMIT_WAIT_SECONDS" in
  ''|*[!0-9]*) die "--commit-wait must be a non-negative integer (got '$COMMIT_WAIT_SECONDS')" ;;
esac

pick_python() {
  if [ -x "$DEPOT_TOOLS/python-bin/python3" ]; then
    printf '%s\n' "$DEPOT_TOOLS/python-bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    die "python3 not found (needed for the local cookie test server)"
  fi
}
PYTHON="$(pick_python)"
# The depot_tools `python-bin/python3` entry can be a wrapper that spawns the
# real interpreter as a child; killing only the wrapper would orphan the
# server. Resolve the real interpreter so cleanup can terminate it.
PYTHON_REAL="$("$PYTHON" -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
if [ -n "$PYTHON_REAL" ] && [ -x "$PYTHON_REAL" ]; then
  PYTHON="$PYTHON_REAL"
fi

[ -f "$SERVER_PY" ] || die "missing local cookie test server: $SERVER_PY"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-cookie.XXXXXX")"
PROFILE_A="$TMP_DIR/profile-a"
PROFILE_B="$TMP_DIR/profile-b"
mkdir -p "$PROFILE_A" "$PROFILE_B"
LOG_A="$TMP_DIR/chromium-a.log"
LOG_B="$TMP_DIR/chromium-b.log"
LIST_A="$TMP_DIR/devtools-list-a.json"
LIST_B="$TMP_DIR/devtools-list-b.json"
SAMPLES_A="$TMP_DIR/window-samples-a.txt"
SAMPLES_B="$TMP_DIR/window-samples-b.txt"
SERVER_LOG="$TMP_DIR/cookie-server.log"
SERVER_PORT_FILE="$TMP_DIR/cookie-server.port"
CHECK_LOG="$TMP_DIR/check-results.txt"
: > "$SERVER_LOG"
: > "$CHECK_LOG"

case "$PROFILE_A:$PROFILE_B" in
  "$TMP_DIR"/*:"$TMP_DIR"/*) : ;;
  *) die "refusing to run: profiles must live under '$TMP_DIR'" ;;
esac

RUN_VALUE="profA_$(date +%Y%m%d%H%M%S)"
COOKIE_NAME="bc_cookie"
TITLE_SET="COOKIE_SET_$RUN_VALUE"
TITLE_READ="COOKIE_READ_$RUN_VALUE"
TITLE_ABSENT="COOKIE_READ_ABSENT"

if [ "$RUN" != "1" ]; then
  cat <<EOF
[dry-run] product identity: '$BROWSER_CLI_PRODUCT_NAME' (team $BROWSER_CLI_TEAM_ID)
[dry-run] app path: $APP_DIR (must be Developer-ID signed)
[dry-run] local server: $PYTHON $SERVER_PY (127.0.0.1, ephemeral port)
[dry-run] phase A: launch with profile A (mktemp), set '$COOKIE_NAME=$RUN_VALUE',
          read it back in the same profile (server + DevTools + Cookie store evidence)
[dry-run] phase B: launch with a NEW mktemp profile B, read -> expect ABSENT
          (no inheritance from profile A)
[dry-run] both launches use: --no-startup-window --use-mock-keychain
          --user-data-dir=<their own mktemp dir> --remote-debugging-port=0
          --browser-cli-background-presentation
[dry-run] phase A stays alive ${COMMIT_WAIT_SECONDS}s after setting the cookie so Chromium's
          ~30s cookie commit timer writes the row to disk (windows sampled throughout)
[dry-run] sample on-screen windows for the PID (must stay 0) and assert policy
          suppress/deny logs; evidence copied to build-logs/
[dry-run] nothing executed; pass --run to execute (one isolated invocation).
EOF
  rm -rf "$TMP_DIR"
  exit 0
fi

[ -x "$BIN" ] || die "browser binary not found/executable: $BIN (set CHROMIUM_ROOT/OUT_NAME)"
[ -d "$APP_DIR" ] || die "product app not found: $APP_DIR"

# No ad-hoc build may be launched or tested (read-only codesign query).
APP_SIG_OK=0
APP_SIG="$(codesign -dv --verbose=4 "$APP_DIR" 2>&1 || true)"
case "$APP_SIG" in
  *"TeamIdentifier=$BROWSER_CLI_TEAM_ID"*) APP_SIG_OK=1; note "app signature OK: Developer ID (team $BROWSER_CLI_TEAM_ID)" ;;
  *"Signature=adhoc"*) die "refusing to test an ad-hoc build; the app must be Developer-ID signed" ;;
  *) die "refusing to test: $APP_DIR is not signed with the pinned Developer ID identity" ;;
esac

note "compiling on-screen window counter"
HELPER_SRC="$SCRIPT_DIR/tools/onscreen-window-count.m"
HELPER="$TMP_DIR/onscreen-window-count"
xcrun clang -fobjc-arc -framework Foundation -framework CoreGraphics \
  -o "$HELPER" "$HELPER_SRC"
[ -x "$HELPER" ] || die "failed to compile window counter"

note "starting local cookie test server"
"$PYTHON" "$SERVER_PY" "$SERVER_PORT_FILE" "$SERVER_LOG" >/dev/null 2>&1 &
SERVER_PID=$!
SERVER_WAITED=0
while [ ! -s "$SERVER_PORT_FILE" ]; do
  kill -0 "$SERVER_PID" 2>/dev/null || die "cookie test server exited early"
  [ "$SERVER_WAITED" -lt 50 ] || die "cookie test server did not report a port"
  sleep 0.2
  SERVER_WAITED=$((SERVER_WAITED + 1))
done
SERVER_PORT="$(head -1 "$SERVER_PORT_FILE" | tr -d '[:space:]')"
note "cookie test server port=$SERVER_PORT"

BROWSER_PID=""
BROWSER_PORT=""
BROWSER_CMDLINE=""
STOPPED=0

stop_browser() {
  [ -n "$BROWSER_PID" ] || return 0
  [ "$STOPPED" = "1" ] && return 0
  if kill -0 "$BROWSER_PID" 2>/dev/null; then
    note "stopping browser pid=$BROWSER_PID (graceful SIGTERM; no window was ever shown)"
    kill -TERM "$BROWSER_PID" 2>/dev/null || true
    for _ in $(seq 1 60); do
      kill -0 "$BROWSER_PID" 2>/dev/null || break
      sleep 0.5
    done
    kill -KILL "$BROWSER_PID" 2>/dev/null || true
  fi
  wait "$BROWSER_PID" 2>/dev/null || true
  BROWSER_PID=""
  STOPPED=1
}

cleanup() {
  stop_browser
  if [ -n "${SERVER_PID:-}" ]; then
    # Kill children first (a python wrapper may own the real interpreter),
    # then the launcher itself; escalate to KILL and verify.
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

start_browser() { # <profile> <logfile> ; sets BROWSER_PID/BROWSER_PORT/BROWSER_CMDLINE
  local profile="$1" logfile="$2"
  local cmd=(
    "$BIN"
    --no-startup-window
    --no-first-run
    --no-default-browser-check
    --disable-background-networking
    --disable-component-update
    --disable-sync
    --disable-extensions
    --use-mock-keychain
    --user-data-dir="$profile"
    --remote-debugging-port=0
    --enable-logging=stderr
    --browser-cli-background-presentation
  )
  STOPPED=0
  "${cmd[@]}" >"$logfile" 2>&1 &
  BROWSER_PID=$!
  local port_file="$profile/DevToolsActivePort"
  local waited=0
  while [ ! -s "$port_file" ]; do
    if ! kill -0 "$BROWSER_PID" 2>/dev/null; then
      local rc=0
      wait "$BROWSER_PID" 2>/dev/null || rc=$?
      die "browser exited early (rc=$rc); log tail:
$(tail -5 "$logfile" 2>/dev/null || true)"
    fi
    local state
    state="$(ps -o stat= -p "$BROWSER_PID" 2>/dev/null | tr -d ' ')"
    case "$state" in
      Z*|'')
        local rc=0
        wait "$BROWSER_PID" 2>/dev/null || rc=$?
        die "browser terminated before DevTools was ready (rc=$rc, state='$state')"
        ;;
    esac
    [ "$waited" -lt "$TIMEOUT_SECONDS" ] || die "DevToolsActivePort not written within ${TIMEOUT_SECONDS}s"
    sleep 1
    waited=$((waited + 1))
  done
  BROWSER_PORT="$(head -1 "$port_file" | tr -d '[:space:]')"
  BROWSER_CMDLINE="$(ps -ww -o command= -p "$BROWSER_PID" 2>/dev/null || true)"
  note "browser port=$BROWSER_PORT pid=$BROWSER_PID"
}

encode_url() {
  local url="$1"
  url="${url//:/%3A}"
  url="${url//\//%2F}"
  url="${url//\?/%3F}"
  url="${url//=/%3D}"
  url="${url//&/%26}"
  printf '%s' "$url"
}

WAIT_MAX=0
wait_with_window_samples() { # <seconds> <samples-file> <pid> ; sets WAIT_MAX
  local secs="$1" samples="$2" pid="$3" i=0 windows
  WAIT_MAX=0
  while [ "$i" -lt "$secs" ]; do
    windows="$("$HELPER" "$pid")"
    echo "t=commit_wait_${i}s windows=$windows" >> "$samples"
    if [ "$windows" -gt "$WAIT_MAX" ]; then WAIT_MAX="$windows"; fi
    sleep 1
    i=$((i + 1))
  done
}

find_cookie_row() { # <profile> ; echoes "<host>|<name>" rows for $COOKIE_NAME (may be empty)
  local profile="$1" db=""
  db="$(find "$profile" -type f -name 'Cookies' 2>/dev/null | head -1)"
  [ -n "$db" ] || return 0
  sqlite3 "$db" "select host_key||'|'||name from cookies where name='$COOKIE_NAME';" 2>/dev/null || true
}

wait_cookie_row() { # <profile> <timeout-seconds> ; echoes row when it appears (may be empty on timeout)
  local profile="$1" limit="$2" waited=0 row=""
  while [ "$waited" -lt "$limit" ]; do
    row="$(find_cookie_row "$profile")"
    if [ -n "$row" ]; then
      printf '%s\n' "$row"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 0
}

SET_URL="http://127.0.0.1:$SERVER_PORT/set?name=$COOKIE_NAME&value=$RUN_VALUE"
READ_URL="http://127.0.0.1:$SERVER_PORT/read?name=$COOKIE_NAME"
SET_URL_ENC="$(encode_url "$SET_URL")"
READ_URL_ENC="$(encode_url "$READ_URL")"

wait_for_title() { # <port> <listfile> <pattern> <samples-file> <pid> ; sets WAIT_MAX
  local port="$1" listfile="$2" pattern="$3" samples="$4" pid="$5"
  local waited=0 windows list
  WAIT_MAX=0
  while [ "$waited" -lt "$TIMEOUT_SECONDS" ]; do
    list="$(curl -sS "http://127.0.0.1:$port/json/list" 2>/dev/null || true)"
    printf '%s' "$list" > "$listfile"
    windows="$("$HELPER" "$pid")"
    echo "t=${waited}s windows=$windows" >> "$samples"
    if [ "$windows" -gt "$WAIT_MAX" ]; then WAIT_MAX="$windows"; fi
    case "$list" in
      *"$pattern"*) return 0 ;;
    esac
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

# ---------------------------------------------------------------- phase A
note "phase A: write + read cookie in profile A ($COOKIE_NAME=$RUN_VALUE)"
: > "$SAMPLES_A"
start_browser "$PROFILE_A" "$LOG_A"
CMDLINE_A="$BROWSER_CMDLINE"
PORT_A="$BROWSER_PORT"
WINDOWS_A_BEFORE="$("$HELPER" "$BROWSER_PID")"
echo "before_target=$WINDOWS_A_BEFORE" >> "$SAMPLES_A"
MAX_A="$WINDOWS_A_BEFORE"
SET_OK=0
READ_OK=0
curl -sS -X PUT "http://127.0.0.1:$PORT_A/json/new?$SET_URL_ENC" >/dev/null 2>&1 || true
if wait_for_title "$PORT_A" "$LIST_A" "$TITLE_SET" "$SAMPLES_A" "$BROWSER_PID"; then SET_OK=1; fi
[ "$WAIT_MAX" -gt "$MAX_A" ] && MAX_A="$WAIT_MAX"
curl -sS -X PUT "http://127.0.0.1:$PORT_A/json/new?$READ_URL_ENC" >/dev/null 2>&1 || true
if wait_for_title "$PORT_A" "$LIST_A" "$TITLE_READ" "$SAMPLES_A" "$BROWSER_PID"; then READ_OK=1; fi
[ "$WAIT_MAX" -gt "$MAX_A" ] && MAX_A="$WAIT_MAX"
echo "after_load=$("$HELPER" "$BROWSER_PID")" >> "$SAMPLES_A"
URL_A_OK=0
case "$(cat "$LIST_A" 2>/dev/null || true)" in
  *"http://127.0.0.1:$SERVER_PORT/read?name=$COOKIE_NAME"*) URL_A_OK=1 ;;
esac
# Keep the browser alive so Chromium's ~30s cookie commit timer writes the row
# to disk; window sampling continues during the wait.
note "waiting ${COMMIT_WAIT_SECONDS}s for the cookie store commit (windows still sampled)"
wait_with_window_samples "$COMMIT_WAIT_SECONDS" "$SAMPLES_A" "$BROWSER_PID"
[ "$WAIT_MAX" -gt "$MAX_A" ] && MAX_A="$WAIT_MAX"
stop_browser

note "waiting for the persisted cookie row in profile A"
COOKIE_ROW_A="$(wait_cookie_row "$PROFILE_A" 15)"
echo "$COOKIE_ROW_A" > "$TMP_DIR/cookies-a.txt"
SERVER_LOG_COUNT_A="$(wc -l < "$SERVER_LOG" | tr -d ' ')"

# ---------------------------------------------------------------- phase B
note "phase B: fresh mktemp profile B must NOT inherit the cookie"
: > "$SAMPLES_B"
start_browser "$PROFILE_B" "$LOG_B"
CMDLINE_B="$BROWSER_CMDLINE"
PORT_B="$BROWSER_PORT"
WINDOWS_B_BEFORE="$("$HELPER" "$BROWSER_PID")"
echo "before_target=$WINDOWS_B_BEFORE" >> "$SAMPLES_B"
MAX_B="$WINDOWS_B_BEFORE"
ABSENT_OK=0
curl -sS -X PUT "http://127.0.0.1:$PORT_B/json/new?$READ_URL_ENC" >/dev/null 2>&1 || true
if wait_for_title "$PORT_B" "$LIST_B" "$TITLE_ABSENT" "$SAMPLES_B" "$BROWSER_PID"; then ABSENT_OK=1; fi
[ "$WAIT_MAX" -gt "$MAX_B" ] && MAX_B="$WAIT_MAX"
echo "after_load=$("$HELPER" "$BROWSER_PID")" >> "$SAMPLES_B"
URL_B_OK=0
case "$(cat "$LIST_B" 2>/dev/null || true)" in
  *"http://127.0.0.1:$SERVER_PORT/read?name=$COOKIE_NAME"*) URL_B_OK=1 ;;
esac
stop_browser

sleep 1
COOKIE_ROW_B="$(find_cookie_row "$PROFILE_B")"
echo "$COOKIE_ROW_B" > "$TMP_DIR/cookies-b.txt"

# server-side HTTP evidence
SERVER_READ_A_LINES="$(grep -F "GET /read?name=$COOKIE_NAME" "$SERVER_LOG" 2>/dev/null | head -1 || true)"
SERVER_READ_B_LINES="$(tail -n +"$((SERVER_LOG_COUNT_A + 1))" "$SERVER_LOG" 2>/dev/null | grep -F "GET /read?name=$COOKIE_NAME" | head -1 || true)"
COOKIE_SENT_A=0
case "$SERVER_READ_A_LINES" in *"cookie=$COOKIE_NAME=$RUN_VALUE"*) COOKIE_SENT_A=1 ;; esac
COOKIE_SENT_B=0
case "$SERVER_READ_B_LINES" in *"cookie=$COOKIE_NAME="*) COOKIE_SENT_B=1 ;; esac

# structured policy logs (both phases)
POLICY_OK=1
for log in "$LOG_A" "$LOG_B"; do
  grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$log" || POLICY_OK=0
  grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*origin=internal' "$log" || POLICY_OK=0
done
NO_ALLOW_OK=1
for log in "$LOG_A" "$LOG_B"; do
  grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$log" && NO_ALLOW_OK=0
done

# ---------------------------------------------------------------- checks
FAILED=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then
    printf 'cookie.check name=%s result=PASS detail=%s\n' "$1" "$3" | tee -a "$CHECK_LOG"
  else
    printf 'cookie.check name=%s result=FAIL expected=%s actual=%s\n' "$1" "$2" "$3" | tee -a "$CHECK_LOG"
    FAILED=1
  fi
}

check "app-developer-id-signed" "1" "$APP_SIG_OK"
check "phase-a-cmdline-mock-keychain" "yes" \
  "$(if printf '%s' "$CMDLINE_A" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "phase-a-cmdline-isolated-profile" "yes" \
  "$(if printf '%s' "$CMDLINE_A" | grep -qF -- "--user-data-dir=$PROFILE_A"; then echo yes; else echo no; fi)"
check "phase-b-cmdline-mock-keychain" "yes" \
  "$(if printf '%s' "$CMDLINE_B" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "phase-b-cmdline-isolated-profile" "yes" \
  "$(if printf '%s' "$CMDLINE_B" | grep -qF -- "--user-data-dir=$PROFILE_B"; then echo yes; else echo no; fi)"
check "local-url-loaded-phase-a" "1" "$URL_A_OK"
check "local-url-loaded-phase-b" "1" "$URL_B_OK"
check "same-profile-cookie-set" "1" "$SET_OK"
check "same-profile-cookie-read" "1" "$READ_OK"
check "same-profile-cookie-sent-to-server" "1" "$COOKIE_SENT_A"
check "same-profile-cookie-persisted-on-disk" "yes" \
  "$(if printf '%s' "$COOKIE_ROW_A" | grep -qF "127.0.0.1|$COOKIE_NAME"; then echo yes; else echo no; fi)"
check "different-profile-cookie-absent" "1" "$ABSENT_OK"
check "different-profile-cookie-not-sent" "no" "$([ "$COOKIE_SENT_B" = "1" ] && echo yes || echo no)"
check "different-profile-cookie-not-persisted" "no" \
  "$(if printf '%s' "$COOKIE_ROW_B" | grep -qF "$COOKIE_NAME"; then echo yes; else echo no; fi)"
check "windows-zero-phase-a" "0" "$MAX_A"
check "windows-zero-phase-b" "0" "$MAX_B"
check "policy-suppress-and-deny" "1" "$POLICY_OK"
check "no-internal-allow-in-background" "1" "$NO_ALLOW_OK"

# ---------------------------------------------------------------- evidence
EVIDENCE_DIR="$REPO_ROOT/build-logs/phase2-cookie-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$EVIDENCE_DIR"
cp "$LOG_A" "$EVIDENCE_DIR/chromium-a.log" 2>/dev/null || true
cp "$LOG_B" "$EVIDENCE_DIR/chromium-b.log" 2>/dev/null || true
cp "$LIST_A" "$EVIDENCE_DIR/devtools-list-a.json" 2>/dev/null || true
cp "$LIST_B" "$EVIDENCE_DIR/devtools-list-b.json" 2>/dev/null || true
cp "$SAMPLES_A" "$EVIDENCE_DIR/window-samples-a.txt" 2>/dev/null || true
cp "$SAMPLES_B" "$EVIDENCE_DIR/window-samples-b.txt" 2>/dev/null || true
cp "$SERVER_LOG" "$EVIDENCE_DIR/cookie-server.log" 2>/dev/null || true
cp "$TMP_DIR/cookies-a.txt" "$EVIDENCE_DIR/cookies-a.txt" 2>/dev/null || true
cp "$TMP_DIR/cookies-b.txt" "$EVIDENCE_DIR/cookies-b.txt" 2>/dev/null || true
DB_A="$(find "$PROFILE_A" -type f -name 'Cookies' 2>/dev/null | head -1)"
DB_B="$(find "$PROFILE_B" -type f -name 'Cookies' 2>/dev/null | head -1)"
[ -n "$DB_A" ] && cp "$DB_A" "$EVIDENCE_DIR/cookies-a.db" 2>/dev/null || true
[ -n "$DB_B" ] && cp "$DB_B" "$EVIDENCE_DIR/cookies-b.db" 2>/dev/null || true
{
  echo "phase_a_cmdline=$CMDLINE_A"
  echo "phase_b_cmdline=$CMDLINE_B"
  echo "cookie_name=$COOKIE_NAME"
  echo "cookie_value=$RUN_VALUE"
  echo "server_port=$SERVER_PORT"
  echo "phase_a_windows_max=$MAX_A"
  echo "phase_b_windows_max=$MAX_B"
  echo "phase_a_cookie_row=$COOKIE_ROW_A"
  echo "phase_b_cookie_row=$COOKIE_ROW_B"
  echo "phase_a_server_read=$SERVER_READ_A_LINES"
  echo "phase_b_server_read=$SERVER_READ_B_LINES"
} > "$EVIDENCE_DIR/summary.txt"
{
  echo "same-profile: set=$SET_OK read=$READ_OK sent=$COOKIE_SENT_A persisted=$COOKIE_ROW_A"
  echo "different-profile: absent=$ABSENT_OK sent=$COOKIE_SENT_B persisted='$COOKIE_ROW_B'"
  echo "policy_suppress_deny=$POLICY_OK no_internal_allow=$NO_ALLOW_OK"
  echo "windows_phase_a=$MAX_A windows_phase_b=$MAX_B"
} > "$EVIDENCE_DIR/outcome.txt"
cp "$CHECK_LOG" "$EVIDENCE_DIR/check-results.txt" 2>/dev/null || true
shasum -a 256 "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/sha256.txt" 2>/dev/null || true

if [ "$FAILED" = "1" ]; then
  printf 'COOKIE-CHECK: FAIL (evidence kept: %s)\n' "$EVIDENCE_DIR"
  KEEP_TMP=1
  exit 1
fi

printf 'COOKIE-CHECK: PASS (evidence: %s)\n' "$EVIDENCE_DIR"
