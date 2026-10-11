#!/usr/bin/env python3
import hashlib,json,resource,subprocess,sys,time
mode,trial=sys.argv[1],int(sys.argv[2]);exe='/tmp/docling215/docling-rs';pdf='benchmarks/daily-tech-20261012/synthetic-200page-text.pdf'
cmd=[exe,'--text-layer-only']+(['--no-stream'] if mode=='buffer' else [])+[pdf]
t0=time.perf_counter();cp=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False);wall=time.perf_counter()-t0
r=resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps({'trial':trial,'mode':mode,'returncode':cp.returncode,'wall_s':wall,'stdout_bytes':len(cp.stdout),'stdout_sha256':hashlib.sha256(cp.stdout).hexdigest(),'stderr':cp.stderr.decode('utf-8','replace')[:1200],'child_peak_rss_kib':r.ru_maxrss,'child_user_cpu_s':r.ru_utime,'child_system_cpu_s':r.ru_stime}))
