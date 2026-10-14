exec(open('/workspace/radar-private/20261014/fetch_review.py').read().split('jobs=[]')[0])
models=['baidu/Unlimited-OCR','microsoft/BitNet-b1.58-2B-4T','microsoft/BitNet-embedding-270M','microsoft/BitNet-embedding-0.6B']
jobs=[('https://huggingface.co/api/models/'+m+'?blobs=true','model-'+str(i)) for i,m in enumerate(models)]
jobs += [('https://recipes.vllm.ai/baidu/Unlimited-OCR','unlimited-vllm')]
res=list(concurrent.futures.ThreadPoolExecutor(max_workers=4).map(lambda p:fetch(*p),jobs));(r/'model-metadata-ledger.json').write_text(json.dumps(res,indent=2))
for m,x in zip(models,res):
 if 'file'not in x:print(x);continue
 d=json.loads((r/x['file']).read_text());weights=[(a['rfilename'],a.get('size')) for a in d.get('siblings',[]) if a['rfilename'].endswith(('.safetensors','.gguf','.bin'))];print(m,d.get('sha'),(d.get('cardData')or{}).get('license'),weights)
