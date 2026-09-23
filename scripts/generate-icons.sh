#!/usr/bin/env bash
# Generate the macOS app icon assets used by the Chromium build from the
# approved product artwork.
#
# Source (never modified): assets/branding/browser-cli-logo-source.png
# Outputs (into the Chromium checkout, untracked by git):
#   chrome/app/theme/browser_cli/mac/app.icns
#   chrome/app/theme/browser_cli/mac/Assets.car
#
# The generated directory is selected at build time by the
# `mac_app_icon_dir` GN arg (chromium/args.gn) and referenced by the
# Browser CLI branding patch (chromium/patches/002-*).
#
# Deterministic and reproducible: sips (resize) + iconutil (icns) + actool
# (asset catalog). No network, no keychain, no browser, no App launch.
#
# Usage: scripts/generate-icons.sh [--dry-run] [--help]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/lib/common.sh"

SOURCE="${BROWSER_CLI_LOGO_SOURCE:-$BROWSER_CLI_ROOT/assets/branding/browser-cli-logo-source.png}"
ICON_DIR="$CHROMIUM_SRC/chrome/app/theme/browser_cli/mac"

usage() {
  cat <<'EOF'
Usage: scripts/generate-icons.sh [options]

Regenerates chrome/app/theme/browser_cli/mac/{app.icns,Assets.car} in the
Chromium checkout from assets/branding/browser-cli-logo-source.png.

Options:
  --dry-run   print the plan; touch nothing
  --help      this message

Environment overrides: CHROMIUM_ROOT, BROWSER_CLI_LOGO_SOURCE.
The source image is never modified; outputs are regenerated from scratch.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

[ -f "$SOURCE" ] || die "logo source not found: $SOURCE"
for tool in sips iconutil; do
  have_cmd "$tool" || die "required macOS tool missing: $tool"
done
have_cmd xcrun || die "required macOS tool missing: xcrun"

SOURCE_SHA_BEFORE="$(shasum -a 256 "$SOURCE" | awk '{print $1}')"
SOURCE_SIZE_INFO="$(sips -g pixelWidth -g pixelHeight "$SOURCE" 2>/dev/null | awk '/pixelWidth|pixelHeight/ {print $2}' | tr '\n' 'x' | sed 's/x$//')"

# icns / appiconset sizes required by iconutil and the macOS asset catalog.
SIZES="16 32 64 128 256 512 1024"

if [ "$DRY_RUN" = "1" ]; then
  cat <<EOF
[dry-run] source : $SOURCE (sha256 $SOURCE_SHA_BEFORE, ${SOURCE_SIZE_INFO}px)
[dry-run] output : $ICON_DIR/app.icns
[dry-run] output : $ICON_DIR/Assets.car
[dry-run] tools  : sips + iconutil + xcrun actool
[dry-run] steps  :
  1. resize source to sizes: $SIZES
  2. iconutil -c icns <iconset> -o app.icns
  3. write Assets.xcassets/AppIcon.appiconset + actool --compile -> Assets.car
[dry-run] source image is never modified; nothing executed
EOF
  exit 0
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/browser-cli-icons.XXXXXX")"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

ICONSET="$TMP_DIR/app.iconset"
mkdir -p "$ICONSET"

resize() { # <size> <out>
  sips -z "$1" "$1" "$SOURCE" --out "$2" >/dev/null
}

log "generating iconset ($SIZES px)"
resize 16 "$ICONSET/icon_16x16.png"
resize 32 "$ICONSET/icon_16x16@2x.png"
resize 32 "$ICONSET/icon_32x32.png"
resize 64 "$ICONSET/icon_32x32@2x.png"
resize 128 "$ICONSET/icon_128x128.png"
resize 256 "$ICONSET/icon_128x128@2x.png"
resize 256 "$ICONSET/icon_256x256.png"
resize 512 "$ICONSET/icon_256x256@2x.png"
resize 512 "$ICONSET/icon_512x512.png"
resize 1024 "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o "$TMP_DIR/app.icns"
[ -s "$TMP_DIR/app.icns" ] || die "iconutil produced no app.icns"

log "generating asset catalog"
XCA="$TMP_DIR/Assets.xcassets"
APPSET="$XCA/AppIcon.appiconset"
mkdir -p "$APPSET"
cat > "$XCA/Contents.json" <<'EOF'
{
  "info" : {
    "author" : "xcode",
    "version" : 1
  }
}
EOF
cat > "$APPSET/Contents.json" <<'EOF'
{
  "images" : [
    { "filename" : "appicon_16.png",  "idiom" : "mac", "scale" : "1x", "size" : "16x16" },
    { "filename" : "appicon_32.png",  "idiom" : "mac", "scale" : "2x", "size" : "16x16" },
    { "filename" : "appicon_32.png",  "idiom" : "mac", "scale" : "1x", "size" : "32x32" },
    { "filename" : "appicon_64.png",  "idiom" : "mac", "scale" : "2x", "size" : "32x32" },
    { "filename" : "appicon_128.png", "idiom" : "mac", "scale" : "1x", "size" : "128x128" },
    { "filename" : "appicon_256.png", "idiom" : "mac", "scale" : "2x", "size" : "128x128" },
    { "filename" : "appicon_256.png", "idiom" : "mac", "scale" : "1x", "size" : "256x256" },
    { "filename" : "appicon_512.png", "idiom" : "mac", "scale" : "2x", "size" : "256x256" },
    { "filename" : "appicon_512.png", "idiom" : "mac", "scale" : "1x", "size" : "512x512" },
    { "filename" : "appicon_1024.png", "idiom" : "mac", "scale" : "2x", "size" : "512x512" }
  ],
  "info" : {
    "author" : "xcode",
    "version" : 1
  }
}
EOF
for size in $SIZES; do
  resize "$size" "$APPSET/appicon_${size}.png"
done

mkdir -p "$TMP_DIR/actool"
xcrun actool \
  --output-format human-readable-text \
  --notices --warnings \
  --platform macosx \
  --minimum-deployment-target 13.0 \
  --app-icon AppIcon \
  --compress-pngs \
  --output-partial-info-plist "$TMP_DIR/assetcatalog_generated_info.plist" \
  --compile "$TMP_DIR/actool" \
  "$XCA"
[ -s "$TMP_DIR/actool/Assets.car" ] || die "actool produced no Assets.car"

mkdir -p "$ICON_DIR"
cp "$TMP_DIR/app.icns" "$ICON_DIR/app.icns"
cp "$TMP_DIR/actool/Assets.car" "$ICON_DIR/Assets.car"

SOURCE_SHA_AFTER="$(shasum -a 256 "$SOURCE" | awk '{print $1}')"
[ "$SOURCE_SHA_BEFORE" = "$SOURCE_SHA_AFTER" ] || die "source image changed unexpectedly: $SOURCE"

log "generated:"
log "  $ICON_DIR/app.icns      ($(stat -f '%z' "$ICON_DIR/app.icns") bytes, sha256 $(shasum -a 256 "$ICON_DIR/app.icns" | awk '{print $1}'))"
log "  $ICON_DIR/Assets.car    ($(stat -f '%z' "$ICON_DIR/Assets.car") bytes, sha256 $(shasum -a 256 "$ICON_DIR/Assets.car" | awk '{print $1}'))"
log "source unchanged: sha256 $SOURCE_SHA_AFTER"
