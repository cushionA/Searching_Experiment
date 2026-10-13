#!/usr/bin/env python3
"""Freeze categorical GPU comparisons on complete original products only.

No annotations, inference, threshold tuning, or scope/citation authoring.
Literal-only products are retained and identified separately in the receipt.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import nextdata_exact_cache_resume_v1 as R
import run_nextdata_table_experiment_v1 as E


def complete_products(db, closure):
    R.validate_private_closure(closure)
    available = {(r['kind'], r['exact_key']) for r in db.execute('SELECT kind,exact_key FROM cache')}
    result = []
    for product in closure['products']:
        required = set()
        for ref in product['pair_refs']:
            required.update((r['kind'], r['exact_key']) for r in db.execute(
                'SELECT DISTINCT kind,exact_key FROM occurrences WHERE pair_ref=?', (R.canonical(ref),)))
        if required <= available:
            result.append(product)
    return result


def read_complete_states(path, expected_refs, public, pairs):
    """A malformed derived assembly is rejected; model unknowns stay unknown."""
    data = path.read_bytes()
    if data and not data.endswith(b'\n'):
        raise ValueError('assembly has an incomplete JSONL tail')
    seen, result = set(), {}
    for raw in data.splitlines():
        row = E.PACKETS.strict_loads(raw)
        ref = row.get('pair_ref')
        if type(ref) is not int or ref not in expected_refs or ref in seen:
            raise ValueError('assembly has a foreign, duplicate, or invalid pair ref')
        if (row.get('scope_status'), row.get('citation_semantics_supported'),
                row.get('production_adoption_allowed')) != ('unproved', False, False):
            raise ValueError('categorical assembly must retain unproved scope')
        seen.add(ref)
        pid = public[ref]['pair_id']
        result[pid] = E.small_pair_states(row, pairs[pid], ref)
    if seen != expected_refs:
        raise ValueError('assembly does not cover every pair of every complete product')
    return result


def checked_subset(bundle, products, public):
    case_ids = {cid for p in products for cid in p['case_ids']}
    refs = {ref for p in products for ref in p['pair_refs']}
    subset = E.subset_source(bundle, case_ids)
    if ({r['case_id'] for r in subset['membership']} != case_ids
            or {(r['case_id'], r['au_row_key']) for r in subset['rows']}
            != {tuple(row) for p in products for row in p['row_keys']}
            or {(r['case_id'], r['task_id']) for r in subset['tasks']}
            != {tuple(task) for p in products for task in p['task_ids']}):
        raise ValueError('complete original product cases/rows/tasks were trimmed')
    dispatched = {row['pair_id'] for row in public.values()}
    required_pair_ids = {b['pair_id'] for b in subset['bindings'] if b.get('pair_id') in dispatched}
    if required_pair_ids != {public[r]['pair_id'] for r in refs}:
        raise ValueError('product pair closure differs from full original task bindings')
    return case_ids, refs, subset


def collect(experiment, assembled, index, closure_file, output):
    _, bundle, public, _, _ = E.load_experiment(experiment)
    assembly = json.loads((assembled / 'ASSEMBLY.json').read_text())
    if (assembly.get('original_input_sha256'), assembly.get('index_sha256'),
            assembly.get('private_source_closure_sha256')) != (R.PINNED_INPUT_SHA, R.sha(index), R.sha(closure_file)):
        raise ValueError('assembled source/index/whole-product closure changed')
    if (assembly.get('product_publication_requires_all_product_pairs_complete'),
            assembly.get('scope_status'), assembly.get('citation_semantics_supported'),
            assembly.get('production_adoption_allowed')) != (True, 'unproved', False, False):
        raise ValueError('assembly publication or scope policy differs')
    for name, pin in assembly['files'].items():
        if Path(name).name != name or name in {'.', '..'}:
            raise ValueError('assembly file must be a top-level file')
        path = assembled / name
        if {'sha256': R.sha(path), 'size_bytes': path.stat().st_size} != pin:
            raise ValueError('assembled bytes changed: ' + name)
    closure = json.loads(closure_file.read_text())
    db = R.connect(index)
    try:
        R.verify_index_identity(db)
        R.verify_resume_ready(db)
        products = complete_products(db, closure)
    finally:
        db.close()
    if len(products) != assembly['complete_product_count'] or not products:
        raise ValueError('assembly completed product count differs from immutable index')
    case_ids, refs, subset = checked_subset(bundle, products, public)
    # Read and verify all four views before publishing any output freeze.
    states = {}
    for kind in R.KINDS:
        for variant, raw_variant in (('full', 'fullbody'), ('table', 'tablebody')):
            name = f'predictions-{kind}-{raw_variant}.jsonl'
            if name not in assembly['files']:
                raise ValueError('required prediction view is outside assembly manifest')
            states[kind, variant] = read_complete_states(assembled / name, refs, public, bundle.source['pair_by_id'])
    output.mkdir(parents=True, exist_ok=False)
    ml_case_ids = {cid for p in products if p['pair_refs'] for cid in p['case_ids']}
    receipt = {'product_count': len(products), 'ml_product_count': sum(bool(p['pair_refs']) for p in products),
               'literal_only_product_count': sum(not p['pair_refs'] for p in products),
               'case_count': len(case_ids), 'row_count': len(subset['rows']), 'task_count': len(subset['tasks']),
               'pair_count': len(refs), 'ml_product_case_ids': sorted(ml_case_ids),
               'literal_only_product_case_ids': sorted(case_ids - ml_case_ids),
               'assembled_source': E.pin(assembled / 'ASSEMBLY.json'), 'index': E.pin(index),
               'private_product_closure': E.pin(closure_file), 'all_original_rows_tasks_retained': True,
               'selection': 'all complete products in frozen label-free execution index',
               'scope_validation_relaxed': True, 'source_citations_manufactured': False}
    results = {}
    for kind in R.KINDS:
        for variant, raw_variant in (('full', 'fullbody'), ('table', 'tablebody')):
            dest = E.fresh_dir(output / f'{kind}-{variant}')
            raw = assembled / f'predictions-{kind}-{raw_variant}.jsonl'
            shutil.copyfile(raw, dest / 'original-small-model-firstpass.jsonl')
            resolutions = E.small_model_resolutions(subset, states[kind, variant], bundle.source['pair_by_id'])
            gate = E.save_gate(dest, subset, resolutions, 'small_model_relaxed_scope_diagnostic_same_whole_sku_gate')
            if (gate['case_count'], gate['row_count'], gate['task_count']) != (len(case_ids), len(subset['rows']), len(subset['tasks'])):
                raise ValueError('categorical gate lost a complete original inventory')
            results[dest.name] = E.freeze_collection(dest, experiment, {**receipt, 'variant': variant,
                'model_id': R.model_identity(kind)['model_id'], 'valid_pair_count': len(states[kind, variant]),
                'expected_pair_count': len(refs), 'missing_or_invalid_pair_count': 0,
                'raw_file': E.pin(dest / 'original-small-model-firstpass.jsonl'), 'gate': gate})
    E.write_json(output / 'complete-product-cohort.json', {**receipt, 'collections': results, **E.FLAGS})
    return {k: v['gate'] for k, v in results.items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('experiment', 'assembled', 'index', 'closure', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    a = parser.parse_args()
    print(collect(a.experiment, a.assembled, a.index, a.closure, a.output))
