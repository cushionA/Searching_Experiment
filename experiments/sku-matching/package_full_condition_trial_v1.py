"""Package explicitly selected full-trial evidence, without weights or credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.lab-output'
DATE = '20261011'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    if args.archive.exists():
        raise FileExistsError(args.archive)
    selected = {}

    def add(path):
        if not path.is_file():
            raise FileNotFoundError(path)
        name = path.relative_to(ROOT).as_posix()
        if path.name.startswith('.env') or '.deps' in path.parts or path.suffix in {'.safetensors', '.bin', '.pt'}:
            raise ValueError('not trial evidence: ' + name)
        selected[name] = path

    def directory(path):
        if not path.is_dir():
            raise FileNotFoundError(path)
        for file in sorted(path.rglob('*')):
            if file.is_file() and '__pycache__' not in file.parts:
                add(file)

    code = ['trial_full_scoped_condition_choices_v1.py', 'kaggle_full_condition_choices_v1.py',
            'prepare_novel_condition_choices_v1.py', 'evaluate_full_condition_choices_v1.py',
            'package_full_condition_kaggle_input_v1.py', 'kaggle_full_condition_bootstrap_v1.py',
            'verify_full_condition_kaggle_results_v1.py', 'compare_full_condition_runtime_v1.py',
            'integrate_claude_description_v1.py', 'import_claude_alignment_handoff_v2.py',
            'import_claude_alignment_handoff_v1.py',
            'trial_generic_model_jev_v1.py', 'trial_generic_model_nli_v1.py',
            'trial_cpu_requirement_relations_v1.py', 'trial_scoped_condition_nli_choice_v1.py',
            'trial_scoped_condition_nli_v1.py', 'trial_scoped_condition_gliner_v1.py',
            'trial_generic_gliner_choice_v1.py', Path(__file__).name]
    for name in code:
        add(ROOT / 'experiments/sku-matching' / name)
    for name in ['test_lab_sku_full_scoped_condition_choices.py', 'test_lab_sku_kaggle_full_condition_choices.py',
                 'test_lab_sku_novel_condition_preparation.py', 'test_lab_sku_condition_runtime_comparison.py']:
        add(ROOT / 'tests' / name)
    add(ROOT / 'experiments/sku-matching/sku-full-condition-choice-trial-20261011.txt')
    package = LAB / 'sku-full-kaggle-package-20261011-v1'
    directory(package / 'payload')
    directory(package / 'notebook-upload')
    for name in ['package-receipt.json', 'acceptance-criteria.json', 'dataset-status.json']:
        add(package / name)
    job = LAB / 'sku-full-kaggle-job-20261011-v1'
    for name in ['job.json', 'continuation.json', 'monitor-events.jsonl', 'persisted.log', 'quota-after.json']:
        add(job / name)
    for name in ['sku-full-condition-results.zip', 'sku-full-condition-result-receipt.json']:
        add(job / 'artifacts' / name)
    add(LAB / 'sku-full-kaggle-results-20261011-v1/verification.json')
    directory(LAB / 'sku-full-condition-evaluation-20261011-v1')
    for name in ['sku-full-novel-stage-20261011-v1', 'sku-full-novel-similarity-20261011-v1',
                 'sku-full-novel-prep-20261011-v1']:
        directory(LAB / name)
    for name in ['sku-full-novel-audit-20261011-v1.json', 'sku-full-kaggle-input-audit-20261011-v2.json',
                 'sku-full-condition-runtime-comparison-20261011-v1.json',
                 'sku-full-condition-results-audit-20261011-v2.json',
                 'sku-full-condition-all-tests-20261011-v3.log',
                 'sku-full-runtime-comparison-tests-20261011-v1.log']:
        add(LAB / name)
    add(LAB / 'sku-full-novel-model-20261011-v1/download-provenance.json')
    for name in ['cases.jsonl', 'products.jsonl', 'manifest.json']:
        add(LAB / 'sku-generic-model-inputs-20261010-v2' / name)
    for path in ['sku-real-luna-labels-20261010-v3/labels.jsonl',
                 'sku-novel-luna-labels-20261010-v1/labels.jsonl',
                 'sku-scoped-condition-reference-20261011-v3/reference.jsonl',
                 'sku-scoped-condition-reference-20261011-v3/manifest.json']:
        add(LAB / path)
    stage = json.loads((LAB / 'sku-full-novel-stage-20261011-v1/manifest.json').read_text())
    for path, expected in stage['source_raw_sha256'].items():
        raw = ROOT / path
        if digest(raw) != expected:
            raise ValueError('novel raw changed: ' + path)
        add(raw)
    dependencies = {}
    for name in ['20261011-sku-pr26-v5-description-integration.zip',
                 '20261011-sku-error-only-cpu-trial.zip',
                 '20261010-sku-generic-residual-checkpoint.zip',
                 '20261011-sku-label-quality-audit.zip']:
        path = ROOT / 'experiments/sku-matching/results' / name
        dependencies[path.relative_to(ROOT).as_posix()] = {'sha256': digest(path), 'size_bytes': path.stat().st_size}
    manifest = {'schema_version': 'full-condition-choice-trial-package-v1',
                'synthetic_performance_data': False, 'reference_kind': 'separate_machine_labels_not_human_gold',
                'production_adoption_allowed': False, 'credentials_or_model_weights_included': False,
                'model_result_archive_must_be_extracted_separately': True,
                'dependencies_not_duplicated': dependencies,
                'files': {name: {'sha256': digest(path), 'size_bytes': path.stat().st_size}
                          for name, path in sorted(selected.items())}}
    content = json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b'\n'
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(selected.items()):
            archive.write(path, name)
        archive.writestr('MANIFEST.json', content)
    with zipfile.ZipFile(args.archive) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(selected) + 1:
            raise ValueError('final archive failed integrity checks')
        for name, metadata in manifest['files'].items():
            data = archive.read(name)
            if len(data) != metadata['size_bytes'] or hashlib.sha256(data).hexdigest() != metadata['sha256']:
                raise ValueError('final archive payload differs: ' + name)
    args.archive.with_suffix('.manifest.json').write_bytes(content)
    checksum = digest(args.archive)
    args.archive.with_suffix('.zip.sha256').write_text(checksum + '  ' + args.archive.name + '\n')
    print(json.dumps({'file_count': len(selected), 'archive_bytes': args.archive.stat().st_size, 'sha256': checksum}))


if __name__ == '__main__':
    main()
