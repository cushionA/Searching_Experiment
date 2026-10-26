"""Offline metadata/source audit, not PageSift execution or a crawl benchmark."""
import hashlib, json, pathlib, re, time, tomllib
ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / 'benchmarks/daily-tech-20261027'
SRC = OUT / 'sources'
start = time.perf_counter()
def text(name): return (SRC/name).read_text()
def data(name): return json.loads(text(name))
manifest=data('../source-manifest.json')
for row in manifest['sources']:
 if 'sha256' in row:
  assert hashlib.sha256((SRC/row['name']).read_bytes()).hexdigest()==row['sha256'],row['name']
cargo=tomllib.loads(text('Cargo.toml'))['workspace']['package']
versions={'cargo':cargo['version'],'pypi':data('pypi.json')['info']['version'],'npm':data('npm.json')['version'],'crates':data('crates.json')['crate']['newest_version'],'github':data('releases.json')[0]['tag_name'].removeprefix('v')}
assert len(set(versions.values()))==1, versions
assert data('tags.json')[0]['commit']['sha']==manifest['candidate_sha']==data('npm.json')['gitHead']
runner=text('bench__p4__src__wild_x2_runner.rs')
paths=re.findall(r'"([^"]*)"',re.search(r'const CANONICAL_PATHS:.*?= &\[(.*?)\];',runner,re.S).group(1))
assert len(paths)==len(set(paths))==50
assert 'global_idx % n_seeds' in runner and '(global_idx / n_seeds) % n_paths' in runner
assert runner.index('m.n_task.fetch_add(1') < runner.index('let res = c.get(&url)')
assert 'md_len >= 100' in runner
scope=text('crates__pagesift__src__crawl__mod.rs')
constants={k:int(re.search(r'const '+k+r': usize = (\d+)',scope).group(1)) for k in ['WIDEN_MAX_CHILDREN','WIDEN_MIN_SIBLINGS','WIDEN_MIN_PROSE','SITEMAP_MAX_DEPTH','SITEMAP_MAX_FILES']}
assert 'engine: Some("http".into())' in scope
# Values below are author's reported aggregate counts, not our measurements.
n,ok,md,seconds=100_000_000,314_170,199_422,2373.9
result={'kind':'offline_static_audit','candidate_runtime_trials':0,'inference_trials':0,'versions':versions,'sha':manifest['candidate_sha'],'rust_min':cargo['rust-version'],'constants':constants,'author_report_recalculation':{'tasks':n,'http_200':ok,'markdown_length_ge_100_bytes':md,'wall_seconds':seconds,'task_rate':n/seconds,'http200_rate':ok/seconds,'markdown_rate':md/seconds,'http200_percent':100*ok/n,'markdown_percent':100*md/n,'canonical_paths':len(paths),'if_one_million_distinct_seeds_max_unique_generated_urls':1_000_000*len(paths),'if_one_million_seeds_cycles_per_100m_tasks':n/(1_000_000*len(paths))},'source_count':len(manifest['sources']),'source_bytes':sum(r.get('bytes',0) for r in manifest['sources']),'collection_seconds':manifest['seconds'],'audit_seconds':time.perf_counter()-start,'errors':[]}
(OUT/'static-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
