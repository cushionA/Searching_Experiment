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

previous=json.loads((R/'review-targets.json').read_text());old={x['id']for x in previous};rows=[x for x in json.loads((R/'ranking.json').read_text())['review_pool']if x['id']not in old];jobs=[]
for x in rows:
 jobs.extend([(x['id'],'paper',x['url']),(x['id'],'page','https://arxiv.org/html/'+x['id'])])
with cf.ThreadPoolExecutor(max_workers=4)as ex:res=list(ex.map(get,jobs))
jobs=[]
for e in res:
 if e['kind']!='paper' or 'file'not in e:continue
 raw=(R/e['file']).read_text();urls=re.findall(r'href=["\']([^"\']+)',raw)
 for u in urls:
  if u.startswith('https://github.com/'):
   repo=u.split('github.com/')[-1].rstrip('/').removesuffix('.git');jobs.extend([(e['id'],'repo','https://api.github.com/repos/'+repo),(e['id'],'readme','https://raw.githubusercontent.com/'+repo+'/HEAD/README.md')]);break
with cf.ThreadPoolExecutor(max_workers=4)as ex:res+=list(ex.map(get,jobs))
ev=json.loads((R/'review-evidence.json').read_text())+res
(R/'review-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');(R/'review-targets.json').write_text(json.dumps(previous+rows,ensure_ascii=False,indent=2)+'\n')
print({'extra_read_targets':len(rows),'requests':len(res),'ok':sum('status'in e for e in res)})
