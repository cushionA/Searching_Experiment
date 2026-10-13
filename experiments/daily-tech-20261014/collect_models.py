"""Collect model metadata/cards only, without weights or remote code."""
import hashlib
import json
from pathlib import Path
import urllib.request
from datetime import datetime,timezone
import time
p=Path('benchmarks/daily-tech-20261014')
m=json.loads((p/'manifest.json').read_text())
def get(name,url):
 start=time.perf_counter(); b=urllib.request.urlopen(url,timeout=30).read();(p/name).write_bytes(b)
 m.append(dict(file=name,url=url,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),seconds=round(time.perf_counter()-start,4),observed_at=datetime.now(timezone.utc).isoformat()))
 return b
get('lfm-series.json','https://huggingface.co/api/models?author=LiquidAI&search=LFM&sort=lastModified&direction=-1&limit=100&full=true')
for size in ['350M','230M','24B-A2B']:
 model='LiquidAI/LFM2.5-'+size
 d=json.loads(get('lfm-'+size+'-metadata.json','https://huggingface.co/api/models/'+model))
 for file in ['README.md','LICENSE']:
  get('lfm-'+size+'-'+file+'.txt','https://huggingface.co/'+model+'/resolve/'+d['sha']+'/'+file)
(p/'manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
print('Saved HF metadata, cards and licenses; no weights.')
