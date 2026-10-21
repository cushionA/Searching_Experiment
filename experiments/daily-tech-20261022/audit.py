"""Offline distribution audit. Reads upstream code as AST, never executes it."""
import ast
import hashlib
import json
import platform
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
start = time.perf_counter()
manifest = json.loads((ROOT / 'manifest.json').read_text())
checked = []
for row in manifest:
    if 'error' in row:
        continue
    payload = (ROOT / 'sources' / row['file']).read_bytes()
    assert len(payload) == row['bytes']
    assert hashlib.sha256(payload).hexdigest() == row['sha256']
    checked.append(row['file'])
tree = json.loads((ROOT / 'sources/tree.json').read_text())
assert not tree['truncated']
paths = [r['path'] for r in tree['tree'] if r['type'] == 'blob']
loong = [json.loads(line) for line in (ROOT / 'sources/loong--doc_manifest.jsonl').read_text().splitlines() if line]
ids = json.loads((ROOT / 'sources/browsecomp_plus--query_ids.json').read_text())
parsed = ast.parse((ROOT / 'sources/loong--rebuild_docs.py').read_text())
imports = sorted({n.name for node in ast.walk(parsed) if isinstance(node, ast.Import) for n in node.names})
result = {
    'kind': 'distribution_audit_not_candidate_performance',
    'python': platform.python_version(), 'sources_ok': len(checked),
    'sources_attempted': len(manifest), 'payload_bytes': sum(r.get('bytes', 0) for r in manifest),
    'capture_seconds': sum(r['seconds'] for r in manifest),
    'tree_truncated': tree['truncated'], 'tree_blob_count': len(paths),
    'kilt_document_count': sum(p.startswith('kilt/docs/') for p in paths),
    'loong_manifest_count': len(loong),
    'loong_unique_refs': len({r['doc_ref'] for r in loong}),
    'loong_valid_sha256_count': sum(len(r.get('sha256', '')) == 64 and all(c in '0123456789abcdef' for c in r['sha256']) for r in loong),
    'loong_document_count_in_distribution': sum(p.startswith('loong/docs/') or p.startswith('loong/doc/') for p in paths),
    'browsecomp_query_ids': len(ids), 'browsecomp_unique_ids': len(set(ids)),
    'python_files': [p for p in paths if p.endswith('.py')],
    'rebuild_script_ast_imports': imports,
    'candidate_executions': 0, 'inference_trials': 0, 'gpu_seconds': 0,
    'cash_jpy': 0, 'audit_seconds': time.perf_counter() - start,
}
out = ROOT.parents[1] / 'benchmarks/daily-tech-20261022/audit.json'
out.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
