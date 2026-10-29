import json,pathlib,urllib.request,hashlib,time
root=pathlib.Path('benchmarks/tactical-intent-20261030');p=pathlib.Path('/tmp/tactical-trial/model');log=[]
for name,repo,files in [('onnx','manjunathshiva/opendecider-nano-ONNX',['onnx/model_q8.onnx','opendecider.json','README.md','LICENSE','NOTICE']),('nano','manjunathshiva/opendecider-nano',['tokenizer.json','tokenizer_config.json','config.json'])]:
 meta=json.loads((root/'sources'/f'{name}-meta.txt').read_text());rev=meta['sha']
 for f in files:
  info=next(x for x in meta['siblings'] if x['rfilename']==f);url=f'https://huggingface.co/{repo}/resolve/{rev}/{f}';dest=p/(f.replace('/','_'));start=time.monotonic();h=hashlib.sha256();n=0
  with urllib.request.urlopen(url,timeout=60) as r, dest.open('wb') as out:
   while b:=r.read(1024*1024):
    n+=len(b);assert n<=info['size'];out.write(b);h.update(b)
  assert n==info['size'];expected=info.get('lfs',{}).get('sha256');assert expected is None or h.hexdigest()==expected
  log.append(dict(url=url,revision=rev,file=dest.name,bytes=n,sha256=h.hexdigest(),seconds=time.monotonic()-start));print(dest.name,n,flush=True)
(root/'download-manifest.json').write_text(json.dumps(log,indent=2))
