"""Read-only primary-text collection; never imports fetched candidate code."""
import hashlib, json, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
API = 'https://api.github.com/repos/AntoineGauquier/efficient_crawler_for_scalable_web_data_acquisition'
manifest = []
def fetch(name, url):
    t = time.monotonic()
    row = {'file': 'sources/' + name, 'url': url, 'utc': datetime.now(timezone.utc).isoformat()}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'daily-tech-primary-review'}), timeout=45) as response:
            payload = response.read(2_000_001)
            if len(payload) > 2_000_000:
                raise ValueError('text budget exceeded')
            row['status'] = response.status
        (ROOT / row['file']).write_bytes(payload)
        row.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    except Exception as exc:
        payload = b''
        row['error'] = str(exc)
    row['seconds'] = time.monotonic() - t
    manifest.append(row)
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return payload
if __name__ == '__main__':
    fetch('sb-paper.html', 'https://arxiv.org/html/2602.11874v1')
    fetch('coverage-paper.html', 'https://arxiv.org/html/2607.13636v1')
    fetch('cc-update.html', 'https://commoncrawl.org/blog/the-common-crawl-foundation-at-the-tpdl-conference')
    fetch('sb-metadata.json', API)
    commit = json.loads(fetch('sb-commit.json', API + '/commits/main'))['sha']
    tree = json.loads(fetch('sb-tree.json', API + '/git/trees/' + commit + '?recursive=1'))
    for path in ['README.md', 'LICENSE', 'requirements.txt']:
        if any(x['path'] == path for x in tree['tree']):
            fetch('sb-' + path, 'https://raw.githubusercontent.com/AntoineGauquier/efficient_crawler_for_scalable_web_data_acquisition/' + commit + '/' + path)
    print(json.dumps({'attempts': len(manifest), 'errors': [x for x in manifest if 'error' in x], 'commit': commit}))
