#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -B -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"; assert sys.platform == "linux", "RSS measurement requires Linux"'
case "${1:-all}" in
  all)
    python3 -m venv .deps/browser-benchmark-venv
    benchmark_python=.deps/browser-benchmark-venv/bin/python
    "$benchmark_python" -m pip install -r requirements/browser-benchmark.txt
    PLAYWRIGHT_BROWSERS_PATH="$PWD/.deps/benchmark-browsers" "$benchmark_python" -m playwright install chromium
    PLAYWRIGHT_BROWSERS_PATH="$PWD/.deps/benchmark-browsers" "$benchmark_python" -m patchright install chromium
    if [ -z "${DISPLAY:-}" ] && ! command -v Xvfb >/dev/null 2>&1; then
      echo 'Headful also needs Xvfb: install the OS package xvfb (e.g. sudo apt-get install xvfb).' >&2
    fi
    ;;
  lightpanda)
    mkdir -p .deps
    benchmark_python=python3
    ;;
  *) echo 'Usage: bash scripts/setup_browser_benchmark.sh [all|lightpanda]' >&2; exit 2 ;;
esac
if [ -n "${LIGHTPANDA_BIN:-}" ]; then
  test -x "$LIGHTPANDA_BIN"
elif command -v lightpanda >/dev/null 2>&1 || [ -x .deps/lightpanda ]; then
  :
else
  # Reproducible official release; a custom download must also supply a digest.
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
release = "1.0.0"
digests = {
    "x86_64": "aa5a4b8ed53d1e38b3c73f5b2647d0a84a82e6744557f45f9a9c85858aa031c3",
    "aarch64": "69791924bcee43b13b224af4c845622c5fe66fdbc1b8143bfaa39ca8f85244f5",
}
custom_url = os.environ.get("LIGHTPANDA_DOWNLOAD_URL")
expected = os.environ.get("LIGHTPANDA_SHA256") or (None if custom_url else digests[machine])
if not expected or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected.lower()):
    raise SystemExit("A custom LIGHTPANDA_DOWNLOAD_URL requires a valid LIGHTPANDA_SHA256")
url = custom_url or f"https://github.com/lightpanda-io/browser/releases/download/{release}/lightpanda-{machine}-linux"
if not url.startswith("https://"):
    raise SystemExit("LIGHTPANDA_DOWNLOAD_URL must use HTTPS")
destination = Path(".deps/lightpanda")
temporary = destination.with_suffix(".download")
digest = hashlib.sha256()
try:
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != expected.lower():
        raise SystemExit("LIGHTPANDA_SHA256 mismatch; executable was not installed")
    temporary.chmod(0o755)
    temporary.replace(destination)
finally:
    temporary.unlink(missing_ok=True)
destination.with_suffix(".source.json").write_text(json.dumps({"url": url, "sha256": digest.hexdigest(), "downloaded_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
print(f"LightPanda installed: {destination} sha256={digest.hexdigest()}")
PY
fi
echo 'Setup complete. A benchmark also requires Playwright/Patchright; headful requires DISPLAY or Xvfb.'
