"""Read-only primary-source capture; never imports downloaded material."""
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REV = 'e5c9beaa52252239b9fb77446ae2c7dcc062c506'
RAW = f'https://raw.githubusercontent.com/sands-lab/aistral-dataset/{REV}/'
SOURCES = {
    'aistral-v2.html': 'https://arxiv.org/html/2511.18354v2',
    'aistral-abstract.html': 'https://arxiv.org/abs/2511.18354v2',
    'flint-v1.html': 'https://arxiv.org/html/2610.27273v1',
    'metadata.json': 'https://api.github.com/repos/sands-lab/aistral-dataset',
    'tree.json': f'https://api.github.com/repos/sands-lab/aistral-dataset/git/trees/{REV}?recursive=1',
    **{name.replace('/', '--'): RAW + name for name in [
        'README.md', 'LICENSE', 'NOTICE', 'DATASHEET.md', 'SCHEMA.md',
        'browsecomp_plus/README.md', 'browsecomp_plus/query_ids.json',
        'loong/doc_manifest.jsonl', 'loong/rebuild_docs.py',
    ]},
}

if __name__ == '__main__':
    (ROOT / 'sources').mkdir(exist_ok=True)
    manifest = []
    for name, url in SOURCES.items():
        row = {'file': name, 'url': url, 'retrieved_utc': datetime.now(timezone.utc).isoformat()}
        start = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'daily-tech-source-audit/1.0'})
            with urllib.request.urlopen(req, timeout=45) as response:
                payload = response.read()
                row['status'] = response.status
            (ROOT / 'sources' / name).write_bytes(payload)
            row.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        except Exception as error:
            row['error'] = str(error)
        row['seconds'] = time.perf_counter() - start
        manifest.append(row)
        print(name, row.get('status', row.get('error')), flush=True)
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
