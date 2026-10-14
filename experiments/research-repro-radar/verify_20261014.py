"""Read-only audit of the 2026-10-14 evening run and canonical state."""
import hashlib,json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[2]
run=root/'experiments/research-repro-radar/runs/2026-10-14-evening'
state=root/'experiments/research-repro-radar/evening-state'
load=lambda p:json.loads(p.read_text())
key=lambda x:str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
base='a4f02d484addcd13be05671cd02f06ad49f32a25'
previous=json.loads(subprocess.check_output(['git','-C',str(root),'show',base+':experiments/research-repro-radar/evening-state/queue.json']))
report=load(run/'report.json'); current=load(state/'queue.json')
completed=set(report.get('completed_ids',[]))
old={key(x) for x in previous['candidates']};new={key(x) for x in current['candidates']}
assert not(old-new-{x.casefold() for x in completed}), 'unfinished carry lost'
assert len(new)==len(current['candidates']), 'duplicate queue keys'
assert all(x['id'].casefold() in new for x in report['recommended'] if not x.get('execution_completed'))
assert report['counts']['pending_after_record']==len(new)
rank=load(run/'ranking-final.json');assert rank['counts']['review_pool']==20
assert len(load(run/'reviews.json'))==20
assert len(load(run/'all-candidates.json'))==1420
assert load(run/'native-host-ledger.json')['probe']['validation_exit']==0
for name in ['seen.json','mail.json','freshness.json','queue.json']:
 assert (root/'.agents/skills/research-repro-radar/state'/name).read_bytes()==(state/name).read_bytes()
requests=load(run/'discovery-ledger.json')['requests']
for x in requests:
 if 'sha256'in x:assert hashlib.sha256((run/'sources'/(x['label']+'.json')).read_bytes()).hexdigest()==x['sha256']
for name in ['review-fetch-ledger.json','fulltext-fetch-ledger.json','secondary-feed-ledger.json','extra-fetch-ledger.json','model-metadata-ledger.json']:
 for x in load(run/name):
  if 'file'in x:assert hashlib.sha256((run/x['file']).read_bytes()).hexdigest()==x['sha256']
print(json.dumps({'ok':True,'collected_unique':1420,'review_pool':20,'extra_reviewed':4,'old_pending':len(old),'pending':len(new),'completed':len(completed),'source_hashes_verified':True}))
