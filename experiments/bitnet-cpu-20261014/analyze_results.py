"""Derive inspectable serial latency/token-rate summaries from captured server JSON."""
import json
import pathlib
import statistics
import sys
p=pathlib.Path(sys.argv[1]); rows=[json.loads(x) for x in (p/'raw.jsonl').read_text().splitlines()]
summary=json.loads((p/'summary.json').read_text())
expected=4 if summary.get('smoke',False) else 24
steady=[r for r in rows if r['phase']=='steady']
complete_repeats=[rep for rep in sorted({r['repeat'] for r in steady}) if len({r['id'] for r in steady if r['repeat']==rep})==expected]
out={'complete_repeats_used':complete_repeats,'incomplete_repeat_requests_excluded':sum(r['repeat'] not in complete_repeats for r in steady),'requests':len(rows),'first':next(r for r in rows if r['phase']=='first_request'),'groups':{}}
for lang in ['ja','en']:
 for task in ['classification','extraction']:
  rs=[r for r in rows if r['phase']=='steady' and r['language']==lang and r['task']==task and r['repeat'] in complete_repeats]
  ts=[r['raw']['timings'] for r in rs]
  out['groups'][lang+'/'+task]={'requests':len(rs),'median_wall_s':statistics.median(r['wall_s'] for r in rs),'serial_requests_per_s':len(rs)/sum(r['wall_s'] for r in rs),'generation_tokens':sum(t['predicted_n'] for t in ts),'generation_tokens_per_s':sum(t['predicted_n'] for t in ts)/(sum(t['predicted_ms'] for t in ts)/1000),'prompt_tokens_per_s':sum(t['prompt_n'] for t in ts)/(sum(t['prompt_ms'] for t in ts)/1000),'truncated_outputs':sum(r['raw'].get('truncated',False) or r['raw'].get('stopped_limit',False) or r['raw'].get('stop_type')=='limit' for r in rs),'field_accuracy_first_pass':sum(r['field_hits'] for r in rs if r['repeat']==0)/sum(r['fields'] for r in rs if r['repeat']==0),'format_successes_first_pass':sum(r['format_ok'] for r in rs if r['repeat']==0),'exact_successes_first_pass':sum(r['exact'] for r in rs if r['repeat']==0),'unique_examples_first_pass':sum(r['repeat']==0 for r in rs),'unique_output_strings':len(set(r['raw']['content'] for r in rs))}
print(json.dumps(out,ensure_ascii=False,indent=2))
