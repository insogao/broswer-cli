#!/usr/bin/env python3
"""Static, browser-free wiring checker for patch 011 (Omnibox Everywhere).

Patch 011 removes the raw AppKit activation path
(omnibox_everywhere::ActivateBrowserWindowOnMac) from Browser CLI managed mode
and routes only provable browser-process user input through the single
USER_EXPLICIT presentation gateway. A unit test cannot see the producer wiring,
so this checker reads the source files and verifies:

 1. mac_window_util.{h,mm}: the pure route decision exists and the native
    activation calls are only reachable through the stock (kDirectAppKit) route;
    managed user input goes through NativeWidgetMacNSWindowHost::
    PresentForUserExplicitAction() and managed unattributed input is a no-op.
 2. omnibox_everywhere_service.cc: OpenUrl forwards the explicit provenance to
    ActivateBrowserWindowOnMac and never calls AppKit itself.
 3. omnibox_everywhere_ui_manager.cc: the WebContents-delegate callback
    (OpenURLFromTab) passes user_gesture=false; the native context-menu commands
    in ExecuteCommand pass user_gesture=true.
 4. mac_window_util_unittest.cc: the pure route matrix is covered.

No browser, build, process or profile is touched.

Usage:
  python3 tests/tools/omnibox-everywhere-origin-check.py [--checkout DIR]
  python3 tests/tools/omnibox-everywhere-origin-check.py --self-test

Exit codes: 0 = all checks PASS; 1 = at least one FAIL; 2 = usage error.
"""

import argparse
import os
import sys

DEFAULT_CHECKOUT = os.path.expanduser("~/dev/chromium/src")

H_PATH = os.path.join(
    "chrome", "browser", "ui", "omnibox", "omnibox_everywhere", "mac_window_util.h")
MM_PATH = os.path.join(
    "chrome", "browser", "ui", "omnibox", "omnibox_everywhere", "mac_window_util.mm")
TEST_PATH = os.path.join(
    "chrome", "browser", "ui", "omnibox", "omnibox_everywhere",
    "mac_window_util_unittest.cc")
SERVICE_PATH = os.path.join(
    "chrome", "browser", "ui", "omnibox", "omnibox_everywhere_service.cc")
MANAGER_PATH = os.path.join(
    "chrome", "browser", "ui", "omnibox", "omnibox_everywhere",
    "omnibox_everywhere_ui_manager.cc")

RAW_APPKIT_CALLS = (
    "activateIgnoringOtherApps",
    "deminiaturize:",
    "makeKeyAndOrderFront:",
)


def slice_cxx_function(text, signature):
    """Returns the function body starting at a line beginning with |signature|
    and ending at the next line that is exactly `}`."""
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


def strip_line_comments(text):
    """Drops // comments so a comment cannot satisfy a wiring check."""
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def check_route_function(mm_text):
    body = strip_line_comments(slice_cxx_function(
        mm_text, "WindowActivationRoute DecideWindowActivationRoute("))
    if not body:
        return (False, "route: DecideWindowActivationRoute not found")
    if "!managed_mode" not in body or "kDirectAppKit" not in body:
        return (False, "route: missing stock (kDirectAppKit) branch")
    if "kExplicitGateway" not in body:
        return (False, "route: missing kExplicitGateway branch")
    if "kNoop" not in body:
        return (False, "route: missing kNoop fail-closed branch")
    return (True, "route: managed fail-closed / gateway / stock decision present")


def check_activation_guard(mm_text):
    body = strip_line_comments(slice_cxx_function(
        mm_text, "void ActivateBrowserWindowOnMac("))
    if not body:
        return (False, "activation: ActivateBrowserWindowOnMac not found")
    if "DecideWindowActivationRoute(" not in body:
        return (False, "activation: does not consult the route decision")
    if "PresentForUserExplicitAction()" not in body:
        return (False, "activation: gateway branch missing")
    noop = body.find("kNoop")
    gateway = body.find("kExplicitGateway")
    if noop == -1 or gateway == -1:
        return (False, "activation: managed route branches missing")
    managed_guard_end = max(noop, gateway)
    for call in RAW_APPKIT_CALLS:
        index = body.find(call)
        if index == -1:
            return (False, "activation: stock call %s missing" % call)
        if index < managed_guard_end:
            return (False, "activation: raw %s before the managed guards" % call)
    return (True, "activation: managed routes first; raw AppKit only as fallback")


def check_service_forwarding(service_text):
    stripped = strip_line_comments(service_text)
    if "ActivateBrowserWindowOnMac(bwi, user_gesture)" not in stripped:
        return (False, "service: provenance not forwarded to the macOS helper")
    for call in RAW_APPKIT_CALLS:
        if call in stripped:
            return (False, "service: raw AppKit call %s present" % call)
    return (True, "service: forwards user_gesture, no raw AppKit in the service")


def check_manager_producers(manager_text):
    open_url_from_tab = strip_line_comments(slice_cxx_function(
        manager_text, "content::WebContents* OmniboxEverywhereUIManager::OpenURLFromTab("))
    if not open_url_from_tab:
        return (False, "manager: OpenURLFromTab not found")
    if "/*user_gesture=*/false" not in open_url_from_tab:
        return (False, "manager: OpenURLFromTab does not pass user_gesture=false")
    if "/*user_gesture=*/true" in open_url_from_tab:
        return (False, "manager: OpenURLFromTab must never pass user_gesture=true")

    execute = strip_line_comments(slice_cxx_function(
        manager_text, "void OmniboxEverywhereUIManager::ExecuteCommand("))
    if not execute:
        return (False, "manager: ExecuteCommand not found")
    true_count = execute.count("/*user_gesture=*/true")
    false_count = execute.count("/*user_gesture=*/false")
    if false_count:
        return (False, "manager: ExecuteCommand passes user_gesture=false")
    if true_count < 4:
        return (False,
                "manager: ExecuteCommand has only %d explicit user sources" % true_count)
    return (True,
            "manager: OpenURLFromTab fails closed; %d context-menu sources explicit"
            % true_count)


def check_unit_test(test_text):
    if not test_text:
        return (False, "test: mac_window_util_unittest.cc missing")
    for token in ("kNoop", "kExplicitGateway", "kDirectAppKit"):
        if token not in test_text:
            return (False, "test: route %s not covered" % token)
    return (True, "test: pure route matrix covered")


def run_checks(checkout):
    paths = {
        "h": os.path.join(checkout, H_PATH),
        "mm": os.path.join(checkout, MM_PATH),
        "test": os.path.join(checkout, TEST_PATH),
        "service": os.path.join(checkout, SERVICE_PATH),
        "manager": os.path.join(checkout, MANAGER_PATH),
    }
    texts = {}
    for key, path in paths.items():
        try:
            with open(path, encoding="utf-8") as handle:
                texts[key] = handle.read()
        except OSError as error:
            return [(False, "cannot read %s: %s" % (path, error))]

    results = []
    if "enum class WindowActivationRoute" not in texts["h"]:
        results.append((False, "header: WindowActivationRoute missing"))
    elif "ActivateBrowserWindowOnMac(BrowserWindowInterface* bwi, bool user_gesture)" \
            not in texts["h"]:
        results.append((False, "header: ActivateBrowserWindowOnMac missing provenance"))
    else:
        results.append((True, "header: route enum and provenance parameter present"))

    results.append(check_route_function(texts["mm"]))
    results.append(check_activation_guard(texts["mm"]))
    results.append(check_service_forwarding(texts["service"]))
    results.append(check_manager_producers(texts["manager"]))
    results.append(check_unit_test(texts["test"]))
    return results


# --------------------------------------------------------------------------
# Self-test fixtures (synthetic source text, no checkout, no browser)
# --------------------------------------------------------------------------

HEADER_OK = """\
enum class WindowActivationRoute { kNoop, kExplicitGateway, kDirectAppKit };
WindowActivationRoute DecideWindowActivationRoute(bool managed_mode,
                                                  bool user_gesture);
void ActivateBrowserWindowOnMac(BrowserWindowInterface* bwi, bool user_gesture);
"""

MM_OK = """\
WindowActivationRoute DecideWindowActivationRoute(bool managed_mode,
                                                  bool user_gesture) {
  if (!managed_mode) {
    return WindowActivationRoute::kDirectAppKit;
  }
  return user_gesture ? WindowActivationRoute::kExplicitGateway
                      : WindowActivationRoute::kNoop;
}

void ActivateBrowserWindowOnMac(BrowserWindowInterface* bwi,
                                bool user_gesture) {
  const WindowActivationRoute route =
      DecideWindowActivationRoute(managed, user_gesture);
  if (route == WindowActivationRoute::kNoop) {
    return;
  }
  if (route == WindowActivationRoute::kExplicitGateway) {
    host->PresentForUserExplicitAction();
    return;
  }
  // stock
  [NSApp activateIgnoringOtherApps:YES];
  [window deminiaturize:nil];
  [window makeKeyAndOrderFront:nil];
}
"""

TEST_OK = """\
TEST(X, A) { EXPECT_EQ(r, WindowActivationRoute::kNoop); }
TEST(X, B) { EXPECT_EQ(r, WindowActivationRoute::kExplicitGateway); }
TEST(X, C) { EXPECT_EQ(r, WindowActivationRoute::kDirectAppKit); }
"""

SERVICE_OK = """\
void OmniboxEverywhereService::OpenUrl(const GURL& url,
                                       WindowDisposition disposition,
                                       PageTransition transition) {
  OpenUrl(url, disposition, transition, /*user_gesture=*/false, base::NullCallback());
}

void OmniboxEverywhereService::OpenUrl(const GURL& url,
                                       WindowDisposition disposition,
                                       PageTransition transition,
                                       bool user_gesture,
                                       Callback callback) {
  omnibox_everywhere::ActivateBrowserWindowOnMac(bwi, user_gesture);
}
"""

MANAGER_OK = """\
content::WebContents* OmniboxEverywhereUIManager::OpenURLFromTab(
    content::WebContents* source,
    const content::OpenURLParams& params,
    base::OnceCallback<void(content::NavigationHandle&)> callback) {
  service->OpenUrl(params.url, params.disposition, params.transition,
                   /*user_gesture=*/false, std::move(callback));
  return nullptr;
}

void OmniboxEverywhereUIManager::ExecuteCommand(int command_id, int event_flags) {
  service->OpenUrl(a, b, c, /*user_gesture=*/true);
  service->OpenUrl(a, b, c, /*user_gesture=*/true);
  service->OpenUrl(a, b, c, /*user_gesture=*/true);
  service->OpenUrl(a, b, c, /*user_gesture=*/true);
}
"""


def build_fixture(root, guarded=True, gateway=True, service_ok=True,
                  from_tab_false=True, execute_true=True, test_ok=True):
    os.makedirs(os.path.join(root, H_PATH.rsplit(os.sep, 1)[0]), exist_ok=True)
    mm = MM_OK
    if not guarded:
        mm = mm.replace(
            "  const WindowActivationRoute route =\n"
            "      DecideWindowActivationRoute(managed, user_gesture);\n",
            "")
    if not gateway:
        mm = mm.replace("    host->PresentForUserExplicitAction();\n", "")
    service = SERVICE_OK
    if not service_ok:
        service = service.replace("ActivateBrowserWindowOnMac(bwi, user_gesture)",
                                  "ActivateBrowserWindowOnMac(bwi)")
    manager = MANAGER_OK
    if not from_tab_false:
        manager = manager.replace("/*user_gesture=*/false", "/*user_gesture=*/true")
    if not execute_true:
        manager = manager.replace("/*user_gesture=*/true", "/*user_gesture=*/false")
    files = {
        H_PATH: HEADER_OK,
        MM_PATH: mm,
        SERVICE_PATH: service,
        MANAGER_PATH: manager,
        TEST_PATH: TEST_OK if test_ok else "",
    }
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)


def failing_labels(results):
    return sorted(detail.split(":")[0] for ok, detail in results if not ok)


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

    tmp = tempfile.mkdtemp(prefix="omnibox-everywhere-origin.")
    try:
        good = os.path.join(tmp, "good")
        build_fixture(good)
        results = run_checks(good)
        check("complete fixture passes all checks",
              all(ok for ok, _ in results),
              "; ".join(detail for ok, detail in results if not ok))

        def expect_fail(name, expected, **kwargs):
            root = os.path.join(tmp, name)
            build_fixture(root, **kwargs)
            failed = failing_labels(run_checks(root))
            check("%s fails in %s" % (name, expected), expected in failed,
                  "failed=%s" % failed)

        expect_fail("unguarded-appkit", "activation", guarded=False)
        expect_fail("no-gateway", "activation", gateway=False)
        expect_fail("no-provenance", "service", service_ok=False)
        expect_fail("from-tab-true", "manager", from_tab_false=False)
        expect_fail("execute-false", "manager", execute_true=False)
        expect_fail("no-test", "test", test_ok=False)

        # A comment mentioning the gateway must not satisfy the check.
        root = os.path.join(tmp, "comment-only")
        build_fixture(root, guarded=False)
        mm_path = os.path.join(root, MM_PATH)
        with open(mm_path, encoding="utf-8") as handle:
            text = handle.read()
        text = text.replace("  // stock\n",
                            "  // DecideWindowActivationRoute(managed, user_gesture)\n"
                            "  // stock\n")
        with open(mm_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        failed = failing_labels(run_checks(root))
        check("comment-only route does not pass", "activation" in failed,
              "failed=%s" % failed)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("SELFTEST SUMMARY: %d passed, %d failed" % (len(passed), len(failures)))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        description="Static wiring checker for patch 011 (no browser, no build)."
    )
    parser.add_argument("--checkout", default=DEFAULT_CHECKOUT,
                        help="Chromium checkout to inspect")
    parser.add_argument("--self-test", action="store_true",
                        help="run synthetic fixture negatives/positives")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    if not os.path.isdir(args.checkout):
        print("omnibox-everywhere-origin-check: no such checkout: %s"
              % args.checkout, file=sys.stderr)
        return 2

    results = run_checks(args.checkout)
    failed = 0
    for ok, detail in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", detail))
        if not ok:
            failed += 1
    print("OMNIBOX-EVERYWHERE-ORIGIN: %s (%d checks, %d failed)"
          % ("OK" if failed == 0 else "FAIL", len(results), failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
