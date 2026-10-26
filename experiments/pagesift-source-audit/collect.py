"""Read-only primary-source collection; never import or execute candidate code."""
import datetime, hashlib, json, pathlib, time, urllib.request
ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / 'benchmarks/daily-tech-20261027'
SHA = '6982f9ea4ebd9989a861e1b9c4b0c5dc223e7de5'
BASE = f'https://raw.githubusercontent.com/YOLOW92/pagesift/{SHA}/'
PATHS = ['docs/releases/0.73.1.md','docs/mcp-service.md','README.md','LICENSE','COMMERCIAL.md','Cargo.toml','crates/pagesift/Cargo.toml','crates/pagesift-py/pyproject.toml','crates/pagesift-node/package.json','docs/releases/0.73.0.md','docs/benchmarks/0.60_p4_wild_x_2_showdown.md','bench/p4/src/wild_x2_runner.rs','bench/p4/run_wild_x2.py','crates/pagesift/src/crawl/mod.rs','crates/pagesift/src/crawl/runner.rs','crates/pagesift/src/crawl/sitemap.rs','crates/pagesift-api/src/firecrawl/map.rs']
SOURCES = {p.replace('/','__'): BASE+p for p in PATHS}
SOURCES.update({'repo.json':'https://api.github.com/repos/YOLOW92/pagesift','releases.json':'https://api.github.com/repos/YOLOW92/pagesift/releases?per_page=10','tags.json':'https://api.github.com/repos/YOLOW92/pagesift/tags?per_page=10','contributors.json':'https://api.github.com/repos/YOLOW92/pagesift/contributors?per_page=100','pypi.json':'https://pypi.org/pypi/pagesift/json','npm.json':'https://registry.npmjs.org/@pagesift/node/latest','crates.json':'https://crates.io/api/v1/crates/pagesift','tree.json':f'https://api.github.com/repos/YOLOW92/pagesift/git/trees/{SHA}?recursive=1'})
for repo in ['apify/crawlee','microsoft/playwright','unclecode/crawl4ai','PaddlePaddle/PaddleOCR','webrecorder/browsertrix-crawler','adbar/trafilatura','alibaba/page-agent']:
 SOURCES['scan__'+repo.replace('/','__')+'.json']='https://api.github.com/repos/'+repo+'/releases?per_page=3'
start=time.monotonic(); records=[]
for name,url in SOURCES.items():
 row={'name':name,'url':url,'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Searching-Experiment-source-audit/1.0'})
  with urllib.request.urlopen(req,timeout=30) as resp: data=resp.read(4_000_001); row['status']=resp.status
  if len(data)>4_000_000: raise ValueError('source exceeds 4 MB cap')
  (OUT/'sources'/name).write_bytes(data); row.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
 except Exception as exc: row['error']=str(exc)
 records.append(row)
(OUT/'source-manifest.json').write_text(json.dumps({'candidate_sha':SHA,'seconds':time.monotonic()-start,'sources':records},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'sources':len(records),'bytes':sum(r.get('bytes',0) for r in records),'errors':[r for r in records if 'error' in r]}))
