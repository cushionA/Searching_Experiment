"""Read-only primary-source snapshots. No candidate code is imported or executed."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

OUT = Path('benchmarks/daily-tech-20261014')
OUT.mkdir(parents=True, exist_ok=True)
manifest = []

def save(name, url, data, elapsed):
    (OUT / name).write_bytes(data)
    manifest.append(dict(file=name, url=url, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), seconds=round(elapsed, 4), observed_at=datetime.now(timezone.utc).isoformat()))

def gh(name, endpoint, content=False):
    start = time.perf_counter()
    data = subprocess.check_output(['gh', 'api', endpoint], timeout=60)
    if content:
        data = base64.b64decode(json.loads(data)['content'])
    save(name, 'https://api.github.com/' + endpoint, data, time.perf_counter()-start)
    return data

def url(name, address):
    start = time.perf_counter()
    data = urllib.request.urlopen(address, timeout=60).read()
    save(name, address, data, time.perf_counter()-start)
    return data

for repo, name in [('microsoft/playwright-mcp','mcp'), ('RapidAI/RapidOCR','rapidocr'), ('ChromeDevTools/chrome-devtools-mcp','devtools')]:
    gh(name+'-metadata.json', f'repos/{repo}')
    gh(name+'-releases.json', f'repos/{repo}/releases?per_page=6')
    gh(name+'-readme.txt', f'repos/{repo}/readme', True)
gh('mcp-package.json', 'repos/microsoft/playwright-mcp/contents/package.json?ref=v0.0.83', True)
pkg=json.loads(url('playwright-core-registry.json', 'https://registry.npmjs.org/playwright-core/1.64.0-alpha-1790635538000'))
ref=pkg['gitHead']
gh('find-release.txt', f'repos/microsoft/playwright/contents/packages/playwright-core/src/tools/backend/find.ts?ref={ref}', True)
for path, name in [('default_models.yaml','rapidocr-models.yaml'), ('config.yaml','rapidocr-config.yaml'), ('utils/model_resolver.py','rapidocr-resolver.txt'), ('utils/typings.py','rapidocr-types.txt')]:
    gh(name, f'repos/RapidAI/RapidOCR/contents/python/rapidocr/{path}?ref=v3.10.0', True)
gh('rapidocr-model-tests.txt', 'repos/RapidAI/RapidOCR/contents/python/tests/test_model_resolver.py?ref=v3.10.0', True)
gh('rapidocr-license-notice.txt', 'repos/RapidAI/RapidOCR/contents/python/MODEL_LICENSES.md?ref=v3.10.0', True)
gh('rapidocr-project.txt', 'repos/RapidAI/RapidOCR/contents/python/pyproject.toml?ref=v3.10.0', True)
url('rapidocr-pypi.json','https://pypi.org/pypi/rapidocr/3.10.0/json')
gh('open-prs.json','repos/cushionA/Searching_Experiment/pulls?state=open&per_page=30')
gh('recent-commits.json','repos/cushionA/Searching_Experiment/commits?per_page=5')
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print(f'Saved {len(manifest)} primary snapshots')
