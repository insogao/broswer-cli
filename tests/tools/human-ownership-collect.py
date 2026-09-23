#!/usr/bin/env python3
"""Collector/verdict engine for the HUMAN-ownership manual scaffold.

Reads only passive evidence produced during a MANUAL session (the script never
launches, opens, activates or clicks anything):
  * browser policy log (BROWSER_CLI_PRESENTATION lines)
  * direct-binary CLI explicit `show` producer log lines
    (BROWSER_CLI_USER_EXPLICIT_SHOW stage=producer from patch 003)
  * pre-started app-identity watcher samples (onscreen-window-watch.m)
  * the ops sink JSONL posted by the manual extension ops page
  * optionally a read-only GET /json/list for the target inventory

Produces report.json + report.md with per-checklist verdicts:
  PASS            evidence found and consistent
  NOT-EVALUATED   the manual step produced no evidence (never a fake pass)
  CONTRADICTION   evidence violates the Gate B invariants (exit 1)
  BLOCKED         known-unsupported area (child dialog attribution, remote
                  app-shim windows, TabsCreate async attribution)

Usage:
  human-ownership-collect.py --log PATH --samples PATH --sink PATH
      --report-dir DIR [--devtools-port PORT]
Exit: 0 = report written without contradictions, 1 = contradiction(s) found.
"""

import argparse
import json
import os
import re
import sys
import urllib.request

AUTOMATION_ORIGINS = {"agent-assist", "extension", "web-page", "session-restore"}

# Producer outcomes of the CLI explicit `show` switch (patch 003).
CLI_SHOW_REASON_EXISTING = "existing-window-presented"
CLI_SHOW_REASON_CREATED = "window-created-and-presented"
CLI_SHOW_REASON_BLOCKED = "launch-arguments-not-user-explicit"

BLOCKED_MATRIX = [
    {
        "area": "child-dialog attribution (window.open child dialogs / alert sheets)",
        "status": "BLOCKED",
        "note": "No producer support yet; must be covered by a follow-up matrix. Never claim a pass.",
    },
    {
        "area": "remote app-shim (PWA) windows",
        "status": "BLOCKED",
        "note": "Presentation happens below the in-process policy (app-shim bridge); follow-up matrix.",
    },
    {
        "area": "chrome.tabs.create async attribution",
        "status": "BLOCKED",
        "note": "TabsCreate attribution is not proven; accept only with explicit policy evidence, otherwise NOT-EVALUATED.",
    },
]


def parse_log(path):
    """Returns (presentation_entries, producer_entries) from one browser log.

    Producer lines never contain BROWSER_CLI_PRESENTATION, so they are split
    out explicitly; an absent producer line can only ever yield
    NOT-EVALUATED, never a fake pass.
    """
    entries = []
    producer_entries = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "BROWSER_CLI_USER_EXPLICIT_SHOW" in line:
                    fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
                    fields["_raw"] = line.rstrip("\n")
                    producer_entries.append(fields)
                    continue
                if "BROWSER_CLI_PRESENTATION" not in line:
                    continue
                fields = dict(re.findall(r"(\w+)=([^\s]+)", line))
                fields["_raw"] = line.rstrip("\n")
                entries.append(fields)
    except OSError:
        pass
    return entries, producer_entries


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


def parse_sink(path):
    payloads = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payloads.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return payloads


def verdict(name, status, detail):
    return {"check": name, "status": status, "detail": detail}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--sink", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--devtools-port", type=int, default=0)
    args = parser.parse_args()

    os.makedirs(args.report_dir, exist_ok=True)
    entries, producer_entries = parse_log(args.log)
    samples, details, final = parse_samples(args.samples)
    sink_payloads = parse_sink(args.sink)

    decisions = [e for e in entries if e.get("stage") == "policy" and "decision" in e]
    transitions = [e for e in entries if e.get("stage") == "policy" and "transition" in e]
    owned_ids = sorted(
        {e.get("window_id") for e in entries if e.get("owned") == "yes"}
        - {None}
    )
    granted = [
        e for e in transitions if e.get("transition") == "human-ownership-granted"
    ]
    released = [
        e for e in transitions if e.get("transition") == "human-ownership-released"
    ]

    automation_allows_owned = [
        e for e in decisions
        if e.get("decision") == "allow"
        and e.get("origin") in AUTOMATION_ORIGINS
        and e.get("owned") == "yes"
    ]
    internal_allows_background = [
        e for e in decisions
        if e.get("decision") == "allow"
        and e.get("origin") == "internal"
        and e.get("state") == "background"
    ]
    extension_denies_owned = [
        e for e in decisions
        if e.get("decision") == "deny"
        and e.get("origin") == "extension"
        and e.get("owned") == "yes"
    ]
    user_explicit_allows = [
        e for e in decisions
        if e.get("decision") == "allow" and e.get("origin") == "user-explicit"
    ]

    cli_show_handled_existing = [
        e for e in producer_entries
        if e.get("result") == "handled"
        and e.get("reason") == CLI_SHOW_REASON_EXISTING
    ]
    cli_show_handled_created = [
        e for e in producer_entries
        if e.get("result") == "handled"
        and e.get("reason") == CLI_SHOW_REASON_CREATED
    ]
    cli_show_denied_blocked = [
        e for e in producer_entries
        if e.get("result") == "denied"
        and e.get("reason") == CLI_SHOW_REASON_BLOCKED
    ]
    cli_show_failed = [e for e in producer_entries if e.get("result") == "failed"]
    # Fail closed on anything the producer contract does not define: a handled /
    # denied line that is not managed, a denied reason other than the known
    # blocked-launch-argument control, or an unknown result.
    cli_show_unexpected = []
    for entry in producer_entries:
        result = entry.get("result")
        if result in ("handled", "denied") and entry.get("managed") != "yes":
            cli_show_unexpected.append(entry)
        elif result == "denied" and entry.get("reason") != CLI_SHOW_REASON_BLOCKED:
            cli_show_unexpected.append(entry)
        elif result not in ("handled", "denied", "failed"):
            cli_show_unexpected.append(entry)
    cli_show_handled = cli_show_handled_existing + cli_show_handled_created
    # A handled presentation must always be observable as an allowed
    # USER_EXPLICIT policy decision; otherwise the evidence is incomplete.
    cli_show_missing_policy_allow = bool(cli_show_handled) and not user_explicit_allows

    watcher_max = final.get("max", max(samples) if samples else -1)

    ops_results = []
    for payload in sink_payloads:
        ops_results.extend(payload.get("results", []))
    latest_inventory = sink_payloads[-1].get("inventory", []) if sink_payloads else []
    latest_focused = sink_payloads[-1].get("focusedWindowId") if sink_payloads else None

    checks = []
    checks.append(
        verdict(
            "watcher-no-transient-window",
            "PASS" if watcher_max == 0 else "CONTRADICTION",
            "max=%s nonzero=%s samples=%s"
            % (watcher_max, final.get("nonzero_samples", "?"), len(samples)),
        )
    )
    checks.append(
        verdict(
            "user-explicit-takeover-recorded",
            "PASS" if granted else "NOT-EVALUATED",
            "grants=%d released=%d owned_ids=%s"
            % (len(granted), len(released), owned_ids),
        )
    )
    checks.append(
        verdict(
            "user-explicit-presentation-allowed",
            "PASS" if user_explicit_allows else "NOT-EVALUATED",
            "allow(origin=user-explicit) count=%d" % len(user_explicit_allows),
        )
    )
    checks.append(
        verdict(
            "automation-never-allowed-on-owned-window",
            "CONTRADICTION" if automation_allows_owned else "PASS",
            "allow(automation, owned=yes) count=%d" % len(automation_allows_owned),
        )
    )
    checks.append(
        verdict(
            "no-internal-allow-in-background",
            "CONTRADICTION" if internal_allows_background else "PASS",
            "allow(internal, background) count=%d" % len(internal_allows_background),
        )
    )
    checks.append(
        verdict(
            "extension-denied-on-owned-window",
            "PASS" if extension_denies_owned else "NOT-EVALUATED",
            "deny(extension, owned=yes) count=%d" % len(extension_denies_owned),
        )
    )

    per_op = []
    for result in ops_results:
        op = result.get("op", "")
        window_id = str(result.get("windowId", ""))
        matching_denies = [
            e for e in extension_denies_owned if e.get("window_id") == window_id
        ]
        contradicting = [
            e for e in automation_allows_owned if e.get("window_id") == window_id
        ]
        if contradicting:
            status = "CONTRADICTION"
        elif matching_denies:
            status = "PASS"
        else:
            status = "NOT-EVALUATED"
        per_op.append(
            {
                "op": op,
                "window_id": window_id,
                "api_ok": result.get("ok"),
                "api_error": result.get("error"),
                "kernel_denies": len(matching_denies),
                "status": status,
            }
        )

    created_windows = [
        r for r in ops_results if r.get("op") == "create.background-window"
    ]
    checks.append(
        verdict(
            "background-window-created-while-human",
            "PASS" if created_windows else "NOT-EVALUATED",
            "create.background-window count=%d inventory=%s focusedWindowId=%s"
            % (len(created_windows), latest_inventory, latest_focused),
        )
    )

    checks.append(
        verdict(
            "cli-show-existing-window-presented",
            "PASS" if cli_show_handled_existing else "NOT-EVALUATED",
            "handled reason=%s count=%d"
            % (CLI_SHOW_REASON_EXISTING, len(cli_show_handled_existing)),
        )
    )
    checks.append(
        verdict(
            "cli-show-zero-window-created-and-presented",
            "PASS" if cli_show_handled_created else "NOT-EVALUATED",
            "handled reason=%s count=%d"
            % (CLI_SHOW_REASON_CREATED, len(cli_show_handled_created)),
        )
    )
    checks.append(
        verdict(
            "cli-show-denied-launch-arguments",
            "PASS" if cli_show_denied_blocked else "NOT-EVALUATED",
            "denied reason=%s count=%d"
            % (CLI_SHOW_REASON_BLOCKED, len(cli_show_denied_blocked)),
        )
    )
    cli_show_inconsistent = bool(
        cli_show_failed or cli_show_unexpected or cli_show_missing_policy_allow
    )
    checks.append(
        verdict(
            "cli-show-producer-consistency",
            "CONTRADICTION" if cli_show_inconsistent else "PASS",
            "failed=%d unexpected=%d handled=%d denied=%d missing-policy-allow=%s"
            % (
                len(cli_show_failed),
                len(cli_show_unexpected),
                len(cli_show_handled),
                len(cli_show_denied_blocked),
                cli_show_missing_policy_allow,
            ),
        )
    )

    target_inventory = None
    if args.devtools_port:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:%d/json/list" % args.devtools_port, timeout=5
            ) as response:
                target_inventory = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - recorded, not fatal
            target_inventory = {"error": "%s: %s" % (type(exc).__name__, exc)}

    contradictions = [c for c in checks if c["status"] == "CONTRADICTION"]
    contradictions += [o for o in per_op if o["status"] == "CONTRADICTION"]
    report = {
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "log_path": args.log,
        "samples_path": args.samples,
        "sink_path": args.sink,
        "policy": {
            "decision_count": len(decisions),
            "transitions": transitions,
            "owned_window_ids": owned_ids,
            "automation_allows_owned": automation_allows_owned,
            "internal_allows_background": internal_allows_background,
            "extension_denies_owned": extension_denies_owned,
        },
        "watcher": {
            "max": watcher_max,
            "nonzero_samples": final.get("nonzero_samples"),
            "samples": len(samples),
            "nonzero_details": details,
        },
        "ops": {"results": ops_results, "per_op": per_op, "inventory": latest_inventory},
        "cli_show": {
            "producer_entries": producer_entries,
            "handled_existing_window": cli_show_handled_existing,
            "handled_created_window": cli_show_handled_created,
            "denied_blocked_launch_arguments": cli_show_denied_blocked,
            "failed": cli_show_failed,
            "unexpected": cli_show_unexpected,
            "missing_policy_allow": cli_show_missing_policy_allow,
        },
        "checks": checks,
        "blocked_matrix": BLOCKED_MATRIX,
        "target_inventory": target_inventory,
        "contradictions": contradictions,
    }

    with open(os.path.join(args.report_dir, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)

    lines = ["# HUMAN ownership manual evidence report", ""]
    lines.append("## Checklist verdicts")
    for check in checks:
        lines.append("- **%s**: %s (%s)" % (check["status"], check["check"], check["detail"]))
    lines.append("")
    lines.append("## Extension ops (manual button presses)")
    if per_op:
        for op in per_op:
            lines.append(
                "- **%s**: op=%s window=%s api_ok=%s error=%s kernel_denies=%d"
                % (op["status"], op["op"], op["window_id"], op["api_ok"], op["api_error"], op["kernel_denies"])
            )
    else:
        lines.append("- NOT-EVALUATED: no ops were recorded by the sink")
    lines.append("")
    lines.append("## CLI explicit show (direct binary, manual cases)")
    if producer_entries:
        for entry in producer_entries:
            lines.append(
                "- result=%s reason=%s managed=%s"
                % (entry.get("result"), entry.get("reason"), entry.get("managed"))
            )
    else:
        lines.append("- NOT-EVALUATED: no CLI show producer evidence in the log")
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
    with open(os.path.join(args.report_dir, "report.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    print("HUMAN-OWNERSHIP-COLLECT: report written to %s" % args.report_dir)
    if contradictions:
        print("HUMAN-OWNERSHIP-COLLECT: CONTRADICTIONS=%d" % len(contradictions))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
