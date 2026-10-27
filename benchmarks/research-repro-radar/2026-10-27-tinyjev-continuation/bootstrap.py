"""One approved offline dependency repair; run the frozen model harness unchanged."""
import json, os, pathlib, resource, subprocess, sys, time
start=time.monotonic()
for p in ('/work/deps','/work/tmp','/work/cache'):
 pathlib.Path(p).mkdir(parents=True,exist_ok=True)
os.environ.update(PYTHONPATH='/work/deps:/src',TMPDIR='/work/tmp',PIP_CACHE_DIR='/work/cache',HF_HOME='/work/cache/hf',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1')
def limit_cpu(): resource.setrlimit(resource.RLIMIT_CPU,(1200,1200))
steps=[]
commands=[
 ('install',[sys.executable,'-m','pip','install','--no-index','--find-links','/wheels','--target','/work/deps','torch','transformers','tokenizers','safetensors','numpy','huggingface_hub']),
 ('import',[sys.executable,'-c','import torch,transformers,tinyjev; print(torch.__version__,transformers.__version__,tinyjev.__version__)']),
 ('trial',[sys.executable,'/eval/run_model.py'])]
for name,cmd in commands:
 t=time.monotonic()
 try:
  result=subprocess.run(cmd,capture_output=True,text=True,timeout=max(1,1200-(time.monotonic()-start)),preexec_fn=limit_cpu)
  row={'stage':name,'returncode':result.returncode,'seconds':time.monotonic()-t,'stdout':result.stdout,'stderr':result.stderr}
 except subprocess.TimeoutExpired as e:
  row={'stage':name,'returncode':None,'seconds':time.monotonic()-t,'error':'timeout'}
 steps.append(row)
 if row.get('returncode')!=0:break
files={p.name:p.read_text()for p in pathlib.Path('/output').glob('*')if p.is_file()}
usage=resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps({'steps':steps,'files':files,'elapsed_seconds':time.monotonic()-start,'child_cpu_seconds':usage.ru_utime+usage.ru_stime,'child_peak_rss_kib':usage.ru_maxrss,'uid':os.getuid(),'gid':os.getgid()}),flush=True)
sys.exit(0 if len(steps)==3 and steps[-1]['returncode']==0 else 1)
