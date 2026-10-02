#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
benchmark_python="${BROWSER_BENCHMARK_PYTHON:-.deps/browser-benchmark-venv/bin/python}"
if [ ! -x "$benchmark_python" ]; then
  if [ -n "${BROWSER_BENCHMARK_PYTHON:-}" ]; then
    echo "BROWSER_BENCHMARK_PYTHON is not executable: $benchmark_python" >&2
    exit 2
  fi
  benchmark_python=python3
fi
exec "$benchmark_python" -B scripts/browser_benchmark.py "$@"
