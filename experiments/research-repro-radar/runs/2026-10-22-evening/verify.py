"""Verify source hashes, conservative completion and evening-state persistence."""
import hashlib,json,subprocess
from pathlib import Path
R=Path(__file__).parent;ROOT=R.parents[3];STATE=ROOT/'experiments/research-repro-radar/evening-state';BASE='4715cc03e4ea376a268e700e94fd3b9ff51981b4'
def read(p):return json.loads(p.read_text())
def old(n):return json.loads(subprocess.check_output(['git','show',BASE+':experiments/research-repro-radar/evening-state/'+n],cwd=ROOT))
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
b={key(x):x for x in old('queue.json')['candidates']};q=read(STATE/'queue.json')['candidates'];a={key(x):x for x in q};report=read(R/'report.json')
assert len(b)==6212 and len(a)==len(q)==6309
assert not(set(b)-set(a))
for k in b:assert a[k].get('carried_over_since')==b[k].get('carried_over_since')
assert not report['completed_ids']
for ident in ['2610.22732','2610.28577']:assert ident in a and ident in read(STATE/'seen.json')['reported']
assert read(STATE/'mail.json')==old('mail.json')
assert set(old('seen.json')['reported'])<=set(read(STATE/'seen.json')['reported'])
for p in STATE.glob('*.json'):assert p.read_bytes()==(ROOT/'.agents/skills/research-repro-radar/state'/p.name).read_bytes()
checked=0
for name in ['review-evidence.json','followup-evidence.json','model-signal-evidence.json']:
 for e in read(R/name):
  if 'file'in e:assert hashlib.sha256((R/e['file']).read_bytes()).hexdigest()==e['sha256'];checked+=1
ledger=read(R/'native-host-ledger.json');assert len(ledger)==9 and len({e['request_id']for e in ledger})==9
assert all(e['schema_valid']and e['elapsed_seconds']<=180 for e in ledger)
assert len(read(R/'reviews.json'))==30 and len(read(R/'updated-reviews.json'))==8
assert len(read(R/'all-candidates.json'))==2515
assert report['counts']['candidate_model_inferences']==0
assert read(R/'ranking.json')['counts']['invalid']==0
out={'previous_pending':len(b),'pending_after_record':len(a),'new_pending':len(set(a)-set(b)),'removed_previous':[],'mail_unchanged':True,'unfinished_recommendations_retained':['2610.22732','2610.28577'],'state_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in STATE.glob('*.json')},'primary_response_hashes_checked':checked,'native_requests':9,'native_candidate_verdicts':38,'candidate_executions':0,'all_checks_passed':True}
(R/'verification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out))
