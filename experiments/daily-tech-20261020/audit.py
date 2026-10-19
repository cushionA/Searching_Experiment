"""Offline evidence integrity audit; not a candidate benchmark."""
import hashlib,json,pathlib,platform,time
root=pathlib.Path(__file__).resolve().parent
start=time.perf_counter(); manifest=json.loads((root/'manifest.json').read_text());ok=[]
for row in manifest:
 if row['status']!='ok':continue
 b=(root/row['file']).read_bytes()
 assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'],row['file']
 ok.append(row)
pkg=json.loads((root/'sources/selectolax-pypi.json').read_text())
wheel=[{'filename':r['filename'],'bytes':r['size'],'sha256':r['digests']['sha256']} for r in pkg['urls'] if 'cp312-cp312-manylinux' in r['filename'] and 'x86_64' in r['filename']]
assert len(wheel)==1
out={'python':platform.python_version(),'source_attempts':len(manifest),'verified_payloads':len(ok),'payload_bytes':sum(r['bytes'] for r in ok),'fetch_wall_seconds_sum':sum(r['seconds'] for r in manifest),'offline_audit_seconds':time.perf_counter()-start,'errors':[r for r in manifest if r['status']!='ok'],'selectolax_cp312_linux_wheel':wheel,'candidate_executions':0,'model_inferences':0,'performance_trials':0,'candidate_repairs':0,'model_dependency_download_bytes':0,'gpu_seconds':0,'spend_jpy':0,'model_runtime_verified':False,'wire_bytes':'unmeasured; git/search/headers excluded'}
target=root.parents[1]/'benchmarks/daily-tech-20261020/audit.json';target.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
