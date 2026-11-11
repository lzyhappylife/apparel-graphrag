#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo '本地 Qwen 的 MLX 运行环境需要 Apple Silicon Mac；其他平台可使用语义检索模式。'
  exit 1
fi
if [ ! -x .semantic-venv/bin/python ]; then bash setup-semantic.sh; fi
.semantic-venv/bin/python -m ensurepip --upgrade
.semantic-venv/bin/python -m pip install -r requirements-extraction.txt
.semantic-venv/bin/python bootstrap_extraction.py
