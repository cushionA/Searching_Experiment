"""Independently audit public saved predictions; never import upstream code.

Python 3.12+, standard library only. This is NOT model inference.
"""
import collections
import hashlib
import json
import math
from pathlib import Path
import resource
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'benchmarks/daily-tech-20261028'
SRC = OUT / 'sources'
PREFIX = 'laya__research__benchmarks__feishu_zh__'
LABELS = ('urgent', 'todo', 'valuable', 'noise')
ACTION = {'urgent', 'todo'}


def read(name):
    return json.loads((SRC / name).read_text())


def lines(name):
    return [json.loads(x) for x in (SRC / name).read_text().splitlines()]


def decode(row):
    answers = row['response']['answers']
    if row['mode'] == 'choice':
        a = answers['category']
        p = a['probabilities']
        assert set(p) == set(LABELS)
        assert all(math.isfinite(v) and 0 <= v <= 1 for v in p.values())
        # Match the published archive's 0.01 rounding tolerance; report stricter
        # deviations separately, without normalizing or silently repairing them.
        assert abs(sum(p.values()) - 1) <= .01 + 1e-12
        assert a['choice'] == max(p, key=p.get)
        assert p == row['probabilities']
        return a['choice']
    p = {k: answers[k]['noul'] for k in ('related', 'action', 'urgent', 'value')}
    assert all(math.isfinite(v) and 0 <= v <= 1 for v in p.values())
    assert p == row['signals']
    if p['related'] >= .5 and p['action'] >= .5:
        return 'urgent' if p['urgent'] >= .75 else 'todo'
    return 'valuable' if p['related'] >= .5 and p['value'] >= .5 else 'noise'


def score(rows):
    n = len(rows)
    return dict(n=n, correct=sum(r['predicted'] == r['expected'] for r in rows),
                false_action_count=sum(r['expected'] not in ACTION and r['predicted'] in ACTION for r in rows),
                nonaction_denominator=sum(r['expected'] not in ACTION for r in rows),
                missed_action_count=sum(r['expected'] in ACTION and r['predicted'] not in ACTION for r in rows),
                action_denominator=sum(r['expected'] in ACTION for r in rows),
                predicted_counts=dict(collections.Counter(r['predicted'] for r in rows)))


def main():
    start = time.perf_counter()
    manifest = read('../source-manifest.json')
    for entry in manifest:
        if 'error' not in entry:
            data = (SRC / entry['name']).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['name']
    cases = lines(PREFIX + 'data__cases.jsonl')
    assert len(cases) == 64
    gold = {r['id']: r for r in cases}
    assert len(gold) == 64
    assert collections.Counter(r['expected'] for r in cases) == dict.fromkeys(LABELS, 16)
    upstream = read(PREFIX + 'results__v1__summary.json')
    result = {'kind': 'independent_recalculation_of_author_archived_outputs',
              'model_inference_calls': 0, 'quality_repeat': 0, 'systems': {}}
    all_rows = {}
    for system in ('laya', 'jev'):
        rows = lines(PREFIX + f'results__v1__{system}__raw.jsonl')
        assert len(rows) == 384
        keys = {(r['id'], r['mode'], r['repeat']) for r in rows}
        assert keys == {(i, m, t) for i in gold for m in ('choice', 'four_noul') for t in range(3)}
        for r in rows:
            assert r['status'] == 'ok'
            assert r['expected'] == gold[r['id']]['expected']
            assert r['family'] == gold[r['id']]['family']
            assert decode(r) == r['predicted']
        all_rows[system] = {(r['id'], r['mode'], r['repeat']): r for r in rows}
        result['systems'][system] = {'probability_sum_deviation_gt_0_001': [
            {'id': r['id'], 'repeat': r['repeat'], 'sum': sum(r['probabilities'].values())}
            for r in rows if r['mode'] == 'choice' and abs(sum(r['probabilities'].values()) - 1) > .001]}
        for mode in ('choice', 'four_noul'):
            first = [r for r in rows if r['mode'] == mode and r['repeat'] == 0]
            metrics = score(first)
            for key in ('n', 'correct', 'false_action_count', 'nonaction_denominator', 'missed_action_count', 'action_denominator'):
                assert metrics[key] == upstream[system][mode][key]
            metrics['repeat_correct'] = [score([r for r in rows if r['mode'] == mode and r['repeat'] == t])['correct'] for t in range(3)]
            if mode == 'choice':
                metrics['max_probability_ge_0_8'] = score([r for r in first if max(r['probabilities'].values()) >= .8])
                metrics['returned_confidence_ge_0_8'] = score([r for r in first if r['confidence'] >= .8])
                metrics['brier_sum'] = sum(sum((r['probabilities'][label] - (label == r['expected']))**2 for label in LABELS) for r in first) / 64
            result['systems'][system][mode] = metrics
    assert all(all_rows['laya'][k]['request_sha256'] == all_rows['jev'][k]['request_sha256'] for k in all_rows['laya'])
    result['matched_request_hashes'] = 384
    # The public registry hash is metadata, not a locally downloaded weight hash.
    old = read('laya_old_model.json'); current = read('laya_model.json')
    old_weights = {x['rfilename']: x['lfs'] for x in old['siblings'] if x['rfilename'].endswith('.safetensors')}
    new_weights = {x['rfilename']: x['lfs'] for x in current['siblings'] if x['rfilename'].endswith('.safetensors')}
    result['registry_weights_unchanged'] = old_weights == new_weights
    result['weight_bytes_all_three'] = sum(x['size'] for x in new_weights.values())
    result['weights'] = new_weights
    positions = read('laya__research__results__position_sensitivity_multilingual.json')
    result['author_position_results'] = []
    for row in positions['rows']:
        assert len(row['predictions']) == row['n']
        correct = sum(p['gold'] == p['prediction'] for p in row['predictions'])
        assert correct == row['correct']
        result['author_position_results'].append({k: row[k] for k in ('pad_tokens', 'needle_position', 'n', 'correct')})
    result['elapsed_seconds_audit_only'] = time.perf_counter() - start
    result['peak_rss_kib_audit_only'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    (OUT / 'audit-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
