"""Offline audit of public saved answers; never runs any model or simulator."""
import json,sys,time,resource,subprocess
from pathlib import Path
start=time.perf_counter()
subprocess.run([sys.executable,'-m','tavernbench.report','--set','data/eval','--runs','results/eval','--systems','tavernagent,gpt-6-astra,claude-opus-5.5,jev','--out','report.json'],check=True,stdout=sys.stderr)
result=json.loads(Path('report.json').read_text())
labels={x['id']:x for x in map(json.loads,Path('data/eval/labels.jsonl').read_text().splitlines())}
prompts={x['id']:x for x in map(json.loads,Path('data/eval/prompts.jsonl').read_text().splitlines())}
assert len(labels)==len(prompts)==368
assert set(result)=={'chance','tavernagent','gpt-6-astra','claude-opus-5.5','jev'}
for value in result.values():
 assert 0<=value['play']<=1 and 0<=value['rule_score']<=1
# Independent exact-label scorer, without importing candidate scorer.
exact={k:v for k,v in labels.items() if v['scoring']=='exact'}
checks={}
for name in ['tavernagent','gpt-6-astra','claude-opus-5.5','jev']:
 answers={x['id']:x for x in map(json.loads,Path(f'results/eval/{name}/answers.jsonl').read_text().splitlines())};correct=0; eligible=0
 for k,l in exact.items():
  a=answers.get(k,{})
  if a.get('error') or a.get('is_error') or a.get('finish_reason') in {'length','incomplete','content_filter','tool_calls','timeout','error'}:continue
  if a.get('mode')!='structured' and (a.get('output_tokens') or 0)>4096:continue
  try:
   text=a['response'];first=text.index('{');last=text.rindex('}');pred=json.loads(text[first:last+1]).get('answer');gold=l['answer'];eligible+=1
   correct+=int(pred.strip().lower()==gold.lower()) if isinstance(gold,str) and isinstance(pred,str) else int(pred==gold)
  except (KeyError,ValueError,TypeError):pass
 checks[name]={'exact_items':len(exact),'parseable_within_budget':eligible,'correct':correct,'accuracy':correct/len(exact),'saved_answer_rows':len(answers)}
out={'status':'complete','kind':'saved_answer_scorer_audit_not_model_inference','task_count':368,'source_commit':'fb61201a557ddd36a411cf44aac51ce4d9b606ce','report':result,'independent_exact_check':checks,'audit_wall_seconds':time.perf_counter()-start,'audit_peak_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,'latency_fields_in_report':'authors archived inference observations, not our measurements','actual_model_inferences':0}
print(json.dumps(out))
