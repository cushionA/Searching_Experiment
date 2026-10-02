#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -B -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"; assert sys.platform == "linux", "RSS measurement requires Linux"'
python3 -m venv .deps/browser-benchmark-venv
benchmark_python=.deps/browser-benchmark-venv/bin/python
"$benchmark_python" -m pip install -r requirements/browser-benchmark.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/.deps/benchmark-browsers" "$benchmark_python" -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.deps/benchmark-browsers" "$benchmark_python" -m patchright install chromium
if [ -z "${DISPLAY:-}" ] && ! command -v Xvfb >/dev/null 2>&1; then
  echo 'Headful also needs Xvfb: install the OS package xvfb (e.g. sudo apt-get install xvfb).' >&2
fi
if [ -n "${LIGHTPANDA_BIN:-}" ]; then
  test -x "$LIGHTPANDA_BIN"
elif command -v lightpanda >/dev/null 2>&1 || [ -x .deps/lightpanda ]; then
  :
else
  # For repeatable comparisons, override this with a fixed release URL and hash.
  "$benchmark_python" -B - <<'PY'
import hashlib
import json
import os
import platform
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

machine = platform.machine()
if machine not in {"x86_64", "aarch64"}:
    raise SystemExit(f"Install LightPanda manually on {machine} and set LIGHTPANDA_BIN")
url = os.environ.get("LIGHTPANDA_DOWNLOAD_URL", f"https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-{machine}-linux")
destination = Path(".deps/lightpanda")
temporary = destination.with_suffix(".download")
digest = hashlib.sha256()
try:
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    expected = os.environ.get("LIGHTPANDA_SHA256")
    if expected and digest.hexdigest() != expected.lower():
        raise SystemExit("LIGHTPANDA_SHA256 mismatch; executable was not installed")
    temporary.chmod(0o755)
    temporary.replace(destination)
finally:
    temporary.unlink(missing_ok=True)
destination.with_suffix(".source.json").write_text(json.dumps({"url": url, "sha256": digest.hexdigest(), "downloaded_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
print(f"LightPanda installed: {destination} sha256={digest.hexdigest()}")
PY
fi
echo 'Browser dependencies ready: npm run benchmark:quick (headful additionally requires DISPLAY or Xvfb).'
