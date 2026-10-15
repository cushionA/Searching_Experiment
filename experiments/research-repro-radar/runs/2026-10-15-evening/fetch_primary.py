import concurrent.futures, datetime, hashlib, html.parser, json, time, urllib.request
from pathlib import Path
run=Path(__file__).parent
ranking=json.loads((run/'ranking-initial.json').read_text())
rows=ranking['review_pool']
class P(html.parser.HTMLParser):
 def __init__(self): super().__init__(); self.meta={}; self.links=[]
 def handle_starttag(self,t,attrs):
  a=dict(attrs)
  if t=='meta' and a.get('name'): self.meta[a['name']]=a.get('content','')
  if t=='a' and a.get('href'): self.links.append(a['href'])
def get(row):
 url=row['url']; target=('model-' if row['id'].startswith('hf:') else 'arxiv-')+row['id'].replace('/','_')
 if row['id'].startswith('hf:'): url='https://huggingface.co/'+row['id'][3:]
 start=time.time(); rec={'id':row['id'],'url':url,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'ResearchReproRadar/1.0 metadata-only'})
  with urllib.request.urlopen(req,timeout=25) as r: body=r.read(2_000_001); rec.update(status=r.status,final_url=r.geturl())
  if len(body)>2_000_000: raise ValueError('size limit')
  path=run/'primary'/f'{target}.html';path.parent.mkdir(exist_ok=True);path.write_bytes(body)
  rec.update(file=str(path.relative_to(run)),bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
  parser=P();parser.feed(body.decode('utf-8','replace'));rec['title']=parser.meta.get('citation_title');rec['abstract']=parser.meta.get('citation_abstract');rec['published']=parser.meta.get('citation_publication_date');rec['github_links']=[x for x in parser.links if 'github.com/' in x][:10]
 except Exception as e:rec['error']=type(e).__name__+': '+str(e)
 rec['seconds']=time.time()-start;return rec
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex: results=list(ex.map(get,rows))
(run/'primary-ledger.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'requested':len(results),'ok':sum('status'in r for r in results),'errors':[r for r in results if 'error'in r]},ensure_ascii=False))
