"""Run official deterministic demo in a credential-free offline container.
No model inference; timings describe only the scripted workflow.
"""
import json,os,platform,resource,runpy,sys,time
from pathlib import Path
out=Path('/out');start=time.perf_counter();before=resource.getrusage(resource.RUSAGE_CHILDREN)
assert os.getuid()==10001
assert not any(Path(p).exists() for p in ['/workspace','/home/agent','/root/.codex','/root/.aws'])
sys.path.insert(0,'/app');sys.argv=['harness2-demo','--output','/out/demo','--k','1','--mode','both']
exit_code=0
try:runpy.run_module('harness2.cli.demo',run_name='__main__')
except SystemExit as e:exit_code=int(e.code or 0)
except Exception as e:exit_code=1;print(type(e).__name__+': '+str(e),file=sys.stderr)
self_usage=resource.getrusage(resource.RUSAGE_SELF);child=resource.getrusage(resource.RUSAGE_CHILDREN)
result={'verification':'scripted_official_demo_not_model_inference','exit_code':exit_code,'wall_seconds':time.perf_counter()-start,'cpu_self_seconds':self_usage.ru_utime+self_usage.ru_stime,'cpu_children_seconds':child.ru_utime+child.ru_stime-before.ru_utime-before.ru_stime,'self_maxrss_kib':self_usage.ru_maxrss,'max_child_rss_kib':child.ru_maxrss,'python':platform.python_version(),'platform':platform.platform(),'uid':os.getuid(),'model_calls':0,'repairs':1,'gpu_used':False}
p=Path('/sys/fs/cgroup/memory.peak');result['cgroup_memory_peak_bytes']=int(p.read_text())if p.exists()else None
(out/'execution.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));raise SystemExit(exit_code)
