"""Check previously reviewed release families without rerunning experiments."""
import subprocess
import json
import hashlib
import time
from pathlib import Path
from datetime import datetime, timezone
p = Path('benchmarks/daily-tech-20261014')
m = json.loads((p / 'manifest.json').read_text())
for repo, name in [('apify/crawlee', 'crawlee'), ('scrapy/scrapy', 'scrapy'), ('webrecorder/warcio.js', 'warcio'), ('docling-project/docling', 'docling')]:
    ep = f'repos/{repo}/releases?per_page=1'
    start = time.perf_counter()
    b = subprocess.check_output(['gh', 'api', ep], timeout=30)
    f = name + '-latest.json'
    (p / f).write_bytes(b)
    m.append(dict(file=f, url='https://api.github.com/' + ep, bytes=len(b), sha256=hashlib.sha256(b).hexdigest(), seconds=round(time.perf_counter()-start, 4), observed_at=datetime.now(timezone.utc).isoformat()))
(p / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2) + '\n')
