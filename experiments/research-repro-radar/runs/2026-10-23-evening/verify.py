"""Verify persistence and evidence consistency, not model reproduction."""
import json,hashlib
from pathlib import Path
r=Path(__file__).parent;root=r.parents[3];state=root/'.agents/skills/research-repro-radar/state';canonical=root/'experiments/research-repro-radar/evening-state'
def read(p):return json.loads(p.read_text())
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
report=read(r/'report.json');before=read(r/'queue-before.json');after=read(state/'queue.json');a={key(x)for x in before['candidates']};b={key(x)for x in after['candidates']}
assert a<=b and len(b)==len(after['candidates'])==6606
assert report['completed_ids']==[]and report['recommended']==[]and len(report['digest'])==10
assert report['counts']['collected_unique']==len(read(r/'all-candidates.json'))==2710
rank=read(r/'ranking.json');reviewed={x['candidate_id']for x in read(r/'reviews.json')};assert len(reviewed)==30
assert {x['id']for x in rank['review_pool']}<=reviewed and len(rank['review_pool'])==20
for f in ['seen.json','mail.json']:
 assert hashlib.sha256((state/f).read_bytes()).hexdigest()==read(r/'start-manifest.json')['state_before'][f]
for f in ['seen.json','mail.json','queue.json','freshness.json']:assert (state/f).read_bytes()==(canonical/f).read_bytes()
fresh=read(state/'freshness.json');assert json.dumps(fresh).count(report['run_id'])==1
ledger=read(r/'native-host-ledger.json');assert len(ledger)==7 and all(x['schema_valid']and x['upper_bound_elapsed_seconds']<=180 for x in ledger)
for f in ['review-evidence.json','followup-evidence.json','model-details-evidence.json']:
 for e in read(r/f):
  if 'file'in e:assert hashlib.sha256((r/e['file']).read_bytes()).hexdigest()==e['sha256'],e['file']
checks={'pending_before':len(a),'pending_after':len(b),'pending_lost':len(a-b),'completed_ids':0,'collected_unique':2710,'primary_read':30,'review_pool':20,'candidate_execution':0,'detail':0,'digest':10,'state_sync':'all four files byte-identical','seen_mail':'unchanged','freshness':'exactly one run','native_batches':'7 validated, each within 180 seconds','primary_evidence_hashes':'all pass'}
(r/'verification.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks))
