import json,pathlib,statistics,math
p=pathlib.Path(__file__).parent
inputs=json.loads((p/'inputs.json').read_text());gold=json.loads((p/'gold.json').read_text());result={}
for name in ['baseline','model-result']:
 data=json.loads((p/(name+'.json')).read_text());pred=data['predictions'];assert len(pred)==len(inputs) and {x['id'] for x in pred}==set(gold)
 by={x['id']:x for x in pred};metrics={}
 for group in ['nlp','game']:
  for variant in ['all']+sorted({x['variant'] for x in inputs if x['group']==group}):
   rows=[x for x in inputs if x['group']==group and (variant=='all' or x['variant']==variant)];n=len(rows);c=sum(by[x['id']]['prediction']==gold[x['id']] for x in rows);labels=sorted({gold[x['id']] for x in rows});fs=[]
   for label in labels:
    tp=sum(by[x['id']]['prediction']==label==gold[x['id']] for x in rows);fp=sum(by[x['id']]['prediction']==label!=gold[x['id']] for x in rows);fn=sum(by[x['id']]['prediction']!=label==gold[x['id']] for x in rows);fs.append(2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0)
   times=[by[x['id']]['seconds'] for x in rows];metrics[group+'-'+variant]={'count':n,'correct':c,'accuracy':c/n,'macro_f1':sum(fs)/len(fs),'mean_seconds':statistics.mean(times),'median_seconds':statistics.median(times),'throughput_per_minute':60/statistics.mean(times),'wall_hours_per_10000':statistics.mean(times)*10000/3600,'wrong_ids':[x['id'] for x in rows if by[x['id']]['prediction']!=gold[x['id']]]}
 result[name]={'metrics':metrics,'timing':{k:v for k,v in data.items() if k!='predictions'}}
result['limitations']=['Synthetic diagnostic, 12 unique NLP situations each clean/noisy and 4 game states each prose/JSON; variants are not independent samples.','Rule baseline is deliberately shallow; no strong competing model.','No calibration, tuning, long-input or held-out external dataset test.','Both model and baseline measured in isolated containers with same image, CPU quota2, RAM8GB, network-none; model uses two Torch threads. Baseline submillisecond timing is timer-sensitive.','No large-effect or generalization claim from this fixture.']
(p/'scores.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
