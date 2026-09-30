#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -B -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
if [ ! -x .deps/kaggle-venv/bin/python ]; then
  python3 -m venv .deps/kaggle-venv
fi
.deps/kaggle-venv/bin/python -m pip install --no-cache-dir -r .agents/skills/kaggle-ops/requirements.txt
.deps/kaggle-venv/bin/python -m pip --no-cache-dir check
.deps/kaggle-venv/bin/python -B -m unittest discover -s tests -p 'test_kaggle_ops.py'
.deps/kaggle-venv/bin/python -B .agents/skills/kaggle-ops/scripts/kaggle_ops.py doctor
