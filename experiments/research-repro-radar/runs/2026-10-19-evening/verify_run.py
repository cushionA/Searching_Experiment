"""Offline audit of evidence and preserved state; does not execute candidate code."""
import hashlib,json,subprocess,sys
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];load=lambda p:json.loads(p.read_text());base='55bcd617ae45f5d8a36bc618e7be05154713bf46';state=root/'experiments/research-repro-radar/evening-state';report=load(R/'report.json');key=lambda x:str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
def old(n):return subprocess.check_output(['git','show',base+':experiments/research-repro-radar/evening-state/'+n+'.json'],cwd=root)
a=json.loads(old('queue'))['candidates'];b=load(state/'queue.json')['candidates'];am={key(x):x for x in a};bm={key(x):x for x in b}
assert len(a)==5204 and len(b)==len(bm)==5236;assert am.keys()<=bm.keys()
for k,x in am.items():assert x.get('carried_over_since')==bm[k].get('carried_over_since')
for n in ['seen','mail']:assert (state/(n+'.json')).read_bytes()==old(n)
f=load(state/'freshness.json')['history'];fo=json.loads(old('freshness'))['history'];assert f[:-1]==fo;assert f[-1]['run_id']==report['run_id'] and f[-1]['recommended_total']==0
assert report['completed_ids']==report['recommended']==[];assert len(report['digest'])==5;assert report['status']=='partial_judgment_audit_failed'
c=load(R/'all-candidates.json');assert len(c)==len({key(x)for x in c})==2515
rank=load(R/'ranking.json');assert rank['counts']['input']==6084 and rank['counts']['ranked']==5229
reviews=load(R/'reviews.json');assert len(reviews)==25;assert {x['id']for x in rank['review_pool']}<={x['candidate_id']for x in reviews}
assert all(x['status']=='not_accepted'for x in load(R/'parent-review-audit.json')[:25])
files={}
for e in load(R/'review-evidence.json'):
 if 'file'not in e:continue
 data=(R/e['file']).read_bytes();assert len(data)==e['bytes'];assert hashlib.sha256(data).hexdigest()==e['sha256'];files[e['file']]=len(data)
assert len(files)==150 and sum(files.values())==6512353
if '--check-index'in sys.argv:
 for n in ['seen','queue','mail','freshness']:
  p='experiments/research-repro-radar/evening-state/'+n+'.json';assert subprocess.check_output(['git','show',':'+p],cwd=root)==(root/p).read_bytes()
print(json.dumps({'ok':True,'collected':2515,'review_pool':20,'schema_valid_but_not_adopted':25,'timeout':6,'inferences':0,'pending':5236,'all_previous_ids_preserved':True,'seen_mail_unchanged':True,'completed':0,'primary_files':len(files),'primary_bytes':sum(files.values()),'status':'partial_judgment_audit_failed'}))
