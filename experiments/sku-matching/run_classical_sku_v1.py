#!/usr/bin/env python3
"""Freeze label-free classical SKU decisions; grade in a separate invocation."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

import sku_classical_quantity_v1 as Q
import sku_classical_matcher_v1 as M

VARIANTS = ('literal', 'typed')
CODE = ('sku_classical_quantity_v1.py', 'sku_classical_matcher_v1.py',
        'run_classical_sku_v1.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def write(path, data):
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n')


def rows_write(path, rows):
    with path.open('x', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)+'\n')


def index(rows, key):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate '+key)
    return result


def assemble(rows, states):
    outputs, cases = [], defaultdict(list)
    for row in rows:
        values = [states[b['request_id']] for b in row['bindings']]
        state = ('conflict' if 'conflict' in values else
                 'support' if values and all(s == 'support' for s in values) else 'unknown')
        output = {'case_id': row['case_id'], 'au_row_key': row['au_row_key'],
                  'state': state, 'condition_count': len(values)}
        outputs.append(output)
        cases[row['case_id']].append(output)
    decisions = []
    for cid, group in cases.items():
        support = [r for r in group if r['state'] == 'support']
        accept = len(support) == 1 and all(r is support[0] or r['state'] == 'conflict' for r in group)
        # Historical binary score drops unresolved cases. Keep disposition so a
        # drop cannot be mistaken for a proof that no matching row exists.
        disposition = ('accept' if accept else 'reject' if all(r['state'] == 'conflict' for r in group)
                       else 'review')
        decisions.append({'case_id': cid, 'decision': 'accept' if accept else 'drop',
                          'disposition': disposition,
                          'au_row_key': support[0]['au_row_key'] if accept else None})
    return outputs, decisions


def run(source, output):
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    inputs = {n: source/n for n in ('requests.jsonl', 'documents.jsonl', 'tasks.jsonl',
                                   'row-plans.jsonl', 'case-plans.jsonl')}
    input_pins = {n: sha(p) for n, p in inputs.items()}
    code_pins = {n: sha(Path(__file__).with_name(n)) for n in CODE}
    requests, docs, tasks, rows, cases = (read(inputs[n]) for n in inputs)
    ri, ti, ci = index(requests, 'request_id'), index(tasks, 'task_id'), index(cases, 'case_id')
    index(docs, 'document_id')
    bound = []
    rowsets = defaultdict(list)
    for row in rows:
        if row['case_id'] not in ci:
            raise ValueError('foreign row case')
        rowsets[row['case_id']].append(row['au_row_key'])
        for b in row['bindings']:
            t = ti[b['task_id']]
            if b['request_id'] not in ri or (t['case_id'], t['au_row_key'], t['request_id']) != (
                    row['case_id'], row['au_row_key'], b['request_id']):
                raise ValueError('original row/condition/request binding differs')
            bound.append(b['task_id'])
    if len(bound) != len(set(bound)) or set(bound) != set(ti):
        raise ValueError('all original conditions must be bound exactly once')
    if set(ri) != {t['request_id'] for t in tasks}:
        raise ValueError('request condition coverage differs')
    for cid, c in ci.items():
        keys = rowsets[cid]
        if len(keys) != len(set(keys)) or set(keys) != set(c['row_keys']):
            raise ValueError('all original AU rows must be retained')
    start = time.perf_counter()
    values = sorted({v for r in requests for v in r['option_values']} |
                    {a['value'] for r in requests for a in r['target_selected_axes']})
    parsed = [Q.parse_value(v) for v in values]
    rows_write(output/'parsed-values.jsonl', parsed)
    families = M.build_families(requests, docs)
    write(output/'native-families.json', {'schema': M.SCHEMA,
        'source_families': families['source_families'], 'request_errors': families['request_errors']})
    counts, results, diagnostics = {}, {}, {}
    for mode in VARIANTS:
        result = [M.evaluate_request(r, families, mode=mode) for r in requests]
        states = {r['request_id']: r['state'] for r in result}
        row_output, decisions = assemble(rows, states)
        results[mode] = result
        for kind, data in (('requests', result), ('rows', row_output), ('cases', decisions)):
            rows_write(output/f'{mode}-{kind}.jsonl', data)
        counts[mode] = {'requests': dict(Counter(states.values())),
                        'rows': dict(Counter(r['state'] for r in row_output)),
                        'cases': dict(Counter(r['disposition'] for r in decisions)),
                        'reason_counts': dict(Counter(reason for r in result for reason in r['reasons']))}
        for r in result:
            req = ri[r['request_id']]
            pair = families['request_pairs'][r['request_id']]
            key = (pair.get('source_document_id'), req['target_document_id'], req['axis_name'], mode)
            if key not in diagnostics:
                diagnostics[key] = {'source_document_id': key[0], 'target_document_id': key[1],
                    'axis_name': key[2], 'mode': mode, 'family_state': r['family_state'],
                    'family_reasons': r.get('family_reasons'),
                    'source_values': (pair.get('source_family') or {}).get('family_values'),
                    'target_values': (pair.get('target_family') or {}).get('family_values'),
                    'suggestions': r.get('candidate_suggestions')}
    rows_write(output/'family-diagnostics.jsonl', diagnostics.values())
    changes = [{'request_id': a['request_id'], 'literal_state': a['state'], 'typed_state': b['state'],
                'axis_name': b['axis_name'], 'selected_value': b['selected_source_value'],
                'target_value': b['target_selected_value'], 'typed_reasons': b['reasons']}
               for a, b in zip(results['literal'], results['typed']) if a['state'] != b['state']]
    rows_write(output/'condition-changes.jsonl', changes)
    unresolved = defaultdict(list)
    for req, state in zip(requests, results['typed']):
        if state['state'] != 'unknown':
            continue
        context = {k: req[k] for k in ('direction', 'axis_name', 'option_values',
                                       'target_document_id', 'target_selected_axes')}
        key = json.dumps(context, ensure_ascii=False, sort_keys=True)
        unresolved[key].append(req['request_id'])
    rows_write(output/'residual-context-queue.jsonl',
        ({'context': json.loads(k), 'unresolved_request_ids': ids,
          'model_dispatch_allowed': False, 'purpose': 'planning, not semantic evidence'}
         for k, ids in unresolved.items()))
    for n, p in inputs.items():
        if sha(p) != input_pins[n]:
            raise ValueError('input changed during execution')
    for n, pin in code_pins.items():
        if sha(Path(__file__).with_name(n)) != pin:
            raise ValueError('code changed during execution')
    summary = {'schema': 'classical-sku-output-freeze-v1', 'labels_read': False,
        'independent_test': False, 'source_split': 'dev', 'model_inference_count': 0,
        'production_adoption_allowed': False, 'description_scope_proved': False,
        'candidate_similarity_used_as_evidence': False, 'code_pins': code_pins,
        'source_path': str(source.resolve()), 'input_pins': input_pins,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'counts': {'cases': len(cases), 'documents': len(docs), 'rows': len(rows),
                   'conditions': len(tasks), 'requests': len(requests), 'unique_values': len(values)},
        'variants': counts, 'condition_change_count': len(changes),
        'residual_context_count': len(unresolved),
        'physical_unit_value_count': sum(any(t.get('quantity_kind') == 'physical' for t in p['tokens']) for p in parsed),
        'count_value_count': sum(any(t.get('quantity_kind') == 'count' for t in p['tokens']) for p in parsed),
        'elapsed_seconds': time.perf_counter()-start,
        'runtime': {n: importlib.metadata.version(n) for n in ('Pint', 'jaconv', 'RapidFuzz')},
        'output_pins': {p.name: sha(p) for p in output.iterdir() if p.is_file()}}
    write(output/'OUTPUT_FREEZE.json', summary)
    print(json.dumps({k: summary[k] for k in ('counts', 'variants', 'condition_change_count',
                                             'residual_context_count', 'elapsed_seconds')}, ensure_ascii=False))


def grade(source, output):
    import grade_final_test_subset_v1 as G
    from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE, V3_ARCHIVE, validate_dev_membership
    from replay_dev_nli_policies_v1 import pruning_metrics
    freeze = json.loads((output/'OUTPUT_FREEZE.json').read_text())
    if freeze['source_path'] != str(source.resolve()):
        raise ValueError('source identity differs')
    for name, pin in freeze['input_pins'].items():
        if sha(source/name) != pin:
            raise ValueError('input changed after freeze')
    for name, pin in freeze['output_pins'].items():
        if sha(output/name) != pin:
            raise ValueError('output changed after freeze')
    for name, pin in freeze['code_pins'].items():
        if sha(Path(__file__).with_name(name)) != pin:
            raise ValueError('code changed after freeze')
    membership, _, _ = validate_dev_membership(source/'membership.jsonl', source/'case-plans.jsonl',
                                                source/'row-plans.jsonl', source/'selection-plan.json')
    refs = G.read_label_archives(V2_ARCHIVE, V3_ARCHIVE, membership, source/'row-plans.jsonl')
    members = [{**m, 'split': m['source_version']} for m in membership]
    result = {'schema': 'classical-sku-dev40-grade-v1', 'independent_test': False,
              'reference_kind': 'Claude Haiku5.5 machine annotation, not human gold',
              'output_freeze_sha256': sha(output/'OUTPUT_FREEZE.json'), 'variants': {}}
    for mode in VARIANTS:
        decisions = read(output/f'{mode}-cases.jsonl')
        score = G.score_variant(members, decisions, refs)
        accepted_known = [d for d in decisions if d['decision']=='accept' and G.canonical_reference(refs[d['case_id']])[0] in ('matched','unmatched')]
        correct = sum(d['au_row_key'] in (G.canonical_reference(refs[d['case_id']])[1] or []) for d in accepted_known)
        result['variants'][mode] = {'score': score,
            'pruning': pruning_metrics(read(output/f'{mode}-rows.jsonl'), refs),
            'accepted_known': len(accepted_known), 'correct_accepted_known': correct,
            'precision_among_known_accepts': correct/len(accepted_known) if accepted_known else None,
            'false_match_rate_among_known_accepts': 1-correct/len(accepted_known) if accepted_known else None,
            'accepted_unknown_reference_case_ids': [d['case_id'] for d in decisions if d['decision']=='accept' and G.canonical_reference(refs[d['case_id']])[0] not in ('matched','unmatched')]}
    write(output/'grade.json', result)
    print(json.dumps({m: {k:v for k,v in r.items() if k!='score'} | {'metrics': r['score']['metrics']}
                      for m,r in result['variants'].items()}, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('run', 'grade'))
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    globals()[a.action](a.source, a.output)
