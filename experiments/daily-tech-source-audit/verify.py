"""Verify saved primary-source evidence without network or candidate imports."""
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / 'source-manifest.json').read_text())
seen = set()
byte_count = 0
errors = []
for row in manifest:
    name = row['name']
    assert name not in seen, name
    seen.add(name)
    if 'error' in row:
        errors.append({'name': name, 'error': row['error']})
        continue
    suffix = '.md' if name.endswith('card') else '.json'
    data = (root / 'sources' / (name + suffix)).read_bytes()
    assert len(data) == row['bytes'], name
    assert hashlib.sha256(data).hexdigest() == row['sha256'], name
    if suffix == '.json':
        json.loads(data)
    byte_count += len(data)
print(json.dumps({'sources': len(seen), 'verified': len(seen)-len(errors),
                  'saved_response_bytes': byte_count, 'fetch_errors': errors,
                  'model_runs': 0, 'candidate_code_runs': 0}, indent=2))
