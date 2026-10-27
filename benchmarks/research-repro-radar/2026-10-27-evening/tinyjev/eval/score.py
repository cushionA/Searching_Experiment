"""Independent scorer for the fixed fixture; candidate code is never imported."""
import json,sys,statistics
from pathlib import Path
inputs={x['id']:x for x in json.loads(Path(sys.argv[1]).read_text())}
gold=json.loads(Path(sys.argv[2]).read_text())
baseline=json.loads(Path(sys.argv[3]).read_text())
preds={x['id']:x for x in baseline['predictions']}
assert set(inputs)==set(gold)==set(preds)
groups={}
for domain in ('nlp','game'):
 for variant in sorted({x['variant']for x in inputs.values()if x['domain']==domain}):
  ids=[i for i,x in inputs.items()if x['domain']==domain and x['variant']==variant]
  correct=sum(preds[i]['choice']==gold[i]for i in ids)
  groups[domain+'/'+variant]={'n':len(ids),'correct':correct,'accuracy':correct/len(ids),'mean_seconds':statistics.mean(preds[i]['wall_seconds']for i in ids)}
out={'baseline':groups,'baseline_peak_rss_kib':baseline['peak_rss_kib'],'baseline_total_seconds':baseline['total_seconds'],'candidate':None,'candidate_status':'not run; dependency isolation installation failed; no quality/latency/memory inference values','not_generalization_evidence':True}
Path(sys.argv[4]).write_text(json.dumps(out,indent=2)+'\n')
