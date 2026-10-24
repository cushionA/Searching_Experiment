import concurrent.futures as cf, datetime as dt, hashlib, json, re, urllib.request, html.parser
from pathlib import Path
R=Path(__file__).parent
class Text(html.parser.HTMLParser):
 def __init__(self):super().__init__();self.parts=[]
 def handle_data(self,d):self.parts.append(d)
def get(job):
 ident,kind,url=job;out={'id':ident,'kind':kind,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar public metadata research','Accept':'application/vnd.github+json' if 'api.github' in url else '*/*'}),timeout=25) as s:b=s.read(3000001);out['status']=s.status
  if len(b)>3000000:raise ValueError('cap')
  name=re.sub(r'[^a-zA-Z0-9_.-]','_',ident)+'-'+kind
  path=R/'primary'/name;path.parent.mkdir(exist_ok=True);path.write_bytes(b)
  out.update(file=str(path.relative_to(R)),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))
  text=b.decode('utf-8','replace')
  if kind in ['paper','page']:
   p=Text();p.feed(text);text=' '.join(p.parts)
  out['text']=text
 except Exception as e:out['error']=type(e).__name__+': '+str(e)
 return out

jobs=[]
for ident,repo in [('2610.30067','Nhantran65/GoiBERT')]:
 jobs += [(ident,'repo','https://api.github.com/repos/'+repo),(ident,'readme','https://raw.githubusercontent.com/'+repo+'/HEAD/README.md'),(ident,'license','https://raw.githubusercontent.com/'+repo+'/HEAD/LICENSE')]
for ident,mid in [('2610.31329','tencent/WeVisOne-VL-2B'),('2610.31329','tencent/WeVisOne-VL-4B')]:
 tag=mid.split('/')[-1]
 jobs += [(ident,'model-'+tag,'https://huggingface.co/api/models/'+mid+'?blobs=true'),(ident,'modelcard-'+tag,'https://huggingface.co/'+mid+'/raw/main/README.md')]
jobs += [('2610.31329','license','https://raw.githubusercontent.com/Tencent/WeVisOne-VL/HEAD/LICENSE'),('2610.31372','dataset','https://huggingface.co/api/datasets/THU-KEG/RIDAR')]
for ident in ['2610.30067','2610.31198','2610.31329','2610.31372','2610.25835']:
 jobs.append((ident,'hf-paper','https://huggingface.co/api/papers/'+ident))
with cf.ThreadPoolExecutor(max_workers=5) as ex:res=list(ex.map(get,jobs))
(R/'followup-evidence.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
print({'requests':len(res),'ok':sum('status'in x for x in res),'errors':[{k:x[k]for k in ('id','kind','error')}for x in res if 'error'in x]})
