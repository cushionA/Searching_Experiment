"""Rebuild budget-limited HF discovery and page audit from saved responses."""
import datetime as dt,hashlib,json, re
from pathlib import Path
run=Path(__file__).parent;src=run/'sources';files=sorted(src.glob('*-page-*.json'))
ledger=[];rows=[];seen_hash={};per={'trending':0,'new':0};bytes_total=0;raw_entries=0
for f in files:
 m=re.fullmatch(r'(trending|new)-page-(\d+)\.json',f.name)
 if not m:continue
 feed,page=m.group(1),int(m.group(2));per[feed]=max(per[feed],page);b=f.read_bytes();h=hashlib.sha256(b).hexdigest();bytes_total+=len(b)
 base='https://huggingface.co/api/daily_papers?sort='+('trending' if feed=='trending' else 'publishedAt')
 url=base if page==1 else base+f'&p={page-1}'
 rec={'feed':feed,'page':page,'url':url,'file':str(f.relative_to(run)),'status':200,'bytes':len(b),'sha256':h,'request_result_count':None}
 if h in seen_hash:rec['duplicate_payload_of']=seen_hash[h];ledger.append(rec);continue
 seen_hash[h]=rec['file']
 data=json.loads(b);rec['request_result_count']=len(data);raw_entries+=len(data)
 for pos,row in enumerate(data,1):
  p=row.get('paper',row);ident=p.get('id');title=p.get('title')
  if not ident or not title:continue
  item={'id':ident,'arxiv_id':ident,'title':title,'source':'huggingface','url':'https://arxiv.org/abs/'+ident,'abstract':p.get('summary',''),'published_at':p.get('publishedAt'),'has_code':bool(p.get('githubRepo')),'github_url':p.get('githubRepo'),'estimated_cpu_minutes':20,'resource_status':'unverified; metadata-only discovery','estimate_basis':'bounded inspection allocation, not inference estimate','discovered_via':[f'{feed}-page-{page}'],'evidence_file':str(f.relative_to(run)),'evidence_position':pos}
  if feed=='trending':item['trend_rank']=(page-1)*50+pos
  rows.append(item)
 ledger.append(rec)
ledger_obj={'started_at':'2026-10-15T09:09:00Z','finished_at':dt.datetime.now(dt.timezone.utc).isoformat(),'elapsed_seconds_approx':127,'termination':'Explicit discovery download budget and review deadline; SIGINT after page file flush. No feed exhaustion claim.','budget_bytes':120_000_000,'downloaded_bytes':bytes_total,'downloaded_files':len(ledger),'unique_page_payloads':len(seen_hash),'duplicate_page_payloads':len(ledger)-len(seen_hash),'pages_by_feed':per,'last_saved_page':{'trending':per['trending'],'new':per['new']},'next_continuations':[{'feed':'trending','url':f'https://huggingface.co/api/daily_papers?sort=trending&p={per["trending"]}','status':'inferred from observed rel=next pattern; not fetched'},{'feed':'new','url':'https://huggingface.co/api/daily_papers?sort=publishedAt','status':'first page not fetched in pagination continuation'}],'requests':ledger,'raw_entries_unique_payloads':raw_entries,'candidate_rows':len(rows)}
(run/'hf-pagination-ledger.json').write_text(json.dumps(ledger_obj,ensure_ascii=False,indent=2)+'\n')
base=json.loads((run/'all-candidates.json').read_text());merged={}
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x.get('url')).casefold()
for x in base+rows:
 k=key(x)
 if k in merged:
  if x.get('source')!=merged[k].get('source'):
   merged[k].setdefault('supplement_sources',[]);merged[k]['supplement_sources']=sorted(set(merged[k]['supplement_sources']+[x['source']]))
  merged[k].setdefault('discovered_via',[]);merged[k]['discovered_via']=sorted(set(merged[k]['discovered_via']+x.get('discovered_via',[])))
  if x.get('trend_rank') and (not merged[k].get('trend_rank') or x['trend_rank']<merged[k]['trend_rank']):merged[k]['trend_rank']=x['trend_rank']
 else:merged[k]=x
out=list(merged.values());(run/'all-candidates.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
orig={key(x)for x in base};print(json.dumps({'saved_pages':len(ledger),'pages_by_feed':per,'bytes':bytes_total,'unique_page_payloads':len(seen_hash),'repeated_payload_pages':len(ledger)-len(seen_hash),'raw_response_entries':raw_entries,'page_candidate_rows':len(rows),'new_unique':sum(key(x)not in orig for x in rows),'merged_unique':len(out),'exhausted':False,'ledger':'hf-pagination-ledger.json'},ensure_ascii=False))
