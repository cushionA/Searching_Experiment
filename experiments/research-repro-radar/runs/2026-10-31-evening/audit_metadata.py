"""Bounded public metadata reads only; never imports or executes candidate code."""
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
JOBS = [
 ('laya-release', 'https://api.github.com/repos/NandhaKishorM/laya/releases/latest'),
 ('pagesift-release', 'https://api.github.com/repos/YOLOW92/pagesift/releases/latest'),
 ('startlux-commit', 'https://api.github.com/repos/StartLuxLabs/StartLux-Decision/commits/main'),
 ('startlux-model', 'https://huggingface.co/api/models/startlux-models/StartLux-Decision-v1.1-0.8B-Instant?blobs=true'),
 ('startlux-gguf', 'https://huggingface.co/api/models/startlux-models/StartLux-Decision-v1.1-0.8B-Instant-GGUF?blobs=true'),
 ('laya-model', 'https://huggingface.co/api/models/convaiinnovations/laya-multilingual?blobs=true'),
 ('laya-author-models', 'https://huggingface.co/api/models?author=convaiinnovations&limit=100&full=true'),
 ('startlux-author-models', 'https://huggingface.co/api/models?author=startlux-models&limit=100&full=true'),
 ('quantcheck-release', 'https://api.github.com/repos/aditi-p31/quantcheck/releases/latest'),
 ('tokeneconomizer-repo', 'https://api.github.com/repos/microsoft/TokenEconomizer'),
]

def fetch(job):
 label,url=job
 rec={'label':label,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchReproRadar public metadata audit'}),timeout=25) as response:
   b=response.read(4_000_001);rec.update(status=response.status,link=response.headers.get('Link'))
  if len(b)>4_000_000:raise ValueError('response cap exceeded')
  json.loads(b)
  p=ROOT/'audit-sources'/(label+'.json');p.write_bytes(b)
  rec.update(file=str(p.relative_to(ROOT)),bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
 except Exception as exc:rec['error']=type(exc).__name__+': '+str(exc)
 return rec

if __name__=='__main__':
 with cf.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(fetch,JOBS))
 (ROOT/'metadata-ledger.json').write_text(json.dumps(rows,indent=2)+'\n')
 print(json.dumps(rows))
