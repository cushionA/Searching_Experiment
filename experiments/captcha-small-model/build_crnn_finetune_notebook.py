#!/usr/bin/env python3
"""Build a small private-Kaggle notebook for the bounded CRNN adaptation pilot."""
from __future__ import annotations

import argparse
import ast
import base64
import gzip
import hashlib
import json
from pathlib import Path
import zipfile

from build_public_eval_notebook import cell
from split_crnn_text import sha256_file

SCRIPTS = ('finetune_crnn_text.py', 'split_crnn_text.py', 'evaluate_crnn_text.py',
           'evaluate_public_text.py', 'subprocess_runner.py')


def build(manifest: Path, split: Path, bundle: Path, dataset_ref: str, output: Path,
          max_epochs: int = 20, max_seconds: int = 1200) -> None:
    if output.exists():
        raise FileExistsError(output)
    if len(dataset_ref.split('/')) != 2 or not all(dataset_ref.split('/')):
        raise ValueError('dataset-ref must be owner/slug')
    if not 1 <= max_epochs <= 100 or not 1 <= max_seconds <= 1200:
        raise ValueError('training bounds are 1..100 epochs and 1..1200 seconds')
    with zipfile.ZipFile(bundle) as archive:
        if archive.read('manifest.json') != manifest.read_bytes() or archive.read('frozen-split.json') != split.read_bytes():
            raise ValueError('bundle manifest or split differs from the pinned inputs')
    root = Path(__file__).parent
    scripts = {name: (root/name).read_text() for name in SCRIPTS}
    payload = {'dataset_ref': dataset_ref, 'manifest_sha256': sha256_file(manifest),
               'split_sha256': sha256_file(split), 'bundle_sha256': sha256_file(bundle),
               'scripts': scripts, 'script_sha256': {name: hashlib.sha256(code.encode()).hexdigest() for name, code in scripts.items()}}
    packed = base64.b64encode(gzip.compress(json.dumps(payload).encode(), mtime=0)).decode()
    setup = '''import base64,gzip,hashlib,json,pathlib,platform,shutil,stat,subprocess,sys,time,zipfile
payload=json.loads(gzip.decompress(base64.b64decode(PACKED)))
work=pathlib.Path('/kaggle/working')
root=pathlib.Path('/tmp/crnn-training-data')
scripts=work/'ocr-training-scripts'
if root.exists() or scripts.exists(): raise FileExistsError('scratch/source paths already exist')
root.mkdir();scripts.mkdir()
for name,code in payload['scripts'].items():
    raw=code.encode()
    if hashlib.sha256(raw).hexdigest()!=payload['script_sha256'][name]: raise ValueError('source hash mismatch')
    (scripts/name).write_bytes(raw)
owner,slug=payload['dataset_ref'].split('/')
mount=pathlib.Path('/kaggle/input')
candidates=[p/'evaluation_bundle.bin' for p in (mount/slug,mount/'datasets'/owner/slug)]
archives=[p for p in candidates if p.is_file()]
if len(archives)!=1: raise FileNotFoundError('expected one opaque input bundle')
archive=archives[0]
h=hashlib.sha256()
with archive.open('rb') as stream:
    for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
if h.hexdigest()!=payload['bundle_sha256']: raise ValueError('input bundle hash mismatch')
print(json.dumps({'stage':'input_verified','bytes':archive.stat().st_size}),flush=True)
with zipfile.ZipFile(archive) as bundle:
    seen=set()
    for item in bundle.infolist():
        relative=pathlib.PurePosixPath(item.filename)
        if relative.is_absolute() or '..' in relative.parts or chr(92) in item.filename or item.filename in seen:
            raise ValueError('unsafe or duplicate ZIP member')
        seen.add(item.filename)
        if stat.S_ISLNK(item.external_attr>>16) or item.is_dir(): raise ValueError('unsupported ZIP member')
        target=root/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        with bundle.open(item) as src,target.open('xb') as dst: shutil.copyfileobj(src,dst)
for name,key in [('manifest.json','manifest_sha256'),('frozen-split.json','split_sha256')]:
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=payload[key]: raise ValueError(name+' hash mismatch')
import torch,torchvision,safetensors
if not torch.cuda.is_available(): raise RuntimeError('GPU required for this submitted training job')
environment={'python':sys.version,'platform':platform.platform(),'torch':torch.__version__,
             'torchvision':torchvision.__version__,'safetensors':safetensors.__version__,
             'cuda_runtime':torch.version.cuda,'cuda_available':True,'gpu':torch.cuda.get_device_name(0),
             'input_manifest_sha256':payload['manifest_sha256'],'split_sha256':payload['split_sha256'],
             'bundle_sha256':payload['bundle_sha256'],'script_sha256':payload['script_sha256']}
(work/'environment.json').write_text(json.dumps(environment,indent=2)+chr(10))
print(json.dumps(environment),flush=True)
'''.replace('PACKED', repr(packed))
    run = '''sys.path.insert(0,str(scripts))
from subprocess_runner import run_logged_process
command=[sys.executable,'-B',str(scripts/'finetune_crnn_text.py'),
         '--manifest',str(root/'manifest.json'),'--split',str(root/'frozen-split.json'),
         '--model-dir',str(root/'models/captcha-crnn'),'--output',str(work/'crnn-finetune-result'),
         '--device','cuda','--max-epochs',MAX_EPOCHS,'--max-seconds',MAX_SECONDS]
result=run_logged_process(command,work/'training.log',1800)
(work/'process-result.json').write_text(json.dumps(result,indent=2)+chr(10))
print(json.dumps(result),flush=True)
if result.get('returncode')!=0 or result.get('timed_out'): raise RuntimeError('training process failed; preserve artifacts and logs')
print((work/'crnn-finetune-result/result.json').read_text(),flush=True)
'''.replace('MAX_EPOCHS', repr(str(max_epochs))).replace('MAX_SECONDS', repr(str(max_seconds)))
    notebook = {'cells': [cell('markdown', 'Bounded CRNN warm-start training. Frozen internal train/validation/test folds; validation chooses the checkpoint. Inputs are a private dataset. No live-site interaction.'),
                          cell('code', '%pip install --no-deps safetensors==0.8.0\n'), cell('code', setup), cell('code', run)],
                'metadata': {'kernelspec': {'display_name':'Python 3','language':'python','name':'python3'}},
                'nbformat':4,'nbformat_minor':5}
    for item in notebook['cells']:
        if item['cell_type']=='code':
            ast.parse('\n'.join(line for line in ''.join(item['source']).splitlines() if not line.startswith('%pip ')))
    text = json.dumps(notebook, ensure_ascii=False, indent=1)+'\n'
    if len(text.encode())>=1024*1024:
        raise ValueError('notebook exceeds the small-code payload budget')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(text)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','split','bundle','output'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--dataset-ref',required=True)
    parser.add_argument('--max-epochs',type=int,default=20)
    parser.add_argument('--max-seconds',type=int,default=1200)
    args=parser.parse_args()
    build(args.manifest,args.split,args.bundle,args.dataset_ref,args.output,args.max_epochs,args.max_seconds)


if __name__=='__main__': main()
