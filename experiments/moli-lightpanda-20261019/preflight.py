"""Run only a self-authored harmless namespace probe. Never fall back."""
import json, subprocess, sys, time
from pathlib import Path
CODE='import os; print({"uid":os.getuid(),"cwd":os.getcwd(),"readable":os.access(".",os.R_OK),"executable":os.access(".",os.X_OK)})'
COMMAND=['bwrap','--unshare-all','--die-with-parent','--ro-bind','/usr','/usr','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/work','--chdir','/work','--clearenv','--setenv','PATH','/usr/bin:/bin','/usr/bin/python3','-I','-c',CODE]
if __name__=='__main__':
 if len(sys.argv)!=2: raise SystemExit('Usage: python3 preflight.py NEW_RESULT.json')
 out=Path(sys.argv[1])
 with out.open('x') as f:
  start=time.monotonic()
  try:
   p=subprocess.run(COMMAND,capture_output=True,text=True,timeout=10)
   result=dict(command=COMMAND,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
  except (OSError,subprocess.TimeoutExpired) as e:
   result=dict(command=COMMAND,returncode=None,error=str(e))
  result['duration_seconds']=time.monotonic()-start
  result['status']='probe_passed_not_full_sandbox_validation' if result['returncode']==0 else 'blocked'
  json.dump(result,f,indent=2); f.write('\n')
 print(result['status']); raise SystemExit(0 if result['returncode']==0 else 1)
