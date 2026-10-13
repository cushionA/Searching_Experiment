#!/usr/bin/env python3
"""Collect a closed, frozen error-8 diagnostic; score original complete row sets.

No classifier calls, retries, raw output repairs, or reference-cache derivation.
The error cohort was selected using historical labels and is not independent.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import run_nextdata_table_experiment_v1 as EXP

V = EXP.VOLUME
EXPECTED_PRIORITY_SHA = '7c45d322f0a3f0ff8d8ed4ef09da2ca1e99d8d42b2d4ab05db543fa2446b45c0'


def collect(experiment: Path, priority_path: Path, output: Path) -> dict:
    if V.sha_file(priority_path) != EXPECTED_PRIORITY_SHA:
        raise ValueError('priority first-pass freeze differs')
    priority = V.read_json(priority_path)
    if priority['input_freeze_sha256'] != V.sha_file(experiment / 'input-freeze.json'):
        raise ValueError('priority input freeze differs')
    if priority.get('no_semantic_redispatch_or_output_repair') is not True:
        raise ValueError('priority did not freeze original first passes')
    for item in priority['files']:
        path, receipt = Path(item['output_path']), item['original_output']
        if path.is_file() != receipt['exists']:
            raise ValueError('frozen output presence differs: ' + item['packet_key'])
        if receipt['exists'] and (path.stat().st_size != receipt['bytes']
                                  or V.sha_file(path) != receipt['sha256']):
            raise ValueError('frozen original bytes differ: ' + item['packet_key'])
    _, bundle, public, maps, selection = EXP.load_experiment(experiment)
    subset = EXP.subset_source(bundle, set(selection['error_case_ids']))
    expected = set(selection['error_pair_refs'])
    dispatched = {r['pair_id'] for r in public.values()}
    case_pairs = {b['pair_id'] for b in subset['bindings'] if b.get('pair_id') in dispatched}
    if case_pairs != {public[ref]['pair_id'] for ref in expected}:
        raise ValueError('error cohort does not retain every whole-case model pair')
    output = EXP.fresh_dir(output)
    bodies = V.unique_index(bundle.source['scoped_rows']['source-bodies.jsonl'],
                            'document_sha256', 'bodies')
    receipts = {}
    for variant in ('table', 'full'):
        dest = output / variant
        dest.mkdir()
        validated, issues, raw_receipts, rendered_rows, citations = [], [], [], [], []
        items = [r for r in priority['files'] if r['packet_key'].startswith(variant + '-')]
        assigned = [ref for item in items for ref in item['pair_refs']]
        if len(assigned) != len(set(assigned)) or set(assigned) != expected:
            raise ValueError('priority variant does not cover exact error pair refs')
        for item in items:
            refs, path = item['pair_refs'], Path(item['output_path'])
            if not path.is_file():
                issues.extend({'pair_ref': ref, 'status': 'missing_unknown'} for ref in refs)
                continue
            raw = path.read_bytes()
            raw_dest = dest / 'raw' / path.name
            raw_dest.parent.mkdir(exist_ok=True)
            raw_dest.write_bytes(raw)
            raw_receipts.append({'packet_key': item['packet_key'],
                                 'original_path': str(path), **EXP.pin(raw_dest)})
            try:
                rows = EXP.parse_assigned_draft(raw, refs)
            except (ValueError, TypeError, UnicodeError) as exc:
                issues.extend({'pair_ref': ref, 'status': 'invalid_packet_unknown',
                               'error': str(exc)} for ref in refs)
                continue
            for row, ref in zip(rows, refs):
                pub = public[ref]
                pair = bundle.source['pair_by_id'][pub['pair_id']]
                try:
                    rendered, evidence = EXP.validate_luna_response(
                        row, pair, pub, ref, maps[ref], bundle, bodies, variant)
                    if evidence.get('source_citations_verified') is not True:
                        raise ValueError('source citations not verified')
                    judgments = [{**d, 'citations_verified':
                                  2 * len(d.get('target_fact_indices', []))
                                  + len(d.get('body_quote_refs', []))}
                                 for d in rendered['decisions']]
                    validated.append({'pair_id': pair['pair_id'], 'cache_key': pair['cache_key'],
                        'judgments': judgments, 'source_citations_verified': True,
                        'semantic_truth_verified': False, 'sourceproof_certified': False,
                        'production_adoption_allowed': False})
                    rendered_rows.append(rendered)
                    citations.extend(evidence.get('citations', []))
                except (KeyError, ValueError, TypeError, OSError,
                        V.JsonSchemaValidationError) as exc:
                    issues.append({'pair_ref': ref, 'status': 'invalid_response_unknown',
                                   'error': str(exc)})
        EXP.write_rows(dest / 'pair-failures.jsonl', issues)
        EXP.write_rows(dest / 'original-firstpass-receipts.jsonl', raw_receipts)
        EXP.write_rows(dest / 'validated-pairs.jsonl', validated)
        EXP.write_rows(dest / 'rendered-v3-responses.jsonl', rendered_rows)
        EXP.write_rows(dest / 'verified-source-citation-metadata.jsonl', citations)
        resolutions = V.EVAL.build_choice_nli_task_resolutions(
            subset['tasks'], subset['bindings'], validated, subset['proofs'], subset['receipts'])
        gate = EXP.save_gate(dest, subset, resolutions,
                             'unchanged_whole_sku_gate_with_frozen_source_citation_validation')
        receipts[variant] = EXP.freeze_collection(dest, experiment, {
            'model_route': 'hosted-gpt-6-luna', 'variant': variant,
            'assigned_pair_count': len(expected), 'validated_pair_count': len(validated),
            'missing_or_invalid_pair_count': len(issues), 'semantic_retries': 0,
            'cohort': 'historical_false_negative_error8', 'gate': gate})
    historical = [r for r in V.read_rows(experiment / 'historical-full-case-decisions.jsonl')
                  if r['case_id'] in set(selection['error_case_ids'])]
    EXP.write_rows(output / 'historical-baseline-case-decisions.jsonl', historical)
    EXP.write_json(output / 'priority-source-receipt.json', EXP.pin(priority_path))
    EXP.freeze_collection(output, experiment, {'cohort': 'historical_false_negative_error8',
        'reference_label_rowsets_used': 'original_archive_only_after_output_freeze',
        'historical_labels_used_for_selection': True, 'collections': receipts})
    # The consumed diagnostic has a truthful freeze contract, without asserting
    # the independent-TEST status required by the older experiment entry point.
    freeze = V.read_json(output / 'output-freeze.json')
    EXP.verify_pins(freeze['files'])
    plans = V.unique_index(subset['cases'], 'case_id', 'grading case plan')
    membership = [{**m, 'source_version': plans[m['case_id']]['source_version'],
                   'split': plans[m['case_id']]['source_version']} for m in subset['membership']]
    archive_v2 = EXP.ROOT / V.SCORE_ORIGINAL.V2_LABEL_ARCHIVE
    archive_v3 = EXP.ROOT / V.SCORE_ORIGINAL.V3_LABEL_ARCHIVE
    references = V.cloned_selected_label_reader()(archive_v2, archive_v3, membership,
                                                   bundle.prepared / 'row-plans.jsonl')
    if set(references) != set(plans):
        raise ValueError('original reference row sets do not cover cohort')
    predictions = {'historical_full': historical, **{
        variant: V.read_rows(output / variant / 'case-decisions.jsonl')
        for variant in ('table', 'full')}}
    report = {'schema_version': 'nextdata-description-error8-original-rowset-grade-v1',
        **EXP.FLAGS, 'output_freeze': EXP.pin(output / 'output-freeze.json'),
        'collector_code': EXP.pin(Path(__file__)),
        'original_grader_code': EXP.pin(Path(V.SCORE_ORIGINAL.__file__)),
        'label_archives': [EXP.pin(archive_v2), EXP.pin(archive_v3)],
        'labels_read_after_prediction_and_gate_freeze': True,
        'reference_kind': 'machine_annotations_haiku_5_5_not_human_gold',
        'case_count': len(membership), 'model_outputs_or_gates_changed_after_grade': False,
        'description_includes_sourcebound_title': True,
        'execution_failures_retained_as_unknown': True,
        'collections': receipts,
        'variants': {name: V.SCORE_ORIGINAL.score_variant(membership, rows, references)
                     for name, rows in predictions.items()}}
    EXP.write_json(output / 'private-original-rowset-grade.json', report)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment', type=Path, required=True)
    p.add_argument('--priority-freeze', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = collect(args.experiment, args.priority_freeze, args.output)
    print({name: {k: v for k, v in row.items() if k != 'differences'}
           for name, row in result['variants'].items()})
