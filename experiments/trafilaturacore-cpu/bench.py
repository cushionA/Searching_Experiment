"""Fixed supplied-HTML preflight: no fetch, no private input, no model calls."""
import hashlib
import importlib.metadata
import json
import math
import pathlib
import resource
import statistics
import sys
import time

arm = sys.argv[1]
started = time.perf_counter()
from lxml import html as lh
if arm == 'core':
    from trafilaturacore import clean
else:
    from trafilatura import extract
import_ms = (time.perf_counter() - started) * 1000
cases = json.loads(pathlib.Path('/app/fixtures.json').read_text())

def run(source):
    if arm == 'core':
        result = clean(source, comment_handling='exclude', image_handling='exclude')
        return result.html, [m.text for m in result.messages]
    return extract(source, fast=True, include_comments=False,
                   include_tables=True, output_format='html') or '', []

def text_only(source):
    return ' '.join(lh.fromstring(source).text_content().split()) if source else ''

rows = []
all_times = []
for case in cases:
    source = case['html']
    t = time.perf_counter()
    output, diagnostics = run(source)
    first_ms = (time.perf_counter() - t) * 1000
    times = []
    stable = True
    for _ in range(20):
        t = time.perf_counter()
        again, _ = run(source)
        times.append((time.perf_counter() - t) * 1000)
        stable &= again == output
    txt = text_only(output)
    all_times.extend(times)
    rows.append(dict(id=case['id'], input_bytes=len(source.encode()), first_ms=first_ms,
                     times_ms=times, median_ms=statistics.median(times), output=output,
                     required_hits=[s in txt for s in case['required']],
                     unwanted_hits=[s in txt for s in case['unwanted']],
                     stable=stable, diagnostics=diagnostics))
summary = dict(arm=arm, import_ms=import_ms, cases=len(rows), warm_calls=len(all_times),
               required_found=sum(sum(r['required_hits']) for r in rows),
               required_total=sum(len(r['required_hits']) for r in rows),
               unwanted_found=sum(sum(r['unwanted_hits']) for r in rows),
               unwanted_total=sum(len(r['unwanted_hits']) for r in rows),
               median_ms=statistics.median(all_times),
               p95_ms=sorted(all_times)[math.ceil(.95*len(all_times))-1],
               max_ms=max(all_times), peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               fixtures_sha256=hashlib.sha256(pathlib.Path('/app/fixtures.json').read_bytes()).hexdigest(),
               versions={n:importlib.metadata.version(n) for n in ['trafilaturacore','trafilatura','lxml','nh3']})
print(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=2))
