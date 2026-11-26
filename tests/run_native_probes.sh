#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$(mktemp -d "${TMPDIR:-/private/tmp}/transcriptor-native-probe.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT

swiftc -swift-version 5 -parse-as-library \
  -module-cache-path "$BUILD/module-cache" \
  "$ROOT/macapp/Sources/Transcriptor/AppModel.swift" \
  "$ROOT/macapp/Sources/Transcriptor/APIClient.swift" \
  "$ROOT/macapp/Sources/Transcriptor/Models.swift" \
  "$ROOT/macapp/Sources/Transcriptor/BackendManager.swift" \
  "$ROOT/tests/native/AppModelRaceProbe.swift" \
  -o "$BUILD/probe"

"$BUILD/probe"

swiftc -swift-version 5 -parse-as-library \
  -module-cache-path "$BUILD/module-cache" \
  "$ROOT/macapp/Sources/Transcriptor/AppModel.swift" \
  "$ROOT/macapp/Sources/Transcriptor/APIClient.swift" \
  "$ROOT/macapp/Sources/Transcriptor/Models.swift" \
  "$ROOT/macapp/Sources/Transcriptor/BackendManager.swift" \
  "$ROOT/tests/native/ChapterRefreshOrderProbe.swift" \
  -o "$BUILD/chapter-refresh-probe"

"$BUILD/chapter-refresh-probe"

"$ROOT/.venv/bin/python" "$ROOT/tests/generate_format_fixtures.py" "$BUILD/format-fixtures"

swiftc -swift-version 5 -parse-as-library \
  -module-cache-path "$BUILD/module-cache" \
  "$ROOT/macapp/Sources/Transcriptor/Models.swift" \
  "$ROOT/tests/native/FormatSafetyProbe.swift" \
  -o "$BUILD/format-probe"

"$BUILD/format-probe" \
  "$BUILD/format-fixtures/list.json" \
  "$BUILD/format-fixtures/healthy-detail.json"
