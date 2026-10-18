"""Offline evidence/state audit; never executes candidate code or repairs state."""
import hashlib,json,re,subprocess,sys
from pathlib import Path
import jsonschema
R=Path(__file__).parent;root=R.parents[3];load=lambda p:json.loads(p.read_text());key=lambda x:str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold();norm=lambda s:re.sub(r'\s+',' ',s).strip();report=load(R/'report.json');state=root/'experiments/research-repro-radar/evening-state';base=report['canonical_base']
def previous(name):return subprocess.check_output(['git','show',base+':experiments/research-repro-radar/evening-state/'+name+'.json'],cwd=root)
old=json.loads(previous('queue'))['candidates'];new=load(state/'queue.json')['candidates'];oldkeys={key(x)for x in old};newkeys={key(x)for x in new}
assert len(old)==5186 and len(new)==len(newkeys)==5204;assert oldkeys<=newkeys;assert report['completed_ids']==[] and report['recommended']==[]
oldmap={key(x):x for x in old}
for x in new:
 if key(x)in oldmap:assert x.get('carried_over_since')==oldmap[key(x)].get('carried_over_since')
for n in ['seen','mail']:assert (state/f'{n}.json').read_bytes()==previous(n)
for n in ['seen','queue','mail','freshness']:
 local=root/'.agents/skills/research-repro-radar/state'/f'{n}.json'
 if local.exists():assert local.read_bytes()==(state/f'{n}.json').read_bytes()
fresh=load(state/'freshness.json')['history'];oldfresh=json.loads(previous('freshness'))['history'];assert fresh[:-1]==oldfresh and fresh[-1]['run_id']==report['run_id'] and fresh[-1]['recommended_total']==0
rows=load(R/'all-candidates.json');assert len(rows)==len({key(x)for x in rows})==2516;rank=load(R/'ranking.json');assert rank['counts']['input']==6049 and rank['counts']['ranked']==5198 and rank['counts']['invalid']==0
reviews=load(R/'reviews.json');assert len(reviews)==29;assert [x['id']for x in reviews[:20]]==[x['id']for x in rank['review_pool']]
ev=load(R/'review-evidence.json');src={};unique={}
for e in ev:
 if 'error'in e:continue
 data=(R/e['file']).read_bytes();assert len(data)==e['bytes'] and hashlib.sha256(data).hexdigest()==e['sha256'];unique[e['file']]=len(data);src[e['url']]=norm(e['text'])
 if e['kind']=='repo':
  d=json.loads(e['text']);src[e['url']]+=' '+norm(json.dumps({k:d.get(k)for k in ['full_name','created_at','pushed_at','updated_at','stargazers_count','forks_count','open_issues_count','license','archived','default_branch']},ensure_ascii=False))
for e in ev:
 if 'error'in e:continue
 try:src[e['url']]+=' '+norm(json.dumps(json.loads(e['text']),ensure_ascii=False))
 except (ValueError,TypeError):pass
schema=load(root/'.agents/skills/research-repro-radar/references/native-candidate.schema.json')
for x in reviews:
 j=x['judgment'];jsonschema.validate(j,schema);assert j['candidate_id']==x['id'];assert j['verification_level']=='reading'
 for e in j['evidence']:assert norm(e['quote'])in src[e['source_url']],(x['id'],e)
ledger=load(R/'native-host-ledger.json');assert ledger['actual_service_model']=='unknown' and ledger['requested_model']=='gpt-6-luna';assert len(ledger['requests'])==49 and len({x['request_id']for x in ledger['requests']})==49;assert all(x['validation_exit']==0 for x in ledger['requests']);assert len(ledger['probe_and_saved'])==2
assert len(report['digest'])<=15 and not {x['id']for x in report['digest']}&{x['id']for x in report['reading_deep_dive_pending']}
audit=load(root/'benchmarks/research-repro-radar/2026-10-18-evening/metadata-audit.json');assert len(unique)==audit['primary_files']==116;assert sum(unique.values())==audit['primary_payload_bytes'];assert audit['candidate_code_executions']==audit['model_inferences']==audit['candidate_repairs_this_run']==audit['money_jpy']==0
manifest=load(R/'evidence-manifest.json')
for e in manifest:
 data=(root/e['file']).read_bytes();assert hashlib.sha256(data).hexdigest()==e['sha256'],e['file']
 if '--check-index'in sys.argv:
  b=subprocess.check_output(['git','show',':'+e['file']],cwd=root);assert hashlib.sha256(b).hexdigest()==e['sha256'],('index normalization',e['file'])
print(json.dumps({'ok':True,'collected':2516,'processed':6049,'ranked':5198,'reviewed':29,'native_judgments':49,'candidate_code_executions':0,'model_inferences':0,'pending':5204,'all_5186_previous_items_retained':True,'seen_mail_unchanged':True,'freshness_one_zero_recommendation_run':True,'source_files':116,'manifest_files':len(manifest),'index_checked':'--check-index'in sys.argv}))
