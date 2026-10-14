import json,urllib.request,concurrent.futures,hashlib,time,re,pathlib,datetime
r=pathlib.Path('/workspace/evening-radar/experiments/research-repro-radar/runs/2026-10-14-evening');out=r/'review-sources';out.mkdir(exist_ok=True)
rows=json.loads((r/'review-input.json').read_text())
def fetch(url,label):
 t=time.monotonic();d={'url':url,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'ResearchReproRadar/1.0'}),timeout=25) as f:b=f.read(2000000);d.update(status=f.status,final_url=f.url)
  (out/(label+'.txt')).write_bytes(b);d.update(file='review-sources/'+label+'.txt',sha256=hashlib.sha256(b).hexdigest(),bytes=len(b))
 except Exception as e:d['error']=str(e)
 d['seconds']=time.monotonic()-t;return d
jobs=[]
for i,x in enumerate(rows):
 jobs.append((x['url'],f'{i:02d}-primary'))
 if x['url'].startswith('https://huggingface.co/'):
  model=x['url'].split('huggingface.co/')[1];jobs.extend([(f'https://huggingface.co/{model}/raw/main/README.md',f'{i:02d}-card'),(f'https://huggingface.co/api/models/{model}',f'{i:02d}-model')])
 g=x['discovery'].get('github_url')
 if g:
  repo=g.rstrip('/').split('github.com/')[-1];jobs.extend([(f'https://api.github.com/repos/{repo}',f'{i:02d}-github'),(f'https://api.github.com/repos/{repo}/readme',f'{i:02d}-readme')])
res=list(concurrent.futures.ThreadPoolExecutor(max_workers=6).map(lambda p:fetch(*p),jobs));(r/'review-fetch-ledger.json').write_text(json.dumps(res,ensure_ascii=False,indent=2));print(json.dumps({'requests':len(res),'ok':sum('status' in x for x in res),'errors':[x for x in res if 'error'in x]}))
