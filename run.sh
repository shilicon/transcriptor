#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "还没有安装运行环境。先执行：./scripts/setup.sh"
  exit 1
fi

export TRANSCRIPTOR_DATA_DIR="${TRANSCRIPTOR_DATA_DIR:-$PWD/data}"
export TRANSCRIPTOR_MODELS_DIR="${TRANSCRIPTOR_MODELS_DIR:-$PWD/models}"

PORT="${PORT:-8765}"
echo "Transcriptor: http://127.0.0.1:${PORT}"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
