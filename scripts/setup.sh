#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "需要先安装 uv：https://docs.astral.sh/uv/"
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "需要先安装 ffmpeg：brew install ffmpeg"
  exit 1
fi

uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt

mkdir -p data/jobs models

if [ ! -d models/whisper-large-v3 ]; then
  echo
  echo "缺少语音模型：models/whisper-large-v3"
  echo "请把 MLX Whisper large-v3 模型放到该目录，或设置："
  echo "  TRANSCRIPTOR_MODELS_DIR=/path/to/models ./run.sh"
  echo
  echo "如果本机已有旧的模型目录，也可以复制："
  echo "  cp -R /path/to/whisper-large-v3 models/"
  echo
fi

chmod +x run.sh scripts/setup.sh 2>/dev/null || true
if ! command -v deno >/dev/null 2>&1 && ! command -v node >/dev/null 2>&1; then
  echo
  echo "提示：安装 Node.js 或 Deno 可提高 YouTube 链接下载兼容性。"
fi
echo "安装完成。运行：./run.sh"
