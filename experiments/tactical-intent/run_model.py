"""CPU ONNX reference path; no gold, credentials, network, or training."""
import time,json,resource,sys,pathlib
from bench import pack,validate,adaptive,stats
start=time.perf_counter()
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer
from vendor_prompt import nano_ids
import_end=time.perf_counter()
rows=json.load(open('/work/inputs.json'));tok=AutoTokenizer.from_pretrained('/work/model',local_files_only=True,trust_remote_code=False)
opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
session=ort.InferenceSession('/work/model/onnx_model_q8.onnx',sess_options=opts,providers=['CPUExecutionProvider'])
loaded=time.perf_counter();pred=[];baselines={}
# All methods receive exactly the same row, actions and observation history.
for name,fn in [('adaptive',adaptive)]+[(f'fixed_{k}',lambda r,k=k:k) for k in rows[0]['choices']]:
 results=[]
 for row in rows:
  t=time.perf_counter();r=json.loads(json.dumps(row));choice=fn(r);output=pack(choice,r,0);validate(output,r);ms=(time.perf_counter()-t)*1000;output=pack(choice,r,ms)
  results.append(dict(id=r['id'],**output,ms=ms,validity=validate(output,r)))
 baselines[name]=results
for row in rows:
 t=time.perf_counter();r=json.loads(json.dumps(row));ids,truncated=nano_ids(tok,r['state'],r['question'],r['choices'],2048)
 x=np.array([ids],dtype=np.int64)
 logits=session.run(['logits'],{'input_ids':x,'attention_mask':np.ones_like(x)})[0][0]
 z=logits[x[0]==tok.mask_token_id].astype(np.float64);assert len(z)==6
 p=np.exp(z-z.max());p/=p.sum();assert np.isfinite(p).all()
 probs=dict(zip(r['choices'],map(float,p)));choice=max(probs,key=probs.get)
 output=pack(choice,r,0);validate(output,r);ms=(time.perf_counter()-t)*1000;output=pack(choice,r,ms)
 pred.append(dict(id=r['id'],**output,ms=ms,validity=validate(output,r),probabilities=probs,tokens=len(ids),truncated=truncated))
 print('DONE',r['id'],round(ms,2),file=sys.stderr,flush=True)
print(json.dumps(dict(model='manjunathshiva/opendecider-nano-ONNX',revision='5137ae7b4f1cf8c55f1d05d3f5cdd3694d92da3e',runtime=ort.__version__,providers=session.get_providers(),threads=2,import_ms=(import_end-start)*1000,load_ms=(loaded-import_end)*1000,cold_start_to_first_usable_output_ms=(loaded-start)*1000+pred[0]['ms'],first_request_ms=pred[0]['ms'],warm=stats([r['ms'] for r in pred[1:]]),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,cpu_seconds=time.process_time(),predictions=pred,baselines=baselines)))
