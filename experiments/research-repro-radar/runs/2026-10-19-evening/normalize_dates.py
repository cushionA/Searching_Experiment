import datetime as dt,json,re
from pathlib import Path
r=Path(__file__).parent
rows=json.loads((r/'all-candidates.json').read_text()); changes=[]
for x in rows:
 v=x.get('published_at')
 if not v: continue
 try:
  parts=v.split('-')
  if re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}',v): n=dt.date(*map(int,parts)).isoformat()
  else: n=dt.datetime.fromisoformat(v.replace('Z','+00:00')).date().isoformat()
  if n>'2026-10-19': raise ValueError('future date')
 except ValueError: n=None
 if n!=v:
  changes.append({'id':x['id'],'original':v,'normalized':n});x['source_published_at']=v;x['published_at']=n
(r/'all-candidates.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
(r/'date-normalization.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2)+'\n')
print({'date_changes':len(changes),'unknown':sum(x['normalized'] is None for x in changes)})
