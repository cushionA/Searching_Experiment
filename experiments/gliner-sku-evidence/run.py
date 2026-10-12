#!/usr/bin/env python3
"""Bounded CPU span extraction. No label input, semantic decision or SKU adoption."""
import argparse, hashlib, json, os, platform, resource, sys, time, traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'benchmarks/gliner-sku-evidence-20261012'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x):
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def chunks(text,processor,schema):
 words=list(processor.word_splitter(text));out=[];i=0
 while i<len(words):
  lo=i+1;hi=len(words);best=i
  while lo<=hi:
   mid=(lo+hi)//2;st=0 if i==0 else words[i][1];en=len(text) if mid==len(words) else words[mid-1][2]
   n=len(processor.transform_record(text[st:en],schema,max_len=None).input_ids)
   if n<=480:best=mid;lo=mid+1
   else:hi=mid-1
  if best==i:raise ValueError('single word exceeds encoder budget')
  st=0 if i==0 else words[i][1];en=len(text) if best==len(words) else words[best-1][2]
  out.append({'start':st,'end':en,'subwords':len(processor.transform_record(text[st:en],schema,max_len=None).input_ids)})
  if best==len(words):break
  i=max(i+1,best-8)
 assert all(c.isspace() or any(w['start']<=n<w['end'] for w in out) for n,c in enumerate(text))
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=2);ap.add_argument('--name',default='smoke');args=ap.parse_args()
 if not (0<=args.start<12 and 1<=args.count<=12 and args.start+args.count<=12):raise ValueError('12-product hard cap')
 t=time.perf_counter();import torch
 torch.set_num_threads(2);torch.set_num_interop_threads(1)
 from gliner2 import AutoExtractor
 sys.path.insert(0,str(ROOT/'experiments/sku-matching'))
 from backend_gliner_extract import verify_snapshot
 verify_snapshot(Path('/tmp/gliner-sku-model'))
 load_t=time.perf_counter();model=AutoExtractor.from_pretrained('/tmp/gliner-sku-model',local_files_only=True);model.eval()
 load_s=time.perf_counter()-load_t
 protocol=json.loads((OUT/'protocol.json').read_text());docs=json.loads((OUT/'input.json').read_text())[args.start:args.start+args.count]
 schema=model.create_schema().entities(protocol['fields'])
 plan=[{'product_id':d['product_id'],'chunks':chunks(d['text'],model.processor,schema)} for d in docs]
 dump(OUT/(args.name+'-plan.json'),{'input_sha256':sha(OUT/'input.json'),'protocol_sha256':sha(OUT/'protocol.json'),'code_sha256':sha(__file__),'plan':plan,'created_before_inference':True})
 results=[];error=None
 try:
  with torch.inference_mode():
   for d,p in zip(docs,plan):
    raw=[];times=[];merged={}
    for chunk in p['chunks']:
     a,b=chunk['start'],chunk['end'];begin=time.perf_counter()
     pred=model.extract(d['text'][a:b],schema,include_spans=True,include_confidence=True,threshold=protocol['threshold'],max_len=None)
     sec=time.perf_counter()-begin;times.append(sec);raw.append({'chunk':chunk,'seconds':sec,'output':pred})
     for field,spans in pred.get('entities',{}).items():
      for span in spans:
       s={**span,'start':span['start']+a,'end':span['end']+a,'field':field};s['literal_valid']=d['text'][s['start']:s['end']]==s['text']
       key=(field,s['start'],s['end'])
       if key not in merged or s.get('confidence',0)>merged[key].get('confidence',0):merged[key]=s
    row={'product_id':d['product_id'],'seconds':sum(times),'chunk_calls':raw,'entities':list(merged.values()),'semantic_status':'unassessed','row_applicability':'unassessed','production_adoption_allowed':False}
    results.append(row)
    dump(OUT/(args.name+'-'+d['product_id']+'.json'),row)
    print(json.dumps({'product_id':d['product_id'],'seconds':row['seconds'],'chunks':len(raw),'spans':len(merged),'rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024}),flush=True)
 except Exception:
  error=traceback.format_exc();print(error,flush=True)
 import importlib.metadata as im
 runtime={'model':'fastino/gliner2.5-multi-v1','revision':'cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3','architecture':type(model).__name__,'dtype':str(next(model.parameters()).dtype),'device':str(next(model.parameters()).device),'parameter_count':sum(p.numel() for p in model.parameters()),'threads':2,'load_seconds':load_s,'total_seconds':time.perf_counter()-t,'inference_seconds':sum(r['seconds'] for r in results),'products_completed':len(results),'chunk_calls':sum(len(r['chunk_calls']) for r in results),'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,'python':platform.python_version(),'platform':platform.platform(),'cpu_visible':os.cpu_count(),'versions':{k:im.version(k) for k in ['gliner2','torch','transformers','tokenizers','safetensors','huggingface-hub']},'error':error}
 dump(OUT/(args.name+'-runtime.json'),runtime)
 if error:sys.exit(1)
if __name__=='__main__':main()
