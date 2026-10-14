import ast,hashlib,json,re
from pathlib import Path
p=Path('benchmarks/daily-tech-20261015')
rows=json.loads((p/'manifest.json').read_text())
for r in rows:
 assert 'error' not in r,r
 b=(p/r['file']).read_bytes(); assert len(b)==r['bytes']; assert hashlib.sha256(b).hexdigest()==r['sha256']
r=json.loads((p/'probe.json').read_text())
assert r['total']==12 and r['pass_count']==8
assert sum(c['actual']==c['expected'] for c in r['cases'])==8
inputs=[{k:c[k] for k in ['id','html','expected']} for c in r['cases']]
assert hashlib.sha256(json.dumps(inputs,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==r['input_sha256']
assert hashlib.sha256((p/'crawl4ai-scraping.py.txt').read_bytes()).hexdigest()==r['source_sha256']
assert (p/'probe.exit').read_text().strip()=='0'
for f in Path('experiments/daily-tech-20261015').glob('*.py'): ast.parse(f.read_text())
report=Path('reports/2026-10/2026-10-15-daily-tech.md')
for link in re.findall(r'\]\((\.\./[^)]+)\)',report.read_text()): assert (report.parent/link).exists(),link
out={'source_hashes':len(rows),'synthetic_cases':12,'expected_matches':8,'expected_mismatches':4,'python_ast':'pass','input_hash':'pass','source_hash':'pass','local_report_links':'pass'}
(p/'validation.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
