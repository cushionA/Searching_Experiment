"""Metadata-only supplements from public primary feeds/APIs; no candidate execution."""
import datetime as dt, hashlib, json, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
run=Path(__file__).parent; raw=run/'supplement-sources';raw.mkdir(exist_ok=True)
at=dt.datetime.now(dt.timezone.utc).isoformat()
requests=[]
def fetch(label,url):
 rec={'label':label,'url':url,'at':at};
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'ResearchReproRadar/1.0 (metadata-only research)'})
  with urllib.request.urlopen(req,timeout=30) as r: data=r.read(8_000_001);rec['status']=r.status
  if len(data)>8_000_000:raise ValueError('response cap exceeded')
  p=raw/(label+'.body');p.write_bytes(data);rec.update(file=str(p.relative_to(run)),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
  return rec,data
 except Exception as e:rec['error']=type(e).__name__+': '+str(e);return rec,b''
# Official MDPI topic feeds.
jobs=[('mdpi-ai','https://www.mdpi.com/rss/journal/ai'),('mdpi-information','https://www.mdpi.com/rss/journal/information')]
# Crossref public works metadata, recent creations, with topic-specific search.
for i,q in enumerate(['small language model quantization inference','information extraction noisy text','efficient text classification distillation','coding agents software engineering','lightweight computer vision models']):
 u='https://api.crossref.org/works?'+urllib.parse.urlencode({'query.bibliographic':q,'filter':'from-created-date:2026-09-01','rows':'100','select':'DOI,title,author,abstract,URL,published,created,relation,link'})
 jobs.append((f'crossref-{i}',u))
# OpenReview public notes API: current public discussions/submissions matching technical interests.
for i,q in enumerate(['language model quantization','information extraction','coding agent','efficient vision','distillation']):
 u='https://api2.openreview.net/notes?'+urllib.parse.urlencode({'content.title':q,'limit':'100','sort':'tmdate:desc'})
 jobs.append((f'openreview-{i}',u))
# GitHub repository search is used as an official artifact source. Anonymous limits are respected.
for i,q in enumerate(['small language model quantization inference','noisy text information extraction','coding agent SWE-bench','lightweight vision model CPU']):
 u='https://api.github.com/search/repositories?'+urllib.parse.urlencode({'q':q,'sort':'updated','order':'desc','per_page':'100'})
 jobs.append((f'github-{i}',u))
results=[]
for label,url in jobs:
 rec,data=fetch(label,url);results.append((rec,data))
 if label.startswith(('crossref-','openreview-','github-')):time.sleep(0.8)
ledger=[x[0] for x in results];(run/'supplement-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
new=[]
for rec,data in results:
 if not data:continue
 label=rec['label']
 try:
  if label.startswith('mdpi-'):
   root=ET.fromstring(data);ns={'r':'http://purl.org/rss/1.0/','dc':'http://purl.org/dc/elements/1.1/'}
   for x in root.findall('.//r:item',ns):
    doi=(x.findtext('dc:identifier',default='',namespaces=ns) or '').replace('doi:','').strip();url=x.findtext('r:link',default='',namespaces=ns);title=x.findtext('dc:title',default='',namespaces=ns) or x.findtext('r:title',default='',namespaces=ns)
    if not doi or not url or not title:continue
    new.append({'id':doi,'doi':doi,'title':title,'source':'mdpi','url':url,'abstract':x.findtext('r:description',default='',namespaces=ns),'published_at':x.findtext('dc:date',default='',namespaces=ns),'estimated_cpu_minutes':20,'resource_status':'unverified; metadata-only discovery','evidence_file':rec['file']})
  elif label.startswith('crossref-'):
   d=json.loads(data);items=d.get('message',{}).get('items',[])
   for x in items:
    doi=x.get('DOI');title=(x.get('title')or[''])[0];url=x.get('URL')
    if not doi or not title or not url:continue
    new.append({'id':doi,'doi':doi,'title':title,'source':'crossref','url':url,'abstract':x.get('abstract',''),'published_at':'-'.join(map(str,(x.get('published',{}).get('date-parts')or[['']])[0])),'estimated_cpu_minutes':20,'resource_status':'unverified; metadata-only discovery','evidence_file':rec['file']})
  elif label.startswith('openreview-'):
   d=json.loads(data)
   for x in d.get('notes',[]):
    c=x.get('content',{});title=c.get('title','');title=title.get('value','') if isinstance(title,dict) else str(title)
    abstract=c.get('abstract','');abstract=abstract.get('value','') if isinstance(abstract,dict) else str(abstract)
    ident=x.get('id');url='https://openreview.net/forum?id='+urllib.parse.quote(str(ident))
    if ident and title:new.append({'id':'openreview:'+str(ident),'title':title,'source':'openreview','url':url,'abstract':abstract,'published_at':dt.datetime.fromtimestamp((x.get('cdate')or 0)/1000,dt.timezone.utc).date().isoformat() if x.get('cdate') else None,'estimated_cpu_minutes':20,'resource_status':'unverified; metadata-only discovery','evidence_file':rec['file']})
  elif label.startswith('github-'):
   d=json.loads(data)
   for x in d.get('items',[]):
    url=x.get('html_url');name=x.get('full_name');title=name+' — '+(x.get('description')or'')
    if url and name:new.append({'id':'github:'+name.lower(),'title':title,'source':'github','url':url,'abstract':x.get('description')or'','published_at':x.get('updated_at','')[:10] or None,'has_code':True,'github_url':url,'hot_signals':['GitHub updated repository'],'estimated_cpu_minutes':20,'resource_status':'unverified; metadata-only discovery','evidence_file':rec['file']})
 except Exception as e:
  rec['parse_error']=type(e).__name__+': '+str(e)
# Merge all sources using skill's canonical DOI/arXiv/id/url key precedence.
base=json.loads((run/'candidates.json').read_text()); merged={}
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x.get('url')).casefold()
for x in base+new:
 k=key(x)
 if k in merged:
  # Keep original discovery record intact; attach non-HF source keys/aliases for audit.
  old=merged[k]; old.setdefault('discovered_via',[])
  if x.get('source')!=old.get('source'): old['supplement_sources']=sorted(set(old.get('supplement_sources',[])+[x['source']]))
  old.setdefault('supplement_records',[]).append({a:x.get(a) for a in ('source','url','title','evidence_file','doi','id') if x.get(a)})
 else:merged[k]=x
allrows=list(merged.values());(run/'all-candidates.json').write_text(json.dumps(allrows,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'requests':len(ledger),'ok':sum('status'in x for x in ledger),'errors':[{'label':x['label'],'error':x['error']}for x in ledger if 'error'in x],'supplement_records':len(new),'merged_unique':len(allrows),'new_unique':sum(key(x) not in {key(y) for y in base} for x in new),'sources':{s:sum(x.get('source')==s for x in new) for s in ('mdpi','crossref','openreview','github')}},ensure_ascii=False))
