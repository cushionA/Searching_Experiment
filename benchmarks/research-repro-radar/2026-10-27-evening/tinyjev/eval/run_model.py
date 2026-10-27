import json,time,resource,sys,traceback,os,platform
from pathlib import Path
import torch
torch.set_num_threads(2)
torch.set_num_interop_threads(1)
import tinyjev
out=Path('/output');start=time.perf_counter(); meta={'python':sys.version,'torch':torch.__version__,'tinyjev':tinyjev.__version__,'platform':platform.platform(),'threads':2,'device':'cpu','dtype':'float32','network':'none','training':False}
try:
 agent=tinyjev.load('/model',backend='torch',device='cpu');meta['load_seconds']=time.perf_counter()-start
 items=json.loads(Path('/eval/inputs.json').read_text())
 for item in items:
  t=time.perf_counter();res=agent.predict({'id':item['id'],'state':item['state'],'questions':item['questions']})
  row={'id':item['id'],'wall_seconds':time.perf_counter()-t,'result':res,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
  with (out/'predictions.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
 meta['status']='complete';meta['count']=len(items)
except Exception as e:
 meta.update(status='error',error_type=type(e).__name__,error=str(e));traceback.print_exc()
finally:
 meta.update(total_seconds=time.perf_counter()-start,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
 (out/'runtime.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta))
if meta['status']!='complete':sys.exit(1)
