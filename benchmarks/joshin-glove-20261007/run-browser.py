"""Run standard Firefox+4play or Camoufox+4play under the same Cloud network."""
import os
import socket
import subprocess
import sys
import urllib.parse
from pathlib import Path
root=Path('/workspace/Searching_Experiment')
mode,directory,script,*extra=sys.argv[1:]
if mode not in {'standard','assembled'}:raise SystemExit('invalid mode')
env=os.environ.copy()
env['BOT_DIAGNOSTICS_SOURCE_COMMIT']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:env.pop(key,None)
proxy=urllib.parse.urlsplit(os.environ.get('HTTPS_PROXY') or os.environ['HTTP_PROXY'])
args=['docker','--host=unix:///var/run/docker.sock','run','--rm','--name','joshin-products-'+mode,
 '--user','1000:1000','--shm-size=512m','--workdir',str(root),
 '--mount','type=bind,source=/workspace,target=/workspace',
 '--mount',f'type=bind,source={os.environ["CODEX_PROXY_CERT"]},target=/run/proxy-ca.pem,readonly',
 '--add-host',f'{proxy.hostname}:{socket.gethostbyname(proxy.hostname)}']
for key in ['HTTP_PROXY','HTTPS_PROXY','NO_PROXY','BOT_DIAGNOSTICS_SOURCE_COMMIT']:
 if key in env:args+=['-e',key]
args+=['-e','DISPLAY=:99','-e','CODEX_PROXY_CERT=/run/proxy-ca.pem','-e','NODE_EXTRA_CA_CERTS=/run/proxy-ca.pem',
 '-e','BOT_DIAGNOSTICS_FIREFOX=/usr/bin/firefox-esr',
 '-e','BOT_DIAGNOSTICS_FOURPLAY_EXTENSION=/workspace/Searching_Experiment/.deps/fourplay/ext',
 '-e','BOT_DIAGNOSTICS_CAMOUFOX_FOURPLAY_EXTENSION=/workspace/Searching_Experiment/.deps/fourplay/ext',
 '--entrypoint','sh','search-fourplay:local','-c',
 'Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/tmp/xvfb.log 2>&1 & task_display_pid=$!; '
 'node "$@"; task_result=$?; kill "$task_display_pid"; exit "$task_result"',
 'run-browser',script,directory,mode,*extra]
raise SystemExit(subprocess.call(args,env=env))
