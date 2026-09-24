#!/usr/bin/env python3
"""Offline verdict engine for the Gate F (parallel background windows) contract.

It never launches, shows, activates or captures anything. The evidence input is
read-only; the only files this tool writes are parallel-report.json/md in the
report dir.

The required window matrix lives HERE, in code (w1/w2/w3, background, real
HTTPS, distinct active tabs) and is never derived from the evidence plan, so
deleting / renaming / optional-izing a window can never reach rc=0.

Lessons from the Gate C checker (BLOCKED) are applied:
  * the raw app-identity watcher samples are mandatory: a `final max=0` line
    without raw samples is NOT evidence and is a contradiction, never a pass;
  * the reported maximum is the upper bound of the raw sample maximum and the
    final max= line, and the two must agree;
  * 0 is returned only when every required check PASSes; insufficient evidence
    returns 3; contradictions return 1.

Screenshots are deliberately NOT part of this contract: per-window capture
remains a Gate C dependency (currently BLOCKED) and is reported as
NOT-EVALUATED, never as a pass.

Usage:
  parallel-evidence-check.py --evidence DIR [--report-dir DIR]
  parallel-evidence-check.py --self-test
  parallel-evidence-check.py --write-fixture DIR   (fixture/test helper only)
Exit: 0 = every required check PASS, 1 = contradiction(s), 2 = usage error,
      3 = INSUFFICIENT-EVIDENCE (required check(s) NOT-EVALUATED or missing).
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse

PRESENTATION_ACTIONS = {"show-and-activate", "show-inactive", "minimize"}

PLAN_FILE = "parallel-plan.json"
WINDOWS_FILE = "windows.json"
PAGE_CONTENT_FILE = "page-content.json"
COOKIE_FILE = "cookie-evidence.json"
HUMAN_ASSIST_FILE = "human-assist.json"
WATCHER_FILE = "watcher-samples.txt"
POLICY_FILE = "browser.log"

MIN_BACKGROUND_WINDOWS = 3

# Fixed required window matrix: names + required properties, defined in code.
REQUIRED_WINDOWS = [
    {"name": "w1", "background": True, "optional": False},
    {"name": "w2", "background": True, "optional": False},
    {"name": "w3", "background": True, "optional": False},
]

REQUIRED_GLOBAL_CHECKS = [
    "session-contract",
    "plan-window-matrix",
    "watcher-no-visible-window",
    "policy-no-presentation-allow",
    "windows-loaded-parallel",
    "windows-real-https",
    "windows-active-tabs-independent",
    "page-content-evidence",
    "cookie-session-semantics",
    "human-assist-noninterference",
]


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def parse_watcher(path):
    """Raw app-identity watcher evidence.

    The raw samples are mandatory: a file with only a `final max=0` line is not
    evidence (the Gate C false pass) and is reported as a contradiction. The
    maximum is the upper bound of the raw sample maximum and the final max=,
    and the two must agree.
    """
    samples = []
    final_max = None
    final_nonzero = None
    malformed = 0
    readable = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            readable = True
            for line in handle:
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
    except OSError:
        return {
            "readable": False,
            "samples": 0,
            "sample_max": None,
            "final_max": None,
            "max": None,
            "nonzero_samples": None,
            "sample_nonzero_samples": 0,
            "consistent": False,
            "malformed": 0,
            "first_nonzero_index": None,
        }
    sample_max = max(samples) if samples else None
    sample_nonzero = sum(1 for count in samples if count > 0)
    present = [value for value in (sample_max, final_max) if value is not None]
    max_seen = max(present) if present else None
    consistent = malformed == 0 and (
        sample_max is None or final_max is None or sample_max == final_max
    )
    if final_nonzero is not None and final_nonzero != sample_nonzero:
        consistent = False
    first_nonzero = next(
        (index for index, count in enumerate(samples) if count > 0), None
    )
    return {
        "readable": True,
        "samples": len(samples),
        "sample_max": sample_max,
        "final_max": final_max,
        "max": max_seen,
        "nonzero_samples": final_nonzero,
        "sample_nonzero_samples": sample_nonzero,
        "consistent": consistent,
        "malformed": malformed,
        "first_nonzero_index": first_nonzero,
    }


def parse_policy(path):
    entries = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "BROWSER_CLI_PRESENTATION" not in line:
                    continue
                fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
                if fields:
                    entries.append(fields)
    except OSError:
        return []
    return entries


def is_real_https(url):
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname)


def evaluate(evidence_dir):
    checks = []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    plan = load_json(os.path.join(evidence_dir, PLAN_FILE))
    windows = load_json(os.path.join(evidence_dir, WINDOWS_FILE)) or []
    page_content = load_json(os.path.join(evidence_dir, PAGE_CONTENT_FILE)) or {}
    cookies = load_json(os.path.join(evidence_dir, COOKIE_FILE)) or {}
    human_assist = load_json(os.path.join(evidence_dir, HUMAN_ASSIST_FILE)) or {}
    watcher = parse_watcher(os.path.join(evidence_dir, WATCHER_FILE))
    policy = parse_policy(os.path.join(evidence_dir, POLICY_FILE))

    plan_windows = []
    cookie_mode = ""
    if plan is None:
        add("session-contract", "NOT-EVALUATED", "%s missing" % PLAN_FILE)
        add("plan-window-matrix", "NOT-EVALUATED", "%s missing" % PLAN_FILE)
    else:
        session = plan.get("session", {})
        plan_windows = plan.get("windows", [])
        cookie_mode = plan.get("cookie_mode", "")
        if not session:
            add("session-contract", "NOT-EVALUATED", "no session block in the plan")
        else:
            missing = [
                key
                for key in ("signed_app", "profile_is_mock_keychain", "managed_background")
                if session.get(key) is not True
            ]
            if missing:
                add(
                    "session-contract",
                    "CONTRADICTION",
                    "session contract violated: %s not true" % ", ".join(missing),
                )
            else:
                add(
                    "session-contract",
                    "PASS",
                    "signed + mock-keychain + managed background",
                )

        names = [w.get("name") for w in plan_windows]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        problems = []
        if duplicates:
            problems.append("duplicate window name(s): %s" % ", ".join(duplicates))
        for required in REQUIRED_WINDOWS:
            matches = [w for w in plan_windows if w.get("name") == required["name"]]
            if not matches:
                problems.append("missing required window %s" % required["name"])
                continue
            window = matches[0]
            if window.get("optional"):
                problems.append("required window %s marked optional" % required["name"])
            if window.get("background") is not True:
                problems.append("window %s is not background" % required["name"])
        if len(plan_windows) < MIN_BACKGROUND_WINDOWS:
            problems.append(
                "planned background windows=%d (<%d)"
                % (len(plan_windows), MIN_BACKGROUND_WINDOWS)
            )
        for window in plan_windows:
            if window.get("optional") and window.get("name") not in (
                w["name"] for w in REQUIRED_WINDOWS
            ):
                problems.append(
                    "window %s is optional (only extra windows may be optional)"
                    % window.get("name")
                )
        if problems:
            add("plan-window-matrix", "CONTRADICTION", "; ".join(problems))
        else:
            add(
                "plan-window-matrix",
                "PASS",
                "fixed matrix satisfied: w1/w2/w3 background, >= %d windows"
                % MIN_BACKGROUND_WINDOWS,
            )

    # App-identity watcher: raw samples are mandatory; final-only files are not
    # evidence (Gate C lesson). The upper bound of sample max and final max must
    # agree and be 0.
    if not watcher["readable"]:
        add("watcher-no-visible-window", "NOT-EVALUATED", "no watcher samples file")
    elif watcher["samples"] == 0:
        add(
            "watcher-no-visible-window",
            "CONTRADICTION",
            "no raw watcher samples: a final-only line (final max=%s) is not "
            "evidence; the 10ms app-identity watcher must have sampled the whole "
            "session" % watcher["final_max"],
        )
    elif not watcher["consistent"]:
        add(
            "watcher-no-visible-window",
            "CONTRADICTION",
            "watcher evidence inconsistent: sample_max=%s final_max=%s "
            "malformed=%d nonzero(sample)=%s nonzero(final)=%s"
            % (
                watcher["sample_max"],
                watcher["final_max"],
                watcher["malformed"],
                watcher["sample_nonzero_samples"],
                watcher["nonzero_samples"],
            ),
        )
    elif watcher["max"] > 0:
        add(
            "watcher-no-visible-window",
            "CONTRADICTION",
            "on-screen window observed: max=%d (sample_max=%s final_max=%s) "
            "first_nonzero_index=%s"
            % (
                watcher["max"],
                watcher["sample_max"],
                watcher["final_max"],
                watcher["first_nonzero_index"],
            ),
        )
    else:
        add(
            "watcher-no-visible-window",
            "PASS",
            "sample_max=0 final_max=0 samples=%d nonzero_samples=%s"
            % (watcher["samples"], watcher["nonzero_samples"]),
        )

    if not policy:
        add("policy-no-presentation-allow", "NOT-EVALUATED", "no policy lines")
    else:
        allows = [
            e
            for e in policy
            if e.get("decision") == "allow" and e.get("action") in PRESENTATION_ACTIONS
        ]
        appkit_allows = [
            e
            for e in policy
            if e.get("stage") == "appkit" and e.get("decision") == "allow"
        ]
        denies = [
            e
            for e in policy
            if e.get("decision") == "deny" and e.get("action") == "show-and-activate"
        ]
        if allows or appkit_allows:
            add(
                "policy-no-presentation-allow",
                "CONTRADICTION",
                "presentation allow(s) found: policy=%d appkit=%d"
                % (len(allows), len(appkit_allows)),
            )
        elif not denies:
            add(
                "policy-no-presentation-allow",
                "NOT-EVALUATED",
                "no show-and-activate deny evidence",
            )
        else:
            add(
                "policy-no-presentation-allow",
                "PASS",
                "show-and-activate denies=%d, presentation allows=0" % len(denies),
            )

    by_name = {}
    for window in windows:
        by_name.setdefault(window.get("name"), []).append(window)
    required_names = [w["name"] for w in REQUIRED_WINDOWS]

    missing = [name for name in required_names if not by_name.get(name)]
    if missing:
        add(
            "windows-loaded-parallel",
            "NOT-EVALUATED",
            "no window evidence for: %s" % ", ".join(missing),
        )
    else:
        ids = [by_name[name][0].get("window_id") for name in required_names]
        problems = []
        if any(not value for value in ids):
            problems.append("missing window_id")
        if len(set(ids)) != len(ids):
            problems.append("duplicate window_id(s): %s" % ids)
        if any(by_name[name][0].get("loaded") is not True for name in required_names):
            problems.append("a required window is not loaded")
        if problems:
            add("windows-loaded-parallel", "CONTRADICTION", "; ".join(problems))
        else:
            add(
                "windows-loaded-parallel",
                "PASS",
                "%d background windows loaded with distinct window ids"
                % len(required_names),
            )

    if missing:
        add("windows-real-https", "NOT-EVALUATED", "no window evidence for: %s" % ", ".join(missing))
    else:
        urls = [by_name[name][0].get("url", "") for name in required_names]
        problems = [
            "window %s URL is not a real https URL: %s" % (name, url)
            for name, url in zip(required_names, urls)
            if not is_real_https(url)
        ]
        if len(set(urls)) != len(urls):
            problems.append("duplicate URL(s) across windows: %s" % urls)
        if problems:
            add("windows-real-https", "CONTRADICTION", "; ".join(problems))
        else:
            add(
                "windows-real-https",
                "PASS",
                "distinct https URLs: %s" % ", ".join(urls),
            )

    if missing:
        add(
            "windows-active-tabs-independent",
            "NOT-EVALUATED",
            "no window evidence for: %s" % ", ".join(missing),
        )
    else:
        tabs = [by_name[name][0].get("active_tab_id") for name in required_names]
        problems = []
        if any(not value for value in tabs):
            problems.append("missing active_tab_id")
        if len(set(tabs)) != len(tabs):
            problems.append("duplicate active_tab_id(s): %s" % tabs)
        if problems:
            add(
                "windows-active-tabs-independent",
                "CONTRADICTION",
                "; ".join(problems),
            )
        else:
            add(
                "windows-active-tabs-independent",
                "PASS",
                "distinct active tabs: %s" % tabs,
            )

    if missing:
        add("page-content-evidence", "NOT-EVALUATED", "no window evidence for: %s" % ", ".join(missing))
    else:
        incomplete = [
            name
            for name in required_names
            if not (
                (page_content.get(name) or {}).get("title")
                and (page_content.get(name) or {}).get("body")
            )
        ]
        if incomplete:
            add(
                "page-content-evidence",
                "NOT-EVALUATED",
                "missing title/body for: %s" % ", ".join(incomplete),
            )
        else:
            add(
                "page-content-evidence",
                "PASS",
                "title/body recorded for %d windows" % len(required_names),
            )

    if missing:
        add(
            "cookie-session-semantics",
            "NOT-EVALUATED",
            "no window evidence for: %s" % ", ".join(missing),
        )
    elif not cookies:
        add("cookie-session-semantics", "NOT-EVALUATED", "%s missing" % COOKIE_FILE)
    elif cookie_mode not in ("shared-profile", "isolated-profiles"):
        add(
            "cookie-session-semantics",
            "CONTRADICTION",
            "plan cookie_mode=%r is not shared-profile / isolated-profiles"
            % cookie_mode,
        )
    else:
        problems = []
        jars = []
        for name in required_names:
            entry = cookies.get(name) or {}
            if entry.get("readback_ok") is not True or not entry.get("readback_value"):
                problems.append("window %s has no cookie readback evidence" % name)
            jars.append(entry.get("jar_id"))
        if any(not jar for jar in jars):
            problems.append("missing cookie jar id")
        elif cookie_mode == "shared-profile" and len(set(jars)) != 1:
            problems.append("shared-profile mode but jar ids differ: %s" % jars)
        elif cookie_mode == "isolated-profiles" and len(set(jars)) != len(jars):
            problems.append("isolated-profiles mode but jar ids repeat: %s" % jars)
        if problems:
            add("cookie-session-semantics", "CONTRADICTION", "; ".join(problems))
        else:
            add(
                "cookie-session-semantics",
                "PASS",
                "%s: readback ok in %d windows, jars=%s"
                % (cookie_mode, len(required_names), sorted(set(jars))),
            )

    if missing:
        add(
            "human-assist-noninterference",
            "NOT-EVALUATED",
            "no window evidence for: %s" % ", ".join(missing),
        )
    elif not human_assist:
        add(
            "human-assist-noninterference",
            "NOT-EVALUATED",
            "%s missing" % HUMAN_ASSIST_FILE,
        )
    else:
        problems = []
        if not human_assist.get("human_or_assist_window_id"):
            problems.append("no HUMAN/ASSIST window recorded")
        per_window = human_assist.get("windows", {})
        for name in required_names:
            entry = per_window.get(name) or {}
            if entry.get("untouched") is not True:
                problems.append("window %s not proven untouched" % name)
            if entry.get("presentation_allows", 0):
                problems.append("window %s saw %s presentation allow(s)" % (
                    name, entry.get("presentation_allows")))
        if problems:
            add("human-assist-noninterference", "CONTRADICTION", "; ".join(problems))
        else:
            add(
                "human-assist-noninterference",
                "PASS",
                "HUMAN/ASSIST window=%s; %d background windows untouched"
                % (human_assist.get("human_or_assist_window_id"), len(required_names)),
            )

    # Screenshots stay a Gate C dependency (BLOCKED): never a pass, never a
    # blocker for this contract.
    add(
        "screenshots-gate-c-dependency",
        "NOT-EVALUATED",
        "per-window screenshots are a Gate C runtime item; the Gate C offline "
        "checker is BLOCKED (rework pending) and this contract does not claim "
        "screenshot coverage",
    )

    required_checks = list(REQUIRED_GLOBAL_CHECKS)
    status_by_name = {check["check"]: check["status"] for check in checks}
    not_passed = [name for name in required_checks if status_by_name.get(name) != "PASS"]
    contradictions = [check for check in checks if check["status"] == "CONTRADICTION"]
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "evidence_dir": evidence_dir,
        "plan": plan,
        "plan_windows": plan_windows,
        "required_window_matrix": required_names,
        "windows": windows,
        "watcher": watcher,
        "policy_entries": len(policy),
        "checks": checks,
        "required_checks": required_checks,
        "not_passed_required_checks": not_passed,
        "evidence_verdict": "CONTRADICTION"
        if contradictions
        else ("INSUFFICIENT-EVIDENCE" if not_passed else "CONTRACT-SATISFIED"),
        "contradictions": contradictions,
    }


def evidence_exit_code(report):
    """0 only when every required check PASSed; 1 for contradictions; 3 for
    insufficient evidence."""
    if report["contradictions"]:
        return 1
    if report["not_passed_required_checks"]:
        return 3
    return 0


def render(report):
    lines = ["# Gate F parallel-windows evidence report", ""]
    lines.append("Evidence dir: `%s`" % report["evidence_dir"])
    lines.append("")
    lines.append("## Verdicts")
    for check in report["checks"]:
        lines.append(
            "- **%s**: %s (%s)"
            % (check["status"], check["check"], check["detail"])
        )
    lines.append("")
    lines.append(
        "Evidence verdict: **%s** (rc=0 only when every required check is PASS; "
        "insufficient evidence = rc 3; contradiction = rc 1; screenshots are a "
        "Gate C dependency and are never claimed here)"
        % report["evidence_verdict"]
    )
    if report["not_passed_required_checks"]:
        lines.append(
            "Not-PASS required checks: %s"
            % ", ".join(report["not_passed_required_checks"])
        )
    lines.append("")
    watcher = report["watcher"]
    lines.append("## Watcher (app identity, 10ms)")
    lines.append(
        "- samples=%s sample_max=%s final_max=%s max=%s consistent=%s"
        % (
            watcher["samples"],
            watcher["sample_max"],
            watcher["final_max"],
            watcher["max"],
            watcher["consistent"],
        )
    )
    lines.append("")
    if report["contradictions"]:
        lines.append("## CONTRADICTIONS (fail-closed)")
        for item in report["contradictions"]:
            lines.append("- %s" % json.dumps(item, ensure_ascii=False))
    else:
        lines.append("## No contradictions found")
        lines.append("")
        lines.append(
            "Gate F is still NOT passed: this report only says the evidence does "
            "not contradict the contract (see NOT-EVALUATED items, including the "
            "Gate C screenshot dependency)."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Fixture self-test (no browser, no window, no keychain)
# --------------------------------------------------------------------------

def _default_windows():
    return [
        {
            "name": "w1",
            "window_id": 101,
            "url": "https://example.com/",
            "active_tab_id": 1001,
            "loaded": True,
        },
        {
            "name": "w2",
            "window_id": 102,
            "url": "https://example.org/",
            "active_tab_id": 1002,
            "loaded": True,
        },
        {
            "name": "w3",
            "window_id": 103,
            "url": "https://example.net/",
            "active_tab_id": 1003,
            "loaded": True,
        },
    ]


def build_fixture(root, *, session=(True, True, True), windows=None,
                  watcher_samples=(0, 0), watcher_final=0,
                  watcher_write_samples=True, policy=True, page=True,
                  cookies=True, human_assist=True, mode="shared-profile",
                  cookie_jars=None):
    os.makedirs(root, exist_ok=True)
    windows = windows if windows is not None else _default_windows()
    plan = {
        "session": {
            "signed_app": session[0],
            "profile_is_mock_keychain": session[1],
            "managed_background": session[2],
        },
        "cookie_mode": mode,
        "windows": [
            {"name": w["name"], "background": True, "optional": False} for w in windows
        ],
    }
    with open(os.path.join(root, PLAN_FILE), "w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2)
    with open(os.path.join(root, WINDOWS_FILE), "w", encoding="utf-8") as handle:
        json.dump(windows, handle, indent=2)
    if page:
        with open(os.path.join(root, PAGE_CONTENT_FILE), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    w["name"]: {
                        "title": "BCLI_PAGE_%s" % w["name"],
                        "body": "BCLI_BODY_%s" % w["name"],
                        "url": w["url"],
                    }
                    for w in windows
                },
                handle,
                indent=2,
            )
    if cookies:
        jars = cookie_jars or {w["name"]: "jar-a" for w in windows}
        with open(os.path.join(root, COOKIE_FILE), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    w["name"]: {
                        "jar_id": jars.get(w["name"], "jar-a"),
                        "readback_value": "bc_cookie_%s" % w["name"],
                        "readback_ok": True,
                    }
                    for w in windows
                },
                handle,
                indent=2,
            )
    if human_assist:
        with open(os.path.join(root, HUMAN_ASSIST_FILE), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "human_or_assist_window_id": 9001,
                    "windows": {
                        w["name"]: {"untouched": True, "presentation_allows": 0}
                        for w in windows
                    },
                },
                handle,
                indent=2,
            )
    if watcher_write_samples:
        with open(os.path.join(root, WATCHER_FILE), "w", encoding="utf-8") as handle:
            for count in watcher_samples:
                handle.write("sample ts=1 count=%d\n" % count)
            handle.write(
                "final max=%d nonzero_samples=%d\n"
                % (watcher_final, sum(1 for c in watcher_samples if c > 0))
            )
    else:
        with open(os.path.join(root, WATCHER_FILE), "w", encoding="utf-8") as handle:
            handle.write("final max=%d nonzero_samples=0\n" % watcher_final)
    if policy:
        with open(os.path.join(root, POLICY_FILE), "w", encoding="utf-8") as handle:
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


def _status(report, name):
    for check in report["checks"]:
        if check["check"] == name:
            return check["status"]
    return None


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

    tmp = tempfile.mkdtemp(prefix="parallel-evidence-selftest.")
    try:
        def fixture(name, **kwargs):
            root = os.path.join(tmp, name)
            os.makedirs(root, exist_ok=True)
            build_fixture(root, **kwargs)
            return root

        # 1. Good fixture: no contradictions, rc 0, screenshots NOT-EVALUATED.
        report = evaluate(fixture("good"))
        good_report = report
        check("good fixture has no contradictions", not report["contradictions"])
        check(
            "good fixture exits 0 (all required checks PASS)",
            evidence_exit_code(report) == 0,
            "rc=%s" % evidence_exit_code(report),
        )
        check(
            "screenshots stay a Gate C dependency (NOT-EVALUATED, never PASS)",
            _status(report, "screenshots-gate-c-dependency") == "NOT-EVALUATED",
        )
        check(
            "fixed window matrix PASS",
            _status(report, "plan-window-matrix") == "PASS",
        )

        # 2. Only two windows -> contradiction.
        two = _default_windows()[:2]
        report = evaluate(fixture("two-windows", windows=two))
        check(
            "fewer than three background windows is a CONTRADICTION",
            _status(report, "plan-window-matrix") == "CONTRADICTION"
            and evidence_exit_code(report) != 0,
        )

        # 3. Required window marked optional -> contradiction.
        root = fixture("window-optional")
        with open(os.path.join(root, PLAN_FILE), encoding="utf-8") as handle:
            plan = json.load(handle)
        plan["windows"][1]["optional"] = True
        with open(os.path.join(root, PLAN_FILE), "w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2)
        report = evaluate(root)
        check(
            "marking w2 optional is a CONTRADICTION (fixed matrix)",
            _status(report, "plan-window-matrix") == "CONTRADICTION",
        )

        # 4. Duplicate window names -> contradiction.
        duplicate = _default_windows()
        duplicate[1]["name"] = "w1"
        report = evaluate(fixture("duplicate-names", windows=duplicate))
        check(
            "duplicate window names is a CONTRADICTION",
            _status(report, "plan-window-matrix") == "CONTRADICTION",
        )

        # 5. Duplicate URL across windows -> contradiction.
        same_url = _default_windows()
        same_url[1]["url"] = same_url[0]["url"]
        report = evaluate(fixture("duplicate-url", windows=same_url))
        check(
            "duplicate URL across windows is a CONTRADICTION",
            _status(report, "windows-real-https") == "CONTRADICTION",
        )

        # 6. Non-https URL -> contradiction.
        http_url = _default_windows()
        http_url[2]["url"] = "http://example.net/"
        report = evaluate(fixture("non-https", windows=http_url))
        check(
            "non-https URL is a CONTRADICTION",
            _status(report, "windows-real-https") == "CONTRADICTION",
        )

        # 7. Duplicate active tab id -> contradiction.
        same_tab = _default_windows()
        same_tab[1]["active_tab_id"] = same_tab[0]["active_tab_id"]
        report = evaluate(fixture("duplicate-tab", windows=same_tab))
        check(
            "duplicate active tab id is a CONTRADICTION",
            _status(report, "windows-active-tabs-independent") == "CONTRADICTION",
        )

        # 8. Watcher final-only (no raw samples) -> contradiction (Gate C lesson).
        report = evaluate(
            fixture("watcher-final-only", watcher_write_samples=False, watcher_final=0)
        )
        check(
            "final-only watcher evidence (no raw samples) is a CONTRADICTION",
            _status(report, "watcher-no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) != 0,
        )

        # 9. Watcher sample>0 -> contradiction.
        report = evaluate(
            fixture("watcher-visible", watcher_samples=(0, 1), watcher_final=1)
        )
        check(
            "watcher sample count>0 is a CONTRADICTION",
            _status(report, "watcher-no-visible-window") == "CONTRADICTION",
        )

        # 10. Missing cookie evidence -> NOT-EVALUATED, rc 3.
        report = evaluate(fixture("no-cookies", cookies=False))
        check(
            "missing cookie evidence is NOT-EVALUATED (rc 3)",
            _status(report, "cookie-session-semantics") == "NOT-EVALUATED"
            and evidence_exit_code(report) == 3,
        )

        # 11. isolated-profiles with shared jar ids -> contradiction.
        report = evaluate(
            fixture("isolated-shared-jar", mode="isolated-profiles")
        )
        check(
            "isolated-profiles mode with shared jar ids is a CONTRADICTION",
            _status(report, "cookie-session-semantics") == "CONTRADICTION",
        )

        # 12. Missing human-assist evidence -> NOT-EVALUATED, rc 3.
        report = evaluate(fixture("no-human-assist", human_assist=False))
        check(
            "missing HUMAN/ASSIST evidence is NOT-EVALUATED (rc 3)",
            _status(report, "human-assist-noninterference") == "NOT-EVALUATED"
            and evidence_exit_code(report) == 3,
        )

        # 13. Empty evidence dir -> rc 3, never 0.
        empty_dir = os.path.join(tmp, "empty-evidence")
        os.makedirs(empty_dir)
        empty_report = evaluate(empty_dir)
        check(
            "empty evidence dir exits 3 (INSUFFICIENT-EVIDENCE)",
            evidence_exit_code(empty_report) == 3,
            "rc=%s" % evidence_exit_code(empty_report),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("SELFTEST SUMMARY: %d passed, %d failed" % (len(passed), len(failures)))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        description="Offline verdict engine for Gate F parallel-window evidence."
    )
    parser.add_argument("--evidence", help="evidence dir to evaluate")
    parser.add_argument("--report-dir", default="", help="where to write the report")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--write-fixture",
        default="",
        help="write a complete good fixture dir (fixture/test helper only)",
    )
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if args.write_fixture:
        build_fixture(args.write_fixture)
        print("PARALLEL-EVIDENCE-CHECK: fixture written to %s" % args.write_fixture)
        return 0
    if not args.evidence:
        parser.error("--evidence is required (or use --self-test)")
    if not os.path.isdir(args.evidence):
        print(
            "parallel-evidence-check: no such dir: %s" % args.evidence,
            file=sys.stderr,
        )
        return 2

    report = evaluate(args.evidence)
    report_dir = args.report_dir or args.evidence
    os.makedirs(report_dir, exist_ok=True)
    with open(
        os.path.join(report_dir, "parallel-report.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    with open(
        os.path.join(report_dir, "parallel-report.md"), "w", encoding="utf-8"
    ) as handle:
        handle.write(render(report) + "\n")
    print("PARALLEL-EVIDENCE-CHECK: report written to %s" % report_dir)
    rc = evidence_exit_code(report)
    if rc == 1:
        print(
            "PARALLEL-EVIDENCE-CHECK: CONTRADICTIONS=%d (Gate F NOT passed)"
            % len(report["contradictions"])
        )
    elif rc == 3:
        print(
            "PARALLEL-EVIDENCE-CHECK: INSUFFICIENT-EVIDENCE (required checks not "
            "PASS: %s); Gate F NOT passed"
            % ", ".join(report["not_passed_required_checks"])
        )
    else:
        print(
            "PARALLEL-EVIDENCE-CHECK: contract satisfied by the evidence "
            "(Gate F still NOT passed by this tool; screenshots remain a Gate C "
            "dependency)"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
