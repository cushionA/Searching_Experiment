"""Fixed Japanese choice trial. No gold is available inside the container."""
import os,time,json,resource
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
t0=time.perf_counter()
import torch,transformers,laya
from laya import Agent
torch.set_num_threads(2);torch.set_num_interop_threads(1)
t1=time.perf_counter();agent=Agent('/work/model',device='cpu',fast=False,compile=False);t2=time.perf_counter();out=[]
for row in json.load(open('/work/inputs.json')):
 start=time.perf_counter()
 result=agent.predict(row['state'],{'decision':{'type':'choice','instructions':row['question'],'criteria':dict(row['choices'])}},max_len=512,lang='ja')
 elapsed=time.perf_counter()-start;answer=result['answers']['decision']
 out.append({'id':row['id'],'prediction':answer['choice'],'seconds':elapsed,'raw':result})
 print('DONE '+row['id'],file=__import__('sys').stderr,flush=True)
print(json.dumps({'status':'complete','model':'convaiinnovations/laya-multilingual','revision':'1720e3e3357cfe1e281542e223f8273b0890ca34','laya':laya.__version__,'torch':torch.__version__,'transformers':transformers.__version__,'threads':2,'load_seconds':t2-t1,'import_seconds':t1-t0,'inference_seconds':time.perf_counter()-t2,'cpu_seconds':time.process_time(),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'predictions':out},ensure_ascii=False))
