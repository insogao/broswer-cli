#!/usr/bin/env bash
# Browser CLI Gate A evidence: real HTTPS response in BACKGROUND.
#
# Proves, in one isolated run of the signed product app:
#   1. a real BrowserWindow loads a stable public HTTPS site and completes the
#      network response (DevTools target url + DOM-derived title);
#   2. HTTP-level evidence is recorded independently via Chromium NetLog
#      (request URL + response status line);
#   3. the desktop never shows a Browser CLI window during the whole run
#      (CGWindowList samples for the browser PID must stay 0);
#   4. every presentation attempt is denied by the policy
#      (structured BROWSER_CLI_PRESENTATION logs; no internal allow in background).
#
# Safety / hygiene (mandatory, see AGENTS.md):
#   * default is dry-run; pass --run for ONE isolated run;
#   * signed Developer-ID app only (ad-hoc is refused);
#   * `--use-mock-keychain` + throwaway `mktemp -d` profile; the real keychain is
#     never touched and no system dialog is expected;
#   * never uses `open`, Dock/Launchpad or AppleScript; never orders a window on
#     screen, never moves windows offscreen, never minimizes to hide;
#   * if anything looks like a system prompt, stop and fail closed.
#
# Usage: tests/phase2-https-background-network.sh [--run] [--keep-tmp]
#          [--url URL] [--timeout SECONDS] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"

# Product identity is a single versioned source; never hardcode the app name.
IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'HTTPS-CHECK: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

PROXY_URL="${PROXY_URL:-http://127.0.0.1:10808}"

RUN=0
KEEP_TMP=0
TIMEOUT_SECONDS="${HTTPS_CHECK_TIMEOUT_SECONDS:-600}"
TARGET_URL="https://example.com/"
EXPECTED_TITLE="Example Domain"

usage() {
  cat <<'EOF'
Usage: tests/phase2-https-background-network.sh [options]

Gate A evidence: real HTTPS response in BACKGROUND, desktop windows stay 0.
Default is dry-run; --run performs ONE isolated run.

Options:
  --run             actually launch the signed browser (no visible window)
  --keep-tmp        keep the temp dir (profile/netlog/log)
  --url URL         HTTPS URL to load (default: https://example.com/)
  --timeout SECONDS max wait for the target (default 600)
  --help            this message

Requires a Developer-ID-signed out/Dev/Browser CLI.app (scripts/build.sh).
Never uses open/Dock/Launchpad; never touches the real keychain.
EOF
}

die() { printf 'HTTPS-CHECK: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[https-check] %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --run) RUN=1 ;;
    --keep-tmp) KEEP_TMP=1 ;;
    --url) shift; [ $# -gt 0 ] || die "--url needs a value"; TARGET_URL="$1" ;;
    --timeout) shift; [ $# -gt 0 ] || die "--timeout needs a value"; TIMEOUT_SECONDS="$1" ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

case "$TARGET_URL" in
  https://*) : ;;
  *) die "--url must be an https:// URL (got '$TARGET_URL')" ;;
esac

# /json/new expects the URL percent-encoded in the query string.
ENCODED_URL="$TARGET_URL"
ENCODED_URL="${ENCODED_URL//:/%3A}"
ENCODED_URL="${ENCODED_URL//\//%2F}"
ENCODED_URL="${ENCODED_URL//\?/%3F}"
ENCODED_URL="${ENCODED_URL//=/%3D}"
ENCODED_URL="${ENCODED_URL//&/%26}"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-https.XXXXXX")"
PROFILE_DIR="$TMP_DIR/profile"
LOG="$TMP_DIR/chromium.log"
NETLOG="$TMP_DIR/netlog.json"
HELPER_SRC="$SCRIPT_DIR/tools/onscreen-window-count.m"
HELPER="$TMP_DIR/onscreen-window-count"

# Hard guard: profile must be a throwaway directory inside this run's temp dir.
case "$PROFILE_DIR" in
  "$TMP_DIR"/*) : ;;
  *) die "refusing to run: profile dir '$PROFILE_DIR' is not inside '$TMP_DIR'" ;;
esac

if [ "$RUN" != "1" ]; then
  cat <<EOF
[dry-run] product identity: '$BROWSER_CLI_PRODUCT_NAME' (team $BROWSER_CLI_TEAM_ID)
[dry-run] app path: $APP_DIR (must be Developer-ID signed)
[dry-run] target URL: $TARGET_URL (expected title: $EXPECTED_TITLE)
[dry-run] would launch (no visible window, no open/Dock/Launchpad):
  "$BIN" --no-startup-window --no-first-run --no-default-browser-check \\
    --disable-background-networking --disable-component-update --disable-sync --disable-extensions \\
    --use-mock-keychain --user-data-dir=<mktemp -d profile> --remote-debugging-port=0 \\
    --enable-logging=stderr --log-net-log=<temp>/netlog.json --net-log-capture-mode=IncludeSensitive \\
    --browser-cli-background-presentation
[dry-run] then: PUT /json/new?<encoded-https-url>, poll /json/list for url+title,
[dry-run] sample on-screen windows (must stay 0), assert NetLog has URL + HTTP 200,
[dry-run] assert policy deny logs, SIGTERM, copy evidence to build-logs/.
[dry-run] nothing executed; pass --run to execute (one isolated run).
EOF
  rm -rf "$TMP_DIR"
  exit 0
fi

[ -x "$BIN" ] || die "browser binary not found/executable: $BIN (set CHROMIUM_ROOT/OUT_NAME; run scripts/build.sh)"
[ -d "$APP_DIR" ] || die "product app not found: $APP_DIR"

# No ad-hoc build may be launched or tested (read-only codesign query).
APP_SIG="$(codesign -dv --verbose=4 "$APP_DIR" 2>&1 || true)"
case "$APP_SIG" in
  *"TeamIdentifier=$BROWSER_CLI_TEAM_ID"*) note "app signature OK: Developer ID (team $BROWSER_CLI_TEAM_ID)" ;;
  *"Signature=adhoc"*) die "refusing to test an ad-hoc build; run scripts/build.sh (mandatory signing)" ;;
  *) die "refusing to test: $APP_DIR is not signed with the pinned Developer ID identity" ;;
esac

# Network preflight before spending the single browser run. Chrome uses the
# system proxy by default; the shell may need an explicit proxy for the host.
NET_MODE="direct"
DIRECT_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -m 20 --noproxy '*' "$TARGET_URL" 2>/dev/null || true)"
if [ "$DIRECT_CODE" = "200" ]; then
  note "network preflight: direct HTTPS to $TARGET_URL -> 200"
else
  PROXY_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -m 20 -x "$PROXY_URL" "$TARGET_URL" 2>/dev/null || true)"
  if [ "$PROXY_CODE" = "200" ]; then
    NET_MODE="proxy"
    note "network preflight: direct failed (code='$DIRECT_CODE'); via $PROXY_URL -> 200"
  else
    die "network preflight failed (direct='$DIRECT_CODE', proxy='$PROXY_CODE'); not launching the browser"
  fi
fi
CONTENT_OK=0
if [ "$NET_MODE" = "proxy" ]; then
  curl -sS -m 20 -x "$PROXY_URL" "$TARGET_URL" 2>/dev/null | grep -q "$EXPECTED_TITLE" && CONTENT_OK=1 || true
else
  curl -sS -m 20 --noproxy '*' "$TARGET_URL" 2>/dev/null | grep -q "$EXPECTED_TITLE" && CONTENT_OK=1 || true
fi
[ "$CONTENT_OK" = "1" ] || die "preflight page content does not contain '$EXPECTED_TITLE'; refusing to run"
note "network preflight: page content contains '$EXPECTED_TITLE'"

note "compiling on-screen window counter"
xcrun clang -fobjc-arc -framework Foundation -framework CoreGraphics \
  -o "$HELPER" "$HELPER_SRC"
[ -x "$HELPER" ] || die "failed to compile window counter"

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
  --log-net-log="$NETLOG"
  --net-log-capture-mode=IncludeSensitive
  --browser-cli-background-presentation
)
if [ "$NET_MODE" = "proxy" ]; then
  LAUNCH_CMD+=(--proxy-server="$PROXY_URL")
fi

WINDOW_SAMPLES="$TMP_DIR/window-samples.txt"
: > "$WINDOW_SAMPLES"

CHROME_PID=""
STOPPED=0
stop_browser() {
  [ -n "$CHROME_PID" ] || return 0
  [ "$STOPPED" = "1" ] && return 0
  if kill -0 "$CHROME_PID" 2>/dev/null; then
    note "stopping browser pid=$CHROME_PID (graceful SIGTERM; no window was ever shown)"
    kill -TERM "$CHROME_PID" 2>/dev/null || true
    for _ in $(seq 1 60); do
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

note "launching signed browser (background managed presentation)"
"${LAUNCH_CMD[@]}" >"$LOG" 2>&1 &
CHROME_PID=$!
note "browser pid=$CHROME_PID"

PORT_FILE="$PROFILE_DIR/DevToolsActivePort"
WAITED=0
while [ ! -s "$PORT_FILE" ]; do
  if ! kill -0 "$CHROME_PID" 2>/dev/null; then
    wait "$CHROME_PID" 2>/dev/null; rc=$?
    die "browser exited early (rc=$rc); log tail:
$(tail -5 "$LOG" 2>/dev/null || true)"
  fi
  STATE="$(ps -o stat= -p "$CHROME_PID" 2>/dev/null | tr -d ' ')"
  case "$STATE" in
    Z*|'')
      wait "$CHROME_PID" 2>/dev/null; rc=$?
      die "browser terminated before DevTools was ready (rc=$rc, state='$STATE'); log tail:
$(tail -5 "$LOG" 2>/dev/null || true)"
      ;;
  esac
  [ "$WAITED" -lt "$TIMEOUT_SECONDS" ] || die "DevToolsActivePort not written within ${TIMEOUT_SECONDS}s"
  if [ $((WAITED % 30)) -eq 0 ] && [ "$WAITED" -gt 0 ]; then
    note "still waiting for DevToolsActivePort (${WAITED}s; signed cold start can be slow)"
  fi
  sleep 1
  WAITED=$((WAITED + 1))
done
PORT="$(head -1 "$PORT_FILE" | tr -d '[:space:]')"
note "DevTools port=$PORT"

CMDLINE="$(ps -ww -o command= -p "$CHROME_PID" 2>/dev/null || true)"
note "browser cmdline: $CMDLINE"

WINDOWS_BEFORE="$("$HELPER" "$CHROME_PID")"
echo "before_target=$WINDOWS_BEFORE" >> "$WINDOW_SAMPLES"
note "on-screen windows before target creation: $WINDOWS_BEFORE"

note "creating background target for $TARGET_URL"
curl -sS -X PUT "http://127.0.0.1:$PORT/json/new?$ENCODED_URL" >/dev/null 2>&1 || true

TITLE_OK=0
URL_OK=0
MAX_WINDOWS="$WINDOWS_BEFORE"
WAITED=0
while [ "$WAITED" -lt "$TIMEOUT_SECONDS" ]; do
  LIST_JSON="$(curl -sS "http://127.0.0.1:$PORT/json/list" 2>/dev/null || true)"
  printf '%s' "$LIST_JSON" > "$TMP_DIR/devtools-list.json"
  W="$("$HELPER" "$CHROME_PID")"
  echo "t=${WAITED}s windows=$W" >> "$WINDOW_SAMPLES"
  if [ "$W" -gt "$MAX_WINDOWS" ]; then MAX_WINDOWS="$W"; fi
  if printf '%s' "$LIST_JSON" | grep -qF "\"url\": \"$TARGET_URL\""; then URL_OK=1; fi
  if printf '%s' "$LIST_JSON" | grep -qF "\"title\": \"$EXPECTED_TITLE\""; then TITLE_OK=1; fi
  if [ "$URL_OK" = "1" ] && [ "$TITLE_OK" = "1" ]; then break; fi
  sleep 1
  WAITED=$((WAITED + 1))
done

WINDOWS_AFTER="$("$HELPER" "$CHROME_PID")"
echo "after_load=$WINDOWS_AFTER" >> "$WINDOW_SAMPLES"
note "on-screen windows after load: $WINDOWS_AFTER (max during run: $MAX_WINDOWS)"

# Stop the browser before reading logs/NetLog (flush) — window samples are done.
stop_browser

FAILED=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then
    printf 'https.check name=%s result=PASS detail=%s\n' "$1" "$3"
  else
    printf 'https.check name=%s result=FAIL expected=%s actual=%s\n' "$1" "$2" "$3"
    FAILED=1
  fi
}

check "cmdline-has-mock-keychain" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -q -- '--use-mock-keychain'; then echo yes; else echo no; fi)"
check "cmdline-has-isolated-user-data-dir" "yes" \
  "$(if printf '%s' "$CMDLINE" | grep -qF -- "--user-data-dir=$PROFILE_DIR"; then echo yes; else echo no; fi)"
check "https-url-loaded" "yes" "$([ "$URL_OK" = "1" ] && echo yes || echo no)"
check "dom-title-verifiable" "yes" "$([ "$TITLE_OK" = "1" ] && echo yes || echo no)"
check "desktop-windows-always-zero" "0" "$MAX_WINDOWS"
check "desktop-windows-final-zero" "0" "$WINDOWS_AFTER"

# HTTP-level evidence from the browser's own NetLog (origin-specific: an HTTP/2
# `:authority` header proves the HTTPS request, and a 200 response carrying
# `content-type: text/html` proves the origin HTML response — not a proxy CONNECT).
AUTHORITY_HOST="$(printf '%s' "$TARGET_URL" | sed -E 's#^https://([^/]+).*#\1#')"
HTTPS_ORIGIN_LINES=0
if grep -F ":authority: $AUTHORITY_HOST" "$NETLOG" | grep -qF ':scheme: https'; then
  grep -F ":authority: $AUTHORITY_HOST" "$NETLOG" > "$TMP_DIR/netlog-origin-request.txt" 2>/dev/null || true
  HTTPS_ORIGIN_LINES="$(wc -l < "$TMP_DIR/netlog-origin-request.txt" | tr -d ' ')"
else
  : > "$TMP_DIR/netlog-origin-request.txt"
fi
check "netlog-has-https-origin-request" "yes" "$([ "${HTTPS_ORIGIN_LINES:-0}" -gt 0 ] && echo yes || echo no)"
grep -E '(:status: 200|HTTP/[0-9.]+ 200).*content-type: text/html' "$NETLOG" > "$TMP_DIR/netlog-origin-200-html.txt" 2>/dev/null || true
HTML_LINES="$(wc -l < "$TMP_DIR/netlog-origin-200-html.txt" | tr -d ' ')"
check "netlog-origin-200-text-html" "yes" "$([ "${HTML_LINES:-0}" -gt 0 ] && echo yes || echo no)"

check "host-suppressed-at-creation" "yes" \
  "$(if grep -q 'BROWSER_CLI_PRESENTATION stage=host decision=suppress' "$LOG"; then echo yes; else echo no; fi)"
check "policy-denied-internal-show" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=deny .*origin=internal' "$LOG"; then echo yes; else echo no; fi)"
check "no-internal-allow-in-background" "yes" \
  "$(if grep -Eq 'BROWSER_CLI_PRESENTATION stage=policy .*decision=allow .*origin=internal .*state=background' "$LOG"; then echo no; else echo yes; fi)"

# --- evidence bundle (build-logs) -------------------------------------------
EVIDENCE_DIR="$REPO_ROOT/build-logs/phase2-https-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$EVIDENCE_DIR"
cp "$LOG" "$EVIDENCE_DIR/chromium.log" 2>/dev/null || true
cp "$WINDOW_SAMPLES" "$EVIDENCE_DIR/window-samples.txt" 2>/dev/null || true
cp "$TMP_DIR/devtools-list.json" "$EVIDENCE_DIR/devtools-list.json" 2>/dev/null || true
cp "$TMP_DIR/netlog-origin-request.txt" "$EVIDENCE_DIR/netlog-origin-request.txt" 2>/dev/null || true
cp "$TMP_DIR/netlog-origin-200-html.txt" "$EVIDENCE_DIR/netlog-origin-200-html.txt" 2>/dev/null || true
NETLOG_SIZE="$(stat -f '%z' "$NETLOG" 2>/dev/null || echo 0)"
if [ "$NETLOG_SIZE" -lt 26214400 ]; then
  cp "$NETLOG" "$EVIDENCE_DIR/netlog.json" 2>/dev/null || true
  NETLOG_COPY="netlog.json (full)"
else
  NETLOG_COPY="omitted (large: ${NETLOG_SIZE} bytes)"
fi
{
  echo "generated_at=$(date '+%Y-%m-%d %H:%M:%S')"
  echo "app=$APP_DIR"
  echo "target_url=$TARGET_URL"
  echo "expected_title=$EXPECTED_TITLE"
  echo "network_mode=$NET_MODE"
  echo "devtools_port=$PORT"
  echo "window_samples_max=$MAX_WINDOWS"
  echo "netlog_size=$NETLOG_SIZE"
  echo "netlog_copy=$NETLOG_COPY"
  echo "netlog_https_origin_request_lines=$HTTPS_ORIGIN_LINES"
  echo "netlog_origin_200_text_html_lines=$HTML_LINES"
  echo "--- policy decisions ---"
  grep 'BROWSER_CLI_PRESENTATION' "$LOG" 2>/dev/null || true
} > "$EVIDENCE_DIR/summary.txt"
shasum -a 256 "$EVIDENCE_DIR"/* > "$EVIDENCE_DIR/sha256.txt" 2>/dev/null || true

if [ "$FAILED" = "1" ]; then
  printf 'HTTPS-CHECK: FAIL (evidence kept: %s)\n' "$EVIDENCE_DIR"
  KEEP_TMP=1
  exit 1
fi

printf 'HTTPS-CHECK: PASS (evidence: %s)\n' "$EVIDENCE_DIR"
