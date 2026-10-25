"""Official arXiv API metadata search supplement; no candidate execution."""
import re, datetime as dt, hashlib, json, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
run=Path(__file__).parent; raw=run/'supplement-sources'; raw.mkdir(exist_ok=True)
queries=['all:"small language model" AND (all:quantization OR all:distillation)','all:"information extraction" AND (all:document OR all:noise)','all:"text classification" AND (all:efficient OR all:compact)','all:"coding agent" OR all:"software engineering agent"','all:"lightweight vision" OR all:"efficient vision"']
ns={'a':'http://www.w3.org/2005/Atom','arxiv':'http://arxiv.org/schemas/atom'};records=[]; ledger=[]
for i,q in enumerate(queries):
 url='https://export.arxiv.org/api/query?'+urllib.parse.urlencode({'search_query':q,'start':0,'max_results':100,'sortBy':'submittedDate','sortOrder':'descending'})
 rec={'label':f'arxiv-{i}','query':q,'url':url,'at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'ResearchReproRadar/1.0 metadata-only literature search'})
  with urllib.request.urlopen(req,timeout=30) as r:data=r.read(8_000_001);rec['status']=r.status
  if len(data)>8_000_000:raise ValueError('response cap exceeded')
  p=raw/f'arxiv-{i}.xml';p.write_bytes(data);rec.update(file=str(p.relative_to(run)),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
  feed=ET.fromstring(data); rows=feed.findall('a:entry',ns);rec['count']=len(rows)
  for e in rows:
   ident=(e.findtext('a:id',default='',namespaces=ns).rstrip('/').split('/')[-1]);title=' '.join((e.findtext('a:title',default='',namespaces=ns)or'').split());abstract=' '.join((e.findtext('a:summary',default='',namespaces=ns)or'').split());published=e.findtext('a:published',default='',namespaces=ns)[:10]
   if not ident or not title:continue
   links=[x.get('href') for x in e.findall('a:link',ns) if x.get('title')=='pdf']
   records.append({'id':ident,'arxiv_id':re.sub(r'v[0-9]+$', '', ident),'title':title,'source':'arxiv','url':'https://arxiv.org/abs/'+ident,'abstract':abstract,'published_at':published,'estimated_cpu_minutes':20,'resource_status':'unverified; metadata-only discovery','evidence_file':rec['file'],'pdf_url':links[0] if links else None})
 except Exception as ex:rec['error']=type(ex).__name__+': '+str(ex)
 ledger.append(rec)
 if i<len(queries)-1:time.sleep(3)
(run/'arxiv-supplement-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
base=[]; merged={}
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x.get('url')).casefold()
for x in base+records:
 k=key(x)
 if k in merged:
  merged[k].setdefault('supplement_sources',[])
  if x.get('source')!=merged[k].get('source'):merged[k]['supplement_sources']=sorted(set(merged[k]['supplement_sources']+[x['source']]))
  merged[k].setdefault('supplement_records',[]).append({a:x.get(a)for a in('source','url','title','evidence_file','arxiv_id')if x.get(a)})
 else:merged[k]=x
out=list(merged.values());(run/'arxiv-candidates.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
basekeys={key(x) for x in base}
print(json.dumps({'requests':len(ledger),'ok':sum('status'in x for x in ledger),'errors':[x for x in ledger if 'error'in x],'records':len(records),'new_unique':sum(key(x)not in basekeys for x in records),'merged_unique':len(out)},ensure_ascii=False))
