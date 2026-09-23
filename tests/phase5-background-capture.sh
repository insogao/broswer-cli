#!/usr/bin/env bash
# Browser CLI Gate C (BACKGROUND_CAPTURE) no-window acceptance harness — PLAN
# AND OFFLINE CHECK ONLY.
#
# This slice has NO execution mode: it never launches the browser, never adds a
# --run path, never shows/activates/reveals/minimizes anything and never opens a
# window. It prints the execution plan (default) and validates evidence produced
# by a future execution slice with tests/tools/capture-evidence-check.py.
#
# Gate C itself is NOT passed by this slice: there is no runtime evidence yet.
#
# Usage: tests/phase5-background-capture.sh [dry-run|check] [--evidence DIR]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
OUT_NAME="${OUT_NAME:-Dev}"
DEPOT_TOOLS="${DEPOT_TOOLS:-$HOME/dev/depot_tools}"

IDENTITY_FILE="$REPO_ROOT/config/product-identity.env"
[ -f "$IDENTITY_FILE" ] || { printf 'BACKGROUND-CAPTURE: FAIL: missing identity config %s\n' "$IDENTITY_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
. "$IDENTITY_FILE"
: "${BROWSER_CLI_PRODUCT_NAME:?missing BROWSER_CLI_PRODUCT_NAME}"
BIN="$CHROMIUM_ROOT/src/out/$OUT_NAME/${BROWSER_CLI_PRODUCT_NAME}.app/Contents/MacOS/${BROWSER_CLI_PRODUCT_NAME}"

WATCHER_SRC="$SCRIPT_DIR/tools/onscreen-window-watch.m"
CDP_LIB="$SCRIPT_DIR/tools/cdp_pipe_lib.py"
CHECK_TOOL="$SCRIPT_DIR/tools/capture-evidence-check.py"

die() { printf 'BACKGROUND-CAPTURE: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf '[background-capture] %s\n' "$*"; }

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

# Pure function exposure for static tests (before the option parser, which
# would otherwise reject a positional value).
if [ "$COMMAND" = "__shell_quote" ]; then
  [ $# -gt 0 ] || die "__shell_quote needs a value"
  shell_quote "$1"
  printf '\n'
  exit 0
fi

EVIDENCE_DIR=""

usage() {
  cat <<'EOF'
Usage: tests/phase5-background-capture.sh <command> [options]

Commands:
  dry-run   print the Gate C execution plan and evidence contract (default;
            nothing is created, started, launched or captured)
  check     validate an evidence dir produced by a future execution slice
            (tests/tools/capture-evidence-check.py; the evidence input is
            read-only and the checker only writes capture-report.json/md)
  help      this message

Options:
  --evidence DIR   evidence dir for 'check'

There is deliberately NO execution mode in this slice: --run/--execute are
rejected, no browser is launched, no window is shown and Gate C is not passed.
The plan describes what a later execution slice must enforce (Developer ID
signature preflight, mock keychain, fresh mktemp profile, managed background
presentation, app-identity watcher, CDP-pipe screenshots, fail-closed verdicts).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --evidence) shift; [ $# -gt 0 ] || die "--evidence needs a value"; EVIDENCE_DIR="$1" ;;
    --run|--execute) die "this slice has no execution mode (dry-run/static only); Gate C is NOT passed" ;;
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
    [ -f "$CDP_LIB" ] || die "missing shared CDP library: $CDP_LIB"
    [ -f "$CHECK_TOOL" ] || die "missing evidence checker: $CHECK_TOOL"
    cat <<EOF
Gate C (BACKGROUND_CAPTURE) no-window acceptance harness — DRY-RUN PLAN ONLY

This slice has NO execution mode: no --run, no prepare, no browser launch and
no visible window. It prints the plan and can validate evidence from a future
execution slice. Gate C is NOT passed.

EXECUTION REQUIREMENTS (enforced by a later slice before any launch)
  1. Developer ID signature preflight of the app bundle; ad-hoc builds are
     refused (same criteria as scripts/verify-signature.sh).
  2. Fresh "mktemp -d" profile + --use-mock-keychain (never the real profile).
  3. --browser-cli-background-presentation (managed background presentation).
  4. Passive app-identity watcher (tests/tools/onscreen-window-watch.m, 10ms)
     started BEFORE the launch; the on-screen count must stay 0 for the whole
     session (any non-zero sample = CONTRADICTION).
  5. Screenshots only through the browser's own CDP surface/renderer path over
     the remote debugging pipe (tests/tools/cdp_pipe_lib.py). No show/activate/
     reveal/minimize/temporary-normal compensation anywhere; a presentation
     allow in the policy log = CONTRADICTION.
  6. One isolated session covers every case; the browser is stopped by exact
     PID and the profile is discarded.

PLANNED SESSION COMMAND (template for a later slice; never run here)
  $(shell_quote "$BIN") --no-first-run --use-mock-keychain \\
      --user-data-dir=<mktemp-profile> --remote-debugging-pipe \\
      --enable-logging=stderr --browser-cli-background-presentation \\
      --no-startup-window > <evidence>/browser.log 2>&1

CASES (same isolated session; raw PNG + sha256 + time series per capture)
  static   static HTML; 2 captures; hashes must be identical
  dynamic  JS/CSS animation; >=2 captures; hashes must differ
  canvas   canvas rendering; >=2 captures; hashes must differ
  long     long page; >=3 scroll-step captures; hashes must differ
  video    optional; deterministic local material is not built in this slice ->
           NOT-EVALUATED (never a fake PASS)

EVIDENCE CONTRACT (<evidence>/)
  watcher-samples.txt   app-identity watcher time series (sample ts= count=)
  browser.log           BROWSER_CLI_PRESENTATION policy/host/appkit lines
  capture-plan.json     session contract flags (signed/mock-keychain/managed) + cases
  captures.json         per capture: case, kind, file, sha256, size, captured_at, scroll_index
  captures/*.png        raw PNG screenshots
  page-content.json     per case: title/body/url read from the real page

VERDICTS (tests/tools/capture-evidence-check.py, fail closed)
  PASS / NOT-EVALUATED / CONTRADICTION. Fake passes it rejects:
   - a capture-plan.json that drops / renames / optional-izes one of the four
     required cases: the matrix static/dynamic/canvas/long-scroll is fixed in
     checker code and only the video case may be optional
   - watcher sample-maximum vs final max= inconsistency (a final max=0 must
     never mask a count>0 sample; the raw samples are authoritative)
   - watcher max > 0 (any on-screen window during the session)
   - empty / blank / suspiciously small PNG
   - sha256 or size mismatch against captures.json
   - identical pixels across different cases
   - dynamic/canvas/long-scroll captures all identical (no change captured)
   - static captures differing
   - any presentation allow (show-and-activate/show-inactive/minimize) or an
     AppKit-level allow in the policy log
   - a session without signed app + mock keychain + managed background

STATUS: Gate C NOT passed; this slice is dry-run/static only (no runtime
evidence). Run 'check --evidence DIR' to validate a future session.
EOF
    ;;

  check)
    [ -n "$EVIDENCE_DIR" ] || die "--evidence DIR is required for check"
    [ -d "$EVIDENCE_DIR" ] || die "no such evidence dir: $EVIDENCE_DIR"
    note "validating Gate C evidence (evidence input is read-only; only capture-report.json/md are written; nothing is launched or captured)"
    note "exit codes: 0 = every required check PASS, 1 = contradiction, 3 = insufficient evidence"
    CHECK_RC=0
    "$PYTHON" "$CHECK_TOOL" --evidence "$EVIDENCE_DIR" || CHECK_RC=$?
    note "report: $EVIDENCE_DIR/capture-report.md"
    exit "$CHECK_RC"
    ;;

  --run|--execute)
    die "this slice has no execution mode (dry-run/static only); Gate C is NOT passed"
    ;;

  *) usage; exit 2 ;;
esac
