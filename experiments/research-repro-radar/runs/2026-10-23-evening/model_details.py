import json,urllib.request,urllib.parse,concurrent.futures as cf,datetime,hashlib,re
from pathlib import Path
r=Path('/workspace/evening-radar-20261023/experiments/research-repro-radar/runs/2026-10-23-evening')
jobs=[(ident,kind,url) for ident,kind,url in [('2610.31574', 'model-small', 'https://huggingface.co/api/models/sihany/ttr_345m?blobs=true'), ('2610.31574', 'model-1b', 'https://huggingface.co/api/models/sihany/ttr_1b?blobs=true'), ('2610.31574', 'model-vision', 'https://huggingface.co/api/models/sihany/ttr_vlm_conv_3_3?blobs=true'), ('2610.31574', 'small-card', 'https://huggingface.co/sihany/ttr_345m/raw/main/README.md'), ('2610.31574', 'small-config', 'https://huggingface.co/sihany/ttr_345m/raw/main/config.json'), ('2610.31574', 'attention-readme', 'https://raw.githubusercontent.com/Sphere-AI-Lab/ttr-compressor/HEAD/block-wise_sparse_attention/HiLS-Attention/README.md'), ('2610.31574', 'license', 'https://raw.githubusercontent.com/Sphere-AI-Lab/ttr-compressor/HEAD/LICENSE'), ('2610.30949', 'model-official', 'https://huggingface.co/api/models/IQuestLab/IQuest-Q1?blobs=true')]]
def get(j):
 ident,kind,url=j;d={'id':ident,'kind':kind,'url':url,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchRadar metadata-only'}),timeout=25)as f:b=f.read(3000001);d['status']=f.status
  if len(b)>3000000:raise ValueError('size cap')
  p=r/'primary'/f'{ident}-{kind}-followup';p.write_bytes(b);d.update(file=str(p.relative_to(r)),sha256=hashlib.sha256(b).hexdigest(),text=b.decode('utf8','replace'))
 except Exception as e:d['error']=str(e)
 return d
out=list(cf.ThreadPoolExecutor(max_workers=4).map(get,jobs));(r/'model-details-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print([{k:v for k,v in x.items()if k not in ['text','sha256','at']}for x in out])
for x in out:
 if 'text'not in x:continue
 if x['kind']=='models':print(x['id'],[d.get('id')for d in json.loads(x['text'])])
 if x['kind']=='model':
  d=json.loads(x['text']);print(d.get('id'),sum(z.get('size',0)for z in d.get('siblings',[])if z['rfilename'].endswith('.safetensors')))
