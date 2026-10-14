"""Additional fixed-revision primary evidence and repo status; no model execution."""
import base64,hashlib,json,subprocess,time,urllib.request
from pathlib import Path
from datetime import datetime,timezone
p=Path('benchmarks/daily-tech-20261015'); manifest=json.loads((p/'manifest.json').read_text())
items=[('crawl4ai-tag-object.json','https://api.github.com/repos/unclecode/crawl4ai/git/tags/62002d6d572f5f59964160c0636a4c6f1f9ef449',False),('gliner-model-card.txt','https://huggingface.co/fastino/GLiNER2.5-multi-Decide/raw/e173bca1f0c4217d7a55b3b0c705d5ece8a0218d/README.md',False),('sku-handoff.txt','https://api.github.com/repos/cushionA/Searching_Experiment/contents/experiments/sku-matching/session-handoff-20261014-final.txt?ref=ebf04aed9b5a747f5db8961b9c96fef7be81adef',True)]
items += [(name+'-api.json','https://huggingface.co/api/models/fastino/'+name,False) for name in ['GLiNER2.5-Decide','GLiNER2.5-Decide-1B']]
manifest=[r for r in manifest if r['file'] not in {x[0] for x in items}]
for name,url,decode in items:
 t=time.perf_counter()
 data=subprocess.check_output(['gh','api',url.split('api.github.com/')[1]],timeout=60) if 'api.github.com/' in url else urllib.request.urlopen(url,timeout=60).read()
 if decode:data=base64.b64decode(json.loads(data)['content'])
 (p/name).write_bytes(data);manifest.append(dict(file=name,url=url,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),seconds=time.perf_counter()-t,observed_at=datetime.now(timezone.utc).isoformat()))
(p/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print('sources',len(manifest))
