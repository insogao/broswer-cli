#!/usr/bin/env python3
"""Static, browser-free wiring checker for the user-menu origin patches (010, 012).

Patch 010 has two mechanisms that a pure unit test cannot see through the
compiler alone:

  * the three AppKit menu callbacks in chrome/browser/app_controller_mac.mm
    must scope views::ForegroundOrigin::kUserExplicit (not kInternal) while
    they present the window the user asked for;
  * TabRestorer must capture views::CurrentForegroundOrigin() by value when the
    restore request is made (RestoreByID) and re-apply the captured value around
    the delayed restore callback (TabRestoreServiceLoaded) through the shared
    RunWithForegroundOrigin() helper.

Patch 012 closes the History-menu gap in
chrome/browser/ui/cocoa/history_menu_cocoa_controller.mm: a click on the native
History menu is a user-explicit producer, so the origin must be carried by value
into OpenURLForItem() on both the direct profile path and the RunInProfileSafely
BindOnce closure, and scoped inside OpenURLForItem() around the real restore /
navigation. RAII lifetime is lexical, so the scope must be declared at the
function-body top brace depth (not nested inside one branch) and before both
producer calls; a scope only in the outer Objective-C method, a scope nested in
the session_id if block, a missing branch origin, a comment-only mention, or a
shared-helper form that cannot be statically proven to wrap both branches must
all fail.

This checker reads the source files only. It never launches a browser or any
process, never touches a profile/keychain, and never builds anything.

Usage:
  python3 tests/tools/user-menu-origin-wiring-check.py [--checkout DIR]
  python3 tests/tools/user-menu-origin-wiring-check.py --self-test

Exit codes: 0 = all checks PASS; 1 = at least one check FAIL; 2 = usage error.
"""

import argparse
import os
import re
import sys

DEFAULT_CHECKOUT = os.path.expanduser("~/dev/chromium/src")

MM_PATH = os.path.join("chrome", "browser", "app_controller_mac.mm")
H_PATH = os.path.join("chrome", "browser", "app_controller_mac.h")
HISTORY_MM_PATH = os.path.join("chrome", "browser", "ui", "cocoa",
                               "history_menu_cocoa_controller.mm")
HISTORY_INCLUDE = '#include "ui/views/widget/browser_cli_foreground_origin.h"'
HISTORY_OPEN_URL_SIGNATURE = "void OpenURLForItem("
HISTORY_METHOD_SIGNATURE = (
    "- (void)openURLForItem:(const HistoryMenuBridge::HistoryItem*)node")
HISTORY_DIRECT_IF = "if (Profile* profile = _bridge->profile())"
HISTORY_RUN_IN_PROFILE = "app_controller_mac::RunInProfileSafely("
HISTORY_SHARED_HELPER = "RunWithForegroundOrigin("
HISTORY_SCOPE_PATTERN = re.compile(
    r"views::ScopedForegroundOrigin\s+\w+\(foreground_origin\)")

MENU_METHODS = [
    ("executeCommand", "- (void)executeCommand:(id)sender withProfile:(Profile*)profile"),
    ("showPreferences", "- (IBAction)showPreferencesForProfile:(Profile*)profile"),
    ("orderFrontAbout", "- (IBAction)orderFrontStandardAboutPanelForProfile:(Profile*)profile"),
]

RESTORE_BY_ID_SIGNATURE = "void TabRestorer::RestoreByID(Profile* profile, SessionID session_id)"
SERVICE_LOADED_SIGNATURE = "void TabRestorer::TabRestoreServiceLoaded("
RUN_WITH_ORIGIN_SIGNATURE = "void RunWithForegroundOrigin(views::ForegroundOrigin origin,"


def slice_objc_method(text, signature):
    """Returns the ObjC method body starting at a line beginning with
    |signature| and ending before the next line that starts a new top-level
    method (`- (` or `+ (`)."""
    lines = text.splitlines(keepends=True)
    out = []
    started = False
    for line in lines:
        if not started:
            if line.startswith(signature):
                started = True
                out.append(line)
            continue
        if line.startswith("- (") or line.startswith("+ ("):
            break
        out.append(line)
    return "".join(out)


def slice_cxx_function(text, signature):
    """Returns the C++ function body starting at a line beginning with
    |signature| and ending at the next line that is exactly `}`."""
    lines = text.splitlines(keepends=True)
    out = []
    started = False
    for line in lines:
        if not started:
            if line.startswith(signature):
                started = True
                out.append(line)
            continue
        out.append(line)
        if line.rstrip("\n") == "}":
            break
    return "".join(out)


def slice_if_block(text, header):
    """Returns the brace-balanced block of the first `if (...)` whose header
    starts with |header| (including the braces), or "" when not found."""
    start = text.find(header)
    if start == -1:
        return ""
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


def slice_call(text, marker):
    """Returns the paren-balanced call starting at the first occurrence of
    |marker| through its matching closing parenthesis, or "" when not found."""
    start = text.find(marker)
    if start == -1:
        return ""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def lexical_depth(text, position):
    """Brace depth at |position| (exclusive) relative to the start of |text|,
    ignoring braces inside string/char literals and // or /* */ comments. Used
    to prove that a declaration sits at the function-body top level, so its
    RAII lifetime really spans the following branches."""
    depth = 0
    index = 0
    length = len(text)
    while index < position and index < length:
        char = text[index]
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            newline = text.find("\n", index)
            index = length if newline == -1 else newline + 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if char in ('"', "'"):
            quote = char
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth


def strip_line_comments(text):
    """Drops // comments so a comment cannot satisfy a wiring check."""
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def check_menu_scope(mm_text, label, signature):
    body = strip_line_comments(slice_objc_method(mm_text, signature))
    if not body:
        return (False, "%s: method not found" % label)
    if "views::ScopedForegroundOrigin browser_cli_user_ui(" not in body:
        return (False, "%s: no ScopedForegroundOrigin scope" % label)
    if "views::ForegroundOrigin::kUserExplicit" not in body:
        return (False, "%s: scope is not kUserExplicit" % label)
    if "views::ForegroundOrigin::kInternal" in body:
        return (False, "%s: scope still uses kInternal" % label)
    return (True, "%s: kUserExplicit scope" % label)


def check_restore_by_id_capture(mm_text):
    body = strip_line_comments(slice_cxx_function(mm_text, RESTORE_BY_ID_SIGNATURE))
    if not body:
        return (False, "RestoreByID: function not found")
    if "new TabRestorer(" not in body:
        return (False, "RestoreByID: does not construct a TabRestorer")
    if "views::CurrentForegroundOrigin()" not in body:
        return (False, "RestoreByID: does not capture CurrentForegroundOrigin()")
    return (True, "RestoreByID: captures CurrentForegroundOrigin() by value")


def check_service_loaded_wrap(mm_text):
    body = strip_line_comments(slice_cxx_function(mm_text, SERVICE_LOADED_SIGNATURE))
    if not body:
        return (False, "TabRestoreServiceLoaded: function not found")
    if "RunWithForegroundOrigin(" not in body:
        return (False, "TabRestoreServiceLoaded: no RunWithForegroundOrigin() wrap")
    if "foreground_origin_" not in body:
        return (False, "TabRestoreServiceLoaded: does not re-apply the captured origin")
    if "DoRestoreTab" not in body:
        return (False, "TabRestoreServiceLoaded: does not run DoRestoreTab")
    return (True, "TabRestoreServiceLoaded: wraps DoRestoreTab with the captured origin")


def check_typed_origin_member(h_text, mm_text):
    header = strip_line_comments(h_text)
    if "views::ForegroundOrigin foreground_origin_;" not in header:
        return (False, "header: typed views::ForegroundOrigin member missing")
    if "int foreground_origin_" in header:
        return (False, "header: untyped int foreground_origin_ member present")
    if "static_cast<views::ForegroundOrigin>" in strip_line_comments(mm_text):
        return (False, "source: int-to-enum static_cast round-trip present")
    if "enum class ForegroundOrigin;" not in header:
        return (False, "header: missing opaque views::ForegroundOrigin declaration")
    return (True, "header: typed views::ForegroundOrigin member (no int round-trip)")


def check_helper_impl(mm_text):
    body = strip_line_comments(slice_cxx_function(mm_text, RUN_WITH_ORIGIN_SIGNATURE))
    if not body:
        return (False, "RunWithForegroundOrigin: implementation not found")
    if "views::ScopedForegroundOrigin" not in body:
        return (False, "RunWithForegroundOrigin: does not scope the origin")
    if ".Run()" not in body:
        return (False, "RunWithForegroundOrigin: does not run the callback")
    return (True, "RunWithForegroundOrigin: scopes the origin around the callback")


def check_history_include(mm_text):
    code = strip_line_comments(mm_text)
    if HISTORY_INCLUDE not in code:
        return (False, "history-include: missing browser_cli_foreground_origin.h")
    return (True, "history-include: includes the origin shim")


def check_history_signature(mm_text):
    body = strip_line_comments(
        slice_cxx_function(mm_text, HISTORY_OPEN_URL_SIGNATURE))
    if not body:
        return (False, "history-signature: OpenURLForItem not found")
    signature = body.split("{", 1)[0]
    if "views::ForegroundOrigin" not in signature:
        return (False, "history-signature: origin parameter missing")
    return (True, "history-signature: OpenURLForItem takes views::ForegroundOrigin")


def check_history_direct_call(mm_text):
    """The direct path must call OpenURLForItem(kUserExplicit, ...) inside the
    profile-loaded `if (Profile* profile = _bridge->profile())` block. A broad
    search over the whole method could let the deferred branch satisfy this."""
    method = strip_line_comments(slice_objc_method(mm_text, HISTORY_METHOD_SIGNATURE))
    if not method:
        return (False, "history-direct: openURLForItem: not found")
    block = slice_if_block(method, HISTORY_DIRECT_IF)
    if not block:
        return (False, "history-direct: profile-loaded branch not found")
    block = strip_line_comments(block)
    if not re.search(r"OpenURLForItem\([^;]*kUserExplicit", block, re.S):
        return (False, "history-direct: profile-loaded branch does not call "
                       "OpenURLForItem(kUserExplicit, ...)")
    return (True, "history-direct: profile-loaded branch passes kUserExplicit")


def check_history_deferred_bind(mm_text):
    """The deferred path must bind OpenURLForItem with kUserExplicit inside the
    RunInProfileSafely(...) callback argument itself, not merely somewhere in
    the method."""
    method = strip_line_comments(slice_objc_method(mm_text, HISTORY_METHOD_SIGNATURE))
    if not method:
        return (False, "history-deferred: openURLForItem: not found")
    call = slice_call(method, HISTORY_RUN_IN_PROFILE)
    if not call:
        return (False, "history-deferred: RunInProfileSafely call not found")
    call = strip_line_comments(call)
    if not re.search(r"base::BindOnce\(&OpenURLForItem,[^)]*kUserExplicit",
                     call, re.S):
        return (False, "history-deferred: RunInProfileSafely callback does not "
                       "bind OpenURLForItem with kUserExplicit")
    return (True, "history-deferred: RunInProfileSafely callback binds kUserExplicit")


def check_history_execution_scope(mm_text):
    """Accepts only the direct form: a ScopedForegroundOrigin declaration that
    uses the passed foreground_origin and sits at the OpenURLForItem
    function-body TOP lexical level (brace depth 0 relative to the function
    body), before both producer calls. RAII lifetime is lexical, so a scope
    nested inside one branch (for example only the session_id if block) would
    not cover the navigation branch even though its text precedes it. The
    shared RunWithForegroundOrigin helper form cannot be proven statically to
    wrap both branches, so it fails closed as unsupported."""
    body = strip_line_comments(
        slice_cxx_function(mm_text, HISTORY_OPEN_URL_SIGNATURE))
    if not body:
        return (False, "history-scope: OpenURLForItem not found")
    open_brace = body.find("{")
    if open_brace == -1:
        return (False, "history-scope: function body not found")
    scope_match = HISTORY_SCOPE_PATTERN.search(body)
    if not scope_match:
        if HISTORY_SHARED_HELPER in body:
            return (False, "history-scope: unsupported scope form (only a direct "
                           "ScopedForegroundOrigin at the function-body top "
                           "level can be verified)")
        return (False, "history-scope: no scope constructed from the passed "
                       "foreground_origin")
    base_depth = lexical_depth(body, open_brace + 1)
    if lexical_depth(body, scope_match.start()) - base_depth != 0:
        return (False, "history-scope: scope is not at the function-body top "
                       "level (it would not cover both branches)")
    restore_position = body.find("RestoreByID(")
    if restore_position == -1:
        return (False, "history-scope: session restore branch missing")
    navigate_position = body.find("Navigate(")
    if navigate_position == -1:
        return (False, "history-scope: URL navigation branch missing")
    if not (scope_match.start() < restore_position
            and scope_match.start() < navigate_position):
        return (False, "history-scope: scope does not precede both the restore "
                       "and the navigation calls")
    return (True, "history-scope: top-level origin scope covers restore and "
                  "navigation")


def run_checks(checkout):
    results = []
    mm_path = os.path.join(checkout, MM_PATH)
    h_path = os.path.join(checkout, H_PATH)
    history_path = os.path.join(checkout, HISTORY_MM_PATH)
    try:
        with open(mm_path, encoding="utf-8") as handle:
            mm_text = handle.read()
        with open(h_path, encoding="utf-8") as handle:
            h_text = handle.read()
        with open(history_path, encoding="utf-8") as handle:
            history_text = handle.read()
    except OSError as error:
        return [(False, "cannot read checkout: %s" % error)]

    for label, signature in MENU_METHODS:
        results.append(check_menu_scope(mm_text, label, signature))
    results.append(check_restore_by_id_capture(mm_text))
    results.append(check_service_loaded_wrap(mm_text))
    results.append(check_typed_origin_member(h_text, mm_text))
    results.append(check_helper_impl(mm_text))
    results.append(check_history_include(history_text))
    results.append(check_history_signature(history_text))
    results.append(check_history_direct_call(history_text))
    results.append(check_history_deferred_bind(history_text))
    results.append(check_history_execution_scope(history_text))
    return results


# --------------------------------------------------------------------------
# Self-test fixtures (synthetic source text, no checkout, no browser)
# --------------------------------------------------------------------------

HEADER_OK = """\
#include "base/functional/callback.h"

namespace views {
enum class ForegroundOrigin;
}  // namespace views

namespace app_controller_mac {

void RunWithForegroundOrigin(views::ForegroundOrigin origin,
                             base::OnceClosure callback);

class TabRestorer {
 private:
  TabRestorer(Profile* profile, SessionID session_id,
              views::ForegroundOrigin foreground_origin);
  views::ForegroundOrigin foreground_origin_;
};

}  // namespace app_controller_mac
"""

MENU_MM = """\
- (void)executeCommand:(id)sender withProfile:(Profile*)profile {
  views::ScopedForegroundOrigin browser_cli_user_ui(
      views::ForegroundOrigin::kUserExplicit);
}

- (IBAction)showPreferencesForProfile:(Profile*)profile {
  views::ScopedForegroundOrigin browser_cli_user_ui(
      views::ForegroundOrigin::kUserExplicit);
}

- (IBAction)orderFrontStandardAboutPanelForProfile:(Profile*)profile {
  views::ScopedForegroundOrigin browser_cli_user_ui(
      views::ForegroundOrigin::kUserExplicit);
}
"""

RESTORE_MM = """\
void TabRestorer::RestoreByID(Profile* profile, SessionID session_id) {
  auto* service = TabRestoreServiceFactory::GetForProfile(profile);
  if (service->IsLoaded()) {
    DoRestoreTab(profile, session_id);
  } else {
    std::ignore = new TabRestorer(profile, session_id,
                                  views::CurrentForegroundOrigin());
    service->LoadTabsFromLastSession();
  }
}

void TabRestorer::TabRestoreServiceLoaded(
    sessions::TabRestoreService* service) {
  observation_.Reset();
  RunWithForegroundOrigin(
      foreground_origin_,
      base::BindOnce(&TabRestorer::DoRestoreTab, profile_, session_id_));
  delete this;
}

void RunWithForegroundOrigin(views::ForegroundOrigin origin,
                             base::OnceClosure callback) {
  DCHECK(callback);
  views::ScopedForegroundOrigin origin_scope(origin);
  std::move(callback).Run();
}
"""


HISTORY_MM = """\
#include "ui/views/widget/browser_cli_foreground_origin.h"

namespace {

void OpenURLForItem(views::ForegroundOrigin foreground_origin,
                    HistoryMenuBridge::HistoryItem node,
                    WindowOpenDisposition disposition,
                    Profile* profile) {
  if (!profile) {
    return;  // Failed to load profile, ignore.
  }
  views::ScopedForegroundOrigin origin_scope(foreground_origin);
  if (node.session_id.is_valid()) {
    app_controller_mac::TabRestorer::RestoreByID(profile, node.session_id);
  } else {
    NavigateParams params(profile, node.url, ui::PAGE_TRANSITION_AUTO_BOOKMARK);
    params.disposition = disposition;
    Navigate(&params);
  }
}

}  // namespace

- (void)openURLForItem:(const HistoryMenuBridge::HistoryItem*)node {
  WindowOpenDisposition disposition =
      ui::WindowOpenDispositionFromNSEvent([NSApp currentEvent]);
  if (Profile* profile = _bridge->profile()) {
    OpenURLForItem(views::ForegroundOrigin::kUserExplicit, *node, disposition,
                   profile);
  } else {
    app_controller_mac::RunInProfileSafely(
        _bridge->profile_dir(),
        base::BindOnce(&OpenURLForItem, views::ForegroundOrigin::kUserExplicit,
                       *node, disposition),
        app_controller_mac::kIgnoreOnFailure);
  }
}
"""


def build_fixture(root, menu_scale="kUserExplicit", capture=True, wrap=True,
                  typed=True, history=None):
    os.makedirs(os.path.join(root, "chrome", "browser"), exist_ok=True)
    menu_text = MENU_MM
    if menu_scale != "kUserExplicit":
        menu_text = menu_text.replace("views::ForegroundOrigin::kUserExplicit",
                                      menu_scale)
    restore = RESTORE_MM
    if not capture:
        restore = restore.replace("views::CurrentForegroundOrigin()",
                                  "views::ForegroundOrigin::kUnknown")
    if not wrap:
        restore = restore.replace(
            "  RunWithForegroundOrigin(\n"
            "      foreground_origin_,\n"
            "      base::BindOnce(&TabRestorer::DoRestoreTab, profile_, session_id_));",
            "  DoRestoreTab(profile_, session_id_);")
    if not typed:
        restore = restore.replace(
            "  views::ScopedForegroundOrigin origin_scope(origin);",
            "  views::ScopedForegroundOrigin origin_scope(\n"
            "      static_cast<views::ForegroundOrigin>(0));")
    with open(os.path.join(root, MM_PATH), "w", encoding="utf-8") as handle:
        handle.write(menu_text + "\n" + restore)
    header = HEADER_OK
    if not typed:
        header = header.replace(
            "  views::ForegroundOrigin foreground_origin_;",
            "  int foreground_origin_;")
    with open(os.path.join(root, H_PATH), "w", encoding="utf-8") as handle:
        handle.write(header)
    history_path = os.path.join(root, HISTORY_MM_PATH)
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    with open(history_path, "w", encoding="utf-8") as handle:
        handle.write(HISTORY_MM if history is None else history)


def failing_names(results):
    return sorted(name for ok, name in ((ok, detail.split(":")[0])
                                        for ok, detail in results) if not ok)


def run_self_test():
    import shutil
    import tempfile

    failures = []
    passed = []

    def check(name, condition, detail=""):
        if condition:
            passed.append(name)
            print("SELFTEST PASS %s" % name)
        else:
            failures.append(name)
            print("SELFTEST FAIL %s %s" % (name, detail))

    tmp = tempfile.mkdtemp(prefix="user-menu-origin-wiring.")
    try:
        good = os.path.join(tmp, "good")
        build_fixture(good)
        results = run_checks(good)
        check("complete fixture passes all checks",
              all(ok for ok, _ in results),
              "; ".join(detail for ok, detail in results if not ok))

        def expect_fail(fixture_name, expected_prefix, **kwargs):
            root = os.path.join(tmp, fixture_name)
            build_fixture(root, **kwargs)
            failed = failing_names(run_checks(root))
            check(
                "%s fails with %s" % (fixture_name, expected_prefix),
                expected_prefix in failed,
                "failed=%s" % failed,
            )

        expect_fail("execute-internal", "executeCommand", menu_scale="kInternal")
        expect_fail("preferences-internal", "showPreferences",
                    menu_scale="kInternal")
        expect_fail("about-internal", "orderFrontAbout", menu_scale="kInternal")
        expect_fail("restore-no-capture", "RestoreByID", capture=False)
        expect_fail("restore-no-wrap", "TabRestoreServiceLoaded", wrap=False)
        expect_fail("typed-member", "header", typed=False)

        # A comment mentioning kUserExplicit must not satisfy the check.
        root = os.path.join(tmp, "comment-only")
        build_fixture(root, menu_scale="kInternal")
        mm_path = os.path.join(root, MM_PATH)
        with open(mm_path, encoding="utf-8") as handle:
            text = handle.read()
        text = text.replace("views::ForegroundOrigin::kInternal",
                            "views::ForegroundOrigin::kUserExplicit")
        text = text.replace(
            "  views::ScopedForegroundOrigin browser_cli_user_ui(",
            "  // views::ForegroundOrigin::kUserExplicit\n"
            "  views::ScopedForegroundOrigin browser_cli_user_ui(")
        with open(mm_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        failed = failing_names(run_checks(root))
        check("comment-only kUserExplicit does not pass",
              "executeCommand" in failed, "failed=%s" % failed)

        # Patch 012: the History native menu must carry the user-explicit origin
        # by value into OpenURLForItem and scope it at the execution boundary.
        def expect_history_fail(fixture_name, expected_prefix, history_text):
            root = os.path.join(tmp, fixture_name)
            build_fixture(root, history=history_text)
            failed = failing_names(run_checks(root))
            check(
                "%s fails with %s" % (fixture_name, expected_prefix),
                expected_prefix in failed,
                "failed=%s" % failed,
            )

        expect_history_fail(
            "history-direct-no-origin", "history-direct",
            HISTORY_MM.replace(
                "OpenURLForItem(views::ForegroundOrigin::kUserExplicit, "
                "*node, disposition,",
                "OpenURLForItem(*node, disposition,"))
        expect_history_fail(
            "history-deferred-no-origin", "history-deferred",
            HISTORY_MM.replace(
                "base::BindOnce(&OpenURLForItem, "
                "views::ForegroundOrigin::kUserExplicit,",
                "base::BindOnce(&OpenURLForItem,"))
        expect_history_fail(
            "history-outer-scope-only", "history-scope",
            HISTORY_MM.replace(
                "  views::ScopedForegroundOrigin "
                "origin_scope(foreground_origin);\n", "")
            .replace(
                HISTORY_METHOD_SIGNATURE + " {\n",
                HISTORY_METHOD_SIGNATURE + " {\n"
                "  views::ScopedForegroundOrigin outer_scope("
                "views::ForegroundOrigin::kUserExplicit);\n"))
        expect_history_fail(
            "history-comment-only-scope", "history-scope",
            HISTORY_MM.replace(
                "  views::ScopedForegroundOrigin "
                "origin_scope(foreground_origin);",
                "  // views::ScopedForegroundOrigin "
                "origin_scope(foreground_origin);"))
        expect_history_fail(
            "history-unknown-origin", "history-direct",
            HISTORY_MM.replace("views::ForegroundOrigin::kUserExplicit",
                               "views::ForegroundOrigin::kUnknown"))

        # Branch/position regressions the main owner found as false passes:
        # (a) the direct call is deleted while the deferred branch keeps
        #     kUserExplicit; a whole-method search would still "pass".
        expect_history_fail(
            "history-direct-branch-removed", "history-direct",
            HISTORY_MM.replace(
                "    OpenURLForItem(views::ForegroundOrigin::kUserExplicit, "
                "*node, disposition,\n"
                "                   profile);\n", ""))
        # (b) the BindOnce closure keeps kUserExplicit but is built outside the
        #     RunInProfileSafely(...) callback argument.
        expect_history_fail(
            "history-deferred-outside-call", "history-deferred",
            HISTORY_MM.replace(
                "    app_controller_mac::RunInProfileSafely(\n"
                "        _bridge->profile_dir(),\n"
                "        base::BindOnce(&OpenURLForItem, "
                "views::ForegroundOrigin::kUserExplicit,\n"
                "                       *node, disposition),\n"
                "        app_controller_mac::kIgnoreOnFailure);",
                "    auto callback = base::BindOnce(&OpenURLForItem,\n"
                "                                   "
                "views::ForegroundOrigin::kUserExplicit,\n"
                "                                   *node, disposition);\n"
                "    app_controller_mac::RunInProfileSafely(\n"
                "        _bridge->profile_dir(), std::move(callback),\n"
                "        app_controller_mac::kIgnoreOnFailure);"))
        # (c) the scope exists but is not constructed from the passed origin.
        expect_history_fail(
            "history-scope-unknown-origin", "history-scope",
            HISTORY_MM.replace(
                "views::ScopedForegroundOrigin origin_scope(foreground_origin);",
                "views::ScopedForegroundOrigin origin_scope(\n"
                "      views::ForegroundOrigin::kUnknown);"))
        # (d) the scope is moved after both the restore and the navigation.
        expect_history_fail(
            "history-scope-after-calls", "history-scope",
            HISTORY_MM.replace(
                "  views::ScopedForegroundOrigin "
                "origin_scope(foreground_origin);\n", "")
            .replace(
                "    Navigate(&params);\n  }\n}",
                "    Navigate(&params);\n  }\n"
                "  views::ScopedForegroundOrigin origin_scope(foreground_origin);\n"
                "}"))
        # (e) the scope is moved into the session_id if block only: its text
        #     still precedes the Navigate branch, but the RAII lifetime does not
        #     cover it. This is the second-review counterexample.
        expect_history_fail(
            "history-scope-only-session-branch", "history-scope",
            HISTORY_MM.replace(
                "  views::ScopedForegroundOrigin "
                "origin_scope(foreground_origin);\n", "")
            .replace(
                "  if (node.session_id.is_valid()) {\n",
                "  if (node.session_id.is_valid()) {\n"
                "    views::ScopedForegroundOrigin "
                "origin_scope(foreground_origin);\n"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("SELFTEST SUMMARY: %d passed, %d failed" % (len(passed), len(failures)))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        description="Static wiring checker for patch 010 (no browser, no build)."
    )
    parser.add_argument("--checkout", default=DEFAULT_CHECKOUT,
                        help="Chromium checkout to inspect")
    parser.add_argument("--self-test", action="store_true",
                        help="run synthetic fixture negatives/positives")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    if not os.path.isdir(args.checkout):
        print("user-menu-origin-wiring-check: no such checkout: %s"
              % args.checkout, file=sys.stderr)
        return 2

    results = run_checks(args.checkout)
    failed = 0
    for ok, detail in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", detail))
        if not ok:
            failed += 1
    print("USER-MENU-ORIGIN-WIRING: %s (%d checks, %d failed)"
          % ("OK" if failed == 0 else "FAIL", len(results), failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
