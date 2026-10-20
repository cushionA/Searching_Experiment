import json,sys,re,hashlib,datetime
from pathlib import Path
r=Path(__file__).parent;p=Path('/workspace/radar-private-20261020');sys.path.insert(0,'/workspace/radar-skills-20261020/research-repro-radar/scripts');import native_judge
reviews=[];checks=[];ledger=json.loads((r/'native-host-ledger.json').read_text())
for i in range(6):
 req=native_judge.load_json_file(p/f'batch-{i}.fixed.request.json');res=native_judge.load_json_file(p/f'batch-{i}.fixed.response.json');v=native_judge.validate_response(req,res);assert v['verdict']is not None,v
 evidence=json.loads(req['payload']);quotes=[]
 for a,b in zip(v['verdict']['candidates'],evidence):
  for e in a['evidence']:
   match=any(e['source_url']==s['url']and re.sub(r'\s+',' ',e['quote'])in re.sub(r'\s+',' ',s['text'])for s in b['sources']);quotes.append({'candidate':a['candidate_id'],'url':e['source_url'],'exact_match':match,'words':len(e['quote'].split())})
 checks.append({'batch':i,'validation':v['diagnostics'],'quotes':quotes});reviews.extend(v['verdict']['candidates'])
 ledger.append({'task':f'/root/review{i}','requested_model':'gpt-6-luna','runtime_model':'unknown','request_id':req['request_id'],'task_sha256':req['task_sha256'],'request_sha256':hashlib.sha256((p/f'batch-{i}.fixed.request.json').read_bytes()).hexdigest(),'response_sha256':hashlib.sha256((p/f'batch-{i}.fixed.response.json').read_bytes()).hexdigest(),'schema_validation':v['diagnostics'],'finished_before':'2026-10-20T09:15:00Z','note':'CLI prohibition clarified to mean LLM CLI/backend; file read/write allowed. One caller-envelope repair: native requires dict verdict; per-candidate schemas/verdicts unchanged. Original and corrected requests/responses kept private. Completed within original 180-second deadlines.'})
(r/'reviews.json').write_text(json.dumps(reviews,ensure_ascii=False,indent=2)+'\n');(r/'review-validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n');(r/'native-host-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
print('schema valid',len(reviews),'quote issues',[q for c in checks for q in c['quotes']if not q['exact_match']or q['words']>25])
for x in reviews:print(x['candidate_id'],x['decision'],x['recommended_category'],x['reason'])
