"""Normalize known date components; missing day remains unknown, never invented."""
import json,datetime,re
from pathlib import Path
r=Path(__file__).parent
p=r/'all-candidates.json';rows=json.loads(p.read_text());changes=[]
for x in rows:
 d=x.get('published_at')
 if not d:continue
 try:datetime.date.fromisoformat(d[:10]);continue
 except ValueError:pass
 parts=d.split('-');out=None
 if len(parts)==3:
  try:out=datetime.date(*map(int,parts)).isoformat()
  except ValueError:pass
 x['published_at_raw']=d;x['published_at']=out;changes.append({'id':x['id'],'raw':d,'normalized':out,'reason':'padding date components' if out else 'partial date; exact day unconfirmed'})
p.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n');(r/'date-normalization.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2)+'\n');print(len(changes))
