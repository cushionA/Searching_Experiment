import hashlib,json,re,subprocess,sys
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];sys.path.insert(0,str(root/'.agents/skills/research-repro-radar/scripts'));import jsonschema
load=lambda p:json.loads(p.read_text());key=lambda x:str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold();norm=lambda s:re.sub(r'\s+',' ',s).strip()
report=load(R/'report.json');state=root/'experiments/research-repro-radar/evening-state';base=report['canonical_base'];previous=json.loads(subprocess.check_output(['git','show',base+':experiments/research-repro-radar/evening-state/queue.json'],cwd=root))['candidates'];now=load(state/'queue.json')['candidates'];oldkeys={key(x)for x in previous};newkeys={key(x)for x in now};complete=set(report['completed_ids'])
assert len(previous)==5133
assert oldkeys-complete<=newkeys
assert not complete & newkeys
assert len(now)==len(newkeys)==report['counts']['pending_after_record']
older=json.loads(subprocess.check_output(['git','show','f197d9d181e59998cfb61040ed763d308e512fa5:experiments/research-repro-radar/evening-state/queue.json'],cwd=root))['candidates'];assert len(older)==1072;assert {key(x)for x in older}<=newkeys
for name in ['seen','queue','mail','freshness']:
 assert (state/f'{name}.json').read_bytes()==(root/'.agents/skills/research-repro-radar/state'/f'{name}.json').read_bytes()
for name in ['seen','mail']:
 assert (state/f'{name}.json').read_bytes()==subprocess.check_output(['git','show',base+':experiments/research-repro-radar/evening-state/'+name+'.json'],cwd=root)
rows=load(R/'all-candidates.json');assert len(rows)==len({key(x)for x in rows})==2515
rank=load(R/'ranking.json');assert rank['counts']['invalid']==0;assert rank['counts']['input']==6025
reviews=load(R/'reviews.json');assert len(reviews)==26;assert [x['id']for x in reviews[:20]]==[x['id']for x in rank['review_pool']]
schema=load(root/'.agents/skills/research-repro-radar/references/native-candidate.schema.json');evidence=load(R/'review-evidence.json');sources={x['url']:norm(x.get('text',''))for x in evidence}
sources['https://www.mdpi.com/1996-1073/19/20/4893']=norm((R/'primary/mdpi-full.txt').read_text());sources['https://docs.ultralytics.com/models/yolo27/']=norm((R/'primary/yolo27.txt').read_text());sources['https://huggingface.co/api/models/baidu/Unlimited-OCR']=norm((R/'primary/Unlimited-OCR.json').read_text())
for e in evidence:
 if e['kind']=='repo':
  d=json.loads(e['text']);sources[e['url']]=norm(json.dumps({k:d.get(k) for k in ['full_name','description','created_at','pushed_at','updated_at','stargazers_count','forks_count','open_issues_count','license','archived','default_branch']},ensure_ascii=False))
for x in reviews:
 j=x['judgment'];jsonschema.validate(j,schema);assert x['id']==j['candidate_id'];assert j['verification_level'] in ['reading','unreviewed']
 for e in j['evidence']:
  # JSON metadata rendered as a projection is semantically checked for numeric quote.
  if e['quote']=='"usedStorage": 6767737257':assert load(R/'primary/Unlimited-OCR.json')['usedStorage']==6767737257
  else:assert norm(e['quote']) in sources[e['source_url']],(x['id'],e)
led=load(R/'native-host-ledger.json');assert len(led['requests'])==29 and len({x['request_id']for x in led['requests']})==29;assert all(x['validation_exit']==0 for x in led['requests']);assert led['probe']['validation_exit']==0
assert led['actual_service_model']=='unknown' and led['requested_model']=='gpt-6-luna'
hashes=[x['sha256'] for x in evidence if 'dm-code-agent' in x['id']and x['kind']=='readme'];assert len(hashes)==3 and len(set(hashes))==1
assert report['recommended']==[] and len(report['reading_deep_dive_pending'])==2
assert report['counts']['candidate_inference_success']==0
manifest=load(R/'evidence-manifest.json')
for x in manifest:assert hashlib.sha256((root/x['file']).read_bytes()).hexdigest()==x['sha256'],x['file']
print(json.dumps({'ok':True,'collected':2515,'processed':6025,'ranked':5172,'reviewed':26,'native_judgments':29,'candidate_inferences':0,'pending':len(now),'explicit_dismissals':2,'old1072_preserved':True,'state_four_files_equal':True,'seen_mail_unchanged':True,'manifest_files':len(manifest)},ensure_ascii=False))
