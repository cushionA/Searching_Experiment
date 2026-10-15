#!/usr/bin/env python3
"""Grade frozen diagnostic component overlays; never change their predictions."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import grade_final_test_subset_v1 as G
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE, V3_ARCHIVE
from replay_dev_nli_policies_v1 import pruning_metrics
from run_installed_library_selection_v1 import read, sha, write

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.lab-output/sku-classical-parser-20261015-v1/population-inputs'
POP = ROOT / '.lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs'
BASELINE = ROOT / '.lab-output/sku-installed-library-matching-20261015-v1/price-free/selection-v1'
MODES = ('baseline_raw', 'strict', 'native_punctuation')
CASE_SHA = '06898908758f6a6dfe3446ee63c7688c0e68d4928dde7dbfe05a821e2ef63a09'
PRODUCT_SHA = '75353ed9c40333bef064109d8c99a0baf72c707564724ca1bd9481ccae539926'


def paths_for(run, mode):
    directory, prefix = (BASELINE, 'raw') if mode == 'baseline_raw' else (run, mode)
    return {kind: directory / f'{prefix}-{kind}.jsonl' for kind in ('requests', 'rows', 'cases')}


def grade(run):
    run = run.resolve()
    frozen = json.loads((run / 'OUTPUT_FREEZE.json').read_text())
    verified = {}

    def check(path, pin):
        actual = sha(path)
        if actual != pin:
            raise ValueError(f'Frozen data/code changed: {path}')
        verified[str(path.relative_to(ROOT))] = actual

    for name, pin in frozen['input_pins'].items():
        check(ROOT / name, pin)
    for name, pin in frozen['output_pins'].items():
        check(run / name, pin)
    for name, pin in frozen['code_pins'].items():
        check(Path(__file__).with_name(name), pin)
    check(POP / 'cases.jsonl', CASE_SHA)
    check(POP / 'products.jsonl', PRODUCT_SHA)
    bf = json.loads((BASELINE / 'OUTPUT_FREEZE.json').read_text())
    for kind, path in paths_for(run, 'baseline_raw').items():
        check(path, bf['output_pins'][path.name])
    requests, original_rows, cases = (read(SOURCE / name) for name in
                                     ('requests.jsonl', 'row-plans.jsonl', 'case-plans.jsonl'))
    ids = {r['request_id'] for r in requests}
    row_keys = [(r['case_id'], r['au_row_key']) for r in original_rows]
    case_ids = {c['case_id'] for c in cases}
    if (len(requests), len(original_rows), len(cases), sum(len(r['bindings']) for r in original_rows)) != (30169, 32325, 1370, 132136):
        raise ValueError('Original all-row/all-condition population differs')
    all_records = {mode: {kind: read(path) for kind, path in paths_for(run, mode).items()} for mode in MODES}
    base_states = {r['request_id']: r['state'] for r in all_records['baseline_raw']['requests']}
    for mode, records in all_records.items():
        if len(records['requests']) != len(ids) or {r['request_id'] for r in records['requests']} != ids:
            raise ValueError(f'Request population differs: {mode}')
        if [(r['case_id'], r['au_row_key']) for r in records['rows']] != row_keys:
            raise ValueError(f'Original row membership/order differs: {mode}')
        if len(records['cases']) != len(case_ids) or {c['case_id'] for c in records['cases']} != case_ids:
            raise ValueError(f'Case population differs: {mode}')
        if any(base_states[r['request_id']] != 'unknown' and base_states[r['request_id']] != r['state']
               for r in records['requests']):
            raise ValueError('An existing resolved state was overwritten')
    original_cases = read(POP / 'cases.jsonl')
    membership = [{'case_id': c['case_id'], 'source_version':
                   'v2' if '/eval-v2/' in c['source_record']['file'].replace('\\', '/') else 'v3'}
                  for c in original_cases]
    # Reference files are opened only after all hypotheses and candidates pass freeze checks.
    refs = G.read_label_archives(V2_ARCHIVE, V3_ARCHIVE, membership, SOURCE / 'row-plans.jsonl')
    keys = ('direction', 'axis_name', 'option_values', 'target_document_id', 'target_selected_axes')

    def context(r):
        return json.dumps({k: r[k] for k in keys}, ensure_ascii=False, sort_keys=True)

    all_contexts = {context(r) for r in requests}
    result = {'schema': 'library-components-diagnostic-grade-v1', 'independent_test': False,
              'reference_kind': 'Claude Haiku5.5 machine annotation, not human gold',
              'prediction_freeze_sha256': sha(run / 'OUTPUT_FREEZE.json'),
              'grader_code_sha256': sha(Path(__file__)), 'verified_before_labels': verified,
              'new_rules_or_thresholds_fitted_on_labels': False,
              'attribute_level_ground_truth_available': False,
              'actual_model_tokens_cost_or_calls_measured': False,
              'production_adoption_allowed': False, 'variants': {}}
    for mode, records in all_records.items():
        states = {r['request_id']: r['state'] for r in records['requests']}
        rows = {(r['case_id'], r['au_row_key']): r['state'] for r in records['rows']}
        decisions = {r['case_id']: r for r in records['cases']}
        accepted, wrong, accepted_unknown, rejected, rejected_wrong, rejected_unknown = [], [], [], [], [], []
        for cid, d in decisions.items():
            label, allowed = G.canonical_reference(refs[cid])
            if d['disposition'] == 'accept':
                if label not in ('matched', 'unmatched'):
                    accepted_unknown.append(cid)
                elif label == 'matched' and d['au_row_key'] in allowed:
                    accepted.append(cid)
                else:
                    wrong.append(cid)
            elif d['disposition'] == 'reject':
                (rejected if label == 'unmatched' else rejected_wrong if label == 'matched'
                 else rejected_unknown).append(cid)
        remaining = {b['request_id'] for row in original_rows
                     if decisions[row['case_id']]['disposition'] == 'review'
                     and rows[row['case_id'], row['au_row_key']] != 'conflict'
                     for b in row['bindings'] if states[b['request_id']] == 'unknown'}
        contexts = {context(r) for r in requests if r['request_id'] in remaining}
        baseline_decisions = {d['case_id']: d for d in all_records['baseline_raw']['cases']}
        changed_cases = [cid for cid, d in decisions.items()
                         if (d['disposition'], d['au_row_key']) !=
                         (baseline_decisions[cid]['disposition'], baseline_decisions[cid]['au_row_key'])]
        result['variants'][mode] = {
            'diagnostic_only': mode != 'baseline_raw',
            'native_punctuation_ignored': mode == 'native_punctuation',
            'requests': dict(Counter(states.values())),
            'rows': dict(Counter(rows.values())),
            'cases': dict(Counter(d['disposition'] for d in decisions.values())),
            'correct_accept_known': len(accepted), 'wrong_accept_known_case_ids': wrong,
            'accept_reference_unknown_case_ids': accepted_unknown,
            'correct_explicit_reject_known': len(rejected),
            'wrong_explicit_reject_known_case_ids': rejected_wrong,
            'reject_reference_unknown_case_ids': rejected_unknown,
            'changed_from_baseline_case_ids': changed_cases,
            'pruning': pruning_metrics(records['rows'], refs),
            'workload': {'all_contexts': len(all_contexts), 'residual_contexts': len(contexts),
                         'residual_option_comparisons': sum(len(json.loads(c)['option_values']) for c in contexts),
                         'nonconflict_row_candidates': sum(s != 'conflict' for s in rows.values()),
                         'eligible_workload_estimate_only': True}}
    baseline_contexts = result['variants']['baseline_raw']['workload']['residual_contexts']
    for variant in result['variants'].values():
        variant['workload']['context_reduction_from_existing_raw_baseline'] = (
            1 - variant['workload']['residual_contexts'] / baseline_contexts)
    write(run / 'grade.json', result)
    print(json.dumps({k: {'correct_accept': v['correct_accept_known'],
                          'wrong_accept': len(v['wrong_accept_known_case_ids']),
                          'correct_reject': v['correct_explicit_reject_known'],
                          'wrong_reject': len(v['wrong_explicit_reject_known_case_ids']),
                          'false_pruning': v['pruning']['matching_rows_marked_conflict'],
                          'workload': v['workload']}
                       for k, v in result['variants'].items()}, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run', type=Path, required=True)
    grade(**vars(p.parse_args()))
