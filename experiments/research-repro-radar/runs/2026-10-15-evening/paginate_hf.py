"""Follow HF trending/new rel=next links until exhaustion or 7-minute budget."""
import datetime as dt, hashlib, json, re, time, urllib.request
from pathlib import Path
run=Path(__file__).parent; src=run/'sources';src.mkdir(exist_ok=True); started=time.monotonic(); budget=420

def parse_next(link):
 if not link:return None
 for item in link.split(','):
  if 'rel="next"' in item or "rel='next'" in item:
   m=re.search(r'<([^>]+)>',item)
   if m:return m.group(1)
 return None
candidates=[]; allpages=[]
for label,first in [('trending','https://huggingface.co/api/daily_papers?sort=trending'),('new','https://huggingface.co/api/daily_papers?sort=publishedAt')]:
 url=first;seen=set();page=0
 while url and url not in seen and time.monotonic()-started<budget:
  seen.add(url);page+=1;rec={'feed':label,'page':page,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'ResearchReproRadar/1.0 metadata-only literature research'})
   with urllib.request.urlopen(req,timeout=25) as r:data=r.read(8_000_001);rec['status']=r.status;link=r.headers.get('Link')
   if len(data)>8_000_000:raise ValueError('response cap exceeded')
   parsed=json.loads(data)
   if not isinstance(parsed,list):raise ValueError('expected JSON list')
   path=src/f'{label}-page-{page:04}.json';path.write_bytes(data);rec.update(file=str(path.relative_to(run)),bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),count=len(parsed),link=link,next_url=parse_next(link))
   for pos,row in enumerate(parsed,1):
    p=row.get('paper',row);ident=p.get('id');title=p.get('title')
    if not ident or not title:continue
    entry={'id':ident,'arxiv_id':ident,'title':title,'source':'huggingface','url':'https://arxiv.org/abs/'+ident,'abstract':p.get('summary',''),'published_at':p.get('publishedAt'),'has_code':bool(p.get('githubRepo')),'github_url':p.get('githubRepo'),'estimated_cpu_minutes':20,'resource_status':'unverified; metadata-only discovery','estimate_basis':'bounded inspection allocation, not inference estimate','discovered_via':[f'{label}-page-{page}'],'evidence_file':str(path.relative_to(run)),'evidence_position':pos}
    if label=='trending':entry['trend_rank']=(page-1)*50+pos
    candidates.append(entry)
   allpages.append(rec);url=rec['next_url']
  except Exception as e:
   rec['error']=type(e).__name__+': '+str(e);allpages.append(rec);break
  if page%10==0:time.sleep(1)
 ledger={'started_at':dt.datetime.fromtimestamp(time.time()-(time.monotonic()-started),dt.timezone.utc).isoformat(),'duration_seconds':time.monotonic()-started,'budget_seconds':budget,'complete':all((x.get('next_url') is None) for x in allpages if 'error' not in x),'requests':allpages,'candidate_rows':len(candidates)}
(run/'hf-pagination-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
base=json.loads((run/'all-candidates.json').read_text());merged={}
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x.get('url')).casefold()
for x in base+candidates:
 k=key(x)
 if k in merged:
  if x.get('source')!=merged[k].get('source'):merged[k].setdefault('supplement_sources',[]);merged[k]['supplement_sources']=sorted(set(merged[k]['supplement_sources']+[x['source']]))
  merged[k].setdefault('discovered_via',[]);merged[k]['discovered_via']=sorted(set(merged[k]['discovered_via']+x.get('discovered_via',[])))
  if x.get('trend_rank') and (not merged[k].get('trend_rank') or x['trend_rank']<merged[k]['trend_rank']):merged[k]['trend_rank']=x['trend_rank']
 else:merged[k]=x
out=list(merged.values());(run/'all-candidates.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
basekeys={key(x)for x in base}
print(json.dumps({'pages':len(allpages),'feed_pages':{f:sum(x['feed']==f for x in allpages)for f in('trending','new')},'exhausted':ledger['complete'],'budget_seconds':budget,'duration_seconds':ledger['duration_seconds'],'page_candidates':len(candidates),'new_unique':sum(key(x)not in basekeys for x in candidates),'merged_unique':len(out),'errors':[{'feed':x['feed'],'page':x['page'],'error':x['error']}for x in allpages if 'error'in x]},ensure_ascii=False))
