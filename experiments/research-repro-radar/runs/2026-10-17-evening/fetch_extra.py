"""Additional primary reading after broad ranking; never executes candidate code."""
import json,concurrent.futures as cf
from pathlib import Path
R=Path(__file__).parent
# Reuse reviewed metadata-only fetch function without its orchestration side effects.
exec((R/'fetch_review.py').read_text().split("rows=json.loads")[0])
queue=json.loads((R.parents[3]/'.agents/skills/research-repro-radar/state/queue.json').read_text())['candidates']
rows=json.loads((R/'review-targets.json').read_text());evidence=json.loads((R/'review-evidence.json').read_text())
ids=['2608.14658','2610.14726','2502.07286'];extra=[];jobs=[]
for ident in ids:
 x=next(x for x in queue if str(x.get('arxiv_id')or x['id'])==ident);x=dict(x);x['id']=ident;extra.append(x);jobs.append((ident,'paper','https://arxiv.org/abs/'+ident))
 if x.get('github_url'):
  repo=x['github_url'].split('github.com/')[-1].rstrip('/')
  jobs +=[(ident,'repo','https://api.github.com/repos/'+repo),(ident,'readme','https://raw.githubusercontent.com/'+repo+'/HEAD/README.md'),(ident,'releases','https://api.github.com/repos/'+repo+'/releases?per_page=3')]
with cf.ThreadPoolExecutor(max_workers=4)as ex:res=list(ex.map(get,jobs))
(R/'review-targets.json').write_text(json.dumps(rows+extra,ensure_ascii=False,indent=2)+'\n');(R/'review-evidence.json').write_text(json.dumps(evidence+res,ensure_ascii=False,indent=2)+'\n');print({'added':len(extra),'requests':len(res),'errors':[x for x in res if 'error'in x]})
