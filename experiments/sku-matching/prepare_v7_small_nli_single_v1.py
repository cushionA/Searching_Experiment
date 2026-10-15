#!/usr/bin/env python3
"""Freeze source-only DEV contexts and host joins for one NLI model on Kaggle.

Claude v7 states are audit strata, never candidate filters or source proof.
No labels, prior model answers, credentials, weights or host joins enter upload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import kaggle_full_condition_choices_v1 as GPU
from grade_fresh_dev_sku_gate_v1 import validate_dev_membership
from restore_session_artifacts_20261014 import sha, verify

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / '.lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8'
ARCHIVE_MANIFEST_SHA256 = 'aca0b1de4919cef5f2bf0da001dadd7c31f4afe9a90750066f0a76a61950d099'
NAMES = ('manifest.json', 'membership.jsonl', 'selection-plan.json', 'case-plans.jsonl',
         'row-plans.jsonl', 'tasks.jsonl', 'requests.jsonl', 'documents.jsonl', 'fresh-v7-task-hints.jsonl')


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def write(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def write_rows(path, rows):
    with path.open('x', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n')


def make_contexts(requests, documents):
    """Desired counterpart values never influence grouping, prompt or ordering."""
    contexts, bindings, lookup = [], [], {}
    for request in requests:
        doc = documents[request['target_document_id']]
        if request['target_document_sha256'] != doc['document_sha256']:
            raise ValueError('target document identity differs')
        if request['selected_value'] not in request['option_values']:
            raise ValueError('desired source whole value absent from original family')
        public = {'axis_name': request['axis_name'], 'option_values': request['option_values'],
            'prefix': '現在の商品名：' + doc['title']['text'] + '\n現在の選択行：\n' + request['target_selected_axes_text'],
            'body_text': doc['description']['text'], 'document_sha256': doc['document_sha256']}
        key = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if key not in lookup:
            lookup[key] = len(contexts)
            contexts.append({'context_ref': lookup[key], **public})
        bindings.append({'request_id': request['request_id'], 'context_ref': lookup[key],
            'selected_option_index': request['option_values'].index(request['selected_value'])})
    return contexts, bindings


def prepare(output, owner='superbigzabuton', slug='sku-v7-nli-single-20261014-v1'):
    if output.exists():
        raise FileExistsError(output)
    archive_manifest = HERE / 'results/20261013-sku-next-dev-checkpoint.zip.manifest.json'
    if sha(archive_manifest) != ARCHIVE_MANIFEST_SHA256:
        raise ValueError('original DEV archive manifest SHA differs')
    pins = {r['path']: r for r in json.loads(archive_manifest.read_text())['members']}
    for name in NAMES:
        p = SOURCE / name
        pin = pins[p.relative_to(ROOT).as_posix()]
        verify(p, pin['bytes'], pin['sha256'])
    membership, cases, _ = validate_dev_membership(SOURCE / 'membership.jsonl', SOURCE / 'case-plans.jsonl',
        SOURCE / 'row-plans.jsonl', SOURCE / 'selection-plan.json')
    requests, tasks, rows = [read(SOURCE / n) for n in ('requests.jsonl', 'tasks.jsonl', 'row-plans.jsonl')]
    documents = {r['document_id']: r for r in read(SOURCE / 'documents.jsonl')}
    if (len(membership), len(rows), len(tasks), len(requests), len(documents)) != (40, 504, 1924, 1045, 40):
        raise ValueError('fixed DEV all-case/all-row/all-condition population differs')
    hints = {h['task_id']: h for h in read(SOURCE / 'fresh-v7-task-hints.jsonl')}
    task_ids = {t['task_id'] for t in tasks}
    if not set(hints) <= task_ids or any(h.get('automatic_support_allowed') is not False
        or h.get('automatic_conflict_allowed') is not False or h.get('producer_status_is_proof') is not False
        for h in hints.values()):
        raise ValueError('v7 hints may not certify support/conflict or refer to foreign tasks')
    contexts, bindings = make_contexts(requests, documents)
    output.mkdir(parents=True, exist_ok=False)
    host, bundle = output / 'host', output / 'public-bundle'
    host.mkdir(); bundle.mkdir()
    for name in NAMES:
        shutil.copyfile(SOURCE / name, host / name)
    write_rows(host / 'request-bindings.jsonl', bindings)
    write_rows(bundle / 'contexts.jsonl', contexts)
    code = bundle / 'code'; code.mkdir()
    for name in dict.fromkeys((*GPU.CODE_NAMES, 'try_gliner.py', 'kaggle_v7_small_nli_single_v1.py')):
        shutil.copyfile(HERE / name, code / name)
    inventory = {p.relative_to(bundle).as_posix(): {'size_bytes': p.stat().st_size, 'sha256': sha(p)}
                 for p in bundle.rglob('*') if p.is_file()}
    write(bundle / 'BUNDLE_MANIFEST.json', {'schema_version': 'v7-small-nli-single-bundle-v1',
        'labels_included': False, 'host_joins_included': False, 'prior_answers_included': False,
        'weights_included': False, 'credentials_included': False, 'files': inventory,
        'context_count': len(contexts), 'option_count': sum(len(c['option_values']) for c in contexts),
        'views': ['title_row', 'body'], 'models_executed': ['nli']})
    verified = GPU.verify_bundle(bundle)
    dataset = output / 'dataset-upload'; dataset.mkdir()
    archive = dataset / 'v7-small-nli-single-inputs.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for p in bundle.rglob('*'):
            if p.is_file(): z.write(p, p.relative_to(bundle).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None: raise ValueError('public input ZIP CRC differs')
    write(dataset / 'dataset-metadata.json', {'id': f'{owner}/{slug}-inputs',
        'title': slug + '-inputs', 'licenses': [{'name': 'other'}]})
    freeze = {'schema': 'v7-small-nli-single-host-freeze-v1', 'split': 'dev', 'independent_test': False,
        'labels_read': False, 'model_inference_run': False, 'all_original_rows_conditions_retained': True,
        'v7_hints_are_strata_only': True, 'v7_candidate_exclusion_used': False, 'jev_prefilter_used': False,
        'literal_overlay': False, 'production_adoption_allowed': False, 'scope_status': 'unproved',
        'case_count': len(cases), 'row_count': len(rows), 'task_count': len(tasks), 'request_count': len(requests),
        'context_count': len(contexts), 'context_options_per_view': sum(len(c['option_values']) for c in contexts),
        'body_character_count': sum(len(c['body_text']) for c in contexts),
        'source_pins': {n: sha(SOURCE / n) for n in NAMES},
        'host_pins': {p.name: sha(p) for p in host.iterdir()},
        'bundle_manifest_sha256': verified['manifest_sha256'], 'archive_sha256': sha(archive),
        'archive_bytes': archive.stat().st_size, 'dataset_ref': f'{owner}/{slug}-inputs',
        'kernel_ref': f'{owner}/{slug}', 'worker_code_sha256': sha(HERE / 'kaggle_v7_small_nli_single_v1.py')}
    write(output / 'HOST_INPUT_FREEZE.json', freeze)
    print(json.dumps(freeze, ensure_ascii=False))
    return freeze


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--owner', default='superbigzabuton')
    parser.add_argument('--slug', default='sku-v7-nli-single-20261014-v1')
    prepare(**vars(parser.parse_args()))
