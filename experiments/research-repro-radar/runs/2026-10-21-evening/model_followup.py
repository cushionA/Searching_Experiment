import concurrent.futures as cf,urllib.request,urllib.parse,json,hashlib,datetime
from pathlib import Path
r=Path(__file__).parent
queries=['Qwen2.5-Coder-1.5B-Instruct','Qwen3-Coder','AfriNLLB','GRACE','Bekko','SWE-Pruner','MemT-1.0']
def get(q):
 u='https://huggingface.co/api/models?'+urllib.parse.urlencode({'search':q,'limit':100,'full':'true'})
 out={'query':q,'url':u,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(u,timeout=25)as f:b=f.read(3000001)
  assert len(b)<=3000000
  out.update(sha256=hashlib.sha256(b).hexdigest(),bytes=len(b),models=json.loads(b))
 except Exception as e:out['error']=type(e).__name__+': '+str(e)
 return out
res=list(cf.ThreadPoolExecutor(max_workers=4).map(get,queries));(r/'model-followup.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
for x in res:print(x['query'],x.get('error'),[a['id']for a in x.get('models',[])])
