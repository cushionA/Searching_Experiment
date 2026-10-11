"""Make a private, label-free Kaggle payload from frozen real SKU inputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import zipfile

from trial_full_scoped_condition_choices_v1 import load_inputs
from trial_scoped_condition_gliner_v1 import sha, write_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CODE = (
    'kaggle_full_condition_choices_v1.py', 'trial_full_scoped_condition_choices_v1.py',
    'trial_generic_model_jev_v1.py', 'trial_generic_model_nli_v1.py',
    'trial_cpu_requirement_relations_v1.py', 'trial_scoped_condition_nli_choice_v1.py',
    'trial_scoped_condition_nli_v1.py', 'trial_scoped_condition_gliner_v1.py',
    'trial_generic_gliner_choice_v1.py',
)
PREP_FILES = ('manifest.json', 'requests.jsonl', 'documents.jsonl', 'tasks.jsonl',
              'case_plans.jsonl', 'row_plans.jsonl')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--full-dir', type=Path, required=True)
    p.add_argument('--novel-dir', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--dataset-id', required=True)
    args = p.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    payload = args.output_dir / 'payload'
    payload.mkdir(parents=True)
    sources, cohorts = {}, {}
    for tag, directory in [('full', args.full_dir), ('novel', args.novel_dir)]:
        requests, documents, bindings, manifest, _ = load_inputs(directory)
        destination = payload / 'input' / tag
        destination.mkdir(parents=True)
        for name in PREP_FILES:
            source = directory / name
            if name != 'manifest.json' and sha(source) != manifest['output_sha256'][name]:
                raise ValueError('prepared file SHA differs: ' + name)
            shutil.copyfile(source, destination / name)
            sources[str(source.resolve())] = sha(source)
        cohorts[tag] = {'case_count': manifest['case_count'], 'request_count': len(requests),
                        'document_count': len(documents), 'task_count': sum(map(len, bindings.values()))}
    (payload / 'code').mkdir()
    for name in CODE:
        source = HERE / name
        shutil.copyfile(source, payload / 'code' / name)
        sources[str(source.resolve())] = sha(source)
    provenance = ROOT / '.deps/sku-modernbert-jev-model/provenance.json'
    (payload / 'provenance').mkdir()
    shutil.copyfile(provenance, payload / 'provenance/jev-provenance.json')
    files = {str(f.relative_to(payload)): {'size_bytes': f.stat().st_size, 'sha256': sha(f)}
             for f in sorted(payload.rglob('*')) if f.is_file()}
    manifest = {'schema_version': 'full-condition-kaggle-input-v1', 'synthetic_data': False,
                'labels_included': False, 'labels_read': False, 'cohorts': cohorts,
                'models': 'frozen ModernBERT JEV and Japanese NLI; FP32 GPU',
                'private_dataset': True, 'input_and_code_sha256': sources, 'files': files}
    write_json(payload / 'BUNDLE_MANIFEST.json', manifest)
    dataset = args.output_dir / 'dataset-upload'
    dataset.mkdir()
    archive = dataset / 'sku-full-input.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(payload.rglob('*')):
            if f.is_file():
                z.write(f, str(f.relative_to(payload)))
    write_json(dataset / 'dataset-metadata.json', {
        'id': args.dataset_id, 'title': args.dataset_id.split('/')[-1],
        'licenses': [{'name': 'other'}], 'description': 'Private real SKU inference inputs. No scoring labels or weights.'})
    write_json(args.output_dir / 'package-receipt.json', {
        'archive_sha256': sha(archive), 'archive_bytes': archive.stat().st_size,
        'bundle_manifest_sha256': sha(payload / 'BUNDLE_MANIFEST.json'),
        'member_count': len(files) + 1, 'cohorts': cohorts,
        'labels_included': False, 'synthetic_data': False, 'private': True})
    print(json.dumps({'archive_bytes': archive.stat().st_size, 'cohorts': cohorts,
                      'bundle_manifest_sha256': sha(payload / 'BUNDLE_MANIFEST.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
