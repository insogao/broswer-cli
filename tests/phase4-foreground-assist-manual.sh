#!/usr/bin/env bash
# Browser CLI Foreground Assist manual acceptance package (Slice 2).
#
# EVIDENCE ONLY — this script NEVER launches, opens, activates, clicks, shows or
# interacts with Browser CLI, and never uses open/osascript/AppleScript. The
# user performs every browser action manually; this scaffold:
#   prepare : creates a fresh mktemp profile + evidence dir, enforces the pinned
#             Developer ID signature (read-only codesign query; ad-hoc builds are
#             refused), compiles and starts the PASSIVE app-identity on-screen
#             watcher and the PASSIVE local page server (neither presents a
#             window), and writes/prints the manual runbook.
#   windows : read-only helper that lists the window ids seen in the log so the
#             user can fill the assist --window= argument.
#   collect : after the manual session, parses the browser log, watcher samples,
#             page-server requests and the optional ABORT marker, and writes
#             report.json/report.md with PASS / NOT-EVALUATED / CONTRADICTION /
#             BLOCKED verdicts.
#   stop    : stops the passive watcher/page server by their exact recorded PIDs;
#             the user's browser is left untouched.
#
# Manual coverage (see the generated manual-checklist.md):
#   cold-start assist+URL negative (no visible window, no assist presentation,
#   no URL navigation at all),
#   grant showing only the target window, agent release and TTL timeout both
#   returning the window to the background, user takeover (Dock/Launchpad) with
#   the old TTL never hiding the HUMAN window, and a blocked assist request
#   during HUMAN that must not pollute the user's window. Every command is only
#   printed for the user; this script never executes them (no auto-run).
#
# The user hand-back ("交还 AI") sub-case is reported BLOCKED: no hand-back
# producer exists yet (PROJECT-PROGRESS C), never a fake pass.
#
# Usage: tests/phase4-foreground-assist-manual.sh <prepare|windows|collect|stop|status|help>
#          [--evidence DIR] [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'FOREGROUND-ASSIST: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
: "${BROWSER_CLI_TEAM_ID:?missing BROWSER_CLI_TEAM_ID}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

WATCHER_SRC="$SCRIPT_DIR/tools/onscreen-window-watch.m"
PAGE_SERVER_TOOL="$SCRIPT_DIR/tools/assist-page-server.py"
COLLECT_TOOL="$SCRIPT_DIR/tools/foreground-assist-collect.py"

# POSIX-shell safe single-quoting for copy-pasteable commands (bash 3.2
# compatible; no eval, no subprocess). Handles spaces and embedded single
# quotes via the standard '\'' escape.
shell_quote() {
  local s="$1" out="" ch i
  for (( i = 0; i < ${#s}; i++ )); do
    ch="${s:i:1}"
    if [ "$ch" = "'" ]; then
      out="$out'\\''"
    else
      out="$out$ch"
    fi
  done
  printf "'%s'" "$out"
}

die() { printf 'FOREGROUND-ASSIST: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[foreground-assist] %s\n' "$*"; }

COMMAND="${1:-help}"
[ $# -gt 0 ] && shift

# Pure function exposure for static tests (before the option parser, which
# would otherwise reject a positional value): prints the safely quoted form of
# the given string. Touches no browser, helper or file.
if [ "$COMMAND" = "__shell_quote" ]; then
  [ $# -gt 0 ] || die "__shell_quote needs a value"
  shell_quote "$1"
  printf '\n'
  exit 0
fi

EVIDENCE_DIR=""
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: tests/phase4-foreground-assist-manual.sh <command> [options]

Commands:
  prepare   create the evidence dir + fresh mktemp profile, verify the pinned
            Developer ID signature, start the passive watcher and page server,
            write and print the manual runbook (never launches Browser CLI)
  windows   list the window ids seen in the browser log (read-only helper for
            the assist --window= argument)
  collect   parse the manual session evidence and write report.json/report.md
  stop      stop the passive watcher/page server by their exact recorded PIDs
  status    print the recorded state
  help      this message

Options:
  --evidence DIR   evidence dir (default: newest build-logs/phase4-assist-*)
  --dry-run        prepare only: print the runbook without starting helpers,
                   creating a profile or checking the app signature

The script never runs open/osascript/AppleScript, never clicks/activates/shows
anything and never launches Browser CLI. Every browser action is manual; all
printed commands include --use-mock-keychain and the fresh mktemp profile.
If any system dialog or password prompt appears, stop immediately, do not click
it, and create the ABORT marker as the runbook describes (fail closed).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --evidence) shift; [ $# -gt 0 ] || die "--evidence needs a value"; EVIDENCE_DIR="$1" ;;
    --dry-run) DRY_RUN=1 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

pick_python() {
  if [ -x "$DEPOT_TOOLS/python-bin/python3" ]; then
    printf '%s\n' "$DEPOT_TOOLS/python-bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    die "python3 not found"
  fi
}
PYTHON="$(pick_python)"
PYTHON_REAL="$("$PYTHON" -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
if [ -n "$PYTHON_REAL" ] && [ -x "$PYTHON_REAL" ]; then
  PYTHON="$PYTHON_REAL"
fi

free_port() {
  "$PYTHON" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

# Lease TTL printed in the runbook; read from the frozen Chromium source when
# available so the wait instructions cannot drift from the implementation.
lease_ttl_minutes() {
  local header="$CHROMIUM_ROOT/src/ui/views/widget/browser_cli_foreground_lease.h"
  local line value
  line=""
  if [ -f "$header" ]; then
    line="$(grep -A2 'kDefaultForegroundAssistLeaseTtl' "$header" \
      | tr -d ' ' | grep -oE 'Minutes\([0-9]+\)|Seconds\([0-9]+\)' | head -1 || true)"
  fi
  case "$line" in
    Minutes\(*)
      value="${line#Minutes(}"; value="${value%)}"
      printf '%s\n' "$value"
      ;;
    Seconds\(*)
      value="${line#Seconds(}"; value="${value%)}"
      printf '%s\n' "$(( (value + 59) / 60 ))"
      ;;
    *) printf '5\n' ;;
  esac
}

resolve_evidence() {
  if [ -n "$EVIDENCE_DIR" ]; then
    [ -f "$EVIDENCE_DIR/state.env" ] || die "no state.env in $EVIDENCE_DIR"
  else
    EVIDENCE_DIR="$(ls -dt "$REPO_ROOT"/build-logs/phase4-assist-* 2>/dev/null | head -1 || true)"
    [ -n "$EVIDENCE_DIR" ] && [ -f "$EVIDENCE_DIR/state.env" ] \
      || die "no prepared evidence dir; run 'prepare' first"
  fi
}

require_signed_app() {
  [ -x "$BIN" ] || die "signed app binary not found: $BIN (build it outside this scaffold)"
  # No ad-hoc build may be launched or tested: require the pinned Developer ID
  # signature (read-only codesign query; no keychain access, no app launch).
  local sig
  sig="$(codesign -dv --verbose=4 "$APP_DIR" 2>&1 || true)"
  case "$sig" in
    *"TeamIdentifier=$BROWSER_CLI_TEAM_ID"*)
      note "app signature OK: Developer ID (team $BROWSER_CLI_TEAM_ID)"
      ;;
    *"Signature=adhoc"*)
      die "refusing to prepare for an ad-hoc build; run scripts/build.sh (mandatory signing, no fallback)"
      ;;
    *)
      die "refusing to prepare: $APP_DIR is not signed with the pinned Developer ID identity"
      ;;
  esac
}

case "$COMMAND" in
  help|--help|-h) usage; exit 0 ;;

  prepare)
    [ -f "$WATCHER_SRC" ] || die "missing watcher source: $WATCHER_SRC"
    [ -f "$PAGE_SERVER_TOOL" ] || die "missing page server tool: $PAGE_SERVER_TOOL"

    if [ "$DRY_RUN" = "1" ]; then
      EVIDENCE_DIR="${EVIDENCE_DIR:-<build-logs/phase4-assist-DRYRUN>}"
      PROFILE_DIR="<fresh-mktemp-profile>"
      BROWSER_LOG="$EVIDENCE_DIR/browser.log"
      SAMPLES="$EVIDENCE_DIR/watcher-samples.txt"
      SERVER_LOG="$EVIDENCE_DIR/page-server.log"
      SERVER_PORT="<free-port>"
      note "DRY-RUN: no profile, no helpers, no state file; signature check skipped"
    else
      if [ -z "$EVIDENCE_DIR" ]; then
        EVIDENCE_DIR="$REPO_ROOT/build-logs/phase4-assist-$(date +%Y%m%d-%H%M%S)"
      fi
      mkdir -p "$EVIDENCE_DIR"
      PROFILE_DIR="$(mktemp -d "$EVIDENCE_DIR/profile.XXXXXX")"
      BROWSER_LOG="$EVIDENCE_DIR/browser.log"
      SAMPLES="$EVIDENCE_DIR/watcher-samples.txt"
      SERVER_LOG="$EVIDENCE_DIR/page-server.log"
      WATCHER="$EVIDENCE_DIR/onscreen-window-watch"
      : > "$BROWSER_LOG"
      : > "$SAMPLES"
      : > "$SERVER_LOG"

      require_signed_app
      SERVER_PORT="$(free_port)"

      note "compiling the passive on-screen watcher (local test tool; no product build)"
      xcrun clang -fobjc-arc -framework Foundation -framework CoreGraphics \
        -o "$WATCHER" "$WATCHER_SRC"

      note "starting the passive page server on 127.0.0.1:$SERVER_PORT (no windows)"
      "$PYTHON" "$PAGE_SERVER_TOOL" --log "$SERVER_LOG" --port "$SERVER_PORT" \
        >/dev/null 2>&1 &
      SERVER_PID=$!

      note "starting the passive app-identity watcher (10ms, no windows)"
      nohup "$WATCHER" "$BIN" "$SAMPLES" 10 >/dev/null 2>&1 &
      WATCHER_PID=$!
      sleep 1
      kill -0 "$WATCHER_PID" 2>/dev/null || die "watcher exited early"
      kill -0 "$SERVER_PID" 2>/dev/null || die "page server exited early"

      # Values are single-quoted so a later `collect`/`stop` can source this
      # file even when the product path or evidence dir contains spaces.
      cat > "$EVIDENCE_DIR/state.env" <<EOF
EVIDENCE_DIR=$(shell_quote "$EVIDENCE_DIR")
PROFILE_DIR=$(shell_quote "$PROFILE_DIR")
BROWSER_LOG=$(shell_quote "$BROWSER_LOG")
SAMPLES=$(shell_quote "$SAMPLES")
SERVER_LOG=$(shell_quote "$SERVER_LOG")
SERVER_PORT=$SERVER_PORT
WATCHER_PID=$WATCHER_PID
SERVER_PID=$SERVER_PID
APP_BIN=$(shell_quote "$BIN")
EOF
    fi

    TTL_MINUTES="$(lease_ttl_minutes)"
    PAGE_BASE="http://127.0.0.1:$SERVER_PORT/page"

    # All commands below are PRINTED for the USER to paste in a terminal; this
    # scaffold never executes them. Every command carries --use-mock-keychain and
    # the fresh mktemp profile; the managed switch keeps a fallback cold start
    # from ever showing a stock visible window.
    LAUNCH_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --enable-logging=stderr --browser-cli-background-presentation >> $(shell_quote "$BROWSER_LOG") 2>&1 &"
    COLD_ASSIST_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --enable-logging=stderr --browser-cli-background-presentation --browser-cli-foreground-assist --browser-cli-foreground-assist-reason=login --browser-cli-foreground-assist-window=1 $(shell_quote "$PAGE_BASE?name=blocked_cold") >> $(shell_quote "$BROWSER_LOG") 2>&1 &"
    URL_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --browser-cli-background-presentation --new-window $(shell_quote "$PAGE_BASE?name=target")"
    ASSIST_GRANT_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --browser-cli-background-presentation --browser-cli-foreground-assist --browser-cli-foreground-assist-reason=login --browser-cli-foreground-assist-window=WINDOW_ID"
    ASSIST_GRANT_CAPTCHA_CMD="${ASSIST_GRANT_CMD/--browser-cli-foreground-assist-reason=login/--browser-cli-foreground-assist-reason=captcha}"
    ASSIST_GRANT_CONFIRM_CMD="${ASSIST_GRANT_CMD/--browser-cli-foreground-assist-reason=login/--browser-cli-foreground-assist-reason=confirmation}"
    ASSIST_RELEASE_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --browser-cli-background-presentation --browser-cli-foreground-assist-release --browser-cli-foreground-assist-window=WINDOW_ID"
    BLOCKED_ASSIST_URL_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --browser-cli-background-presentation --browser-cli-foreground-assist --browser-cli-foreground-assist-reason=login --browser-cli-foreground-assist-window=WINDOW_ID $(shell_quote "$PAGE_BASE?name=blocked_session")"

    RUNBOOK="$EVIDENCE_DIR/manual-checklist.md"
    RUNBOOK_TEXT="$(cat <<EOF
# Foreground Assist manual acceptance runbook

Evidence dir: \`$EVIDENCE_DIR\`
Profile (fresh mktemp): \`$PROFILE_DIR\`  (--use-mock-keychain; never your real profile)
Local page server: \`http://127.0.0.1:$SERVER_PORT/\` (requests are logged; host-side only)
Assist TTL: **$TTL_MINUTES minutes** (from \`kDefaultForegroundAssistLeaseTtl\`)

The passive watcher (10ms, app identity) and the page server are already
running. This scaffold never launches or touches the browser; you perform every
browser action yourself. If ANY system dialog / password prompt appears, stop
immediately, do not click it, and run:

    touch "$EVIDENCE_DIR/ABORT"

Then report FAIL. Never treat a dialog as a pass.

## A. Cold-start assist + URL negative (must fail closed)

Run this as the FIRST launch (nothing else running). It carries both the assist
switch and a URL; the assist request must be DENIED and no window may become
visible:

    $COLD_ASSIST_CMD

Expect: \`BROWSER_CLI_FOREGROUND_ASSIST stage=producer result=denied
reason=launch-arguments-not-agent-assist managed=yes\`, no
\`assist-lease-granted\` / \`assist-window-visible\` line, nothing visible on
screen, and **NO page-server GET** for \`name=blocked_cold\`: the assist intent
is consumed (cold-start sanitizer strips the URL, ProcessSingleton handler
consumes the request before ordinary processing), so there is no fallback
navigation. A blocked-URL GET makes the collector report
\`assist-blocked-url-never-navigates\` CONTRADICTION and the session FAILS.
When done, quit this instance yourself (Cmd+Q).

## B. Main session + target window

Start the managed browser and keep it running:

    $LAUNCH_CMD

Create the target window with the real page (second terminal; forwards to the
running instance):

    $URL_CMD

Confirm nothing is visible. The page server log must show
\`path=/page?name=target\`. Find the target window id:

    tests/phase4-foreground-assist-manual.sh windows --evidence "$EVIDENCE_DIR"

The newest id is your WINDOW_ID for the steps below.

## C. Grant shows only the target window

    $ASSIST_GRANT_CMD

Replace WINDOW_ID with the id from step B. Expect ONLY the target window to
appear (no other window), showing the real page (title BCLI_PAGE_target).
Evidence: producer \`result=handled reason=lease-granted-and-presented\`;
policy \`transition=assist-lease-granted window_id=<W>\` and
\`decision=allow ... origin=agent-assist ... state=assist leased=yes\`;
host \`transition=assist-window-visible widget_id=<W>\`.

## D. Release and timeout return the window to the background

Agent release (replace WINDOW_ID):

    $ASSIST_RELEASE_CMD

Expect the window to disappear from the desktop. Evidence: producer
\`result=handled reason=lease-released\`; policy
\`transition=assist-lease-released ... reason=agent-release\`; host
\`transition=assist-window-background widget_id=<W>\`.

Then grant again with the captcha reason and DO NOT touch the window:

    $ASSIST_GRANT_CAPTCHA_CMD

Wait at least $TTL_MINUTES minutes without clicking it. Expect the window to
return to the background automatically. Evidence: policy
\`transition=assist-lease-released ... reason=timeout\` followed by host
\`transition=assist-window-background widget_id=<W>\` (no producer line for the
timeout).

## E. User takeover: the old TTL must never hide the HUMAN window

Grant again with the confirmation reason:

    $ASSIST_GRANT_CONFIRM_CMD

Then click the Browser CLI Dock icon (or Launchpad) to take over. The window
stays visible and becomes HUMAN-owned. Evidence: policy
\`transition=assist-lease-released ... reason=user-takeover\` followed by
\`transition=human-ownership-granted window_id=<W>\`.

## F. Blocked assist request during HUMAN must not pollute the user window

With the HUMAN window still visible, run (replace WINDOW_ID):

    $BLOCKED_ASSIST_URL_CMD

Expect: producer \`result=denied reason=launch-arguments-not-agent-assist\`;
NO assist lease / visible / background / release line for the HUMAN window id;
\`owned=yes\` stays; no new visible window; and **NO page-server GET** for
\`name=blocked_session\` (same consumed-intent contract as step A). A
blocked-URL GET is an \`assist-blocked-url-never-navigates\` CONTRADICTION.

## G. Old TTL after takeover (wait) and collect

Wait at least $TTL_MINUTES minutes after the grant in step E WITHOUT touching
the HUMAN window. Expect: the window is NOT hidden; there is no
\`assist-window-background\` line for its id after the takeover.

Then run the collector while the HUMAN window is still visible:

    tests/phase4-foreground-assist-manual.sh collect --evidence "$EVIDENCE_DIR"

Quit the browser yourself (Cmd+Q), then stop the passive helpers:

    tests/phase4-foreground-assist-manual.sh stop --evidence "$EVIDENCE_DIR"

## BLOCKED / follow-up matrix (never a fake pass)

- user hand-back ("交还 AI") producer: BLOCKED — no hand-back producer exists yet
  (PROJECT-PROGRESS C); the old-TTL-after-hand-back sub-check is reported
  BLOCKED, never PASS.
- entry-point attribution (cold start vs ProcessSingleton): BLOCKED — the
  producer log does not record which entry point handled the request; both share
  one classifier. Step A performs the cold-start variant; the verdict rests on
  the shared fail-closed evidence.
- dialog detection is manual: the collector can only fail closed on the ABORT
  marker you create; it cannot detect a dialog itself.
EOF
)"

    if [ "$DRY_RUN" = "1" ]; then
      note "evidence dir: $EVIDENCE_DIR (dry-run: nothing written)"
      note "runbook: (dry-run: printed only)"
      printf '\n===== MANUAL RUNBOOK (dry-run preview; you run these) =====\n'
      printf '%s\n' "$RUNBOOK_TEXT"
      printf '================================================================\n'
    else
      printf '%s\n' "$RUNBOOK_TEXT" > "$RUNBOOK"
      note "evidence dir: $EVIDENCE_DIR"
      note "runbook: $RUNBOOK"
      printf '\n===== MANUAL RUNBOOK (you run these; the script does not) =====\n'
      cat "$RUNBOOK"
      printf '================================================================\n'
    fi
    ;;

  windows)
    resolve_evidence
    # shellcheck disable=SC1090
    . "$EVIDENCE_DIR/state.env"
    "$PYTHON" - "$BROWSER_LOG" <<'PY'
import re
import sys

path = sys.argv[1]
counts = {}
order = []
for line in open(path, "r", encoding="utf-8", errors="replace"):
    for match in re.finditer(r"(?:window_id|widget_id)=(\d+)", line):
        window_id = match.group(1)
        if window_id == "0":
            continue
        if window_id not in counts:
            counts[window_id] = 0
            order.append(window_id)
        counts[window_id] += 1
if not order:
    print("no window ids in the log yet")
else:
    for window_id in order:
        print("window_id=%s mentions=%d" % (window_id, counts[window_id]))
    print("newest=%s" % order[-1])
PY
    ;;

  collect)
    resolve_evidence
    # shellcheck disable=SC1090
    . "$EVIDENCE_DIR/state.env"
    note "collecting evidence (read-only; the browser is not touched)"
    "$PYTHON" "$COLLECT_TOOL" \
      --log "$BROWSER_LOG" \
      --samples "$SAMPLES" \
      --server-log "$SERVER_LOG" \
      --report-dir "$EVIDENCE_DIR" \
      --abort-flag "$EVIDENCE_DIR/ABORT"
    note "report: $EVIDENCE_DIR/report.md"
    ;;

  stop)
    resolve_evidence
    # shellcheck disable=SC1090
    . "$EVIDENCE_DIR/state.env"
    for spec in "WATCHER_PID:$SAMPLES:watcher" "SERVER_PID:$SERVER_LOG:page-server"; do
      pid_var="${spec%%:*}"; rest="${spec#*:}"; needle="${rest%%:*}"; label="${rest##*:}"
      pid="${!pid_var:-}"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        cmdline="$(ps -ww -o command= -p "$pid" 2>/dev/null || true)"
        case "$cmdline" in
          *"$needle"*) kill -TERM "$pid" 2>/dev/null || true ;;
          *) note "not stopping $label pid=$pid: identity mismatch" ;;
        esac
      fi
    done
    note "passive helpers stopped (your browser was not touched)"
    ;;

  status)
    resolve_evidence
    cat "$EVIDENCE_DIR/state.env"
    ;;

  *) usage; exit 2 ;;
esac
