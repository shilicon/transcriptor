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
chmod +x "$APP/Contents/MacOS/Transcriptor"

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "Built: $APP"
