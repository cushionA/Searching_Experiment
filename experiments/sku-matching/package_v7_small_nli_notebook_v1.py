#!/usr/bin/env python3
"""Package a new private Kaggle notebook; no upload or model execution."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from restore_session_artifacts_20261014 import sha

BOOTSTRAP = r'''
import hashlib, json, pathlib, shutil, stat, subprocess, sys, tempfile, time, zipfile
CONFIG = json.loads(__CONFIG__)
def sha(path):
    h=hashlib.sha256()
    with pathlib.Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
started=time.perf_counter()
print(json.dumps({'stage':'input-start','kernel_ref':CONFIG['kernel_ref']}),flush=True)
target=pathlib.Path(tempfile.mkdtemp(prefix='v7-nli-single-'))/'bundle'
mount=pathlib.Path('/kaggle/input')
archives=list(mount.rglob('v7-small-nli-single-inputs.zip'))
markers=[p for p in mount.rglob('BUNDLE_MANIFEST.json') if sha(p)==CONFIG['bundle_manifest_sha256']]
if len(archives)==1 and not markers:
    archive=archives[0]
    if sha(archive)!=CONFIG['archive_sha256'] or archive.stat().st_size!=CONFIG['archive_bytes']:
        raise ValueError('mounted private input ZIP differs')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or z.testzip() is not None: raise ValueError('invalid ZIP inventory/CRC')
        for entry in z.infolist():
            p=pathlib.PurePosixPath(entry.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in entry.filename or stat.S_ISLNK(entry.external_attr>>16):
                raise ValueError('unsafe private archive path')
        target.mkdir();z.extractall(target)
elif len(markers)==1 and not archives:
    marker=markers[0];manifest=json.loads(marker.read_text());target.mkdir()
    for name in [*manifest['files'],'BUNDLE_MANIFEST.json']:
        p=pathlib.PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or '\\' in name: raise ValueError('unsafe expanded input path')
        src=marker.parent/name
        if src.is_symlink() or not src.resolve().is_relative_to(marker.parent.resolve()):
            raise ValueError('unsafe expanded input member')
        dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
else: raise ValueError('ambiguous or missing private input mount')
if sha(target/'BUNDLE_MANIFEST.json')!=CONFIG['bundle_manifest_sha256']:
    raise ValueError('mounted manifest identity differs')
print(json.dumps({'stage':'dependencies-start','python':sys.version}),flush=True)
subprocess.run([sys.executable,'-m','pip','install','-q','transformers==5.17.0','tokenizers==0.23.3',
    'huggingface-hub==1.33.0','safetensors==0.8.0','fugashi==1.5.2','unidic-lite==1.0.8'],check=True)
sys.path.insert(0,str(target/'code'))
import kaggle_full_condition_choices_v1 as GPU
verified=GPU.verify_bundle(target)
if verified['manifest_sha256']!=CONFIG['bundle_manifest_sha256']:raise ValueError('worker bundle differs')
if sys.version_info<(3,12):raise ValueError('Python 3.12+ required')
output=pathlib.Path('/kaggle/working/nli-single-results')
print(json.dumps({'stage':'nli-single-start','max_seconds':6000}),flush=True)
returncode=None;failure=None
try:
    process=subprocess.run([sys.executable,'-B',str(target/'code/kaggle_v7_small_nli_single_v1.py'),
        '--bundle-dir',str(target),'--output-dir',str(output)],timeout=6000)
    returncode=process.returncode
except subprocess.TimeoutExpired:
    failure='worker_timeout';returncode=124
files={p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in output.glob('*') if p.is_file()}
archive=pathlib.Path('/kaggle/working/v7-small-nli-single-results.zip')
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
    for p in output.glob('*'):
        if p.is_file():z.write(p,p.name)
receipt={'schema':'v7-small-nli-single-notebook-receipt-v1','kernel_ref':CONFIG['kernel_ref'],
    'bundle_manifest_sha256':verified['manifest_sha256'],'returncode':returncode,'failure':failure,
    'expected_context_count':CONFIG['context_count'],'files':files,
    'result_archive_bytes':archive.stat().st_size,'result_archive_sha256':sha(archive),
    'notebook_wall_seconds':time.perf_counter()-started,'production_adoption_allowed':False}
pathlib.Path('/kaggle/working/NOTEBOOK_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'stage':'nli-single-finished',**receipt}),flush=True)
if returncode:raise RuntimeError('worker did not complete; partial first outputs preserved')
'''


def package(experiment):
    freeze=json.loads((experiment/'HOST_INPUT_FREEZE.json').read_text())
    archive=experiment/'dataset-upload/v7-small-nli-single-inputs.zip'
    if sha(archive)!=freeze['archive_sha256']:raise ValueError('local public payload changed')
    config={k:freeze[k] for k in ['kernel_ref','context_count','bundle_manifest_sha256','archive_sha256','archive_bytes']}
    source=BOOTSTRAP.replace('__CONFIG__',repr(json.dumps(config)))
    folder=experiment/'notebook-upload';folder.mkdir(exist_ok=False)
    def write(name,value):
        with (folder/name).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    write('nli_single.ipynb',{'nbformat':4,'nbformat_minor':5,
        'metadata':{'kernelspec':{'name':'python3','display_name':'Python 3','language':'python'},
                    'language_info':{'name':'python'}},'cells':[{'id':'v7-nli-single-bootstrap',
         'cell_type':'code','execution_count':None,'metadata':{},'outputs':[],'source':source.splitlines(keepends=True)}]})
    write('kernel-metadata.json',{'id':freeze['kernel_ref'],'title':freeze['kernel_ref'].split('/')[1],
        'code_file':'nli_single.ipynb','language':'python','kernel_type':'notebook','is_private':True,
        'enable_gpu':True,'enable_tpu':False,'enable_internet':True,
        'dataset_sources':[freeze['dataset_ref']],'competition_sources':[],'kernel_sources':[]})
    write('training-params.json',{'timeout_seconds':7200})
    compile(source,'kaggle-notebook-bootstrap','exec')
    print(json.dumps({'notebook_dir':str(folder),'notebook_sha256':sha(folder/'nli_single.ipynb'),
        'dataset_ref':freeze['dataset_ref'],'kernel_ref':freeze['kernel_ref'],'submitted':False}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--experiment',type=Path,required=True)
    package(p.parse_args().experiment)
