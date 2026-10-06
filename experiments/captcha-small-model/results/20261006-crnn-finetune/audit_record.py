#!/usr/bin/env python3
"""Offline integrity and metric audit for this self-contained OCR record."""
from __future__ import annotations
import gzip, hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def digest(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def raw(path: Path) -> bytes: return path.read_bytes()
def unpack(path: Path) -> bytes: return gzip.decompress(raw(path))
def j(path: Path): return json.loads(raw(path))
def jgz(path: Path): return json.loads(unpack(path))
def jl_bytes(data: bytes): return [json.loads(x) for x in data.decode('utf-8').splitlines() if x.strip()]
def lev(a: str,b: str)->int:
    prev=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        cur=[i]
        for k,y in enumerate(b,1): cur.append(min(cur[-1]+1,prev[k]+1,prev[k-1]+(x!=y)))
        prev=cur
    return prev[-1]
def metrics(rows):
    n=len(rows); chars=sum(len(x['label']) for x in rows); edit=sum(lev(x['label'],x['answer']) for x in rows)
    return {'samples':n,'exact_count':sum(x['label']==x['answer'] for x in rows),
            'accuracy':sum(x['label']==x['answer'] for x in rows)/n,
            'reference_characters':chars,'edit_distance':edit,'cer':edit/chars,
            'by_source':{s:metrics_plain([x for x in rows if x['source']==s]) for s in sorted({x['source'] for x in rows})}}
def metrics_plain(rows):
    n=len(rows); chars=sum(len(x['label']) for x in rows); edit=sum(lev(x['label'],x['answer']) for x in rows)
    return {'samples':n,'exact_count':sum(x['label']==x['answer'] for x in rows),
            'accuracy':sum(x['label']==x['answer'] for x in rows)/n,
            'reference_characters':chars,'edit_distance':edit,'cer':edit/chars}
def check_reported(got,want,tag):
    for a,b in [('samples','samples'),('exact_count','exact_count'),('reference_characters','reference_characters'),('edit_distance','edit_distance')]:
        assert got[a]==want[b],(tag,a,got[a],want[b])
    assert abs(got['accuracy']-want['exact_match'])<1e-12,(tag,'accuracy')
    assert abs(got['cer']-want['character_error_rate'])<1e-12,(tag,'cer')
    if 'by_source' in got:
        for source,sub in got['by_source'].items(): check_reported(sub,want['by_source'][source],tag+'/'+source)

def check_rows(path: Path, expected, tag):
    rows=jl_bytes(unpack(path)); assert len(rows)==len(expected),(tag,len(rows),len(expected))
    assert [r['id'] for r in rows]==[r['id'] for r in expected],tag+' IDs/order'
    for r,e in zip(rows,expected):
        for k in ('id','source','sha256','label'): assert r[k]==e[k],(tag,k,r.get('id'))
        assert isinstance(r['answer'],str)
        assert r.get('exact')==(r['label']==r['answer']),(tag,'raw exact',r['id'])
        assert r.get('distance')==lev(r['label'],r['answer']),(tag,'distance',r['id'])
    return rows,metrics(rows)

record=j(ROOT/'record.json'); manifest=jgz(ROOT/'data/input-manifest.json.gz'); split=jgz(ROOT/'data/split.json.gz')
samples=manifest['samples']; assignment=split['assignments']
byid={s['id']:s for s in samples}; assert len(samples)==3070 and len(byid)==3070
fold_by_id={a['id']:a['fold'] for a in assignment}; assert len(fold_by_id)==3070 and set(fold_by_id)==set(byid)
for a in assignment:
    s=byid[a['id']]; assert (a['sha256'],a['source'])==(s['sha256'],s['source'])
folds={f:sorted((s for s in samples if fold_by_id[s['id']]==f),key=lambda s:s['id']) for f in ('train','validation','test')}
assert {k:len(v) for k,v in folds.items()}=={'train':2149,'validation':307,'test':614}
hash_fold={}
for s in samples:
    h=s['sha256']; f=fold_by_id[s['id']]
    assert h not in hash_fold or hash_fold[h]==f
    hash_fold[h]=f
assert len(hash_fold)==3070
for source,kind in [('kaggle_fournierp_captcha_version_2','alphanumeric'),('project_sloth_captcha_images_test','digits')]:
    rows=[s for s in folds['test'] if s['source']==source]
    assert all(s['label'].isalnum() for s in rows)
    assert all(s['label'].isdigit() for s in rows) if kind=='digits' else any(not s['label'].isdigit() for s in rows)

trial_dirs={'pilot':'trials/pilot-job-001','extended':'trials/extended-job-003'}
run_metrics={}
for key,relative in trial_dirs.items():
    base=ROOT/relative; result=j(base/'result/result.json')
    config=j(base/'result/config.json')
    assert config['manifest_sha256']==digest(unpack(ROOT/'data/input-manifest.json.gz'))
    assert config['split_sha256']==digest(unpack(ROOT/'data/split.json.gz'))
    assert config['pretrained']['revision']=='8ca7bfadc2608b007b5cafe20a7d0c29888a5cbb'
    assert config['pretrained']['files']['model.safetensors']['sha256']=='df4c6fc59d1a6c0e3c7b7ef4ae9466a55285df18877f104b58a9c05eaffb26e5'
    assert result['status']=='complete'
    for fold in ('validation','test'):
        for kind in ('baseline','finetuned'):
            rows,m=check_rows(base/f'predictions/{kind}-{fold}.jsonl.gz',folds[fold],key+'/'+kind+'/'+fold)
            check_reported(m,result[kind][fold],key+'/'+kind+'/'+fold)
            run_metrics[(key,kind,fold)]=(rows,m)

pilot_val=run_metrics[('pilot','finetuned','validation')][1]
ext_val=run_metrics[('extended','finetuned','validation')][1]
selection=record['selection'];
assert selection['pilot']['epoch']==19 and selection['extended']['epoch']==37
assert selection['pilot']['validation']['exact_count']==pilot_val['exact_count']
assert selection['extended']['validation']['exact_count']==ext_val['exact_count']
assert abs(selection['pilot']['validation']['character_error_rate']-pilot_val['cer'])<1e-12
assert abs(selection['extended']['validation']['character_error_rate']-ext_val['cer'])<1e-12
winner=sorted([('pilot epoch 19',pilot_val),('extended epoch 37',ext_val)],key=lambda x:(-x[1]['exact_count'],x[1]['cer']))[0][0]
assert winner=='extended epoch 37' and selection['winner']==winner

# Same 614 IDs, truth, source and bytes for all archived existing OCR comparators.
expected=folds['test']; comparison_files={
 'common-old':'predictions/matched-baselines/common-old-test.jsonl.gz',
 'common_old_autocontrast':'predictions/matched-baselines/common-old-autocontrast-test.jsonl.gz',
 'ppocr-v6-small':'predictions/matched-baselines/ppocr-v6-small-test.jsonl.gz',
 'ppocr_v6_medium':'predictions/matched-baselines/ppocr-v6-medium-test.jsonl.gz',}
for name,relative in comparison_files.items():
    rows=jl_bytes(unpack(ROOT/relative)); assert len(rows)==614 and [x['id'] for x in rows]==[x['id'] for x in expected]
    for r,e in zip(rows,expected):
        for k in ('id','source','sha256','label'): assert r[k]==e[k],(name,k,r['id'])
    m=metrics(rows); saved=record['matched_existing_results'][name]
    assert m['samples']==saved['matched_rows'] and m['exact_count']==saved['exact_count']
    assert abs(m['accuracy']-saved['exact_match'])<1e-12
    for source,s in m['by_source'].items():
        want=saved['by_source'][source]
        assert s['samples']==want['samples'] and s['exact_count']==want['exact_count']
        assert abs(s['accuracy']-want['exact_match'])<1e-12

# Exported ONNX batch-size predictions are raw-identical in IDs and answers.
onnx_receipt=j(ROOT/'onnx-runtime/numerical-audit.json')
gpu_test=run_metrics[('extended','finetuned','test')][0]
for batch in (1,32):
    rows,_=check_rows(ROOT/f'onnx-runtime/onnx-batch{batch}-test.jsonl.gz',folds['test'],f'onnx/batch{batch}')
    assert [(r['id'],r['answer']) for r in rows]==[(r['id'],r['answer']) for r in gpu_test]
    assert onnx_receipt['audits'][batch==32]['predictions_sha256']==digest(unpack(ROOT/f'onnx-runtime/onnx-batch{batch}-test.jsonl.gz'))
    assert onnx_receipt['audits'][batch==32]['argmax_token_differences']==0
graph_record=j(ROOT/'onnx-runtime/graph-record.json')
verification=j(ROOT/'onnx-runtime/verification.json')
assert graph_record['graph_sha256']==verification['graph']['sha256']=='63f750d945030a32f43c088e869db436e0dda1c321651e3f849f9df0c6b6733d'
assert graph_record['graph_bytes']==verification['graph']['bytes'] and graph_record['external_data'] is False
assert graph_record['checkpoint_sha256']==record['selection']['best_sha256']
for entry in j(ROOT/'onnx-runtime/source-catalog.json'):
    data=unpack(ROOT/'onnx-runtime/source'/(entry['source']+'.gz'))
    assert digest(data)==entry['source_sha256']

# CPU output must byte-match the selected GPU JSONL and have identical answers.
gpu_data=unpack(ROOT/'trials/extended-job-003/predictions/finetuned-test.jsonl.gz')
cpu_data=unpack(ROOT/'predictions/extended-job-003-selected-test-cpu.jsonl.gz')
receipt=j(ROOT/'cpu-cross-runtime/audit-receipt.json')
assert digest(gpu_data)==digest(cpu_data)==receipt['gpu_pred_sha256']==receipt['cpu_pred_sha256']
assert receipt['status']=='PASS' and receipt['rows']==614 and receipt['different_answers']==[]

# Delivered source snapshots must decompress to the pinned code bytes.
codecat=j(ROOT/'source-code-catalog.json')
for entry in codecat:
    path=ROOT/'source'/(entry['name']+'.gz'); data=unpack(path)
    assert digest(data)==entry['source_sha256'],entry['name']
assert next(x for x in codecat if x['name']=='finetune-pilot-20epoch.py')['source_sha256']=='14323b0e13b9e25700818344dcc2a2685981d79fc83a926857346bb9677a2650'
assert next(x for x in codecat if x['name']=='finetune-extended-100epoch.py')['source_sha256']=='110ff4244b21a9bef6a408a71ba82f118f7f8af98d854f23603c4edbb25cf3ba'
for name,want in {
 'evaluate_crnn_text.py':'79a0b6f1b4fcf432170f55d290decfb5b4494e83e24d693c685df2bed53f5616',
 'evaluate_public_text.py':'a731eaf94a1025025d196511fe18dd6c67dc184b7ba864a4b6f84bfbb35d8034',
 'split_crnn_text.py':'4bda7d85b215379fc04d19f2e526fe1da380c5a127e67f8bd85b98622fd0ace6'}.items():
    assert next(x for x in codecat if x['name']==name)['source_sha256']==want

# Private Kaggle continuation proves the excluded best checkpoint artifact SHA.
cont=j(ROOT/'trials/job-003/kaggle/continuation.json')
entry=next(x for x in cont['files'] if x['path']=='crnn-finetune-result/best.safetensors')
assert entry['sha256']==selection['best_sha256'] and entry['sha256']=='cce95d7cd320d332fecb606b39ccdcf269eb9794093bea7e2c0a4e4b30c11691'
for job,version,status in [('001',1,'complete'),('002',1,'error'),('003',2,'complete')]:
    receipt=j(ROOT/f'trials/job-{job}/kaggle/continuation.json')
    monitor=jl_bytes(raw(ROOT/f'trials/job-{job}/kaggle/monitor-events.jsonl'))
    assert receipt['version']==version and receipt['status']==status
    assert monitor[-1]['status']==status
failure=gzip.decompress(raw(ROOT/'trials/job-002/kaggle/persisted.log.gz')).decode('utf-8')
assert 'pilot budget must be 1..20 epochs and at most 1200 training seconds' in failure
pilot_code=unpack(ROOT/'source/finetune-pilot-20epoch.py.gz').decode('utf-8')
extended_code=unpack(ROOT/'source/finetune-extended-100epoch.py.gz').decode('utf-8')
assert pilot_code.count('if not 1 <= args.max_epochs <= 20 or not 0 < args.max_seconds <= 1200:')==1
assert extended_code.count('if not 1 <= args.max_epochs <= 100 or not 0 < args.max_seconds <= 1200:')==1
assert 'if not 1 <= args.max_epochs <= 20 or not 0 < args.max_seconds <= 1200:' in pilot_code
assert 'if not 1 <= args.max_epochs <= 100 or not 0 < args.max_seconds <= 1200:' in extended_code
assert not any(p.suffix in {'.onnx','.safetensors','.pt'} for p in ROOT.rglob('*') if p.is_file())

# MANIFEST.sha256 is intentionally self-excluding; verify its complete coverage.
catalog_path=ROOT/'artifact-catalog.json'
if catalog_path.exists():
    catalog=j(catalog_path)['files']; catalog_map={x['path']:x for x in catalog}
    actual_paths={str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and p not in (catalog_path,ROOT/'MANIFEST.sha256')}
    assert set(catalog_map)==actual_paths,('catalog coverage',actual_paths-set(catalog_map),set(catalog_map)-actual_paths)
    for rel,entry in catalog_map.items():
        data=raw(ROOT/rel)
        assert len(data)==entry['bytes'] and digest(data)==entry['sha256'],rel
        if rel.endswith('.gz'):
            unpacked=unpack(ROOT/rel)
            assert len(unpacked)==entry['uncompressed_bytes'] and digest(unpacked)==entry['uncompressed_sha256'],rel
manifest_path=ROOT/'MANIFEST.sha256'
if manifest_path.exists():
    listed={}
    for line in manifest_path.read_text().splitlines():
        sha,rel=line.split('  ',1); assert rel not in listed; listed[rel]=sha
    actual={str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in ROOT.rglob('*') if p.is_file() and p!=manifest_path}
    assert listed==actual,('manifest coverage',set(actual)-set(listed),set(listed)-set(actual))
print('PASS: frozen IDs/folds, raw predictions and CER/exact metrics, validation-only winner, four same-614 comparators, CPU/GPU/ONNX answer identity, source pins, selected artifact SHA' + (' and MANIFEST.sha256' if manifest_path.exists() else ''))
print(f"winner={winner}; epoch37 test={run_metrics[('extended','finetuned','test')][1]['exact_count']}/614")
