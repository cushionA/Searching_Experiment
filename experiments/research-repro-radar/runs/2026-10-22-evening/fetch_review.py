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
rows=json.loads((R/'ranking.json').read_text())['review_pool']
allrows=json.loads((R/'all-candidates.json').read_text())
extras=['2610.29545','2610.29680','2610.28577','2610.20538','2610.17860','2610.27450','2610.30090','2610.25519','2610.22732','2610.11942']
rows+= [x for x in allrows if x['id'] in extras and x['id'] not in {r['id'] for r in rows}]
jobs=[]
for x in rows:
 ident=x['id'];url=x['url']
 if ident.startswith('github:'):
  repo=ident[7:];jobs += [(ident,'repo','https://api.github.com/repos/'+repo),(ident,'readme','https://raw.githubusercontent.com/'+repo+'/HEAD/README.md'),(ident,'releases','https://api.github.com/repos/'+repo+'/releases?per_page=3')]
 elif ident.startswith('hf:'):
  mid=x['url'].split('huggingface.co/')[-1];jobs += [(ident,'model','https://huggingface.co/api/models/'+mid),(ident,'readme','https://huggingface.co/'+mid+'/raw/main/README.md')]
 else:
  jobs.append((ident,'paper',url))
  if x.get('github_url'):
   repo=x['github_url'].split('github.com/')[-1].rstrip('/');jobs += [(ident,'repo','https://api.github.com/repos/'+repo),(ident,'readme','https://raw.githubusercontent.com/'+repo+'/HEAD/README.md'),(ident,'releases','https://api.github.com/repos/'+repo+'/releases?per_page=3')]
with cf.ThreadPoolExecutor(max_workers=5) as ex:res=list(ex.map(get,jobs))
(R/'review-evidence.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
(R/'review-targets.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
print({'targets':len(rows),'requests':len(res),'ok':sum('status'in x for x in res),'errors':[{k:x[k] for k in ('id','kind','error')} for x in res if 'error'in x]})
