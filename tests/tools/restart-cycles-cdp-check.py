#!/usr/bin/env python3
"""CDP scenario: repeated start/close cycles + one exact-PID crash recovery,
with a pre-started on-screen window watcher.

Designed for PROJECT-PROGRESS B item "多次启动、退出和崩溃恢复均无短暂上屏".

Key properties:
  * The watcher (tests/tools/onscreen-window-watch.m) is started BEFORE the
    first launch, samples every 10ms (<= the 20ms gate) and matches windows by
    app identity (owner executable path via proc_pidpath), so it covers the
    spawn-time blind spot that a PID sampler started after Popen cannot see. It
    runs until the very end of the scenario; the actual interval and sample
    counts are recorded.
  * All phases use ONE fresh mktemp profile, --use-mock-keychain, the signed app
    and the shared CDP pipe launcher; no real keychain, no `open`/Dock/Launchpad,
    no screenshot APIs, no show-then-hide.
  * N clean cycles: launch -> real page (cycle_N) -> verify content -> clean exit
    via CDP Browser.close.
  * One abnormal termination: the crash-phase page is created and verified, then
    the Sessions files must show a clear update (file set/size/mtime_ns) followed
    by a stable window (timing-only persistence evidence, no URL parsing from
    binary session files). Only then is the exact spawned PID killed with SIGKILL
    after its command line is verified to contain the expected app binary and
    this run's --user-data-dir. No broad process-name kill is used.
  * Crash recovery: relaunch the same profile with `--restore-last-session`
    `--hide-crash-restore-bubble` and without `--no-startup-window`; verify the
    crash-phase page is restored with real content.

Usage:
  restart-cycles-cdp-check.py --app PATH --watcher PATH --server-url URL
      --profile DIR --log-dir DIR [--result-json PATH] [--cycles N]
      [--timeout SEC]
Exit: 0 = harness finished (results in JSON), 1 = fatal launch/CDP failure.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

from cdp_pipe_lib import (
    default_profile,
    inspect_target,
    launch_app,
    stop_app,
)

WATCHER_STARTUP_GRACE_SECONDS = 1.0
# The acceptance gate requires a sampling interval <= 20ms so a sub-100ms flash
# cannot hide between samples; 10ms is used and recorded in the evidence.
WATCHER_INTERVAL_MS = 10
# Crash-phase session persistence: poll for a Sessions file-set/size/mtime
# change after the crash page is verified, then require a short stable window
# before SIGKILL. This is timing-only evidence (no URL parsing from the binary
# session files).
SESSIONS_UPDATE_WAIT_SECONDS = 30
SESSIONS_STABILITY_SECONDS = 2.0
CRASH_PAGE_NAME = "crash_marker"


def wait_for_exit(proc, timeout):
    deadline = time.time() + timeout
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.2)
    if proc.poll() is None:
        return stop_app(proc)
    return proc.returncode


def clean_close(cdp, proc, timeout=60):
    try:
        cdp.request("Browser.close", timeout=10)
    except Exception:  # noqa: BLE001 - pipe closes as the browser exits
        pass
    return wait_for_exit(proc, timeout)


def create_page(cdp, url, timeout):
    result = cdp.request(
        "Target.createTarget",
        {"url": url, "newWindow": True, "background": False},
        timeout=timeout,
    )
    return result["targetId"]


def read_samples(path):
    samples = []
    details = []
    final = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("sample "):
                    fields = dict(part.split("=", 1) for part in line.split()[1:])
                    samples.append(
                        {"ts": int(fields.get("ts", "0")),
                         "count": int(fields.get("count", "0"))}
                    )
                elif line.startswith("detail "):
                    details.append(line)
                elif line.startswith("final "):
                    fields = dict(part.split("=", 1) for part in line.split()[1:])
                    final = {
                        "max": int(fields.get("max", "-1")),
                        "nonzero_samples": int(fields.get("nonzero_samples", "-1")),
                    }
    except OSError:
        pass
    return samples, details, final


def snapshot_sessions(profile):
    """File-set/size/mtime_ns snapshot of <profile>/Default/Sessions.

    Timing-only persistence evidence: the binary session files are NOT parsed
    for URLs (that is deliberately out of scope).
    """
    sessions_dir = os.path.join(profile, "Default", "Sessions")
    snapshot = {}
    try:
        for name in sorted(os.listdir(sessions_dir)):
            path = os.path.join(sessions_dir, name)
            if not os.path.isfile(path):
                continue
            stat = os.stat(path)
            snapshot[name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        pass
    return snapshot


def wait_for_sessions_update(profile, before, seconds):
    """Poll until the Sessions snapshot changes; returns (detected, snapshot, waited)."""
    start = time.time()
    while True:
        current = snapshot_sessions(profile)
        if current != before:
            return True, current, round(time.time() - start, 3)
        if time.time() - start >= seconds:
            return False, current, round(time.time() - start, 3)
        time.sleep(0.2)


def wait_for_stable_sessions(profile, before, update_timeout, stability_seconds):
    """Require a Sessions update, then no further change for stability_seconds.

    Returns (detected, snapshot, waited_seconds, stable). Timing-only evidence:
    the binary session files are never parsed.
    """
    detected, snapshot, waited = wait_for_sessions_update(
        profile, before, update_timeout
    )
    if not detected:
        return False, snapshot, waited, False
    current = snapshot
    last_change = time.time()
    deadline = time.time() + update_timeout
    while time.time() < deadline:
        time.sleep(0.2)
        new_snapshot = snapshot_sessions(profile)
        if new_snapshot != current:
            current = new_snapshot
            last_change = time.time()
        elif time.time() - last_change >= stability_seconds:
            return True, current, waited, True
    return True, current, waited, False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--watcher", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--result-json", default="")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    profile = args.profile or default_profile()
    log_dir = args.log_dir or profile
    os.makedirs(log_dir, exist_ok=True)
    result_path = args.result_json or os.path.join(log_dir, "cdp-results.json")
    samples_path = os.path.join(log_dir, "watcher-samples.txt")
    base = args.server_url.rstrip("/")
    app_real = os.path.realpath(args.app)

    results = {
        "app": args.app,
        "app_real": app_real,
        "profile": profile,
        "watcher": {"samples_file": samples_path, "started": False},
        "cycles": [],
        "crash": {},
        "recovery": {},
        "errors": [],
    }

    watcher_proc = None
    try:
        # Watcher first: covers the spawn-time blind spot and the whole timeline.
        watcher_proc = subprocess.Popen(
            [args.watcher, app_real, samples_path, str(WATCHER_INTERVAL_MS)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        results["watcher"]["pid"] = watcher_proc.pid
        results["watcher"]["interval_ms"] = WATCHER_INTERVAL_MS
        results["watcher"]["startup_grace_seconds"] = WATCHER_STARTUP_GRACE_SECONDS
        time.sleep(WATCHER_STARTUP_GRACE_SECONDS)
        results["watcher"]["started"] = watcher_proc.poll() is None

        # ------------------------------------------------------ clean cycles
        for index in range(1, args.cycles + 1):
            page_name = "cycle_%d" % index
            log_path = os.path.join(log_dir, "browser-cycle%d.log" % index)
            entry = {"index": index, "page": page_name}
            proc = cdp = None
            try:
                proc, cdp, cmdline = launch_app(args.app, profile, log_path)
                entry["cmdline"] = cmdline
                entry["launch_ts"] = int(time.time() * 1000)
                target_id = create_page(cdp, base + "/page?name=" + page_name,
                                        args.timeout)
                entry["inspect"] = inspect_target(cdp, target_id, args.timeout)
            except Exception as exc:  # noqa: BLE001 - recorded per cycle
                entry["error"] = "%s: %s" % (type(exc).__name__, exc)
                results["errors"].append("cycle%d %s" % (index, entry["error"]))
            finally:
                if cdp is not None and proc is not None:
                    entry["exit_code"] = clean_close(cdp, proc)
                elif proc is not None:
                    entry["exit_code"] = stop_app(proc)
                if cdp is not None:
                    cdp.close()
            results["cycles"].append(entry)

        # ------------------------------------------------------ crash phase
        crash = results["crash"]
        log_path = os.path.join(log_dir, "browser-crash.log")
        proc = cdp = None
        try:
            proc, cdp, cmdline = launch_app(args.app, profile, log_path)
            crash["cmdline"] = cmdline
            crash["launch_ts"] = int(time.time() * 1000)
            # Persistence baseline BEFORE the crash page exists.
            crash["sessions_before"] = snapshot_sessions(profile)
            target_id = create_page(cdp, base + "/page?name=" + CRASH_PAGE_NAME,
                                    args.timeout)
            crash["inspect"] = inspect_target(cdp, target_id, args.timeout)

            # Persistence gate: only kill after the Sessions files show a clear
            # update (file set/size/mtime_ns) and then no further change for a
            # short window. Timing-only evidence; binary session files are not
            # parsed.
            detected, snapshot, waited, stable = wait_for_stable_sessions(
                profile,
                crash["sessions_before"],
                SESSIONS_UPDATE_WAIT_SECONDS,
                SESSIONS_STABILITY_SECONDS,
            )
            crash["sessions_update_detected"] = detected
            crash["sessions_update_snapshot"] = snapshot
            crash["sessions_update_wait_seconds"] = waited
            crash["sessions_stable"] = stable
            crash["sessions_persistence_basis"] = (
                "timing-only: file set/size/mtime_ns; no URL parsing from "
                "binary session files"
            )
            if not detected or not stable:
                raise RuntimeError(
                    "session persistence not confirmed before crash kill "
                    "(update=%s stable=%s)" % (detected, stable)
                )

            crash["pid"] = proc.pid
            command_line = subprocess.check_output(
                ["ps", "-ww", "-o", "command=", "-p", str(proc.pid)],
                timeout=10,
            ).decode()
            crash["pid_identity"] = {
                "command_line": command_line.strip(),
                "contains_app_binary": app_real in command_line
                or args.app in command_line,
                "contains_profile": ("--user-data-dir=" + profile) in command_line,
            }
            if not (
                crash["pid_identity"]["contains_app_binary"]
                and crash["pid_identity"]["contains_profile"]
            ):
                raise RuntimeError(
                    "refusing to kill: PID identity not verified for pid=%d"
                    % proc.pid
                )
            os.kill(proc.pid, signal.SIGKILL)
            crash["kill_signal"] = "SIGKILL"
            crash["exit_code"] = wait_for_exit(proc, 30)
        except Exception as exc:  # noqa: BLE001 - recorded
            crash["error"] = "%s: %s" % (type(exc).__name__, exc)
            results["errors"].append("crash %s" % crash["error"])
        finally:
            if cdp is not None:
                cdp.close()
            # Never leave the crash-phase browser running: a leaked process would
            # contaminate the recovery phase and the session evidence.
            if proc is not None and proc.poll() is None and not crash.get("kill_signal"):
                crash["stopped_without_kill"] = True
                crash["exit_code"] = stop_app(proc)

        # ------------------------------------------------------ recovery
        recovery = results["recovery"]
        if crash.get("kill_signal") == "SIGKILL":
            log_path = os.path.join(log_dir, "browser-recovery.log")
            proc = cdp = None
            try:
                proc, cdp, cmdline = launch_app(
                    args.app,
                    profile,
                    log_path,
                    extra_args=["--restore-last-session", "--hide-crash-restore-bubble"],
                    no_startup_window=False,
                )
                recovery["cmdline"] = cmdline
                needle = "/page?name=" + CRASH_PAGE_NAME
                restored = None
                infos = []
                deadline = time.time() + args.timeout
                while time.time() < deadline:
                    infos = cdp.request("Target.getTargets", timeout=args.timeout).get(
                        "targetInfos", []
                    )
                    for info in infos:
                        if needle in info.get("url", ""):
                            restored = info
                            break
                    if restored is not None:
                        break
                    time.sleep(0.5)
                if restored is None:
                    raise RuntimeError("crash-phase page was not restored")
                recovery["target"] = restored
                recovery["inspect"] = inspect_target(
                    cdp, restored.get("targetId", ""), args.timeout
                )
                recovery["all_targets"] = infos
            except Exception as exc:  # noqa: BLE001 - recorded
                recovery["error"] = "%s: %s" % (type(exc).__name__, exc)
                results["errors"].append("recovery %s" % recovery["error"])
            finally:
                if cdp is not None and proc is not None:
                    recovery["exit_code"] = clean_close(cdp, proc)
                elif proc is not None:
                    recovery["exit_code"] = stop_app(proc)
                if cdp is not None:
                    cdp.close()
        else:
            recovery["skipped_reason"] = (
                "crash kill did not happen; recovery skipped to avoid "
                "contaminated evidence"
            )
            results["errors"].append("recovery skipped: crash kill did not happen")
    finally:
        if watcher_proc is not None:
            watcher_proc.send_signal(signal.SIGTERM)
            try:
                watcher_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                watcher_proc.kill()
                watcher_proc.wait(timeout=10)

    samples, details, final = read_samples(samples_path)
    first_launch_ts = None
    for entry in results["cycles"] + [results["crash"], results["recovery"]]:
        ts = entry.get("launch_ts")
        if ts is not None and (first_launch_ts is None or ts < first_launch_ts):
            first_launch_ts = ts
    results["watcher"].update(
        {
            "samples": len(samples),
            "samples_before_first_launch": sum(
                1 for sample in samples
                if first_launch_ts is not None and sample["ts"] < first_launch_ts
            ),
            "max": final.get("max", max((s["count"] for s in samples), default=-1)),
            "nonzero_samples": final.get(
                "nonzero_samples", sum(1 for s in samples if s["count"] > 0)
            ),
            "nonzero_details": details[:20],
        }
    )

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
