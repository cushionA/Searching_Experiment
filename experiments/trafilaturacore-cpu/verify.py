"""Validate archived evidence and fixtures without importing candidate code."""
import ast
import hashlib
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
root = repo / 'benchmarks/daily-tech-20261101'
manifest = json.loads((root / 'source-manifest.json').read_text())
for row in manifest:
    raw = (root / 'sources' / row['file']).read_bytes()
    assert len(raw) == row['bytes']
    assert hashlib.sha256(raw).hexdigest() == row['sha256']
    if row['file'].endswith('.json'):
        json.loads(raw)
fixtures = json.loads((Path(__file__).parent / 'fixtures.json').read_text())
assert len(fixtures) == len({x['id'] for x in fixtures}) == 12
assert sum(len(x['required']) for x in fixtures) == 36
assert sum(len(x['unwanted']) for x in fixtures) == 24
ast.parse((Path(__file__).parent / 'bench.py').read_text())
execution = json.loads((root / 'execution.json').read_text())
assert execution['candidate_code_started'] is False
assert execution['latency_metrics'] is None
print(json.dumps(dict(verified_sources=len(manifest), source_bytes=sum(x['bytes'] for x in manifest),
                     fixtures=12, expected_content_segments=36, unwanted_segments=24,
                     candidate_runs_completed=0, benchmark_script_syntax='valid'), indent=2))
