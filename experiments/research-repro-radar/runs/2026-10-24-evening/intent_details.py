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

jobs=[('2610.30067','intent-card','https://huggingface.co/TwoheartsTechnologies/goi-intent-v1/raw/main/README.md'),('2610.30067','intent-contract','https://huggingface.co/TwoheartsTechnologies/goi-intent-v1/raw/main/goibert-b-dapt-j2/contract.json'),('2610.30067','intent-meta','https://huggingface.co/api/models/TwoheartsTechnologies/goi-intent-v1?blobs=true')]
with cf.ThreadPoolExecutor(max_workers=3) as ex:res=list(ex.map(get,jobs))
(R/'intent-evidence.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
for e in res:
 if e['kind']!='intent-meta':print(e['kind'],e.get('text',e.get('error',''))[:9000])
