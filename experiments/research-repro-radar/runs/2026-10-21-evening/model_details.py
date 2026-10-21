import concurrent.futures as cf,urllib.request,json,datetime,hashlib
from pathlib import Path
r=Path(__file__).parent
ids=['Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF','Qwen/Qwen3-Coder-Next','MemTensor/MemT-1.0-Nano','MemTensor/MemT-1.0-Flash','liana313/tokengate-niah-qwen3-1.7b','ForeverBlue/Qwen3-VL-2B-GRACE-W4G128','hotchpotch/bekko-embedding-v1-a25m','ayanami-kitasan/swe-pruner-pro-qwen3-coder-next-head']
def get(mid):
 out={'id':mid,'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sources':[]}
 for u in ['https://huggingface.co/api/models/'+mid+'?blobs=true','https://huggingface.co/'+mid+'/raw/main/README.md']:
  e={'url':u}
  try:
   with urllib.request.urlopen(u,timeout=20)as f:b=f.read(4000001)
   assert len(b)<=4000000
   e.update(bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),text=b.decode())
  except Exception as ex:e['error']=type(ex).__name__+': '+str(ex)
  out['sources'].append(e)
 return out
res=list(cf.ThreadPoolExecutor(max_workers=4).map(get,ids));(r/'model-details.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
for x in res:
 e=x['sources'][0]
 if 'text' in e:
  d=json.loads(e['text']);files=[{'name':a['rfilename'],'size':a.get('size')}for a in d.get('siblings',[])if a['rfilename'].endswith(('.safetensors','.gguf','.pt'))];print(x['id'],d.get('lastModified'),d.get('cardData',{}).get('license'),files[:20])
 else:print(x['id'],e)
