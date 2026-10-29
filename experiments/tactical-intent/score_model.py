import json,pathlib,hashlib,math
from bench import P,score,validate
out=pathlib.Path('benchmarks/tactical-intent-20261030')
rows=json.loads((P/'inputs.json').read_text());gold=json.loads((P/'gold.json').read_text())
for n,h in json.loads((P/'fixture-manifest.json').read_text()).items():assert hashlib.sha256((P/n).read_bytes()).hexdigest()==h
result=json.loads((out/'model-result.json').read_text());pred=result['predictions'];by={r['id']:r for r in rows}
for r in pred:
 assert r['id'] in by and r['validity']==validate(r,by[r['id']])
 assert set(r['probabilities'])==set(by[r['id']]['choices'])
 assert all(math.isfinite(x) and 0<=x<=1 for x in r['probabilities'].values())
 assert abs(sum(r['probabilities'].values())-1)<1e-6
 assert not r['truncated']
metrics={'model':score(pred,rows,gold)}
for name,preds in result['baselines'].items():metrics[name]=score(preds,rows,gold)
# Select a fixed baseline on DEV ONLY (deterministic tie break); never select on heldout scores.
fixed=sorted(n for n in metrics if n.startswith('fixed_'));chosen=max(fixed,key=lambda n:metrics[n]['dev']['acceptable'])
summary=dict(metrics=metrics,dev_selected_fixed=chosen,wrong_ids=[r['id'] for r in pred if r['choice'] not in gold[r['id']]['acceptable']],clearly_bad_ids=[r['id'] for r in pred if r['choice'] in gold[r['id']]['clearly_bad']],timing={k:v for k,v in result.items() if k not in ['predictions','baselines']},limits=['48 outputs represent 8 correlated series, not 48 independent problems.','Heldout refers to prospective split without post-result tuning, not author-blind independent gold.','p95 is descriptive only; warm values mix 47 different prompts, not repeated identical requests.','Static recorded trajectories are counterfactual for most selected actions; no causal win rate or induced behavior measured.'])
(out/'scores.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
