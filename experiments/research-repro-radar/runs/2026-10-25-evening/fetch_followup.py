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

jobs=[('2610.31623', 'install', 'https://raw.githubusercontent.com/jrryzh/F2G-Pose/HEAD/docs/INSTALL.md'), ('2610.31623', 'deploy', 'https://raw.githubusercontent.com/jrryzh/F2G-Pose/HEAD/docs/DEPLOYMENT.md'), ('2610.31623', 'license', 'https://raw.githubusercontent.com/jrryzh/F2G-Pose/HEAD/LICENSE'), ('2610.31623', 'model', 'https://huggingface.co/api/models/J3rr1/F2G-Pose?blobs=true'), ('2610.31623', 'family', 'https://huggingface.co/api/models?author=J3rr1&search=F2G'), ('2610.30181', 'license', 'https://raw.githubusercontent.com/Yu-xm/OpenDS/HEAD/LICENSE'), ('2610.30181', 'tree', 'https://api.github.com/repos/Yu-xm/OpenDS/git/trees/HEAD?recursive=1'), ('2610.30949', 'model-search', 'https://huggingface.co/api/models?search=IQuest-Q1&limit=100'), ('2610.31305', 'model-search', 'https://huggingface.co/api/models?search=Cola2&limit=100'), ('2610.28889', 'fulltext', 'https://arxiv.org/html/2610.28889v1'), ('2610.27598', 'fulltext', 'https://arxiv.org/html/2610.27598v1'), ('2610.30949', 'fulltext', 'https://arxiv.org/html/2610.30949v1'), ('2610.31305', 'fulltext', 'https://arxiv.org/html/2610.31305v1')]
with cf.ThreadPoolExecutor(max_workers=5) as ex:res=list(ex.map(get,jobs))
(R/'followup-evidence.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
print({'requests':len(res),'ok':sum('status'in x for x in res),'errors':[{k:x[k]for k in ('id','kind','error')}for x in res if 'error'in x]})
