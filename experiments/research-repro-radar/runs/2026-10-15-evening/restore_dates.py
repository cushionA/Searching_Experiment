import datetime as dt,json
from pathlib import Path
import xml.etree.ElementTree as ET
run=Path(__file__).parent
p=run/'all-candidates.json';rows=json.loads(p.read_text());dates={}
def add(k,v):
 if k and v:
  try:dt.datetime.fromisoformat(str(v).replace('Z','+00:00'))
  except ValueError:return
  dates[str(k).casefold()]=str(v)
def ident(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x.get('url')).casefold()
for x in json.loads((run/'candidates.json').read_text()):add(ident(x),x.get('published_at'))
for f in (run/'sources').glob('*-page-*.json'):
 for row in json.loads(f.read_text()):
  x=row.get('paper',row); add(x.get('id'),x.get('publishedAt'))
ns={'a':'http://www.w3.org/2005/Atom'}
for f in (run/'supplement-sources').glob('arxiv-*.xml'):
 try:
  for e in ET.fromstring(f.read_bytes()).findall('a:entry',ns):add(e.findtext('a:id',default='',namespaces=ns).rstrip('/').split('/')[-1],e.findtext('a:published',default='',namespaces=ns))
 except:pass
# Crossref: preserve day precision only when the publisher supplied the full day.
for f in (run/'supplement-sources').glob('crossref-*.body'):
 try:
  for x in json.loads(f.read_bytes()).get('message',{}).get('items',[]):
   dp=((x.get('published') or x.get('created') or {}).get('date-parts')or[[]])[0]
   if len(dp)==3:add(x.get('DOI'),f'{int(dp[0]):04d}-{int(dp[1]):02d}-{int(dp[2]):02d}')
 except:pass
# GitHub update timestamps are precise ISO timestamps.
for f in (run/'supplement-sources').glob('github-*.body'):
 try:
  for x in json.loads(f.read_bytes()).get('items',[]):add('github:'+x.get('full_name','').lower(),x.get('updated_at'))
 except:pass
# MDPI dates are carried in RSS metadata.
nsmd={'r':'http://purl.org/rss/1.0/','dc':'http://purl.org/dc/elements/1.1/'}
for f in (run/'supplement-sources').glob('mdpi-*.body'):
 try:
  root=ET.fromstring(f.read_bytes())
  for x in root.findall('.//r:item',nsmd):
   doi=(x.findtext('dc:identifier',default='',namespaces=nsmd)or'').replace('doi:','').strip();add(doi,x.findtext('dc:date',default='',namespaces=nsmd))
 except:pass
restored=0
for x in rows:
 k=ident(x)
 if k in dates and x.get('published_at')!=dates[k]:x['published_at']=dates[k];restored+=1
 else:
  bad=x.get('published_at')
  try:
   if bad:dt.datetime.fromisoformat(str(bad).replace('Z','+00:00'))
  except ValueError:x.pop('published_at',None)
p.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
(run/'date-normalization.json').write_text(json.dumps({'restored_valid_source_dates':restored,'candidate_date_keys':len(dates),'note':'Dates were restored from raw source responses; incomplete Crossref year/month precision stays omitted rather than imputed.'},ensure_ascii=False,indent=2)+'\n')
print({'candidate_rows':len(rows),'source_date_keys':len(dates),'restored':restored})
