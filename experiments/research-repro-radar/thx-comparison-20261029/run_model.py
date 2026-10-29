import os,time,json,resource,platform
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
started=time.perf_counter()
import torch,transformers,thx01
torch.set_num_threads(2);torch.set_num_interop_threads(1)
load=time.perf_counter();agent=thx01.load('/work/model',device='cpu',half=False);loaded=time.perf_counter()
out=[]
for row in json.load(open('/work/inputs.json')):
 t=time.perf_counter()
 result=agent.decide(row['state'],{'q':{'type':'choice','question':row['question'],'criteria':dict(row['choices'])}})
 answer=result['answers']['q']
 out.append({'id':row['id'],'prediction':answer['choice'],'probabilities':answer['probabilities'],'seconds':time.perf_counter()-t,'raw':result})
print(json.dumps({'status':'complete','model':'doofz/THX-01','revision':'b323afcef149f9288a9d0daef4badd67b9b87c61','package':'thx01==1.0.0','torch':torch.__version__,'transformers':transformers.__version__,'threads':2,'attention':'sdpa (official package)','device':'cpu','load_seconds':loaded-load,'import_seconds':load-started,'inference_seconds':time.perf_counter()-loaded,'cpu_seconds':time.process_time(),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'predictions':out},ensure_ascii=False))
