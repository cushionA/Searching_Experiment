"""Validate this run's carry preservation and completed-artifact claims."""
import hashlib,json
from pathlib import Path
r=Path(__file__).parent
root=r.parents[3]
def load(p):return json.loads(p.read_text())
before=load(r/'queue-before.json')['candidates']
state=root/'experiments/research-repro-radar/evening-state'
after=load(state/'queue.json')['candidates']
def key(x):return str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold()
a={key(x):x for x in after};b={key(x):x for x in before}
assert len(b)==9384 and len(a)==9467 and len(a)==len(after)
assert set(b)<=set(a)
assert all(a[k].get('carried_over_since')==x.get('carried_over_since') for k,x in b.items())
report=load(r/'report.json');assert report['completed_ids']==report['recommended']==[]
assert len(report['digest'])<=15
ranking=load(r/'ranking.json');assert ranking['counts']['invalid']==0
assert len(ranking['review_pool'])==20 and load(r/'final-review-coverage.json')['all_read']
fresh=load(state/'freshness.json');rows=fresh.get('runs',fresh.get('history',[]))
assert sum(x.get('run_id')=='2026-10-27-evening'for x in rows)==1
bench=root/'benchmarks/research-repro-radar/2026-10-27-evening'
audit=load(bench/'autotavern_audit_result.json')
assert audit['status']=='complete' and audit['task_count']==368 and audit['actual_model_inferences']==0
fixture=bench/'tinyjev/eval';manifest=load(fixture/'fixture-manifest.json')
for name,h in manifest['files'].items():assert hashlib.sha256((fixture/name).read_bytes()).hexdigest()==h
assert len(load(fixture/'inputs.json'))==len(load(fixture/'gold.json'))==48
assert load(bench/'tinyjev/output/comparison.json')['candidate'] is None
result={'ok':True,'original_pending':len(b),'pending':len(a),'added_pending':len(a)-len(b),'carry_dates_preserved':True,'final_pool_read':20,'model_inferences':0,'scorer_audits':1,'state_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in state.glob('*.json')}}
(r/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
