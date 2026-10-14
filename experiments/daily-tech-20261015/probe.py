"""Bounded offline behavioral probe of the exact upstream noscript helper.
Only three reviewed assignments and one function are AST-extracted. This is
not the complete Crawl4AI package, browser, scraper, or an HTML conformance test.
"""
import ast, hashlib, json, platform, resource, statistics, time
from pathlib import Path
source=Path('/input/crawl4ai-scraping.py.txt').read_bytes()
tree=ast.parse(source)
names={'NOSCRIPT_OPEN_REGEX','NOSCRIPT_CLOSE_REGEX','RAW_TEXT_REGEX','unwrap_orphaned_noscript'}
nodes=[n for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name in names) or (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets))]
assert len(nodes)==4
module=ast.Module(body=[ast.Import(names=[ast.alias(name='re')])]+nodes,type_ignores=[])
ns={}; exec(compile(ast.fix_missing_locations(module),'<upstream-helper>','exec'),ns)
f=ns['unwrap_orphaned_noscript']
# Explicit expectations fixed before execution; no generated labels or fitting.
cases=[]
def add(name,html,expected): cases.append(dict(id=name,html=html,expected=expected))
for name,html in [
 ('plain','<p>日本語の記事本文</p>'),
 ('balanced','<noscript>代替本文</noscript><p>記事</p>'),
 ('script_decoy','<script>const x="<noscript>";</script><noscript>fallback</noscript>'),
 ('style_decoy','<style>/* <noscript> */</style><noscript>fallback</noscript>'),
 ('comment_decoy','<!-- <noscript> --><noscript>fallback</noscript>'),
 ('excess_close','<p>記事</p></noscript>')]: add(name,html,html)
add('orphan','<noscript><p>救出本文</p>','<p>救出本文</p>')
add('nested_damage','<noscript><noscript>fallback</noscript><h1>記事</h1>','fallback<h1>記事</h1>')
for name,html in [
 ('quoted_attribute','<div data-example="<noscript>">記事</div><noscript>fallback</noscript>'),
 ('textarea_decoy','<textarea><noscript></textarea><noscript>fallback</noscript>'),
 ('title_decoy','<title><noscript></title><noscript>fallback</noscript>')]: add(name,html,html)
add('orphan_with_script_literal','<script>const x="<noscript>";</script><noscript><p>記事</p>','<script>const x="<noscript>";</script><p>記事</p>')
start=time.perf_counter(); rows=[]
for c in cases:
 out=f(c['html']); rows.append({**c,'actual':out,'pass':out==c['expected']})
perf=[]
for opener in ['<!--','<script>','<style>']:
 text=(opener+'a'*42)*20000
 times=[]
 for _ in range(5):
  t=time.perf_counter(); f(text); times.append(time.perf_counter()-t)
 perf.append(dict(opener=opener,input_bytes=len(text.encode()),input_sha256=hashlib.sha256(text.encode()).hexdigest(),seconds=times,median_seconds=statistics.median(times)))
print(json.dumps(dict(source_sha256=hashlib.sha256(source).hexdigest(),input_sha256=hashlib.sha256(json.dumps(cases,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),python=platform.python_version(),platform=platform.platform(),cases=rows,pass_count=sum(r['pass'] for r in rows),total=len(rows),performance=perf,wall_seconds=time.perf_counter()-start,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),ensure_ascii=False,indent=2))
