#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ -x .semantic-venv/bin/python ]; then exec .semantic-venv/bin/python server.py; fi
if [ -f data/runtime.json ]; then
 echo '请先运行 bash setup-semantic.sh，安装语义模型运行环境。'
 exit 1
fi
if [ -x .venv/bin/python ]; then exec .venv/bin/python server.py; else exec python3 server.py; fi
