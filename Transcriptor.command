#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
( sleep 1.5; open "http://127.0.0.1:${PORT}" ) >/dev/null 2>&1 &
exec ./run.sh
