#!/usr/bin/env python3
"""Compare frozen full-body and specification-table-first DEV40 predictions."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

from prepare_v7_small_nli_single_v1 import read, write
from restore_session_artifacts_20261014 import sha
import grade_final_test_subset_v1 as GRADE
from grade_fresh_dev_sku_gate_v1 import V2_ARCHIVE, V3_ARCHIVE


def indexed(rows, fields):
    result = {tuple(row[k] for k in fields): row for row in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate comparison identity')
    return result


def transitions(before, after, fields, subset):
    a, b = indexed(before, fields), indexed(after, fields)
    if set(a) != set(b):
        raise ValueError('comparison prediction populations differ')
    counts = Counter()
    changed = []
    for key in a:
        if subset(key):
            counts[a[key]['state'] + '->' + b[key]['state']] += 1
            if a[key]['state'] != b[key]['state']:
                changed.append({'identity': key, 'before': a[key]['state'], 'after': b[key]['state']})
    return {'counts': dict(counts), 'changed_count': len(changed), 'changes': changed}


def verify_frozen_grade(experiment):
    grade_dir = experiment / 'grade-v1'
    grade = json.loads((grade_dir / 'grade.json').read_text())
    freeze_path = grade_dir / 'OUTPUT_FREEZE.json'
    if sha(freeze_path) != grade['output_freeze_sha256']:
        raise ValueError('grade output freeze differs')
    freeze = json.loads(freeze_path.read_text())
    for name, digest in freeze['outputs'].items():
        if sha(grade_dir / name) != digest:
            raise ValueError('frozen prediction differs: ' + name)
    for name, digest in freeze['raw_output_files'].items():
        if sha(grade_dir / 'first-pass-raw' / name) != digest:
            raise ValueError('frozen raw output differs: ' + name)
    host = json.loads((experiment / 'HOST_INPUT_FREEZE.json').read_text())
    if sha(experiment / 'HOST_INPUT_FREEZE.json') != freeze['host_input_freeze_sha256']:
        raise ValueError('grade belongs to different frozen inputs')
    for name, digest in host['host_pins'].items():
        if sha(experiment / 'host' / name) != digest:
            raise ValueError('frozen host input differs: ' + name)
    job = json.loads((experiment / 'job/job.json').read_text())
    continuation = json.loads((experiment / 'job/continuation.json').read_text())
    notebook = json.loads((experiment / 'job/artifacts/NOTEBOOK_RECEIPT.json').read_text())
    if (continuation['version'] != job['version'] or continuation['ref'] != job['ref']
            or continuation['status'] != 'complete' or not continuation['ready_for_verification']
            or notebook['kernel_ref'] != job['ref']
            or notebook['bundle_manifest_sha256'] != host['bundle_manifest_sha256']):
        raise ValueError('fixed recovered job/grade identity differs')
    return grade


def compare(baseline: Path, experiment: Path, output: Path):
    a = read(baseline / 'public-bundle/contexts.jsonl')
    b = read(experiment / 'public-bundle/contexts.jsonl')
    ai, bi = indexed(a, ('context_ref',)), indexed(b, ('context_ref',))
    if list(ai) != list(bi):
        raise ValueError('context identity/order differs')
    changed = set()
    for key, row in ai.items():
        if ({k: v for k, v in row.items() if k != 'body_text'} !=
                {k: v for k, v in bi[key].items() if k != 'body_text'}):
            raise ValueError('a nonbody public field changed')
        if row['body_text'] != bi[key]['body_text']:
            changed.add(key[0])
    baseline_host = json.loads((baseline / 'HOST_INPUT_FREEZE.json').read_text())
    for name in baseline_host['host_pins']:
        if sha(baseline / 'host' / name) != sha(experiment / 'host' / name):
            raise ValueError('original host cohort/condition binding changed: ' + name)
    ag, bg = baseline / 'grade-v1', experiment / 'grade-v1'
    ea = json.loads((ag / 'first-pass-raw/EXECUTION_FREEZE.json').read_text())
    eb = json.loads((bg / 'first-pass-raw/EXECUTION_FREEZE.json').read_text())
    for name in ('code_sha256', 'model_pin', 'policy', 'runtime_policy', 'text_policy'):
        if ea[name] != eb[name]:
            raise ValueError('fixed worker/model/decision policy differs: ' + name)
    sa, sb = verify_frozen_grade(baseline), verify_frozen_grade(experiment)
    oa, ob = read(ag / 'first-pass-raw/option-states.jsonl'), read(bg / 'first-pass-raw/option-states.jsonl')
    bindings = {r['request_id']: r for r in read(experiment / 'host/request-bindings.jsonl')}
    touched_requests = {rid for rid, r in bindings.items() if r['context_ref'] in changed}
    rows = read(experiment / 'host/row-plans.jsonl')
    touched_rows = {(r['case_id'], r['au_row_key']) for r in rows
                    if any(v['request_id'] in touched_requests for v in r['bindings'])}
    touched_cases = sorted({cid for cid, _ in touched_rows})
    # Both complete prediction sets have already been frozen and graded.
    # Reuse the pinned original-rowset reader for identity-level exclusion audit.
    references = GRADE.read_label_archives(V2_ARCHIVE, V3_ARCHIVE,
        read(experiment / 'host/membership.jsonl'), experiment / 'host/row-plans.jsonl')
    correct_rows = set()
    for cid, reference in references.items():
        label, keys = GRADE.canonical_reference(reference)
        if label == 'matched':
            if keys is None:
                raise ValueError('complete reference rowset required')
            correct_rows.update((cid, key) for key in keys)
    result = {'schema': 'sku-spec-table-first-paired-comparison-v1', 'source_split': 'dev',
        'independent_test': False, 'production_adoption_allowed': False,
        'reference_kind': sb['reference_kind'], 'same_nonbody_inputs': True,
        'same_worker_model_and_decision_policy': True,
        'baseline_job_version': json.loads((baseline / 'job/job.json').read_text())['version'],
        'experiment_job_version': json.loads((experiment / 'job/job.json').read_text())['version'],
        'baseline_grade_sha256': sha(ag / 'grade.json'), 'experiment_grade_sha256': sha(bg / 'grade.json'),
        'changed_body_context_count': len(changed), 'unchanged_body_context_count': len(a) - len(changed),
        'touched_request_count': len(touched_requests), 'touched_row_count': len(touched_rows),
        'touched_case_ids': touched_cases,
        'baseline_gpu': sa['runtime']['cuda_runtime']['gpu_name'],
        'experiment_gpu': sb['runtime']['cuda_runtime']['gpu_name'],
        'gpu_name_changed': sa['runtime']['cuda_runtime']['gpu_name'] != sb['runtime']['cuda_runtime']['gpu_name'],
        'runtime_metadata_equal': sa['runtime']['cuda_runtime'] == sb['runtime']['cuda_runtime'], 'views': {}}
    for view in ('title_row', 'body'):
        av, bv = [r for r in oa if r['view'] == view], [r for r in ob if r['view'] == view]
        option = {group: transitions(av, bv, ('context_ref', 'option_index'), selector)
                  for group, selector in (('changed_body', lambda key: key[0] in changed),
                                          ('unchanged_body', lambda key: key[0] not in changed))}
        request = {group: transitions(read(ag / f'{view}-requests.jsonl'), read(bg / f'{view}-requests.jsonl'),
                      ('request_id',), selector)
                   for group, selector in (('touched', lambda key: key[0] in touched_requests),
                                           ('untouched', lambda key: key[0] not in touched_requests))}
        row = {group: transitions(read(ag / f'{view}-rows.jsonl'), read(bg / f'{view}-rows.jsonl'),
                    ('case_id', 'au_row_key'), selector)
               for group, selector in (('touched', lambda key: key in touched_rows),
                                       ('untouched', lambda key: key not in touched_rows))}
        ac, bc = indexed(read(ag / f'{view}-cases.jsonl'), ('case_id',)), indexed(read(bg / f'{view}-cases.jsonl'), ('case_id',))
        if set(ac) != set(bc):
            raise ValueError('final case populations differ')
        removed = sorted(set(sa['variants'][view]['pruning']['positive_cases_losing_all_matching_rows']) -
                         set(sb['variants'][view]['pruning']['positive_cases_losing_all_matching_rows']))
        added = sorted(set(sb['variants'][view]['pruning']['positive_cases_losing_all_matching_rows']) -
                       set(sa['variants'][view]['pruning']['positive_cases_losing_all_matching_rows']))
        ar = indexed(read(ag / f'{view}-rows.jsonl'), ('case_id', 'au_row_key'))
        br = indexed(read(bg / f'{view}-rows.jsonl'), ('case_id', 'au_row_key'))
        restored_rows = sorted(key for key in correct_rows if ar[key]['state'] == 'conflict' and br[key]['state'] != 'conflict')
        newly_conflicted_rows = sorted(key for key in correct_rows if ar[key]['state'] != 'conflict' and br[key]['state'] == 'conflict')
        result['views'][view] = {'baseline': sa['variants'][view], 'spec_first': sb['variants'][view],
            'option_transitions': option, 'request_transitions': request, 'row_transitions': row,
            'case_decision_changes': [key[0] for key in ac if ac[key] != bc[key]],
            'restored_original_matching_rows': restored_rows,
            'newly_conflicted_original_matching_rows': newly_conflicted_rows,
            'removed_complete_correct_row_exclusion_case_ids': removed,
            'added_complete_correct_row_exclusion_case_ids': added}
    write(output, result)
    print(json.dumps({'changed_body_contexts': len(changed), 'touched_cases': len(touched_cases),
        'views': {v: {'before_metrics': x['baseline']['score']['metrics'],
                     'after_metrics': x['spec_first']['score']['metrics'],
                     'before_correct_row_conflict': x['baseline']['pruning']['matching_rows_marked_conflict'],
                     'after_correct_row_conflict': x['spec_first']['pruning']['matching_rows_marked_conflict'],
                     'changed_option_transitions': x['option_transitions']['changed_body']['counts'],
                     'unchanged_option_changed_count': x['option_transitions']['unchanged_body']['changed_count']}
                  for v, x in result['views'].items()}}, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'experiment', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    compare(**vars(parser.parse_args()))
