#!/usr/bin/env python3
"""Offline verdict engine for the Gate C (BACKGROUND_CAPTURE) evidence contract.

It never launches, shows, activates or captures anything. The evidence input is
read-only; the only files this tool writes are capture-report.json/md in the
report dir. It parses evidence produced by a (future) execution slice and
applies fail-closed verdicts. Fake passes it rejects:

  * any on-screen window during the session (app-identity watcher max > 0);
  * empty / blank / suspiciously small PNG captures: blank detection validates
    IHDR and the exact scanline payload, then reverses the PNG scanline filters
    0..4 (W3C PNG Third Edition §9) before measuring pixel uniformity, so a
    blank capture encoded with Sub/Up/Average/Paeth filters is still caught and
    filter bytes are never counted as pixels; unsupported-but-legal PNG formats
    are unverifiable and can never PASS;
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


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# W3C PNG (Third Edition) §9: filter method 0 defines five per-scanline filters
# (0 None, 1 Sub, 2 Up, 3 Average, 4 Paeth) that a decoder must reverse before
# the scanline bytes are pixels. Chromium's Page.captureScreenshot PNG path
# (page_handler.cc: EncodeBGRASkBitmap / FastEncodeBGRASkBitmap ->
# SkPngRustEncoder -> rust/png FFI) writes either Filter::Up or Filter::Adaptive
# (0..4 per row), so all five must be implemented; the compressed payload can
# never be treated as pixels.
PNG_FILTER_NONE = 0
PNG_FILTER_SUB = 1
PNG_FILTER_UP = 2
PNG_FILTER_AVERAGE = 3
PNG_FILTER_PAETH = 4
PNG_FILTER_TYPES = (
    PNG_FILTER_NONE,
    PNG_FILTER_SUB,
    PNG_FILTER_UP,
    PNG_FILTER_AVERAGE,
    PNG_FILTER_PAETH,
)

# Legal IHDR field values (W3C PNG Third Edition §4.1). Values outside these are
# invalid; legal values outside the verified screenshot subset are
# `unsupported` (unverifiable) and can never PASS.
PNG_LEGAL_COLOR_TYPES = (0, 2, 3, 4, 6)
PNG_LEGAL_BIT_DEPTHS = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}
# Supported subset: 8-bit RGB/RGBA, non-interlaced, compression/filter method 0.
# That is what Chromium screenshots can contain; everything else stays
# unverifiable instead of being guessed.
PNG_SUPPORTED_COLOR_TYPES = {2: ("RGB", 3), 6: ("RGBA", 4)}
# Decoding bound: the expected scanline payload of a plausible screenshot is far
# below this; anything larger is `unsupported` (never a guess).
PNG_MAX_DECODE_BYTES = 128 * 1024 * 1024


def _paeth_predictor(a, b, c):
    """Paeth predictor (W3C PNG Third Edition §9.4)."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _blank_heuristic(pixels):
    """Conservative uniformity heuristic, applied to reconstructed pixel bytes
    (scanline filter bytes removed): at most two distinct byte values with one
    value covering >95% of the bytes means a uniform (blank) image."""
    if not pixels:
        return False
    counts = {}
    for byte in pixels:
        counts[byte] = counts.get(byte, 0) + 1
    return len(counts) <= 2 and (max(counts.values()) / len(pixels)) > 0.95


def decode_png(data):
    """Decodes the supported PNG subset and validates the structure.

    Returns a dict with `status`:
      * "ok": IHDR/chunks/scanline payload are valid, the scanline filters
        0..4 were reversed, and width/height/color_type/bit_depth/pixels
        (reconstructed samples without filter bytes)/blank are set;
      * "invalid": the bytes violate the PNG structure or IHDR/scanline rules
        (truncated chunk, chunk CRC mismatch, non-consecutive IDAT chunks,
        illegal field value, filter byte outside 0..4, scanline payload size
        mismatch, broken zlib stream);
      * "unsupported": legal PNG data outside the verified screenshot subset
        (other color type/bit depth, interlaced, oversized). Callers must treat
        it as unverifiable: never non-blank, never PASS.
    """
    size = len(data)
    if size < 8 or data[:8] != PNG_MAGIC:
        return {"status": "invalid", "size": size, "detail": "not a PNG (magic)"}
    pos = 8
    header = None
    idat = b""
    idat_state = "none"  # none -> started -> ended; IDAT chunks must be consecutive
    seen_iend = False
    while pos < size:
        if pos + 8 > size:
            return {
                "status": "invalid",
                "size": size,
                "detail": "truncated chunk header at offset %d" % pos,
            }
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        end = pos + 8 + length
        if end + 4 > size:
            return {
                "status": "invalid",
                "size": size,
                "detail": "truncated %s chunk (length %d)"
                % (tag.decode("latin-1"), length),
            }
        # W3C PNG Third Edition §5.5: every chunk carries a CRC over the chunk
        # type and the chunk data (the length field is excluded). Corrupted
        # chunk bytes must fail closed even when the zlib stream and scanlines
        # would still decode.
        expected_crc = struct.unpack(">I", data[end : end + 4])[0]
        actual_crc = zlib.crc32(data[pos + 4 : end]) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            return {
                "status": "invalid",
                "size": size,
                "detail": "chunk CRC mismatch for %s at offset %d"
                % (tag.decode("latin-1"), pos),
            }
        if header is None and tag != b"IHDR":
            return {
                "status": "invalid",
                "size": size,
                "detail": "first chunk is not IHDR",
            }
        if tag == b"IHDR":
            if header is not None:
                return {"status": "invalid", "size": size, "detail": "duplicate IHDR"}
            if length != 13:
                return {
                    "status": "invalid",
                    "size": size,
                    "detail": "IHDR length %d (must be 13)" % length,
                }
            header = struct.unpack(">IIBBBBB", data[pos + 8 : end])
        elif tag == b"IDAT":
            # §5.6 / §11.2.3: multiple IDAT chunks are allowed but must be
            # consecutive; an ancillary chunk between two IDATs is invalid.
            if idat_state == "ended":
                return {
                    "status": "invalid",
                    "size": size,
                    "detail": "non-consecutive IDAT chunks (ancillary chunk "
                    "between IDATs)",
                }
            idat_state = "started"
            idat += data[pos + 8 : end]
        elif tag == b"IEND":
            if length != 0:
                return {
                    "status": "invalid",
                    "size": size,
                    "detail": "IEND length %d (must be 0)" % length,
                }
            idat_state = "ended" if idat_state == "started" else idat_state
            seen_iend = True
            if end + 4 != size:
                return {
                    "status": "invalid",
                    "size": size,
                    "detail": "trailing bytes after IEND",
                }
            break
        elif idat_state == "started":
            # Any non-IDAT chunk ends the IDAT run (§5.6).
            idat_state = "ended"
        pos = end + 4
    if header is None:
        return {"status": "invalid", "size": size, "detail": "missing IHDR"}
    if not seen_iend:
        return {"status": "invalid", "size": size, "detail": "missing IEND"}

    width, height, bit_depth, color_type, compression, filter_method, interlace = header
    if width == 0 or height == 0:
        return {
            "status": "invalid",
            "size": size,
            "detail": "zero width/height in IHDR",
        }
    if color_type not in PNG_LEGAL_COLOR_TYPES:
        return {
            "status": "invalid",
            "size": size,
            "detail": "illegal color type %d" % color_type,
        }
    if bit_depth not in PNG_LEGAL_BIT_DEPTHS[color_type]:
        return {
            "status": "invalid",
            "size": size,
            "detail": "illegal bit depth %d for color type %d" % (bit_depth, color_type),
        }
    if compression != 0:
        return {
            "status": "invalid",
            "size": size,
            "detail": "illegal compression method %d (only 0 is defined)" % compression,
        }
    if filter_method != 0:
        return {
            "status": "invalid",
            "size": size,
            "detail": "illegal filter method %d (only 0 is defined)" % filter_method,
        }
    if interlace not in (0, 1):
        return {
            "status": "invalid",
            "size": size,
            "detail": "illegal interlace method %d" % interlace,
        }
    if color_type not in PNG_SUPPORTED_COLOR_TYPES or bit_depth != 8 or interlace != 0:
        return {
            "status": "unsupported",
            "size": size,
            "width": width,
            "height": height,
            "detail": "unsupported PNG format (unverifiable): color type %d, "
            "bit depth %d, interlace %d" % (color_type, bit_depth, interlace),
        }

    name, channels = PNG_SUPPORTED_COLOR_TYPES[color_type]
    bpp = channels
    stride = width * bpp
    expected = height * (stride + 1)
    if expected > PNG_MAX_DECODE_BYTES:
        return {
            "status": "unsupported",
            "size": size,
            "width": width,
            "height": height,
            "detail": "unsupported PNG size (unverifiable): %d decoded bytes" % expected,
        }
    if not idat:
        return {"status": "invalid", "size": size, "detail": "no IDAT payload"}
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(idat, expected + 1)
        if decompressor.unconsumed_tail or len(raw) > expected:
            return {
                "status": "invalid",
                "size": size,
                "detail": "scanline payload larger than the expected %d bytes" % expected,
            }
        raw += decompressor.flush()
    except zlib.error as exc:
        return {"status": "invalid", "size": size, "detail": "bad IDAT: %s" % exc}
    if not decompressor.eof:
        return {"status": "invalid", "size": size, "detail": "truncated zlib stream"}
    if decompressor.unused_data:
        return {
            "status": "invalid",
            "size": size,
            "detail": "trailing data after the zlib stream",
        }
    if len(raw) != expected:
        return {
            "status": "invalid",
            "size": size,
            "detail": "scanline payload size mismatch: expected %d, got %d "
            "(truncated/excess)" % (expected, len(raw)),
        }

    rows = []
    previous = bytes(stride)
    offset = 0
    for row_index in range(height):
        filter_type = raw[offset]
        if filter_type not in PNG_FILTER_TYPES:
            return {
                "status": "invalid",
                "size": size,
                "detail": "invalid filter type %d at row %d" % (filter_type, row_index),
            }
        row = bytearray(raw[offset + 1 : offset + 1 + stride])
        if filter_type == PNG_FILTER_SUB:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif filter_type == PNG_FILTER_UP:
            for i in range(stride):
                row[i] = (row[i] + previous[i]) & 0xFF
        elif filter_type == PNG_FILTER_AVERAGE:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == PNG_FILTER_PAETH:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                up = previous[i]
                up_left = previous[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth_predictor(left, up, up_left)) & 0xFF
        rows.append(bytes(row))
        previous = rows[-1]
        offset += stride + 1

    pixels = b"".join(rows)
    blank = _blank_heuristic(pixels)
    return {
        "status": "ok",
        "ok": True,
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "format": name,
        "size": size,
        "pixels": pixels,
        "blank": blank,
        "detail": "%s %dx%d, %d bytes%s" % (name, width, height, size, " BLANK" if blank else ""),
    }


def read_png(path):
    """Reads a capture file and decodes the supported PNG subset.

    Returns decode_png()'s dict (plus ok/size); `ok` is true only for a fully
    validated and decoded supported image. Invalid or unsupported files are
    fail-closed: the caller must never treat them as non-blank or PASS.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return {"status": "invalid", "ok": False, "detail": "unreadable: %s" % exc}
    result = decode_png(data)
    result.setdefault("size", len(data))
    result["ok"] = result["status"] == "ok"
    return result


def parse_watcher(path):
    """Parses the app-identity watcher evidence.

    09:03 UTC Gate C watcher evidence contract (main-owner decision):
      * at least one parseable raw `sample ts=<ms> count=<n>` line and exactly
        one complete `final max=<n> nonzero_samples=<n>` line; all values must
        be non-negative integers and the sample timestamps strictly increasing;
      * missing raw samples or a missing final line -> the check is
        NOT-EVALUATED (insufficient evidence, non-zero exit; final-only
        evidence can never PASS);
      * malformed lines, duplicate final lines, negative values, non-monotonic
        timestamps and any sample/final disagreement (max or non-zero count)
        -> CONTRADICTION;
      * the raw samples stay authoritative: a final max=0 can never mask a
        count>0 sample.

    Returns a dict with readable/sample_lines/samples/sample_malformed/
    sample_max/final_lines/final_max/final_malformed/max/nonzero_samples/
    sample_nonzero_samples/timestamps_monotonic/consistent/malformed/
    first_nonzero_index/contradiction_reason/missing_reason so the raw evidence
    and the exact verdict survive into the report.
    """
    sample_timestamps = []
    sample_counts = []
    sample_lines = 0
    sample_malformed = 0
    final_lines = 0
    final_values = []
    final_malformed = 0
    negative = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("sample "):
                    sample_lines += 1
                    fields = dict(
                        part.split("=", 1) for part in line.split()[1:] if "=" in part
                    )
                    if "ts" not in fields or "count" not in fields:
                        sample_malformed += 1
                        continue
                    try:
                        timestamp = int(fields["ts"])
                        count = int(fields["count"])
                    except (TypeError, ValueError):
                        sample_malformed += 1
                        continue
                    if timestamp < 0 or count < 0:
                        negative = True
                    sample_timestamps.append(timestamp)
                    sample_counts.append(count)
                elif line.startswith("final "):
                    final_lines += 1
                    fields = dict(
                        part.split("=", 1) for part in line.split()[1:] if "=" in part
                    )
                    try:
                        final_max = int(fields["max"])
                        final_nonzero = int(fields["nonzero_samples"])
                    except (KeyError, TypeError, ValueError):
                        final_malformed += 1
                        continue
                    if final_max < 0 or final_nonzero < 0:
                        negative = True
                    final_values.append((final_max, final_nonzero))
    except OSError:
        return {
            "readable": False,
            "sample_lines": 0,
            "samples": 0,
            "sample_malformed": 0,
            "sample_max": None,
            "final_lines": 0,
            "final_max": None,
            "final_malformed": 0,
            "max": None,
            "nonzero_samples": None,
            "sample_nonzero_samples": 0,
            "timestamps_monotonic": False,
            "consistent": False,
            "malformed": 0,
            "first_nonzero_index": None,
            "contradiction_reason": "",
            "missing_reason": "watcher samples file not readable",
        }

    reasons = []
    if sample_malformed:
        reasons.append("%d malformed sample line(s)" % sample_malformed)
    if final_malformed:
        reasons.append("%d incomplete/malformed final line(s)" % final_malformed)
    if final_lines > 1:
        reasons.append("%d final lines (exactly one required)" % final_lines)
    if negative:
        reasons.append("negative watcher value(s)")

    timestamps_monotonic = True
    for previous, current in zip(sample_timestamps, sample_timestamps[1:]):
        if current <= previous:
            timestamps_monotonic = False
            break
    if sample_timestamps and not timestamps_monotonic:
        reasons.append("sample timestamps are not strictly increasing")

    sample_max = max(sample_counts) if sample_counts else None
    sample_nonzero = sum(1 for count in sample_counts if count > 0)
    # Exactly one complete final line is required; duplicates or malformed
    # lines above already contribute a contradiction reason.
    final_max = final_values[0][0] if len(final_values) == 1 else None
    final_nonzero = final_values[0][1] if len(final_values) == 1 else None
    if sample_max is not None and final_max is not None and sample_max != final_max:
        reasons.append("sample_max=%s != final max=%s" % (sample_max, final_max))
    if final_nonzero is not None and final_nonzero != sample_nonzero:
        reasons.append(
            "sample nonzero=%d != final nonzero_samples=%s"
            % (sample_nonzero, final_nonzero)
        )

    missing = []
    if sample_lines == 0:
        missing.append("no raw sample lines (final-only evidence never passes)")
    if final_lines == 0:
        missing.append("no final line (max/nonzero_samples missing)")

    present = [value for value in (sample_max, final_max) if value is not None]
    max_seen = max(present) if present else None
    first_nonzero = next(
        (index for index, count in enumerate(sample_counts) if count > 0), None
    )
    contradiction_reason = "; ".join(reasons)
    return {
        "readable": True,
        "sample_lines": sample_lines,
        "samples": len(sample_counts),
        "sample_malformed": sample_malformed,
        "sample_max": sample_max,
        "final_lines": final_lines,
        "final_max": final_max,
        "final_malformed": final_malformed,
        "max": max_seen,
        "nonzero_samples": final_nonzero,
        "sample_nonzero_samples": sample_nonzero,
        "timestamps_monotonic": timestamps_monotonic,
        "consistent": not contradiction_reason,
        "malformed": sample_malformed + final_malformed,
        "first_nonzero_index": first_nonzero,
        "contradiction_reason": contradiction_reason,
        "missing_reason": "; ".join(missing),
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
    #
    # 09:03 UTC watcher evidence contract: raw samples and exactly one complete
    # final line are mandatory (missing evidence -> NOT-EVALUATED, non-zero
    # exit), and any malformed/duplicate/negative/non-monotonic/inconsistent
    # form is a CONTRADICTION. A final-only file has zero raw samples and can
    # therefore never PASS.
    if not watcher["readable"]:
        add("no-visible-window", "NOT-EVALUATED", watcher["missing_reason"])
    elif watcher["contradiction_reason"]:
        add(
            "no-visible-window",
            "CONTRADICTION",
            "watcher evidence invalid: %s" % watcher["contradiction_reason"],
        )
    elif watcher["missing_reason"]:
        add("no-visible-window", "NOT-EVALUATED", watcher["missing_reason"])
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
            "raw_samples=%d sample_max=0 final_max=0 nonzero_samples=%s "
            "timestamps_monotonic=%s"
            % (
                watcher["samples"],
                watcher["nonzero_samples"],
                watcher["timestamps_monotonic"],
            ),
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
        unsupported = []
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
            if png.get("status") == "unsupported":
                # Legal PNG outside the verified screenshot subset: never guess
                # non-blank and never PASS; the case stays unverifiable.
                unsupported.append(
                    "%s unverifiable PNG: %s"
                    % (os.path.basename(path), png.get("detail"))
                )
            elif png.get("status") != "ok":
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
        if unsupported:
            add("capture-%s-integrity" % name, "NOT-EVALUATED", "; ".join(unsupported))
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
        "- readable=%s sample_lines=%s samples=%s sample_max=%s final_lines=%s "
        "final_max=%s max=%s timestamps_monotonic=%s consistent=%s malformed=%s "
        "nonzero(sample)=%s nonzero(final)=%s first_nonzero_index=%s"
        % (
            watcher["readable"],
            watcher["sample_lines"],
            watcher["samples"],
            watcher["sample_max"],
            watcher["final_lines"],
            watcher["final_max"],
            watcher["max"],
            watcher["timestamps_monotonic"],
            watcher["consistent"],
            watcher["malformed"],
            watcher["sample_nonzero_samples"],
            watcher["nonzero_samples"],
            watcher["first_nonzero_index"],
        )
    )
    if watcher["missing_reason"]:
        lines.append("- missing: %s" % watcher["missing_reason"])
    if watcher["contradiction_reason"]:
        lines.append("- invalid: %s" % watcher["contradiction_reason"])
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

def _png_chunk(tag, data, crc_xor=0):
    """Chunk bytes with the standard CRC over type + data (§5.5); `crc_xor`
    lets negative fixtures write a deliberately wrong CRC."""
    crc = (zlib.crc32(tag + data) ^ crc_xor) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _forward_filter_row(filter_type, row, previous, bpp):
    """Forward scanline filter (encoder direction, W3C PNG §9.2 formulas).

    The fixture encoder must stay independent from decode_png(): it subtracts
    the predictor where the decoder adds it. The known-answer fixtures pin the
    exact filtered bytes computed from the standard, so a predictor bug shared
    by both directions cannot cancel out unnoticed.
    """
    if filter_type == PNG_FILTER_NONE:
        return bytes(row)
    out = bytearray(len(row))
    for i in range(len(row)):
        left = row[i - bpp] if i >= bpp else 0
        up = previous[i] if previous is not None else 0
        up_left = previous[i - bpp] if (previous is not None and i >= bpp) else 0
        if filter_type == PNG_FILTER_SUB:
            predictor = left
        elif filter_type == PNG_FILTER_UP:
            predictor = up
        elif filter_type == PNG_FILTER_AVERAGE:
            predictor = (left + up) >> 1
        elif filter_type == PNG_FILTER_PAETH:
            predictor = _paeth_predictor(left, up, up_left)
        else:
            raise ValueError("filter type must be 0..4, got %r" % (filter_type,))
        out[i] = (row[i] - predictor) & 0xFF
    return bytes(out)


def _forward_filtered_rows(pixels, width, bpp, filters):
    """Raw scanline stream (filter byte + filtered bytes per row)."""
    stride = width * bpp
    height = len(pixels) // stride if (width and bpp) else 0
    out = bytearray()
    previous = None
    for y in range(height):
        row = pixels[y * stride : (y + 1) * stride]
        if isinstance(filters, (list, tuple)):
            filter_type = filters[y % len(filters)]
        else:
            filter_type = filters
        out.append(filter_type)
        out += _forward_filter_row(filter_type, row, previous, bpp)
        previous = row
    return bytes(out)


def _encode_png(pixels, width, bpp, filters, *, color_type=None, bit_depth=8,
                compression=0, filter_method=0, interlace=0,
                raw_override=None, idat_override=None, idat_chunks=1,
                bad_crc_tags=(), ancillary_pre=(), ancillary_mid=(),
                ancillary_post=()):
    """Independent forward-filter PNG encoder (fixture/test helper only).

    `pixels` are flat row-major samples; `filters` is one filter type for every
    row or a per-row sequence. Negative fixtures may inject `raw_override`
    (malformed scanline stream) or `idat_override` (broken zlib payload),
    override IHDR fields, split the zlib stream into `idat_chunks` adjacent
    IDAT chunks, place `(tag, data)` ancillary chunks before / between / after
    the IDAT run, or write a deliberately wrong CRC for the tags listed in
    `bad_crc_tags`. This helper never calls decode_png/read_png.
    """
    color_type = color_type if color_type is not None else (6 if bpp == 4 else 2)
    height = len(pixels) // (width * bpp) if (width and bpp) else 0
    if idat_override is not None:
        idat = idat_override
    else:
        raw = (
            raw_override
            if raw_override is not None
            else _forward_filtered_rows(pixels, width, bpp, filters)
        )
        idat = zlib.compress(raw)
    if idat_chunks <= 1 or len(idat) < idat_chunks:
        idat_parts = [idat]
    else:
        part_size = len(idat) // idat_chunks
        idat_parts = [
            idat[index * part_size : (index + 1) * part_size]
            for index in range(idat_chunks - 1)
        ]
        idat_parts.append(idat[(idat_chunks - 1) * part_size :])
    if ancillary_mid and len(idat_parts) < 2:
        raise ValueError("ancillary_mid requires idat_chunks >= 2")

    def chunk(tag, data):
        return _png_chunk(
            tag, data, crc_xor=0xFFFFFFFF if tag in bad_crc_tags else 0
        )

    ihdr = struct.pack(
        ">IIBBBBB", width, height, bit_depth, color_type, compression,
        filter_method, interlace,
    )
    out = bytearray(PNG_MAGIC)
    out += chunk(b"IHDR", ihdr)
    for tag, data in ancillary_pre:
        out += chunk(tag, data)
    for index, part in enumerate(idat_parts):
        out += chunk(b"IDAT", part)
        if index == 0:
            for tag, data in ancillary_mid:
                out += chunk(tag, data)
    for tag, data in ancillary_post:
        out += chunk(tag, data)
    out += chunk(b"IEND", b"")
    return bytes(out)


def _flip_last_chunk_crc(png, tag):
    """Test-side walker: flips the final CRC byte of the last chunk with `tag`.

    Independent fixture mutator (plain length walk, no decode_png logic), used
    to corrupt exactly one chunk CRC without touching its data.
    """
    pos = 8
    crc_offset = None
    while pos + 8 <= len(png):
        length = struct.unpack(">I", png[pos : pos + 4])[0]
        if png[pos + 4 : pos + 8] == tag:
            crc_offset = pos + 8 + length
        pos += 12 + length
    if crc_offset is None:
        raise ValueError("no %r chunk to corrupt" % (tag,))
    flipped = bytearray(png)
    flipped[crc_offset] ^= 0xFF
    return bytes(flipped)


class _RawPng:
    """Marks prebuilt PNG file bytes in a capture plan (negative fixtures).

    Plain `bytes`/`bytearray` values in a capture plan are flat pixel samples
    that the fixture encoder must filter; this wrapper carries already-encoded
    PNG file bytes so the two can never be confused.
    """

    def __init__(self, data):
        self.data = bytes(data)


def _png_bytes(pixels, width=24, bpp=3, filters=None, **kwargs):
    """RGB/RGBA fixture PNG. By default every scanline filter 0..4 is exercised,
    so the production decoder always runs through the forward/inverse pair."""
    if filters is None:
        filters = tuple(range(5))
    return _encode_png(bytes(pixels), width, bpp, filters, **kwargs)


def _pixels(seed, width=24, height=24, bpp=3):
    """Incompressible flat row-major pixels so the fixture PNG stays above the
    suspicious-size threshold; the same seed yields byte-identical captures."""
    import random

    rng = random.Random(seed)
    return rng.randbytes(width * height * bpp)


def _blank_pixels(width=24, height=24, bpp=3, value=255):
    return bytes([value]) * (width * height * bpp)


def build_fixture(root, session=(True, True, True), cases=None, capture_plan=None,
                  watcher=(0, 0), watcher_lines=None, policy=True,
                  page_content=True):
    """Writes a synthetic evidence dir. `capture_plan` maps case name -> list of
    pixel grids (or the string "blank" / "empty"). `watcher` writes a valid
    (sample max, final nonzero) pair; `watcher_lines` writes the given raw
    watcher lines verbatim for contract negatives (takes precedence)."""
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
            elif isinstance(grid, _RawPng):
                # Prebuilt PNG bytes (negative/unsupported-format fixtures).
                data = grid.data
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
    if watcher_lines is not None:
        with open(os.path.join(root, WATCHER_FILE), "w", encoding="utf-8") as handle:
            handle.write("\n".join(watcher_lines) + "\n")
    elif watcher is not None:
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

        # 17. 09:03 UTC watcher evidence contract: raw samples and exactly one
        #     complete final are mandatory; malformed/duplicate/negative/
        #     non-monotonic/inconsistent evidence is a CONTRADICTION; a
        #     final-only file can never PASS.
        def watcher_report(name, lines):
            return evaluate(fixture(name, watcher_lines=lines))

        def watcher_status(report):
            return "%s rc=%s" % (
                _status(report, "no-visible-window"),
                evidence_exit_code(report),
            )

        report = watcher_report("watcher-final-only", ["final max=0 nonzero_samples=0"])
        check(
            "final-only watcher (zero raw samples) is NOT-EVALUATED, rc=3",
            _status(report, "no-visible-window") == "NOT-EVALUATED"
            and evidence_exit_code(report) == 3,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-no-final", ["sample ts=1 count=0", "sample ts=2 count=0"]
        )
        check(
            "raw samples without a final line are NOT-EVALUATED, rc=3",
            _status(report, "no-visible-window") == "NOT-EVALUATED"
            and evidence_exit_code(report) == 3,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-malformed-sample",
            ["sample ts=1 count=x", "sample ts=2 count=0",
             "final max=0 nonzero_samples=0"],
        )
        check(
            "malformed sample line is a CONTRADICTION, rc=1",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-sample-without-ts",
            ["sample count=0", "sample ts=2 count=0",
             "final max=0 nonzero_samples=0"],
        )
        check(
            "sample line without ts is a CONTRADICTION, rc=1",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-duplicate-final",
            ["sample ts=1 count=0", "sample ts=2 count=0",
             "final max=0 nonzero_samples=0", "final max=0 nonzero_samples=0"],
        )
        check(
            "duplicate final lines are a CONTRADICTION, rc=1",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-incomplete-final",
            ["sample ts=1 count=0", "sample ts=2 count=0", "final max=0"],
        )
        check(
            "incomplete final (missing nonzero_samples) is a CONTRADICTION, rc=1",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-negative-sample",
            ["sample ts=1 count=-1", "sample ts=2 count=0",
             "final max=0 nonzero_samples=0"],
        )
        check(
            "negative sample count is a CONTRADICTION, rc=1",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-negative-final",
            ["sample ts=1 count=0", "sample ts=2 count=0",
             "final max=-1 nonzero_samples=0"],
        )
        check(
            "negative final max is a CONTRADICTION, rc=1",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-non-monotonic",
            ["sample ts=2 count=0", "sample ts=1 count=0",
             "final max=0 nonzero_samples=0"],
        )
        check(
            "non-increasing sample timestamps are a CONTRADICTION, rc=1",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-nonzero-mismatch",
            ["sample ts=1 count=1", "sample ts=2 count=0",
             "final max=1 nonzero_samples=0"],
        )
        check(
            "final nonzero_samples disagreeing with raw samples is a CONTRADICTION",
            _status(report, "no-visible-window") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            watcher_status(report),
        )
        report = watcher_report(
            "watcher-valid-raw",
            ["sample ts=100 count=0", "sample ts=110 count=0",
             "sample ts=120 count=0", "final max=0 nonzero_samples=0"],
        )
        check(
            "real raw samples max=0 with a complete final PASSes the watcher check",
            _status(report, "no-visible-window") == "PASS"
            and evidence_exit_code(report) == 0,
            watcher_status(report),
        )

        # 18. PNG scanline filters 0..4: the fixture encoder implements the
        #     forward formulas independently; the known-answer literals pin the
        #     exact filtered bytes computed from W3C PNG Third Edition §9.2, so
        #     a predictor bug shared with the decoder cannot cancel out.
        kat_pixels = bytes([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120])
        kat_filtered_rows = {
            0: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120],
            1: [10, 20, 30, 30, 30, 30, 70, 80, 90, 30, 30, 30],
            2: [10, 20, 30, 40, 50, 60, 60, 60, 60, 60, 60, 60],
            3: [10, 20, 30, 35, 40, 45, 65, 70, 75, 45, 45, 45],
            4: [10, 20, 30, 30, 30, 30, 60, 60, 60, 30, 30, 30],
        }
        for filter_type, filtered in kat_filtered_rows.items():
            expected_raw = (
                bytes([filter_type])
                + bytes(filtered[:6])
                + bytes([filter_type])
                + bytes(filtered[6:])
            )
            actual_raw = _forward_filtered_rows(
                kat_pixels, 2, 3, [filter_type, filter_type]
            )
            check(
                "KAT filter %d forward bytes match the W3C formula" % filter_type,
                actual_raw == expected_raw,
                "raw=%s expected=%s" % (list(actual_raw), list(expected_raw)),
            )
            decoded = decode_png(
                _encode_png(kat_pixels, 2, 3, [filter_type, filter_type])
            )
            check(
                "KAT filter %d decodes back to the exact pixels" % filter_type,
                decoded.get("status") == "ok" and decoded.get("pixels") == kat_pixels,
                decoded.get("detail"),
            )

        # 19. Uniform images must be blank for every filter and both supported
        #     color types; non-uniform pixels must never be misjudged blank.
        for color_type, bpp, name in ((2, 3, "RGB"), (6, 4, "RGBA")):
            uniform = _blank_pixels(bpp=bpp)
            for filter_type in range(5):
                decoded = decode_png(_encode_png(uniform, 24, bpp, filter_type))
                check(
                    "uniform %s with filter %d is blank after unfiltering"
                    % (name, filter_type),
                    decoded.get("status") == "ok"
                    and decoded.get("blank") is True
                    and decoded.get("pixels") == uniform,
                    decoded.get("detail"),
                )
            varied = _pixels(100 + color_type, bpp=bpp)
            for filter_type in range(5):
                decoded = decode_png(_encode_png(varied, 24, bpp, filter_type))
                check(
                    "non-uniform %s with filter %d is not blank" % (name, filter_type),
                    decoded.get("status") == "ok"
                    and decoded.get("blank") is False
                    and decoded.get("pixels") == varied,
                    decoded.get("detail"),
                )

        # 20. Filter bytes are never pixels: the raw stream of a uniform image
        #     with mixed filters has >2 distinct byte values, yet the decoded
        #     image must still be blank (the old byte-frequency-on-raw heuristic
        #     would have missed this blank capture).
        white_rgba = _blank_pixels(bpp=4)
        mixed_filters = tuple(range(5))
        mixed_raw = _forward_filtered_rows(white_rgba, 24, 4, mixed_filters)
        decoded = decode_png(_encode_png(white_rgba, 24, 4, mixed_filters))
        check(
            "blank detection ignores filter bytes (raw has >2 distinct bytes)",
            decoded.get("status") == "ok"
            and decoded.get("blank") is True
            and len(set(mixed_raw)) > 2,
            "distinct_raw=%d blank=%s" % (len(set(mixed_raw)), decoded.get("blank")),
        )
        near_uniform = bytearray(_blank_pixels(bpp=3, value=0x20))
        near_uniform[:28] = b"\x21" * 28
        decoded = decode_png(_encode_png(bytes(near_uniform), 24, 3, 1))
        check(
            "near-uniform pixels with the Sub filter stay blank",
            decoded.get("status") == "ok" and decoded.get("blank") is True,
            decoded.get("detail"),
        )
        checkerboard = bytes(
            0xFF if ((pixel % 24) + (pixel // 24)) % 2 == 0 else 0x00
            for pixel in range(24 * 24)
            for _ in range(3)
        )
        decoded = decode_png(_encode_png(checkerboard, 24, 3, 4))
        check(
            "two-value checkerboard with the Paeth filter is not blank",
            decoded.get("status") == "ok" and decoded.get("blank") is False,
            decoded.get("detail"),
        )

        # 21. Fail-closed format handling: legal-but-unsupported PNGs are
        #     unverifiable (never non-blank, never PASS); malformed data is
        #     invalid (contradiction).
        unsupported_cases = [
            ("16-bit RGB", _encode_png(_pixels(201), 24, 3, 0, bit_depth=16)),
            ("grayscale 8-bit", _encode_png(_pixels(202), 24, 1, 0, color_type=0)),
            ("palette 8-bit", _encode_png(_pixels(203), 24, 1, 0, color_type=3)),
            ("interlaced RGBA", _encode_png(_pixels(204, bpp=4), 24, 4, 0, interlace=1)),
        ]
        for label, data in unsupported_cases:
            decoded = decode_png(data)
            check(
                "unsupported PNG format (%s) is unverifiable, never non-blank/PASS"
                % label,
                decoded.get("status") == "unsupported",
                decoded.get("detail"),
            )
        valid_rgb = _encode_png(_pixels(205), 24, 3, 0)
        invalid_cases = [
            ("illegal bit depth 3 for RGB",
             _encode_png(_pixels(206), 24, 3, 0, bit_depth=3)),
            ("illegal color type 7",
             _encode_png(_pixels(207), 24, 3, 0, color_type=7)),
            ("illegal compression method 1",
             _encode_png(_pixels(208), 24, 3, 0, compression=1)),
            ("illegal filter method 1",
             _encode_png(_pixels(209), 24, 3, 0, filter_method=1)),
            ("illegal interlace method 2",
             _encode_png(_pixels(210), 24, 3, 0, interlace=2)),
            ("zero width",
             _encode_png(b"", 0, 3, 0)),
            ("invalid filter byte 5",
             _encode_png(_pixels(211), 24, 3, 0,
                         raw_override=(b"\x05" + bytes(24 * 3)) * 24)),
            ("truncated scanline payload",
             _encode_png(_pixels(212), 24, 3, 0,
                         raw_override=_forward_filtered_rows(_pixels(212), 24, 3, 0)[:-1])),
            ("excess scanline payload",
             _encode_png(_pixels(213), 24, 3, 0,
                         raw_override=_forward_filtered_rows(_pixels(213), 24, 3, 0) + b"\x00")),
            ("broken zlib stream",
             _encode_png(_pixels(214), 24, 3, 0, idat_override=b"not-a-zlib-stream")),
            ("truncated chunk (missing CRC)", valid_rgb[:-4]),
        ]
        for label, data in invalid_cases:
            decoded = decode_png(data)
            check(
                "invalid PNG (%s) is rejected as invalid" % label,
                decoded.get("status") == "invalid",
                decoded.get("detail"),
            )

        # 22. Verdict integration: unsupported captures leave the case
        #     unverifiable (non-PASS, non-zero exit), invalid captures are a
        #     contradiction, and a blank image with mixed filters is still a
        #     contradiction end to end.
        unsupported_png = _encode_png(_pixels(220), 24, 3, 0, bit_depth=16)
        report = evaluate(
            fixture(
                "png-unsupported",
                capture_plan={
                    "static": [_RawPng(unsupported_png), _RawPng(unsupported_png)],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "unsupported PNG format makes capture integrity non-PASS (unverifiable)",
            _status(report, "capture-static-integrity") == "NOT-EVALUATED"
            and evidence_exit_code(report) != 0,
            _status(report, "capture-static-integrity"),
        )
        invalid_png = _encode_png(
            _pixels(221), 24, 3, 0,
            raw_override=(b"\x05" + bytes(24 * 3)) * 24,
        )
        report = evaluate(
            fixture(
                "png-invalid",
                capture_plan={
                    "static": [_RawPng(invalid_png), _RawPng(invalid_png)],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "invalid PNG makes capture integrity a CONTRADICTION",
            _status(report, "capture-static-integrity") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            _status(report, "capture-static-integrity"),
        )
        report = evaluate(
            fixture(
                "blank-mixed-filters",
                capture_plan={
                    "static": [
                        _RawPng(_png_bytes(_blank_pixels(), filters=mixed_filters)),
                        _RawPng(_png_bytes(_blank_pixels(), filters=(4, 3, 2, 1, 0))),
                    ],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "blank capture with mixed filters is still a CONTRADICTION",
            _status(report, "capture-static-integrity") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            _status(report, "capture-static-integrity"),
        )

        # 23. Chunk CRC (§5.5) and IDAT continuity (§5.6 / §11.2.3): corrupted
        #     chunk bytes must fail closed even when the zlib stream still
        #     decodes, and multiple IDAT chunks must be adjacent.
        base_pixels = _pixels(230)
        crc_cases = [
            ("IHDR", _encode_png(base_pixels, 24, 3, 0, bad_crc_tags=(b"IHDR",))),
            ("IDAT", _encode_png(base_pixels, 24, 3, 0, bad_crc_tags=(b"IDAT",))),
            ("IDAT (split stream)", _encode_png(base_pixels, 24, 3, 0,
                                                idat_chunks=2,
                                                bad_crc_tags=(b"IDAT",))),
            ("IEND", _encode_png(base_pixels, 24, 3, 0, bad_crc_tags=(b"IEND",))),
            ("ancillary tEXt", _encode_png(base_pixels, 24, 3, 0,
                                           ancillary_pre=((b"tEXt", b"Comment\x00x"),),
                                           bad_crc_tags=(b"tEXt",))),
        ]
        for label, data in crc_cases:
            decoded = decode_png(data)
            check(
                "CRC mismatch (%s) is invalid" % label,
                decoded.get("status") == "invalid"
                and "CRC mismatch" in (decoded.get("detail") or ""),
                decoded.get("detail"),
            )
        split_valid = _encode_png(base_pixels, 24, 3, 0, idat_chunks=2)
        decoded = decode_png(_flip_last_chunk_crc(split_valid, b"IDAT"))
        check(
            "CRC mismatch (second IDAT of a split stream) is invalid",
            decoded.get("status") == "invalid"
            and "CRC mismatch" in (decoded.get("detail") or ""),
            decoded.get("detail"),
        )

        uniform = _blank_pixels()
        decoded = decode_png(
            _encode_png(uniform, 24, 3, tuple(range(5)), idat_chunks=3)
        )
        check(
            "adjacent IDAT chunks decode as one stream (uniform stays blank)",
            decoded.get("status") == "ok"
            and decoded.get("blank") is True
            and decoded.get("pixels") == uniform,
            decoded.get("detail"),
        )
        decoded = decode_png(_encode_png(base_pixels, 24, 3, 2, idat_chunks=3))
        check(
            "adjacent IDAT chunks decode as one stream (non-uniform stays non-blank)",
            decoded.get("status") == "ok"
            and decoded.get("blank") is False
            and decoded.get("pixels") == base_pixels,
            decoded.get("detail"),
        )
        decoded = decode_png(
            _encode_png(base_pixels, 24, 3, 0, idat_chunks=2,
                        ancillary_mid=((b"tEXt", b"Comment\x00between"),))
        )
        check(
            "ancillary chunk between IDAT chunks is invalid",
            decoded.get("status") == "invalid"
            and "non-consecutive IDAT" in (decoded.get("detail") or ""),
            decoded.get("detail"),
        )
        decoded = decode_png(
            _encode_png(base_pixels, 24, 3, 2,
                        ancillary_pre=((b"tEXt", b"Comment\x00pre"),),
                        ancillary_post=((b"tEXt", b"Comment\x00post"),))
        )
        check(
            "ancillary chunks before/after IDAT decode normally (non-uniform)",
            decoded.get("status") == "ok"
            and decoded.get("blank") is False
            and decoded.get("pixels") == base_pixels,
            decoded.get("detail"),
        )
        decoded = decode_png(
            _encode_png(uniform, 24, 3, 4,
                        ancillary_pre=((b"tEXt", b"Comment\x00pre"),),
                        ancillary_post=((b"tEXt", b"Comment\x00post"),))
        )
        check(
            "ancillary chunks before/after IDAT decode normally (uniform blank)",
            decoded.get("status") == "ok"
            and decoded.get("blank") is True
            and decoded.get("pixels") == uniform,
            decoded.get("detail"),
        )

        bad_idat_crc = _encode_png(_pixels(231), 24, 3, 0, bad_crc_tags=(b"IDAT",))
        report = evaluate(
            fixture(
                "png-bad-chunk-crc",
                capture_plan={
                    "static": [_RawPng(bad_idat_crc), _RawPng(bad_idat_crc)],
                    "dynamic": [_pixels(2), _pixels(3)],
                    "canvas": [_pixels(4), _pixels(5)],
                    "long": [_pixels(6), _pixels(7), _pixels(8)],
                },
            )
        )
        check(
            "corrupted IDAT CRC makes capture integrity a CONTRADICTION",
            _status(report, "capture-static-integrity") == "CONTRADICTION"
            and evidence_exit_code(report) == 1,
            _status(report, "capture-static-integrity"),
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
