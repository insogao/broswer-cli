#!/usr/bin/env python3
"""Offline evidence helpers and fixtures for the Gate F parallel-window contract.

OFFLINE-ONLY: no runtime executor is authorized or present in this slice. The
launch path (Developer ID preflight call, app-identity watcher compile/start,
cdp_pipe_lib launch, CDP session) was removed from this file; it no longer
imports the CDP launcher and no longer exposes any execution flag. Future
runtime requires a new explicit task after the gates (Gate C is BLOCKED).

What remains are the pure, fixture-tested pieces the contract needs:

  * `evaluate_signature_output` -- the four fail-closed signature gates;
  * `parse_watcher_text` / `evaluate_watcher` -- raw watcher samples are
    mandatory; the sample maximum must agree with the final max= line and be 0;
  * `is_acceptable_dom` / `evaluate_windows` -- https + 2xx + complete DOM
    evidence and distinct windows/tabs/URLs;
  * `evaluate_policy`, `evaluate_subset`, `subset_exit_code` -- the split
    verdict helpers (background subset vs Gate F overall);
  * `write_contract` / `write_run_fixture` -- pure evidence-file helpers used by
    the static tests (the fixture never writes human-assist.json);
  * `run_self_test` -- the deterministic fixture suite.

Usage:
  parallel-window-cdp-check.py --self-test
  parallel-window-cdp-check.py --write-run-fixture DIR   (fixture helper only)
Exit: 0 = self-test/fixture ok, 1 = fixture failure, 2 = usage error.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse

SITES = [
    {"name": "w1", "url": "https://example.com/"},
    {"name": "w2", "url": "https://example.org/"},
    {"name": "w3", "url": "https://example.net/"},
]

PRESENTATION_ACTIONS = {"show-and-activate", "show-inactive", "minimize"}

SUBSET_CHECKS = [
    "signature-preflight",
    "watcher-raw-samples-zero",
    "windows-three-distinct",
    "dom-https-2xx-content",
    "cookie-readback-per-window",
    "policy-no-presentation-allow",
]


# --------------------------------------------------------------------------
# Pure helpers (fixture-tested by --self-test)
# --------------------------------------------------------------------------

def evaluate_signature_output(info, returncode, team_id):
    """Four fail-closed gates, same criteria as scripts/verify-signature.sh."""
    if returncode != 0:
        return "unsigned", "codesign exited with %d; fail closed" % returncode
    if "Signature=adhoc" in info:
        return "adhoc", "Signature=adhoc"
    if not re.search(r"^Authority=Developer ID Application:", info, re.MULTILINE):
        authorities = re.findall(r"^Authority=(.+)$", info, re.MULTILINE)
        return (
            "unknown",
            "Authority is not Developer ID Application: %s"
            % ("; ".join(authorities) if authorities else "<none>"),
        )
    if not team_id or not re.search(
        r"^TeamIdentifier=%s$" % re.escape(team_id), info, re.MULTILINE
    ):
        return "unknown", "TeamIdentifier != %s (or missing)" % (team_id or "<unset>")
    return "signed", "Developer ID Application, team %s" % team_id


def parse_watcher_text(text):
    """Parses raw watcher samples. The raw samples are mandatory: a final-only
    line is not evidence (Gate C lesson)."""
    samples = []
    final_max = None
    final_nonzero = None
    malformed = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("sample "):
            fields = dict(
                part.split("=", 1) for part in line.split()[1:] if "=" in part
            )
            try:
                samples.append(int(fields.get("count", "")))
            except ValueError:
                malformed += 1
        elif line.startswith("final "):
            fields = dict(
                part.split("=", 1) for part in line.split()[1:] if "=" in part
            )
            try:
                final_max = int(fields.get("max", ""))
            except ValueError:
                malformed += 1
            try:
                final_nonzero = int(fields.get("nonzero_samples", ""))
            except ValueError:
                final_nonzero = None
    sample_max = max(samples) if samples else None
    sample_nonzero = sum(1 for count in samples if count > 0)
    present = [value for value in (sample_max, final_max) if value is not None]
    max_seen = max(present) if present else None
    consistent = malformed == 0 and (
        sample_max is None or final_max is None or sample_max == final_max
    )
    if final_nonzero is not None and final_nonzero != sample_nonzero:
        consistent = False
    return {
        "samples": len(samples),
        "sample_max": sample_max,
        "final_max": final_max,
        "max": max_seen,
        "nonzero_samples": final_nonzero,
        "sample_nonzero_samples": sample_nonzero,
        "consistent": consistent,
        "malformed": malformed,
    }


def evaluate_watcher(watcher):
    if watcher["samples"] == 0:
        return (
            "CONTRADICTION",
            "no raw watcher samples: a final-only max=%s is not evidence"
            % watcher["final_max"],
        )
    if not watcher["consistent"]:
        return (
            "CONTRADICTION",
            "watcher sample_max=%s != final_max=%s (malformed=%d)"
            % (
                watcher["sample_max"],
                watcher["final_max"],
                watcher["malformed"],
            ),
        )
    if watcher["max"] > 0:
        return (
            "CONTRADICTION",
            "on-screen window observed: max=%d" % watcher["max"],
        )
    return (
        "PASS",
        "raw samples=%d sample_max=0 final_max=0" % watcher["samples"],
    )


def is_acceptable_dom(dom):
    if not dom:
        return False
    try:
        parsed = urllib.parse.urlparse(dom.get("href", ""))
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and dom.get("ready") == "complete"
        and 200 <= int(dom.get("status", 0)) < 300
        and bool(dom.get("title"))
    )


def evaluate_windows(windows, dom_by_name, cookies_by_name, jar_id):
    """Returns (checks, verdict_problems). `windows` is the list of dicts with
    name/window_id/tab_target_id/url."""
    checks = []
    problems = []

    names = [w.get("name") for w in windows]
    ids = [w.get("window_id") for w in windows]
    tabs = [w.get("tab_target_id") for w in windows]
    urls = [w.get("url") for w in windows]
    if len(windows) < 3:
        problems.append("only %d window(s), need >= 3" % len(windows))
    if len(set(ids)) != len(ids) or any(not value for value in ids):
        problems.append("window ids not distinct: %s" % ids)
    if len(set(tabs)) != len(tabs) or any(not value for value in tabs):
        problems.append("active tab targets not distinct: %s" % tabs)
    if len(set(urls)) != len(urls):
        problems.append("URLs not distinct: %s" % urls)
    if problems:
        checks.append(
            {"check": "windows-three-distinct", "status": "CONTRADICTION",
             "detail": "; ".join(problems)}
        )
    else:
        checks.append(
            {"check": "windows-three-distinct", "status": "PASS",
             "detail": "%d windows, distinct ids/tabs/urls: %s"
             % (len(windows), urls)}
        )

    dom_problems = []
    for window in windows:
        name = window.get("name")
        dom = dom_by_name.get(name)
        if not dom:
            dom_problems.append("window %s has no DOM evidence" % name)
        elif not is_acceptable_dom(dom):
            dom_problems.append(
                "window %s DOM not acceptable: href=%s ready=%s status=%s title=%r"
                % (
                    name,
                    (dom or {}).get("href"),
                    (dom or {}).get("ready"),
                    (dom or {}).get("status"),
                    (dom or {}).get("title"),
                )
            )
    if dom_problems:
        checks.append(
            {"check": "dom-https-2xx-content", "status": "CONTRADICTION",
             "detail": "; ".join(dom_problems)}
        )
    else:
        checks.append(
            {"check": "dom-https-2xx-content", "status": "PASS",
             "detail": "https + 2xx + complete + title for %d windows"
             % len(windows)}
        )

    cookie_problems = []
    for window in windows:
        name = window.get("name")
        entry = cookies_by_name.get(name) or {}
        if entry.get("readback_ok") is not True or not entry.get("readback_value"):
            cookie_problems.append("window %s has no cookie readback" % name)
        elif entry.get("jar_id") != jar_id:
            cookie_problems.append(
                "window %s jar_id=%s != session jar" % (name, entry.get("jar_id"))
            )
    if cookie_problems:
        checks.append(
            {"check": "cookie-readback-per-window", "status": "CONTRADICTION",
             "detail": "; ".join(cookie_problems)}
        )
    else:
        checks.append(
            {"check": "cookie-readback-per-window", "status": "PASS",
             "detail": "shared-profile readback ok in %d windows (jar=%s)"
             % (len(windows), jar_id)}
        )
    return checks


def evaluate_policy(entries):
    if not entries:
        return "NOT-EVALUATED", "no policy lines"
    allows = [
        e
        for e in entries
        if e.get("decision") == "allow" and e.get("action") in PRESENTATION_ACTIONS
    ]
    appkit_allows = [
        e
        for e in entries
        if e.get("stage") == "appkit" and e.get("decision") == "allow"
    ]
    denies = [
        e
        for e in entries
        if e.get("decision") == "deny" and e.get("action") == "show-and-activate"
    ]
    if allows or appkit_allows:
        return (
            "CONTRADICTION",
            "presentation allow(s): policy=%d appkit=%d"
            % (len(allows), len(appkit_allows)),
        )
    if not denies:
        return "NOT-EVALUATED", "no show-and-activate deny evidence"
    return "PASS", "show-and-activate denies=%d, presentation allows=0" % len(denies)


def evaluate_subset(signature_status, watcher, windows, dom_by_name,
                    cookies_by_name, jar_id, policy_entries):
    """Builds the automatic background-subset report. HUMAN/ASSIST and
    screenshots are reported as NOT-EVALUATED and are never claimed."""
    checks = []

    if signature_status == "signed":
        checks.append(
            {"check": "signature-preflight", "status": "PASS",
             "detail": "Developer ID Application verified"}
        )
    else:
        checks.append(
            {"check": "signature-preflight", "status": "CONTRADICTION",
             "detail": "signature status=%s" % signature_status}
        )

    watcher_status, watcher_detail = evaluate_watcher(watcher)
    checks.append(
        {"check": "watcher-raw-samples-zero", "status": watcher_status,
         "detail": watcher_detail}
    )

    if not windows:
        checks.append(
            {"check": "windows-three-distinct", "status": "NOT-EVALUATED",
             "detail": "no windows recorded"}
        )
        checks.append(
            {"check": "dom-https-2xx-content", "status": "NOT-EVALUATED",
             "detail": "no windows recorded"}
        )
        checks.append(
            {"check": "cookie-readback-per-window", "status": "NOT-EVALUATED",
             "detail": "no windows recorded"}
        )
    else:
        checks.extend(
            evaluate_windows(windows, dom_by_name, cookies_by_name, jar_id)
        )

    policy_status, policy_detail = evaluate_policy(policy_entries)
    checks.append(
        {"check": "policy-no-presentation-allow", "status": policy_status,
         "detail": policy_detail}
    )

    checks.append(
        {"check": "human-assist-noninterference", "status": "NOT-EVALUATED",
         "detail": "HUMAN/ASSIST takeover is a manual step; the automatic "
                   "subset never claims it"}
    )
    checks.append(
        {"check": "screenshots-gate-c-dependency", "status": "NOT-EVALUATED",
         "detail": "per-window screenshots remain a Gate C dependency "
                   "(offline checker BLOCKED); not claimed here"}
    )

    status_by_name = {check["check"]: check["status"] for check in checks}
    required = [name for name in SUBSET_CHECKS]
    not_passed = [name for name in required if status_by_name.get(name) != "PASS"]
    contradictions = [c for c in checks if c["status"] == "CONTRADICTION"]
    if contradictions:
        subset = "CONTRADICTION"
    elif not_passed:
        subset = "NOT-EVALUATED"
    else:
        subset = "PASS"
    return {
        "background_subset": subset,
        "gate_f_overall": "NOT-EVALUATED",
        "gate_f_overall_detail": "HUMAN/ASSIST is manual and per-window "
                                 "screenshots are a Gate C dependency; the "
                                 "automatic subset can never make Gate F pass",
        "checks": checks,
        "required_checks": required,
        "not_passed_required_checks": not_passed,
        "contradictions": contradictions,
    }


def subset_exit_code(report):
    if report["contradictions"]:
        return 1
    if report["not_passed_required_checks"]:
        return 3
    return 0


# --------------------------------------------------------------------------
# Runtime executor: intentionally absent
# --------------------------------------------------------------------------

# This file is offline-only. The launch/execution path (signature preflight
# call, watcher compile/start, cdp_pipe_lib launch, CDP session) was removed
# so no runtime executor is authorized or present in this slice. Future
# runtime requires a new explicit task after the gates (Gate C is BLOCKED).


def write_contract(evidence_dir, report):
    """Writes the evidence contract files the offline checker reads. It never
    writes human-assist.json: that step is manual."""
    os.makedirs(evidence_dir, exist_ok=True)
    plan = {
        "session": {
            "signed_app": report["signature"]["status"] == "signed",
            "profile_is_mock_keychain": True,
            "managed_background": True,
        },
        "cookie_mode": "shared-profile",
        "windows": [
            {"name": site["name"], "background": True, "optional": False}
            for site in SITES
        ],
    }
    with open(os.path.join(evidence_dir, "parallel-plan.json"), "w",
              encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2)
    with open(os.path.join(evidence_dir, "windows.json"), "w",
              encoding="utf-8") as handle:
        json.dump(report["windows"], handle, indent=2)
    with open(os.path.join(evidence_dir, "page-content.json"), "w",
              encoding="utf-8") as handle:
        json.dump(report["dom"], handle, indent=2)
    with open(os.path.join(evidence_dir, "cookie-evidence.json"), "w",
              encoding="utf-8") as handle:
        json.dump(report["cookies"], handle, indent=2)


def write_run_fixture(root):
    """Fixture/test helper: writes exactly the automatic contract a successful
    run would produce (no human-assist.json, raw watcher samples included)."""
    os.makedirs(root, exist_ok=True)
    windows = [
        {"name": site["name"], "window_id": 100 + index,
         "tab_target_id": "tab-%d" % index, "url": site["url"], "loaded": True}
        for index, site in enumerate(SITES)
    ]
    dom = {
        site["name"]: {
            "title": "Example Domain",
            "href": site["url"],
            "ready": "complete",
            "status": 200,
            "text": "Example Domain",
        }
        for site in SITES
    }
    cookies = {
        site["name"]: {"jar_id": "jar-auto", "readback_value": "v_%s" % site["name"],
                       "readback_ok": True}
        for site in SITES
    }
    report = {
        "signature": {"status": "signed", "detail": "fixture"},
        "windows": windows,
        "dom": dom,
        "cookies": cookies,
        "watcher": parse_watcher_text("sample ts=1 count=0\nfinal max=0 nonzero_samples=0\n"),
        "policy_entries": [
            {"stage": "policy", "decision": "deny", "action": "show-and-activate",
             "origin": "internal", "window_id": "101", "state": "background",
             "owned": "no", "owned_windows": "0",
             "reason": "background-suppresses-native-presentation"},
        ],
        "profile_jar_id": "jar-auto",
    }
    write_contract(root, report)
    with open(os.path.join(root, "watcher-samples.txt"), "w", encoding="utf-8") as handle:
        handle.write("sample ts=1 count=0\nsample ts=2 count=0\n"
                     "final max=0 nonzero_samples=0\n")
    with open(os.path.join(root, "browser.log"), "w", encoding="utf-8") as handle:
        handle.write(
            "BROWSER_CLI_PRESENTATION stage=policy transition=background "
            "reason=command-line-switch window_id=0\n"
        )
        handle.write(
            "BROWSER_CLI_PRESENTATION stage=policy seq=1 decision=deny "
            "action=show-and-activate origin=internal window_id=101 "
            "state=background owned=no owned_windows=0 "
            "reason=background-suppresses-native-presentation\n"
        )
    return root


# --------------------------------------------------------------------------
# Fixture self-test (no browser, no window, no keychain)
# --------------------------------------------------------------------------

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

    good_sig = (
        "Authority=Developer ID Application: jian gao (RAXC5D3A3S)\n"
        "TeamIdentifier=RAXC5D3A3S\n"
    )
    check(
        "Developer ID Application + team is signed",
        evaluate_signature_output(good_sig, 0, "RAXC5D3A3S")[0] == "signed",
    )
    check(
        "ad-hoc signature is rejected",
        evaluate_signature_output("Signature=adhoc\n", 0, "RAXC5D3A3S")[0] == "adhoc",
    )
    check(
        "Apple Development with the same team is rejected",
        evaluate_signature_output(
            "Authority=Apple Development: dev (RAXC5D3A3S)\nTeamIdentifier=RAXC5D3A3S\n",
            0,
            "RAXC5D3A3S",
        )[0] == "unknown",
    )
    check(
        "non-zero codesign exit is rejected",
        evaluate_signature_output("", 1, "RAXC5D3A3S")[0] == "unsigned",
    )

    check(
        "final-only watcher text is a CONTRADICTION",
        evaluate_watcher(parse_watcher_text("final max=0 nonzero_samples=0\n"))[0]
        == "CONTRADICTION",
    )
    check(
        "watcher sample>0 is a CONTRADICTION",
        evaluate_watcher(parse_watcher_text("sample ts=1 count=1\nfinal max=1 nonzero_samples=1\n"))[0]
        == "CONTRADICTION",
    )
    check(
        "consistent zero watcher samples PASS",
        evaluate_watcher(parse_watcher_text("sample ts=1 count=0\nfinal max=0 nonzero_samples=0\n"))[0]
        == "PASS",
    )

    good_windows = [
        {"name": site["name"], "window_id": 100 + index,
         "tab_target_id": "tab-%d" % index, "url": site["url"], "loaded": True}
        for index, site in enumerate(SITES)
    ]
    good_dom = {
        site["name"]: {"title": "Example Domain", "href": site["url"],
                       "ready": "complete", "status": 200, "text": "Example Domain"}
        for site in SITES
    }
    good_cookies = {
        site["name"]: {"jar_id": "jar-a", "readback_value": "v_%s" % site["name"],
                       "readback_ok": True}
        for site in SITES
    }
    good_policy = [
        {"stage": "policy", "decision": "deny", "action": "show-and-activate",
         "origin": "internal", "window_id": "101", "state": "background"}
    ]
    report = evaluate_subset(
        "signed",
        parse_watcher_text("sample ts=1 count=0\nfinal max=0 nonzero_samples=0\n"),
        good_windows,
        good_dom,
        good_cookies,
        "jar-a",
        good_policy,
    )
    check("good automatic subset is PASS", report["background_subset"] == "PASS")
    check("Gate F overall is NOT-EVALUATED", report["gate_f_overall"] == "NOT-EVALUATED")
    check("subset exit code 0 for PASS", subset_exit_code(report) == 0)
    check(
        "HUMAN/ASSIST stays NOT-EVALUATED (never faked)",
        [c for c in report["checks"] if c["check"] == "human-assist-noninterference"][0][
            "status"
        ] == "NOT-EVALUATED",
    )
    check(
        "screenshots stay a Gate C dependency (NOT-EVALUATED)",
        [c for c in report["checks"] if c["check"] == "screenshots-gate-c-dependency"][0][
            "status"
        ] == "NOT-EVALUATED",
    )

    bad = evaluate_subset(
        "signed",
        parse_watcher_text("final max=0 nonzero_samples=0\n"),
        good_windows,
        good_dom,
        good_cookies,
        "jar-a",
        good_policy,
    )
    check(
        "final-only watcher fails the automatic subset",
        bad["background_subset"] == "CONTRADICTION" and subset_exit_code(bad) != 0,
    )

    duplicate_tab = [dict(w) for w in good_windows]
    duplicate_tab[1]["tab_target_id"] = duplicate_tab[0]["tab_target_id"]
    bad = evaluate_subset(
        "signed",
        parse_watcher_text("sample ts=1 count=0\nfinal max=0 nonzero_samples=0\n"),
        duplicate_tab,
        good_dom,
        good_cookies,
        "jar-a",
        good_policy,
    )
    check("duplicate active tab target fails the subset", bad["background_subset"] == "CONTRADICTION")

    bad_dom = dict(good_dom)
    bad_dom["w2"] = dict(bad_dom["w2"], status=404)
    bad = evaluate_subset(
        "signed",
        parse_watcher_text("sample ts=1 count=0\nfinal max=0 nonzero_samples=0\n"),
        good_windows,
        bad_dom,
        good_cookies,
        "jar-a",
        good_policy,
    )
    check("non-2xx DOM evidence fails the subset", bad["background_subset"] == "CONTRADICTION")

    bad_policy = [{"stage": "policy", "decision": "allow",
                   "action": "show-and-activate", "origin": "internal"}]
    bad = evaluate_subset(
        "signed",
        parse_watcher_text("sample ts=1 count=0\nfinal max=0 nonzero_samples=0\n"),
        good_windows,
        good_dom,
        good_cookies,
        "jar-a",
        bad_policy,
    )
    check("presentation allow fails the subset", bad["background_subset"] == "CONTRADICTION")

    missing = evaluate_subset(
        "signed",
        parse_watcher_text("sample ts=1 count=0\nfinal max=0 nonzero_samples=0\n"),
        [],
        {},
        {},
        "jar-a",
        good_policy,
    )
    check(
        "missing window evidence is NOT-EVALUATED (rc 3)",
        missing["background_subset"] == "NOT-EVALUATED" and subset_exit_code(missing) == 3,
    )

    tmp = tempfile.mkdtemp(prefix="parallel-run-fixture.")
    try:
        write_run_fixture(tmp)
        check(
            "run fixture has no human-assist.json (never faked)",
            not os.path.exists(os.path.join(tmp, "human-assist.json")),
        )
        check(
            "run fixture keeps raw watcher samples",
            "sample " in open(os.path.join(tmp, "watcher-samples.txt"),
                              encoding="utf-8").read(),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("SELFTEST SUMMARY: %d passed, %d failed" % (len(passed), len(failures)))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        description="Offline Gate F parallel-window evidence helpers (no runtime "
                    "executor is authorized or present)."
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--write-run-fixture", default="",
                        help="fixture/test helper only")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if args.write_run_fixture:
        write_run_fixture(args.write_run_fixture)
        print("PARALLEL-WINDOW-CDP-CHECK: run fixture written to %s"
              % args.write_run_fixture)
        return 0
    parser.error(
        "no runtime executor is authorized or present in this slice; only "
        "--self-test and --write-run-fixture are available"
    )


if __name__ == "__main__":
    sys.exit(main())
