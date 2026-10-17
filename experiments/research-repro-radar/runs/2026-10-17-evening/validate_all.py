import hashlib,json,re,sys
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];sys.path.insert(0,str(root/'.agents/skills/research-repro-radar/scripts'));import native_judge
P=Path('/tmp/radar-native-20261017/reviews');norm=lambda s:re.sub(r'\s+',' ',s).strip();ledger=[];latest={};failures=[]
for a in sorted(P.glob('*.request.json')):
 b=a.with_name(a.name.replace('request','response'));idx=int(a.name.split('.')[0])
 if not b.exists():failures.append({'index':idx,'failure':'pending'});continue
 req=native_judge.load_json_file(a);res=native_judge.load_json_file(b);result=native_judge.validate_response(req,res);v=result['verdict']
 if v is None:failures.append({'index':idx,'failure':result});continue
 payload=json.loads(req['payload']);assert v['candidate_id']==payload['candidate']['id'];src={x['url']:norm(x['text'])for x in payload['sources']}
 bad=[e for e in v['evidence']if e['source_url']not in src or norm(e['quote'])not in src[e['source_url']]]
 if bad:failures.append({'index':idx,'failure':'quote mismatch','evidence':bad});continue
 ledger.append({'index':idx,'candidate_id':v['candidate_id'],'request_id':req['request_id'],'task_sha256':req['task_sha256'],'request_sha256':hashlib.sha256(a.read_bytes()).hexdigest(),'response_sha256':hashlib.sha256(b.read_bytes()).hexdigest(),'validation_exit':0,'quote_checked':True})
 latest[v['candidate_id']]={'id':v['candidate_id'],'request_index':idx,'judgment':v,'priority':{'test':'A','defer':'B','reject':'C'}[v['decision']],'quote_checked':True}
print(json.dumps({'validated':len(ledger),'unique':len(latest),'failures':failures},ensure_ascii=False))
if failures:raise SystemExit(1)
rows=json.loads((R/'review-targets.json').read_text());ordered=[latest[x['id']]for x in rows]
(R/'reviews.json').write_text(json.dumps(ordered,ensure_ascii=False,indent=2)+'\n');(R/'review-validation.json').write_text(json.dumps(ledger,indent=2)+'\n')
for x in ordered:print(x['id'],x['priority'],x['judgment']['recommended_category'],x['judgment']['reason'])
