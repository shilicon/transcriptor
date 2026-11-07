#!/usr/bin/env bash
set -euo pipefail
LMS="$HOME/.lmstudio/bin/lms"
if [ ! -x "$LMS" ]; then
  echo "找不到 LM Studio CLI：$LMS"
  echo "请先安装 LM Studio，或在网页中手动填写其他 OpenAI 兼容接口。"
  exit 1
fi
"$LMS" server start
"$LMS" load gemma-4-e4b-it-mlx --identifier gemma-4-e4b
"$LMS" ps
