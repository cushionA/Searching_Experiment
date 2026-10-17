#!/usr/bin/env python3
"""Host-only scoring after private Kaggle output verification; no rule tuning."""
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path

from benchmark_sudachi_dice_volume_v1 import frozen_functions, sha, write_gzip

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.lab-output/sku-dice-volume-20261017-v1'
OLD = ROOT / '.lab-output/sku-dictionary-half-20261015-v1'


def read_lines(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return [json.loads(s) for s in f if s.strip()]


def grade():
    freezes = list((LAB / 'job-v2/artifacts').rglob('FREEZE.json'))
    if len(freezes) != 1:
        raise ValueError('expected exactly one remote receipt')
    folder = freezes[0].parent
    receipt = json.loads(freezes[0].read_text())
    if receipt['input_manifest_sha256'] != sha(LAB / 'dataset-upload/MANIFEST.json'):
        raise ValueError('wrong mounted input')
    if receipt['worker_code_sha256'] != sha(Path(__file__).with_name('benchmark_sudachi_dice_volume_v1.py')):
        raise ValueError('wrong remote worker')
    if not receipt['compressed_payload_sha256_checked']:
        raw_pin = json.loads((LAB / 'payload-raw-sha256.json').read_text())['sha256']
        if receipt['expanded_payload_sha256_checked'] != raw_pin:
            raise ValueError('expanded Kaggle input did not match the original raw bytes')
    for name, pin in receipt['outputs'].items():
        if sha(folder / name) != pin:
            raise ValueError('remote output hash mismatch')
    manifest = json.loads((LAB / 'dataset-upload/MANIFEST.json').read_text())
    for name, pin in manifest['files'].items():
        if sha(LAB / 'dataset-upload' / name) != pin:
            raise ValueError('local source changed')
    with gzip.open(LAB / 'payload.json.gz', 'rt', encoding='utf-8') as f:
        data = json.load(f)
    if receipt['plan'] != data['plan']:
        raise ValueError('remote plan mismatch')
    benchmark = json.loads((folder / 'benchmark.json').read_text())
    expected_load = {(n, t) for n in data['plan']['warm_load_modes'] for t in [1000, 10000, 50000]}
    actual_load = {(r['mode'], r['target']) for r in benchmark['load']}
    if actual_load != expected_load or len(benchmark['load']) != 9:
        raise ValueError('load targets were not completed')
    if any(r['decision_differences'] or r['worker_count'] != 1 or r['cases_including_repeats'] < r['target']
           for r in benchmark['load']):
        raise ValueError('load verification failed')
    cases, requests, plans = data['cases'], data['requests'], data['rows']
    members = {c['case_id']: c for c in cases}
    if len(members) != 2662 or Counter(c['split'] for c in cases) != {'train': 1330, 'eval': 1332}:
        raise ValueError('population differs')
    ranking_records = read_lines(folder / 'rankings.jsonl.gz')
    expected_pairs = {(c['case_id'], v) for c in cases for v in data['plan']['ranking_modes']}
    if len(ranking_records) != len(expected_pairs) or {(r['case_id'], r['view']) for r in ranking_records} != expected_pairs:
        raise ValueError('ranking case/view coverage differs')
    native_rows = {p['dossier_id']: [r['row_key'] for r in p['au_rows']] for p in data['products']}
    for r in ranking_records:
        ranked = r['candidates']
        expected = native_rows[members[r['case_id']]['dossier_id']]
        if [x['row_key'] for x in sorted(ranked, key=lambda x: x['source_order'])] != expected:
            raise ValueError('native rank candidates were removed/replaced')
        if ranked != sorted(ranked, key=lambda x: (-x['score'], x['source_order'])):
            raise ValueError('ranking tie order changed')
    # Independently reconstruct all condition states on the host, then compare
    # complete rows/cases to the remote outputs before opening any references.
    ns = frozen_functions(LAB / 'snapshot')
    docs, catalog = ns['catalogs'](requests, data['documents'])
    scoped = {d: {k: v for k, v in catalog.items() if k[0] == d}
              for d in {c['dossier_id'] for c in cases}}
    base = data['base_states']
    states = {'exact': data['exact_states'], 'current': base, 'dictionary2': dict(base)}
    for req in requests:
        rid = req['request_id']
        if states['dictionary2'][rid] == 'unknown':
            d = docs[req['target_document_id']]['dossier_id']
            states['dictionary2'][rid] = ns['dictionary_request_state'](
                req, docs, scoped[d], data['dictionary'], data['raw_view'])[0]
    from sudachipy import dictionary, tokenizer
    previous_plan = json.loads((ROOT / '.lab-output/sku-sudachi-dice-20261017-v1/PLAN.json').read_text())
    for edition in ['core', 'full']:
        asset = previous_plan['dictionary_pins'][edition]
        if sha(asset['path']) != manifest['dictionary_binary_sha256'][edition]:
            raise ValueError('host dictionary differs')
        obj = dictionary.Dictionary(dict=asset['path'])
        tok = obj.tokenizer()
        for mode in 'ABC':
            name = edition + '_' + mode
            view = {v: raw if ns['protected'](raw) else ''.join(m.normalized_form() for m in
                    tok.tokenize(raw, getattr(tokenizer.Tokenizer.SplitMode, mode)))
                    for v, raw in data['raw_view'].items()}
            states[name] = dict(states['dictionary2'])
            for req in requests:
                rid = req['request_id']
                if states[name][rid] == 'unknown':
                    d = docs[req['target_document_id']]['dossier_id']
                    states[name][rid] = ns['dictionary_request_state'](req, docs, scoped[d], data['dictionary'], view)[0]
        del tok, obj
    outputs = {}
    for name, s in states.items():
        rr, cc = ns['assemble_mode'](plans, requests, s)
        remote_rows, remote_cases = read_lines(folder / (name + '-rows.jsonl.gz')), read_lines(folder / (name + '-cases.jsonl.gz'))
        if rr != remote_rows or cc != remote_cases:
            raise ValueError('host/remote complete projection mismatch: ' + name)
        if any(s[rid] != old for rid, old in base.items() if old != 'unknown') and name != 'exact':
            raise ValueError('known baseline overwritten')
        outputs[name] = {'rows': rr, 'cases': cc}
    eval_ids = {c['case_id'] for c in cases if c['split'] == 'eval'}
    old_dice = ROOT / '.lab-output/sku-sudachi-dice-20261017-v1/run-v1'
    eval_projection = {}
    for name in states:
        if name in ['exact', 'current', 'dictionary2']:
            directory = OLD / 'eval-v1'
            prefix = {'exact': 'baseline_raw', 'current': 'current_pipeline',
                      'dictionary2': 'exact_current_dictionary2'}[name]
        else:
            directory, prefix = old_dice, name
        for kind in ['rows', 'cases']:
            expected = read_lines(directory / (prefix + '-' + kind + '.jsonl'))
            actual = [r for r in outputs[name][kind] if r['case_id'] in eval_ids]
            if actual != expected:
                raise ValueError('original EVAL1332 projection changed: ' + name + '-' + kind)
            eval_projection[name + '-' + kind] = True
    write_gzip(LAB / 'host-condition-states.jsonl.gz', (
        {'request_id': r['request_id'], 'states': {n: s[r['request_id']] for n, s in states.items()}} for r in requests))
    audit = {'all_remote_outputs_hash_verified': True, 'all_host_remote_row_case_projections_equal': True,
             'all_original_conditions_retained': len(requests), 'all_original_rows_retained': len(plans),
             'reference_data_read': False, 'remote_freeze_sha256': sha(freezes[0]),
             'prior_eval1332_projections_equal': eval_projection,
             'condition_state_sha256': sha(LAB / 'host-condition-states.jsonl.gz')}
    (LAB / 'HOST_PREDICTION_AUDIT.json').write_text(json.dumps(audit, indent=2) + '\n')
    # Only below this point do we open the existing machine scoring references.
    import sys
    shim = sys.modules.get('run_installed_library_selection_v1')
    if shim is not None and not getattr(shim, '__file__', None):
        del sys.modules['run_installed_library_selection_v1']
    from grade_final_test_subset_v1 import canonical_reference
    from grade_library_axis_domains_v1 import pruning
    refs = json.loads((OLD / 'train-reference-v1.json').read_text())['references']
    refs.update({r['case_id']: r['reference'] for r in read_lines(OLD / 'grade-v1/selected_eval_references.jsonl')})
    if set(refs) != set(members):
        raise ValueError('reference membership differs')
    by_split = {}
    fields = ('direction', 'axis_name', 'option_values', 'target_document_id', 'target_selected_axes')
    for split in ['train', 'eval']:
        ids = {c['case_id'] for c in cases if c['split'] == split}
        subrefs = {cid: refs[cid] for cid in ids}
        scores = {}
        for name, output in outputs.items():
            rr = [r for r in output['rows'] if r['case_id'] in ids]
            cc = [r for r in output['cases'] if r['case_id'] in ids]
            count, errors = Counter(), []
            for d in cc:
                label, allowed = canonical_reference(refs[d['case_id']])
                disp = d['disposition']
                count[disp] += 1
                if disp == 'accept':
                    tag = 'correct_accept' if label == 'matched' and d['au_row_key'] in allowed else 'wrong_accept' if label in ['matched', 'unmatched'] else 'unknown_accept'
                elif disp == 'reject':
                    tag = 'correct_reject' if label == 'unmatched' else 'wrong_reject' if label == 'matched' else 'unknown_reject'
                else:
                    continue
                count[tag] += 1
                if tag.startswith('wrong'):
                    errors.append(d['case_id'])
            decisions = {c['case_id']: c for c in cc}
            rowstates = {(r['case_id'], r['au_row_key']): r['state'] for r in rr}
            remaining = {b['request_id'] for r in plans if r['case_id'] in ids
                         and decisions[r['case_id']]['disposition'] == 'review'
                         and rowstates[r['case_id'], r['au_row_key']] != 'conflict'
                         for b in r['bindings'] if states[name][b['request_id']] == 'unknown'}
            contexts = {json.dumps({k: r[k] for k in fields}, ensure_ascii=False, sort_keys=True)
                        for r in requests if r['request_id'] in remaining}
            scores[name] = {'counts': dict(count), 'error_case_ids': errors,
                            'pruning': pruning(rr, subrefs), 'residual_AI_contexts': len(contexts)}
        by_split[split] = {'case_count': len(ids), 'reference_counts': dict(Counter(canonical_reference(r)[0] for r in subrefs.values())), 'gate': scores}
    rank_counts = {'train': defaultdict(Counter), 'eval': defaultdict(Counter)}
    rowmaps = {(r['case_id'], r['au_row_key']): r['state'] for r in outputs['dictionary2']['rows']}
    for r in ranking_records:
        cid, split, view = r['case_id'], r['split'], r['view']
        label, allowed = canonical_reference(refs[cid])
        for scope, ranked in [('all', r['candidates']), ('guarded', [x for x in r['candidates'] if rowmaps[cid, x['row_key']] != 'conflict'])]:
            count = rank_counts[split][view]
            count[scope + '_' + str(label)] += 1
            if label == 'matched':
                tied = [x for x in ranked if x['score'] == ranked[0]['score']] if ranked else []
                first = bool(ranked and ranked[0]['row_key'] in allowed)
                count[scope + '_stable_correct'] += first
                count[scope + '_unique_correct'] += first and len(tied) == 1
                count[scope + '_tie_contains_correct'] += any(x['row_key'] in allowed for x in tied)
            elif label == 'unmatched':
                count[scope + '_unmatched_with_candidates'] += bool(ranked)
    for split, counts in rank_counts.items():
        by_split[split]['ranking'] = {n: dict(c) for n, c in counts.items()}
    result = {'schema': 'sku-dice-volume-grade-v1', 'cohorts': by_split,
              'independent_test': False, 'train_half_dictionary_fit': True,
              'references': 'previous Claude machine annotations, not human gold',
              'host_prediction_audit_sha256': sha(LAB / 'HOST_PREDICTION_AUDIT.json'),
              'benchmark': benchmark,
              'post_grade_tuning': False}
    if (LAB / 'GRADE.json').exists():
        raise FileExistsError('refuse to overwrite grade')
    (LAB / 'GRADE.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({s: {n: {'counts': v['counts'], 'AI_contexts': v['residual_AI_contexts']} for n, v in x['gate'].items()} for s, x in by_split.items()}))


if __name__ == '__main__':
    grade()
