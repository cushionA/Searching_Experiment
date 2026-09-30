#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-core}" in
  core|http|browser|adaptive) profile="${1:-core}" ;;
  *) echo 'Usage: bash scripts/cloud_setup.sh [core|http|browser|adaptive]' >&2; exit 2 ;;
esac
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
