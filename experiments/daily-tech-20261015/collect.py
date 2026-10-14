"""Fetch primary-source data only; never import candidate code."""
import base64, concurrent.futures, hashlib, json, subprocess, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone
OUT=Path('benchmarks/daily-tech-20261015'); OUT.mkdir(exist_ok=True,parents=True)
JOBS=[]
def gh(name,path,decode=False): JOBS.append((name,'https://api.github.com/'+path,decode))
for name,repo in [('rapidocr','RapidAI/RapidOCR'),('crawl4ai','unclecode/crawl4ai'),('browseruse','browser-use/browser-use'),('gliner','fastino-ai/GLiNER2'),('warcio','webrecorder/warcio'),('docling','docling-project/docling')]:
 gh(name+'-meta.json','repos/'+repo)
 gh(name+'-releases.json','repos/'+repo+'/releases?per_page=3')
 gh(name+'-readme.txt','repos/'+repo+'/readme',True)
for name,path in [('models','python/rapidocr/default_models.yaml'),('config','python/rapidocr/config.yaml'),('license','python/MODEL_LICENSES.md'),('project','python/pyproject.toml')]:
 gh('rapidocr-'+name+'.txt','repos/RapidAI/RapidOCR/contents/'+path+'?ref=v3.10.0',True)
gh('crawl4ai-changelog.txt','repos/unclecode/crawl4ai/contents/CHANGELOG.md?ref=v0.9.5',True)
gh('crawl4ai-scraping.py.txt','repos/unclecode/crawl4ai/contents/crawl4ai/content_scraping_strategy.py?ref=v0.9.5',True)
gh('crawl4ai-noscript-tests.txt','repos/unclecode/crawl4ai/contents/tests/test_unclosed_noscript.py?ref=v0.9.5',True)
gh('crawl4ai-license.txt','repos/unclecode/crawl4ai/contents/LICENSE?ref=v0.9.5',True)
gh('crawl4ai-tag.json','repos/unclecode/crawl4ai/git/ref/tags/v0.9.5')
gh('docling-confidence-change.json','repos/docling-project/docling/commits/5bdabb917617d0ff434f676d537bda80cd19615f')
gh('gliner-license.txt','repos/fastino-ai/GLiNER2/contents/LICENSE?ref=v2.1.0',True)
for date,ref in [('14-daily-tech','e3fe007aa9130af49ae9dd78ad38fcebde8797f7'),('14-evening-radar','f197d9d181e59998cfb61040ed763d308e512fa5'),('14-bitnet-cpu-poc','a177520672f7fc09b64ebbb7f8854c533cfe3596')]:
 gh('prior-'+date+'.txt',f'repos/cushionA/Searching_Experiment/contents/reports/2026-10/2026-10-{date}.md?ref={ref}',True)
gh('open-prs.json','repos/cushionA/Searching_Experiment/pulls?state=open&per_page=30')
JOBS += [('rapidocr-pypi.json','https://pypi.org/pypi/rapidocr/json',False),('cc-indexes.json','https://index.commoncrawl.org/collinfo.json',False)]
JOBS += [('lfm-series.json','https://huggingface.co/api/models?author=LiquidAI&search=LFM&sort=lastModified&direction=-1&limit=50',False),('gliner-model.json','https://huggingface.co/api/models/fastino/GLiNER2.5-multi-Decide',False)]
def fetch(job):
 name,url,decode=job; start=time.perf_counter()
 try:
  data=(subprocess.check_output(['gh','api',url.removeprefix('https://api.github.com/')],timeout=60) if 'api.github.com' in url else urllib.request.urlopen(url,timeout=60).read())
  if decode: data=base64.b64decode(json.loads(data)['content'])
  (OUT/name).write_bytes(data)
  return dict(file=name,url=url,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),seconds=round(time.perf_counter()-start,4),observed_at=datetime.now(timezone.utc).isoformat())
 except Exception as e: return dict(file=name,url=url,error=str(e))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool: rows=list(pool.map(fetch,JOBS))
(OUT/'manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'sources':len(rows),'errors':[r for r in rows if 'error' in r]}))
