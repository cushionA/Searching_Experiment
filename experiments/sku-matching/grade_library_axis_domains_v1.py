#!/usr/bin/env python3
"""Evaluate all frozen axis-domain variants without changing any hypothesis."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import grade_final_test_subset_v1 as G
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE, V3_ARCHIVE
from run_installed_library_selection_v1 import read, sha, write

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.lab-output/sku-classical-parser-20261015-v1/population-inputs'
POP = ROOT / '.lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs'
BASE = ROOT / '.lab-output/sku-installed-library-matching-20261015-v1/price-free/selection-v1'
COMP = ROOT / '.lab-output/sku-library-components-20261015-v1/run-v3'
EXPECTED = ('mutual_unique_raw', 'mutual_unique_spacy', 'forced_maximum_raw', 'forced_maximum_spacy',
            'mutual_unique_raw_components', 'mutual_unique_spacy_components',
            'forced_maximum_raw_components', 'forced_maximum_spacy_components')
CASE_SHA = '06898908758f6a6dfe3446ee63c7688c0e68d4928dde7dbfe05a821e2ef63a09'
PRODUCT_SHA = '75353ed9c40333bef064109d8c99a0baf72c707564724ca1bd9481ccae539926'


def pruning(records, refs):
    """Original pruning definitions, indexed by case to avoid quadratic scans."""
    groups = defaultdict(dict)
    for r in records:
        groups[r['case_id']][r['au_row_key']] = r['state']
    positive, removed, all_removed, outside, unknown = [], [], [], [], []
    for cid, reference in refs.items():
        label, allowed = G.canonical_reference(reference)
        if label not in ('matched', 'unmatched'):
            unknown.append(cid)
            continue
        allowed = set(allowed or [])
        if label == 'matched':
            positive.extend((cid, key) for key in sorted(allowed))
            bad = [(cid, key) for key in sorted(allowed) if groups[cid][key] == 'conflict']
            removed.extend(bad)
            if len(bad) == len(allowed):
                all_removed.append(cid)
        outside.extend((cid, key) for key, state in groups[cid].items()
                       if state == 'support' and key not in allowed)
    return {'known_matching_row_count': len(positive), 'matching_rows_marked_conflict': len(removed),
            'matching_row_conflict_rate': len(removed) / len(positive) if positive else None,
            'positive_cases_losing_all_matching_rows': all_removed,
            'supported_rows_outside_original_reference': outside,
            'unknown_reference_case_ids': unknown}


def grade(run):
    run = run.resolve()
    freeze = json.loads((run / 'OUTPUT_FREEZE.json').read_text())
    verified = {}

    def check(path, pin):
        observed = sha(path)
        if observed != pin:
            raise ValueError(f'Frozen input/output/code differs: {path}')
        verified[str(path.relative_to(ROOT))] = observed

    for name, pin in freeze['input_pins'].items():
        check(ROOT / name, pin)
    for name, pin in freeze['output_pins'].items():
        check(run / name, pin)
    for name, pin in freeze['code_pins'].items():
        check(Path(__file__).with_name(name), pin)
    check(POP / 'cases.jsonl', CASE_SHA)
    check(POP / 'products.jsonl', PRODUCT_SHA)
    sources = {'baseline_raw': (BASE, 'raw'), 'previous_components': (COMP, 'native_punctuation')}
    sources.update({mode: (run, mode) for mode in EXPECTED})
    bf = json.loads((BASE / 'OUTPUT_FREEZE.json').read_text())
    cf = json.loads((COMP / 'OUTPUT_FREEZE.json').read_text())
    for directory, prefix, frozen in ((BASE, 'raw', bf), (COMP, 'native_punctuation', cf)):
        for kind in ('requests', 'rows', 'cases'):
            name = f'{prefix}-{kind}.jsonl'
            check(directory / name, frozen['output_pins'][name])
    requests, rows, cases = (read(SOURCE / name) for name in
                             ('requests.jsonl', 'row-plans.jsonl', 'case-plans.jsonl'))
    if (len(requests), len(rows), len(cases), sum(len(r['bindings']) for r in rows)) != (30169, 32325, 1370, 132136):
        raise ValueError('Original population or full condition coverage differs')
    request_ids, case_ids = {r['request_id'] for r in requests}, {c['case_id'] for c in cases}
    row_keys = [(r['case_id'], r['au_row_key']) for r in rows]
    modes = {mode: {kind: read(directory / f'{prefix}-{kind}.jsonl')
                   for kind in ('requests', 'rows', 'cases')}
             for mode, (directory, prefix) in sources.items()}
    original_states = {r['request_id']: r['state'] for r in modes['baseline_raw']['requests']}
    for mode, records in modes.items():
        if len(records['requests']) != len(request_ids) or {r['request_id'] for r in records['requests']} != request_ids:
            raise ValueError(f'Request membership differs: {mode}')
        if [(r['case_id'], r['au_row_key']) for r in records['rows']] != row_keys:
            raise ValueError(f'Original row membership/order differs: {mode}')
        if len(records['cases']) != len(case_ids) or {r['case_id'] for r in records['cases']} != case_ids:
            raise ValueError(f'Case membership differs: {mode}')
        if any(original_states[r['request_id']] != 'unknown' and r['state'] != original_states[r['request_id']]
               for r in records['requests']):
            raise ValueError(f'Known baseline state overwritten: {mode}')
    native_cases = read(POP / 'cases.jsonl')
    membership = [{'case_id': c['case_id'], 'source_version':
                   'v2' if '/eval-v2/' in c['source_record']['file'].replace('\\', '/') else 'v3'}
                  for c in native_cases]
    # Reference archives are opened only after every predeclared output is frozen and verified.
    refs = G.read_label_archives(V2_ARCHIVE, V3_ARCHIVE, membership, SOURCE / 'row-plans.jsonl')
    context_fields = ('direction', 'axis_name', 'option_values', 'target_document_id', 'target_selected_axes')

    def context(r):
        return json.dumps({k: r[k] for k in context_fields}, ensure_ascii=False, sort_keys=True)

    all_contexts = {context(r) for r in requests}
    plans = {c['case_id']: c for c in cases}
    previous_decisions = {c['case_id']: c for c in modes['previous_components']['cases']}
    baseline_decisions = {c['case_id']: c for c in modes['baseline_raw']['cases']}
    result = {'schema': 'library-axis-domains-dev-grade-v1', 'independent_test': False,
              'reference_kind': 'Claude Haiku5.5 machine annotation, not human gold',
              'grader_code_sha256': sha(Path(__file__)),
              'prediction_freeze_sha256': sha(run / 'OUTPUT_FREEZE.json'),
              'verified_before_labels': verified, 'rules_or_thresholds_fitted_on_this_grade': False,
              'attribute_level_ground_truth_available': False,
              'primary_predeclared_variant': 'mutual_unique_raw_components',
              'production_adoption_allowed': False, 'actual_ai_tokens_cost_or_calls_measured': False,
              'variants': {}}
    for mode, records in modes.items():
        states = {r['request_id']: r['state'] for r in records['requests']}
        row_states = {(r['case_id'], r['au_row_key']): r['state'] for r in records['rows']}
        decisions = {c['case_id']: c for c in records['cases']}
        good, bad, unknown_accept, no_match, bad_reject, unknown_reject = [], [], [], [], [], []
        for cid, decision in decisions.items():
            label, allowed = G.canonical_reference(refs[cid])
            if decision['disposition'] == 'accept':
                if label not in ('matched', 'unmatched'):
                    unknown_accept.append(cid)
                elif label == 'matched' and decision['au_row_key'] in allowed:
                    good.append(cid)
                else:
                    bad.append(cid)
            elif decision['disposition'] == 'reject':
                (no_match if label == 'unmatched' else bad_reject if label == 'matched' else unknown_reject).append(cid)
        remaining_ids = {b['request_id'] for row in rows
                         if decisions[row['case_id']]['disposition'] == 'review'
                         and row_states[row['case_id'], row['au_row_key']] != 'conflict'
                         for b in row['bindings'] if states[b['request_id']] == 'unknown'}
        contexts = {context(r) for r in requests if r['request_id'] in remaining_ids}
        changed = [cid for cid, decision in decisions.items()
                   if (decision['disposition'], decision['au_row_key']) !=
                   (previous_decisions[cid]['disposition'], previous_decisions[cid]['au_row_key'])]
        gained = [cid for cid in good if previous_decisions[cid]['disposition'] != 'accept']
        base_gain = [cid for cid in good if baseline_decisions[cid]['disposition'] != 'accept']
        result['variants'][mode] = {
            'counts': {'requests': dict(Counter(states.values())),
                       'rows': dict(Counter(row_states.values())),
                       'cases': dict(Counter(c['disposition'] for c in decisions.values()))},
            'correct_accept_known': len(good), 'wrong_accept_known_case_ids': bad,
            'accept_reference_unknown_case_ids': unknown_accept,
            'correct_explicit_reject_known': len(no_match),
            'wrong_explicit_reject_known_case_ids': bad_reject,
            'reject_reference_unknown_case_ids': unknown_reject,
            'pruning': pruning(records['rows'], refs),
            'correct_accept_gain_from_previous_components': len(gained),
            'gain_cases_by_product': dict(Counter(plans[c]['dossier_id'] for c in gained)),
            'gain_product_count': len({plans[c]['dossier_id'] for c in gained}),
            'correct_accept_gain_from_baseline_raw': len(base_gain),
            'gain_from_baseline_product_count': len({plans[c]['dossier_id'] for c in base_gain}),
            'changed_from_previous_components_case_ids': changed,
            'workload': {'all_contexts': len(all_contexts), 'residual_contexts': len(contexts),
                         'residual_option_comparisons': sum(len(json.loads(c)['option_values']) for c in contexts),
                         'nonconflict_row_candidates': sum(s != 'conflict' for s in row_states.values()),
                         'eligible_workload_estimate_only': True}}
    for variant in result['variants'].values():
        for comparison in ('baseline_raw', 'previous_components'):
            before = result['variants'][comparison]['workload']['residual_contexts']
            variant['workload']['context_reduction_from_' + comparison] = (
                1 - variant['workload']['residual_contexts'] / before)
    write(run / 'grade.json', result)
    print(json.dumps({mode: {'correct_accept': value['correct_accept_known'],
                             'wrong_accept': len(value['wrong_accept_known_case_ids']),
                             'correct_reject': value['correct_explicit_reject_known'],
                             'wrong_reject': len(value['wrong_explicit_reject_known_case_ids']),
                             'matching_rows_pruned': value['pruning']['matching_rows_marked_conflict'],
                             'gain_product_count': value['gain_product_count'],
                             'workload': value['workload']}
                       for mode, value in result['variants'].items()}, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run', type=Path, required=True)
    grade(**vars(p.parse_args()))
