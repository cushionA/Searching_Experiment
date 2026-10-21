"""Check actual persisted identities, hashes and run evidence without executing candidates."""
import hashlib,json,subprocess
from pathlib import Path
R=Path(__file__).parent
ROOT=R.parents[3]
STATE=ROOT/'experiments/research-repro-radar/evening-state'
BASE='b9682b985b1c708d5680937fdbda1c27edea53b5'
def read(p):return json.loads(p.read_text())
def old(name):return json.loads(subprocess.check_output(['git','show',BASE+':experiments/research-repro-radar/evening-state/'+name],cwd=ROOT))
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
before=old('queue.json')['candidates'];after=read(STATE/'queue.json')['candidates']
b={key(x):x for x in before};a={key(x):x for x in after}
report=read(R/'report.json');completed=set(report['completed_ids'])
assert len(before)==6136 and len(after)==6212
assert len(a)==len(after)
assert set(b)-set(a)<=completed
for k in set(b)&set(a):
    assert a[k].get('carried_over_since')==b[k].get('carried_over_since')
assert '2610.14224' in a
assert completed.isdisjoint(a)
assert read(STATE/'mail.json')==old('mail.json')
assert set(old('seen.json')['reported'])<=set(read(STATE/'seen.json')['reported'])
assert '2610.14224' in read(STATE/'seen.json')['reported']
for p in STATE.glob('*.json'):
    assert p.read_bytes()==(ROOT/'.agents/skills/research-repro-radar/state'/p.name).read_bytes()
sources=read(R/'review-evidence.json')
for source in sources:
    if 'file' in source:assert hashlib.sha256((R/source['file']).read_bytes()).hexdigest()==source['sha256']
assert len(read(R/'reviews.json'))==30
ledger=read(R/'native-host-ledger.json')
assert len({x['request_id']for x in ledger})==len(ledger)
assert all(x.get('elapsed_seconds',0)<=180 for x in ledger)
assert report['counts']['candidate_model_inferences']==0
out={'previous_pending':len(before),'pending_after_record':len(after),'removed_previous':sorted(set(b)-set(a)),'new_pending':len(set(a)-set(b)),'detailed_but_unexecuted_retained':['2610.14224'],'mail_unchanged':True,'state_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in STATE.glob('*.json')},'primary_response_hashes_checked':sum('file'in x for x in sources),'native_requests':len(ledger),'native_initial_candidates':30,'native_updated_candidates':6,'candidate_executions':0,'runtime_model':'unknown','all_checks_passed':True}
(R/'verification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out))
