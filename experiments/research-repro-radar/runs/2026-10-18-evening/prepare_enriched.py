import json,re,sys
from pathlib import Path
R=Path(__file__).parent;root=R.parents[3];sys.path.insert(0,str(root/'.agents/skills/research-repro-radar/scripts'));import native_judge
P=Path('/workspace/radar-native-20261018/reviews');rows=json.loads((R/'review-targets.json').read_text());ev=json.loads((R/'review-evidence.json').read_text());schema=json.loads((root/'.agents/skills/research-repro-radar/references/native-candidate.schema.json').read_text());instruction=json.loads((P/'00.request.json').read_text())['instruction']
instruction+=' Added primary evidence changes previous unknowns. License API404 means unconfirmed, not permission. Edge0 allocator peak is NOT RSS; small active params do not imply small download. AfriNLLB CC-BY-NC restricts commercial use. Bekko System One is a DIFFERENT model from embedding and S1MB TaskAvg NOT accuracy, synthetic600 English cases self-reviewed; do not claim Japanese/generalization. C priority does not automatically complete a pending trial. Only propose hypothesis, no execution. At most TWO exact short quotes; copy verbatim including case. Never mix candidates. All model/quality numbers author metadata, not inference measurements.'
indices=[0,1,2,3,6,8,10,11,12,13,15,16,17,20,21,22,23,24,25,26,27,28];mapping=[]
for number,index in enumerate(indices,27):
 row=rows[index];sources=[]
 for e in ev:
  if e['id']!=row['id']or'error'in e:continue
  t=e.get('text','')
  if e['kind']=='repo':
   d=json.loads(t);t=json.dumps({k:d.get(k)for k in ['full_name','created_at','pushed_at','updated_at','stargazers_count','forks_count','open_issues_count','license','archived','default_branch']},ensure_ascii=False)
  elif e['kind'].startswith('model-') and e['kind']!='model-search':
   d=json.loads(t);t=json.dumps({k:d.get(k)for k in ['id','sha','createdAt','lastModified','downloads','likes','cardData','safetensors','siblings']},ensure_ascii=False)
  elif e['kind']=='paper':
   t=re.sub(r'\s+',' ',t);a=t.find('[Submitted');t=t[a:]if a>=0 else t
  sources.append({'url':e['url'],'kind':e['kind'],'sha256':e.get('sha256'),'text':t[:32000]})
 req=native_judge.prepare_request(instruction,json.dumps({'candidate':row,'sources':sources},ensure_ascii=False),schema)
 path=P/f'{number:02}.request.json'
 with path.open('x')as f:json.dump(req,f,ensure_ascii=False)
 mapping.append({'index':number,'target_index':index,'id':row['id']})
(R/'enriched-judgment-map.json').write_text(json.dumps(mapping,indent=2)+'\n');print(mapping)
