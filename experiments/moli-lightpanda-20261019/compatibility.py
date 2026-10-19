import subprocess,json
for exe in ['moli','lightpanda']:
 for args in [['--version'],['serve','--help']]:
  try:
   p=subprocess.run(['/work/'+exe]+args,text=True,capture_output=True,timeout=10)
   print(json.dumps({'engine':exe,'args':args,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}),flush=True)
  except Exception as e:print(json.dumps({'engine':exe,'args':args,'error':str(e)}),flush=True)
