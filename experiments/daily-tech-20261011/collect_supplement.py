import sys,json,concurrent.futures,urllib.request,hashlib,datetime,time
from pathlib import Path
sys.path.insert(0,'experiments/daily-tech-20261011')
from collect_sources import fetch
urls=[
'https://raw.githubusercontent.com/docling-project/docling.rs/v2.13.0/README.md',
'https://raw.githubusercontent.com/docling-project/docling.rs/v2.13.0/Cargo.toml',
'https://api.github.com/repos/docling-project/docling.rs/compare/v2.10.0...v2.13.0',
'https://raw.githubusercontent.com/webrecorder/browsertrix-crawler/v1.15.0/README.md',
'https://raw.githubusercontent.com/adbar/trafilatura/v2.3.1/README.rst',
'https://raw.githubusercontent.com/fastino-ai/GLiNER2/v2.1.0/README.md',
'https://api.github.com/repos/microsoft/onnxruntime/releases?per_page=1',
'https://index.commoncrawl.org/collinfo.json',
'https://api.github.com/repos/cushionA/Searching_Experiment/commits?sha=codex-sku-generic-model-gates-20261010&per_page=3',
'https://api.github.com/repos/cushionA/Searching_Experiment/commits?sha=codex-sku-gemini-vertex-smoke-20261010&per_page=3',
'https://api.github.com/repos/cushionA/Searching_Experiment/branches?per_page=100']
def get(url):
 start=time.perf_counter(); r={'url':url,'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Searching-Experiment-daily-tech/20261011'}),timeout=30) as res:
   raw=res.read(); r.update(status=res.status,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
  if 'raw.githubusercontent' in url:r['text']=raw.decode()
  else:r['data']=json.loads(raw)
 except Exception as e:r['error']=str(e)
 r['seconds']=time.perf_counter()-start; return r
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as p:r=list(p.map(get,urls))
Path('benchmarks/daily-tech-20261011/supplement.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
for i,x in enumerate(r):
 print(i,x['url'],x.get('error','OK'))
 if 'text' in x:Path(f'/tmp/source-{i}.txt').write_text(x['text'])
 if i==7:print(json.dumps(x.get('data',[])[:2],ensure_ascii=False))
 if i in [8,9]:print([(v['sha'],v['commit']['message'][:180]) for v in x.get('data',[])])
