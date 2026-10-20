"""Read official implementation/release metadata; never run candidate source."""
import json,urllib.request,hashlib,datetime,re,concurrent.futures as cf
from pathlib import Path
r=Path(__file__).parent
jobs=[('dlite-tree','https://api.github.com/repos/SJTU-DAI-Sys/DLite/git/trees/HEAD?recursive=1'),('dlite-issues','https://api.github.com/repos/SJTU-DAI-Sys/DLite/issues?state=all&per_page=100'),('ncm-issues','https://api.github.com/repos/HazyResearch/ncm/issues?state=all&per_page=100'),('qwen-coder-successors','https://huggingface.co/api/models?author=Qwen&search=Coder&sort=lastModified&direction=-1&limit=100&full=true'),('pruner-head','https://huggingface.co/api/models/ayanami-kitasan/swe-pruner-pro-qwen3-coder-next-head?blobs=true'),('pruner-model','https://huggingface.co/api/models/ayanami-kitasan/code-pruner?blobs=true')]
def get(pair):
 label,url=pair;e={'label':label,'url':url,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar primary review'}),timeout=25)as res:b=res.read(3000001);e['status']=res.status
  if len(b)>3000000:raise ValueError('response cap')
  p=r/'primary'/label;p.write_bytes(b);e.update(file=str(p.relative_to(r)),bytes=len(b),sha256=hashlib.sha256(b).hexdigest());return e,json.loads(b)
 except Exception as ex:e['error']=type(ex).__name__+': '+str(ex);return e,None
with cf.ThreadPoolExecutor(max_workers=4)as pool:res=list(pool.map(get,jobs))
ledger=[e for e,d in res]
for e,d in res:
 print(e['label'],e.get('status',e.get('error')))
 if e['label']=='dlite-tree' and d:
  paths=[x['path']for x in d['tree']if x['path'].endswith('.py')and any(v in x['path'].lower()for v in ['qwen','model','decode'])];print('implementation paths',paths)
  for i,p in enumerate(paths[:12]):
   u='https://raw.githubusercontent.com/SJTU-DAI-Sys/DLite/HEAD/'+p
   ent={'label':f'dlite-source-{i}','url':u,'at':e['at'],'original_path':p}
   try:
    with urllib.request.urlopen(u,timeout=20)as resp:b=resp.read(500000);ent['status']=resp.status
    dest=r/'primary'/ent['label'];dest.write_bytes(b);ent.update(file=str(dest.relative_to(r)),bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
   except Exception as ex:ent['error']=type(ex).__name__
   ledger.append(ent)
(r/'updates-ledger.json').write_text(json.dumps(ledger,indent=2)+'\n')
