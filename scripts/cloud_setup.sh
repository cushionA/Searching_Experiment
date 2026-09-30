#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-core}" in
  core|http|browser|adaptive|adaptive-agent) profile="${1:-core}" ;;
  *) echo 'Usage: bash scripts/cloud_setup.sh [core|http|browser|adaptive|adaptive-agent]' >&2; exit 2 ;;
esac
if [ "$profile" = adaptive-agent ]; then
  bash scripts/setup_model_runner.sh
  profile=adaptive
fi

# Codex Cloud images do not necessarily expose the Codex CLI on PATH.
# Install it explicitly so model-selectable CLI experiments are available.
if ! command -v codex >/dev/null 2>&1; then
  npm install -g @openai/codex@latest
fi
command -v codex
codex --version
# Do not require credentials during setup. Authentication is checked only when
# a later task actually invokes a model.
if codex login status >/dev/null 2>&1; then
  echo "Codex CLI authentication: available"
else
  echo "Codex CLI authentication: not configured"
fi
python3 -B -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
python3 -B -m unittest discover -s tests -p 'test_lab*.py'
python3 -B -m jse.lab --help
if [ "$profile" = http ]; then
  python3 -m pip install -r requirements/crawl-tools.txt
  python3 -B scripts/check_crawl_tools.py
elif [ "$profile" = browser ]; then
  python3 -m pip install -r requirements/browser-tools.txt
  python3 -m patchright install --with-deps chromium
  python3 -B scripts/check_crawl_tools.py --browser
elif [ "$profile" = adaptive ]; then
  python3 -m pip install -r requirements/adaptive-tools.txt
  if ! python3 -B scripts/check_playwright_browser.py; then
    python3 -m playwright install --with-deps chromium
    python3 -B scripts/check_playwright_browser.py
  fi
  python3 -B -m unittest discover -s tests -p 'test_adaptive.py'
  python3 -B scripts/check_cloud_environment.py --adaptive
fi
python3 -B -m unittest discover -s tests -p 'test_cloud_environment.py'
python3 -B scripts/check_cloud_environment.py
if [ "$profile" != core ]; then
  python3 -B -m unittest discover -s tests -p 'test_impit.py'
fi
