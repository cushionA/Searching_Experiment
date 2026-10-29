import pathlib,subprocess,tarfile,time,json,hashlib
src=pathlib.Path(__file__).parent;p=pathlib.Path('/tmp/tactical-trial');out=pathlib.Path('benchmarks/tactical-intent-20261030')
image='python:3.12-slim@sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07'
boot="""import tarfile,sys,subprocess,os
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as a:a.extractall('/work',filter='data')
os.mkdir('/work/pip-tmp');os.environ['TMPDIR']='/work/pip-tmp'
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links=/work/wheels','--target=/work/site','--no-cache-dir','onnxruntime==1.30.0','transformers==5.18.0','numpy==2.2.6'],check=True,stdout=sys.stderr,timeout=180)
env=dict(os.environ,PYTHONPATH='/work/site',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
r=subprocess.run([sys.executable,'/work/run_model.py'],env=env,timeout=1100);sys.exit(r.returncode)
"""
cmd=['docker','run','--rm','--name','tactics-20261030','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','8g','--cpus','2','--pids-limit','128','--user','65534:65534','--tmpfs','/work:rw,exec,size=4g,mode=1777','--tmpfs','/tmp:rw,size=256m,mode=1777','-i',image,'python','-c',boot]
files=[(f,'wheels/'+f.name) for f in sorted((p/'wheels').glob('*.whl'))]+[(f,'model/'+f.name) for f in sorted((p/'model').glob('*'))]+[(src/n,n) for n in ['run_model.py','inputs.json','bench.py','vendor_prompt.py']]
manifest=[dict(file=n,bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f,n in files]
assert sum(x['bytes'] for x in manifest)<1_900_000_000
(out/'trial-files.json').write_text(json.dumps(manifest,indent=2));(out/'trial-command.json').write_text(json.dumps(cmd,indent=2));start=time.monotonic()
with (out/'model-result.json').open('wb') as stdout,(out/'model-stderr.log').open('wb') as stderr:
 proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr)
 try:
  with tarfile.open(fileobj=proc.stdin,mode='w|') as a:
   for f,n in files:a.add(f,arcname=n,recursive=False)
  proc.stdin.close();rc=proc.wait(timeout=1200)
 except Exception:
  subprocess.run(['docker','stop','-t','1','tactics-20261030'],capture_output=True);raise
(out/'trial-exit.json').write_text(json.dumps(dict(exit_code=rc,wall_seconds=time.monotonic()-start,files_bytes=sum(x['bytes'] for x in manifest),repairs=0),indent=2));print('exit',rc,'wall',time.monotonic()-start)
