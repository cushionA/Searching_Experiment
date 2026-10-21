"""Read-only primary artifact follow-up; no external code execution."""
import concurrent.futures as cf,datetime as dt,hashlib,json,re,urllib.request,urllib.parse,html
from pathlib import Path
R=Path(__file__).parent
rows=json.loads((R/'review-targets.json').read_text()); ev=json.loads((R/'review-evidence.json').read_text());jobs=[]
existing={(x['id'],x['kind'])for x in ev}
for e in ev:
 if e['kind']!='paper':continue

 if 'file' not in e:continue
 raw=(R/e['file']).read_text();urls=re.findall(r'href=["\']([^"\']+)',raw)
 repos=[x.split('github.com/')[-1].strip('/').split('/')[:2]for x in urls if 'github.com/'in x]
 if repos and (e['id'],'repo')not in existing:
  repo='/'.join(repos[0]);jobs.extend([(e['id'],'repo','https://api.github.com/repos/'+repo),(e['id'],'readme','https://raw.githubusercontent.com/'+repo+'/HEAD/README.md'),(e['id'],'releases','https://api.github.com/repos/'+repo+'/releases?per_page=3')])
# Summarization representative already discovered, adds a reading target, not new collection.
allrows=json.loads((R/'all-candidates.json').read_text())
for ident in ['2610.26754','2610.24648','2610.27327','2610.24649']:
 x=next((x for x in allrows if x['id']==ident),None)
 if x and ident not in {y['id']for y in rows}:
  rows.append(x);jobs.append((x['id'],'paper',x['url']))
  if x.get('github_url'):
   repo=x['github_url'].split('github.com/')[-1].strip('/')
   jobs.extend([(ident,'repo','https://api.github.com/repos/'+repo),(ident,'readme','https://raw.githubusercontent.com/'+repo+'/HEAD/README.md'),(ident,'releases','https://api.github.com/repos/'+repo+'/releases?per_page=3')])
for x in rows:
 if x.get('arxiv_id'): jobs.append((x['id'],'fulltext','https://arxiv.org/html/'+x['arxiv_id']))
def get(job):
 ident,kind,url=job;out={'id':ident,'kind':kind,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar read-only primary-source research'}),timeout=25)as s:b=s.read(3000001);out['status']=s.status
  if len(b)>3000000:raise ValueError('response cap')
  name=re.sub(r'[^a-zA-Z0-9_.-]','_',ident)+'-'+kind;path=R/'primary'/name;path.write_bytes(b)
  t=b.decode('utf-8','replace')
  if kind in ['paper','fulltext']:
   import html.parser
   class Text(html.parser.HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,d):self.parts.append(d)
   h=Text();h.feed(t);t=' '.join(h.parts)
  out.update(file=str(path.relative_to(R)),sha256=hashlib.sha256(b).hexdigest(),bytes=len(b),text=t)
 except Exception as ex:out['error']=type(ex).__name__+': '+str(ex)
 return out
with cf.ThreadPoolExecutor(max_workers=5)as pool:extra=list(pool.map(get,jobs))
ev+=extra;(R/'review-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');(R/'review-targets.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n');(R/'enrichment-ledger.json').write_text(json.dumps([{k:v for k,v in e.items()if k!='text'}for e in extra],ensure_ascii=False,indent=2)+'\n')
print({'added_sources':len(extra),'ok':sum('status'in e for e in extra),'errors':[{k:e[k]for k in ['id','kind','error']}for e in extra if 'error'in e]})
