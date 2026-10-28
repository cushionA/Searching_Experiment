"""Exact-choice scoring against the unchanged prior fixture; stdlib only."""
import json,pathlib,statistics,math,hashlib
p=pathlib.Path('benchmarks/daily-tech-20261029')
inputs=json.loads((p/'inputs.json').read_text());gold=json.loads((p/'gold.json').read_text());result={}
manifest=json.loads((p/'fixture-manifest.json').read_text())
for n,h in manifest['sha256'].items():assert hashlib.sha256((p/n).read_bytes()).hexdigest()==h
for name in ['prior-baseline','prior-model-result','laya-result']:
 data=json.loads((p/(name+'.json')).read_text());pred=data['predictions'];assert len(pred)==len(inputs) and {x['id'] for x in pred}==set(gold)
 by={x['id']:x for x in pred};metrics={}
 for row in inputs:
  r=by[row['id']];assert r['prediction'] in dict(row['choices']);assert math.isfinite(r['seconds']) and r['seconds']>=0
  if name=='laya-result':
   a=r['raw']['answers']['decision'];probs=a['probabilities'];assert set(probs)==set(dict(row['choices']));assert all(math.isfinite(v) and 0<=v<=1 for v in probs.values());assert abs(sum(probs.values())-1)<.001
 for group in ['nlp','game']:
  for variant in ['all']+sorted({x['variant'] for x in inputs if x['group']==group}):
   rows=[x for x in inputs if x['group']==group and (variant=='all' or x['variant']==variant)];n=len(rows);c=sum(by[x['id']]['prediction']==gold[x['id']] for x in rows);labels=sorted({gold[x['id']] for x in rows});fs=[]
   for label in labels:
    tp=sum(by[x['id']]['prediction']==label==gold[x['id']] for x in rows);fp=sum(by[x['id']]['prediction']==label!=gold[x['id']] for x in rows);fn=sum(by[x['id']]['prediction']!=label==gold[x['id']] for x in rows);fs.append(2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0)
   times=[by[x['id']]['seconds'] for x in rows];metrics[group+'-'+variant]={'count':n,'correct':c,'accuracy':c/n,'macro_f1':sum(fs)/len(fs),'mean_seconds':statistics.mean(times),'median_seconds':statistics.median(times),'wrong_ids':[x['id'] for x in rows if by[x['id']]['prediction']!=gold[x['id']]]}
 result[name]={'metrics':metrics,'timing':{k:v for k,v in data.items() if k!='predictions'}}
result['limitations']=['32 representations of 16 synthetic underlying situations, not independent 32 cases.','Prior model and rules are saved observations at source SHA, not rerun today.','Cross-model timing is descriptive: separate sessions/CPUs; SDPA versus prior eager attention; one run, no repetition.','No held-out or real-site quality claim; no training or tuning.']
(p/'scores.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
