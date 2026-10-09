#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
experiment_dir="$repo_root/experiments/sku-matching"
deps_dir="$repo_root/.deps"
venv_dir="$deps_dir/sku-matching-venv"
model_dir="$deps_dir/sku-matching-model"

mkdir -p "$deps_dir"
python3 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --disable-pip-version-check -q -r "$experiment_dir/requirements-runtime.txt"
"$venv_dir/bin/python" "$experiment_dir/download_model.py" "$model_dir"
printf 'Ready. Activate with: source %q/bin/activate\n' "$venv_dir"
