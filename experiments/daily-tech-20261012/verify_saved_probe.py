#!/usr/bin/env python3
"""Check saved PoC result JSON without rerunning conversion scripts."""
import json, hashlib, pathlib, statistics
base=pathlib.Path('benchmarks/daily-tech-20261012')
full=json.loads((base/'docling-probe-results.json').read_text())
first=json.loads((base/'docling-first-byte-results.json').read_text())
assert len(full)==6 and len(first)==4
assert all(x['returncode']==0 and not x['stderr'] for x in full+first)
hashes={x['stdout_sha256'] for x in full+first}
assert len(hashes)==1
assert len({x['stdout_bytes'] for x in full+first})==1
assert len({x['mode'] for x in full})==2
assert len({x['mode'] for x in first})==2
res={}
for mode in ('stream','buffer'):
 a=[x for x in full if x['mode']==mode]
 b=[x for x in first if x['mode']==mode]
 res[mode]={'wall_median_s':statistics.median(x['wall_s'] for x in a),'peak_child_rss_median_kib':statistics.median(x['child_peak_rss_kib'] for x in a),'first_byte_median_s':statistics.median(x['first_byte_s'] for x in b)}
fixture=base/'synthetic-200page-text.pdf'
print(json.dumps({'saved_trials':len(full),'saved_first_byte_trials':len(first),'exit_ok':True,'unique_output_sha256':next(iter(hashes)),'stdout_bytes':full[0]['stdout_bytes'],'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'recomputed_medians':res},indent=2))
