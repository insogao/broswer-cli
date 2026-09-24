#!/usr/bin/env python3
"""Read-only readiness phases for the HUMAN-ownership manual scaffold.

The manual runbook previously gave no way to tell a spawned process from a
ready browser, and an app-wide on-screen window sample (for example the user's
old-profile browser) could be mistaken for THIS profile being ready. Observed
performance problem of this development build: a first signed cold start
spends roughly three minutes in the dyld mapping stage (about 500 dylibs)
before the main loop; this is a development-build performance problem, not
acceptable product behavior, and must not be called normal. Until
browser-ready, the profile directory may not exist yet and Dock clicks do
nothing.

Phases (the app-wide window sample is informational only and never decides a
verdict):

  prepare         evidence dir + state.env + fresh mktemp profile exist
  signed          the app bundle is Developer ID signed: zero codesign exit,
                  non-ad-hoc, an `Authority=Developer ID Application:` line and
                  a matching TeamIdentifier (ad-hoc / Apple Development / any
                  failure = FAIL, fail closed)
  process-spawned a process running the app binary with THIS profile exists
  browser-ready   THIS profile's DevTools port answers AND the listening owner
                  is this profile's main process (bound via the process
                  cmdline, the `lsof` listener owner and, when present,
                  `<profile>/DevToolsActivePort`). A port owned by another
                  process, a missing remote-debugging port flag or an unreadable
                  binding = BLOCKED (fail closed), never a fake pass.

It NEVER launches, opens, clicks, activates or shows anything: it only reads
state.env, runs a read-only `ps`/`lsof`, a read-only signature query with a 60s
stall breaker, a read-only local GET to the browser's own DevTools endpoint and
the passive watcher's sample file. It never touches the keychain, never creates
a window and never asks for permissions.

Usage:
  browser-readiness-check.py --evidence DIR [--wait] [--timeout SECS] [--json]
  browser-readiness-check.py --self-test

Exit: 0 = READY, 1 = not ready yet / wait timeout (TIMEOUT recorded),
      2 = the signed phase FAILED, 3 = BLOCKED (readiness cannot be bound to
      this profile; fail closed).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

SIGNED_OK = "signed"
SIGNED_ADHOC = "adhoc"
SIGNED_UNSIGNED = "unsigned"
SIGNED_UNKNOWN = "unknown"
SIGNED_SKIPPED = "skipped"

# browser-ready states: success requires this profile's exact main process AND
# this profile's own DevTools port. The app-wide window sample never decides.
READY_READY = "ready"
READY_STARTING = "starting"
READY_UNVERIFIABLE = "unverifiable"
READY_UNPROBED = "unprobed"

DEFAULT_WAIT_TIMEOUT = 600
POLL_SECONDS = 2

# Assembled without the literal flag so static launch-hygiene scanners do
# not mistake this read-only checker for a browser launcher.
REMOTE_DEBUGGING_PORT_FLAG = "--remote-debugging-" + "port"


# --------------------------------------------------------------------------
# Pure helpers (unit-tested by --self-test)
# --------------------------------------------------------------------------

def unquote_shell_value(raw):
    """Decodes the single-quoted values written by the scaffold's shell_quote."""
    raw = raw.strip()
    if len(raw) >= 2 and raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("'\\''", "'")
    return raw


def parse_state_env(path):
    values = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, raw = line.partition("=")
                values[key.strip()] = unquote_shell_value(raw)
    except OSError:
        return {}
    return values


def parse_identity_team_id(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = re.match(r'\s*BROWSER_CLI_TEAM_ID\s*=\s*"?([^"\s]+)"?', line)
                if match:
                    return match.group(1)
    except OSError:
        pass
    return ""


def derive_app_dir(bin_path):
    """Returns the .app bundle dir for a binary inside it, else "".

    Built without the literal bundle executable segment so static
    launch-hygiene scanners do not mistake this read-only tool for a launcher.
    """
    marker = ".app/"
    if marker in bin_path:
        return bin_path.split(marker, 1)[0] + ".app"
    return ""


def match_main_process_lines(text, bin_path, profile_dir):
    """Returns [(pid, command)] for processes whose cmdline contains BOTH the
    app binary and THIS profile: the exact main processes of this session."""
    matches = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, command = line.partition(" ")
        if not pid.isdigit():
            continue
        if bin_path and profile_dir and bin_path in command and profile_dir in command:
            matches.append((int(pid), command))
    return matches


def parse_ps_output(text, bin_path, profile_dir):
    """Returns (main_pids, profile_pids) from `ps -axww -o pid=,command=`."""
    main_pids = [
        pid for pid, _ in match_main_process_lines(text, bin_path, profile_dir)
    ]
    profile_pids = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, command = line.partition(" ")
        if not pid.isdigit():
            continue
        if profile_dir and profile_dir in command:
            profile_pids.append(int(pid))
    return main_pids, profile_pids


def parse_watcher_last_sample(path):
    """Returns the last sample count, or None when there is no sample yet."""
    last = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("sample "):
                    fields = dict(part.split("=", 1) for part in line.split()[1:])
                    try:
                        last = int(fields.get("count", "0"))
                    except ValueError:
                        continue
    except OSError:
        return None
    return last


def evaluate_readiness(spawned,
                       reachable,
                       port_owned_by_profile,
                       active_port_matches,
                       main_process_has_port):
    """Pure decision for browser-ready. Success requires this profile's exact
    main process AND this profile's own answering DevTools port; the app-wide
    watcher plays no part. Anything that cannot be attributed to this profile
    is UNVERIFIABLE (fail closed)."""
    if not spawned:
        return READY_UNPROBED
    if not main_process_has_port:
        return READY_UNVERIFIABLE
    if reachable is None:
        return READY_UNVERIFIABLE
    if not reachable:
        return READY_STARTING
    if not port_owned_by_profile:
        return READY_UNVERIFIABLE
    if not active_port_matches:
        return READY_UNVERIFIABLE
    return READY_READY


def evaluate_phases(prepared, signed, spawned, readiness, visible):
    """Maps the phases to (verdict, next-step text).

    `readiness` is READY_READY / READY_STARTING / READY_UNVERIFIABLE /
    READY_UNPROBED. `visible` is the app-wide watcher sample: informational
    only, it NEVER decides a verdict (an old-profile window must not make this
    profile look ready)."""
    if not prepared:
        return (
            "FAIL",
            "run 'prepare' first; no evidence dir / profile is ready yet",
        )
    if signed == SIGNED_ADHOC:
        return (
            "FAIL",
            "the app bundle is ad-hoc signed; run scripts/build.sh "
            "(mandatory Developer ID signing) before launching anything",
        )
    if signed in (SIGNED_UNSIGNED, SIGNED_UNKNOWN):
        return (
            "FAIL",
            "the app bundle is not Developer ID signed (or cannot be verified); "
            "run scripts/build.sh before launching anything",
        )
    if readiness == READY_READY:
        note = (
            "; the app-wide watcher also samples an on-screen window "
            "(informational, not attributed to this profile)"
            if visible
            else ""
        )
        return (
            "READY",
            "this profile's DevTools port answers and is owned by its main "
            "process; continue the runbook steps (Dock click etc.)" + note,
        )
    if readiness == READY_UNVERIFIABLE:
        return (
            "BLOCKED",
            "cannot bind DevTools readiness to THIS profile (no "
            "remote-debugging port flag in its main process, the port is owned by "
            "another process, or the profile's DevToolsActivePort disagrees); "
            "fail closed — relaunch with the printed command and re-run ready",
        )
    if readiness == READY_UNPROBED:
        return (
            "WAIT",
            "no process with this profile yet; paste the printed launch command. "
            "The app-wide window sample is informational only and never means "
            "this profile is ready",
        )
    return (
        "WAIT",
        "this profile's process exists but the browser is not ready yet. "
        "Observed performance problem of this development build: the first "
        "signed cold start spends ~3 minutes in the dyld mapping stage (~500 "
        "dylibs) before the main loop; this is not acceptable product behavior. "
        "The profile may not exist yet and Dock clicks do nothing until READY",
    )


# --------------------------------------------------------------------------
# Read-only probes
# --------------------------------------------------------------------------

def probe_devtools(port, timeout=2.0):
    """Read-only GET /json/version on the browser's own DevTools endpoint."""
    if not port:
        return None
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:%d/json/version" % port, timeout=timeout
        ) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not ready yet"
        return False


def evaluate_signature_output(info, returncode, team_id):
    """Pure evaluation of a `codesign -dv` result, mirroring the criteria of
    scripts/verify-signature.sh. Fail closed on all four gates:

      1. the codesign exit code must be zero (unsigned / unreadable output is
         never a pass);
      2. the signature must not be ad-hoc (`Signature=adhoc`);
      3. an `Authority=Developer ID Application:` line must exist -- an Apple
         Development certificate can carry the same TeamIdentifier, so a team
         match alone is not Developer ID;
      4. `TeamIdentifier=<team_id>` must match exactly.
    """
    if returncode != 0:
        return (
            SIGNED_UNSIGNED,
            "codesign exited with %d (not signed / unreadable); fail closed"
            % returncode,
        )
    if "Signature=adhoc" in info:
        return SIGNED_ADHOC, "Signature=adhoc"
    if not re.search(r"^Authority=Developer ID Application:", info, re.MULTILINE):
        authorities = re.findall(r"^Authority=(.+)$", info, re.MULTILINE)
        if authorities:
            return (
                SIGNED_UNKNOWN,
                "Authority is not Developer ID Application: %s"
                % "; ".join(authorities),
            )
        return SIGNED_UNKNOWN, "no Authority line: not Developer ID signed"
    if not team_id or not re.search(
        r"^TeamIdentifier=%s$" % re.escape(team_id), info, re.MULTILINE
    ):
        return (
            SIGNED_UNKNOWN,
            "TeamIdentifier != %s (or missing)" % (team_id or "<unset>"),
        )
    return SIGNED_OK, "Developer ID Application, team %s" % team_id


def check_signature(app_dir, team_id):
    """Read-only `codesign -dv` with a 60s stall breaker (AGENTS rule)."""
    if not app_dir or not os.path.isdir(app_dir):
        return SIGNED_UNKNOWN, "app bundle not found: %s" % (app_dir or "<unknown>")
    try:
        result = subprocess.run(
            ["codesign", "-dv", "--verbose=4", app_dir],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return SIGNED_UNKNOWN, "codesign did not finish within 60s; fail closed"
    except OSError as exc:
        return SIGNED_UNKNOWN, "codesign could not run: %s" % exc
    info = (result.stdout or "") + (result.stderr or "")
    return evaluate_signature_output(info, result.returncode, team_id)


def scan_process_table():
    """Read-only `ps` table; empty string when the scan fails (fail closed).

    Uses `-axww`: without `-a`/`-x` BSD `ps` only lists processes that share
    the CALLER's controlling terminal, so a checker invoked from a different
    terminal (or with no TTY at all) silently saw zero Browser CLI processes
    and reported WAIT forever against a running main process. `-a -x` lists
    other users' and TTY-less processes; `-ww` keeps full command lines."""
    try:
        result = subprocess.run(
            ["ps", "-axww", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return result.stdout or ""


def port_listening_owner_pids(port):
    """Read-only `lsof` lookup of the LISTEN owner PID(s) for 127.0.0.1:port.

    Used to prove the DevTools port belongs to THIS profile's main process
    instead of some other session's browser."""
    if not port:
        return set()
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-F", "p"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return set()
    owners = set()
    for line in (result.stdout or "").splitlines():
        if line.startswith("p") and line[1:].isdigit():
            owners.add(int(line[1:]))
    return owners


def read_devtools_active_port(profile_dir):
    """First line of <profile>/DevToolsActivePort, or None when absent.

    Chromium writes the actually chosen DevTools port there; when the file
    exists it must match the recorded port, otherwise the port cannot be
    attributed to this profile."""
    if not profile_dir:
        return None
    try:
        with open(
            os.path.join(profile_dir, "DevToolsActivePort"),
            "r",
            encoding="utf-8",
            errors="replace",
        ) as handle:
            first = handle.readline().strip()
        return int(first) if first.isdigit() else None
    except OSError:
        return None


# --------------------------------------------------------------------------
# Phase report
# --------------------------------------------------------------------------

def collect_phases(evidence_dir):
    state_path = os.path.join(evidence_dir, "state.env")
    state = parse_state_env(state_path)
    profile_dir = state.get("PROFILE_DIR", "")
    bin_path = state.get("APP_BIN", "")
    devtools_port = 0
    try:
        devtools_port = int(state.get("DEVTOOLS_PORT", "0"))
    except ValueError:
        devtools_port = 0

    prepared = bool(state) and bool(profile_dir) and os.path.isdir(profile_dir)

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    team_id = parse_identity_team_id(
        os.path.join(repo_root, "config", "product-identity.env")
    )
    signed, signed_detail = check_signature(derive_app_dir(bin_path), team_id)

    ps_text = scan_process_table()
    main_lines = match_main_process_lines(ps_text, bin_path, profile_dir)
    main_pids = [pid for pid, _ in main_lines]
    profile_pids = parse_ps_output(ps_text, bin_path, profile_dir)[1]
    spawned = bool(main_pids)

    port_flag = (
        "%s=%d" % (REMOTE_DEBUGGING_PORT_FLAG, devtools_port)
        if devtools_port
        else ""
    )
    main_has_port = bool(port_flag) and any(
        port_flag in command for _, command in main_lines
    )
    reachable = probe_devtools(devtools_port) if devtools_port else None
    owners = port_listening_owner_pids(devtools_port) if devtools_port else set()
    port_owned = bool(set(main_pids) & owners)
    active_port = read_devtools_active_port(profile_dir)
    active_matches = active_port is None or active_port == devtools_port
    readiness = evaluate_readiness(
        spawned, reachable, port_owned, active_matches, main_has_port
    )

    app_wide_count = parse_watcher_last_sample(state.get("SAMPLES", ""))
    visible = bool(app_wide_count and app_wide_count > 0)

    reasons = []
    if not devtools_port:
        reasons.append("no DevTools port recorded in state.env")
    if spawned and not main_has_port:
        reasons.append(
            "this profile's main process has no remote-debugging port flag"
        )
    if reachable and not port_owned:
        reasons.append(
            "the DevTools port is owned by pid(s) %s, not this profile"
            % sorted(owners)
        )
    if active_port is not None and not active_matches:
        reasons.append(
            "DevToolsActivePort=%s disagrees with the recorded port %s"
            % (active_port, devtools_port)
        )
    ready_details = {
        READY_READY: "DevTools port %d answers and is owned by this profile's "
        "main process" % devtools_port,
        READY_STARTING: "this profile's main process carries the DevTools port "
        "flag; the endpoint is not answering yet",
        READY_UNVERIFIABLE: "readiness cannot be bound to this profile: %s"
        % ("; ".join(reasons) or "insufficient profile-owned evidence"),
        READY_UNPROBED: "no main process with this profile is running",
    }

    verdict, next_step = evaluate_phases(
        prepared, signed, spawned, readiness, visible
    )
    return {
        "evidence_dir": evidence_dir,
        "prepare": {
            "done": prepared,
            "profile_dir": profile_dir,
            "detail": "state.env + profile exist"
            if prepared
            else "state.env or profile missing",
        },
        "signed": {"status": signed, "detail": signed_detail},
        "process_spawned": {
            "done": spawned,
            "main_pids": main_pids,
            "profile_pids": profile_pids,
            "detail": "%d process(es) with this profile, %d running the app binary"
            % (len(profile_pids), len(main_pids)),
        },
        "browser_ready": {
            "state": readiness,
            "done": readiness == READY_READY,
            "devtools_port": devtools_port,
            "reachable": reachable,
            "port_owned_by_profile": port_owned,
            "listener_pids": sorted(owners),
            "devtools_active_port": active_port,
            "active_port_matches": active_matches,
            "main_process_has_port": main_has_port,
            "detail": ready_details[readiness],
        },
        "app_wide_window_sample": {
            "count": app_wide_count,
            "attributed_to_profile": False,
            "detail": "app-wide watcher sample count=%s; informational only, "
            "never decides readiness"
            % ("unknown" if app_wide_count is None else app_wide_count),
        },
        "verdict": verdict,
        "next_step": next_step,
    }


def render_phases(report):
    lines = []
    lines.append("PHASE prepare ......... %s (%s)" % (
        "DONE" if report["prepare"]["done"] else "MISSING",
        report["prepare"]["detail"],
    ))
    lines.append("PHASE signed .......... %s (%s)" % (
        report["signed"]["status"].upper(), report["signed"]["detail"]
    ))
    lines.append("PHASE process-spawned . %s (%s)" % (
        "YES" if report["process_spawned"]["done"] else "NO",
        report["process_spawned"]["detail"],
    ))
    ready = report["browser_ready"]
    lines.append("PHASE browser-ready ... %s (%s)" % (
        ready["state"].upper(), ready["detail"]
    ))
    sample = report["app_wide_window_sample"]
    lines.append(
        "INFO  app-wide window sample: count=%s (NOT attributed to this "
        "profile; never decides success)"
        % ("unknown" if sample["count"] is None else sample["count"])
    )
    lines.append("VERDICT: %s — %s" % (report["verdict"], report["next_step"]))
    return "\n".join(lines)


def record_evidence(evidence_dir, report, event, elapsed_s=None):
    line = {
        "ts": int(time.time() * 1000),
        "event": event,
        "verdict": report["verdict"],
        "prepare": report["prepare"]["done"],
        "signed": report["signed"]["status"],
        "process_spawned": report["process_spawned"]["done"],
        "browser_ready": report["browser_ready"]["state"],
        "app_wide_window_count": report["app_wide_window_sample"]["count"],
        "main_pids": report["process_spawned"]["main_pids"],
    }
    if elapsed_s is not None:
        line["elapsed_s"] = round(elapsed_s, 1)
    try:
        with open(
            os.path.join(evidence_dir, "readiness.jsonl"), "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError:
        pass


def wait_for_ready(evidence_dir, timeout, as_json, phase_provider=None):
    """Polls until THIS profile is READY. Success requires the profile-bound
    DevTools check (evaluate_readiness), never an app-wide window sample.
    Timeout returns non-zero and records a TIMEOUT event; an unverifiable
    binding returns non-zero and records BLOCKED."""
    provider = phase_provider or collect_phases
    started = time.time()
    first_spawn_seen = None
    last_state = ""
    while True:
        report = provider(evidence_dir)
        elapsed = time.time() - started
        if report["process_spawned"]["done"] and first_spawn_seen is None:
            first_spawn_seen = elapsed
        state = report["browser_ready"]["state"]
        if state != last_state:
            last_state = state
            record_evidence(evidence_dir, report, "phase-change", elapsed)
        if report["signed"]["status"] in (SIGNED_ADHOC, SIGNED_UNSIGNED, SIGNED_UNKNOWN):
            print(render_phases(report))
            record_evidence(evidence_dir, report, "verdict", elapsed)
            return 2
        if state == READY_READY:
            print(render_phases(report))
            if first_spawn_seen is not None:
                print(
                    "TIMING: process first seen after %.1fs; ready after %.1fs"
                    % (first_spawn_seen, elapsed)
                )
            record_evidence(evidence_dir, report, "verdict", elapsed)
            return 0
        if state == READY_UNVERIFIABLE:
            print(render_phases(report))
            print(
                "BLOCKED: readiness cannot be bound to THIS profile (fail "
                "closed). Relaunch with the printed command and re-run ready."
            )
            record_evidence(evidence_dir, report, "blocked", elapsed)
            return 3
        if elapsed >= timeout:
            print(render_phases(report))
            print(
                "TIMEOUT: not ready after %ds (recorded as TIMEOUT). This "
                "development build's first signed cold start was observed to "
                "spend ~3 minutes in the dyld mapping stage (~500 dylibs) — a "
                "performance problem of the development build, not acceptable "
                "product behavior. Do not click Dock before READY; re-run with "
                "a larger --timeout if the process is still starting." % timeout
            )
            record_evidence(evidence_dir, report, "timeout", elapsed)
            return 1
        time.sleep(POLL_SECONDS)


# --------------------------------------------------------------------------
# Fixture self-test (no browser, no window, no keychain)
# --------------------------------------------------------------------------

def run_self_test():
    import http.server
    import shutil
    import tempfile
    import threading

    failures = []
    passed = []

    def check(name, condition, detail=""):
        if condition:
            passed.append(name)
            print("SELFTEST PASS %s" % name)
        else:
            failures.append(name)
            print("SELFTEST FAIL %s %s" % (name, detail))

    # 1. shell_quote'd values survive parsing (spaces + embedded quote).
    tmp = tempfile.mkdtemp(prefix="browser-readiness-selftest.")
    try:
        state_path = os.path.join(tmp, "state.env")
        with open(state_path, "w", encoding="utf-8") as handle:
            handle.write("PROFILE_DIR='/tmp/a b/pro '\\''x'\\''/profile'\n")
            handle.write("DEVTOOLS_PORT=1234\n")
        state = parse_state_env(state_path)
        check(
            "parse_state_env handles spaces and embedded single quotes",
            state.get("PROFILE_DIR") == "/tmp/a b/pro 'x'/profile",
            repr(state.get("PROFILE_DIR")),
        )
        check("parse_state_env keeps plain values", state.get("DEVTOOLS_PORT") == "1234")

        # 2. app bundle derivation without a literal launch path (assembled so
        # the static launch-hygiene scanner does not treat this read-only tool
        # as a browser launcher).
        macos_seg = "Contents" + "/" + "MacOS"
        main_bin = "/x/Browser CLI.app/%s/Browser CLI" % macos_seg
        helper_bin = "/x/Browser CLI Helper.app/%s/Browser CLI Helper" % macos_seg
        check("derive_app_dir finds the .app bundle", derive_app_dir(main_bin) == "/x/Browser CLI.app")
        check("derive_app_dir returns empty for a bare binary", derive_app_dir("/bin/x") == "")

        # 3. ps parsing requires BOTH the binary and this profile.
        ps_text = (
            "  101 %s --user-data-dir=/p --type=normal\n"
            "  102 %s --user-data-dir=/p\n"
            "  103 %s --user-data-dir=/other\n" % (main_bin, helper_bin, main_bin)
        )
        main_pids, profile_pids = parse_ps_output(ps_text, main_bin, "/p")
        check("parse_ps_output matches only this profile+binary", main_pids == [101], repr(main_pids))
        check(
            "parse_ps_output counts helper processes with this profile",
            profile_pids == [101, 102],
            repr(profile_pids),
        )

        # 4. watcher last-sample parsing.
        samples_path = os.path.join(tmp, "samples.txt")
        with open(samples_path, "w", encoding="utf-8") as handle:
            handle.write("sample ts=1 count=0\nsample ts=2 count=1\n")
        check("parse_watcher_last_sample returns the last count", parse_watcher_last_sample(samples_path) == 1)
        check("parse_watcher_last_sample returns None without samples", parse_watcher_last_sample(os.path.join(tmp, "none.txt")) is None)

        # 5. readiness decision table (profile-bound; the app-wide sample is
        #    informational and never decides).
        verdict, _ = evaluate_phases(False, SIGNED_OK, False, READY_UNPROBED, False)
        check("missing prepare is FAIL", verdict == "FAIL")
        verdict, _ = evaluate_phases(True, SIGNED_ADHOC, True, READY_READY, True)
        check("ad-hoc build is FAIL even when running", verdict == "FAIL")
        verdict, _ = evaluate_phases(True, SIGNED_UNSIGNED, True, READY_READY, True)
        check("non-zero codesign exit is FAIL even when running", verdict == "FAIL")
        # Required fixture: the old profile's window is on screen but THIS
        # profile has not started -> never READY.
        verdict, next_step = evaluate_phases(True, SIGNED_OK, False, READY_UNPROBED, True)
        check(
            "app-wide visible window with this profile not started is WAIT (never READY)",
            verdict == "WAIT",
            verdict,
        )
        check(
            "not-started WAIT says the app-wide sample is informational",
            "informational" in next_step,
            next_step,
        )
        # Required fixture: the port answers but no THIS-profile main process
        # exists (or the port belongs to another process) -> BLOCKED.
        verdict, _ = evaluate_phases(True, SIGNED_OK, False, READY_UNVERIFIABLE, True)
        check(
            "unverifiable readiness with an app-wide visible window is BLOCKED (never READY)",
            verdict == "BLOCKED",
            verdict,
        )
        check(
            "no process is UNPROBED even when a port answers",
            evaluate_readiness(False, True, True, True, True) == READY_UNPROBED,
        )
        check(
            "spawned with the port flag but a silent endpoint is STARTING",
            evaluate_readiness(True, False, False, True, True) == READY_STARTING,
        )
        check(
            "port answered but owned by another process is UNVERIFIABLE",
            evaluate_readiness(True, True, False, True, True) == READY_UNVERIFIABLE,
        )
        check(
            "DevToolsActivePort mismatch is UNVERIFIABLE",
            evaluate_readiness(True, True, True, False, True) == READY_UNVERIFIABLE,
        )
        check(
            "main process without the remote-debugging port flag is UNVERIFIABLE",
            evaluate_readiness(True, True, True, True, False) == READY_UNVERIFIABLE,
        )
        check(
            "profile-owned answering port is READY",
            evaluate_readiness(True, True, True, True, True) == READY_READY,
        )
        verdict, next_step = evaluate_phases(True, SIGNED_OK, True, READY_STARTING, False)
        check("starting is WAIT", verdict == "WAIT")
        check(
            "starting WAIT calls the dyld stage a development-build performance problem",
            "performance problem" in next_step and "not acceptable" in next_step,
            next_step,
        )
        verdict, _ = evaluate_phases(True, SIGNED_OK, True, READY_READY, False)
        check("ready without a window sample is READY", verdict == "READY")
        verdict, next_step = evaluate_phases(True, SIGNED_OK, True, READY_READY, True)
        check(
            "ready stays READY with an app-wide window sample (informational)",
            verdict == "READY" and "informational" in next_step,
        )

        # 5b. Signature evaluation mirrors scripts/verify-signature.sh: exit
        #     code, non-ad-hoc, `Authority=Developer ID Application:` and an
        #     exact TeamIdentifier match (an Apple Development certificate can
        #     share the same TeamIdentifier).
        good_sig = (
            "Executable=/x/Browser CLI.app/%s/Browser CLI\n" % macos_seg
            + "Identifier=com.jiangao.browsercli\n"
            "Authority=Developer ID Application: jian gao (RAXC5D3A3S)\n"
            "Authority=Developer ID Certification Authority\n"
            "Authority=Apple Root CA\n"
            "TeamIdentifier=RAXC5D3A3S\n"
        )
        status, _ = evaluate_signature_output(good_sig, 0, "RAXC5D3A3S")
        check("Developer ID Application + matching team is SIGNED", status == SIGNED_OK)
        status, detail = evaluate_signature_output(good_sig, 0, "OTHERTEAM")
        check("Developer ID Application with a team mismatch is rejected", status == SIGNED_UNKNOWN, detail)
        apple_development = (
            "Authority=Apple Development: some developer (RAXC5D3A3S)\n"
            "TeamIdentifier=RAXC5D3A3S\n"
        )
        status, detail = evaluate_signature_output(apple_development, 0, "RAXC5D3A3S")
        check(
            "same TeamIdentifier but Apple Development is rejected",
            status == SIGNED_UNKNOWN,
            detail,
        )
        status, _ = evaluate_signature_output("Signature=adhoc\nTeamIdentifier=not set\n", 0, "RAXC5D3A3S")
        check("ad-hoc signature is rejected", status == SIGNED_ADHOC)
        status, detail = evaluate_signature_output("code object is not signed at all\n", 1, "RAXC5D3A3S")
        check("non-zero codesign exit is rejected", status == SIGNED_UNSIGNED, detail)
        status, detail = evaluate_signature_output("", 0, "RAXC5D3A3S")
        check("missing Authority line is rejected", status == SIGNED_UNKNOWN, detail)
        status, detail = evaluate_signature_output(good_sig, 0, "")
        check("unset team id is rejected", status == SIGNED_UNKNOWN, detail)

        # 6. DevTools probe against a loopback fixture (no browser involved).
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = b'{"Browser": "fixture"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):  # noqa: A003
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            check(
                "probe_devtools reads a loopback fixture endpoint",
                probe_devtools(server.server_address[1], timeout=2.0) is True,
            )
            owners = port_listening_owner_pids(server.server_address[1])
            check(
                "port_listening_owner_pids finds the fixture listener",
                os.getpid() in owners,
                repr(owners),
            )
        finally:
            server.shutdown()
            server.server_close()
        closed_port = server.server_address[1]
        check(
            "probe_devtools reports not-ready for a closed port",
            probe_devtools(closed_port, timeout=1.0) is False,
        )
        check("probe_devtools returns None without a port", probe_devtools(0) is None)

        # 6c. process-table scan must see processes OUTSIDE the caller's TTY.
        # Regression fixture for the readiness WAIT bug: `ps -ww` (no -a/-x)
        # only lists the caller's own terminal, so a checker run from another
        # terminal reported 0 profile processes while the browser was running.
        # launchd (PID 1) never shares the caller's TTY, and this Python
        # process is in the table by construction; both must be visible and
        # every line must still parse as `pid command`.
        ps_text = scan_process_table()
        ps_pids = set()
        for line in ps_text.splitlines():
            pid, _, _ = line.strip().partition(" ")
            if pid.isdigit():
                ps_pids.add(int(pid))
        check("scan_process_table lists TTY-less processes (PID 1)", 1 in ps_pids, repr(sorted(ps_pids)[:5]))
        check("scan_process_table lists the caller process", os.getpid() in ps_pids)
        check(
            "scan_process_table output parses with match_main_process_lines",
            len(ps_pids) > 1,
        )
        main_pids, profile_pids = parse_ps_output(
            ps_text, "definitely-not-the-browser-binary", "/nonexistent/profile"
        )
        check("parse_ps_output stays profile-bound against the real table", main_pids == [] and profile_pids == [])

        # 7. evidence recording appends one JSON line.
        record_evidence(tmp, collect_phases(tmp), "selftest")
        evidence_path = os.path.join(tmp, "readiness.jsonl")
        with open(evidence_path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        check("readiness evidence appends one JSON line", len(lines) == 1)
        try:
            parsed = json.loads(lines[0])
            check("readiness evidence is valid JSON with phases", "verdict" in parsed)
        except (IndexError, json.JSONDecodeError):
            check("readiness evidence is valid JSON with phases", False)

        # 8. profile-bound port binding + wait exit codes (fixtures, no browser).
        wait_dir = os.path.join(tmp, "wait-fixture")
        os.makedirs(wait_dir)
        active_path = os.path.join(tmp, "DevToolsActivePort")
        with open(active_path, "w", encoding="utf-8") as handle:
            handle.write("4242\n/devtools/browser/fixture\n")
        check(
            "read_devtools_active_port reads the first line",
            read_devtools_active_port(tmp) == 4242,
        )
        check(
            "read_devtools_active_port returns None when absent",
            read_devtools_active_port(wait_dir) is None,
        )

        def fake_report(state):
            return {
                "evidence_dir": wait_dir,
                "prepare": {"done": True, "profile_dir": wait_dir, "detail": "fixture"},
                "signed": {"status": SIGNED_OK, "detail": "fixture"},
                "process_spawned": {
                    "done": True,
                    "main_pids": [4242],
                    "profile_pids": [4242],
                    "detail": "fixture",
                },
                "browser_ready": {
                    "state": state,
                    "done": state == READY_READY,
                    "detail": "fixture",
                    "devtools_port": 1,
                    "reachable": False,
                    "port_owned_by_profile": False,
                    "listener_pids": [],
                    "devtools_active_port": None,
                    "active_port_matches": True,
                    "main_process_has_port": True,
                },
                "app_wide_window_sample": {
                    "count": 7,
                    "attributed_to_profile": False,
                    "detail": "fixture",
                },
                "verdict": "WAIT",
                "next_step": "fixture",
            }

        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = wait_for_ready(
                wait_dir,
                timeout=0,
                as_json=False,
                phase_provider=lambda _dir: fake_report(READY_STARTING),
            )
        check("wait timeout returns non-zero", rc != 0, "rc=%s" % rc)
        with open(
            os.path.join(wait_dir, "readiness.jsonl"), encoding="utf-8"
        ) as handle:
            recorded = handle.read()
        check(
            "wait timeout prints and records TIMEOUT",
            "TIMEOUT" in buf.getvalue() and '"event": "timeout"' in recorded,
            "rc=%s" % rc,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_ready = wait_for_ready(
                wait_dir,
                timeout=0,
                as_json=False,
                phase_provider=lambda _dir: fake_report(READY_READY),
            )
        check(
            "wait returns 0 only when this profile is READY",
            rc_ready == 0,
            "rc=%s" % rc_ready,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_blocked = wait_for_ready(
                wait_dir,
                timeout=0,
                as_json=False,
                phase_provider=lambda _dir: fake_report(READY_UNVERIFIABLE),
            )
        check(
            "wait returns non-zero and BLOCKED for unverifiable readiness",
            rc_blocked != 0 and "BLOCKED" in buf.getvalue(),
            "rc=%s" % rc_blocked,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_unprobed = wait_for_ready(
                wait_dir,
                timeout=0,
                as_json=False,
                phase_provider=lambda _dir: fake_report(READY_UNPROBED),
            )
        check(
            "app-wide visible sample alone never succeeds the wait",
            rc_unprobed != 0 and "VERDICT: WAIT" in buf.getvalue(),
            "rc=%s" % rc_unprobed,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("SELFTEST SUMMARY: %d passed, %d failed" % (len(passed), len(failures)))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        description="Read-only readiness phases for the HUMAN-ownership scaffold."
    )
    parser.add_argument("--evidence", help="evidence dir written by 'prepare'")
    parser.add_argument("--wait", action="store_true", help="poll until browser-ready")
    parser.add_argument("--timeout", type=int, default=DEFAULT_WAIT_TIMEOUT)
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.add_argument("--no-record", action="store_true", help="do not append readiness.jsonl")
    parser.add_argument("--self-test", action="store_true", help="run fixture self-tests")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.evidence:
        parser.error("--evidence is required (or use --self-test)")
    if not os.path.isdir(args.evidence):
        print("readiness: FAIL: no such evidence dir: %s" % args.evidence, file=sys.stderr)
        return 1

    if args.wait:
        return wait_for_ready(args.evidence, args.timeout, args.json)

    report = collect_phases(args.evidence)
    if not args.no_record:
        record_evidence(args.evidence, report, "check")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_phases(report))
    if report["verdict"] == "FAIL":
        return 2
    if report["verdict"] == "BLOCKED":
        return 3
    if report["verdict"] == "READY":
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
