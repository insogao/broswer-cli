#!/usr/bin/env python3
"""CDP scenario: AI-created Tab vs independent BrowserWindow under BACKGROUND.

Scenario tool on top of tests/tools/cdp_pipe_lib.py (shared, fd-fixed CDP pipe
launcher). It launches the signed app once and records:
  * createTarget(newWindow=true)  -> an independent BrowserWindow
  * createTarget(newWindow=false) -> a Tab inside that window
    (verified with Browser.getWindowForTarget window ids)
  * real page content per target (HTTP + DOM via Runtime.evaluate)
  * document.hasFocus()/visibilityState recorded as INFORMATIONAL WebContents
    state only: a background tab/window legitimately stays internally active
    for continued running. OS-level "not shown / not activated" is evidenced by
    on-screen window samples == 0 plus the native policy deny logs.
  * on-screen window samples for the browser PID (must stay 0)

Never uses `open`/Dock/Launchpad; mock-keychain + isolated profile come from
the shared launcher.

Usage:
  tab-window-cdp-check.py --app PATH --helper PATH --server-url URL
      [--profile DIR] [--log PATH] [--result-json PATH] [--timeout SEC]
Exit: 0 = harness finished (results in JSON), 1 = fatal launch/CDP failure.
"""

import argparse
import json
import os
import sys

from cdp_pipe_lib import (
    WindowSampler,
    default_profile,
    inspect_target,
    launch_app,
    stop_app,
)


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
        "tab": {},
        "window1": {},
        "window2": {},
        "windows": {},
        "window_samples": {"max": -1, "samples": []},
        "cmdline": "",
        "exit_code": None,
    }

    proc = None
    cdp = None
    sampler = None
    try:
        proc, cdp, cmdline = launch_app(args.app, profile, log_path)
        results["cmdline"] = cmdline
        results["pid"] = proc.pid
        sampler = WindowSampler(args.helper, proc.pid)
        sampler.start()

        page = args.server_url.rstrip("/") + "/page?name="

        # 1) AI creates a new independent BrowserWindow.
        window1 = cdp.request(
            "Target.createTarget",
            {"url": page + "window1", "newWindow": True, "background": False},
            timeout=args.timeout,
        )
        window1_id = window1["targetId"]
        results["window1"]["targetId"] = window1_id
        results["window1"]["window"] = cdp.request(
            "Browser.getWindowForTarget", {"targetId": window1_id}, timeout=args.timeout
        )

        # 2) AI creates a normal Tab: it must join the existing window.
        tab = cdp.request(
            "Target.createTarget",
            {"url": page + "tab", "newWindow": False, "background": False},
            timeout=args.timeout,
        )
        tab_id = tab["targetId"]
        results["tab"]["targetId"] = tab_id
        results["tab"]["window"] = cdp.request(
            "Browser.getWindowForTarget", {"targetId": tab_id}, timeout=args.timeout
        )

        # 3) A second independent window for contrast/isolation.
        window2 = cdp.request(
            "Target.createTarget",
            {"url": page + "window2", "newWindow": True, "background": False},
            timeout=args.timeout,
        )
        window2_id = window2["targetId"]
        results["window2"]["targetId"] = window2_id
        results["window2"]["window"] = cdp.request(
            "Browser.getWindowForTarget", {"targetId": window2_id}, timeout=args.timeout
        )

        # 4) Real page content + (informational) focus state.
        results["tab"]["inspect"] = inspect_target(cdp, tab_id, args.timeout)
        results["window1"]["inspect"] = inspect_target(cdp, window1_id, args.timeout)
        results["window2"]["inspect"] = inspect_target(cdp, window2_id, args.timeout)

        # 5) Target list snapshot (types/urls).
        targets = cdp.request("Target.getTargets", timeout=args.timeout)
        results["targets"] = targets.get("targetInfos", [])

    except Exception as exc:  # noqa: BLE001 - harness must record, not crash
        results["errors"].append("%s: %s" % (type(exc).__name__, exc))
    finally:
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
