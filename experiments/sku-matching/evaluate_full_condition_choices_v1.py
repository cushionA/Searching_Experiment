"""Score frozen full-data runs and report regressions without changing labels."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from integrate_claude_description_v1 import finish, score, verify_outputs
from trial_scoped_condition_gliner_v1 import read, sha, write_json


def indexed(path, field):
    records = read(path)
    result = {r[field]: r for r in records}
    if len(result) != len(records):
        raise ValueError(f"duplicate {field}: {path}")
    return result


def row_evidence_coverage(labels_path, finished_decisions):
    """Coverage among resolved matched references in this frozen decision set."""
    references = indexed(labels_path, 'case_id')
    complete = missing = 0
    for case_id in finished_decisions:
        reference = references.get(case_id)
        if reference is None:
            continue
        label = reference.get('label', reference.get('decision'))
        if label != 'matched':
            continue
        rows = reference.get('matched_au_row_keys', reference.get('matching_au_row_keys'))
        if rows is None:
            missing += 1
        elif isinstance(rows, list) and rows:
            complete += 1
        else:
            raise ValueError(f'invalid matched row evidence for case {case_id}')
    total = complete + missing
    return {'matched_with_complete_row_keys': complete,
            'matched_without_row_evidence': missing,
            'matched_reference_count': total,
            'row_evidence_coverage': complete / total if total else None}


def row_metric_summary(row_metrics, sku_metrics, coverage):
    correct = row_metrics.get('correct_accepted_row', 0)
    wrong = row_metrics.get('wrong_accepted_row', 0)
    unscored = row_metrics.get('accepted_matched_row_unscored', 0)
    precision_denominator = correct + wrong + sku_metrics['fp']
    recall_denominator = sku_metrics['tp'] + sku_metrics['fn']
    return {
        'row_metrics': row_metrics,
        'row_evidence_coverage': coverage,
        'correct_row_precision': (correct / precision_denominator
                                  if precision_denominator and unscored == 0 else None),
        'correct_row_recall': (correct / recall_denominator
                               if recall_denominator and coverage['matched_without_row_evidence'] == 0 else None),
    }


def compare_cases(before, after):
    if set(before) != set(after):
        raise ValueError("case IDs differ across frozen runs")
    changed = []
    for key, new in after.items():
        old = before[key]
        if (old['decision'], old['top_row_key']) != (new['decision'], new['top_row_key']):
            changed.append({'case_id': key, 'au_product_id': new['au_product_id'],
                            'cohort': new['cohort'], 'before': old['decision'],
                            'after': new['decision'], 'before_row': old['top_row_key'],
                            'after_row': new['top_row_key']})
    return {'changed_case_count': len(changed),
            'transitions': dict(Counter(r['before'] + '->' + r['after'] for r in changed)),
            'cases': changed}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared-dir', type=Path, required=True)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--predictions', type=Path, action='append', required=True)
    p.add_argument('--labels', type=Path, required=True)
    p.add_argument('--previous-gate', type=Path, required=True)
    p.add_argument('--previous-subset-run', type=Path, required=True)
    p.add_argument('--previous-subset-reference', type=Path, required=True)
    p.add_argument('--novel-prepared-dir', type=Path)
    p.add_argument('--novel-run-dir', type=Path)
    p.add_argument('--novel-labels', type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    run_summary = json.loads((args.run_dir / 'summary.json').read_text())
    for name, checksum in run_summary['output_sha256'].items():
        if sha(args.run_dir / name) != checksum:
            raise ValueError('model output SHA differs: ' + name)
    prepared = json.loads((args.prepared_dir / 'manifest.json').read_text())
    names = ['requests.jsonl', 'documents.jsonl', 'tasks.jsonl', 'case_plans.jsonl', 'row_plans.jsonl']
    verify_outputs(args.prepared_dir, prepared, names)
    requests = indexed(args.prepared_dir / 'requests.jsonl', 'request_id')
    novel = None
    if any([args.novel_prepared_dir, args.novel_run_dir, args.novel_labels]):
        if not all([args.novel_prepared_dir, args.novel_run_dir, args.novel_labels]):
            raise ValueError('all three novel options are required together')
        novel_manifest = json.loads((args.novel_prepared_dir / 'manifest.json').read_text())
        verify_outputs(args.novel_prepared_dir, novel_manifest, names)
        novel_summary = json.loads((args.novel_run_dir / 'summary.json').read_text())
        for name, checksum in novel_summary['output_sha256'].items():
            if sha(args.novel_run_dir / name) != checksum:
                raise ValueError('novel model output SHA differs: ' + name)
        novel_requests = indexed(args.novel_prepared_dir / 'requests.jsonl', 'request_id')
        novel = (novel_manifest, novel_requests)
    old_manifest = json.loads((args.previous_gate / 'manifest.json').read_text())
    verify_outputs(args.previous_gate, old_manifest, ['decisions.jsonl'])
    old_cases = indexed(args.previous_gate / 'decisions.jsonl', 'case_id')
    old_subset_summary = json.loads((args.previous_subset_run / 'summary.json').read_text())
    if sha(args.previous_subset_run / 'predictions.jsonl') != old_subset_summary['output_sha256']['predictions.jsonl']:
        raise ValueError('previous subset prediction SHA differs')
    old_subset = indexed(args.previous_subset_run / 'predictions.jsonl', 'request_id')
    frozen = {}
    for path in args.predictions:
        if path.parent.resolve() != args.run_dir.resolve():
            raise ValueError('predictions must belong to verified completed model run')
        predictions = indexed(path, 'request_id')
        if set(predictions) != set(requests):
            raise ValueError('full run has missing/extra request IDs')
        for key, pred in predictions.items():
            if pred['source_request'] != requests[key] or pred['production_adoption_allowed'] is not False:
                raise ValueError('request binding or experimental flag differs')
        frozen[path.stem] = (path, predictions)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    gates = {}
    novel_gates = {}
    # Finish every binary run before reading any separate reference labels.
    for variant, (path, predictions) in frozen.items():
        destination = args.output_dir / variant
        finish(args.prepared_dir, path, destination)
        gates[variant] = indexed(destination / 'decisions.jsonl', 'case_id')
        if novel is not None:
            novel_path = args.novel_run_dir / path.name
            values = indexed(novel_path, 'request_id')
            if set(values) != set(novel[1]):
                raise ValueError('novel run has missing/extra request IDs')
            for key, pred in values.items():
                if pred['source_request'] != novel[1][key] or pred['production_adoption_allowed'] is not False:
                    raise ValueError('novel request binding or experimental flag differs')
            novel_destination = args.output_dir / ('novel-' + variant)
            finish(args.novel_prepared_dir, novel_path, novel_destination)
            novel_gates[variant] = indexed(novel_destination / 'decisions.jsonl', 'case_id')
            if set(novel_gates[variant]) & set(gates[variant]):
                raise ValueError('novel/full case IDs overlap')
    refmanifest = json.loads((args.previous_subset_reference / 'manifest.json').read_text())
    refpath = args.previous_subset_reference / 'reference.jsonl'
    if sha(refpath) != refmanifest['output_sha256']['reference.jsonl']:
        raise ValueError('previous subset reference SHA differs')
    refs = indexed(refpath, 'request_id')
    if set(refs) != set(old_subset):
        raise ValueError('previous subset IDs differ')
    variants, subset_details = {}, []
    for variant, (_, predictions) in frozen.items():
        destination = args.output_dir / variant
        scoring = score(destination, args.labels, destination / 'score-machine.json')
        corrections = Counter()
        for key, ref in refs.items():
            new, old = predictions[key], old_subset[key]
            if new['source_request'] != old['source_request']:
                raise ValueError('previous subset original condition/context changed')
            actual = 'accept' if new['state'] == 'support' else 'drop'
            was = 'accept' if old['state'] == 'support' else 'drop'
            correct, previously_correct = actual == ref['expected_binary'], was == ref['expected_binary']
            corrections['correct_now' if correct else 'wrong_now'] += 1
            if previously_correct:
                corrections['previously_correct_retained' if correct else 'previously_correct_regressed'] += 1
            else:
                corrections['previously_wrong_corrected' if correct else 'previously_wrong_remaining'] += 1
            subset_details.append({'variant': variant, 'request_id': key, 'case_id': ref['case_id'],
                                   'au_product_id': ref['au_product_id'], 'expected_binary': ref['expected_binary'],
                                   'previous_binary': was, 'new_binary': actual, 'correct': correct,
                                   'regression': previously_correct and not correct})
        cells = scoring['metrics']
        row_summary = row_metric_summary(scoring['row_metrics'], cells,
                                         row_evidence_coverage(args.labels, gates[variant]))
        variants[variant] = {'sku_metrics': cells, **row_summary,
                             'cohort_metrics': scoring['cohort_metrics'],
                             'excluded_reference_counts': dict(Counter(x['reason'] for x in scoring['excluded'])),
                             'condition_states': dict(Counter(x['state'] for x in predictions.values())),
                             'sku_decisions': dict(Counter(x['decision'] for x in gates[variant].values())),
                             'previous_17_condition_regressions': dict(corrections),
                             'changes_vs_previous_full_gate': compare_cases(old_cases, gates[variant])}
        if novel is not None:
            novel_destination = args.output_dir / ('novel-' + variant)
            novel_score = score(novel_destination, args.novel_labels, novel_destination / 'score-machine.json')
            n = novel_score['metrics']
            combined = {key: cells[key] + n[key] for key in ['tp', 'fp', 'fn', 'tn', 'scored_case_count']}
            combined.update(precision=combined['tp'] / (combined['tp'] + combined['fp'])
                            if combined['tp'] + combined['fp'] else None,
                            recall=combined['tp'] / (combined['tp'] + combined['fn'])
                            if combined['tp'] + combined['fn'] else None,
                            binary_accuracy=(combined['tp'] + combined['tn']) / combined['scored_case_count']
                            if combined['scored_case_count'] else None)
            novel_row_summary = row_metric_summary(
                novel_score['row_metrics'], n,
                row_evidence_coverage(args.novel_labels, novel_gates[variant]))
            all_row_metrics = Counter(scoring['row_metrics']) + Counter(novel_score['row_metrics'])
            full_coverage = row_summary['row_evidence_coverage']
            novel_coverage = novel_row_summary['row_evidence_coverage']
            all_coverage_counts = {
                key: full_coverage[key] + novel_coverage[key]
                for key in ('matched_with_complete_row_keys', 'matched_without_row_evidence',
                            'matched_reference_count')}
            all_coverage_counts['row_evidence_coverage'] = (
                all_coverage_counts['matched_with_complete_row_keys'] /
                all_coverage_counts['matched_reference_count']
                if all_coverage_counts['matched_reference_count'] else None)
            all_row_summary = row_metric_summary(all_row_metrics, combined, all_coverage_counts)
            variants[variant].update(novel_sku_metrics=n, **{
                'novel_' + key: value for key, value in novel_row_summary.items()},
                novel_excluded_reference_counts=dict(Counter(x['reason'] for x in novel_score['excluded'])),
                novel_sku_decisions=dict(Counter(x['decision'] for x in novel_gates[variant].values())),
                all_scored_case_metrics=combined, all_scored_row_metrics=all_row_summary)
    write_json(args.output_dir / 'comparison.json', {
        'reference_kind': 'machine_labels_not_human_gold', 'independent_test': False,
        'labels_read_after_all_model_and_gate_predictions_frozen': True,
        'production_adoption_allowed': False, 'threshold_search': False,
        'full_case_count': prepared['case_count'] + (novel[0]['case_count'] if novel else 0),
        'full_request_count': len(requests) + (len(novel[1]) if novel else 0),
        'real_cases_outside_received_cohorts': 0 if novel else prepared.get('input_cases_outside_received_cohorts', 0),
        'variants': variants, 'model_run_summary_sha256': sha(args.run_dir / 'summary.json'),
        'label_sha256': sha(args.labels), 'novel_label_sha256': sha(args.novel_labels) if novel else None,
        'previous_subset_reference_sha256': sha(refpath)})
    with (args.output_dir / 'previous-17-regressions.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(subset_details[0]))
        writer.writeheader()
        writer.writerows(subset_details)
    print(json.dumps({v: {k: x for k, x in metrics.items() if k != 'changes_vs_previous_full_gate'}
                      for v, metrics in variants.items()}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
