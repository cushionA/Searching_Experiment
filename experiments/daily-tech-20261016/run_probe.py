"""Transfer only reviewed public modules and synthetic inputs into a bounded sandbox."""
import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/daily-tech-20261016'
OUT = ROOT / 'benchmarks/daily-tech-20261016'
IMAGE = 'python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1'
FILES = {
    'probe.py': EXP / 'probe.py',
    'protocol.json': EXP / 'protocol.json',
    'grounding.py': OUT / 'sources/lightonocr_grounding.py.txt',
    'models.py': OUT / 'sources/lightonocr_models.py.txt',
}
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w') as tar:
    for name, path in FILES.items():
        data = path.read_bytes()
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o444
        tar.addfile(info, io.BytesIO(data))
command = ['docker', 'run', '--rm', '-i', '--network=none', '--read-only', '--user=1000:1000',
           '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=32',
           '--cpus=1', '--memory=256m', '--memory-swap=256m', '--tmpfs=/input:rw,noexec,nosuid,size=16m,mode=1777',
           '--workdir=/input', '--env=PYTHONDONTWRITEBYTECODE=1', IMAGE,
           'sh', '-c', 'tar --no-same-owner -xf - && python -B probe.py']
started = datetime.datetime.now(datetime.UTC).isoformat()
t = time.perf_counter()
result = subprocess.run(command, input=archive.getvalue(), capture_output=True, timeout=30)
(OUT / 'probe.json').write_bytes(result.stdout)
(OUT / 'probe.stderr').write_bytes(result.stderr)
(OUT / 'execution.json').write_text(json.dumps({
    'started_utc': started, 'command': command, 'exit_code': result.returncode,
    'wall_seconds_including_container': time.perf_counter()-t,
    'input_sha256': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in FILES.items()},
    'archive_bytes': len(archive.getvalue()), 'repairs': 0,
    'candidate_execution_network': False, 'host_mounts': [], 'credentials_passed': False,
}, indent=2)+'\n')
print('exit', result.returncode)
if result.returncode:
    print(result.stderr.decode())
else:
    parsed = json.loads(result.stdout)
    print(json.dumps({k: [sum(x['pass'] for x in parsed[k]), len(parsed[k])] for k in ['parser', 'models']}))
raise SystemExit(result.returncode)
