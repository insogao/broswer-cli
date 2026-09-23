#!/usr/bin/env python3
"""CDP scenario: Session restore of internal structure without native show.

Two phases inside ONE driver invocation, sharing ONE fresh mktemp profile:

Phase 1 (no_startup_window=True):
  * CDP builds a uniquely identifiable structure: BrowserWindow A with tabs
    /page?name=restore_a1 + /page?name=restore_a2, and BrowserWindow B with
    /page?name=restore_b1 (real pages from the local test server);
  * verifies content + window grouping (Browser.getWindowForTarget);
  * waits (bounded) until the profile's Default/Sessions/Session_* file with a
    marker exists, then exits CLEANLY via CDP Browser.close
    (ChromeDevToolsManagerDelegate::CloseBrowserSoon ->
     chrome::ExitIgnoreUnloadHandlers -> MarkAsCleanShutdown).

Phase 2 (no_startup_window=False, same profile):
  * relaunches with `--restore-last-session` (+ `--hide-crash-restore-bubble`).
    `--no-startup-window` MUST be absent here: StartupBrowserCreator::
    ShouldLoadProfileWithoutWindow skips window/restore startup when present.
    The profile is not "new" anymore, which is what makes the restore switch
    effective (see SessionRestoreWithIncompleteFileTest in Chromium);
  * proves the three targets are restored with real readable content and the
    original grouping (a1+a2 in one window, b1 in a different window);
  * authoritative OS/policy evidence: on-screen window samples for the browser
    PID stay 0 in BOTH phases, and the policy logs suppress/deny presentation
    with no internal allow while BACKGROUND.

DOM hasFocus/visibilityState are recorded as informational only. Never uses
`open`/Dock/Launchpad; mock-keychain + isolated profile come from the shared
launcher.

Usage:
  session-restore-cdp-check.py --app PATH --helper PATH --server-url URL
      --profile DIR [--log-p1 PATH] [--log-p2 PATH] [--result-json PATH]
      [--timeout SEC]
Exit: 0 = harness finished (results in JSON), 1 = fatal launch/CDP failure.
"""

import argparse
import glob
import json
import os
import sys
import time

from cdp_pipe_lib import (
    WindowSampler,
    default_profile,
    inspect_target,
    launch_app,
    stop_app,
)

PAGE_NAMES = ["restore_a1", "restore_a2", "restore_b1"]
SESSIONS_POLL_SECONDS = 30
EXIT_WAIT_SECONDS = 60


def wait_for_sessions_file(profile, seconds):
    sessions_dir = os.path.join(profile, "Default", "Sessions")
    deadline = time.time() + seconds
    while True:
        files = []
        for pattern in ("Session_*", "Tabs_*"):
            files.extend(glob.glob(os.path.join(sessions_dir, pattern)))
        with_content = [
            {"path": os.path.basename(path), "size": os.path.getsize(path)}
            for path in files
            if os.path.getsize(path) > 0
        ]
        if any(item["path"].startswith("Session_") for item in with_content):
            return with_content
        if time.time() >= deadline:
            return with_content
        time.sleep(0.5)


def clean_close(cdp, proc, timeout=EXIT_WAIT_SECONDS):
    try:
        cdp.request("Browser.close", timeout=10)
    except Exception:  # noqa: BLE001 - pipe may close as the browser exits
        pass
    deadline = time.time() + timeout
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.5)
    if proc.poll() is None:
        return stop_app(proc)
    return proc.returncode


def create_target(cdp, url, new_window, timeout):
    result = cdp.request(
        "Target.createTarget",
        {"url": url, "newWindow": new_window, "background": False},
        timeout=timeout,
    )
    return result["targetId"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--helper", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--log-p1", default="")
    parser.add_argument("--log-p2", default="")
    parser.add_argument("--result-json", default="")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    profile = args.profile or default_profile()
    log_p1 = args.log_p1 or os.path.join(profile, "browser-phase1.log")
    log_p2 = args.log_p2 or os.path.join(profile, "browser-phase2.log")
    result_path = args.result_json or os.path.join(profile, "cdp-results.json")
    base = args.server_url.rstrip("/")

    results = {
        "app": args.app,
        "profile": profile,
        "server_url": args.server_url,
        "phase1": {},
        "phase2": {},
        "origins_seen": [],
        "errors": [],
    }

    # ------------------------------------------------------------------ phase 1
    proc1 = cdp1 = sampler1 = None
    try:
        proc1, cdp1, cmdline1 = launch_app(args.app, profile, log_p1)
        sampler1 = WindowSampler(args.helper, proc1.pid)
        sampler1.start()
        results["phase1"]["cmdline"] = cmdline1
        results["phase1"]["pid"] = proc1.pid

        a1_id = create_target(cdp1, base + "/page?name=restore_a1", True, args.timeout)
        a2_id = create_target(cdp1, base + "/page?name=restore_a2", False, args.timeout)
        b1_id = create_target(cdp1, base + "/page?name=restore_b1", True, args.timeout)

        structure = {}
        for name, target_id in (
            ("restore_a1", a1_id),
            ("restore_a2", a2_id),
            ("restore_b1", b1_id),
        ):
            structure[name] = {
                "targetId": target_id,
                "inspect": inspect_target(cdp1, target_id, args.timeout),
                "window": cdp1.request(
                    "Browser.getWindowForTarget", {"targetId": target_id},
                    timeout=args.timeout,
                ),
            }
        results["phase1"]["structure"] = structure
        results["phase1"]["sessions_files"] = wait_for_sessions_file(
            profile, SESSIONS_POLL_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - harness must record, not crash
        results["errors"].append("phase1 %s: %s" % (type(exc).__name__, exc))
    finally:
        if sampler1 is not None:
            results["phase1"]["window_samples_max"] = sampler1.stop()
        if cdp1 is not None and proc1 is not None:
            results["phase1"]["exit_code"] = clean_close(cdp1, proc1)
        elif proc1 is not None:
            results["phase1"]["exit_code"] = stop_app(proc1)
        if cdp1 is not None:
            cdp1.close()

    # ------------------------------------------------------------------ phase 2
    proc2 = cdp2 = sampler2 = None
    try:
        proc2, cdp2, cmdline2 = launch_app(
            args.app,
            profile,
            log_p2,
            extra_args=["--restore-last-session", "--hide-crash-restore-bubble"],
            no_startup_window=False,
        )
        sampler2 = WindowSampler(args.helper, proc2.pid)
        sampler2.start()
        results["phase2"]["cmdline"] = cmdline2
        results["phase2"]["pid"] = proc2.pid

        restored = {}
        all_targets = []
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            all_targets = cdp2.request("Target.getTargets", timeout=args.timeout).get(
                "targetInfos", []
            )
            for name in PAGE_NAMES:
                if name in restored:
                    continue
                needle = "/page?name=" + name
                for info in all_targets:
                    if needle in info.get("url", ""):
                        restored[name] = {"target": info}
                        break
            if len(restored) == len(PAGE_NAMES):
                break
            time.sleep(0.5)

        for name, entry in restored.items():
            target_id = entry["target"].get("targetId", "")
            entry["inspect"] = inspect_target(cdp2, target_id, args.timeout)
            entry["window"] = cdp2.request(
                "Browser.getWindowForTarget", {"targetId": target_id},
                timeout=args.timeout,
            )
        results["phase2"]["restored"] = restored
        results["phase2"]["all_targets"] = all_targets
    except Exception as exc:  # noqa: BLE001 - harness must record, not crash
        results["errors"].append("phase2 %s: %s" % (type(exc).__name__, exc))
    finally:
        if sampler2 is not None:
            results["phase2"]["window_samples_max"] = sampler2.stop()
        if cdp2 is not None and proc2 is not None:
            results["phase2"]["exit_code"] = clean_close(cdp2, proc2)
        elif proc2 is not None:
            results["phase2"]["exit_code"] = stop_app(proc2)
        if cdp2 is not None:
            cdp2.close()

    # origins actually emitted by the current policy producer (informational:
    # the ForegroundOrigin::kSessionRestore enum exists, but attribution may
    # still be pending, so the gates use suppress/deny/no-allow instead).
    origins = set()
    for log_path in (log_p1, log_p2):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    marker = "origin="
                    if "BROWSER_CLI_PRESENTATION" in line and marker in line:
                        value = line.split(marker, 1)[1].split(" ", 1)[0]
                        origins.add(value.strip())
        except OSError:
            pass
    results["origins_seen"] = sorted(origins)

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
