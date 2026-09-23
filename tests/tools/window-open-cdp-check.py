#!/usr/bin/env python3
"""CDP scenario: web page window.open() under BACKGROUND.

Scenario tool on top of tests/tools/cdp_pipe_lib.py (shared, fd-fixed CDP pipe
launcher). It opens a real opener page, triggers the page's own JS
`window.open()` (with a user gesture so Chrome's popup blocker is not the
subject under test), and records:
  * the returned child WindowProxy is live (not blocked/closed);
  * a NEW target/WebContents exists with the child URL and correct DOM content;
  * parent/child opener relationship from both sides:
      - parent: window.__bcliLastChild.location.href == child URL
      - child:  window.opener.location.href == parent URL
      - structural: TargetInfo.openerId == parent target id
  * the child's window id via Browser.getWindowForTarget (informational);
  * document.hasFocus()/visibilityState as INFORMATIONAL WebContents state only
    (background pages legitimately stay internally active);
  * on-screen window samples for the browser PID (must stay 0).

Never uses `open`/Dock/Launchpad; mock-keychain + isolated profile come from
the shared launcher.

Usage:
  window-open-cdp-check.py --app PATH --helper PATH --server-url URL
      [--profile DIR] [--log PATH] [--result-json PATH] [--timeout SEC]
Exit: 0 = harness finished (results in JSON), 1 = fatal launch/CDP failure.
"""

import argparse
import json
import os
import sys
import time

from cdp_pipe_lib import (
    WindowSampler,
    attach,
    default_profile,
    evaluate,
    launch_app,
    stop_app,
    wait_for_page,
)


def find_target(cdp, needle, timeout):
    deadline = time.time() + timeout
    while True:
        infos = cdp.request("Target.getTargets", timeout=timeout).get("targetInfos", [])
        for info in infos:
            if needle in info.get("url", ""):
                return info
        if time.time() >= deadline:
            return None
        time.sleep(0.2)


def snapshot(cdp, session_id, timeout):
    return {
        "title": evaluate(cdp, session_id, "document.title", timeout),
        "href": evaluate(cdp, session_id, "location.href", timeout),
        "body_marker": evaluate(
            cdp, session_id, "document.getElementById('marker').textContent", timeout
        ),
        "has_focus": evaluate(cdp, session_id, "document.hasFocus()", timeout),
        "visibility_state": evaluate(
            cdp, session_id, "document.visibilityState", timeout
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--helper", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("--result-json", default="")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    profile = args.profile or default_profile()
    log_path = args.log or os.path.join(profile, "browser-cli-cdp.log")
    result_path = args.result_json or os.path.join(profile, "cdp-results.json")

    results = {
        "app": args.app,
        "profile": profile,
        "server_url": args.server_url,
        "errors": [],
        "targets": [],
        "parent": {},
        "child": {},
        "window_samples": {"max": -1, "samples": []},
        "cmdline": "",
        "exit_code": None,
    }

    proc = None
    cdp = None
    sampler = None
    parent_session = None
    child_session = None
    try:
        proc, cdp, cmdline = launch_app(args.app, profile, log_path)
        results["cmdline"] = cmdline
        results["pid"] = proc.pid
        sampler = WindowSampler(args.helper, proc.pid)
        sampler.start()

        base = args.server_url.rstrip("/")
        opener_url = base + "/opener?name=parent&child=child"
        child_url = base + "/page?name=child"

        # 1) A background BrowserWindow hosting the opener page.
        parent = cdp.request(
            "Target.createTarget",
            {"url": opener_url, "newWindow": True, "background": False},
            timeout=args.timeout,
        )
        parent_id = parent["targetId"]
        results["parent"]["targetId"] = parent_id
        results["parent"]["window"] = cdp.request(
            "Browser.getWindowForTarget", {"targetId": parent_id}, timeout=args.timeout
        )

        parent_session = attach(cdp, parent_id, args.timeout)
        wait_for_page(cdp, parent_session, args.timeout)
        results["parent"]["inspect"] = snapshot(cdp, parent_session, args.timeout)

        # 2) Real JS window.open() from the page (user gesture on purpose, so the
        #    popup blocker is not what is under test here).
        results["parent"]["open_return"] = evaluate(
            cdp,
            parent_session,
            "window.__bcliOpenChild()",
            args.timeout,
            user_gesture=True,
        )

        # 3) The child must appear as a NEW target with the expected URL.
        child_info = find_target(cdp, "/page?name=child", args.timeout)
        if child_info is not None:
            child_id = child_info.get("targetId", "")
            results["child"]["targetId"] = child_id
            results["child"]["type"] = child_info.get("type", "")
            results["child"]["url"] = child_info.get("url", "")
            results["child"]["openerId"] = child_info.get("openerId", "")
            results["child"]["window"] = cdp.request(
                "Browser.getWindowForTarget", {"targetId": child_id}, timeout=args.timeout
            )
            child_session = attach(cdp, child_id, args.timeout)
            wait_for_page(cdp, child_session, args.timeout)
            results["child"]["inspect"] = snapshot(cdp, child_session, args.timeout)
            results["child"]["window_name"] = evaluate(
                cdp, child_session, "window.name", args.timeout
            )
            results["child"]["opener_href"] = evaluate(
                cdp,
                child_session,
                "window.opener ? window.opener.location.href : ''",
                args.timeout,
            )

        # 4) Parent-side view of the child WindowProxy.
        results["parent"]["child_href"] = evaluate(
            cdp,
            parent_session,
            "window.__bcliLastChild ? window.__bcliLastChild.location.href : ''",
            args.timeout,
        )
        results["parent"]["child_closed"] = evaluate(
            cdp,
            parent_session,
            "window.__bcliLastChild ? window.__bcliLastChild.closed : true",
            args.timeout,
        )

        # 5) Target list snapshot (types/urls/openerId).
        targets = cdp.request("Target.getTargets", timeout=args.timeout)
        results["targets"] = targets.get("targetInfos", [])

    except Exception as exc:  # noqa: BLE001 - harness must record, not crash
        results["errors"].append("%s: %s" % (type(exc).__name__, exc))
    finally:
        if parent_session is not None and cdp is not None:
            try:
                cdp.request(
                    "Target.detachFromTarget", {"sessionId": parent_session}, timeout=5
                )
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
        if child_session is not None and cdp is not None:
            try:
                cdp.request(
                    "Target.detachFromTarget", {"sessionId": child_session}, timeout=5
                )
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
        if sampler is not None:
            results["window_samples"] = {"max": sampler.stop(), "samples": sampler.samples}
        if cdp is not None:
            cdp.close()
        if proc is not None:
            results["exit_code"] = stop_app(proc)
        try:
            with open(log_path, "rb") as handle:
                results["policy_log_tail"] = handle.read()[-20000:].decode(
                    "utf-8", "replace"
                ).splitlines()
        except OSError:
            results["policy_log_tail"] = []
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, sort_keys=True)

    if results["errors"]:
        print("CDP-HARNESS: FAIL")
        for err in results["errors"]:
            print("  " + err)
        return 1
    print("CDP-HARNESS: done (results: %s)" % result_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
