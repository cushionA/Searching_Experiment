"""Inspect actual model identifiers and artifacts without downloading weights."""
import concurrent.futures as cf,datetime as dt,hashlib,json,urllib.request,re,html.parser
from pathlib import Path
R=Path(__file__).parent
models=['2HeartsVN/GoiBERT-S','2HeartsVN/GoiBERT-M','QiushiSun/PrismaCoder-9B','QiushiSun/PrismaCoder-35B-A3B','QiushiSun/PrismaCoder-35B-A3B-SFT','gasolsun/Math_DeepSeek-R1-Distill-Qwen-1.5B_JustRL-DeepSeek-1.5B']
jobs=[(m,'https://huggingface.co/api/models/'+m+'?blobs=true')for m in models]
jobs += [('akv-hf','https://huggingface.co/api/papers/2610.28577'),('token-hf','https://huggingface.co/api/papers/2610.22732'),('akv-system','https://raw.githubusercontent.com/thu-nics/AKV/HEAD/akv/system/README.md'),('akv-agent','https://raw.githubusercontent.com/thu-nics/AKV/HEAD/akv/agent/README.md'),('akv-license','https://raw.githubusercontent.com/thu-nics/AKV/HEAD/LICENSE'),('akv-commits','https://api.github.com/repos/thu-nics/AKV/commits?per_page=5'),('akv-dair','https://www.linkedin.com/posts/dair-ai_we-love-papers-that-go-deeper-down-the-stack-activity-7518733866879336448-FcTh'),('token-community','https://www.linkedin.com/posts/apanshina_uc-berkeley-and-microsoft-just-published-activity-7518598074869694465-VQJ5')]
def fetch(j):
 label,url=j;e={'label':label,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar public-source reading'}),timeout=25)as s:b=s.read(3000001);e['status']=s.status;e['final_url']=s.url
  if len(b)>3000000:raise ValueError('response cap')
  p=R/'primary'/('detail-'+re.sub('[^a-zA-Z0-9_.-]','_',label));p.write_bytes(b);e.update(file=str(p.relative_to(R)),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b),text=b.decode('utf-8','replace'))
 except Exception as ex:e['error']=type(ex).__name__+': '+str(ex)
 return e
with cf.ThreadPoolExecutor(max_workers=5)as pool:out=list(pool.map(fetch,jobs))
(R/'model-signal-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
for e in out:
 if 'error'in e:print(e['label'],e['error']);continue
 if e['label']in models:
  d=json.loads(e['text']);print(e['label'],'ID',d.get('id'),'card',d.get('cardData'),'weights',[(x['rfilename'],x.get('size'))for x in d.get('siblings',[])if x['rfilename'].endswith(('.bin','.safetensors','.gguf'))])
 else:print(e['label'],e['status'],e['bytes'])
