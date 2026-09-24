#!/usr/bin/env python3
"""Static, browser-free checker for the Browser CLI command-line intent
arbitration (patch 013).

Root cause it guards: the explicit `show` handler used to run before the
foreground-assist handler in both production entry points
(chrome/browser/chrome_browser_main.cc), so a command line carrying both the
show switch and an assist request/release was presented through the
USER_EXPLICIT path before the assist classifier could deny it
(kBlockedByConflictingSwitches was unreachable).

The checker reads chrome/browser/chrome_browser_main.cc only. It never launches
a browser or any process, never touches a profile/keychain, and never builds.

Usage:
  python3 tests/tools/command-line-intent-arbitration-check.py [--checkout DIR]
  python3 tests/tools/command-line-intent-arbitration-check.py --self-test

Exit codes: 0 = all checks PASS; 1 = at least one check FAIL; 2 = usage error.
"""

import argparse
import os
import re
import sys

DEFAULT_CHECKOUT = os.path.expanduser("~/dev/chromium/src")
MAIN_CC_PATH = os.path.join("chrome", "browser", "chrome_browser_main.cc")

ARBITRATION = "ArbitrateBrowserCliCommandLineIntent("
ASSIST_HANDLER = "HandleForegroundAssistRequest("
SHOW_HANDLER = "HandleUserExplicitShowRequest("
ASSIST_CASE = "BrowserCliCommandLineIntent::kForegroundAssist"
SHOW_CASE = "BrowserCliCommandLineIntent::kUserExplicitShow"


def slice_braced_block(text, start):
    """Returns the brace-balanced block starting at the first `{` at or after
    |start| (including the braces), or "" when not found."""
    brace = text.find("{", start)
    if brace == -1:
        return ""
    depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace:index + 1]
    return ""


def arbitration_blocks(text):
    """[(call_position, switch_body)] for every arbitration call site."""
    return [
        (match.start(), slice_braced_block(text, match.start()))
        for match in re.finditer(re.escape(ARBITRATION), text)
    ]


def check_arbitration_sites(text):
    blocks = arbitration_blocks(text)
    if len(blocks) < 2:
        return (False, "arbitration-sites: expected both production entry points "
                       "to use %s, found %d" % (ARBITRATION, len(blocks)))
    return (True, "arbitration-sites: %d entry points use the shared arbitration"
            % len(blocks))


def check_case_assignment(text):
    blocks = arbitration_blocks(text)
    if not blocks:
        return (False, "case-assignment: no arbitration site")
    for index, (_, block) in enumerate(blocks):
        if not block:
            return (False, "case-assignment: site %d has no switch body"
                    % (index + 1))
        assist_label = block.find(ASSIST_CASE)
        show_label = block.find(SHOW_CASE)
        if assist_label == -1 or show_label == -1:
            return (False, "case-assignment: site %d lacks the assist/show case "
                           "labels" % (index + 1))
        if assist_label > show_label:
            return (False, "case-assignment: site %d lists the show case before "
                           "the assist case" % (index + 1))
        assist_body = block[assist_label:show_label]
        if ASSIST_HANDLER not in assist_body:
            return (False, "case-assignment: site %d assist case does not call "
                           "%s" % (index + 1, ASSIST_HANDLER))
        if SHOW_HANDLER in assist_body:
            return (False, "case-assignment: site %d assist case calls the show "
                           "handler" % (index + 1))
        if SHOW_HANDLER not in block[show_label:]:
            return (False, "case-assignment: site %d show case does not call %s"
                    % (index + 1, SHOW_HANDLER))
    return (True, "case-assignment: assist case consumes first, show case separate")


def check_handlers_inside_arbitration(text):
    ranges = [
        (start, start + len(block))
        for start, block in arbitration_blocks(text)
        if block
    ]
    for match in re.finditer(re.escape(SHOW_HANDLER), text):
        if not any(start <= match.start() < end for start, end in ranges):
            return (False, "show-outside-arbitration: %s is called outside the "
                           "arbitration switch (show-first order)" % SHOW_HANDLER)
    for match in re.finditer(re.escape(ASSIST_HANDLER), text):
        if not any(start <= match.start() < end for start, end in ranges):
            return (False, "assist-outside-arbitration: %s is called outside the "
                           "arbitration switch" % ASSIST_HANDLER)
    return (True, "handlers-inside-arbitration: both handlers only run inside "
                  "the arbitration switch")


def run_checks_on_text(text):
    return [
        check_arbitration_sites(text),
        check_case_assignment(text),
        check_handlers_inside_arbitration(text),
    ]


def run_checks(checkout):
    path = os.path.join(checkout, MAIN_CC_PATH)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        return [(False, "cannot read checkout: %s" % error)]
    return run_checks_on_text(text)


# --------------------------------------------------------------------------
# Self-test fixtures (synthetic source text, no checkout, no browser)
# --------------------------------------------------------------------------

GOOD_SOURCE = """\
void ProcessSingletonNotificationCallbackImpl(
    base::CommandLine command_line,
    const base::FilePath& current_directory) {
#if BUILDFLAG(IS_MAC)
  switch (browser_cli::ArbitrateBrowserCliCommandLineIntent(command_line)) {
    case browser_cli::BrowserCliCommandLineIntent::kForegroundAssist:
      browser_cli::HandleForegroundAssistRequest(command_line);
      return;
    case browser_cli::BrowserCliCommandLineIntent::kUserExplicitShow:
      if (browser_cli::HandleUserExplicitShowRequest(command_line,
                                                     current_directory)) {
        return;
      }
      break;
    case browser_cli::BrowserCliCommandLineIntent::kOrdinary:
      break;
  }
#endif
}

void PreMainMessageLoopRunImpl() {
  if (browser_creator_->Start(launch_command_line, base::FilePath(),
                              profile_info, last_opened_profiles)) {
#if BUILDFLAG(IS_MAC)
    switch (browser_cli::ArbitrateBrowserCliCommandLineIntent(
        *base::CommandLine::ForCurrentProcess())) {
      case browser_cli::BrowserCliCommandLineIntent::kForegroundAssist:
        browser_cli::HandleForegroundAssistRequest(
            *base::CommandLine::ForCurrentProcess());
        break;
      case browser_cli::BrowserCliCommandLineIntent::kUserExplicitShow:
        browser_cli::HandleUserExplicitShowRequest(
            *base::CommandLine::ForCurrentProcess(), base::FilePath());
        break;
      case browser_cli::BrowserCliCommandLineIntent::kOrdinary:
        break;
    }
#endif
  }
}
"""

SINGLETON_SWITCH = """\
  switch (browser_cli::ArbitrateBrowserCliCommandLineIntent(command_line)) {
    case browser_cli::BrowserCliCommandLineIntent::kForegroundAssist:
      browser_cli::HandleForegroundAssistRequest(command_line);
      return;
    case browser_cli::BrowserCliCommandLineIntent::kUserExplicitShow:
      if (browser_cli::HandleUserExplicitShowRequest(command_line,
                                                     current_directory)) {
        return;
      }
      break;
    case browser_cli::BrowserCliCommandLineIntent::kOrdinary:
      break;
  }
"""

COLD_SWITCH = """\
    switch (browser_cli::ArbitrateBrowserCliCommandLineIntent(
        *base::CommandLine::ForCurrentProcess())) {
      case browser_cli::BrowserCliCommandLineIntent::kForegroundAssist:
        browser_cli::HandleForegroundAssistRequest(
            *base::CommandLine::ForCurrentProcess());
        break;
      case browser_cli::BrowserCliCommandLineIntent::kUserExplicitShow:
        browser_cli::HandleUserExplicitShowRequest(
            *base::CommandLine::ForCurrentProcess(), base::FilePath());
        break;
      case browser_cli::BrowserCliCommandLineIntent::kOrdinary:
        break;
    }
"""

SINGLETON_SEQUENTIAL = """\
  if (browser_cli::HandleUserExplicitShowRequest(command_line,
                                                 current_directory)) {
    return;
  }
  if (browser_cli::HandleForegroundAssistRequest(command_line)) {
    return;
  }
"""

COLD_SEQUENTIAL = """\
    browser_cli::HandleUserExplicitShowRequest(
        *base::CommandLine::ForCurrentProcess(), base::FilePath());
    browser_cli::HandleForegroundAssistRequest(
        *base::CommandLine::ForCurrentProcess());
"""

SHOW_FIRST_SOURCE = GOOD_SOURCE.replace(SINGLETON_SWITCH, SINGLETON_SEQUENTIAL)
SHOW_FIRST_SOURCE = SHOW_FIRST_SOURCE.replace(COLD_SWITCH, COLD_SEQUENTIAL)

ONE_SITE_ONLY_SOURCE = GOOD_SOURCE.replace(COLD_SWITCH, COLD_SEQUENTIAL)

SWAPPED_CASES_SOURCE = GOOD_SOURCE.replace(
    """      case browser_cli::BrowserCliCommandLineIntent::kForegroundAssist:
        browser_cli::HandleForegroundAssistRequest(
            *base::CommandLine::ForCurrentProcess());
        break;""",
    """      case browser_cli::BrowserCliCommandLineIntent::kForegroundAssist:
        browser_cli::HandleUserExplicitShowRequest(
            *base::CommandLine::ForCurrentProcess(), base::FilePath());
        break;""")

SHOW_BEFORE_ARBITRATION_SOURCE = GOOD_SOURCE.replace(
    SINGLETON_SWITCH,
    "  browser_cli::HandleUserExplicitShowRequest(command_line,\n"
    "                                               current_directory);\n"
    + SINGLETON_SWITCH)

ASSIST_CASE_MISSING_SOURCE = GOOD_SOURCE.replace(
    """    case browser_cli::BrowserCliCommandLineIntent::kForegroundAssist:
      browser_cli::HandleForegroundAssistRequest(command_line);
      return;""",
    """    case browser_cli::BrowserCliCommandLineIntent::kForegroundAssist:
      return;""")


def failing_names(results):
    return sorted(
        name for ok, name in ((ok, detail.split(":")[0])
                              for ok, detail in results)
        if not ok
    )


def run_self_test():
    failures = []
    passed = []

    def check(name, condition, detail=""):
        if condition:
            passed.append(name)
            print("SELFTEST PASS %s" % name)
        else:
            failures.append(name)
            print("SELFTEST FAIL %s %s" % (name, detail))

    def expect_fail(fixture_name, expected_prefix, text):
        failed = failing_names(run_checks_on_text(text))
        check(
            "%s fails with %s" % (fixture_name, expected_prefix),
            expected_prefix in failed,
            "failed=%s" % failed,
        )

    results = run_checks_on_text(GOOD_SOURCE)
    check("complete fixture passes all checks",
          all(ok for ok, _ in results),
          "; ".join(detail for ok, detail in results if not ok))

    expect_fail("show-first-sequential", "arbitration-sites", SHOW_FIRST_SOURCE)
    expect_fail("one-site-only", "show-outside-arbitration", ONE_SITE_ONLY_SOURCE)
    expect_fail("swapped-cases", "case-assignment", SWAPPED_CASES_SOURCE)
    expect_fail("show-before-arbitration", "show-outside-arbitration",
                SHOW_BEFORE_ARBITRATION_SOURCE)
    expect_fail("assist-case-missing", "case-assignment",
                ASSIST_CASE_MISSING_SOURCE)

    print("SELFTEST SUMMARY: %d passed, %d failed" % (len(passed), len(failures)))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        description="Static checker for the Browser CLI command-line intent "
                    "arbitration (patch 013; no browser, no build)."
    )
    parser.add_argument("--checkout", default=DEFAULT_CHECKOUT,
                        help="Chromium checkout to inspect")
    parser.add_argument("--self-test", action="store_true",
                        help="run synthetic fixture negatives/positives")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    if not os.path.isdir(args.checkout):
        print("command-line-intent-arbitration-check: no such checkout: %s"
              % args.checkout, file=sys.stderr)
        return 2

    results = run_checks(args.checkout)
    failed = 0
    for ok, detail in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", detail))
        if not ok:
            failed += 1
    print("COMMAND-LINE-INTENT-ARBITRATION: %s (%d checks, %d failed)"
          % ("OK" if failed == 0 else "FAIL", len(results), failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
