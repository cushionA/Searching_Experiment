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

jobs=[('2610.31623', 'model-card', 'https://raw.githubusercontent.com/jrryzh/F2G-Pose/HEAD/docs/MODEL_CARD.md'), ('2610.31623', 'third-party', 'https://raw.githubusercontent.com/jrryzh/F2G-Pose/HEAD/THIRD_PARTY.md'), ('2610.31623', 'radio-model', 'https://huggingface.co/api/models/nvidia/RADIO?blobs=true'), ('2610.31623', 'radio-card', 'https://huggingface.co/nvidia/RADIO/raw/main/README.md'), ('2610.30949', 'model', 'https://huggingface.co/api/models/IQuestLab/IQuest-Q1?blobs=true'), ('2610.30949', 'model-card', 'https://huggingface.co/IQuestLab/IQuest-Q1/raw/main/README.md'), ('2610.30949', 'family', 'https://huggingface.co/api/models?author=IQuestLab&limit=100'), ('2610.30949', 'quantized', 'https://huggingface.co/api/models/Baekpica/IQuest-Q1-Mixed-Quant-GGUF?blobs=true')]
with cf.ThreadPoolExecutor(max_workers=4) as ex:res=list(ex.map(get,jobs))
(R/'model-details-evidence.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
print({'requests':len(res),'ok':sum('status'in x for x in res),'errors':[{k:x[k]for k in ('id','kind','error')}for x in res if 'error'in x]})
