#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .semantic-venv/bin/python ]; then
 if command -v python3.12 >/dev/null; then task_python=python3.12; else task_python=python3; fi
 "$task_python" -c 'import sys; assert sys.version_info >= (3,12), "请先安装 Python 3.12 或更新版本"'
 "$task_python" -m venv .semantic-venv
fi
.semantic-venv/bin/python -m ensurepip --upgrade
.semantic-venv/bin/python -m pip install -r requirements-semantic.txt
.semantic-venv/bin/python bootstrap_models.py
