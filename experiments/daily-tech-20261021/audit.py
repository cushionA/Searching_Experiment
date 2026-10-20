"""Offline evidence integrity and reproducibility-gap audit, not a crawl benchmark."""
import hashlib, json, platform, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent
start = time.perf_counter()
manifest = json.loads((ROOT / 'manifest.json').read_text())
for row in manifest:
    if 'error' in row:
        continue
    data = (ROOT / row['file']).read_bytes()
    assert len(data) == row['bytes'], row['file']
    assert hashlib.sha256(data).hexdigest() == row['sha256'], row['file']
tree = json.loads((ROOT / 'sources/sb-tree.json').read_text())
assert not tree.get('truncated'), 'Incomplete tree cannot establish database absence'
metadata = json.loads((ROOT / 'sources/sb-metadata.json').read_text())
requirements = (ROOT / 'sources/sb-requirements.txt').read_text().splitlines()
result = {
    'kind': 'offline_source_audit_not_candidate_execution',
    'python': platform.python_version(), 'payloads_verified': sum('error' not in x for x in manifest),
    'bytes_verified': sum(x.get('bytes', 0) for x in manifest),
    'fetch_seconds': sum(x['seconds'] for x in manifest),
    'tracked_db_paths': [x['path'] for x in tree['tree'] if x['path'].endswith('.db')],
    'requirements': requirements,
    'fully_version_pinned': all('==' in x for x in requirements if x.strip()),
    'upstream_revision': tree['sha'], 'stars': metadata['stargazers_count'],
    'forks': metadata['forks_count'], 'pushed_at': metadata['pushed_at'],
    'candidate_executions': 0, 'model_inferences': 0, 'gpu_seconds': 0,
    'candidate_dependency_download_bytes': 0, 'cash_spent_jpy': 0,
    'audit_seconds': time.perf_counter() - start,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
