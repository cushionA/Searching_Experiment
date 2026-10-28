import os,time,json,resource,sys
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
start=time.perf_counter()
import torch,transformers
from transformers import AutoTokenizer,AutoModelForSequenceClassification
torch.set_num_threads(2);torch.set_num_interop_threads(1)
load=time.perf_counter();tok=AutoTokenizer.from_pretrained('/work/model',local_files_only=True,trust_remote_code=False)
model=AutoModelForSequenceClassification.from_pretrained('/work/model',local_files_only=True,trust_remote_code=False,attn_implementation='eager').eval()
loaded=time.perf_counter();out=[]
for row in json.load(open('/work/inputs.json')):
 t=time.perf_counter();context=f"質問: {row['question']}\n状況: {row['state']}";cs=row['choices']
 enc=tok([context]*len(cs),[c[1] for c in cs],padding=True,truncation='only_first',max_length=512,return_tensors='pt')
 with torch.inference_mode(): logits=model(**enc).logits[:,0];probs=logits.softmax(0).tolist()
 out.append({'id':row['id'],'prediction':cs[max(range(len(probs)),key=lambda i:probs[i])][0],'probabilities':probs,'choice_ids':[c[0] for c in cs],'seconds':time.perf_counter()-t,'tokens':enc['input_ids'].shape[1]})
print(json.dumps({'status':'complete','model':'argos1111/modernbert-ja-310m-jev','revision':'07cda23579443e7a33c0f474114279fa032340d6','torch':torch.__version__,'transformers':transformers.__version__,'threads':2,'load_seconds':loaded-load,'import_seconds':load-start,'inference_seconds':time.perf_counter()-loaded,'cpu_seconds':time.process_time(),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'predictions':out},ensure_ascii=False))
