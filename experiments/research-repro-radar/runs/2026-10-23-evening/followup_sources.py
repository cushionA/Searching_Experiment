import json,urllib.request,urllib.parse,concurrent.futures as cf,datetime,hashlib,re
from pathlib import Path
r=Path('/workspace/evening-radar-20261023/experiments/research-repro-radar/runs/2026-10-23-evening')
jobs=[('2610.31574','collection','https://huggingface.co/api/collections/SphereLab/ttr-compressor'),('2610.31574','models','https://huggingface.co/api/models?author=SphereLab&search=ttr&limit=100'),('2610.30949','models','https://huggingface.co/api/models?search=IQuest-Q1&limit=100'),('2610.31062','dataset','https://huggingface.co/api/datasets/Plurai/SCOPE'),('2610.31062','dataset-card','https://huggingface.co/datasets/Plurai/SCOPE/raw/main/README.md'),('2610.19705','model','https://huggingface.co/api/models/e2rea1/RouteWeaver-4B?blobs=true'),('2610.19705','cost-model','https://huggingface.co/api/models/e2rea1/RouteWeaver-4B-cost?blobs=true'),('2610.28679','dataset','https://huggingface.co/api/datasets/Catherine1213/VerifierBench'),('2610.31574','hf-signal','https://huggingface.co/api/papers/2610.31574'),('2610.31574','independent-signal','https://aiweekly.co/alerts/ttr-compressor-matches-mlp-merger-with-556-fewer-visual-tokens')]
def get(j):
 ident,kind,url=j;d={'id':ident,'kind':kind,'url':url,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar metadata-only'}),timeout=25)as f:b=f.read(3000001);d['status']=f.status
  if len(b)>3000000:raise ValueError('size cap')
  p=r/'primary'/f'{ident}-{kind}-followup';p.write_bytes(b);d.update(file=str(p.relative_to(r)),sha256=hashlib.sha256(b).hexdigest(),text=b.decode('utf8','replace'))
 except Exception as e:d['error']=str(e)
 return d
out=list(cf.ThreadPoolExecutor(max_workers=4).map(get,jobs));(r/'followup-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print([{k:v for k,v in x.items()if k not in ['text','sha256','at']}for x in out])
for x in out:
 if 'text'not in x:continue
 if x['kind']=='models':print(x['id'],[d.get('id')for d in json.loads(x['text'])])
 if x['kind']=='model':
  d=json.loads(x['text']);print(d.get('id'),sum(z.get('size',0)for z in d.get('siblings',[])if z['rfilename'].endswith('.safetensors')))
