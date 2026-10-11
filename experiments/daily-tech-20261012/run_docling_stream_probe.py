#!/usr/bin/env python3
import json,subprocess,sys
modes=['stream','buffer','buffer','stream','stream','buffer']
rows=[]
for n,mode in enumerate(modes,1):
 cp=subprocess.run([sys.executable,'-B','experiments/daily-tech-20261012/probe_one.py',mode,str(n)],capture_output=True,text=True)
 if cp.returncode: raise SystemExit(cp.stderr or cp.stdout)
 rows.append(json.loads(cp.stdout))
 if rows[-1]['returncode']!=0: break
out='benchmarks/daily-tech-20261012/docling-probe-results.json'
open(out,'w').write(json.dumps(rows,indent=2)+'\n')
print(json.dumps(rows,indent=2))
