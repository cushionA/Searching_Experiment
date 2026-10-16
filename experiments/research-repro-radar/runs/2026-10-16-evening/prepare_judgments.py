import json,re,sys
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];sys.path.insert(0,str(root/'.agents/skills/research-repro-radar/scripts'));import native_judge
rows=json.loads((R/'review-targets.json').read_text());ev=json.loads((R/'review-evidence.json').read_text())
out=Path('/tmp/radar-oct16-native/reviews');out.mkdir(exist_ok=True)
schema=json.loads((root/'.agents/skills/research-repro-radar/references/native-candidate.schema.json').read_text())
instruction='''2026-10-16 JST evening research radar. Judge this candidate using only supplied primary evidence. Japanese concise reason with purpose, novelty, workload fit and adoption condition. test=A worth trying now (PoC not prerequisite); defer=B conditional; reject=C not needed with explicit evidence-backed reason. Measured candidate inference=0; all performance values author claims. 0 JPY,30min run,20min CPU/candidate,8GiB RAM,2GB downloads, no training or paid backend. Deep dive requires TWO INDEPENDENT hotness signals + primary artifact; author repo and paper alone are NOT independent; novelty/freshness/keywords are not adoption evidence. recommended_category=null unless independent signals explicit. Record unknown license/commercial use/install/resources/model IDs/successors explicitly in unconfirmed. No automatic decline solely because age. Keep Squeez and BitNet stopped, do not propose trials; morning LightOnOCR3 work and SKU excluded. Reading is not smoke or compared. Quote exact short verbatim excerpts from supplied evidence with source URLs. Never invent measured timings or quality. Include concrete next_check if useful.'''
for i,row in enumerate(rows):
 sources=[]
 for e in ev:
  if e['id']!=row['id']:continue
  t=e.get('text','')
  if e['kind']=='paper':
   t=re.sub(r'\s+',' ',t);start=t.find('[Submitted');t=t[start:start+18000] if start>=0 else t[:18000]
  if e['kind']=='repo':
   d=json.loads(t);t=json.dumps({k:d.get(k) for k in ['full_name','description','created_at','pushed_at','updated_at','stargazers_count','forks_count','open_issues_count','license','archived','default_branch']},ensure_ascii=False)
  sources.append({'url':e['url'],'kind':e['kind'],'sha256':e.get('sha256'),'text':t[:24000]})
 payload=json.dumps({'candidate':row,'sources':sources},ensure_ascii=False)
 req=native_judge.prepare_request(instruction,payload,schema)
 (out/f'{i:02}.request.json').write_text(json.dumps(req,ensure_ascii=False))
print({'requests':len(rows)})
