#!/usr/bin/env python3
"""Freeze specification-table-first bodies on the same completed DEV40 inputs.

Only public body_text changes. Original rows, conditions, hidden bindings,
prefixes, options, model code, thresholds and aggregation remain identical.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import zipfile

import kaggle_full_condition_choices_v1 as GPU
from prepare_v7_small_nli_single_v1 import read, write, write_rows, ROOT
from restore_session_artifacts_20261014 import sha
from sku_spec_table_first_v1 import select_document, POLICY

BASE = ROOT / '.lab-output/sku-v7-small-nli-single-20261014-v2'


def prepare(output: Path, source: Path = BASE):
    if output.exists():
        raise FileExistsError(output)
    previous = json.loads((source / 'HOST_INPUT_FREEZE.json').read_text())
    continuation = json.loads((source / 'job/continuation.json').read_text())
    if continuation['status'] != 'complete' or not continuation['ready_for_verification']:
        raise ValueError('comparison source is not a recovered completed job')
    for name, digest in previous['host_pins'].items():
        if sha(source / 'host' / name) != digest:
            raise ValueError('completed comparison host input changed: ' + name)
    for name, pin in json.loads((source / 'public-bundle/BUNDLE_MANIFEST.json').read_text())['files'].items():
        p = source / 'public-bundle' / name
        if sha(p) != pin['sha256'] or p.stat().st_size != pin['size_bytes']:
            raise ValueError('completed comparison public input changed: ' + name)
    documents = read(source / 'host/documents.jsonl')
    selections = [select_document(document, ROOT) for document in documents]
    by_digest = {row['document_sha256']: row for row in selections}
    if len(selections) != 40 or len(by_digest) != len(selections):
        raise ValueError('all original document identities must be unique and retained')
    original = read(source / 'public-bundle/contexts.jsonl')
    contexts = [{**row, 'body_text': by_digest[row['document_sha256']]['text']} for row in original]
    for before, after in zip(original, contexts, strict=True):
        if ({k: v for k, v in before.items() if k != 'body_text'}
                != {k: v for k, v in after.items() if k != 'body_text'}):
            raise ValueError('comparison changed a field other than body_text')
    output.mkdir(parents=True, exist_ok=False)
    host = output / 'host'; host.mkdir()
    for p in (source / 'host').iterdir():
        if not p.is_file() or p.is_symlink():
            raise ValueError('unexpected host input')
        shutil.copyfile(p, host / p.name)
    write_rows(host / 'spec-table-selections.jsonl', selections)
    write(host / 'SPEC_TABLE_POLICY.json', POLICY)
    bundle = output / 'public-bundle'; bundle.mkdir()
    shutil.copytree(source / 'public-bundle/code', bundle / 'code')
    write_rows(bundle / 'contexts.jsonl', contexts)
    inventory = {p.relative_to(bundle).as_posix(): {'size_bytes': p.stat().st_size, 'sha256': sha(p)}
                 for p in bundle.rglob('*') if p.is_file()}
    write(bundle / 'BUNDLE_MANIFEST.json', {
        'schema_version': 'v7-small-nli-single-bundle-v1', 'labels_included': False,
        'host_joins_included': False, 'prior_answers_included': False, 'weights_included': False,
        'credentials_included': False, 'files': inventory, 'context_count': len(contexts),
        'option_count': sum(len(c['option_values']) for c in contexts),
        'views': ['title_row', 'body'], 'models_executed': ['nli'],
        'body_policy': 'specification_table_first_with_exact_full_body_fallback'})
    verified = GPU.verify_bundle(bundle)
    dataset = output / 'dataset-upload'; dataset.mkdir()
    archive = dataset / 'v7-small-nli-single-inputs.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(bundle.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(bundle).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError('table-first public archive CRC failure')
    write(dataset / 'dataset-metadata.json', {
        'id': previous['dataset_ref'], 'title': previous['dataset_ref'].split('/')[1],
        'licenses': [{'name': 'other'}]})
    summary = {
        'document_count': len(selections), 'document_modes': dict(Counter(s['mode'] for s in selections)),
        'document_modes_by_side': {side: dict(Counter(s['mode'] for s in selections if s['side'] == side))
                                   for side in ('au', 'rakuten')},
        'context_count': len(contexts),
        'context_modes': dict(Counter(by_digest[c['document_sha256']]['mode'] for c in contexts)),
        'original_body_characters': sum(len(c['body_text']) for c in original),
        'selected_body_characters': sum(len(c['body_text']) for c in contexts),
        'body_changed_context_count': sum(a['body_text'] != b['body_text'] for a, b in zip(original, contexts)),
        'all_nonbody_public_fields_identical': True,
        'fallback_body_is_exact_original': all(s['text'] == d['description']['text']
            for s, d in zip(selections, documents) if s['mode'] == 'full_body_fallback')}
    freeze = {**previous, 'schema': 'v7-small-nli-spec-table-first-host-freeze-v1',
        'labels_read': False, 'model_inference_run': False,
        'comparison_source': str(source.relative_to(ROOT)),
        'comparison_host_freeze_sha256': sha(source / 'HOST_INPUT_FREEZE.json'),
        'comparison_job_version': continuation['version'], 'body_policy': POLICY,
        'table_selection_summary': summary,
        'host_pins': {p.name: sha(p) for p in host.iterdir()},
        'bundle_manifest_sha256': verified['manifest_sha256'],
        'archive_sha256': sha(archive), 'archive_bytes': archive.stat().st_size,
        'body_character_count': summary['selected_body_characters'],
        'selector_code_sha256': sha(Path(__file__).with_name('sku_spec_table_first_v1.py')),
        'preparation_code_sha256': sha(Path(__file__))}
    write(output / 'HOST_INPUT_FREEZE.json', freeze)
    print(json.dumps({'experiment': str(output), 'table_selection_summary': summary,
                      'archive_bytes': archive.stat().st_size, 'labels_read': False}, ensure_ascii=False))
    return freeze


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=BASE)
    args = parser.parse_args()
    prepare(args.output.resolve(), args.source.resolve())
