"""Fetch public primary sources as bytes; never import or execute candidate code."""
import ast, hashlib, json, pathlib, time, urllib.request, datetime
ROOT=pathlib.Path(__file__).resolve().parent
OUT=ROOT/'sources'; OUT.mkdir(exist_ok=True)
manifest=[]
def get(name,url):
 start=time.perf_counter(); row={'name':name,'url':url,'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(url,timeout=40) as r: data=r.read(); row['resolved_url']=r.url
  (OUT/name).write_bytes(data)
  row.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),error=None)
 except Exception as e:
  row['error']=str(e); data=b''
 row['seconds']=time.perf_counter()-start; manifest.append(row)
 (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 return data
repo='EvoScientist/SearchJev'; sha='054ea81d43071b93e2d75b7396a729a2598ac6ac'; modelsha='9af4b6c653e48abd74032510f080e467534ca6dd'
get('repo.json','https://api.github.com/repos/'+repo)
get('commit.json','https://api.github.com/repos/'+repo+'/commits/'+sha)
for f in ['README.md','LICENSE','training/inference.py','training/modeling.py','requirements.txt']:
 get(f.replace('/','_')+'.txt','https://raw.githubusercontent.com/'+repo+'/'+sha+'/'+f)
get('paper.html','https://arxiv.org/html/2610.05107v1')
get('paper-abs.html','https://arxiv.org/abs/2610.05107v1')
get('model.json','https://huggingface.co/api/models/SearchJev/SearchJev-0.8B/revision/'+modelsha+'?blobs=true')
for f in ['README.md','adapter/adapter_config.json','calibration.json','label_space.json']:
 get('model_'+f.replace('/','_')+'.txt','https://huggingface.co/SearchJev/SearchJev-0.8B/resolve/'+modelsha+'/'+f)
get('base.json','https://huggingface.co/api/models/Qwen/Qwen3.5-0.8B?blobs=true')
get('mango-repo.json','https://api.github.com/repos/VichyTong/Mango')
get('mango-readme.txt','https://raw.githubusercontent.com/VichyTong/Mango/c6490d5530d9f0d6a189fe107d1bb17183dc4caf/README.md')
s=ast.parse((OUT/'training_inference.py.txt').read_text())
cls=next(n for n in s.body if isinstance(n,ast.ClassDef) and n.name=='JEVPredictor')
tta=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='decide_batch_tta')
defaults=dict(zip([a.arg for a in tta.args.args][-len(tta.args.defaults):], [ast.unparse(n) for n in tta.args.defaults]))
m=json.loads((OUT/'model.json').read_text()); b=json.loads((OUT/'base.json').read_text())
def weights(d):
 return [{'file':f['rfilename'],'bytes':f.get('size')} for f in d['siblings'] if f['rfilename'].endswith('.safetensors')]
result={'method':'metadata and AST only; no candidate execution','candidate_revision':sha,'model_revision':modelsha,'base_revision':b['sha'],'adapter_weights':weights(m),'base_weights':weights(b),'tta_defaults':defaults,'tta_observation':'identity plus trials random option permutations for choice/noul; score excluded','cpu_observation':'inference.py exposes CPU fallback; README requires CUDA kernels; working CPU inference unverified','candidate_trials':0,'money_jpy':0,'downloaded_weights_bytes':0,'successful_sources':sum(r['error'] is None for r in manifest),'source_bytes':sum(r.get('bytes',0) for r in manifest),'fetch_seconds':sum(r['seconds'] for r in manifest)}
p=ROOT.parents[1]/'benchmarks/daily-tech-20261023/audit.json'; p.write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result,indent=2))
