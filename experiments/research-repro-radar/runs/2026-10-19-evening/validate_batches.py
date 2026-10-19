import sys,json,re,time
from pathlib import Path
sys.path.insert(0,'/workspace/evening-radar-20261019/.agents/skills/research-repro-radar/scripts');import native_judge
r=Path('/workspace/radar-probe-20261019');run=Path('/workspace/evening-radar-20261019/experiments/research-repro-radar/runs/2026-10-19-evening');ev=json.loads((run/'review-evidence.json').read_text());norm=lambda x:re.sub(r'\s+',' ',x).strip();src={e['url']:norm(e['text'])for e in ev if 'text'in e};out=[];ledger=[]
for i in range(6):
 p=r/f'batch-{i}-repair.response.json'
 if not p.exists():print('WAIT',i);continue
 req=native_judge.load_json_file(r/f'batch-{i}-repair.request.json');res=native_judge.load_json_file(p);v=native_judge.validate_response(req,res);errors=[]
 if v['verdict']:
  for j in v['verdict']['reviews']:
   for e in j['evidence']:
    if norm(e['quote'])not in src.get(e['source_url'],''):errors.append({'id':j['candidate_id'],'quote':e})
  out+=v['verdict']['reviews']
 ledger.append({'batch':i,'diagnostics':v['diagnostics'],'quote_errors':errors,'request_id':req['request_id'],'task_sha256':req['task_sha256'],'response_delay_seconds':p.stat().st_mtime-(r/f'batch-{i}.request.json').stat().st_mtime})
 print(i,v['diagnostics'],errors)
(run/'review-validation.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n');(run/'reviews.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
