#!/usr/bin/env python3
"""Add byte-identical source-regeneration proof to the existing checkpoint."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[2]
LAB=ROOT/'.lab-output/sku-classical-parser-20261015-v1'
EXP=ROOT/'experiments/sku-matching'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def main():
    a=json.loads((LAB/'population-inputs/manifest.json').read_text())
    b=json.loads((LAB/'population-reproduced-v2/manifest.json').read_text())
    if a['output_sha256']!=b['output_sha256']:raise ValueError('source regeneration differs')
    for name,pin in a['output_sha256'].items():
        if sha(LAB/'population-inputs'/name)!=pin or sha(LAB/'population-reproduced-v2'/name)!=pin:
            raise ValueError('actual source bytes differ')
    code=EXP/'prepare_classical_population_v1.py'
    if sha(code)!=b['preparer_code_sha256']:raise ValueError('regenerated preparer differs')
    source=EXP/'results/20261015-sku-classical-result-v1.zip'
    target=EXP/'results/20261015-sku-classical-result-v2.zip'
    proof=LAB/'SOURCE_REGENERATION_PROOF.json'
    record={'schema':'classical-source-regeneration-proof-v1',
        'old_preparer_code_sha256':a['preparer_code_sha256'],
        'regenerated_preparer_code_sha256':b['preparer_code_sha256'],
        'all_five_input_files_byte_identical':True,'actual_input_sha256':a['output_sha256'],
        'predictions_or_thresholds_changed':False,'new_reference_labels_read':False,
        'reason':'Preparer file hash changed after generation; verify exact inputs with the current code, retaining both manifests.',
        'prior_complete_checkpoint_sha256':sha(source),
        'regeneration_command':'python3 -B experiments/sku-matching/prepare_classical_population_v1.py --source .lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs --output NEW_DIRECTORY'}
    with proof.open('x') as f:json.dump(record,f,ensure_ascii=False,indent=2);f.write('\n')
    handoff=EXP/'session-handoff-20261015-classical-final.txt'
    with handoff.open('x') as f:
        f.write((EXP/'session-handoff-20261015-classical-v1.txt').read_text())
        f.write('\nLATEST COMPLETE CHECKPOINT: experiments/sku-matching/results/20261015-sku-classical-result-v2.zip\n')
        f.write('Source regeneration proved all 5 population inputs byte-identical with current preparer.\n')
        f.write('See SOURCE_REGENERATION_PROOF.json and population-reproduced-v2/manifest.json.\n')
        f.write('v2 adds provenance verification; measured predictions and scores unchanged.\n')
    additions=[proof,LAB/'population-reproduced-v2/manifest.json',
        LAB/'population-reproduced-v2.log',handoff,Path(__file__)]
    continuation=LAB/'EXPORT_CONTINUATION_MANIFEST.json'
    with continuation.open('x') as f:
        json.dump({'source_archive_sha256':sha(source),'additional_files':{
            p.relative_to(ROOT).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size} for p in additions}},f,indent=2)
        f.write('\n')
    additions.append(continuation)
    with zipfile.ZipFile(source) as old,zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as new:
        for entry in old.infolist():
            with old.open(entry.filename) as r,new.open(entry.filename,'w') as w:shutil.copyfileobj(r,w)
        for p in additions:new.write(p,p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(target) as z:
        if len(z.namelist())!=len(set(z.namelist())) or z.testzip() is not None:
            raise ValueError('checkpoint CRC/inventory failed')
    digest=sha(target)
    with target.with_suffix('.zip.sha256').open('x') as f:f.write(digest+'  '+target.name+'\n')
    print(json.dumps({'path':str(target),'bytes':target.stat().st_size,'sha256':digest},ensure_ascii=False))


if __name__=='__main__':main()
