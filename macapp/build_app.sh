#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

REPO="$(cd .. && pwd)"
APP="$REPO/Transcriptor.app"

swift build -c release

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/Transcriptor "$APP/Contents/MacOS/Transcriptor"
cp Info.plist "$APP/Contents/Info.plist"
cp Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp -R Resources/zh-Hans.lproj "$APP/Contents/Resources/"
cp -R Resources/en.lproj "$APP/Contents/Resources/"
chmod +x "$APP/Contents/MacOS/Transcriptor"

# Prefer a stable signing identity.  Ad-hoc signatures change on every build,
# which makes macOS treat each rebuild as a new app and re-prompt for the
# Keychain password.
IDENTITY="${TRANSCRIPTOR_CODESIGN_IDENTITY:-}"
if [ -z "$IDENTITY" ]; then
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | awk '/"Developer ID Application/ { print $2; exit }')"
fi
if [ -z "$IDENTITY" ]; then
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | awk '/"Apple Development/ { print $2; exit }')"
fi
if [ -n "$IDENTITY" ]; then
  codesign --force --deep --sign "$IDENTITY" "$APP" >/dev/null
  echo "Signed with: $IDENTITY"
else
  codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
  echo "Signed ad-hoc; Keychain may ask again after rebuilds"
fi
echo "Built: $APP"
