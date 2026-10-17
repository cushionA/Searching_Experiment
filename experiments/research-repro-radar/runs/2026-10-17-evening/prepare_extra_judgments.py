import json,re,sys
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];sys.path.insert(0,str(root/'.agents/skills/research-repro-radar/scripts'));import native_judge
P=Path('/tmp/radar-native-20261017/reviews');rows=json.loads((R/'review-targets.json').read_text());ev=json.loads((R/'review-evidence.json').read_text());schema=json.loads((root/'.agents/skills/research-repro-radar/references/native-candidate.schema.json').read_text())
base=json.loads((P/'00.request.json').read_text())['instruction']
base+='\nIMPORTANT: scope is BROAD NLP classification/summarization/extraction, lightweight vision, small CPU models AND AI development. Workloads guide usefulness, NOT exclusion by current OCR/SKU demand. SKU is outside this task. A=test worth trying now, does NOT require PoC or deep_dive qualification. Independent popularity signals may include observed HF trending and GitHub community stars on separate platforms; these are attention, NEVER measured benefit or independent reproduction. A fresh repo is not an independent hot signal. Be conservative about quality/resources; usedStorage is repository storage NOT required single-download size. No inference completed in this run. Harness2 attempt STOPPED at one-repair cap before demo code; do NOT propose retry without new user continuation. It can remain B reading-deepdive if justified. '
extra_map=[]
indices=list(range(25,28))+[20,21,22,23,24,4,6,9,14,15,7,10]
for num,idx in enumerate(indices,25):
 row=rows[idx];sources=[]
 for e in ev:
  if e['id']!=row['id']:continue
  t=e.get('text','')
  if e['kind']=='paper':
   t=re.sub(r'\s+',' ',t);start=t.find('[Submitted');t=t[start:start+18000] if start>=0 else t[:18000]
  if e['kind']=='repo':
   d=json.loads(t);t=json.dumps({k:d.get(k)for k in ['full_name','created_at','pushed_at','updated_at','stargazers_count','forks_count','open_issues_count','license','archived']},ensure_ascii=False)
  sources.append({'url':e['url'],'text':t[:26000],'sha256':e.get('sha256')})
 for e in json.loads((R/'extra-model-ledger.json').read_text()):
  if ('pico' in e['file'] and idx==25)or('lightvae' in e['file']and idx==26)or('scholar' in e['file']and idx==27):
   if 'error'not in e:sources.append({'url':e['url'],'text':(R/e['file']).read_text()[:40000],'sha256':e['sha256']})
 notes={}
 if 'dm-code-agent'in row['id']:
  notes=json.loads((R/'duplicate-evidence.json').read_text());notes['previously_dismissed']=['github:jasonpolyakov44995/dm-code-agent','github:maximmulder1213/dm-code-agent'];notes['host_check']='All eight README files hash identically; retain Harper representative. Do not treat distinct copies as independent new projects. Explicit dismissal of duplicates justified.'
 if idx==20:notes={'attempts':2,'repair_count':1,'candidate_code_started':False,'model_inference':0,'failure':'Initial public source directory mode0700 fixed to0755 once, then wrapper isolation assertion raises PermissionError when statting inaccessible /root/.codex. STOP; new user continuation required. No runtime/model metrics.'}
 payload=json.dumps({'candidate':row,'sources':sources,'parent_verified_notes':notes},ensure_ascii=False);req=native_judge.prepare_request(base,payload,schema);p=P/f'{num:02}.request.json';assert not p.exists();p.write_text(json.dumps(req,ensure_ascii=False));extra_map.append({'index':num,'target_index':idx,'id':row['id']})
(R/'extra-judgment-map.json').write_text(json.dumps(extra_map,indent=2));print(extra_map)
