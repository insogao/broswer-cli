#!/usr/bin/env python3
"""CDP scenario: Extension window focus requests under BACKGROUND.

Scenario tool on top of tests/tools/cdp_pipe_lib.py (shared, fd-fixed CDP pipe
launcher). It launches the signed app with **only** this test's unpacked
extension loaded (shared launcher with the blanket extension disable turned
off, plus `--disable-extensions-except=<dir>` + `--load-extension=<dir>`),
opens the extension's own test page, and evaluates:

    __bcliRunWindowFocusTest(<local page url>)

with `awaitPromise`, which uses the real Extension API:
  * chrome.windows.create({url, focused: true})   -> createResult
  * chrome.windows.update(id, {focused: true})    -> updateResult

Recorded evidence:
  * extension id (+ how it was resolved) and its service-worker target;
  * API-level create/update outcomes (resolution or rejection) verbatim,
    including returned window/tab identifiers;
  * the created target/WebContents found independently over CDP
    (type/url + Browser.getWindowForTarget), so "creation succeeded" is proven
    outside the extension's own report;
  * informational comparison of the extension-reported window id with the CDP
    window id;
  * on-screen window samples for the browser PID (must stay 0);
  * policy log tail (suppress/deny asserted by the caller).

Whether native focus/activation was granted is decided by the kernel policy logs
and CGWindowList, NOT by API return values or DOM focus state (informational).

Never uses `open`/Dock/Launchpad; mock-keychain + isolated profile come from
the shared launcher.

Usage:
  extension-window-focus-cdp-check.py --app PATH --helper PATH --server-url URL
      --extension-dir DIR [--profile DIR] [--log PATH] [--result-json PATH]
      [--timeout SEC]
Exit: 0 = harness finished (results in JSON), 1 = fatal launch/CDP failure.
"""

import argparse
import hashlib
import json
import os
import sys
import time

from cdp_pipe_lib import (
    WindowSampler,
    attach,
    default_profile,
    evaluate,
    find_target,
    launch_app,
    stop_app,
    wait_for_page,
)

# MV3 service workers are not guaranteed to stay resident, so never wait long
# for one. Deterministic unpacked-id verification below is the primary path;
# this is only a bounded fallback discovery window.
SW_DISCOVERY_SECONDS = 10

# The created page target should exist as soon as windows.create resolves; keep
# the independent CDP cross-check bounded instead of using the global timeout.
CREATED_TARGET_WAIT_SECONDS = 30


def unpacked_extension_id(path):
    """Chrome's unpacked-extension id: sha256(path)[:32] mapped to a..p."""
    digest = hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:32]
    return "".join(chr(ord("a") + int(ch, 16)) for ch in digest)


def discover_extension_target(cdp, seconds):
    """Poll Target.getTargets for a chrome-extension:// target, preferring a
    service worker; returns (target_info_or_None, elapsed_seconds)."""
    deadline = time.time() + seconds
    fallback = None
    while True:
        infos = cdp.request("Target.getTargets", timeout=seconds).get("targetInfos", [])
        for info in infos:
            url = info.get("url", "")
            if not url.startswith("chrome-extension://"):
                continue
            if info.get("type") == "service_worker":
                return info, time.time() - (deadline - seconds)
            if fallback is None:
                fallback = info
        if time.time() >= deadline:
            return fallback, seconds
        time.sleep(0.2)


def try_open_test_page(cdp, extension_id, timeout):
    """Verify an extension id by loading its test page; returns a dict with a
    live session on success."""
    try:
        page = cdp.request(
            "Target.createTarget",
            {
                "url": "chrome-extension://%s/test.html" % extension_id,
                "newWindow": True,
                "background": False,
            },
            timeout=timeout,
        )
        target_id = page["targetId"]
        session_id = attach(cdp, target_id, timeout)
        wait_for_page(cdp, session_id, timeout)
        return {
            "extension_id": extension_id,
            "ok": True,
            "targetId": target_id,
            "sessionId": session_id,
        }
    except Exception as exc:  # noqa: BLE001 - recorded as a verification attempt
        return {
            "extension_id": extension_id,
            "ok": False,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }


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
    parser.add_argument("--extension-dir", required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("--result-json", default="")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    extension_dir = os.path.abspath(args.extension_dir)
    profile = args.profile or default_profile()
    log_path = args.log or os.path.join(profile, "browser-cli-cdp.log")
    result_path = args.result_json or os.path.join(profile, "cdp-results.json")

    results = {
        "app": args.app,
        "profile": profile,
        "server_url": args.server_url,
        "extension_dir": extension_dir,
        "extension": {},
        "ext_page": {},
        "api": {},
        "created_target": {},
        "created_window": {},
        "window_id_cross_check": {},
        "targets": [],
        "window_samples": {"max": -1, "samples": []},
        "cmdline": "",
        "exit_code": None,
        "errors": [],
    }

    proc = None
    cdp = None
    sampler = None
    ext_session = None
    try:
        if not os.path.isfile(os.path.join(extension_dir, "manifest.json")):
            raise RuntimeError("missing manifest.json in %s" % extension_dir)

        proc, cdp, cmdline = launch_app(
            args.app,
            profile,
            log_path,
            extra_args=[
                "--disable-extensions-except=" + extension_dir,
                "--load-extension=" + extension_dir,
            ],
            # The shared launcher must NOT also pass the blanket extension
            # disable flag here: the except/load pair already restricts loading
            # to this fixture and the three flags must never be combined.
            disable_extensions=False,
        )
        results["cmdline"] = cmdline
        results["pid"] = proc.pid
        sampler = WindowSampler(args.helper, proc.pid)
        sampler.start()

        # 1) Resolve the extension id without long waits: the deterministic
        #    unpacked id (sha256(path) mapped to a..p) is tried first and
        #    *verified* by actually loading its test page. Only if that fails do
        #    we spend a bounded (<= SW_DISCOVERY_SECONDS) service-worker
        #    discovery window and try the observed id as a fallback. MV3 service
        #    workers are not guaranteed to stay resident, so no long wait.
        deterministic_id = unpacked_extension_id(extension_dir)
        attempts = []
        chosen = None
        id_source = ""
        sw_target = {}
        discovery_seconds = 0.0
        ext_session = None
        ext_page_id = ""

        first = try_open_test_page(cdp, deterministic_id, args.timeout)
        attempts.append({k: v for k, v in first.items() if k != "sessionId"})
        if first["ok"]:
            chosen = deterministic_id
            id_source = "deterministic"
            ext_session = first["sessionId"]
            ext_page_id = first["targetId"]
        else:
            sw_info, discovery_seconds = discover_extension_target(
                cdp, SW_DISCOVERY_SECONDS
            )
            if sw_info:
                sw_target = sw_info
                sw_id = (
                    sw_info.get("url", "")
                    .split("chrome-extension://")[1]
                    .split("/")[0]
                )
                if sw_id != deterministic_id:
                    second = try_open_test_page(cdp, sw_id, args.timeout)
                    attempts.append({k: v for k, v in second.items() if k != "sessionId"})
                    if second["ok"]:
                        chosen = sw_id
                        id_source = "service_worker"
                        ext_session = second["sessionId"]
                        ext_page_id = second["targetId"]
        if chosen is None:
            raise RuntimeError("could not verify extension id; attempts=%s" % attempts)

        results["extension"] = {
            "dir": extension_dir,
            "id": chosen,
            "id_source": id_source,
            "id_verified": True,
            "deterministic_id": deterministic_id,
            "sw_target": sw_target,
            "sw_discovery_seconds": discovery_seconds,
            "attempts": attempts,
        }

        # 2) The verified test page (session kept open for the API calls).
        results["ext_page"]["targetId"] = ext_page_id
        results["ext_page"]["window"] = cdp.request(
            "Browser.getWindowForTarget", {"targetId": ext_page_id}, timeout=args.timeout
        )
        results["ext_page"]["inspect"] = snapshot(cdp, ext_session, args.timeout)

        # 3) Real Extension API calls with focused:true.
        child_url = args.server_url.rstrip("/") + "/page?name=ext_child"
        expression = "globalThis.__bcliRunWindowFocusTest(%s)" % json.dumps(child_url)
        results["api"] = evaluate(
            cdp,
            ext_session,
            expression,
            args.timeout,
            await_promise=True,
        )

        # 4) Independently prove the created window/tab exists over CDP.
        created = find_target(cdp, "/page?name=ext_child", CREATED_TARGET_WAIT_SECONDS)
        if created is not None:
            created_id = created.get("targetId", "")
            results["created_target"] = created
            if created_id:
                results["created_window"] = cdp.request(
                    "Browser.getWindowForTarget",
                    {"targetId": created_id},
                    timeout=args.timeout,
                )

        # 5) Informational cross-check of the extension-reported window id.
        ext_window_id = None
        create_result = (results.get("api") or {}).get("createResult") or {}
        if isinstance(create_result, dict):
            ext_window_id = create_result.get("windowId")
        cdp_window_id = (results.get("created_window") or {}).get("windowId")
        results["window_id_cross_check"] = {
            "extension_window_id": ext_window_id,
            "cdp_window_id": cdp_window_id,
            "match": ext_window_id is not None
            and cdp_window_id is not None
            and ext_window_id == cdp_window_id,
        }

        # 6) Target list snapshot (types/urls).
        targets = cdp.request("Target.getTargets", timeout=args.timeout)
        results["targets"] = targets.get("targetInfos", [])

    except Exception as exc:  # noqa: BLE001 - harness must record, not crash
        results["errors"].append("%s: %s" % (type(exc).__name__, exc))
    finally:
        if ext_session is not None and cdp is not None:
            try:
                cdp.request(
                    "Target.detachFromTarget", {"sessionId": ext_session}, timeout=5
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
