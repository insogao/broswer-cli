#!/usr/bin/env bash
# Browser CLI Gate F (parallel background windows) harness — OFFLINE ONLY.
#
# NO runtime executor is authorized or present in this slice: this script never
# launches the browser, never hands off to an app-launching helper, never
# shows/activates/reveals/minimizes anything and never opens a window. The
# execution token is rejected fail-closed. Only dry-run/check/help exist.
#
# It prints the future-runtime contract (default) and validates an evidence dir
# offline with tests/tools/parallel-evidence-check.py. Future runtime requires a
# new explicit task after the gates (Gate C remains BLOCKED).
#
# Screenshots are deliberately NOT part of this contract: per-window capture
# remains a Gate C dependency. This slice does not claim screenshot coverage
# and Gate F is NOT passed.
#
# Usage: tests/phase6-parallel-windows.sh [dry-run|check] [--evidence DIR]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'PARALLEL-WINDOWS: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
BIN="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

WATCHER_SRC="$SCRIPT_DIR/tools/onscreen-window-watch.m"
CHECK_TOOL="$SCRIPT_DIR/tools/parallel-evidence-check.py"

die() { printf 'PARALLEL-WINDOWS: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[parallel-windows] %s\n' "$*"; }

# POSIX-shell safe single-quoting for copy-pasteable commands (bash 3.2
# compatible; no eval, no subprocess).
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

COMMAND="${1:-dry-run}"
[ $# -gt 0 ] && shift

# This slice has NO runtime executor: the execution token is rejected
# fail-closed wherever it appears, and no app-launching helper is wired in.
case "$COMMAND" in
  --run|--execute)
    die "no runtime executor is authorized or present in this slice; future runtime requires a new explicit task after the gates"
    ;;
esac

# Pure function exposure for static tests (before the option parser).
if [ "$COMMAND" = "__shell_quote" ]; then
  [ $# -gt 0 ] || die "__shell_quote needs a value"
  shell_quote "$1"
  printf '\n'
  exit 0
fi

EVIDENCE_DIR=""

usage() {
  cat <<'EOF'
Usage: tests/phase6-parallel-windows.sh <command> [options]

Commands:
  dry-run   print the Gate F plan and offline evidence contract (default;
            nothing is created, started, launched or captured)
  check     validate an evidence dir offline (tests/tools/parallel-evidence-check.py;
            the evidence input is read-only and the checker only writes
            parallel-report.json/md)
  help      this message

Options:
  --evidence DIR   evidence dir for 'check'

There is deliberately NO runtime executor in this slice: the execution token is
rejected fail-closed, no browser is launched, no window is shown, and no
app-launching helper is wired in. Gate F is NOT passed. Future runtime requires
a new explicit task after the gates (Gate C remains BLOCKED).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --evidence) shift; [ $# -gt 0 ] || die "--evidence needs a value"; EVIDENCE_DIR="$1" ;;
    --run|--execute) die "no runtime executor is authorized or present in this slice; future runtime requires a new explicit task after the gates" ;;
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

case "$COMMAND" in
  help|--help|-h) usage; exit 0 ;;

  dry-run)
    [ -f "$WATCHER_SRC" ] || die "missing watcher source: $WATCHER_SRC"
    [ -f "$CHECK_TOOL" ] || die "missing evidence checker: $CHECK_TOOL"
    cat <<EOF
Gate F (parallel background windows) harness — DRY-RUN PLAN (default)

NO runtime executor is authorized or present in this slice: nothing is
created, started, launched or captured, no app-launching helper is wired in,
and the execution token is rejected fail-closed. This driver only prints the
future-runtime contract and validates evidence offline. Gate F is NOT passed.
Future runtime requires a new explicit task after the gates (Gate C remains
BLOCKED).

FUTURE RUNTIME CONTRACT (NOT implemented or authorized here)
  1. Developer ID signature preflight of the app bundle; ad-hoc builds are
     refused (same criteria as scripts/verify-signature.sh).
  2. Fresh "mktemp -d" profile + --use-mock-keychain (never the real profile).
  3. --browser-cli-background-presentation (managed background presentation).
  4. Passive app-identity watcher (tests/tools/onscreen-window-watch.m, 10ms)
     started BEFORE the launch; the on-screen count must stay 0 for the whole
     session, with raw samples kept as evidence.
  5. >= 3 background BrowserWindows created over the CDP pipe
     (tests/tools/cdp_pipe_lib.py, remote debugging pipe), each loading a
     DIFFERENT real HTTPS site; every window keeps its own active tab and its
     own page/session evidence. No window may be presented, activated or
     revealed at any point (no show/reveal/minimize compensation anywhere).
  6. Cookie/session semantics recorded per window for the session profile mode
     (shared-profile: one jar, readback in every window; isolated-profiles:
     distinct jars with no cross-readback).
  7. HUMAN/ASSIST non-interference: the user takes over one window (Dock click,
     manual) while the background windows keep running; their URLs/tabs stay
     untouched and no presentation allow appears for them.
  8. Stop by exact PID and discard the profile.

PLANNED SESSION COMMAND (template for a later slice; never run here)
  $(shell_quote "$BIN") --no-first-run --use-mock-keychain \\
      --user-data-dir=<mktemp-profile> --remote-debugging-pipe \\
      --enable-logging=stderr --browser-cli-background-presentation \\
      --no-startup-window > <evidence>/browser.log 2>&1

WINDOW MATRIX (fixed in checker code; deleting / renaming / optional-izing a
window can never reach rc=0)
  w1  background  real https  own active tab
  w2  background  real https  own active tab
  w3  background  real https  own active tab
  (>= 3 background windows total)

EVIDENCE CONTRACT (<evidence>/)
  parallel-plan.json    session contract (signed/mock-keychain/managed) + cookie_mode + window matrix
  windows.json          per window: name, window_id, url, active_tab_id, loaded
  page-content.json     per window: title/body/url read from the real page
  cookie-evidence.json  per window: jar_id, readback_value, readback_ok
  human-assist.json     HUMAN/ASSIST window id + per-window untouched / presentation_allows
  watcher-samples.txt   app-identity watcher RAW samples (10ms) + final max= line
  browser.log           BROWSER_CLI_PRESENTATION policy lines

VERDICTS (tests/tools/parallel-evidence-check.py, fail closed)
  PASS / NOT-EVALUATED / CONTRADICTION. Fake passes it rejects:
   - fewer than 3 background windows, a required window deleted/renamed/marked
     optional (the matrix lives in checker code)
   - duplicate window ids, duplicate URLs, non-https URLs, duplicate active tabs
   - raw watcher samples missing or inconsistent with the final max= line (a
     final-only max=0 is NOT evidence), or any sample count > 0
   - any presentation allow (show-and-activate/show-inactive/minimize) or AppKit allow
   - cookie/session evidence contradicting the declared cookie_mode
   - a HUMAN/ASSIST window whose takeover touched a background window
  Exit codes: 0 = every required check PASS; 1 = contradiction; 3 = insufficient
  evidence (INSUFFICIENT-EVIDENCE). The evidence input is read-only; the
  checker only writes parallel-report.json/md.

NO RUNTIME EXECUTOR
  The automatic background-subset logic (signature gates, watcher upper-bound
  rule, window/tab/URL distinctness, DOM evidence, cookie readback, policy
  allows) exists only as pure offline helpers and fixtures under tests/tools
  (--self-test / fixture arguments). Those helpers have no launch path, no
  subprocess use, no signing execution and no CDP import, and this driver never
  hands off to them. Any future runtime executor requires a new explicit task
  after the gates.

SCREENSHOTS: NOT part of this contract. Per-window capture remains a Gate C
dependency; the Gate C offline checker is BLOCKED (rework pending) and this
slice does not claim screenshot coverage.

STATUS: Gate F NOT passed; this slice is offline-only: dry-run plan + offline
checker + pure evidence helpers/fixtures. No runtime executor is authorized or
present; there is no runtime evidence. Run 'check --evidence DIR' to validate
an evidence dir offline.
EOF
    ;;

  check)
    [ -n "$EVIDENCE_DIR" ] || die "--evidence DIR is required for check"
    [ -d "$EVIDENCE_DIR" ] || die "no such evidence dir: $EVIDENCE_DIR"
    note "validating Gate F evidence (evidence input is read-only; only parallel-report.json/md are written; nothing is launched or captured)"
    note "exit codes: 0 = every required check PASS, 1 = contradiction, 3 = insufficient evidence"
    CHECK_RC=0
    "$PYTHON" "$CHECK_TOOL" --evidence "$EVIDENCE_DIR" || CHECK_RC=$?
    note "report: $EVIDENCE_DIR/parallel-report.md"
    exit "$CHECK_RC"
    ;;

  *) usage; exit 2 ;;
esac
