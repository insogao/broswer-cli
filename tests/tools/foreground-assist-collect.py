#!/usr/bin/env python3
"""Offline collector/verdict engine for the Foreground Assist manual package.

Reads only passive evidence produced during a MANUAL session (this tool never
launches, opens, activates or clicks anything):

  * browser log: BROWSER_CLI_FOREGROUND_ASSIST producer lines (Slice 2) and
    BROWSER_CLI_PRESENTATION policy/host lines (lease grants, decisions,
    assist-window-visible / assist-window-background)
  * pre-started app-identity watcher samples (onscreen-window-watch.m)
  * the local page server request log (assist-page-server.py)
  * an optional ABORT marker the user creates when a system dialog appeared

Verdicts (fail closed, never a fake pass):
  PASS            evidence found and consistent
  NOT-EVALUATED   the manual step produced no (ordered) evidence
  CONTRADICTION   evidence violates a Gate B / Assist invariant (exit 1)
  BLOCKED         known-unsupported area (user hand-back producer, entry-point
                  attribution)

Ordering matters and is taken from the log's line order (chronological): a
"no grant before the blocked request" claim is only accepted when the blocked
producer line really precedes every lease grant.

Patch 005 contract: an assist intent is consumed (cold-start sanitizer strips
the URL, ProcessSingleton handler consumes the request before ordinary
processing), so a blocked assist URL must never reach the page server. Any
`/page?name=blocked_cold` or `/page?name=blocked_session` GET is a hard
CONTRADICTION, and missing producer / server-log / watcher evidence is
NOT-EVALUATED, never a pass.

Usage:
  foreground-assist-collect.py --log PATH --samples PATH --server-log PATH
      --report-dir DIR [--abort-flag PATH]
Exit: 0 = report written without contradictions, 1 = contradiction(s) found.
"""

import argparse
import json
import os
import re
import sys
import time

PRODUCER_MARKER = "BROWSER_CLI_FOREGROUND_ASSIST"
PRESENTATION_MARKER = "BROWSER_CLI_PRESENTATION"

BLOCKED_LAUNCH_REASON = "launch-arguments-not-agent-assist"
HANDLED_REASONS = {"lease-granted-and-presented", "lease-released"}
DENIED_REASONS = {
    "launch-arguments-not-agent-assist",
    "conflicting-producer-switches",
    "invalid-reason",
    "invalid-target",
    "target-window-not-found",
    "target-human-owned",
    "lease-denied",
    "lease-busy",
    "no-matching-lease",
}
NOOP_REASONS = {"unmanaged-stock-behavior"}

BLOCKED_MATRIX = [
    {
        "area": 'user hand-back ("交还 AI") producer',
        "status": "BLOCKED",
        "note": "No hand-back producer exists yet (PROJECT-PROGRESS C); the "
        "old-TTL-after-hand-back sub-check cannot be evaluated. Never claim a pass.",
    },
    {
        "area": "entry-point attribution (cold start vs ProcessSingleton)",
        "status": "BLOCKED",
        "note": "The producer log does not record which entry point handled the "
        "request; both share one classifier. The runbook performs the cold-start "
        "variant first, and the verdict rests on the shared fail-closed evidence.",
    },
]


def parse_events(path):
    """Returns the merged, chronologically ordered producer/policy/host events.

    Every event carries `_kind` and `_index` (line order) so verdicts can make
    ordering claims without trusting timestamps.
    """
    events = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if PRODUCER_MARKER not in line and PRESENTATION_MARKER not in line:
                    continue
                fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
                if not fields:
                    continue
                fields["_raw"] = line.rstrip("\n")
                fields["_index"] = len(events)
                if PRODUCER_MARKER in line:
                    fields["_kind"] = "producer"
                elif "stage=policy" in line:
                    fields["_kind"] = "policy"
                elif "stage=host" in line:
                    fields["_kind"] = "host"
                else:
                    fields["_kind"] = "other"
                events.append(fields)
    except OSError:
        pass
    return events


def parse_samples(path):
    samples = []
    details = []
    final = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("sample "):
                    fields = dict(part.split("=", 1) for part in line.split()[1:])
                    samples.append(int(fields.get("count", "0")))
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


def parse_server_log(path):
    """Returns (requests, readable). `readable` distinguishes "the server log
    exists and was read" from "no evidence at all" so a missing log can never
    be mistaken for a clean zero-request session."""
    requests = []
    readable = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            readable = True
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                fields = dict(
                    part.split("=", 1) for part in line.split() if "=" in part
                )
                if fields:
                    requests.append(fields)
    except OSError:
        pass
    return requests, readable


def verdict(name, status, detail):
    return {"check": name, "status": status, "detail": detail}


def by_transition(events, name):
    return [e for e in events if e.get("transition") == name]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--server-log", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--abort-flag", default="")
    args = parser.parse_args()

    os.makedirs(args.report_dir, exist_ok=True)
    events = parse_events(args.log)
    samples, sample_details, final = parse_samples(args.samples)
    server_requests, server_readable = parse_server_log(args.server_log)
    watcher_max = final.get("max", max(samples) if samples else -1)
    target_page_requests = [
        r for r in server_requests if "name=target" in r.get("path", "")
    ]
    # Patch 005 contract: an assist intent is consumed (cold-start sanitizer
    # strips the URL; the ProcessSingleton handler consumes the request before
    # ordinary processing), so neither blocked URL may ever reach the page
    # server. A single such GET is a hard contradiction.
    blocked_navigation_requests = [
        r
        for r in server_requests
        if "name=blocked_cold" in r.get("path", "")
        or "name=blocked_session" in r.get("path", "")
    ]

    producer = [e for e in events if e["_kind"] == "producer"]
    policy = [e for e in events if e["_kind"] == "policy"]
    host = [e for e in events if e["_kind"] == "host"]

    grants = by_transition(policy, "assist-lease-granted")
    releases = by_transition(policy, "assist-lease-released")
    human_grants = by_transition(policy, "human-ownership-granted")
    human_releases = by_transition(policy, "human-ownership-released")
    visible = by_transition(host, "assist-window-visible")
    background = by_transition(host, "assist-window-background")
    allow_agent = [
        e
        for e in policy
        if e.get("decision") == "allow" and e.get("origin") == "agent-assist"
    ]
    blocked = [
        e
        for e in producer
        if e.get("result") == "denied" and e.get("reason") == BLOCKED_LAUNCH_REASON
    ]
    handled = [e for e in producer if e.get("result") == "handled"]
    failed = [e for e in producer if e.get("result") == "failed"]

    checks = []

    # A. Cold-start / blocked assist+URL: fail closed, no assist presentation
    # before the first grant, and never more than one visible window overall.
    if not blocked:
        checks.append(
            verdict(
                "assist-blocked-request-fail-closed",
                "NOT-EVALUATED",
                "no denied producer line with reason=%s" % BLOCKED_LAUNCH_REASON,
            )
        )
    else:
        first_blocked = min(e["_index"] for e in blocked)
        first_grant = min((e["_index"] for e in grants), default=None)
        first_visible = min((e["_index"] for e in visible), default=None)
        problems = []
        if first_visible is not None and (
            first_grant is None or first_visible < first_grant
        ):
            problems.append("assist-window-visible before any lease grant")
        grants_before = [g for g in grants if g["_index"] < first_blocked]
        if grants_before:
            problems.append("lease granted before the blocked request")
        if first_grant is not None and first_blocked > first_grant:
            problems.append("blocked request not ordered before the first grant")
        if problems:
            checks.append(
                verdict(
                    "assist-blocked-request-fail-closed",
                    "CONTRADICTION",
                    "; ".join(problems),
                )
            )
        else:
            checks.append(
                verdict(
                    "assist-blocked-request-fail-closed",
                    "PASS",
                    "denied=%d ordered-before-grant=%s visible-before-grant=False"
                    % (len(blocked), first_grant is not None),
                )
            )

    # A2. Patch 005 contract: a blocked assist URL must never navigate at all
    # (cold-start sanitizer strips the URL, ProcessSingleton handler consumes
    # the request). Any page-server GET for the blocked paths is a hard
    # contradiction, and missing producer/server/watcher evidence never becomes
    # a pass.
    if blocked_navigation_requests:
        checks.append(
            verdict(
                "assist-blocked-url-never-navigates",
                "CONTRADICTION",
                "blocked assist URL reached the page server (no fallback "
                "navigation allowed): %s"
                % "; ".join(
                    "path=%s status=%s" % (r.get("path"), r.get("status"))
                    for r in blocked_navigation_requests
                ),
            )
        )
    elif not blocked:
        checks.append(
            verdict(
                "assist-blocked-url-never-navigates",
                "NOT-EVALUATED",
                "no blocked producer line to correlate",
            )
        )
    elif not server_readable:
        checks.append(
            verdict(
                "assist-blocked-url-never-navigates",
                "NOT-EVALUATED",
                "page server log missing/unreadable; cannot prove no navigation",
            )
        )
    elif not target_page_requests:
        checks.append(
            verdict(
                "assist-blocked-url-never-navigates",
                "NOT-EVALUATED",
                "page server log has no /page?name=target request; cannot prove "
                "the server was logging during the session",
            )
        )
    elif watcher_max < 0:
        checks.append(
            verdict(
                "assist-blocked-url-never-navigates",
                "NOT-EVALUATED",
                "no watcher samples",
            )
        )
    else:
        checks.append(
            verdict(
                "assist-blocked-url-never-navigates",
                "PASS",
                "blocked_cold=0 blocked_session=0 target_requests=%d watcher_max=%s"
                % (len(target_page_requests), watcher_max),
            )
        )

    # B. A blocked request during HUMAN must not touch the user's window.
    if not human_grants or not blocked:
        checks.append(
            verdict(
                "assist-blocked-request-does-not-pollute-human",
                "NOT-EVALUATED",
                "human_grants=%d blocked=%d" % (len(human_grants), len(blocked)),
            )
        )
    else:
        first_human = min(human_grants, key=lambda e: e["_index"])
        human_id = first_human.get("window_id")
        human_index = first_human["_index"]
        denied_after = [e for e in blocked if e["_index"] > human_index]
        if not denied_after:
            checks.append(
                verdict(
                    "assist-blocked-request-does-not-pollute-human",
                    "NOT-EVALUATED",
                    "no blocked request after the HUMAN grant (window=%s)" % human_id,
                )
            )
        else:
            problems = []
            for e in grants:
                if e["_index"] > human_index and e.get("window_id") == human_id:
                    problems.append("assist lease granted for the HUMAN window")
            for e in background:
                if e["_index"] > human_index and e.get("widget_id") == human_id:
                    problems.append("HUMAN window returned to background by assist")
            for e in human_releases:
                if e.get("window_id") == human_id:
                    problems.append("HUMAN ownership released during the session")
            for e in allow_agent:
                if e["_index"] > human_index and e.get("window_id") == human_id:
                    problems.append("agent-assist allow on the HUMAN window")
            checks.append(
                verdict(
                    "assist-blocked-request-does-not-pollute-human",
                    "CONTRADICTION" if problems else "PASS",
                    "; ".join(problems)
                    if problems
                    else "window=%s blocked-after-human=%d owned-intact=True"
                    % (human_id, len(denied_after)),
                )
            )

    # C. A grant shows only the target window, through the policy gateway.
    if not grants:
        checks.append(
            verdict(
                "assist-grant-shows-only-the-target-window",
                "NOT-EVALUATED",
                "no assist-lease-granted line",
            )
        )
    else:
        granted_ids = sorted({e.get("window_id") for e in grants})
        visible_ids = sorted({e.get("widget_id") for e in visible})
        problems = []
        for v in visible:
            wid = v.get("widget_id")
            if wid not in granted_ids:
                problems.append("visible for never-granted window %s" % wid)
            elif not any(
                g.get("window_id") == wid and g["_index"] < v["_index"]
                for g in grants
            ):
                problems.append("visible before the grant for window %s" % wid)
            if not any(
                a.get("window_id") == wid and a["_index"] < v["_index"]
                for a in allow_agent
            ):
                problems.append("visible without agent-assist policy allow %s" % wid)
        if problems:
            checks.append(
                verdict(
                    "assist-grant-shows-only-the-target-window",
                    "CONTRADICTION",
                    "; ".join(problems),
                )
            )
        else:
            checks.append(
                verdict(
                    "assist-grant-shows-only-the-target-window",
                    "PASS",
                    "granted=%s visible=%s allow-lines=%d"
                    % (granted_ids, visible_ids, len(allow_agent)),
                )
            )

    # D. Agent release and TTL timeout both return exactly the leased window
    # to the background.
    for name, release_reason in (
        ("assist-agent-release-returns-to-background", "agent-release"),
        ("assist-timeout-returns-to-background", "timeout"),
    ):
        matching = [e for e in releases if e.get("reason") == release_reason]
        if not matching:
            checks.append(
                verdict(name, "NOT-EVALUATED", "no release with reason=%s" % release_reason)
            )
            continue
        first = min(matching, key=lambda e: e["_index"])
        wid = first.get("window_id")
        hidden_after = [
            b
            for b in background
            if b.get("widget_id") == wid and b["_index"] > first["_index"]
        ]
        checks.append(
            verdict(
                name,
                "PASS" if hidden_after else "CONTRADICTION",
                "release window=%s hidden_after=%d" % (wid, len(hidden_after)),
            )
        )

    # E. User takeover: the old TTL must not hide (or re-grant) the window.
    takeovers = [e for e in releases if e.get("reason") == "user-takeover"]
    if not takeovers:
        checks.append(
            verdict(
                "assist-user-takeover-not-hidden-by-old-ttl",
                "NOT-EVALUATED",
                "no release with reason=user-takeover",
            )
        )
    else:
        first_takeover = min(takeovers, key=lambda e: e["_index"])
        wid = first_takeover.get("window_id")
        human_after = [
            g
            for g in human_grants
            if g.get("window_id") == wid and g["_index"] > first_takeover["_index"]
        ]
        problems = []
        if not human_after:
            problems.append("no human-ownership-granted after the takeover")
        for b in background:
            if b.get("widget_id") == wid and b["_index"] > first_takeover["_index"]:
                problems.append("HUMAN window hidden after the takeover")
        for g in grants:
            if g.get("window_id") == wid and g["_index"] > first_takeover["_index"]:
                problems.append("assist lease re-granted after the takeover")
        checks.append(
            verdict(
                "assist-user-takeover-not-hidden-by-old-ttl",
                "CONTRADICTION" if problems else "PASS",
                "; ".join(problems)
                if problems
                else "window=%s human-grant-after=%d hidden-after=0"
                % (wid, len(human_after)),
            )
        )

    # F. Hand-back ("交还 AI"): no producer exists yet -> BLOCKED, never faked.
    if not human_releases:
        checks.append(
            verdict(
                "assist-handback-old-ttl-not-hide",
                "BLOCKED",
                "no human-ownership-released evidence: the user hand-back producer "
                "does not exist yet (PROJECT-PROGRESS C); cannot evaluate",
            )
        )
    else:
        first_handback = min(human_releases, key=lambda e: e["_index"])
        wid = first_handback.get("window_id")
        hidden_after = [
            b
            for b in background
            if b.get("widget_id") == wid and b["_index"] > first_handback["_index"]
        ]
        regrants = [
            g
            for g in grants
            if g.get("window_id") == wid and g["_index"] > first_handback["_index"]
        ]
        if not hidden_after:
            checks.append(
                verdict(
                    "assist-handback-old-ttl-not-hide",
                    "NOT-EVALUATED",
                    "hand-back recorded but the window was not returned to the background",
                )
            )
        else:
            checks.append(
                verdict(
                    "assist-handback-old-ttl-not-hide",
                    "CONTRADICTION"
                    if len(hidden_after) > 1 or regrants
                    else "PASS",
                    "hidden_after=%d regrants=%d" % (len(hidden_after), len(regrants)),
                )
            )

    # G. Producer consistency: fail closed on failed / unknown outcomes, and
    # every handled grant must have a matching policy grant.
    problems = []
    for e in failed:
        problems.append("failed producer: reason=%s" % e.get("reason"))
    for e in producer:
        result = e.get("result")
        if result == "handled":
            if e.get("managed") != "yes":
                problems.append("handled producer without managed=yes")
            if e.get("reason") not in HANDLED_REASONS:
                problems.append("unknown handled reason: %s" % e.get("reason"))
            if e.get("reason") == "lease-granted-and-presented":
                wid = e.get("window_id")
                if not any(
                    g.get("window_id") == wid and g["_index"] > e["_index"]
                    for g in grants
                ):
                    problems.append("handled grant without a policy grant line")
            if e.get("reason") == "lease-released":
                wid = e.get("window_id")
                if not any(
                    r.get("window_id") == wid
                    and r.get("reason") == "agent-release"
                    and r["_index"] > e["_index"]
                    for r in releases
                ):
                    problems.append("handled release without an agent-release line")
        elif result == "denied":
            if e.get("managed") != "yes":
                problems.append("denied producer without managed=yes")
            if e.get("reason") not in DENIED_REASONS:
                problems.append("unknown denied reason: %s" % e.get("reason"))
        elif result == "noop":
            if e.get("reason") not in NOOP_REASONS:
                problems.append("unknown noop reason: %s" % e.get("reason"))
        elif result != "failed":
            problems.append("unknown producer result: %s" % result)
    checks.append(
        verdict(
            "assist-producer-consistency",
            "CONTRADICTION" if problems else "PASS",
            "; ".join(problems)
            if problems
            else "producer=%d handled=%d denied=%d failed=%d"
            % (len(producer), len(handled), len(blocked), len(failed)),
        )
    )

    # H. Watcher: never more than one visible Browser CLI window at a time.
    if watcher_max < 0:
        checks.append(
            verdict(
                "assist-at-most-one-visible-window",
                "NOT-EVALUATED",
                "no watcher samples",
            )
        )
    else:
        checks.append(
            verdict(
                "assist-at-most-one-visible-window",
                "CONTRADICTION" if watcher_max > 1 else "PASS",
                "watcher max=%s nonzero_samples=%s samples=%d"
                % (watcher_max, final.get("nonzero_samples", "?"), len(samples)),
            )
        )

    # I. The user-reported abort marker always fails the session closed.
    if args.abort_flag and os.path.exists(args.abort_flag):
        checks.append(
            verdict(
                "session-aborted-by-user",
                "CONTRADICTION",
                "ABORT marker present: the user reported a system dialog or aborted "
                "the session; never a fake pass",
            )
        )
    else:
        checks.append(
            verdict("session-aborted-by-user", "PASS", "no ABORT marker")
        )

    contradictions = [c for c in checks if c["status"] == "CONTRADICTION"]
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "log_path": args.log,
        "samples_path": args.samples,
        "server_log_path": args.server_log,
        "policy": {
            "assist_grants": grants,
            "assist_releases": releases,
            "assist_visible": visible,
            "assist_background": background,
            "agent_assist_allows": allow_agent,
            "human_grants": human_grants,
            "human_releases": human_releases,
        },
        "producer": {
            "entries": producer,
            "handled": handled,
            "blocked_launch_arguments": blocked,
            "failed": failed,
        },
        "watcher": {
            "max": watcher_max,
            "nonzero_samples": final.get("nonzero_samples"),
            "samples": len(samples),
            "nonzero_details": sample_details,
        },
        "page_server": {
            "requests": server_requests,
            "readable": server_readable,
            "target_page_requests": len(target_page_requests),
            "blocked_navigation_requests": blocked_navigation_requests,
        },
        "checks": checks,
        "blocked_matrix": BLOCKED_MATRIX,
        "contradictions": contradictions,
    }

    with open(
        os.path.join(args.report_dir, "report.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump(report, handle, indent=2, sort_keys=True)

    lines = ["# Foreground Assist manual evidence report", ""]
    lines.append("## Checklist verdicts")
    for check in checks:
        lines.append(
            "- **%s**: %s (%s)" % (check["status"], check["check"], check["detail"])
        )
    lines.append("")
    lines.append("## Producer evidence (BROWSER_CLI_FOREGROUND_ASSIST)")
    if producer:
        for entry in producer:
            lines.append(
                "- result=%s reason=%s window_id=%s lease_reason=%s managed=%s"
                % (
                    entry.get("result"),
                    entry.get("reason"),
                    entry.get("window_id"),
                    entry.get("lease_reason"),
                    entry.get("managed"),
                )
            )
    else:
        lines.append("- NOT-EVALUATED: no assist producer evidence in the log")
    lines.append("")
    lines.append("## Policy evidence (BROWSER_CLI_PRESENTATION)")
    for entry in grants + releases + visible + background + human_grants:
        lines.append("- %s" % entry.get("_raw"))
    if not (grants or releases or visible or background or human_grants):
        lines.append("- NOT-EVALUATED: no assist policy evidence in the log")
    lines.append("")
    lines.append("## Real page evidence (local page server)")
    if server_requests:
        for entry in server_requests:
            lines.append(
                "- ts=%s path=%s status=%s"
                % (entry.get("ts"), entry.get("path"), entry.get("status"))
            )
    else:
        lines.append("- NOT-EVALUATED: the page server saw no requests")
    lines.append(
        "- blocked-navigation requests (must be 0): %d"
        % len(blocked_navigation_requests)
    )
    lines.append("")
    lines.append("## BLOCKED / follow-up matrix (never a fake pass)")
    for item in BLOCKED_MATRIX:
        lines.append("- **%s** %s — %s" % (item["status"], item["area"], item["note"]))
    lines.append("")
    if contradictions:
        lines.append("## CONTRADICTIONS (fail-closed)")
        for item in contradictions:
            lines.append("- %s" % json.dumps(item, ensure_ascii=False))
    else:
        lines.append("## No contradictions found")
    with open(
        os.path.join(args.report_dir, "report.md"), "w", encoding="utf-8"
    ) as handle:
        handle.write("\n".join(lines) + "\n")

    print("FOREGROUND-ASSIST-COLLECT: report written to %s" % args.report_dir)
    if contradictions:
        print(
            "FOREGROUND-ASSIST-COLLECT: CONTRADICTIONS=%d" % len(contradictions)
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
