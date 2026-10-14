exec(open('/workspace/radar-private/20261014/fetch_review.py').read().split('jobs=[]')[0])
import xml.etree.ElementTree as E
res=[fetch('https://www.mdpi.com/rss/journal/'+journal,'mdpi-'+journal) for journal in ['ai','information']]
records=json.loads((r/'candidates.json').read_text());RSS='{http://purl.org/rss/1.0/}';DC='{http://purl.org/dc/elements/1.1/}'
for x in res:
 if 'file' not in x:continue
 for item in E.fromstring((r/x['file']).read_bytes()).findall(RSS+'item'):
  doi=item.findtext(DC+'identifier','').removeprefix('doi: ').strip()
  records.append({'doi':doi,'id':doi,'title':item.findtext(DC+'title') or item.findtext(RSS+'title'),'source':'mdpi','url':item.findtext(RSS+'link'),'abstract':item.findtext(RSS+'description',''),'published_at':item.findtext(DC+'date'),'estimated_cpu_minutes':20,'resource_status':'unverified; execution prohibited until primary inspection','estimate_basis':'20 minute bounded check, not inference estimate','evidence_file':x['file']})
(r/'all-candidates.json').write_text(json.dumps(records,ensure_ascii=False,indent=2));(r/'secondary-feed-ledger.json').write_text(json.dumps(res,indent=2));print('all',len(records))
