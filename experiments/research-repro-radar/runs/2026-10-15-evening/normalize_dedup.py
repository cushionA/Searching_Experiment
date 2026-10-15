import json,re,hashlib
from pathlib import Path
run=Path(__file__).parent;p=run/'all-candidates.json';rows=json.loads(p.read_text())
version=re.compile(r'^(\d{4}\.\d{4,5})v\d+$',re.I)
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x.get('url')).casefold()
def union(a,b):return sorted(set((a or [])+(b or [])))
for x in rows:
 aid=x.get('arxiv_id')
 if not aid and x.get('source')=='arxiv':aid=x.get('id')
 if aid:
  m=version.match(str(aid))
  if m:
   original=str(aid);x.setdefault('source_record_ids',[]);x['source_record_ids']=union(x['source_record_ids'],[original]);x['arxiv_id']=m.group(1)
   if x.get('id')==original:x['id']=m.group(1)
 if x.get('source')=='github' and str(x.get('id','')).startswith('github:'):
  repo=str(x['id'])[7:];desc=x.get('abstract','');x.setdefault('source_title_raw',x.get('title'));x['title']=repo;x['abstract']=desc[:2000]
  if len(desc)>2000:x['abstract_truncated_chars']=len(desc)-2000
  x['github_description_source']='supplement-sources raw API response'
# Collapse on DOI/arxiv/id/url after canonical IDs. Preserve all candidate identity and discovery evidence.
merged={}
for x in rows:
 k=key(x)
 if k not in merged:
  y=dict(x); y['source_urls']=union(y.get('source_urls',[]),[y.get('url')] if y.get('url') else []);merged[k]=y;continue
 y=merged[k]
 for f in ('source_record_ids','source_urls','discovered_via','hot_signals','supplement_sources','evidence_files'):
  y[f]=union(y.get(f,[]),x.get(f,[]))
 if x.get('url'):y['source_urls']=union(y.get('source_urls'),[x['url']])
 # Prefer a more-specific/official record without throwing away raw IDs or URLs.
 for f in ('github_url','primary_source_url','primary_evidence_file','primary_sha256','evidence_file','evidence_position','trend_rank','published_at','primary_title','has_code','has_model','has_data','requires_gpu','estimated_cpu_minutes','estimated_ram_gb','abstract'):
  if x.get(f) is None:continue
  if f in ('has_code','has_model','has_data'):y[f]=bool(y.get(f)) or bool(x.get(f))
  elif f=='trend_rank':y[f]=min(y[f],x[f]) if y.get(f) is not None else x[f]
  elif f=='published_at':y[f]=max(str(y.get(f,'')),str(x[f]))
  elif f=='abstract' and len(str(x[f]))>len(str(y.get(f,''))):y[f]=x[f]
  elif not y.get(f):y[f]=x[f]
 if x.get('source')!=y.get('source'):y['supplement_sources']=union(y.get('supplement_sources'),[x.get('source')])
# Audit exact originals/unique merge behavior.
out=list(merged.values());p.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
ledger={'records_before':len(rows),'records_after_canonical_dedup':len(out),'collapsed_duplicates':len(rows)-len(out),'arxiv_versioned_records':sum(bool(version.match(str(x.get('arxiv_id','')))) or bool(x.get('source_record_ids')) for x in rows),'canonical_rule':'Remove terminal arXiv vN suffix from matching ids; preserve each raw ID and original URL in source_record_ids/source_urls.','github_titles':'Repository name; abstract capped at 2,000 chars, full descriptions preserved in hashed supplement source responses.','dedup_key':'doi, arxiv_id, id, url in skill precedence order.'}
(run/'id-title-normalization.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n');print(json.dumps(ledger,ensure_ascii=False))
