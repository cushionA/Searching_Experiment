"""Public metadata and documentation only; no candidate code execution."""
import concurrent.futures as cf,datetime as dt,hashlib,json,re,urllib.request,urllib.parse,html.parser
from pathlib import Path
R=Path(__file__).parent
jobs=[]
for ident,repo in [('2610.22732','microsoft/TokenEconomizer'),('2610.30067','Nhantran65/GoiBERT')]:
 for kind,suffix in [('repo',''),('issues','/issues?state=all&per_page=100'),('releases','/releases?per_page=100'),('commits','/commits?per_page=5')]:jobs.append((ident,kind,'https://api.github.com/repos/'+repo+suffix))
 for kind,path in [('readme','README.md'),('license','LICENSE'),('pyproject','pyproject.toml')]:jobs.append((ident,kind,'https://raw.githubusercontent.com/'+repo+'/HEAD/'+path))
jobs += [('2610.30067','fulltext','https://arxiv.org/html/2610.30067v1'),('2610.30067','model-search','https://huggingface.co/api/models?search=GoiBERT&full=true&limit=100'),('2610.20538','model-search','https://huggingface.co/api/models?author=QiushiSun&search=PrismaCoder&full=true&limit=100'),('2610.11942','model','https://huggingface.co/api/models/jiamingzz/lgwm?blobs=true'),('2610.30090','model','https://huggingface.co/api/models/jsantillana/vx-one?blobs=true'),('2610.27450','model-search','https://huggingface.co/api/models?author=gasolsun&full=true&limit=100')]
class Text(html.parser.HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.skip=0
 def handle_starttag(self,t,a):
  if t in ['script','style']:self.skip+=1
 def handle_endtag(self,t):
  if t in ['script','style']:self.skip=max(0,self.skip-1)
 def handle_data(self,d):
  if not self.skip:self.parts.append(d)
def fetch(job):
 ident,kind,url=job;out={'id':ident,'kind':kind,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar metadata-only'}),timeout=30)as res:b=res.read(5000001);out['status']=res.status
  if len(b)>5000000:raise ValueError('response cap')
  p=R/'primary'/('followup-'+ident+'-'+kind);p.write_bytes(b);t=b.decode('utf-8','replace')
  if kind=='fulltext':h=Text();h.feed(t);t=' '.join(h.parts)
  out.update(file=str(p.relative_to(R)),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b),text=t)
 except Exception as e:out['error']=type(e).__name__+': '+str(e)
 return out
with cf.ThreadPoolExecutor(max_workers=5)as pool:out=list(pool.map(fetch,jobs))
(R/'followup-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print({'requests':len(out),'ok':sum('status'in x for x in out),'errors':[{k:x[k]for k in ['id','kind','error']}for x in out if 'error'in x]})
for x in out:
 if 'error'in x:continue
 if x['kind']=='issues':
  print(x['id'],'ISSUES',[(a['number'],a['title'],a.get('author_association'),a['user']['login'],a['html_url'],a.get('body','')[:1300])for a in json.loads(x['text'])])
 elif x['kind']=='model-search':print(x['id'],'MODELS',[(a['id'],a.get('lastModified'))for a in json.loads(x['text'])])
 elif x['kind']=='repo':
  d=json.loads(x['text']);print(x['id'],{k:d.get(k)for k in ['full_name','stargazers_count','pushed_at','license']})
