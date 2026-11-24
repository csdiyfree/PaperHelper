#!/bin/bash
# Build PaperHelper.app — a tiny launcher that runs `bin/paperhelper open`.
# Usage: macos/build-app.sh [/path/to/PaperHelper.app]   (default ~/Applications)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$HOME/Applications/PaperHelper.app}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Icon: render the SVG with Quick Look, then pack an .icns.
qlmanage -t -s 1024 -o "$TMP" "$ROOT/app/web/icon.svg" >/dev/null 2>&1
mkdir "$TMP/icon.iconset"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$TMP/icon.svg.png" --out "$TMP/icon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$TMP/icon.svg.png" --out "$TMP/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/icon.iconset" -o "$TMP/PaperHelper.icns"

# AppleScript applet that starts the server (if needed) and opens the window.
mkdir -p "$(dirname "$APP")"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script (quoted form of \"$ROOT/bin/paperhelper\") & \" open > /dev/null 2>&1 &\""

cp "$TMP/PaperHelper.icns" "$APP/Contents/Resources/applet.icns"
rm -f "$APP/Contents/Resources/Assets.car"   # otherwise the default applet icon wins
PB=/usr/libexec/PlistBuddy
PL="$APP/Contents/Info.plist"
$PB -c "Delete :CFBundleIconName" "$PL" 2>/dev/null || true
$PB -c "Set :CFBundleIconFile applet" "$PL" 2>/dev/null || $PB -c "Add :CFBundleIconFile string applet" "$PL"
$PB -c "Set :CFBundleName PaperHelper" "$PL" 2>/dev/null || true
$PB -c "Set :CFBundleIdentifier local.paperhelper" "$PL" 2>/dev/null || $PB -c "Add :CFBundleIdentifier string local.paperhelper" "$PL"
codesign --force --deep -s - "$APP" >/dev/null 2>&1   # re-sign ad hoc after editing the bundle
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
echo "已生成 $APP"
