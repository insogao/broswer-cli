#!/usr/bin/env bash
# Browser CLI Gate B evidence scaffold: USER_EXPLICIT / HUMAN per-window ownership.
#
# EVIDENCE ONLY — this script NEVER launches, opens, activates, clicks, shows or
# interacts with Browser CLI, and never uses open/osascript/AppleScript. The
# user performs every browser action manually; this scaffold:
#   prepare : creates a fresh mktemp profile + evidence dir, compiles and starts
#             the PASSIVE app-identity on-screen watcher and the PASSIVE local
#             result sink (neither presents a window), writes the manual runbook.
#   collect : after the manual session, parses the policy log, watcher samples
#             and the extension ops sink, and writes report.json/report.md with
#             PASS / NOT-EVALUATED / CONTRADICTION / BLOCKED verdicts.
#   stop    : stops the passive watcher/sink by their exact recorded PIDs; the
#             user's browser is left untouched.
#
# Manual coverage (see the generated manual-checklist.md):
#   Dock / Launchpad reopen, native minimize / restore / fullscreen, Extension
#   activate / deactivate / minimize / maximize / restore / fullscreen / bounds
#   attempts on the HUMAN window (must be denied), creating another background
#   Window/Tab while HUMAN exists, and direct-binary CLI explicit `show` cases
#   (running existing window; zero-window / --no-startup-window; optional
#   blocked-launch-argument negative control). The CLI show commands are only
#   printed for the user; this script never executes them (no auto-run).
#
# Known-unsupported areas (child-dialog attribution, remote app-shim windows,
# TabsCreate async attribution) are reported as BLOCKED/follow-up, never faked.
#
# Usage: tests/phase3-human-ownership-manual.sh <prepare|collect|stop|status|help>
#          [--evidence DIR] [--port DEVTOOLS_PORT] [--sink-port PORT]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'HUMAN-OWNERSHIP: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
APP_DIR="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app"
BIN="$APP_DIR/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

OPS_DIR="$SCRIPT_DIR/tools/extensions/browser-cli-human-window-ops"
WATCHER_SRC="$SCRIPT_DIR/tools/onscreen-window-watch.m"
SINK_TOOL="$SCRIPT_DIR/tools/ops-result-sink.py"
COLLECT_TOOL="$SCRIPT_DIR/tools/human-ownership-collect.py"
READINESS_TOOL="$SCRIPT_DIR/tools/browser-readiness-check.py"

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
DEVTOOLS_PORT=""
SINK_PORT="8737"
DRY_RUN=0
PRINT_RUNBOOK=0
WAIT_FLAG=0
JSON_FLAG=0
TIMEOUT_SECS=""

usage() {
  cat <<'EOF'
Usage: tests/phase3-human-ownership-manual.sh <command> [options]

Commands:
  prepare   create the evidence dir + fresh mktemp profile, compile and start
            the passive watcher and result sink, verify the signed build phase,
            and write the manual runbook (never launches Browser CLI)
  ready     read-only readiness phases: prepare / signed / process-spawned /
            browser-ready (bound to THIS profile's DevTools port; --wait polls
            until this profile is ready; timeout exits non-zero). Never
            launches, clicks, shows or touches anything
  collect   parse the manual session evidence and write report.json/report.md
  stop      stop the passive watcher/sink by their exact recorded PIDs
  status    print the recorded state
  help      this message

Options:
  --evidence DIR     evidence dir (default: newest build-logs/phase3-human-*)
  --port PORT        DevTools port for the MANUAL launch command (default: free port)
  --sink-port PORT   local result sink port (default 8737)
  --dry-run          prepare only: print the short phase/next-steps preview and
                     the runbook without creating anything or starting helpers
  --print-runbook    prepare only: also print the full runbook
  --wait             ready only: poll until browser-ready (default timeout 600s)
  --timeout SECS     ready only: poll timeout
  --json             ready only: print the readiness report as JSON

Phase model (never misjudge a spawned process as a ready browser):
  prepare          evidence dir + fresh mktemp profile exist
  signed           the app bundle is Developer ID signed (ad-hoc = FAIL)
  process-spawned  a process with THIS profile is running
  browser-ready    THIS profile's DevTools port answers AND its listening owner
                   is this profile's main process; when that cannot be bound
                   the result is BLOCKED (fail closed), never a fake pass
  (info)           the app-wide on-screen window sample is informational only:
                   it is not attributed to this profile and never decides

Observed performance problem of this development build: a first signed cold
start spends ~3 minutes in the dyld mapping stage (~500 dylibs) before the main
loop. This is not acceptable product behavior and is not described as normal.
Until browser-ready the profile directory may not exist yet and Dock clicks do
nothing.

The script never runs open/osascript/AppleScript, never clicks/activates/shows
anything, and never launches Browser CLI. Every browser action is manual.
EOF
}

die() { printf 'HUMAN-OWNERSHIP: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[human-ownership] %s\n' "$*"; }

# Exact-PID helper shutdown with an identity check. Never a broad match: the
# cmdline must contain the helper's own evidence file, so a reused PID from
# another session is left alone.
stop_helper_by_pid() {
  local pid="$1" needle="$2" label="$3"
  [ -n "$pid" ] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  local cmdline
  cmdline="$(ps -ww -o command= -p "$pid" 2>/dev/null || true)"
  case "$cmdline" in
    *"$needle"*) kill -TERM "$pid" 2>/dev/null || true; note "stopped $label pid=$pid" ;;
    *) note "not stopping $label pid=$pid: identity mismatch" ;;
  esac
}

# If prepare fails after starting the passive helpers, stop exactly those PIDs
# (identity-checked). Disarmed on success so the helpers keep running.
STARTED_WATCHER_PID=""
STARTED_SINK_PID=""
HELPERS_STARTED=0
cleanup_started_helpers() {
  [ "$HELPERS_STARTED" = "1" ] || return 0
  HELPERS_STARTED=0
  note "prepare did not complete: stopping the helpers it started"
  stop_helper_by_pid "$STARTED_SINK_PID" "$SINK_JSONL" "sink"
  stop_helper_by_pid "$STARTED_WATCHER_PID" "$SAMPLES" "watcher"
}
trap 'cleanup_started_helpers' EXIT

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --evidence) shift; [ $# -gt 0 ] || die "--evidence needs a value"; EVIDENCE_DIR="$1" ;;
    --port) shift; [ $# -gt 0 ] || die "--port needs a value"; DEVTOOLS_PORT="$1" ;;
    --sink-port) shift; [ $# -gt 0 ] || die "--sink-port needs a value"; SINK_PORT="$1" ;;
    --dry-run) DRY_RUN=1 ;;
    --print-runbook) PRINT_RUNBOOK=1 ;;
    --wait) WAIT_FLAG=1 ;;
    --json) JSON_FLAG=1 ;;
    --timeout)
      shift; [ $# -gt 0 ] || die "--timeout needs a value"; TIMEOUT_SECS="$1"
      case "$TIMEOUT_SECS" in *[!0-9]*|"") die "--timeout must be an integer" ;; esac
      ;;
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

resolve_evidence() {
  if [ -n "$EVIDENCE_DIR" ]; then
    [ -f "$EVIDENCE_DIR/state.env" ] || die "no state.env in $EVIDENCE_DIR"
  else
    EVIDENCE_DIR="$(ls -dt "$REPO_ROOT"/build-logs/phase3-human-* 2>/dev/null | head -1 || true)"
    [ -n "$EVIDENCE_DIR" ] && [ -f "$EVIDENCE_DIR/state.env" ] \
      || die "no prepared evidence dir; run 'prepare' first"
  fi
}

extension_id() {
  "$PYTHON" - "$1" <<'PY'
import hashlib
import os
import sys
digest = hashlib.sha256(os.path.abspath(sys.argv[1]).encode()).hexdigest()[:32]
print("".join(chr(ord("a") + int(ch, 16)) for ch in digest))
PY
}

free_port() {
  "$PYTHON" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

# Prints "in-use" when the port cannot be bound. Used only as a read-only
# preflight: another session's helpers are never touched, the caller picks a
# different port instead.
port_in_use() {
  "$PYTHON" - "$1" <<'PY'
import socket
import sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    print("in-use")
finally:
    s.close()
PY
}

case "$COMMAND" in
  help|--help|-h) usage; exit 0 ;;

  prepare)
    [ -f "$OPS_DIR/manifest.json" ] || die "missing ops fixture: $OPS_DIR"
    [ -f "$WATCHER_SRC" ] || die "missing watcher source: $WATCHER_SRC"
    [ -f "$READINESS_TOOL" ] || die "missing readiness tool: $READINESS_TOOL"
    if [ "$DRY_RUN" = "1" ]; then
      EVIDENCE_DIR="${EVIDENCE_DIR:-<build-logs/phase3-human-DRYRUN>}"
      PROFILE_DIR="<fresh-mktemp-profile>"
      BROWSER_LOG="$EVIDENCE_DIR/browser.log"
      SAMPLES="$EVIDENCE_DIR/watcher-samples.txt"
      SINK_JSONL="$EVIDENCE_DIR/ops-sink.jsonl"
      WATCHER="<compiled-watcher>"
      WATCHER_PID="<pid>"
      SINK_PID="<pid>"
      EXT_ID="<extension-id>"
      [ -n "$DEVTOOLS_PORT" ] || DEVTOOLS_PORT="<free-port>"
      note "DRY-RUN: nothing created, no helpers started, no browser touched"
    else
      # Never fight another session for a port: fail closed before creating
      # anything or starting any helper.
      if [ "$(port_in_use "$SINK_PORT")" = "in-use" ]; then
        die "sink port $SINK_PORT is already in use (another passive session?); this scaffold never stops other sessions — pick --sink-port PORT, or stop that session with its own scaffold"
      fi
      if [ -n "$DEVTOOLS_PORT" ] && [ "$(port_in_use "$DEVTOOLS_PORT")" = "in-use" ]; then
        die "DevTools port $DEVTOOLS_PORT is already in use; pick another --port"
      fi
      if [ -z "$EVIDENCE_DIR" ]; then
        EVIDENCE_DIR="$REPO_ROOT/build-logs/phase3-human-$(date +%Y%m%d-%H%M%S)"
      fi
      mkdir -p "$EVIDENCE_DIR"
      PROFILE_DIR="$(mktemp -d "$EVIDENCE_DIR/profile.XXXXXX")"
      BROWSER_LOG="$EVIDENCE_DIR/browser.log"
      SAMPLES="$EVIDENCE_DIR/watcher-samples.txt"
      SINK_JSONL="$EVIDENCE_DIR/ops-sink.jsonl"
      WATCHER="$EVIDENCE_DIR/onscreen-window-watch"
      : > "$BROWSER_LOG"
      : > "$SAMPLES"
      : > "$SINK_JSONL"

      [ -x "$BIN" ] || die "signed app binary not found: $BIN (build it outside this scaffold)"
      [ -n "$DEVTOOLS_PORT" ] || DEVTOOLS_PORT="$(free_port)"
      EXT_ID="$(extension_id "$OPS_DIR")"

      note "compiling the passive on-screen watcher (local test tool; no product build)"
      xcrun clang -fobjc-arc -framework Foundation -framework CoreGraphics \
        -o "$WATCHER" "$WATCHER_SRC"

      note "starting the passive result sink on 127.0.0.1:$SINK_PORT (no windows)"
      "$PYTHON" "$SINK_TOOL" "$SINK_JSONL" "$SINK_PORT" >/dev/null 2>&1 &
      SINK_PID=$!
      STARTED_SINK_PID="$SINK_PID"

      note "starting the passive app-identity watcher (10ms, no windows)"
      nohup "$WATCHER" "$BIN" "$SAMPLES" 10 >/dev/null 2>&1 &
      WATCHER_PID=$!
      STARTED_WATCHER_PID="$WATCHER_PID"
      HELPERS_STARTED=1
      sleep 1
      kill -0 "$WATCHER_PID" 2>/dev/null || die "watcher exited early"
      kill -0 "$SINK_PID" 2>/dev/null || die "result sink exited early"

      # Values are single-quoted so a later `collect`/`stop` can source this file
      # even when the product path or evidence dir contains spaces (macOS release
      # app bundles do: `Browser CLI.app/...`).
      cat > "$EVIDENCE_DIR/state.env" <<EOF
EVIDENCE_DIR=$(shell_quote "$EVIDENCE_DIR")
PROFILE_DIR=$(shell_quote "$PROFILE_DIR")
BROWSER_LOG=$(shell_quote "$BROWSER_LOG")
SAMPLES=$(shell_quote "$SAMPLES")
SINK_JSONL=$(shell_quote "$SINK_JSONL")
SINK_PORT=$SINK_PORT
DEVTOOLS_PORT=$DEVTOOLS_PORT
WATCHER_PID=$WATCHER_PID
SINK_PID=$SINK_PID
EXTENSION_ID=$EXT_ID
OPS_DIR=$(shell_quote "$OPS_DIR")
APP_BIN=$(shell_quote "$BIN")
EOF
    fi

    LAUNCH_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --remote-debugging-port=$DEVTOOLS_PORT --enable-logging=stderr --disable-extensions-except=$(shell_quote "$OPS_DIR") --load-extension=$(shell_quote "$OPS_DIR") --browser-cli-background-presentation > $(shell_quote "$BROWSER_LOG") 2>&1 &"

    # Direct-binary CLI explicit `show` cases. These are printed for the USER to
    # run in a second terminal, never executed by this scaffold. The second
    # invocation forwards to the running instance through ProcessSingleton; a
    # cold start is kept managed too, so a failed forward can never fall back to
    # a stock visible window. The zero-window phase reuses the same profile and
    # appends to the same evidence log so the collector sees every producer line.
    SHOW_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --browser-cli-background-presentation --browser-cli-user-explicit-show"
    SHOW_BLOCKED_CMD="$SHOW_CMD about:blank"
    LAUNCH_NO_WINDOW_CMD="$(shell_quote "$BIN") --user-data-dir=$(shell_quote "$PROFILE_DIR") --use-mock-keychain --no-first-run --remote-debugging-port=$DEVTOOLS_PORT --enable-logging=stderr --disable-extensions-except=$(shell_quote "$OPS_DIR") --load-extension=$(shell_quote "$OPS_DIR") --browser-cli-background-presentation --no-startup-window >> $(shell_quote "$BROWSER_LOG") 2>&1 &"

    # The runbook is written with a plain heredoc: bash 3.2 mis-parses a
    # heredoc inside a command substitution when the body contains an
    # apostrophe, so it is never captured into a variable. A dry-run writes a
    # throwaway preview file under the system temp dir and removes it after
    # printing.
    if [ "$DRY_RUN" = "1" ]; then
      RUNBOOK="$(mktemp "${TMPDIR:-/tmp}/phase3-human-runbook.XXXXXX")"
      DRY_RUN_RUNBOOK=1
    else
      RUNBOOK="$EVIDENCE_DIR/manual-checklist.md"
      DRY_RUN_RUNBOOK=0
    fi
    cat > "$RUNBOOK" <<EOF
# HUMAN ownership manual runbook

Evidence dir: \`$EVIDENCE_DIR\`
Profile (fresh mktemp): \`$PROFILE_DIR\`  (--use-mock-keychain; never your real profile)
Extension ops page: \`chrome-extension://$EXT_ID/ops.html\`

The passive watcher (10ms, app identity) and the local result sink are already
running. This scaffold never launches or touches the browser; you perform every
browser action yourself.

READINESS PHASES — do not misjudge a spawned process as a ready browser, and do
not read the app-wide watcher sample as THIS profile being ready. Observed
performance problem of this development build: a first signed cold start spends
~3 minutes in the dyld mapping stage (~500 dylibs) before the main loop; this is
not acceptable product behavior. Until the readiness check prints READY, the
profile directory may not exist yet and Dock clicks do nothing. Check any time
with:

    tests/phase3-human-ownership-manual.sh ready --evidence "$EVIDENCE_DIR"

\`ready\` prints BLOCKED (and exits non-zero) when readiness cannot be bound to
this profile; that is a fail-closed result, not a pass.

1. Run this launch command in a terminal (your action; managed mode ON so the
   policy is active). Paths are single-quoted so this line is safe to copy
   even when they contain spaces or quotes. Keep it running:

   $LAUNCH_CMD

   Then, in a SECOND terminal, wait until the browser is ready (read-only
   check; this development build's first signed cold start was observed to
   spend ~3 minutes in the dyld mapping stage). Process existence alone is NOT
   readiness, and the app-wide watcher sample is not this profile:

   tests/phase3-human-ownership-manual.sh ready --wait --evidence "$EVIDENCE_DIR"

   Only after it prints READY, continue with step 2.

2. (Only after READY in step 1.) Click the Browser CLI Dock icon (or Launchpad)
   to take over:
   expect the window to appear/activate (USER_EXPLICIT -> HUMAN ownership).
   Evidence: policy log \`transition=human-ownership-granted\`.

3. Native window controls (your actions): minimize, restore, enter/exit
   fullscreen via the traffic lights or shortcuts. The browser must behave
   normally and AI must not fight you.

4. Open \`chrome-extension://$EXT_ID/ops.html\` as a NEW TAB in the HUMAN window
   (Cmd+T, paste the URL). Press "Detect HUMAN window" first.

5. Press every op button: activate, deactivate, minimize, maximize, restore,
   fullscreen on/off, bounds move/resize. Expect: the extension API may resolve
   at the model level, but the native window must NOT be stolen, minimized,
   moved or re-ordered. Evidence: policy \`deny ... origin=extension ...
   owned=yes\` for the HUMAN window id.

6. Press "create background Window (focused:false)" and "create background Tab
   (active:false)" while HUMAN exists: they must stay in the background.
   Evidence: policy host suppress / deny and the sink inventory.

7. Press "Send all results to sink" (also sent automatically after each op).

8. CLI explicit show — running existing window (your action, direct binary, not
   \`open\`). With the browser from step 1 still running, run this in a SECOND
   terminal. The second invocation forwards to the running instance through the
   profile singleton and exits:

   $SHOW_CMD

   Expect the existing hidden window to appear and activate with HUMAN
   ownership. Evidence in \`$BROWSER_LOG\`:
   - producer \`BROWSER_CLI_USER_EXPLICIT_SHOW stage=producer result=handled
     reason=existing-window-presented managed=yes\`
   - policy \`decision=allow ... origin=user-explicit window_id=<W> state=human
     owned=yes\` and \`transition=human-ownership-granted window_id=<W>\`.
   No new duplicate window is created.

9. CLI explicit show — zero-window / no-startup-window (your action). Quit the
   browser yourself (Cmd+Q), then start a managed instance that has no window at
   all and run the show command again from the second terminal:

   $LAUNCH_NO_WINDOW_CMD
   $SHOW_CMD

   Expect exactly one window to be created and presented (no profile picker, no
   session restore). Evidence: producer \`result=handled
   reason=window-created-and-presented managed=yes\` plus the same policy
   allow/transition pair as step 8.

10. (optional negative control) With the browser running, run the show switch
    together with a positional URL. This must be DENIED, never presented:

   $SHOW_BLOCKED_CMD

    Expect producer \`result=denied
    reason=launch-arguments-not-user-explicit managed=yes\` and no new HUMAN
    ownership for that request. Ordinary command-line handling may still load
    the URL into a background-managed window.

11. When done, close the ops tab and quit the browser yourself (Cmd+Q), or leave
    it running; either way the log + sink evidence is complete.

12. Run the collector (does not touch the browser):

   tests/phase3-human-ownership-manual.sh collect --evidence "$EVIDENCE_DIR"

## Known-unsupported follow-up areas (INFORMATIONAL — not errors, not part of today's steps; never a fake pass)

This list is future work, not a failure of this session and not an instruction
to fix anything now. It is reported separately by the collector.

- child-dialog attribution (child dialogs raised via window.open() / alert sheets): BLOCKED
- remote app-shim (PWA) windows: BLOCKED
- chrome.tabs.create async attribution: BLOCKED (accept only with explicit
  policy evidence, otherwise NOT-EVALUATED)
EOF

    if [ "$DRY_RUN" = "1" ]; then
      printf '\n===== PHASE STATUS (dry-run preview) =====\n'
      printf 'PHASE prepare ......... PREVIEW (dry-run: nothing created)\n'
      printf 'PHASE signed .......... NOT CHECKED (dry-run)\n'
      printf 'PHASE process-spawned . NO (no browser started)\n'
      printf 'PHASE browser-ready ... NO (no browser started)\n'
      printf 'INFO  app-wide window sample: not started (informational only)\n'
      printf 'VERDICT: PREVIEW — dry-run output only; nothing was created or started\n'
    else
      note "evidence dir: $EVIDENCE_DIR"
      note "runbook: $RUNBOOK"
      printf '\n===== PHASE STATUS (read-only; nothing launched) =====\n'
      READINESS_RC=0
      READINESS_OUT="$("$PYTHON" "$READINESS_TOOL" --evidence "$EVIDENCE_DIR" 2>&1)" || READINESS_RC=$?
      printf '%s\n' "$READINESS_OUT"
    fi

    if [ "${READINESS_RC:-0}" -eq 2 ]; then
      printf '\nFAIL: the signed phase failed — do NOT launch this build.\n'
      printf 'NEXT: run scripts/build.sh (mandatory Developer ID signing, no fallback),\n'
      printf 'then re-run: tests/phase3-human-ownership-manual.sh prepare --evidence %s\n' "$(shell_quote "$EVIDENCE_DIR")"
      exit 2
    fi

    printf '\n===== NEXT STEPS (3) =====\n'
    printf '1) Launch (keep the terminal running):\n\n   %s\n\n' "$LAUNCH_CMD"
    printf '2) Wait for readiness in a SECOND terminal (read-only; this development\n'
    printf '   build was observed to spend ~3 minutes in the dyld mapping stage on the\n'
    printf '   first signed cold start):\n\n'
    printf '   tests/phase3-human-ownership-manual.sh ready --wait --evidence %s\n\n' "$(shell_quote "$EVIDENCE_DIR")"
    if [ "$DRY_RUN" = "1" ]; then
      printf '3) Only after it prints READY, follow the runbook (re-run prepare without\n'
      printf '   --dry-run to write it; the full text is printed below only with\n'
      printf '   --print-runbook).\n'
    else
      printf '3) Only after it prints READY, follow the runbook (Dock click etc.):\n\n   %s\n' "$RUNBOOK"
    fi
    printf '\nNOTE: the "Known-unsupported follow-up areas" list in the runbook is\n'
    printf 'informational future work — NOT an error and NOT part of today'"'"'s steps.\n'

    if [ "$PRINT_RUNBOOK" = "1" ]; then
      printf '\n===== MANUAL RUNBOOK (you run these; the script does not) =====\n'
      cat "$RUNBOOK"
      printf '================================================================\n'
    fi
    if [ "$DRY_RUN_RUNBOOK" = "1" ]; then
      rm -f "$RUNBOOK"
    fi
    # Success: keep the helpers running for the manual session.
    HELPERS_STARTED=0
    ;;

  ready)
    resolve_evidence
    # shellcheck disable=SC1090
    . "$EVIDENCE_DIR/state.env"
    note "readiness check (read-only; never launches, clicks, shows or touches anything)"
    set -- --evidence "$EVIDENCE_DIR"
    [ "$WAIT_FLAG" = "1" ] && set -- "$@" --wait
    [ "$JSON_FLAG" = "1" ] && set -- "$@" --json
    [ -n "$TIMEOUT_SECS" ] && set -- "$@" --timeout "$TIMEOUT_SECS"
    "$PYTHON" "$READINESS_TOOL" "$@"
    ;;

  collect)
    resolve_evidence
    # shellcheck disable=SC1090
    . "$EVIDENCE_DIR/state.env"
    note "collecting evidence (read-only; the browser is not touched)"
    "$PYTHON" "$COLLECT_TOOL" \
      --log "$BROWSER_LOG" \
      --samples "$SAMPLES" \
      --sink "$SINK_JSONL" \
      --report-dir "$EVIDENCE_DIR" \
      --devtools-port "$DEVTOOLS_PORT"
    note "report: $EVIDENCE_DIR/report.md"
    ;;

  stop)
    resolve_evidence
    # shellcheck disable=SC1090
    . "$EVIDENCE_DIR/state.env"
    stop_helper_by_pid "${WATCHER_PID:-}" "$SAMPLES" "watcher"
    stop_helper_by_pid "${SINK_PID:-}" "$SINK_JSONL" "sink"
    note "passive helpers stopped (your browser was not touched)"
    ;;

  status)
    resolve_evidence
    cat "$EVIDENCE_DIR/state.env"
    ;;

  *) usage; exit 2 ;;
esac
