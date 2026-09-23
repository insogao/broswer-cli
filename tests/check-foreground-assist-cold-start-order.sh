#!/usr/bin/env bash
# Browser CLI static gate: cold-start foreground-assist ordering.
#
# Verifies, without building or running a browser, that in the patched Chromium
# source tree:
#   1. chrome_browser_main.cc derives the effective cold-start command line via
#      browser_cli::CommandLineForColdStartStartup() and passes it to
#      StartupBrowserCreator::Start(); the raw process command line is no longer
#      passed there.
#   2. startup_browser_creator.cc still suppresses URL/window launching under
#      --no-startup-window (silent_launch early return before LaunchBrowser()),
#      which is what the sanitized line relies on.
#   3. foreground_assist.cc strips the blocked launch switches / URL args and
#      adds --no-startup-window only when an assist intent is present.
#   4. The ProcessSingleton path consumes an assist invocation (handler before
#      ordinary launch processing, returning on consume).
#
# Exit: 0 = all checks pass; 1 = at least one check failed. Read-only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHROMIUM_ROOT="${CHROMIUM_ROOT:-$HOME/dev/chromium}"
CHROMIUM_SRC="${CHROMIUM_SRC:-$CHROMIUM_ROOT/src}"

MAIN="$CHROMIUM_SRC/chrome/browser/chrome_browser_main.cc"
ASSIST="$CHROMIUM_SRC/chrome/browser/browser_cli/foreground_assist.cc"
STARTUP="$CHROMIUM_SRC/chrome/browser/ui/startup/startup_browser_creator.cc"

FAILS=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }

for f in "$MAIN" "$ASSIST" "$STARTUP"; do
  [ -f "$f" ] || { printf 'ERROR missing source file: %s (set CHROMIUM_ROOT?)\n' "$f" >&2; exit 2; }
done

# 1. Cold-start call site uses the sanitized launch command line.
if grep -q 'browser_cli::CommandLineForColdStartStartup(' "$MAIN"; then
  pass "cold start builds browser_cli::CommandLineForColdStartStartup()"
else
  fail "cold start does not call CommandLineForColdStartStartup()"
fi
if grep -q 'browser_creator_->Start(launch_command_line, base::FilePath(),' "$MAIN"; then
  pass "StartupBrowserCreator::Start() receives the sanitized launch line"
else
  fail "StartupBrowserCreator::Start() does not receive launch_command_line"
fi
if grep -q 'browser_creator_->Start(\*base::CommandLine::ForCurrentProcess' "$MAIN"; then
  fail "Start() still receives the raw process command line"
else
  pass "Start() never receives the raw process command line"
fi

# 2. Upstream silence guaranteed by --no-startup-window in ProcessCmdLineImpl.
no_startup_line="$(grep -n 'command_line.HasSwitch(switches::kNoStartupWindow)' "$STARTUP" | awk -F: '$1 > 1008 {print $1; exit}')"
silent_guard_line="$(grep -n 'if (silent_launch) {' "$STARTUP" | awk -F: '$1 > 1008 {print $1; exit}')"
launch_browser_line="$(grep -n 'LaunchBrowser((profile == last_used_profile)' "$STARTUP" | awk -F: '{print $1; exit}')"
if [ -n "$no_startup_line" ] && [ -n "$silent_guard_line" ] && [ -n "$launch_browser_line" ] \
   && [ "$no_startup_line" -lt "$silent_guard_line" ] && [ "$silent_guard_line" -lt "$launch_browser_line" ]; then
  pass "--no-startup-window early return precedes LaunchBrowser() in ProcessCmdLineImpl"
else
  fail "--no-startup-window silent_launch ordering changed (no-startup=$no_startup_line silent=$silent_guard_line launch=$launch_browser_line)"
fi

# 3. Sanitizer semantics in foreground_assist.cc.
if grep -q 'if (!HasForegroundAssistIntent(command_line))' "$ASSIST" \
   && grep -q 'sanitized.AppendSwitch(switches::kNoStartupWindow);' "$ASSIST"; then
  pass "assist sanitizer adds --no-startup-window and passes ordinary lines through"
else
  fail "assist sanitizer is missing the intent gate or --no-startup-window"
fi

# 4. ProcessSingleton consumes assist invocations before ordinary processing.
singleton_assist_line="$(grep -n 'if (browser_cli::HandleForegroundAssistRequest(command_line)) {' "$MAIN" | awk -F: '{print $1; exit}')"
ordinary_launch_line="$(grep -n 'StartupBrowserCreator::ProcessCommandLineAlreadyRunning(' "$MAIN" | awk -F: '{print $1; exit}')"
if [ -n "$singleton_assist_line" ] && [ -n "$ordinary_launch_line" ] \
   && [ "$singleton_assist_line" -lt "$ordinary_launch_line" ]; then
  pass "ProcessSingleton: assist handler precedes ordinary launch processing"
else
  fail "ProcessSingleton: assist handler ordering changed (assist=$singleton_assist_line ordinary=$ordinary_launch_line)"
fi
if sed -n "${singleton_assist_line},$((singleton_assist_line + 2))p" "$MAIN" | grep -q 'return;'; then
  pass "ProcessSingleton: consumed assist invocation returns before ordinary processing"
else
  fail "ProcessSingleton: consumed assist invocation does not return"
fi

echo
if [ "$FAILS" -gt 0 ]; then
  printf 'cold-start assist ordering gate: FAIL (%d check(s) failed)\n' "$FAILS"
  exit 1
fi
printf 'cold-start assist ordering gate: PASS\n'
