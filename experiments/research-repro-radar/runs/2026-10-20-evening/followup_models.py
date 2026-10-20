"""Read only public model metadata/cards; never download weights or execute source."""
import concurrent.futures as cf,datetime as dt,hashlib,json,re,urllib.request
from pathlib import Path
R=Path(__file__).parent;ev=json.loads((R/'review-evidence.json').read_text())
models=[('2610.22428','QLNI/embeddings-are-overpriced'),('2610.14162','hazyresearch/ncm-checkpoints'),('2610.23787','SJTU-DAI-Sys/Qwen3-4B-DLite'),('2610.23787','SJTU-DAI-Sys/Qwen3.5-4B-DLite'),('2610.11287','mocoV3/Qwen3.8-27B-REMORY-1.9B'),('2610.11287','mocoV3/GLM-5.3-Flash-REMORY-1.24B')]
jobs=[]
for ident,mid in models:
 for kind,url in [('model','https://huggingface.co/api/models/'+mid+'?blobs=true'),('card','https://huggingface.co/'+mid+'/raw/main/README.md')]:jobs.append((ident,kind+'-'+mid.split('/')[-1],url))
def get(job):
 ident,kind,url=job;e={'id':ident,'kind':kind,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar metadata only'}),timeout=20)as r:b=r.read(2000001);e['status']=r.status
  if len(b)>2000000:raise ValueError('cap')
  p=R/'primary'/(re.sub(r'[^a-zA-Z0-9_.-]','_',ident)+'-'+kind);p.write_bytes(b);e.update(file=str(p.relative_to(R)),bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),text=b.decode())
 except Exception as ex:e['error']=type(ex).__name__+': '+str(ex)
 return e
with cf.ThreadPoolExecutor(max_workers=4)as pool:out=list(pool.map(get,jobs))
ev+=out;(R/'review-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n')
for e in out:
 print(e['id'],e['kind'],e.get('error','ok'))
 if e['kind'].startswith('model') and 'text'in e:
  d=json.loads(e['text']);print(json.dumps({'id':d.get('id'),'sha':d.get('sha'),'modified':d.get('lastModified'),'license':d.get('cardData',{}).get('license'),'files':[{k:x.get(k)for k in ['rfilename','size']}for x in d.get('siblings',[])if x.get('size',0)>100000 or x['rfilename'].endswith('.yaml')]},ensure_ascii=False))
