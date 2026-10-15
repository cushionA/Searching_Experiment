import json,re
from pathlib import Path
root=Path('.');run=root/'experiments/research-repro-radar/runs/2026-10-15-evening';skill=root/'.agents/skills/research-repro-radar';state=skill/'state';canon=root/'experiments/research-repro-radar/evening-state'
rx=re.compile(r'^(\d{4}\.\d{4,5})v\d+$',re.I)
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x.get('url')).casefold()
def norm(row):
 x=dict(row); aid=x.get('arxiv_id')
 if not aid and x.get('source')=='arxiv':aid=x.get('id')
 if aid and (m:=rx.match(str(aid))):
  old=str(aid);new=m.group(1);x.setdefault('source_record_ids',[]);x['source_record_ids']=sorted(set(x['source_record_ids']+[old]));x['arxiv_id']=new
  if x.get('id')==old:x['id']=new
  if x.get('url'):x['source_urls']=sorted(set(x.get('source_urls',[])+[x['url']]))
 return x
def merge(rows):
 out={}
 for r in rows:
  x=norm(r);k=key(x)
  if k not in out:out[k]=x;continue
  y=out[k]
  for f in ('source_record_ids','source_urls','discovered_via','hot_signals'):
   y[f]=sorted(set(y.get(f,[])+x.get(f,[])))
  if x.get('url'):y['source_urls']=sorted(set(y.get('source_urls',[])+[x['url']]))
  if x.get('published_at') and str(x['published_at'])>str(y.get('published_at','')):y['published_at']=x['published_at']
 rows=list(out.values());return rows
# Normalize working queue before the final rank so a version alias does not get reinserted.
w=json.loads((state/'queue.json').read_text());before=len(w['candidates']);w['candidates']=merge(w['candidates']);(state/'queue.json').write_text(json.dumps(w,ensure_ascii=False,indent=2)+'\n')
# Canonicalized baseline copy for post-ranking retention; original evening-state stays byte-identical.
b=json.loads((canon/'queue.json').read_text());b['candidates']=merge(b['candidates']);out=run/'queue-baseline-canonical.json';out.write_text(json.dumps(b,ensure_ascii=False,indent=2)+'\n')
(run/'queue-normalization.json').write_text(json.dumps({'working_queue_before':before,'working_queue_after':len(w['candidates']),'baseline_records_before':len(json.loads((canon/'queue.json').read_text())['candidates']),'baseline_unique_after_canonicalization':len(b['candidates']),'source_record_ids_preserve_versioned_ids':True,'original_canonical_state_untouched':True},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'working_before':before,'working_after':len(w['candidates']),'baseline_before':1072,'baseline_after':len(b['candidates'])}))
