#!/usr/bin/env python3
import hashlib,json,resource,subprocess,sys,time
mode,trial=sys.argv[1],int(sys.argv[2]);exe='/tmp/docling215/docling-rs';pdf='benchmarks/daily-tech-20261012/synthetic-200page-text.pdf'
cmd=[exe,'--text-layer-only']+(['--no-stream'] if mode=='buffer' else [])+[pdf]
t0=time.perf_counter();p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
first=p.stdout.read(1);first_s=time.perf_counter()-t0
rest=p.stdout.read();stderr=p.stderr.read();rc=p.wait();wall=time.perf_counter()-t0;data=first+rest
r=resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps({'trial':trial,'mode':mode,'returncode':rc,'first_byte_s':first_s,'wall_s':wall,'stdout_bytes':len(data),'stdout_sha256':hashlib.sha256(data).hexdigest(),'stderr':stderr.decode('utf-8','replace')[:1200],'child_peak_rss_kib':r.ru_maxrss}))
