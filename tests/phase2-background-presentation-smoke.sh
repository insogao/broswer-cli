#!/usr/bin/env bash
# Browser CLI Phase 2 smoke: BACKGROUND_PRESENTATION / Gate A evidence.
#
# Proves, without ever opening a visible window:
#   1. a real browser window + renderer can be created and execute JS
#      (via the DevTools HTTP endpoint);
#   2. the managed window never reaches the desktop
#      (CGWindowList on-screen window count for the browser PID == 0);
#   3. every presentation attempt was denied by the policy
#      (structured BROWSER_CLI_PRESENTATION logs).
#
# Safety / test hygiene (mandatory, see AGENTS.md):
#   * default is dry-run; pass --run to actually launch the browser process;
#   * the browser always runs with `--use-mock-keychain` so it never reads or
#     writes the real login keychain and can never trigger a "Chromium Safe
#     Storage" password dialog;
#   * the browser always runs with a throwaway `mktemp -d` profile via
#     `--user-data-dir`; the user's real profile is never reused;
#   * this script never uses `open`, Dock, Launchpad or AppleScript, never
#     orders a window on screen, and must not require any system dialog.
#   * Ad-hoc signed dev builds have no stable keychain ACL; omitting
#     `--use-mock-keychain` would pop a system keychain prompt. That is a
#     blocking defect, not a warning.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"

# Product identity is a single versioned source; never hardcode the app name.
IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'SMOKE: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

RUN=0
KEEP_TMP=0
# Cold start after (re-)signing validates ~1600 component dylibs; the first
# launch can take several minutes. Allow 600s by default.
TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-600}"

# Expected renderer result: data URL that sets document.title to
# "BROWSER_CLI_SMOKE_RENDERER_OK_42" only if the JS actually executed.
RAW_URL="data:text/html,<script>document.title='BROWSER_CLI_SMOKE_RENDERER_OK_'+(6*7)</script>"
ENCODED_URL="${RAW_URL//</%3C}"
ENCODED_URL="${ENCODED_URL//>/%3E}"
ENCODED_URL="${ENCODED_URL//\'/%27}"
ENCODED_URL="${ENCODED_URL//(/%28}"
ENCODED_URL="${ENCODED_URL//)/%29}"
EXPECTED_TITLE="BROWSER_CLI_SMOKE_RENDERER_OK_42"

usage() {
  cat <<'EOF'
Usage: tests/phase2-background-presentation-smoke.sh [options]

Gate A smoke for Phase 2 BACKGROUND_PRESENTATION. Default is dry-run.

Options:
  --run             actually launch the browser process (no visible window)
  --keep-tmp        keep profile/log/temp dirs for inspection
  --timeout SECONDS max wait for the browser and renderer (default 600;
                    the first launch after signing can be slow)
  --help            this message

Environment overrides: CHROMIUM_ROOT, OUT_NAME, SMOKE_TIMEOUT_SECONDS.

Requires: a browser-cli patched Chromium build in out/Dev (see docs), `curl`,
and the Xcode command line tools (to compile the CGWindowList helper).
Never uses `open`, Dock/Launchpad automation, or any visible window. The
browser is launched with --use-mock-keychain and an isolated mktemp
--user-data-dir so it cannot touch the real login keychain or prompt for a
password (static gate: tests/check-browser-launch-hygiene.sh).
EOF
}

die() { printf 'SMOKE: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[smoke] %s\n' "$*"; }

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

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-smoke.XXXXXX")"
PROFILE_DIR="$TMP_DIR/profile"
LOG="$TMP_DIR/chromium.log"
HELPER_SRC="$SCRIPT_DIR/tools/onscreen-window-count.m"
HELPER="$TMP_DIR/onscreen-window-count"

# Hard guard: the profile must be a throwaway directory inside this run's temp
# dir. Never fall back to (or reuse) the user's real Chromium profile.
case "$PROFILE_DIR" in
  "$TMP_DIR"/*) : ;;
  *) die "refusing to run: profile dir '$PROFILE_DIR' is not inside '$TMP_DIR'" ;;
esac

LAUNCH_CMD=(
  "$BIN"
  --no-startup-window
  --no-first-run
  --no-default-browser-check
  --disable-background-networking
  --disable-component-update
  --disable-sync
  --disable-extensions
  --use-mock-keychain
  --user-data-dir="$PROFILE_DIR"
  --remote-debugging-port=0
  --enable-logging=stderr
  --browser-cli-background-presentation
)

if [ "$RUN" != "1" ]; then
  cat <<EOF
[dry-run] product identity: '$BROWSER_CLI_PRODUCT_NAME' (team $BROWSER_CLI_TEAM_ID)
[dry-run] app path: $APP_DIR (must already be Developer-ID signed; build may not exist yet)
[dry-run] would compile window-list helper:
  xcrun clang -fobjc-arc -framework Foundation -framework CoreGraphics -o $HELPER $HELPER_SRC
[dry-run] would launch (no visible window, no open/Dock/Launchpad):
  ${LAUNCH_CMD[*]}
[dry-run] then: PUT /json/new?<data-url>, poll /json/list for title,
[dry-run] assert on-screen window count == 0, assert policy deny logs, kill PID.
[dry-run] nothing executed; pass --run to execute.
EOF
  rm -rf "$TMP_DIR"
  exit 0
fi

[ -x "$BIN" ] || die "browser binary not found/executable: $BIN (set CHROMIUM_ROOT/OUT_NAME; run scripts/build.sh)"

# No ad-hoc build may be launched or tested: require the pinned Developer ID
# signature (read-only codesign query; no keychain access, no app launch).
APP_SIG="$(codesign -dv --verbose=4 "$APP_DIR" 2>&1 || true)"
case "$APP_SIG" in
  *"TeamIdentifier=$BROWSER_CLI_TEAM_ID"*)
    note "app signature OK: Developer ID (team $BROWSER_CLI_TEAM_ID)"
    ;;
  *"Signature=adhoc"*)
    die "refusing to test an ad-hoc build; run scripts/build.sh (mandatory signing, no fallback)"
    ;;
  *)
    die "refusing to test: $APP_DIR is not signed with the pinned Developer ID identity"
    ;;
esac

CHROME_PID=""
STOPPED=0

stop_browser() {
  [ -n "$CHROME_PID" ] || return 0
  [ "$STOPPED" = "1" ] && return 0
  if kill -0 "$CHROME_PID" 2>/dev/null; then
    note "stopping browser pid=$CHROME_PID (graceful SIGTERM; no windows were created)"
    kill -TERM "$CHROME_PID" 2>/dev/null || true
    for _ in $(seq 1 40); do
      kill -0 "$CHROME_PID" 2>/dev/null || break
      sleep 0.5
    done
    kill -KILL "$CHROME_PID" 2>/dev/null || true
  fi
  STOPPED=1
}

cleanup() {
  stop_browser
  if [ "$KEEP_TMP" = "1" ]; then
    note "keeping temp dir: $TMP_DIR"
  else
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

note "compiling on-screen window counter"
xcrun clang -fobjc-arc -framework Foundation -framework CoreGraphics \
  -o "$HELPER" "$HELPER_SRC"
[ -x "$HELPER" ] || die "failed to compile window counter"

note "launching browser (no-startup-window, managed background presentation)"
note "command: ${LAUNCH_CMD[*]}"
"${LAUNCH_CMD[@]}" >"$LOG" 2>&1 &
CHROME_PID=$!
note "browser pid=$CHROME_PID log=$LOG"

PORT_FILE="$PROFILE_DIR/DevToolsActivePort"
WAITED=0
while [ ! -s "$PORT_FILE" ]; do
  if ! kill -0 "$CHROME_PID" 2>/dev/null; then
    wait "$CHROME_PID" 2>/dev/null; rc=$?
    die "browser exited early (rc=$rc); log tail:
$(tail -5 "$LOG" 2>/dev/null || true)"
  fi
  # kill -0 also succeeds for a zombie; detect and report precisely.
  STATE="$(ps -o stat= -p "$CHROME_PID" 2>/dev/null | tr -d ' ')"
  case "$STATE" in
    Z*|'')
      wait "$CHROME_PID" 2>/dev/null; rc=$?
      die "browser terminated before DevTools was ready (rc=$rc, state='$STATE'); log tail:
$(tail -5 "$LOG" 2>/dev/null || true)"
      ;;
  esac
  if [ "$WAITED" -ge "$TIMEOUT_SECONDS" ]; then
    die "DevToolsActivePort not written within ${TIMEOUT_SECONDS}s (state='$STATE'; cold-start signature validation can be slow)"
  fi
  if [ $((WAITED % 30)) -eq 0 ] && [ "$WAITED" -gt 0 ]; then
    note "still waiting for DevToolsActivePort (${WAITED}s; cold start validates signed dylibs)"
  fi
  sleep 1
  WAITED=$((WAITED + 1))
done
PORT="$(head -1 "$PORT_FILE" | tr -d '[:space:]')"
note "DevTools port=$PORT"

# Capture the live command line: the two isolation flags must be present in the
# real running process, not only in this script.
CMDLINE="$(ps -ww -o command= -p "$CHROME_PID" 2>/dev/null || true)"
note "browser cmdline: $CMDLINE"

WINDOWS_BEFORE="$("$HELPER" "$CHROME_PID")"
note "on-screen windows before target creation: $WINDOWS_BEFORE"

note "creating background target via DevTools HTTP"
CREATE_RESPONSE="$(curl -sS -X PUT \
  "http://127.0.0.1:$PORT/json/new?$ENCODED_URL" 2>/dev/null || true)"
[ -n "$CREATE_RESPONSE" ] || note "warning: /json/new returned no body (continuing; target list decides)"

TITLE_OK=0
WAITED=0
while [ "$WAITED" -lt "$TIMEOUT_SECONDS" ]; do
  if curl -sS "http://127.0.0.1:$PORT/json/list" 2>/dev/null | grep -q "$EXPECTED_TITLE"; then
    TITLE_OK=1
    break
  fi
  sleep 1
  WAITED=$((WAITED + 1))
done
[ "$TITLE_OK" = "1" ] || die "renderer did not produce expected title '$EXPECTED_TITLE' (JS/page load failed)"

WINDOWS_AFTER="$("$HELPER" "$CHROME_PID")"
note "on-screen windows after target creation: $WINDOWS_AFTER"

# Stop the browser before grepping the log so all buffered lines are flushed.
stop_browser

FAILED=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then
    printf 'smoke.check name=%s result=PASS detail=%s\n' "$1" "$3"
  else
    printf 'smoke.check name=%s result=FAIL expected=%s actual=%s\n' "$1" "$2" "$3"
    FAILED=1
  fi
}

# Gate A core: desktop zero display.
check "desktop-onscreen-windows-zero" "0" "$WINDOWS_AFTER"

# Test hygiene: the live process must be keychain-isolated and profile-isolated.
check "cmdline-has-mock-keychain" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "cmdline-has-isolated-user-data-dir" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -qF -- "--user-data-dir=$PROFILE_DIR"; then echo yes; else echo no; fi)"

# Renderer really ran and loaded.
check "renderer-js-title" "yes" "$([ "$TITLE_OK" = "1" ] && echo yes || echo no)"

# Policy was armed at window creation.
check "host-suppressed-at-creation" "yes" \
  "$(if grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$LOG"; then echo yes; else echo no; fi)"

# Internal presentation attempts were denied.
check "policy-denied-internal-show" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*origin=internal' "$LOG"; then echo yes; else echo no; fi)"

# No internal presentation was ever allowed while in background state.
check "no-internal-allow-in-background" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$LOG"; then echo no; else echo yes; fi)"

note "policy decisions seen:"
grep 'BROWSER_CLI_PRESENTATION' "$LOG" | sed 's/^/  /' || true

if [ "$FAILED" = "1" ]; then
  printf 'SMOKE: FAIL (log: %s)\n' "$LOG"
  KEEP_TMP=1
  exit 1
fi

printf 'SMOKE: PASS (Gate A background presentation slice; log: %s)\n' "$LOG"
