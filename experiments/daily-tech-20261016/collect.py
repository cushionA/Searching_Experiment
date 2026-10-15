"""Bounded public-primary-source collection; never executes downloaded sources."""
import concurrent.futures
import datetime
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'benchmarks/daily-tech-20261016'
REV = '36755d461be079737860a5f03ae0c803501269e9'
URLS = {
    'repo.json': 'https://api.github.com/repos/lightonai/LightOnOCR',
    'commits.json': 'https://api.github.com/repos/lightonai/LightOnOCR/commits?per_page=10',
    'releases.json': 'https://api.github.com/repos/lightonai/LightOnOCR/releases?per_page=3',
    'contributors.json': 'https://api.github.com/repos/lightonai/LightOnOCR/contributors?per_page=10',
}
for name in ['lightonocr/grounding.py', 'lightonocr/models.py', 'lightonocr/render.py', 'lightonocr/pipeline.py', 'README.md', 'LICENSE', 'pyproject.toml']:
    URLS[name.replace('/', '_') + '.txt'] = f'https://raw.githubusercontent.com/lightonai/LightOnOCR/{REV}/{name}'
for model in ['LightOnOCR-3-0.8B', 'LightOnOCR-3-1B', 'LightOnOCR-3-4B', 'LightOnOCR-2-1B']:
    URLS[model + '.json'] = f'https://huggingface.co/api/models/lightonai/{model}?blobs=true'
URLS['family.json'] = 'https://huggingface.co/api/models?author=lightonai&search=LightOnOCR&sort=lastModified&direction=-1&limit=50'


def fetch(item):
    name, url = item
    rec = {'name': name, 'url': url, 'retrieved_utc': datetime.datetime.now(datetime.UTC).isoformat()}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Searching-Experiment-public-research/1.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError('source exceeds 2MB cap')
            rec.update(status=response.status, final_url=response.url)
        (OUT / 'sources' / name).write_bytes(data)
        rec.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    except Exception as exc:
        rec['error'] = repr(exc)
    return rec


if __name__ == '__main__':
    (OUT / 'sources').mkdir(parents=True, exist_ok=True)
    records = list(concurrent.futures.ThreadPoolExecutor(4).map(fetch, URLS.items()))
    # Immutable model cards/configs; no weight downloads.
    extra = {}
    for model in ['LightOnOCR-3-0.8B', 'LightOnOCR-3-1B', 'LightOnOCR-3-4B', 'LightOnOCR-2-1B']:
        path = OUT / 'sources' / (model + '.json')
        if path.exists():
            info = json.loads(path.read_text())
            for name in ['README.md', 'config.json']:
                extra[f'{model}-{name}.txt'] = f'https://huggingface.co/lightonai/{model}/resolve/{info["sha"]}/{name}'
    records += list(concurrent.futures.ThreadPoolExecutor(4).map(fetch, extra.items()))
    (OUT / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'sources': len(records), 'bytes': sum(x.get('bytes', 0) for x in records), 'errors': [x for x in records if 'error' in x]}, indent=2))
