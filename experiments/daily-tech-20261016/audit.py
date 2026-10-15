"""Check retained evidence without executing candidate code."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/daily-tech-20261016'
OUT = ROOT / 'benchmarks/daily-tech-20261016'
manifest = json.loads((OUT / 'manifest.json').read_text())
for row in manifest:
    assert 'error' not in row, row
    data = (OUT / 'sources' / row['name']).read_bytes()
    assert len(data) == row['bytes']
    assert hashlib.sha256(data).hexdigest() == row['sha256']
for p in EXP.glob('*.py'):
    ast.parse(p.read_text())
r = json.loads((OUT / 'probe.json').read_text())
execution = json.loads((OUT / 'execution.json').read_text())
protocol = json.loads((EXP / 'protocol.json').read_text())
assert execution['exit_code'] == 0 and not (OUT / 'probe.stderr').read_bytes()
assert r['source_sha256'] == execution['input_sha256']
for name, path in {'protocol.json': EXP / 'protocol.json', 'probe.py': EXP / 'probe.py',
                   'grounding.py': OUT / 'sources/lightonocr_grounding.py.txt',
                   'models.py': OUT / 'sources/lightonocr_models.py.txt'}.items():
    assert hashlib.sha256(path.read_bytes()).hexdigest() == r['source_sha256'][name]
for original, result in zip(protocol['parser_cases'], r['parser'], strict=True):
    assert original['expected'] == result['expected'] and original['raw'] == result['input']
    assert result['pass'] == (result['expected'] == result['actual'])
for original, result in zip(protocol['model_cases'], r['models'], strict=True):
    assert original['expected'] == result['expected'] and original['model'] == result['model']
    assert result['pass'] == (result['expected'] == result['actual'])
summary = {'source_hashes_verified': len(manifest), 'parser_documented': [sum(x['pass'] for x in r['parser'] if x['group']=='documented'), 6],
           'parser_adoption_guards': [sum(x['pass'] for x in r['parser'] if x['group']=='adoption_guard'), 5],
           'exact_model_profiles': [sum(x['pass'] for x in r['models'][:5]), 5],
           'unknown_model_guard': [sum(x['pass'] for x in r['models'][5:]), 1],
           'probe_seconds': r['wall_seconds'], 'probe_peak_rss_kib': r['peak_rss_kib'],
           'model_inferences': 0, 'candidate_repairs': execution['repairs']}
(OUT / 'audit.json').write_text(json.dumps(summary, indent=2)+'\n')
print(json.dumps(summary, indent=2))
