#!/usr/bin/env bash
# Lightweight regression tests for the Browser CLI scripts.
#
# No network, no build, no browser/UI: only bash -n, --help, --dry-run, temp-dir
# simulations and read-only checks. Safe to run any time.
#
# Usage: tests/run-script-tests.sh
# Exit code: 0 = all passed, 1 = at least one failure.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$REPO_ROOT/scripts"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$*"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$*"; }

# expect_rc <want: 0|nonzero> <description> -- <cmd...>
expect_rc() {
  local want="$1"; shift
  local desc="$1"; shift
  [ "${1:-}" = "--" ] && shift
  "$@" >"$TMP/out.txt" 2>&1
  local rc=$?
  if { [ "$want" = "0" ] && [ "$rc" -eq 0 ]; } || { [ "$want" = "nonzero" ] && [ "$rc" -ne 0 ]; }; then
    ok "$desc (rc=$rc)"
  else
    bad "$desc (want=$want rc=$rc)"
    sed 's/^/      /' "$TMP/out.txt" | head -4
  fi
}

# expect_fail_msg <description> <pattern> -- <cmd...>
expect_fail_msg() {
  local desc="$1"; shift
  local pat="$1"; shift
  [ "${1:-}" = "--" ] && shift
  "$@" >"$TMP/out.txt" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ] && grep -q "$pat" "$TMP/out.txt"; then
    ok "$desc (rc=$rc, matched: $pat)"
  else
    bad "$desc (rc=$rc, pattern not matched: $pat)"
    sed 's/^/      /' "$TMP/out.txt" | head -6
  fi
}

make_fake_checkout() { # <dir> : fake git repo + .gclient (tip-like HEAD)
  local d="$1"
  mkdir -p "$d/src"
  git -C "$d/src" init -q
  echo "fake" > "$d/src/file.txt"
  git -C "$d/src" -c user.name=t -c user.email=t@example.com add file.txt
  git -C "$d/src" -c user.name=t -c user.email=t@example.com commit -q -m init
  : > "$d/.gclient"
}

echo "== 1. syntax (bash -n) =="
for f in "$SCRIPTS"/*.sh "$SCRIPTS"/lib/*.sh; do
  if bash -n "$f" 2>"$TMP/out.txt"; then ok "bash -n $(basename "$f")"; else bad "bash -n $f"; sed 's/^/      /' "$TMP/out.txt"; fi
done

echo "== 2. --help =="
for s in preflight checkout configure build verify-phase1; do
  expect_rc 0 "$s --help" -- "$SCRIPTS/$s.sh" --help
done

echo "== 3. --dry-run on a simulated fresh host =="
FRESH="$TMP/fresh/chromium"
for s in preflight checkout configure build verify-phase1; do
  expect_rc 0 "$s --dry-run (fresh host)" -- env CHROMIUM_ROOT="$FRESH" "$SCRIPTS/$s.sh" --dry-run
done

echo "== 4. checkout dry-run shows pinned revision + revision-aware sync =="
OUT="$(env CHROMIUM_ROOT="$FRESH" "$SCRIPTS/checkout.sh" --dry-run 2>&1)"
if printf '%s' "$OUT" | grep -q 'revision src@7d0f28c30d1c'; then ok "dry-run shows gclient sync --revision src@<VERSION rev>"; else bad "dry-run missing revision-aware gclient sync"; fi
if printf '%s' "$OUT" | grep -q 'with_proxy'; then ok "dry-run shows with_proxy on network calls"; else bad "dry-run missing with_proxy"; fi
if printf '%s' "$OUT" | grep -q 'checkout --detach 7d0f28c30d1c'; then ok "dry-run shows checkout --detach <VERSION rev>"; else bad "dry-run missing checkout --detach <VERSION rev>"; fi

echo "== 5. verify-phase1 dry-run documents strict HEAD rule =="
OUT="$(env CHROMIUM_ROOT="$FRESH" "$SCRIPTS/verify-phase1.sh" --dry-run 2>&1)"
if printf '%s' "$OUT" | grep -q 'HEAD mismatch is FAIL by default'; then ok "dry-run documents HEAD mismatch FAIL"; else bad "dry-run does not document HEAD mismatch FAIL"; fi

echo "== 6. runtime disk breaker hard floor (25 GiB) =="
expect_fail_msg "DISK_MIN_GIB=24 rejected" "hard floor" -- env DISK_MIN_GIB=24 "$SCRIPTS/preflight.sh" --dry-run
expect_fail_msg "DISK_MIN_GIB=0 DISK_MIN_FRESH_GIB=0 rejected" "hard floor" -- env DISK_MIN_GIB=0 DISK_MIN_FRESH_GIB=0 "$SCRIPTS/preflight.sh" --dry-run
expect_fail_msg "DISK_MIN_GIB=abc rejected" "integer" -- env DISK_MIN_GIB=abc "$SCRIPTS/preflight.sh" --dry-run
expect_fail_msg "DISK_MIN_FRESH_GIB=24 (< runtime) rejected" "must be >= DISK_MIN_GIB" -- env DISK_MIN_FRESH_GIB=24 "$SCRIPTS/preflight.sh" --dry-run
expect_fail_msg "DISK_MIN_GIB=30 DISK_MIN_FRESH_GIB=29 rejected" "must be >= DISK_MIN_GIB" -- env DISK_MIN_GIB=30 DISK_MIN_FRESH_GIB=29 "$SCRIPTS/checkout.sh" --dry-run
expect_rc 0 "DISK_MIN_GIB=25 DISK_MIN_FRESH_GIB=65 accepted" -- env DISK_MIN_GIB=25 DISK_MIN_FRESH_GIB=65 "$SCRIPTS/preflight.sh" --dry-run
expect_rc 0 "DISK_MIN_GIB=30 (raised) accepted" -- env DISK_MIN_GIB=30 DISK_MIN_FRESH_GIB=65 "$SCRIPTS/checkout.sh" --dry-run
expect_rc 0 "DISK_MIN_GIB=80 DISK_MIN_FRESH_GIB=120 accepted" -- env DISK_MIN_GIB=80 DISK_MIN_FRESH_GIB=120 "$SCRIPTS/build.sh" --dry-run

echo "== 7. build.sh has no watchdog bypass =="
expect_rc nonzero "build.sh --no-watchdog rejected" -- "$SCRIPTS/build.sh" --no-watchdog
if "$SCRIPTS/build.sh" --help | grep -qi "no-watchdog"; then bad "help advertises --no-watchdog"; else ok "help does not advertise --no-watchdog"; fi

echo "== 8. checkout --resume rejects HEAD != VERSION before network =="
FAKE="$TMP/fake/chromium"
make_fake_checkout "$FAKE"
FAKE_HEAD="$(git -C "$FAKE/src" rev-parse HEAD)"
expect_fail_msg "resume on tip-like checkout rejected" "requires checkout HEAD == chromium/VERSION" -- env CHROMIUM_ROOT="$FAKE" "$SCRIPTS/checkout.sh" --resume
env CHROMIUM_ROOT="$FAKE" "$SCRIPTS/checkout.sh" --resume >"$TMP/out.txt" 2>&1 || true
if grep -q "revision-matched dependency sync" "$TMP/out.txt"; then bad "resume reached gclient sync despite inconsistent HEAD"; else ok "resume stopped before any dependency sync (HEAD=$FAKE_HEAD)"; fi

echo "== 9. verify-phase1 strict HEAD check (fake tip checkout) =="
FAKE2="$TMP/fake2/chromium"
make_fake_checkout "$FAKE2"
mkdir -p "$FAKE2/src/out/Dev"
cp "$REPO_ROOT/chromium/args.gn" "$FAKE2/src/out/Dev/args.gn"
expect_fail_msg "check-only fails on HEAD != VERSION" "reproducibility FAIL" -- env CHROMIUM_ROOT="$FAKE2" "$SCRIPTS/verify-phase1.sh" --check-only
expect_rc 0 "check-only --allow-tip passes as explicit opt-out" -- env CHROMIUM_ROOT="$FAKE2" "$SCRIPTS/verify-phase1.sh" --check-only --allow-tip

echo "== 10. real checkout read-only revision check (skipped if absent) =="
if [ -d "$HOME/dev/chromium/src/.git" ]; then
  expect_rc 0 "verify-phase1 --check-only on the real checkout" -- "$SCRIPTS/verify-phase1.sh" --check-only
else
  printf 'SKIP  no real checkout at ~/dev/chromium/src\n'
fi

echo "== 11. static audit: network calls go through with_proxy =="
UNSAFE="$(grep -nE 'run +(\$FETCH_BIN|\$GCLIENT_BIN|git .*fetch)' "$SCRIPTS"/*.sh | grep -v 'with_proxy' || true)"
if [ -z "$UNSAFE" ]; then ok "no bare fetch/gclient/git-fetch invocations"; else bad "unproxied network invocation(s):"; printf '%s\n' "$UNSAFE" | sed 's/^/      /'; fi
UNSAFE_CURL="$(grep -nE '(^|[^a-zA-Z_])curl ' "$SCRIPTS"/*.sh | grep -v 'with_proxy' || true)"
if [ -z "$UNSAFE_CURL" ]; then ok "no bare curl invocations"; else bad "unproxied curl invocation(s):"; printf '%s\n' "$UNSAFE_CURL" | sed 's/^/      /'; fi

echo
echo "== summary: ${PASS} passed, ${FAIL} failed =="
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
