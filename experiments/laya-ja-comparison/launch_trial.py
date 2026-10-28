import pathlib,subprocess,tarfile,time,json,hashlib
p=pathlib.Path('/tmp/laya-trial')
image='python:3.12-slim@sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07'
boot="""import tarfile,sys,subprocess,os,json,time
t=time.monotonic()
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as a:a.extractall('/work',filter='data')
os.mkdir('/work/pip-tmp');os.environ['TMPDIR']='/work/pip-tmp'
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links=/work/wheels','--target=/work/site','--no-cache-dir','torch==2.7.1+cpu','transformers==5.17.0','laya==0.4.17','numpy==2.2.6'],check=True,stdout=sys.stderr,timeout=180)
env=dict(os.environ,PYTHONPATH='/work/site',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
r=subprocess.run([sys.executable,'/work/run_model.py'],env=env,capture_output=True,text=True,timeout=600)
sys.stderr.write(r.stderr);sys.stdout.write(r.stdout);sys.stderr.write('\\nSETUP_AND_RUN_SECONDS='+str(time.monotonic()-t)+'\\n');sys.exit(r.returncode)
"""
cmd=['docker','run','--rm','--name','laya-ja-20261029','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','8g','--cpus','2','--pids-limit','128','--user','65534:65534','--tmpfs','/work:rw,exec,size=6g,mode=1777','--tmpfs','/tmp:rw,size=256m,mode=1777','-i',image,'python','-c',boot]
files=[*sorted((p/'wheels').glob('*.whl')),*sorted(f for f in (p/'model').rglob('*') if f.is_file()),p/'inputs.json',p/'run_model.py']
manifest=[{'file':str(f.relative_to(p)),'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in files]
assert sum(x['bytes'] for x in manifest)<1_900_000_000
(p/'trial-files.json').write_text(json.dumps(manifest,indent=2));(p/'trial-command.json').write_text(json.dumps(cmd,indent=2))
t=time.monotonic()
with open(p/'model-result.json','wb') as out,open(p/'model-stderr.log','wb') as err:
 proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=out,stderr=err)
 try:
  with tarfile.open(fileobj=proc.stdin,mode='w|') as a:
   for f in files:a.add(f,arcname=str(f.relative_to(p)),recursive=False)
  proc.stdin.close();rc=proc.wait(timeout=900)
 except Exception:
  subprocess.run(['docker','stop','-t','1','laya-ja-20261029'],capture_output=True);raise
(p/'trial-exit.json').write_text(json.dumps({'exit_code':rc,'wall_seconds':time.monotonic()-t,'input_files_bytes':sum(x['bytes'] for x in manifest),'candidate_repairs':0},indent=2));print('exit',rc,'seconds',time.monotonic()-t)
