#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -B -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
python3 -B -m unittest discover -s tests -p 'test_lab*.py'
python3 -B -m jse.lab --help
