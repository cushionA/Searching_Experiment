import hashlib,json,re,sys
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];sys.path.insert(0,str(root/'.agents/skills/research-repro-radar/scripts'));import native_judge
P=Path('/tmp/radar-native-20261017/reviews');rows=json.loads((R/'review-targets.json').read_text());reviews=[];ledger=[]
def norm(s):return re.sub(r'\s+',' ',s).strip()
for i,row in enumerate(rows):
 a=P/f'{i:02}.request.json';b=P/f'{i:02}.response.json'
 if not b.exists(): print('PENDING',i);continue
 req=native_judge.load_json_file(a);res=native_judge.load_json_file(b);v=native_judge.validate_response(req,res);assert v['verdict'] is not None,(i,v)
 j=v['verdict'];assert j['candidate_id']==row['id']
 payload=json.loads(req['payload']);sources={x['url']:norm(x['text']) for x in payload['sources']}
 quotes=[e['source_url'] in sources and norm(e['quote']) in sources[e['source_url']] for e in j['evidence']]
 assert all(quotes),(i,'quote mismatch',j['evidence'])
 reviews.append({'id':row['id'],'judgment':j,'quote_checked':True,'priority':{'test':'A','defer':'B','reject':'C'}[j['decision']]})
 ledger.append({'index':i,'request_id':req['request_id'],'task_sha256':req['task_sha256'],'request_sha256':hashlib.sha256(a.read_bytes()).hexdigest(),'response_sha256':hashlib.sha256(b.read_bytes()).hexdigest(),'validation_exit':0})
(R/'reviews.json').write_text(json.dumps(reviews,ensure_ascii=False,indent=2)+'\n')
(R/'review-validation.json').write_text(json.dumps(ledger,indent=2)+'\n')
print({'validated':len(reviews),'priorities':{p:sum(x['priority']==p for x in reviews)for p in 'ABC'}})
for x in reviews:print(x['id'],x['priority'],x['judgment']['reason'][:200])
