"""Read public source documents; never import or execute downloaded material."""
import argparse
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--plan', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
manifest = []
started = time.perf_counter()
for item in json.loads(a.plan.read_text()):
    url, name = item['url'], item['name']
    assert url.startswith('https://') and '/' not in name and name not in ('.', '..')
    row = dict(item, retrieved_at=datetime.now(timezone.utc).isoformat())
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'daily-tech-readonly-review/20261018'})
        with urllib.request.urlopen(req, timeout=35) as r:
            data = r.read(3_000_001)
            if len(data) > 3_000_000:
                raise ValueError('source exceeds 3MB document cap')
            row.update(status=r.status, final_url=r.url)
        (a.output / name).write_bytes(data)
        row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    except Exception as exc:
        row.update(error_type=type(exc).__name__, error=str(exc))
    manifest.append(row)
    (a.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({'documents': len(manifest), 'successful': sum('sha256' in r for r in manifest), 'bytes': sum(r.get('bytes',0) for r in manifest), 'wall_seconds': time.perf_counter()-started}))
