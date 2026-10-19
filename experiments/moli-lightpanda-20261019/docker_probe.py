"""Harmless probe: checks only its own files, process and isolation controls."""
import json,os,pathlib,socket
p=pathlib.Path
status=dict(line.split(':',1) for line in p('/proc/self/status').read_text().splitlines() if ':' in line)
files=['/work/docker_probe.py','/work/expected.json','/work/fixtures/static.html']
result={'uid':os.getuid(),'gid':os.getgid(),'cwd':os.getcwd(),'cwd_read_execute':os.access('.',os.R_OK|os.X_OK),'files_readable':{f:os.access(f,os.R_OK) for f in files},'cap_eff':status['CapEff'].strip(),'cap_bnd':status['CapBnd'].strip(),'no_new_privs':status['NoNewPrivs'].strip(),'seccomp':status['Seccomp'].strip(),'interfaces':sorted(p('/sys/class/net').iterdir(),key=str),'memory_max':p('/sys/fs/cgroup/memory.max').read_text().strip(),'cpu_max':p('/sys/fs/cgroup/cpu.max').read_text().strip()}
result['interfaces']=[f.name for f in result['interfaces']]
mounts=p('/proc/mounts').read_text().splitlines()
result['mount_flags']={line.split()[1]:line.split()[3] for line in mounts if line.split()[1] in ['/','/work','/tmp']}
assert result['uid']==1000 and result['gid']==1000
assert result['cwd_read_execute'] and all(result['files_readable'].values())
assert int(result['cap_eff'],16)==0 and int(result['cap_bnd'],16)==0
assert result['no_new_privs']=='1' and result['seccomp']=='2'
assert result['interfaces']==['lo']
assert result['memory_max']=='8589934592' and result['cpu_max']=='200000 100000'
assert 'ro' in result['mount_flags']['/'].split(',') and 'ro' in result['mount_flags']['/work'].split(',')
with socket.socket() as s:s.bind(('127.0.0.1',0));result['loopback_bind']=True
print(json.dumps(result,indent=2))
