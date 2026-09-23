#!/usr/bin/env bash
# Browser CLI Phase 1 verification (no visible UI).
#
# Steps:
#   0. config consistency: chromium/args.gn == out/Dev/args.gn; checkout HEAD must
#      equal the chromium/VERSION revision (mismatch is FAIL unless --allow-tip)
#   1. artifacts: product app binary/framework/Info.plist, arm64
#   2. no-UI smoke: `<Product>.app/Contents/MacOS/<Product> --version
#      --use-mock-keychain --user-data-dir=<mktemp -d>` (bounded, no `open`).
#      The app must already carry the pinned Developer ID signature; ad-hoc
#      builds are never launched/tested (see AGENTS.md).
#   3. empty incremental: ninja -> expect "no work to do"
#   4. real C++ incremental: temporary static_assert probe, rebuild, exact restore
#      via `git cat-file blob HEAD:<file>`, rebuild, probe-file diff check
#   5. mandatory product signing + signature acceptance after all builds
#
# The checkout is expected to contain the browser-cli patch series
# (chromium/patches/*.patch, uncommitted by design), so global "git status must
# be clean" checks are scoped to the probe file.
#
# Never uses `git reset --hard` or `git checkout --`; never launches visible UI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"
load_product_identity

usage() {
  cat <<'EOF'
Usage: scripts/verify-phase1.sh [options]

Phase 1 acceptance for an already-built Chromium checkout. No visible UI.

Options:
  --dry-run            print the plan without executing it (read-only)
  --check-only         read-only step 0 only: args.gn parity + HEAD == VERSION
                       revision check, then exit (no smoke, no ninja; does not
                       require a built app)
  --allow-tip          opt out of the HEAD == VERSION revision requirement.
                       Only for tip builds; NOT valid for reproducibility
                       acceptance (the default is FAIL on mismatch).
  --skip-smoke         skip the `--version` smoke
  --skip-incremental   skip the real C++ incremental probe/restore loop
  --probe-file REL     repo-relative .cc file for the incremental probe
                       (default: base/debug/alias.cc)
  --smoke-timeout N    seconds before the smoke is killed (default 180)
  --help               this message

Environment overrides: CHROMIUM_ROOT, OUT_NAME, BUILD_JOBS, DEPOT_TOOLS
EOF
}

SKIP_SMOKE=0
SKIP_INCREMENTAL=0
CHECK_ONLY=0
ALLOW_TIP=0
PROBE_REL="base/debug/alias.cc"
SMOKE_TIMEOUT=180

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    --allow-tip) ALLOW_TIP=1 ;;
    --skip-smoke) SKIP_SMOKE=1 ;;
    --skip-incremental) SKIP_INCREMENTAL=1 ;;
    --probe-file) shift; [ $# -gt 0 ] || die "--probe-file needs a value"; PROBE_REL="$1" ;;
    --smoke-timeout) shift; [ $# -gt 0 ] || die "--smoke-timeout needs a value"; SMOKE_TIMEOUT="$1" ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

APP_DIR="$PRODUCT_APP_DIR"
APP_BIN="$PRODUCT_APP_BIN"
INFO_PLIST="$APP_DIR/Contents/Info.plist"
FRAMEWORK_VERSIONS="$APP_DIR/Contents/Frameworks/$BROWSER_CLI_PRODUCT_NAME Framework.framework/Versions"
FRAMEWORK_BIN="$(find "$FRAMEWORK_VERSIONS" -maxdepth 2 -name "$BROWSER_CLI_PRODUCT_NAME Framework" -type f 2>/dev/null | head -1 || true)"
EXPECTED_VERSION="$(awk -F= '$1 == "version" {print $2}' "$BROWSER_CLI_ROOT/chromium/VERSION")"
PINNED_REV="$(pinned_revision || true)"

if [ "$DRY_RUN" = "1" ]; then
  cat <<EOF
[dry-run] verify-phase1 would run:
  0. compare $BROWSER_CLI_ROOT/chromium/args.gn with out/$OUT_NAME/args.gn
     and require checkout HEAD == chromium/VERSION revision ($PINNED_REV);
     HEAD mismatch is FAIL by default (--allow-tip=$ALLOW_TIP opt-out for tip builds)
  1. artifact checks: $APP_DIR (binary, framework, Info.plist=$EXPECTED_VERSION, arm64)
  2. smoke (skipped=$SKIP_SMOKE, timeout=${SMOKE_TIMEOUT}s), only for a
     Developer-ID-signed app (team $BROWSER_CLI_TEAM_ID; ad-hoc is refused):
     "$APP_BIN" --version --use-mock-keychain --user-data-dir=<mktemp -d>
     # no "open"/Dock/Launchpad, no window, no keychain/system dialog, no profile reuse;
     # residual-process check after
  3. empty incremental: ninja -C out/$OUT_NAME -j $BUILD_JOBS chrome  (expect "no work to do")
  4. incremental probe (skipped=$SKIP_INCREMENTAL) on $PROBE_REL:
     append static_assert -> ninja -> count edges -> restore via
     \`git cat-file blob HEAD:$PROBE_REL\` -> verify probe unchanged -> ninja -> expect "no work to do"
  5. mandatory signing + acceptance: scripts/sign-browser-cli.sh && scripts/verify-signature.sh
  check-only mode: $CHECK_ONLY (step 0 only, read-only)
[dry-run] nothing executed
EOF
  exit 0
fi

[ -d "$CHROMIUM_SRC/.git" ] || die "no Chromium checkout at $CHROMIUM_SRC (run scripts/checkout.sh)"
if [ "$CHECK_ONLY" != "1" ]; then
  [ -x "$NINJA" ] || die "ninja not found at $NINJA"
  [ -d "$APP_DIR" ] || die "no built app at $APP_DIR (run scripts/build.sh)"
fi
use_depot_tools_python

mkdir -p "$LOG_DIR"
NOTE_LOG="$LOG_DIR/verify-phase1-$(date +%Y%m%d-%H%M%S).log"
note() { printf '%s\n' "$*" | tee -a "$NOTE_LOG"; }

PROBE_APPLIED=0
TMP_BACKUP=""
SMOKE_PROFILE=""
restore_probe_on_exit() {
  local rc=$?
  if [ "${PROBE_APPLIED:-0}" = "1" ]; then
    warn "trap: restoring $PROBE_REL from git blob HEAD:$PROBE_REL"
    if ! git -C "$CHROMIUM_SRC" cat-file blob "HEAD:$PROBE_REL" > "$CHROMIUM_SRC/$PROBE_REL" 2>/dev/null; then
      if [ -n "$TMP_BACKUP" ] && [ -f "$TMP_BACKUP" ]; then
        cp "$TMP_BACKUP" "$CHROMIUM_SRC/$PROBE_REL" || warn "MANUAL RESTORE NEEDED: copy $TMP_BACKUP to $CHROMIUM_SRC/$PROBE_REL"
      fi
    fi
    PROBE_APPLIED=0
  fi
  if [ -n "$SMOKE_PROFILE" ] && [ -d "$SMOKE_PROFILE" ]; then
    rm -rf "$SMOKE_PROFILE"
  fi
  exit "$rc"
}
trap restore_probe_on_exit EXIT

count_edges() {
  local n
  n="$(grep -cE '^\[[0-9]+/[0-9]+\]' "$1" 2>/dev/null || true)"
  printf '%s' "${n:-0}"
}

ninja_run() { # <logfile> [ninja args...]
  local logf="$1"; shift
  local rc=0
  ( cd "$CHROMIUM_SRC" && "$NINJA" -C "out/$OUT_NAME" -j "$BUILD_JOBS" "$@" ) >"$logf" 2>&1 || rc=$?
  return "$rc"
}

step() { log ""; log "===== $* ====="; }

# ---------------------------------------------------------------- step 0
step "step 0: config consistency"
if diff -q "$BROWSER_CLI_ROOT/chromium/args.gn" "$OUT_DIR/args.gn" >/dev/null 2>&1; then
  note "PASS  args.gn matches chromium/args.gn"
else
  die "out/$OUT_NAME/args.gn differs from chromium/args.gn (re-run scripts/configure.sh)"
fi
HEAD_REV="$(checkout_revision || true)"
note "INFO  checkout HEAD = $HEAD_REV"
if [ -z "$PINNED_REV" ]; then
  if [ "$ALLOW_TIP" = "1" ]; then
    note "WARN  no revision= in chromium/VERSION; tip mode explicitly allowed (NOT valid for acceptance)"
  else
    die "chromium/VERSION has no revision= line; fix it or pass --allow-tip for a non-acceptance tip check"
  fi
elif [ "$HEAD_REV" = "$PINNED_REV" ]; then
  note "PASS  checkout HEAD == chromium/VERSION revision ($PINNED_REV)"
elif [ "$ALLOW_TIP" = "1" ]; then
  note "WARN  HEAD ($HEAD_REV) != chromium/VERSION ($PINNED_REV); tip mode allowed (NOT valid for reproducibility acceptance)"
  if git -C "$CHROMIUM_SRC" rev-parse --verify "${PINNED_REV}^{commit}" >/dev/null 2>&1; then
    note "INFO  pinned revision is also present locally"
  else
    note "INFO  pinned revision is not present locally (expected for a tip checkout)"
  fi
else
  die "HEAD ($HEAD_REV) != chromium/VERSION ($PINNED_REV): reproducibility FAIL. Pin with scripts/checkout.sh --repin; use --allow-tip only for non-acceptance tip builds."
fi

if [ "$CHECK_ONLY" = "1" ]; then
  log "check-only mode: step 0 passed (read-only); no smoke, no ninja"
  exit 0
fi

# ---------------------------------------------------------------- step 1
step "step 1: artifacts"
[ -x "$APP_BIN" ] || die "missing executable $APP_BIN"
note "PASS  app binary: $(stat -f '%z bytes' "$APP_BIN")"
if lipo -info "$APP_BIN" 2>/dev/null | grep -q "arm64"; then
  note "PASS  app binary arch: $(lipo -info "$APP_BIN" 2>&1)"
else
  die "app binary is not arm64"
fi
PLIST_VERSION="$(plutil -extract CFBundleShortVersionString raw -o - "$INFO_PLIST" 2>/dev/null || true)"
if [ "$PLIST_VERSION" = "$EXPECTED_VERSION" ]; then
  note "PASS  Info.plist version: $PLIST_VERSION"
else
  die "Info.plist version '$PLIST_VERSION' != expected '$EXPECTED_VERSION'"
fi
[ -n "$FRAMEWORK_BIN" ] && [ -f "$FRAMEWORK_BIN" ] || die "framework binary not found under $FRAMEWORK_VERSIONS"
if lipo -info "$FRAMEWORK_BIN" 2>/dev/null | grep -q "arm64"; then
  note "PASS  framework arch: arm64"
else
  die "framework binary is not arm64"
fi
HELPER_APP="$(find "$APP_DIR/Contents/Frameworks" -maxdepth 6 -name "$BROWSER_CLI_PRODUCT_NAME Helper (Renderer).app" 2>/dev/null | head -1 || true)"
[ -n "$HELPER_APP" ] && note "PASS  helper app present: $(basename "$HELPER_APP")"
# No ad-hoc build may be launched or tested. The app must already be signed by
# the pinned Developer ID identity (scripts/build.sh does this automatically).
SIG_INFO="$(codesign -dv --verbose=4 "$APP_DIR" 2>&1 || true)"
case "$SIG_INFO" in
  *"TeamIdentifier=$BROWSER_CLI_TEAM_ID"*)
    note "PASS  signature: Developer ID (team $BROWSER_CLI_TEAM_ID)"
    ;;
  *"Signature=adhoc"*)
    die "app is ad-hoc signed; ad-hoc builds are never testable. Run scripts/build.sh (mandatory signing)."
    ;;
  *)
    die "app signature is not the pinned Developer ID identity; run scripts/build.sh"
    ;;
esac

# ---------------------------------------------------------------- step 2
if [ "$SKIP_SMOKE" = "1" ]; then
  step "step 2: smoke skipped"
  note "SKIP  smoke (--skip-smoke)"
else
  step "step 2: no-UI smoke (--version)"
  SMOKE_OUT="$(mktemp)"
  SMOKE_ERR="$(mktemp)"
  # Mandatory test hygiene: isolated throwaway profile + mock keychain. The
  # keychain must never be touched by tests ("Chromium Safe Storage" prompt;
  # see AGENTS.md). The app is already Developer-ID-signed (checked in step 1).
  SMOKE_PROFILE="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-verify-profile.XXXXXX")"
  SMOKE_START="$(date +%s)"
  SMOKE_RC=0
  SMOKE_WAITED=0
  "$APP_BIN" --version --use-mock-keychain --user-data-dir="$SMOKE_PROFILE" >"$SMOKE_OUT" 2>"$SMOKE_ERR" &
  SMOKE_PID=$!
  while kill -0 "$SMOKE_PID" 2>/dev/null; do
    if [ "$SMOKE_WAITED" -ge "$SMOKE_TIMEOUT" ]; then
      warn "smoke timeout after ${SMOKE_TIMEOUT}s; killing pid $SMOKE_PID"
      kill -9 "$SMOKE_PID" 2>/dev/null || true
      SMOKE_RC=124
      break
    fi
    sleep 1
    SMOKE_WAITED=$((SMOKE_WAITED + 1))
  done
  if [ "$SMOKE_RC" -eq 0 ]; then
    wait "$SMOKE_PID" 2>/dev/null || SMOKE_RC=$?
  fi
  SMOKE_ELAPSED="$(( $(date +%s) - SMOKE_START ))"
  SMOKE_TEXT="$(cat "$SMOKE_OUT")"
  if [ "$SMOKE_RC" -eq 0 ] && printf '%s' "$SMOKE_TEXT" | grep -q "Chromium ${EXPECTED_VERSION%%.*}\."; then
    note "PASS  smoke: '$SMOKE_TEXT' rc=0 elapsed=${SMOKE_ELAPSED}s"
  else
    warn "smoke output: $SMOKE_TEXT"
    warn "smoke stderr (first 5 lines):"; head -5 "$SMOKE_ERR" >&2 || true
    die "smoke failed (rc=$SMOKE_RC)"
  fi
  sleep 2
  RESIDUAL="$(pgrep -f "$APP_DIR" || true)"
  if [ -n "$RESIDUAL" ]; then
    die "residual Chromium processes after smoke: $RESIDUAL"
  fi
  note "PASS  no residual processes after smoke"
fi

# ---------------------------------------------------------------- step 3
step "step 3: empty incremental"
EMPTY_LOG="$LOG_DIR/verify-empty-$(date +%Y%m%d-%H%M%S).log"
EMPTY_START="$(date +%s)"
EMPTY_RC=0
ninja_run "$EMPTY_LOG" chrome || EMPTY_RC=$?
EMPTY_ELAPSED="$(( $(date +%s) - EMPTY_START ))"
if [ "$EMPTY_RC" -ne 0 ]; then
  tail -5 "$EMPTY_LOG" >&2 || true
  die "empty incremental failed rc=$EMPTY_RC"
fi
if grep -q "no work to do" "$EMPTY_LOG"; then
  note "PASS  empty incremental: 'no work to do' (${EMPTY_ELAPSED}s)"
else
  note "WARN  empty incremental rc=0 but no 'no work to do' line (${EMPTY_ELAPSED}s); see $EMPTY_LOG"
fi

# ---------------------------------------------------------------- step 4
if [ "$SKIP_INCREMENTAL" = "1" ]; then
  step "step 4: incremental probe skipped"
  note "SKIP  incremental probe (--skip-incremental)"
else
  step "step 4: real C++ incremental probe ($PROBE_REL)"
  git -C "$CHROMIUM_SRC" ls-files --error-unmatch "$PROBE_REL" >/dev/null 2>&1 \
    || die "probe file is not tracked by git: $PROBE_REL"
  [ -z "$(git -C "$CHROMIUM_SRC" status --porcelain -- "$PROBE_REL")" ] \
    || die "probe file already has local changes; refusing to probe: $PROBE_REL"
  PROBE_ABS="$CHROMIUM_SRC/$PROBE_REL"
  BEFORE_SHA="$(git -C "$CHROMIUM_SRC" hash-object "$PROBE_ABS")"
  TMP_BACKUP="$(mktemp)"
  cp "$PROBE_ABS" "$TMP_BACKUP"
  note "INFO  baseline blob=$(git -C "$CHROMIUM_SRC" rev-parse "HEAD:$PROBE_REL") sha256=$(shasum -a 256 "$PROBE_ABS" | awk '{print $1}')"

  printf '\nstatic_assert(true, "browser-cli-phase1-incremental-probe");\n' >> "$PROBE_ABS"
  PROBE_APPLIED=1

  PROBE_LOG="$LOG_DIR/verify-probe-$(date +%Y%m%d-%H%M%S).log"
  PROBE_START="$(date +%s)"
  PROBE_RC=0
  ninja_run "$PROBE_LOG" chrome || PROBE_RC=$?
  PROBE_ELAPSED="$(( $(date +%s) - PROBE_START ))"
  PROBE_EDGES="$(count_edges "$PROBE_LOG")"
  [ "$PROBE_RC" -eq 0 ] || { tail -5 "$PROBE_LOG" >&2 || true; die "probe build failed rc=$PROBE_RC"; }
  note "PASS  probe build rc=0 edges=$PROBE_EDGES elapsed=${PROBE_ELAPSED}s"

  git -C "$CHROMIUM_SRC" cat-file blob "HEAD:$PROBE_REL" > "$PROBE_ABS"
  PROBE_APPLIED=0
  AFTER_SHA="$(git -C "$CHROMIUM_SRC" hash-object "$PROBE_ABS")"
  [ "$AFTER_SHA" = "$BEFORE_SHA" ] || die "restore mismatch: $AFTER_SHA != $BEFORE_SHA"
  [ -z "$(git -C "$CHROMIUM_SRC" status --porcelain -- "$PROBE_REL")" ] || die "probe file not clean after restore"
  note "PASS  restore exact (blob $AFTER_SHA); probe file unchanged"
  note "INFO  checkout stays patch-dirty by design (chromium/patches/*.patch are applied, uncommitted)"

  RESTORE_LOG="$LOG_DIR/verify-restore-$(date +%Y%m%d-%H%M%S).log"
  RESTORE_START="$(date +%s)"
  RESTORE_RC=0
  ninja_run "$RESTORE_LOG" chrome || RESTORE_RC=$?
  RESTORE_ELAPSED="$(( $(date +%s) - RESTORE_START ))"
  RESTORE_EDGES="$(count_edges "$RESTORE_LOG")"
  [ "$RESTORE_RC" -eq 0 ] || { tail -5 "$RESTORE_LOG" >&2 || true; die "restore build failed rc=$RESTORE_RC"; }
  note "PASS  restore build rc=0 edges=$RESTORE_EDGES elapsed=${RESTORE_ELAPSED}s"

  FINAL_LOG="$LOG_DIR/verify-final-$(date +%Y%m%d-%H%M%S).log"
  FINAL_RC=0
  ninja_run "$FINAL_LOG" chrome || FINAL_RC=$?
  [ "$FINAL_RC" -eq 0 ] || die "final incremental failed rc=$FINAL_RC"
  if grep -q "no work to do" "$FINAL_LOG"; then
    note "PASS  final: no work to do (artifacts match clean source)"
  else
    note "WARN  final rc=0 without 'no work to do'; see $FINAL_LOG"
  fi
  [ -z "$(git -C "$CHROMIUM_SRC" status --porcelain -- "$PROBE_REL")" ] || die "probe file dirty at the end"
fi

# ---------------------------------------------------------------- step 5
# Mandatory: any build performed above leaves ad-hoc linker signatures, so the
# product must be re-signed and accepted before this run can pass. Ad-hoc builds
# must never be kept or claimed testable (see AGENTS.md).
step "step 5: mandatory product signing + signature acceptance"
"$SCRIPT_DIR/sign-browser-cli.sh"
"$SCRIPT_DIR/verify-signature.sh"
note "PASS  product signed with the pinned Developer ID identity and accepted"

step "summary"
note "verify-phase1 completed; note log: $NOTE_LOG"
log "verify-phase1 PASS"
