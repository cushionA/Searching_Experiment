"""Continue observed GitHub search result pages; no candidate execution."""
import json,urllib.request,urllib.parse,time,hashlib
from pathlib import Path
r=Path(__file__).parent;rows=json.loads((r/'all-candidates.json').read_text());by={str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).lower():x for x in rows};ledger=[]
for page in [2,3]:
 url='https://api.github.com/search/repositories?'+urllib.parse.urlencode({'q':'coding agent SWE-bench','sort':'updated','order':'desc','per_page':100,'page':page})
 rec={'url':url,'page':page}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar metadata-only'}),timeout=30)as f:b=f.read(8000000);rec['status']=f.status;rec['link']=f.headers.get('Link')
  (r/'supplement-sources'/f'github-2-page-{page}.body').write_bytes(b);d=json.loads(b);rec.update(total_count=d.get('total_count'),count=len(d.get('items',[])),sha256=hashlib.sha256(b).hexdigest())
  for x in d.get('items',[]):
   name=x['full_name'];ident='github:'+name.lower();by.setdefault(ident,{'id':ident,'title':name+' — '+(x.get('description')or''),'source':'github','url':x['html_url'],'abstract':x.get('description')or'','published_at':x.get('created_at','')[:10]or None,'has_code':True,'github_url':x['html_url'],'github_updated_at':x.get('updated_at'),'github_stars':x.get('stargazers_count'),'license':x.get('license'),'estimated_cpu_minutes':20,'resource_status':'unverified; bounded inspection allocation, not inference estimate','evidence_file':f'supplement-sources/github-2-page-{page}.body'})
 except Exception as e:rec['error']=str(e)
 ledger.append(rec);time.sleep(1)
(r/'all-candidates.json').write_text(json.dumps(list(by.values()),ensure_ascii=False,indent=2)+'\n');(r/'pagination-ledger.json').write_text(json.dumps(ledger,indent=2)+'\n');print({'unique':len(by),'pages':ledger})
