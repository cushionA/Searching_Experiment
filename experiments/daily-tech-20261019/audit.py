"""Offline evidence audit only; does not execute candidate code or run inference."""
import hashlib
import json
import platform
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
base = root / 'benchmarks/daily-tech-20261019'
start = time.perf_counter()
rows = []
for folder in ('sources', 'supplement', 'correction'):
    for row in json.loads((base / folder / 'manifest.json').read_text()):
        if 'sha256' in row:
            data = (base / folder / row['name']).read_bytes()
            assert len(data) == row['bytes']
            assert hashlib.sha256(data).hexdigest() == row['sha256']
        rows.append(row)
repos = {}
for name in ('moli', 'heritrix3', 'firecrawl'):
    repo = json.loads((base / 'sources' / f'{name}-repo.json').read_text())
    releases = json.loads((base / 'sources' / f'{name}-releases.json').read_text())
    r = next(r for r in releases if not r['draft'] and not r['prerelease'])
    repos[name] = {'stars': repo['stargazers_count'], 'pushed_at': repo['pushed_at'],
                   'release': r['tag_name'], 'published_at': r['published_at'],
                   'assets': {a['name']: a['size'] for a in r['assets']}}
result = {'python': platform.python_version(), 'documents_attempted': len(rows),
          'documents_verified': sum('sha256' in r for r in rows),
          'document_errors': [r for r in rows if 'error' in r],
          'payload_bytes': sum(r.get('bytes', 0) for r in rows), 'repos': repos,
          'candidate_execution': 0, 'model_inference': 0, 'performance_poc': 0,
          'candidate_repair': 0, 'model_dependency_download_bytes': 0, 'cost_jpy': 0,
          'classifications': {'A': 0, 'B': 2, 'C': 1},
          'model_runtime_verified': False, 'wall_seconds': time.perf_counter() - start}
(base / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('repos','document_errors')}))
