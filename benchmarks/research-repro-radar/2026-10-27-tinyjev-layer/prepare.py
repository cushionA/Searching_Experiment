import ctypes,json,os,pathlib,subprocess,sys,tarfile,tempfile
# Verify executable image library and writable, separate temporary area first.
ctypes.CDLL('/usr/local/lib/libpython3.12.so.1.0')
os.environ.update(TMPDIR='/work/tmp',PIP_CACHE_DIR='/work/cache',PYTHONDONTWRITEBYTECODE='1')
for p in ('/work/tmp','/work/cache','/work/deps'):pathlib.Path(p).mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryFile() as f:f.write(b'preflight')
print(json.dumps({'preflight':'image library load and temp write passed','uid':os.getuid(),'mountinfo':pathlib.Path('/proc/self/mountinfo').read_text()}),file=sys.stderr)
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links','/wheels','--target','/work/deps','torch','transformers','tokenizers','safetensors','numpy','huggingface_hub'],stdout=sys.stderr,check=True)
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as t:t.add('/work/deps',arcname='deps')
