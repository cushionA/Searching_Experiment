#!/usr/bin/env python3
"""Offline DEV decision-rule ablation on immutable, identical NLI scores.

No inference, threshold search, literal overlay or category rules. The absolute
policy is replayed on OLD short-text inputs, not the new body-chunk protocol.
Numeric row decisions are diagnostics; applicability and citations are unproved.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil

import grade_final_test_subset_v1 as GRADE
from grade_fresh_dev_sku_gate_v1 import validate_dev_membership, V2_ARCHIVE, V3_ARCHIVE
import trial_generic_model_nli_v1 as NLI
import trial_scoped_condition_nli_choice_v1 as CHOICE
import grade_fresh_dev_sku_gate_v1 as DEV
from trial_scoped_condition_nli_choice_v1 import choose, pairs_for
from restore_session_artifacts_20261014 import sha, verify

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / '.lab-output/sku-next-dev-fresh-baseline-20261013-v1/model-run'
ARCHIVE = ROOT / 'experiments/sku-matching/results/20261013-sku-next-dev-checkpoint.zip'
ARCHIVE_SHA256 = 'b585408001f7755fb6bec6cd167fec119f6ed3bbb00724b4fba91a7b4c76b19c'
ARCHIVE_MANIFEST_SHA256 = 'aca0b1de4919cef5f2bf0da001dadd7c31f4afe9a90750066f0a76a61950d099'
POLICIES = ('relative_choice', 'absolute_three_class')
INPUTS = ('manifest.json', 'requests.jsonl', 'documents.jsonl', 'tasks.jsonl',
          'case-plans.jsonl', 'row-plans.jsonl', 'membership.jsonl', 'selection-plan.json')


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


def absolute_state(record):
    count = record.get('token_count_untruncated')
    if (record.get('error_type') or type(count) is not int or count < 1
            or NLI.exceeds_token_limit(count)):
        return 'unknown'
    try:
        probabilities = NLI._probabilities(record)
    except (ValueError, TypeError):
        return 'unknown'
    if probabilities is None:
        return 'unknown'
    winner = max(probabilities, key=probabilities.get)
    return winner if probabilities[winner] >= NLI.THRESHOLD else 'unknown'


def validate_records(records, requests, documents, bindings, skipped_requests=()):
    expected = {p['id']: p for p in pairs_for(requests, documents, bindings)
                if p['request_id'] not in skipped_requests}
    grouped, seen = defaultdict(list), set()
    for record in records:
        identity = record.get('id')
        if identity not in expected or identity in seen:
            raise ValueError('duplicate or foreign original candidate record')
        seen.add(identity)
        pair = expected[identity]
        for field in ('request_id', 'request_sha256', 'option_index', 'option_value', 'premise', 'hypothesis'):
            if record.get(field) != pair[field]:
                raise ValueError('original candidate binding differs: ' + field)
        if (record.get('model_id'), record.get('model_revision'), record.get('precision')) != (
                NLI.MODEL_ID, NLI.MODEL_REVISION, 'fp32'):
            raise ValueError('original NLI model identity differs')
        if record.get('cache_key') != NLI.pair_key(pair['premise'], pair['hypothesis']):
            raise ValueError('original exact-text cache identity differs')
        grouped[record['request_id']].append(record)
    if seen != set(expected):
        raise ValueError('incomplete original all-option scores outside recorded skips')
    for group in grouped.values():
        group.sort(key=lambda r: r['option_index'])
    return grouped


def row_state(states):
    if not states:
        return 'unknown'
    if 'conflict' in states:
        return 'conflict'
    return 'support' if all(s == 'support' for s in states) else 'unknown'


def assemble(rows, requests, states):
    outputs, by_case = [], defaultdict(list)
    for row in rows:
        values = [states[b['request_id']] for b in row['bindings']]
        result = {'case_id': row['case_id'], 'au_row_key': row['au_row_key'],
                  'state': row_state(values), 'condition_count': len(values)}
        outputs.append(result)
        by_case[row['case_id']].append(result)
    decisions = []
    for cid, group in by_case.items():
        supports = [r for r in group if r['state'] == 'support']
        # Unknown competitors never certify exclusivity.
        accepted = len(supports) == 1 and all(
            r is supports[0] or r['state'] == 'conflict' for r in group)
        decisions.append({'case_id': cid, 'decision': 'accept' if accepted else 'drop',
                          'au_row_key': supports[0]['au_row_key'] if accepted else None})
    return outputs, decisions


def pruning_metrics(rows, references):
    by_row = {(r['case_id'], r['au_row_key']): r['state'] for r in rows}
    positive, pruned, all_pruned, outside_support = [], [], [], []
    unknown_cases = []
    for cid, reference in references.items():
        label, keys = GRADE.canonical_reference(reference)
        if label not in ('matched', 'unmatched'):
            unknown_cases.append(cid)
            continue
        if label == 'matched':
            if keys is None:
                raise ValueError('complete original matching-row set required')
            positive.extend((cid, key) for key in keys)
            removed = [(cid, key) for key in keys if by_row[cid, key] == 'conflict']
            pruned.extend(removed)
            if len(removed) == len(keys):
                all_pruned.append(cid)
        allowed = set(keys or [])
        outside_support.extend((case, key) for (case, key), state in by_row.items()
                               if case == cid and state == 'support' and key not in allowed)
    return {'known_matching_row_count': len(positive), 'matching_rows_marked_conflict': len(pruned),
            'matching_row_conflict_rate': len(pruned) / len(positive) if positive else None,
            'positive_cases_losing_all_matching_rows': all_pruned,
            'supported_rows_outside_original_reference': outside_support,
            'unknown_reference_case_ids': unknown_cases}


def run(output):
    if output.exists():
        raise FileExistsError(output)
    manifest_path = ARCHIVE.with_suffix('.zip.manifest.json')
    if sha(manifest_path) != ARCHIVE_MANIFEST_SHA256:
        raise ValueError('original DEV archive manifest identity differs')
    archive_manifest = json.loads(manifest_path.read_text())
    if archive_manifest['archive_sha256'] != ARCHIVE_SHA256:
        raise ValueError('original DEV archive identity differs')
    verify(ARCHIVE, archive_manifest['archive_bytes'], archive_manifest['archive_sha256'])
    pinned = {r['path']: r for r in archive_manifest['members']}
    source, scores = BASE / 'source-inputs', BASE / 'model-run'
    paths = [source / n for n in INPUTS] + [scores / n for n in
        ('freeze.json', 'summary.json', 'nli-classification-options.jsonl', 'nli-skips.jsonl')]
    for path in paths:
        entry = pinned[path.relative_to(ROOT).as_posix()]
        verify(path, entry['bytes'], entry['sha256'])
    membership, cases, _ = validate_dev_membership(source / 'membership.jsonl',
        source / 'case-plans.jsonl', source / 'row-plans.jsonl', source / 'selection-plan.json')
    requests = read(source / 'requests.jsonl')
    documents = {r['document_id']: r for r in read(source / 'documents.jsonl')}
    tasks, rows = read(source / 'tasks.jsonl'), read(source / 'row-plans.jsonl')
    bindings = defaultdict(list)
    for task in tasks:
        bindings[task['request_id']].append(task)
    if (len(rows), len(tasks), len(requests)) != (504, 1924, 1045):
        raise ValueError('frozen DEV all-row/all-condition coverage differs')
    task_index = GRADE.unique_index(tasks, 'task_id', 'DEV tasks')
    request_index = GRADE.unique_index(requests, 'request_id', 'DEV requests')
    bound = []
    for row in rows:
        for binding in row['bindings']:
            task = task_index[binding['task_id']]
            if (task['case_id'], task['au_row_key'], task['request_id']) != (
                    row['case_id'], row['au_row_key'], binding['request_id']):
                raise ValueError('row/condition binding differs')
            bound.append(task['task_id'])
    if len(bound) != len(set(bound)) or set(bound) != set(task_index) or set(bindings) != set(request_index):
        raise ValueError('complete unique task/request coverage required')
    skips = GRADE.unique_index(read(scores / 'nli-skips.jsonl'), 'request_id', 'original NLI skips')
    for rid, skip in skips.items():
        if (rid not in request_index or skip.get('source_request') != request_index[rid]
                or skip.get('skipped_pair_count') != len(request_index[rid]['option_values'])):
            raise ValueError('original prefilter skip binding differs')
    if (len(skips) != 11 or sum(s['skipped_pair_count'] for s in skips.values()) != 43
            or any(s.get('reason') != 'jev_unknown_control' for s in skips.values())):
        raise ValueError('original JEV prefilter skip population differs')
    grouped = validate_records(read(scores / 'nli-classification-options.jsonl'), requests, documents, bindings, skips)
    output.mkdir(parents=True, exist_ok=False)
    for name in INPUTS:
        shutil.copyfile(source / name, output / name)
    artifacts, counts = {}, {}
    for policy in POLICIES:
        predictions = []
        for request in requests:
            records = grouped.get(request['request_id'], [])
            if policy == 'relative_choice':
                decision = choose(request['option_values'], request['selected_value'], records)
                state = decision['state']
            else:
                selected = next((r for r in records if r['option_value'] == request['selected_value']), None)
                state = absolute_state(selected) if selected is not None else 'unknown'
                decision = {'state': state, 'reason': 'independent_absolute_probability'}
            predictions.append({'request_id': request['request_id'], **decision})
        states = {r['request_id']: r['state'] for r in predictions}
        row_outputs, decisions = assemble(rows, requests, states)
        for suffix, values in [('requests', predictions), ('rows', row_outputs), ('cases', decisions)]:
            path = output / f'{policy}-{suffix}.jsonl'
            write_rows(path, values)
            artifacts[path.name] = sha(path)
        counts[policy] = {'request_states': dict(Counter(states.values())),
                         'row_states': dict(Counter(r['state'] for r in row_outputs)),
                         'coverage': sum(s != 'unknown' for s in states.values()) / len(requests)}
    freeze = {'schema': 'sku-dev-nli-policy-replay-v1', 'split': 'dev', 'independent_test': False,
        'new_inference': False, 'threshold_search': False, 'literal_overlay': False,
        'production_adoption_allowed': False, 'scope_status': 'unproved', 'citation_semantics_supported': False,
        'input_kind': 'unchanged_old_title_and_selected_row',
        'standalone_nli_capability_test': False,
        'legacy_jev_prefilter_skipped_requests': len(skips),
        'legacy_jev_prefilter_unscored_options': sum(s['skipped_pair_count'] for s in skips.values()),
        'unscored_request_policy': 'all original prefilter skips retained as unknown for both policies',
        'not_a_replay_of_new_body_chunk_inputs': True,
        'thresholds': {'absolute': NLI.THRESHOLD, 'relative': .5},
        'case_count': len(cases), 'row_count': len(rows), 'condition_count': len(tasks),
        'request_count': len(requests), 'original_option_count': sum(len(r['option_values']) for r in requests),
        'source_pins': {str(p.relative_to(ROOT)): sha(p) for p in paths},
        'code_pins': {Path(m.__file__).name: sha(Path(m.__file__)) for m in (NLI, GRADE, CHOICE, DEV)},
        'script_sha256': sha(Path(__file__)), 'outputs': artifacts, 'counts': counts}
    write(output / 'output-freeze.json', freeze)
    for name, digest in artifacts.items():
        if sha(output / name) != digest:
            raise ValueError('frozen replay output changed before grading')
    # Original answer envelopes, never historical singleton-derived caches.
    references = GRADE.read_label_archives(V2_ARCHIVE, V3_ARCHIVE, membership, source / 'row-plans.jsonl')
    scoring_members = [{**m, 'split': m['source_version']} for m in membership]
    report = {'freeze_sha256': sha(output / 'output-freeze.json'),
        'reference_kind': 'Claude Haiku 5.5 machine annotations; not human gold',
        'reference_archives': {str(p.relative_to(ROOT)): sha(p) for p in (V2_ARCHIVE, V3_ARCHIVE)},
        'original_multiple_rowsets': True, 'known_negative_cases': sum(
            GRADE.canonical_reference(r)[0] == 'unmatched' for r in references.values()), 'policies': {}}
    for policy in POLICIES:
        report['policies'][policy] = {'score': GRADE.score_variant(scoring_members,
            read(output / f'{policy}-cases.jsonl'), references),
            'pruning': pruning_metrics(read(output / f'{policy}-rows.jsonl'), references)}
    write(output / 'grade.json', report)
    print(json.dumps({'output': str(output), 'counts': counts,
        'scores': {k: v['score']['metrics'] for k, v in report['policies'].items()}}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
