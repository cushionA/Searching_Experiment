"""Audit continuity, source identity, judgments and honest failure accounting."""
import hashlib,json,re,subprocess,sys
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];sys.path.insert(0,str(root/'.agents/skills/research-repro-radar/scripts'));import jsonschema
load=lambda p:json.loads(p.read_text());key=lambda x:str(x.get('doi')or x.get('arxiv_id')or x.get('id')or x['url']).casefold();norm=lambda s:re.sub(r'\s+',' ',s).strip();report=load(R/'report.json');state=root/'experiments/research-repro-radar/evening-state';base=report['canonical_base']
def gitstate(ref,name):return subprocess.check_output(['git','show',ref+':experiments/research-repro-radar/evening-state/'+name+'.json'],cwd=root)
old=json.loads(gitstate(base,'queue'))['candidates'];now=load(state/'queue.json')['candidates'];oldkeys={key(x)for x in old};newkeys={key(x)for x in now};complete=set(report['completed_ids'])
assert len(old)==5176;assert oldkeys-complete<=newkeys;assert not complete&newkeys;assert len(now)==len(newkeys)==report['counts']['pending_after_record']==5186
older=json.loads(gitstate('f197d9d181e59998cfb61040ed763d308e512fa5','queue'))['candidates'];assert len(older)==1072 and {key(x)for x in older}<=newkeys
for name in ['seen','queue','mail','freshness']:assert (state/f'{name}.json').read_bytes()==(root/'.agents/skills/research-repro-radar/state'/f'{name}.json').read_bytes()
for name in ['seen','mail']:assert (state/f'{name}.json').read_bytes()==gitstate(base,name)
rows=load(R/'all-candidates.json');assert len(rows)==len({key(x)for x in rows})==2512;rank=load(R/'ranking.json');assert rank['counts']['input']==6040 and rank['counts']['invalid']==0
reviews=load(R/'reviews.json');assert len(reviews)==28;assert [x['id']for x in reviews[:20]]==[x['id']for x in rank['review_pool']]
ev=load(R/'review-evidence.json');src={}
for e in ev:
 t=e.get('text','');src[e['url']]=norm(t)
 if e['kind']=='repo':
  d=json.loads(t)
  for fields in [['full_name','description','created_at','pushed_at','updated_at','stargazers_count','forks_count','open_issues_count','license','archived','default_branch'],['full_name','created_at','pushed_at','updated_at','stargazers_count','forks_count','open_issues_count','license','archived']]:
   src[e['url']]+=' '+norm(json.dumps({k:d.get(k)for k in fields},ensure_ascii=False))
for e in load(R/'extra-model-ledger.json'):
 if 'error'not in e:src[e['url']]=norm((R/e['file']).read_text())
src['https://www.mdpi.com/1996-1073/19/20/4893']=norm((R/'primary/mdpi-clean.txt').read_text());src['https://huggingface.co/api/models/Qwen/Qwen2.5-Coder-1.5B-Instruct?blobs=true']=norm((R/'primary/qwen-exact-files.json').read_text())
schema=load(root/'.agents/skills/research-repro-radar/references/native-candidate.schema.json')
for x in reviews:
 j=x['judgment'];jsonschema.validate(j,schema);assert j['candidate_id']==x['id'];assert j['verification_level']in ['reading','unreviewed']
 for e in j['evidence']:assert norm(e['quote'])in src[e['source_url']],(x['id'],e)
led=load(R/'native-host-ledger.json');assert len(led['requests'])==43 and len({x['request_id']for x in led['requests']})==43;assert all(x['validation_exit']==0 for x in led['requests']);assert led['probe']['validation_exit']==0 and led['actual_service_model']=='unknown'
groups=load(R/'duplicate-evidence.json')['groups'];assert len(groups)==1;assert len(next(iter(groups.values())))==8;assert complete==set(next(iter(groups.values())))-{'github:harper3105/dm-code-agent'}
assert report['recommended']==[] and len(report['reading_deep_dive_pending'])==2;assert report['counts']['candidate_code_executions']==0
bench=load(root/'benchmarks/research-repro-radar/2026-10-17-harness2/result.json');assert bench['repair_count']==1 and bench['container_attempts']==2 and not bench['candidate_code_started'];assert bench['status']=='STOPPED_REPAIR_CAP'
manifest=load(R/'evidence-manifest.json')
for x in manifest:assert hashlib.sha256((root/x['file']).read_bytes()).hexdigest()==x['sha256'],x['file']
print(json.dumps({'ok':True,'collected':len(rows),'processed':6040,'ranked':5186,'reviewed':28,'native_judgments':43,'candidate_code_executions':0,'candidate_inferences':0,'pending':len(now),'explicit_dismissals':len(complete),'old1072_preserved':True,'state_four_files_equal':True,'seen_mail_unchanged':True,'manifest_files':len(manifest)}))
