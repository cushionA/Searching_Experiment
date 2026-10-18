"""Read-only primary artifact follow-up; no external code execution."""
import concurrent.futures as cf,datetime as dt,hashlib,json,re,urllib.request,urllib.parse,html
from pathlib import Path
R=Path(__file__).parent
rows=json.loads((R/'review-targets.json').read_text()); ev=json.loads((R/'review-evidence.json').read_text());jobs=[]
existing={(x['id'],x['kind'])for x in ev}
for e in ev:
 if e['kind']!='paper':continue
 raw=(R/e['file']).read_text();urls=re.findall(r'href=["\']([^"\']+)',raw)
 repos=[x.split('github.com/')[-1].strip('/').split('/')[:2]for x in urls if 'github.com/'in x]
 if repos and (e['id'],'repo')not in existing:
  repo='/'.join(repos[0]);jobs.extend([(e['id'],'repo','https://api.github.com/repos/'+repo),(e['id'],'readme','https://raw.githubusercontent.com/'+repo+'/HEAD/README.md'),(e['id'],'releases','https://api.github.com/repos/'+repo+'/releases?per_page=3')])
for ident,mid in [('2609.18063','Edge0/Edge0-35B-A3B-preview'),('2609.18063','Edge0/Edge0-8B-A1B-preview'),('2606.23050','baidu/Unlimited-OCR'),('2610.17953','dongguanting/Agent-Reflex-8B'),('2503.11576','ds4sd/SmolDocling-256M-preview'),('2503.11576','ibm-granite/granite-docling-258M'),('hf:qwen/qwen2.5-coder-1.5b-instruct','Qwen/Qwen2.5-Coder-1.5B-Instruct')]:
 slug=mid.split('/')[-1];jobs.extend([(ident,'model-'+slug,'https://huggingface.co/api/models/'+mid+'?blobs=true'),(ident,'card-'+slug,'https://huggingface.co/'+mid+'/raw/main/README.md')])
for ident,q in [('2602.09373','AfriNLLB'),('2607.25180','bekko')]:jobs.append((ident,'model-search','https://huggingface.co/api/models?search='+q+'&limit=50&full=true'))
# Summarization representative already discovered, adds a reading target, not new collection.
x=next(x for x in json.loads((R/'all-candidates.json').read_text())if x['id']=='2401.17919');rows.append(x);jobs.append((x['id'],'paper',x['url']))
def get(job):
 ident,kind,url=job;out={'id':ident,'kind':kind,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar read-only primary-source research'}),timeout=25)as s:b=s.read(3000001);out['status']=s.status
  if len(b)>3000000:raise ValueError('response cap')
  name=re.sub(r'[^a-zA-Z0-9_.-]','_',ident)+'-'+kind;path=R/'primary'/name;path.write_bytes(b)
  t=b.decode('utf-8','replace')
  if kind=='paper':
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
