"""Run CPU browser probe; resource.ru_maxrss is a process maximum, not summed RAM."""
import datetime,json,os,pathlib,resource,subprocess,time
root=pathlib.Path(__file__).resolve().parents[2]
start=time.perf_counter()
env=dict(os.environ,PLAYWRIGHT_BROWSERS_PATH=os.environ.get('PLAYWRIGHT_BROWSERS_PATH','/tmp/daily-tech-browsers'))
p=subprocess.run(['node','experiments/daily-tech-20261011/shadow_probe.mjs'],cwd=root,env=env,capture_output=True,text=True)
usage=resource.getrusage(resource.RUSAGE_CHILDREN)
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':p.returncode,'seconds':time.perf_counter()-start,'children_maxrss_kib':usage.ru_maxrss,'rss_note':'Linux getrusage RUSAGE_CHILDREN maximum, not simultaneous process-tree total','stdout':p.stdout,'stderr':p.stderr}
(root/'benchmarks/daily-tech-20261011/shadow-process.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps(r));raise SystemExit(p.returncode)
