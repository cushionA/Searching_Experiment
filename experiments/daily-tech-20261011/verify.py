"""Recompute the local serialization probe with the unchanged repo parser."""
import hashlib,json,pathlib,statistics,sys
ROOT=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from jse.lab.fetch import HTML
E=ROOT/'experiments/daily-tech-20261011';B=ROOT/'benchmarks/daily-tech-20261011'
hash_file=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=json.loads((E/'protocol.json').read_text());r=json.loads((B/'shadow-result.json').read_text())
assert r['protocol_sha256']==hash_file(E/'protocol.json')
assert r['fixture_sha256']==hash_file(E/'fixture.html')
assert r['runner_sha256']==hash_file(E/'shadow_probe.mjs')
assert not r['errors'] and not r['requests']
results={}; summary={}
for mode in ['default','shadow','frame']:
 parser=HTML();parser.feed((B/f'{mode}.html').read_text())
 results['child_frame_parser' if mode=='frame' else f'{mode}_parser']=sorted(x['href'].rsplit('/',1)[-1] for x in parser.links)
 if mode!='frame':
  times=[x['ms'] for x in r['timings'] if x['mode']==mode]
  assert len(times)==p['repeats']
  assert r['snapshot_hashes'][mode]==[hash_file(B/f'{mode}.html')]
  summary[mode]={'median_ms':statistics.median(times),'first_ms':times[0],'bytes':(B/f'{mode}.html').stat().st_size}
results['live_locator']=sorted(u.rsplit('/',1)[-1] for u in r['live_locator'])
assert results==p['expected'],(results,p['expected'])
summary.update(results=results,additional_links_over_default=sorted(set(results['shadow_parser'])-set(results['default_parser'])),additional_links_over_live_locator=sorted(set(results['shadow_parser'])-set(results['live_locator'])),passed=True,source_errors=[{'url':x['url'],'error':x['error']} for name in ['sources','supplement','details'] for x in json.loads((B/f'{name}.json').read_text()) if 'error' in x])
(B/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
