import concurrent.futures as cf,datetime as dt,hashlib,json,re,urllib.request
from pathlib import Path
R=Path(__file__).parent;ev=json.loads((R/'review-evidence.json').read_text());jobs=[]
for ident,mid in [('2602.09373','AfriNLP/AfriNLLB-12enc-4dec-iterative-481m-ft'),('2607.25180','hotchpotch/bekko-embedding-v1-a8m'),('2607.25180','hotchpotch/bekko-embedding-v1-a25m'),('2607.25180','hotchpotch/bekko-system-one-v0-17m')]:
 slug=mid.split('/')[-1];jobs.extend([(ident,'model-'+slug,'https://huggingface.co/api/models/'+mid+'?blobs=true'),(ident,'card-'+slug,'https://huggingface.co/'+mid+'/raw/main/README.md')])
for e in ev:
 if e['kind']=='repo':
  d=json.loads(e['text']);jobs.append((e['id'],'license','https://api.github.com/repos/'+d['full_name']+'/license'))
def get(job):
 ident,kind,url=job;e={'id':ident,'kind':kind,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar read-only primary research','Accept':'application/vnd.github.raw+json'}),timeout=20)as r:b=r.read(1000001);e['status']=r.status
  if len(b)>1000000:raise ValueError('cap')
  p=R/'primary'/(re.sub(r'[^a-zA-Z0-9_.-]','_',ident)+'-'+kind);p.write_bytes(b);e.update(file=str(p.relative_to(R)),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b),text=b.decode('utf-8','replace'))
 except Exception as ex:e['error']=type(ex).__name__+': '+str(ex)
 return e
with cf.ThreadPoolExecutor(max_workers=4)as pool:out=list(pool.map(get,jobs))
ev+=out;(R/'review-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');(R/'model-details-ledger.json').write_text(json.dumps([{k:v for k,v in e.items()if k!='text'}for e in out],indent=2)+'\n')
print({'requests':len(out),'ok':sum('status'in e for e in out),'errors':[{k:e[k]for k in ['id','kind','error']}for e in out if 'error'in e]})
