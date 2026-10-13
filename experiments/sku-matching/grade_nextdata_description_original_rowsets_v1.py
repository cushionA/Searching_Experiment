#!/usr/bin/env python3
"""Post-freeze consumed-data grading against complete original annotation rows.

This entry point deliberately does not use the derived historical reference
cache: one accepted historical row cannot establish a singleton reference set.
No inference, output repair, threshold selection, or independent-TEST assertion.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import run_nextdata_table_experiment_v1 as EXP

V = EXP.VOLUME


def grade(experiment: Path, collections: list[Path], output: Path) -> dict:
    _, bundle, _, _, selection = EXP.load_experiment(experiment)
    if not collections or len(set(p.resolve() for p in collections)) != len(collections):
        raise ValueError('collections must be nonempty and unique')
    records = []
    all_case_ids = set()
    source_cases = V.unique_index(bundle.source['cohort_rows']['case-plans.jsonl'],
                                 'case_id', 'source cases')
    for collection in collections:
        freeze_path = collection / 'output-freeze.json'
        freeze = V.read_json(freeze_path)
        if freeze.get('all_predictions_and_gates_frozen') is not True:
            raise ValueError('collection is not frozen')
        if freeze.get('input_freeze') != EXP.pin(experiment / 'input-freeze.json'):
            raise ValueError('collection input freeze differs')
        if freeze.get('independent_test') is not False:
            raise ValueError('consumed diagnostic must not claim independence')
        EXP.verify_pins(freeze['files'])
        for name in ('membership.jsonl', 'case-decisions.jsonl', 'collection-receipt.json'):
            if freeze['files'].get(str((collection / name).resolve())) != EXP.pin(collection / name):
                raise ValueError('required grading file not in freeze')
        membership = V.read_rows(collection / 'membership.jsonl')
        predictions = V.read_rows(collection / 'case-decisions.jsonl')
        members = V.unique_index(membership, 'case_id', 'collection membership')
        predicted = V.unique_index(predictions, 'case_id', 'collection predictions')
        if set(predicted) != set(members) or not set(members) <= set(source_cases):
            raise ValueError('predictions do not exactly cover a source cohort')
        all_case_ids.update(members)
        records.append((collection, membership, predictions,
                        V.read_json(collection / 'collection-receipt.json')))
    # All requested variants and gates are verified before opening annotations.
    union_membership = [{**m, 'source_version': source_cases[m['case_id']]['source_version'],
                         'split': source_cases[m['case_id']]['source_version']}
        for m in bundle.source['cohort_rows']['membership.jsonl'] if m['case_id'] in all_case_ids]
    if len(union_membership) != len(all_case_ids):
        raise ValueError('annotation membership union differs')
    archives = [EXP.ROOT / V.SCORE_ORIGINAL.V2_LABEL_ARCHIVE,
                EXP.ROOT / V.SCORE_ORIGINAL.V3_LABEL_ARCHIVE]
    references = V.cloned_selected_label_reader()(*archives, union_membership,
                                                  bundle.prepared / 'row-plans.jsonl')
    if set(references) != all_case_ids:
        raise ValueError('original annotation rows do not cover the union')
    historical = V.read_rows(experiment / 'historical-full-case-decisions.jsonl')
    historical_by_id = V.unique_index(historical, 'case_id', 'historical baseline')
    results = {}

    def score(membership, predictions):
        ids = {m['case_id'] for m in membership}
        enriched = [{**m, 'source_version': source_cases[m['case_id']]['source_version'],
                     'split': source_cases[m['case_id']]['source_version']} for m in membership]
        local_refs = {cid: references[cid] for cid in ids}
        result = V.SCORE_ORIGINAL.score_variant(enriched, predictions, local_refs)
        metric, rows = result['metrics'], result['row_metrics']
        positives = metric['tp'] + metric['fn']
        correct = rows.get('correct_accepted_row', 0)
        unknown = set(result['unknown_reference_case_ids'])
        result.update(EXP.FLAGS)
        result['exact_row_recall'] = correct / positives if positives else None
        result['exact_row_known_accuracy'] = ((correct + metric['tn']) / metric['scored_case_count']
                                               if metric['scored_case_count'] else None)
        result['known_wrong_accepted_count'] = metric['fp'] + rows.get('wrong_accepted_row', 0)
        result['unknown_accepted_count'] = sum(p['decision'] == 'accept'
            and p['case_id'] in unknown for p in predictions)
        return result

    for collection, members, predictions, receipt in records:
        name = collection.name
        if name in results:
            raise ValueError('collection directory names must be distinct')
        ids = {m['case_id'] for m in members}
        baseline = [historical_by_id[cid] for cid in ids]
        result = {'collection_freeze': EXP.pin(collection / 'output-freeze.json'),
            'model_route': receipt.get('model_route', receipt.get('model_id')),
            'variant': receipt['variant'], 'collection_receipt': receipt,
            'score': score(members, predictions),
            'same_cohort_historical_full': score(members, baseline),
            'paired_vs_historical_full': EXP.paired_change(baseline, predictions, references),
            'strata': {}}
        for label, key in (('main', 'main_case_ids'), ('error8', 'error_case_ids')):
            chosen = ids & set(selection[key])
            if chosen:
                chosen_members = [m for m in members if m['case_id'] in chosen]
                chosen_predictions = [p for p in predictions if p['case_id'] in chosen]
                chosen_baseline = [historical_by_id[cid] for cid in chosen]
                result['strata'][label] = {'score': score(chosen_members, chosen_predictions),
                    'same_cohort_historical_full': score(chosen_members, chosen_baseline),
                    'paired_vs_historical_full': EXP.paired_change(
                        chosen_baseline, chosen_predictions, references)}
        results[name] = result
    fresh_paired = []
    for before in records:
        for after in records:
            b_path, _, b_preds, b_receipt = before
            a_path, _, a_preds, a_receipt = after
            route = b_receipt.get('model_route', b_receipt.get('model_id'))
            if (b_receipt['variant'] != 'full' or a_receipt['variant'] != 'table'
                or route != a_receipt.get('model_route', a_receipt.get('model_id'))):
                continue
            ids = {p['case_id'] for p in b_preds} & {p['case_id'] for p in a_preds}
            if ids:
                fresh_paired.append({'model_route': route, 'full_collection': b_path.name,
                    'description_collection': a_path.name, 'case_count': len(ids),
                    'change': EXP.paired_change([p for p in b_preds if p['case_id'] in ids],
                        [p for p in a_preds if p['case_id'] in ids], references)})
    report = {'schema_version': 'nextdata-description-original-rowsets-grade-v1', **EXP.FLAGS,
        'annotations_read_after_all_requested_output_freezes_verified': True,
        'reference_rowsets': 'complete_original_annotation_sets_not_historical_singleton_cache',
        'reference_kind': 'claude_haiku_5_5_machine_annotation_not_human_gold',
        'thresholds_selected': False, 'gates_changed': False, 'outputs_changed': False,
        'description_payload_alias': 'table', 'description_includes_sourcebound_title': True,
        'main_comparison_is_fresh_description_vs_historical_full': True,
        'small_models_are_scope_relaxed_diagnostics': True,
        'execution_failures_are_retained_as_unknown': True,
        'grader_code': EXP.pin(Path(__file__)),
        'original_score_code': EXP.pin(Path(V.SCORE_ORIGINAL.__file__)),
        'label_archives': [EXP.pin(p) for p in archives],
        'input_freeze': EXP.pin(experiment / 'input-freeze.json'),
        'reference_case_count': len(references), 'variants': results,
        'fresh_full_vs_description': fresh_paired}
    EXP.write_json(output, report)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment', type=Path, required=True)
    p.add_argument('--collection', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = grade(args.experiment, args.collection, args.output)
    print({name: {'metrics': row['score']['metrics'],
                  'exact_row_recall': row['score']['exact_row_recall'],
                  'known_wrong_accepted_count': row['score']['known_wrong_accepted_count']}
           for name, row in result['variants'].items()})
