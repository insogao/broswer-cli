#!/usr/bin/env python3
"""Offline verdict engine for the Gate C (BACKGROUND_CAPTURE) evidence contract.

It never launches, shows, activates or captures anything. The evidence input is
read-only; the only files this tool writes are capture-report.json/md in the
report dir. It parses evidence produced by a (future) execution slice and
applies fail-closed verdicts. Fake passes it rejects:

  * any on-screen window during the session (app-identity watcher max > 0);
  * empty / blank / suspiciously small PNG captures;
  * capture integrity failures (sha256 / size mismatch against the manifest);
  * the same pixels across different cases, or a dynamic / canvas / long-scroll
    case whose captures are all identical;
  * a static case whose captures differ;
  * any presentation allow (show-and-activate / show-inactive / minimize) or an
    AppKit-level allow in the policy log;
  * a session contract that is not signed + mock-keychain + managed-background.

Video is optional in this slice: without deterministic local material it is
NOT-EVALUATED, never a fake PASS. Missing evidence is NOT-EVALUATED, never a
PASS, and a session whose required checks are not all PASS never exits 0.
Gate C itself is NOT passed by this tool.

Usage:
  capture-evidence-check.py --evidence DIR [--report-dir DIR]
  capture-evidence-check.py --self-test
  capture-evidence-check.py --write-fixture DIR   (fixture/test helper only)
Exit: 0 = every required check PASS (the optional video case may be
      NOT-EVALUATED),
      1 = contradiction(s) found,
      2 = usage error,
      3 = INSUFFICIENT-EVIDENCE (required check(s) NOT-EVALUATED or missing).
"""

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import time
import zlib

PRESENTATION_ACTIONS = {"show-and-activate", "show-inactive", "minimize"}
CHANGE_EXPECTED_KINDS = {"dynamic", "canvas", "long-scroll", "video"}

# Planned minimum captures per case kind. A single capture can never satisfy a
# case that must show change (and static needs its repeat capture too).
MIN_CAPTURES = {
    "static": 2,
    "dynamic": 2,
    "canvas": 2,
    "long-scroll": 3,
    "video": 2,
}

REQUIRED_GLOBAL_CHECKS = [
    "session-contract",
    "no-visible-window",
    "policy-no-presentation-allow",
    "plan-case-matrix",
    "capture-evidence-present",
    "capture-cross-case-uniqueness",
    "page-content-evidence",
]

# The required case matrix lives HERE, in code, and is never derived from the
# evidence's capture-plan.json: deleting a case, renaming it or marking it
# optional cannot make the session pass. Only `video` may be optional.
REQUIRED_CASE_MATRIX = [
    {"name": "static", "kind": "static", "optional": False},
    {"name": "dynamic", "kind": "dynamic", "optional": False},
    {"name": "canvas", "kind": "canvas", "optional": False},
    {"name": "long", "kind": "long-scroll", "optional": False},
]

PLAN_FILE = "capture-plan.json"
CAPTURES_FILE = "captures.json"
WATCHER_FILE = "watcher-samples.txt"
POLICY_FILE = "browser.log"
PAGE_CONTENT_FILE = "page-content.json"


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def sha256_file(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def read_png(path):
    """Header + payload level PNG validation (no full image decode).

    Returns a dict with ok/width/height/size/blank/detail. `blank` is a
    conservative heuristic: after zlib-decompressing the IDAT payload, at most
    two distinct byte values with one value covering >95% of the bytes means a
    uniform (blank) image. A blank capture is never acceptable evidence.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return {"ok": False, "detail": "unreadable: %s" % exc}
    size = len(data)
    if size < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return {"ok": False, "size": size, "detail": "not a PNG (magic)"}
    pos = 8
    width = height = None
    idat = b""
    while pos + 8 <= size:
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        if len(chunk) != length:
            return {"ok": False, "size": size, "detail": "truncated chunk"}
        if tag == b"IHDR" and len(chunk) >= 8:
            width, height = struct.unpack(">II", chunk[:8])
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
        pos += 12 + length
    if not width or not height:
        return {"ok": False, "size": size, "detail": "missing/zero IHDR size"}
    if not idat:
        return {"ok": False, "size": size, "detail": "no IDAT payload"}
    try:
        raw = zlib.decompress(idat)
    except zlib.error as exc:
        return {"ok": False, "size": size, "detail": "bad IDAT: %s" % exc}
    if not raw:
        return {"ok": False, "size": size, "detail": "empty IDAT payload"}
    counts = {}
    for byte in raw:
        counts[byte] = counts.get(byte, 0) + 1
    blank = len(counts) <= 2 and (max(counts.values()) / len(raw)) > 0.95
    return {
        "ok": True,
        "width": width,
        "height": height,
        "size": size,
        "blank": blank,
        "detail": "%dx%d, %d bytes%s" % (width, height, size, " BLANK" if blank else ""),
    }


def parse_watcher(path):
    """Parses the app-identity watcher evidence.

    The raw samples are authoritative: the reported maximum is the upper bound
    of the per-sample counts and the `final max=` line. If the two disagree (for
    example a `count=1` sample hidden behind `final max=0`), the evidence is
    inconsistent and must never PASS. Returns a dict with readable/samples/
    sample_max/final_max/max/nonzero_samples/consistent/malformed/
    first_nonzero_index so the raw evidence survives into the report.
    """
    samples = []
    final_max = None
    final_nonzero = None
    malformed = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
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


def evaluate(evidence_dir):
    checks = []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    plan = load_json(os.path.join(evidence_dir, PLAN_FILE))
    captures = load_json(os.path.join(evidence_dir, CAPTURES_FILE)) or []
    page_content = load_json(os.path.join(evidence_dir, PAGE_CONTENT_FILE)) or {}
    watcher = parse_watcher(os.path.join(evidence_dir, WATCHER_FILE))
    policy = parse_policy(os.path.join(evidence_dir, POLICY_FILE))

    # Session contract: the evidence must come from the required execution
    # hygiene (signed app, mock keychain, managed background presentation).
    plan_cases = []
    if plan is None:
        add("session-contract", "NOT-EVALUATED", "%s missing" % PLAN_FILE)
    else:
        session = plan.get("session", {})
        plan_cases = plan.get("cases", [])
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

    # Fixed required-case matrix, defined in code and never derived from the
    # evidence: deleting a case, changing its kind or marking it optional is a
    # contradiction. Only the video case may be optional.
    if plan is None:
        add("plan-case-matrix", "NOT-EVALUATED", "%s missing" % PLAN_FILE)
    else:
        names = [c.get("name") for c in plan_cases]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        problems = []
        if duplicates:
            problems.append("duplicate case name(s): %s" % ", ".join(duplicates))
        for required in REQUIRED_CASE_MATRIX:
            matches = [c for c in plan_cases if c.get("name") == required["name"]]
            if not matches:
                problems.append("missing required case %s" % required["name"])
                continue
            case = matches[0]
            if case.get("optional"):
                problems.append("required case %s marked optional" % required["name"])
            if case.get("kind") != required["kind"]:
                problems.append(
                    "case %s kind=%s (expected %s)"
                    % (required["name"], case.get("kind"), required["kind"])
                )
        for case in plan_cases:
            if case.get("optional") and case.get("name") != "video":
                problems.append(
                    "case %s is optional (only video may be optional)"
                    % case.get("name")
                )
        if problems:
            add("plan-case-matrix", "CONTRADICTION", "; ".join(problems))
        else:
            add(
                "plan-case-matrix",
                "PASS",
                "fixed matrix satisfied: static/dynamic/canvas/long-scroll",
            )

    # Effective case list: the four matrix cases always come from code; extra
    # declared cases (video and anything else) are validated as declared.
    required_names = {case["name"] for case in REQUIRED_CASE_MATRIX}
    cases = [
        {
            "name": case["name"],
            "kind": case["kind"],
            "optional": False,
            "required": True,
        }
        for case in REQUIRED_CASE_MATRIX
    ]
    for case in plan_cases:
        if case.get("name") in required_names:
            continue
        cases.append(
            {
                "name": case.get("name"),
                "kind": case.get("kind", "static"),
                "optional": bool(case.get("optional")),
                "required": False,
            }
        )

    # Gate C invariant: no on-screen window at any time. The raw sample maximum
    # and the final max= line must agree; the upper bound of both is
    # authoritative, so a final max=0 can never mask a count>0 sample.
    if not watcher["readable"] or (
        watcher["samples"] == 0 and watcher["final_max"] is None
    ):
        add("no-visible-window", "NOT-EVALUATED", "no watcher samples/final line")
    elif not watcher["consistent"]:
        add(
            "no-visible-window",
            "CONTRADICTION",
            "watcher evidence inconsistent: sample_max=%s final_max=%s "
            "malformed=%d nonzero(sample)=%s nonzero(final)=%s; the raw samples "
            "are authoritative and a final max=0 must not mask an on-screen window"
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
            "no-visible-window",
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
            "no-visible-window",
            "PASS",
            "sample_max=0 final_max=0 samples=%d nonzero_samples=%s"
            % (watcher["samples"], watcher["nonzero_samples"]),
        )

    # No presentation allow anywhere: screenshots must never be bought with a
    # show/activate/reveal/minimize compensation.
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
                "show-and-activate denies=%d, presentation allows=0"
                % len(denies),
            )

    entries_by_case = {}
    for entry in captures:
        entries_by_case.setdefault(entry.get("case"), []).append(entry)

    non_optional = [c for c in cases if not c.get("optional")]
    missing_cases = [c.get("name") for c in non_optional if not entries_by_case.get(c.get("name"))]
    if plan is None:
        add("capture-evidence-present", "NOT-EVALUATED", "%s missing" % PLAN_FILE)
    elif missing_cases:
        add(
            "capture-evidence-present",
            "NOT-EVALUATED",
            "no captures for required case(s): %s" % ", ".join(missing_cases),
        )
    else:
        add(
            "capture-evidence-present",
            "PASS",
            "%d required case(s) have captures" % len(non_optional),
        )

    case_hashes = {}
    for case in cases:
        name = case.get("name")
        kind = case.get("kind", "static")
        optional = bool(case.get("optional"))
        entries = entries_by_case.get(name, [])
        if not entries:
            add(
                "capture-%s" % name,
                "NOT-EVALUATED",
                "optional case has no captures (deterministic local material not "
                "built: video is NOT-EVALUATED, never a fake PASS)"
                if optional
                else "no captures for this case",
            )
            continue
        hashes = []
        problems = []
        for entry in entries:
            path = entry.get("file", "")
            actual = sha256_file(path)
            if actual is None:
                problems.append("capture file unreadable: %s" % path)
                continue
            expected_hash = entry.get("sha256")
            expected_size = entry.get("size")
            try:
                actual_size = os.path.getsize(path)
            except OSError:
                actual_size = -1
            if expected_hash != actual:
                problems.append("sha256 mismatch for %s" % os.path.basename(path))
            if expected_size != actual_size:
                problems.append("size mismatch for %s" % os.path.basename(path))
            png = read_png(path)
            if not png.get("ok"):
                problems.append(
                    "%s invalid PNG: %s" % (os.path.basename(path), png.get("detail"))
                )
            elif png.get("blank"):
                problems.append("%s is blank/uniform" % os.path.basename(path))
            elif png.get("size", 0) < 512:
                problems.append(
                    "%s suspiciously small (%d bytes)"
                    % (os.path.basename(path), png.get("size", 0))
                )
            hashes.append(actual)
        case_hashes[name] = hashes
        if problems:
            add("capture-%s-integrity" % name, "CONTRADICTION", "; ".join(problems))
            continue
        add(
            "capture-%s-integrity" % name,
            "PASS",
            "%d capture(s) non-empty, non-blank, hash/size verified" % len(entries),
        )
        minimum = MIN_CAPTURES.get(kind)
        if minimum is None:
            add(
                "capture-%s-uniqueness" % name,
                "NOT-EVALUATED",
                "unknown case kind: %s" % kind,
            )
            continue
        if len(entries) < minimum:
            add(
                "capture-%s-min-count" % name,
                "CONTRADICTION",
                "planned minimum is %d capture(s), found %d"
                % (minimum, len(entries)),
            )
            continue
        add(
            "capture-%s-min-count" % name,
            "PASS",
            "%d capture(s) >= planned minimum %d" % (len(entries), minimum),
        )
        distinct = len(set(hashes))
        if kind == "static":
            if distinct != 1:
                add(
                    "capture-%s-uniqueness" % name,
                    "CONTRADICTION",
                    "static case captures differ (%d distinct hashes)" % distinct,
                )
            else:
                add(
                    "capture-%s-uniqueness" % name,
                    "PASS",
                    "static captures are identical as expected",
                )
        else:
            if distinct <= 1:
                add(
                    "capture-%s-uniqueness" % name,
                    "CONTRADICTION",
                    "all %s captures are identical: no change captured" % kind,
                )
            else:
                add(
                    "capture-%s-uniqueness" % name,
                    "PASS",
                    "%d distinct hash(es) across %d capture(s)"
                    % (distinct, len(hashes)),
                )

    representative = {
        name: hashes[0] for name, hashes in case_hashes.items() if hashes
    }
    if len(representative) < 2:
        add(
            "capture-cross-case-uniqueness",
            "NOT-EVALUATED",
            "fewer than two cases with captures",
        )
    else:
        seen = {}
        duplicates = []
        for name, digest in representative.items():
            if digest in seen:
                duplicates.append("%s == %s" % (seen[digest], name))
            else:
                seen[digest] = name
        if duplicates:
            add(
                "capture-cross-case-uniqueness",
                "CONTRADICTION",
                "different cases produced identical pixels: %s"
                % "; ".join(duplicates),
            )
        else:
            add(
                "capture-cross-case-uniqueness",
                "PASS",
                "%d cases with pairwise distinct pixels" % len(representative),
            )

    if not page_content:
        add("page-content-evidence", "NOT-EVALUATED", "%s missing" % PAGE_CONTENT_FILE)
    else:
        missing = [
            c.get("name")
            for c in non_optional
            if not (
                (page_content.get(c.get("name")) or {}).get("title")
                and (page_content.get(c.get("name")) or {}).get("body")
            )
        ]
        if missing:
            add(
                "page-content-evidence",
                "NOT-EVALUATED",
                "missing title/body evidence for: %s" % ", ".join(missing),
            )
        else:
            add(
                "page-content-evidence",
                "PASS",
                "title/body recorded for %d required case(s)" % len(non_optional),
            )

    required_checks = list(REQUIRED_GLOBAL_CHECKS)
    for case in cases:
        name = case.get("name")
        if case.get("optional") and not entries_by_case.get(name):
            # The optional video case may stay NOT-EVALUATED; it never blocks a
            # contract-satisfied exit. If it has captures they must pass.
            continue
        required_checks += [
            "capture-%s-min-count" % name,
            "capture-%s-integrity" % name,
            "capture-%s-uniqueness" % name,
        ]
    status_by_name = {c["check"]: c["status"] for c in checks}
    not_passed = [n for n in required_checks if status_by_name.get(n) != "PASS"]

    contradictions = [c for c in checks if c["status"] == "CONTRADICTION"]
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "evidence_dir": evidence_dir,
        "plan": plan,
        "cases": cases,
        "watcher": watcher,
        "policy": {
            "entries": len(policy),
            "presentation_allows": len(
                [
                    e
                    for e in policy
                    if e.get("decision") == "allow"
                    and e.get("action") in PRESENTATION_ACTIONS
                ]
            ),
        },
        "captures": captures,
        "checks": checks,
        "plan_cases": plan_cases,
        "required_case_matrix": [c["name"] for c in REQUIRED_CASE_MATRIX],
        "required_checks": required_checks,
        "not_passed_required_checks": not_passed,
        "evidence_verdict": "CONTRADICTION"
        if contradictions
        else ("INSUFFICIENT-EVIDENCE" if not_passed else "CONTRACT-SATISFIED"),
        "contradictions": contradictions,
    }


def evidence_exit_code(report):
    """0 only when every required check PASSed (the optional video case may be
    NOT-EVALUATED); 1 for contradictions; 3 for insufficient evidence."""
    if report["contradictions"]:
        return 1
    if report["not_passed_required_checks"]:
        return 3
    return 0


def render(report):
    lines = ["# Gate C capture evidence report", ""]
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
        "the optional video case may be NOT-EVALUATED; insufficient evidence = "
        "rc 3; contradiction = rc 1)" % report["evidence_verdict"]
    )
    if report["not_passed_required_checks"]:
        lines.append(
            "Not-PASS required checks: %s"
            % ", ".join(report["not_passed_required_checks"])
        )
    lines.append("")
    lines.append("## Watcher (app identity, 10ms)")
    watcher = report["watcher"]
    lines.append(
        "- samples=%s sample_max=%s final_max=%s max=%s consistent=%s "
        "malformed=%s nonzero(sample)=%s nonzero(final)=%s first_nonzero_index=%s"
        % (
            watcher["samples"],
            watcher["sample_max"],
            watcher["final_max"],
            watcher["max"],
            watcher["consistent"],
            watcher["malformed"],
            watcher["sample_nonzero_samples"],
            watcher["nonzero_samples"],
            watcher["first_nonzero_index"],
        )
    )
    lines.append("")
    lines.append("## Captures")
    if report["captures"]:
        for entry in report["captures"]:
            lines.append(
                "- case=%s kind=%s file=%s sha256=%s"
                % (
                    entry.get("case"),
                    entry.get("kind"),
                    entry.get("file"),
                    str(entry.get("sha256"))[:16],
                )
            )
    else:
        lines.append("- NOT-EVALUATED: no captures recorded")
    lines.append("")
    if report["contradictions"]:
        lines.append("## CONTRADICTIONS (fail-closed)")
        for item in report["contradictions"]:
            lines.append("- %s" % json.dumps(item, ensure_ascii=False))
    else:
        lines.append("## No contradictions found")
        lines.append("")
        lines.append(
            "Gate C is still NOT passed: this report only says the evidence does "
            "not contradict the contract (see NOT-EVALUATED items)."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Fixture self-test (no browser, no window, no keychain)
# --------------------------------------------------------------------------

def _png_bytes(pixels):
    height = len(pixels)
    width = len(pixels[0]) // 3
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _pixels(seed, width=24, height=24):
    """Incompressible 24x24 pixels so the fixture PNG stays above the
    suspicious-size threshold; the same seed yields byte-identical captures."""
    import random

    rng = random.Random(seed)
    return [list(rng.randbytes(width * 3)) for _ in range(height)]


def _blank_pixels(width=24, height=24):
    return [[255, 255, 255] * width for _ in range(height)]


def build_fixture(root, session=(True, True, True), cases=None, capture_plan=None,
                  watcher=(0, 0), policy=True, page_content=True):
    """Writes a synthetic evidence dir. `capture_plan` maps case name -> list of
    pixel grids (or the string "blank" / "empty")."""
    os.makedirs(os.path.join(root, "captures"), exist_ok=True)
    if cases is None:
        cases = [
            {"name": "static", "kind": "static", "optional": False},
            {"name": "dynamic", "kind": "dynamic", "optional": False},
            {"name": "canvas", "kind": "canvas", "optional": False},
            {"name": "long", "kind": "long-scroll", "optional": False},
            {
                "name": "video",
                "kind": "video",
                "optional": True,
                "note": "deterministic local material not built -> NOT-EVALUATED",
            },
        ]
    if capture_plan is None:
        capture_plan = {
            "static": [_pixels(1), _pixels(1)],
            "dynamic": [_pixels(2), _pixels(3)],
            "canvas": [_pixels(4), _pixels(5)],
            "long": [_pixels(6), _pixels(7), _pixels(8)],
        }
    plan = {
        "session": {
            "signed_app": session[0],
            "profile_is_mock_keychain": session[1],
            "managed_background": session[2],
        },
        "cases": cases,
    }
    with open(os.path.join(root, PLAN_FILE), "w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2)
    manifest = []
    for case_name, grids in capture_plan.items():
        for index, grid in enumerate(grids):
            filename = "%s-%d.png" % (case_name, index)
            path = os.path.join(root, "captures", filename)
            if grid == "empty":
                data = b""
            elif grid == "blank":
                data = _png_bytes(_blank_pixels())
            else:
                data = _png_bytes(grid)
            with open(path, "wb") as handle:
                handle.write(data)
            manifest.append(
                {
                    "case": case_name,
                    "kind": next(
                        (c.get("kind") for c in cases if c.get("name") == case_name),
                        "static",
                    ),
                    "file": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "captured_at": 1700000000000 + index,
                    "scroll_index": index,
                }
            )
    with open(os.path.join(root, CAPTURES_FILE), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    if watcher is not None:
        with open(os.path.join(root, WATCHER_FILE), "w", encoding="utf-8") as handle:
            handle.write("sample ts=1 count=0\n")
            handle.write("sample ts=2 count=%d\n" % watcher[0])
            handle.write(
                "final max=%d nonzero_samples=%d\n"
                % (watcher[1], 1 if watcher[0] else 0)
            )
    if policy:
        with open(os.path.join(root, POLICY_FILE), "w", encoding="utf-8") as handle:
            handle.write(
                "BROWSER_CLI_PRESENTATION stage=policy transition=background "
                "reason=command-line-switch window_id=0\n"
            )
            handle.write(
                "BROWSER_CLI_PRESENTATION stage=policy seq=1 decision=deny "
                "action=show-and-activate origin=internal window_id=1 "
                "state=background owned=no owned_windows=0 "
                "reason=background-suppresses-native-presentation\n"
            )
    if page_content:
        with open(os.path.join(root, PAGE_CONTENT_FILE), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    case.get("name"): {
                        "title": "BCLI_PAGE_%s" % case.get("name"),
                        "body": "BCLI_BODY_%s" % case.get("name"),
                    }
                    for case in cases
                    if not case.get("optional")
                },
                handle,
                indent=2,
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

    tmp = tempfile.mkdtemp(prefix="capture-evidence-selftest.")
    try:
        def fixture(name, **kwargs):
            root = os.path.join(tmp, name)
            os.makedirs(root, exist_ok=True)
            build_fixture(root, **kwargs)
            return root

        # 1. Good fixture: no contradictions, video NOT-EVALUATED.
        report = evaluate(fixture("good"))
        good_report = report
        check("good fixture has no contradictions", not report["contradictions"])
        check(
            "good fixture: video case is NOT-EVALUATED (never a fake pass)",
            _status(report, "capture-video") == "NOT-EVALUATED",
            _status(report, "capture-video"),
        )
        check("good fixture: no-visible-window PASS", _status(report, "no-visible-window") == "PASS")
        check(
            "good fixture: cross-case pixels distinct",
            _status(report, "capture-cross-case-uniqueness") == "PASS",
        )
        check(
            "good fixture: static captures identical",
            _status(report, "capture-static-uniqueness") == "PASS",
        )

        # 2. Window shown -> contradiction.
        report = evaluate(fixture("visible", watcher=(1, 1)))
        check(
            "visible window is a CONTRADICTION",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and report["contradictions"],
        )

        # 3. Blank capture -> contradiction.
        report = evaluate(
            fixture(
                "blank",
                capture_plan={
                    "static": ["blank", "blank"],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "blank/uniform capture is a CONTRADICTION",
            _status(report, "capture-static-integrity") == "CONTRADICTION",
            _status(report, "capture-static-integrity"),
        )

        # 4. Empty capture file -> contradiction.
        report = evaluate(
            fixture(
                "empty",
                capture_plan={
                    "static": ["empty", _pixels(1)],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "empty capture file is a CONTRADICTION",
            _status(report, "capture-static-integrity") == "CONTRADICTION",
        )

        # 5. Duplicate pixels across cases -> contradiction.
        report = evaluate(
            fixture(
                "duplicate-across",
                capture_plan={
                    "static": [_pixels(9), _pixels(9)],
                    "dynamic": [_pixels(9), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "identical pixels across different cases is a CONTRADICTION",
            _status(report, "capture-cross-case-uniqueness") == "CONTRADICTION",
        )

        # 6. Static captures differ -> contradiction.
        report = evaluate(
            fixture(
                "static-differs",
                capture_plan={
                    "static": [_pixels(1), _pixels(2)],
                    "dynamic": [_pixels(3), _pixels(4)],
                    "canvas": [_pixels(5), _pixels(6)],
                    "long": [_pixels(7), _pixels(8), _pixels(9)],
                },
            )
        )
        check(
            "differing static captures is a CONTRADICTION",
            _status(report, "capture-static-uniqueness") == "CONTRADICTION",
        )

        # 7. Dynamic captures all identical -> contradiction.
        report = evaluate(
            fixture(
                "dynamic-identical",
                capture_plan={
                    "static": [_pixels(1), _pixels(1)],
                    "dynamic": [_pixels(2), _pixels(2)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "identical dynamic captures (no change) is a CONTRADICTION",
            _status(report, "capture-dynamic-uniqueness") == "CONTRADICTION",
        )

        # 8. Missing policy log -> NOT-EVALUATED, no contradiction.
        report = evaluate(fixture("no-policy", policy=False))
        check(
            "missing policy log is NOT-EVALUATED",
            _status(report, "policy-no-presentation-allow") == "NOT-EVALUATED"
            and not report["contradictions"],
        )

        # 9. Missing watcher samples -> NOT-EVALUATED.
        report = evaluate(fixture("no-watcher", watcher=None))
        check(
            "missing watcher samples is NOT-EVALUATED",
            _status(report, "no-visible-window") == "NOT-EVALUATED"
            and not report["contradictions"],
        )

        # 10. Hash mismatch (tampered manifest) -> contradiction.
        root = fixture("hash-mismatch")
        with open(os.path.join(root, CAPTURES_FILE), encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest[0]["sha256"] = "0" * 64
        with open(os.path.join(root, CAPTURES_FILE), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        report = evaluate(root)
        check(
            "tampered manifest hash is a CONTRADICTION",
            any(c["check"].endswith("-integrity") and c["status"] == "CONTRADICTION"
                for c in report["checks"]),
        )

        # 11. Session contract violation -> contradiction.
        report = evaluate(fixture("bad-session", session=(True, False, True)))
        check(
            "non-mock-keychain session is a CONTRADICTION",
            _status(report, "session-contract") == "CONTRADICTION",
        )

        # 12. Missing required case -> NOT-EVALUATED, no contradiction.
        report = evaluate(
            fixture(
                "missing-case",
                capture_plan={
                    "static": [_pixels(1), _pixels(1)],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "missing required case captures is NOT-EVALUATED",
            _status(report, "capture-evidence-present") == "NOT-EVALUATED"
            and not report["contradictions"],
        )

        # 13. Planned minimum capture counts are enforced (static>=2,
        #     dynamic>=2, canvas>=2, long-scroll>=3).
        report = evaluate(
            fixture(
                "dynamic-single",
                capture_plan={
                    "static": [_pixels(1), _pixels(1)],
                    "dynamic": [_pixels(2)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "dynamic with a single capture is a CONTRADICTION",
            _status(report, "capture-dynamic-min-count") == "CONTRADICTION",
            _status(report, "capture-dynamic-min-count"),
        )
        report = evaluate(
            fixture(
                "long-two",
                capture_plan={
                    "static": [_pixels(1), _pixels(1)],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7)],
                },
            )
        )
        check(
            "long with two captures is a CONTRADICTION",
            _status(report, "capture-long-min-count") == "CONTRADICTION",
            _status(report, "capture-long-min-count"),
        )
        report = evaluate(
            fixture(
                "static-single",
                capture_plan={
                    "static": [_pixels(1)],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "static with a single capture is a CONTRADICTION",
            _status(report, "capture-static-min-count") == "CONTRADICTION",
        )
        report = evaluate(
            fixture(
                "canvas-single",
                capture_plan={
                    "static": [_pixels(1), _pixels(1)],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "canvas with a single capture is a CONTRADICTION",
            _status(report, "capture-canvas-min-count") == "CONTRADICTION",
        )

        # 14. Exit codes: 0 only when every required check PASSed (the optional
        #     video case may be NOT-EVALUATED); 3 for insufficient evidence;
        #     1 for contradictions.
        empty_dir = os.path.join(tmp, "empty-evidence")
        os.makedirs(empty_dir)
        empty_report = evaluate(empty_dir)
        check(
            "complete required evidence exits 0 (video NOT-EVALUATED allowed)",
            evidence_exit_code(good_report) == 0,
            "rc=%s" % evidence_exit_code(good_report),
        )
        check(
            "empty evidence dir exits 3 (INSUFFICIENT-EVIDENCE)",
            evidence_exit_code(empty_report) == 3,
            "rc=%s" % evidence_exit_code(empty_report),
        )
        contradiction_report = evaluate(fixture("exit-contradiction", watcher=(1, 1)))
        check(
            "contradiction exits 1",
            evidence_exit_code(contradiction_report) == 1,
            "rc=%s" % evidence_exit_code(contradiction_report),
        )

        # 15. The fixed required matrix comes from code: dropping a case,
        #     marking a required case optional or duplicating names can never
        #     reach rc=0.
        report = evaluate(
            fixture(
                "matrix-missing-dynamic",
                cases=[
                    {"name": "static", "kind": "static", "optional": False},
                    {"name": "canvas", "kind": "canvas", "optional": False},
                    {"name": "long", "kind": "long-scroll", "optional": False},
                    {"name": "video", "kind": "video", "optional": True},
                ],
                capture_plan={
                    "static": [_pixels(1), _pixels(1)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "deleting the dynamic case is a CONTRADICTION (fixed matrix)",
            _status(report, "plan-case-matrix") == "CONTRADICTION"
            and evidence_exit_code(report) != 0,
            _status(report, "plan-case-matrix"),
        )
        report = evaluate(
            fixture(
                "matrix-canvas-optional",
                cases=[
                    {"name": "static", "kind": "static", "optional": False},
                    {"name": "dynamic", "kind": "dynamic", "optional": False},
                    {"name": "canvas", "kind": "canvas", "optional": True},
                    {"name": "long", "kind": "long-scroll", "optional": False},
                    {"name": "video", "kind": "video", "optional": True},
                ],
            )
        )
        check(
            "marking canvas optional is a CONTRADICTION (fixed matrix)",
            _status(report, "plan-case-matrix") == "CONTRADICTION"
            and evidence_exit_code(report) != 0,
            _status(report, "plan-case-matrix"),
        )
        report = evaluate(
            fixture(
                "matrix-duplicate-names",
                cases=[
                    {"name": "static", "kind": "static", "optional": False},
                    {"name": "static", "kind": "static", "optional": False},
                    {"name": "dynamic", "kind": "dynamic", "optional": False},
                    {"name": "canvas", "kind": "canvas", "optional": False},
                    {"name": "long", "kind": "long-scroll", "optional": False},
                    {"name": "video", "kind": "video", "optional": True},
                ],
            )
        )
        check(
            "duplicate case names is a CONTRADICTION",
            _status(report, "plan-case-matrix") == "CONTRADICTION"
            and evidence_exit_code(report) != 0,
            _status(report, "plan-case-matrix"),
        )
        check(
            "the complete fixed matrix passes",
            _status(good_report, "plan-case-matrix") == "PASS"
            and _status(good_report, "capture-dynamic-min-count") == "PASS",
        )

        # 16. Raw watcher samples are authoritative: a count>0 sample must never
        #     be masked by a final max=0 line.
        report = evaluate(fixture("watcher-masked", watcher=(1, 0)))
        check(
            "sample count=1 with final max=0 is a CONTRADICTION (no masking)",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) != 0,
            _status(report, "no-visible-window"),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("SELFTEST SUMMARY: %d passed, %d failed" % (len(passed), len(failures)))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(
        description="Offline verdict engine for Gate C capture evidence."
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
        print("CAPTURE-EVIDENCE-CHECK: fixture written to %s" % args.write_fixture)
        return 0
    if not args.evidence:
        parser.error("--evidence is required (or use --self-test)")
    if not os.path.isdir(args.evidence):
        print("capture-evidence-check: no such dir: %s" % args.evidence, file=sys.stderr)
        return 2

    report = evaluate(args.evidence)
    report_dir = args.report_dir or args.evidence
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "capture-report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    with open(os.path.join(report_dir, "capture-report.md"), "w", encoding="utf-8") as handle:
        handle.write(render(report) + "\n")
    print("CAPTURE-EVIDENCE-CHECK: report written to %s" % report_dir)
    rc = evidence_exit_code(report)
    if rc == 1:
        print(
            "CAPTURE-EVIDENCE-CHECK: CONTRADICTIONS=%d (Gate C NOT passed)"
            % len(report["contradictions"])
        )
    elif rc == 3:
        print(
            "CAPTURE-EVIDENCE-CHECK: INSUFFICIENT-EVIDENCE (required checks not "
            "PASS: %s); Gate C NOT passed"
            % ", ".join(report["not_passed_required_checks"])
        )
    else:
        print(
            "CAPTURE-EVIDENCE-CHECK: contract satisfied by the evidence "
            "(Gate C still NOT passed by this tool)"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
