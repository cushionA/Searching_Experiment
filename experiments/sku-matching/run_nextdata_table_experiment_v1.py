#!/usr/bin/env python3
"""Frozen, consumed-TEST description diagnostics; never runs inference itself.

Luna keeps the original semantic schema and whole-SKU gate, adding source
provenance verification for the user's normalized description text. Small
models produce a separately labelled scope-relaxed diagnostic with the same
whole-SKU gate; their categorical output is never forged into cited evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import run_indexed_volume_test_v1 as VOLUME
import prepare_body_indexed_volume_packets_v1 as PACKETS
import sku_nextdata_table_view_v1 as TABLE

DEFAULT_OUTPUT = ROOT / '.lab-output/sku-nextdata-table-20261013-v1/prepared-v1'
DEFAULT_CONFIG = ROOT / '.lab-output/sku-volume-unused-test-20261013-v1/runner-config-volume-v2.json'
OLD_EXECUTION = ROOT / '.lab-output/sku-volume-unused-test-execution-20261013-v1'
BASELINE = OLD_EXECUTION / 'final-gate-run-v1/indexed-body-invariant-v3'
OLD_GRADE = OLD_EXECUTION / 'postfreeze-private-grade-v1.json'
GUIDANCE = HERE / 'sku-body-indexed-grounding-guidance-v2.txt'
FLAGS = {'independent_test': False, 'consumed_test_diagnostic': True,
         'production_adoption_allowed': False, 'semantic_truth_verified': False}


def canon(value: Any) -> bytes:
    return PACKETS.canon(value)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canon(value) + b'\n')


def write_rows(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        for row in rows:
            stream.write(canon(row) + b'\n')


def pin(path: Path) -> dict[str, Any]:
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size,
            'sha256': VOLUME.sha_file(path)}


def verify_pins(pins: dict[str, dict]) -> None:
    for name, value in pins.items():
        path = Path(value['path'])
        if not path.is_file() or path.is_symlink() or pin(path) != value:
            raise ValueError('frozen artifact differs: ' + name)


def fresh_dir(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(ROOT / '.lab-output') or path.exists():
        raise FileExistsError('output must be a new directory under .lab-output')
    path.mkdir(parents=True)
    return path


def ordered_products(membership: list[dict], count: int = 12) -> list[str]:
    products = {str(row['au_product_id']) for row in membership}
    return sorted(products, key=lambda item: hashlib.sha256(
        ('nextdata-table-pilot-v1|' + item).encode()).hexdigest())[:count]


def case_dispatch_pairs(bindings: list[dict], dispatched: set[str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for binding in bindings:
        cid = (binding.get('task_binding') or {}).get('case_id')
        if binding.get('pair_id') in dispatched:
            result[cid].add(binding['pair_id'])
    return result


def select_error_cases(old_grade: dict, pairs_by_case: dict[str, set[str]], count: int = 8) -> list[str]:
    failures = [row['case_id'] for row in old_grade['variants']['indexed-body-invariant-v3']['differences']
                if row['difference'] == 'fn']
    return sorted(failures, key=lambda cid: (len(pairs_by_case.get(cid, set())),
                  hashlib.sha256(('nextdata-table-error-v1|' + cid).encode()).hexdigest()))[:count]


def packet_groups(items: list[dict], max_pairs: int = 2, max_options: int = 8) -> list[list[dict]]:
    """Never trim a whole option family; oversize families remain singleton."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    options = 0
    for item in items:
        size = len(item['payload']['candidate_vector'])
        if current and (len(current) >= max_pairs or options + size > max_options):
            groups.append(current)
            current, options = [], 0
        current.append(item)
        options += size
        if len(current) >= max_pairs or options >= max_options:
            groups.append(current)
            current, options = [], 0
    if current:
        groups.append(current)
    return groups


def make_packets(output: Path, variant: str, rows: list[dict], refs: set[int]) -> dict:
    directory = output / 'packets' / variant
    directory.mkdir(parents=True)
    instructions = PACKETS.WORKER_INSTRUCTIONS.replace(
        'This assignment is from the independent TEST split (`independent_test=true`, `test_split_read=true`). Do not seek, inspect, or infer from TEST labels or selected references.',
        'This is a consumed-TEST diagnostic (`independent_test=false`, `consumed_test_diagnostic=true`). Do not seek, inspect, or infer from labels, selected references, selection strata, or prior predictions.')
    instructions += '\n' + GUIDANCE.read_text(encoding='utf-8')
    items = [{'pair_ref': row['pair_ref'], 'payload': row[variant + '_payload']}
             for row in rows if row['pair_ref'] in refs]
    packets = []
    # Retain large singleton families in full; avoid joining two already large
    # contexts into one worker assignment.
    bounded_groups = []
    for group in packet_groups(items):
        candidate = {'protocol': VOLUME.INDEXED.PROTOCOL, 'instructions': instructions, 'pairs': group}
        bounded_groups.extend([[item] for item in group] if len(canon(candidate)) > 90000 and len(group) > 1 else [group])
    for ordinal, group in enumerate(bounded_groups, 1):
        file = f'packet-{ordinal:03d}.json'
        write_json(directory / file, {'protocol': VOLUME.INDEXED.PROTOCOL,
                   'instructions': instructions, 'pairs': group})
        packets.append({'file': file, 'pair_refs': [r['pair_ref'] for r in group],
                        'option_count': sum(len(r['payload']['candidate_vector']) for r in group),
                        'oversized_whole_option_family': any(len(r['payload']['candidate_vector']) > 8 for r in group),
                        'oversized_single_payload': len(canon({'protocol': VOLUME.INDEXED.PROTOCOL,
                            'instructions': instructions, 'pairs': group})) > 90000,
                        **pin(directory / file)})
    index = {'schema_version': 'sku-nextdata-table-packet-index-v1', 'variant': variant,
             'pair_count': len(items), 'packet_count': len(packets), 'packets': packets,
             'max_pairs': 2, 'max_options_soft': 8, 'option_families_trimmed': False, **FLAGS}
    write_json(directory / 'index.json', index)
    return index


def prepare(output: Path = DEFAULT_OUTPUT, config: Path = DEFAULT_CONFIG,
            live_inventory: Path | None = None) -> dict:
    bundle = VOLUME.Bundle(config)
    source = bundle.source
    VOLUME.validate_partition_receipt(bundle, bundle.preflight_output)
    refs = VOLUME.read_rows(bundle.preflight_output / 'pair-ref-index.jsonl')
    ref_by_id = VOLUME.unique_index(refs, 'pair_id', 'original pair refs')
    dispatch = set(source['partition']['dispatch_pair_ids'])
    if len(source['pair_by_id']) != 1976 or len(dispatch) != 971:
        raise ValueError('original prepared/dispatch population differs')
    if source['counts']['selected_case_count'] != 569 or source['counts']['task_count'] != 62252:
        raise ValueError('original case/task population differs')
    bodies = VOLUME.unique_index(source['scoped_rows']['source-bodies.jsonl'], 'document_sha256', 'original bodies')
    membership = source['cohort_rows']['membership.jsonl']
    pairs_by_case = case_dispatch_pairs(source['bindings'], dispatch)
    products = ordered_products(membership)
    main_cases = {r['case_id'] for r in membership if str(r['au_product_id']) in products}
    old_grade = VOLUME.read_json(OLD_GRADE)
    errors = select_error_cases(old_grade, pairs_by_case)
    main_pairs = set().union(*(pairs_by_case.get(cid, set()) for cid in main_cases))
    error_pairs = set().union(*(pairs_by_case.get(cid, set()) for cid in errors))
    main_refs = {ref_by_id[p]['pair_ref'] for p in main_pairs}
    error_refs = {ref_by_id[p]['pair_ref'] for p in error_pairs}
    output = fresh_dir(output)
    live_adapter = importlib.import_module('sku_live_nextdata_body_adapter_v1') if live_inventory else None
    description_proof = None
    if live_adapter is not None:
        description_proof = live_adapter.verify_description_sourceproof(bodies, live_inventory,
            source_root=bundle.source_root, live_root=live_inventory.parent)
        write_json(output / 'public-description-sourceproof.json', description_proof)
    public_rows, maps, audits = [], [], []
    for pair_id in sorted(dispatch, key=lambda p: ref_by_id[p]['pair_ref']):
        ref = ref_by_id[pair_id]['pair_ref']
        pair, payload = source['pair_by_id'][pair_id], source['payload_by_id'][pair_id]
        if live_adapter is not None:
            full_base, table_base, live_context, audit = live_adapter.build_live_payload_views(
                payload, bodies, live_inventory, source_root=bundle.source_root,
                live_root=live_inventory.parent)
            # Normalized sections have their own exact raw-leaf source proof.
            # Their indices cannot be expanded into historical BODY text.
            mapping = {}
            if full_base != payload:
                raise ValueError('exact-equivalence NEXT adapter changed the frozen full-body control')
        else:
            full_base = payload
            table_base, mapping, audit = TABLE.apply_table_view(payload, bodies, bundle.source_root)
            live_context = None
        full = PACKETS._public_payload(pair, full_base, ref)
        table = PACKETS._public_payload(pair, table_base, ref)
        if table['candidate_vector'] != full['candidate_vector']:
            raise ValueError('table view changed original whole options')
        original_context, table_context = full['context'], table['context']
        untouched = set(original_context) - {'eligible_source_bodies', 'eligible_target_body'}
        if any(original_context[k] != table_context[k] for k in untouched):
            raise ValueError('table view changed native inventory or selected target facts')
        public_rows.append({'pair_ref': ref, 'pair_id': pair_id,
                            'full_payload': full, 'table_payload': table})
        maps.append({'pair_ref': ref, 'pair_id': pair_id,
                     'target_document_sha256': payload['context']['target_document_sha256'],
                     'body_index_maps': mapping, 'live_validation_context': live_context})
        audits.append({'pair_ref': ref, 'audit': audit})
    write_rows(output / 'public-paired-inputs.jsonl', public_rows)
    write_rows(output / 'private-pair-body-index-maps.jsonl', maps)
    write_rows(output / 'public-table-audits.jsonl', audits)
    write_json(output / 'gpu-public-input-freeze.json', {
        'schema_version': 'sku-nextdata-table-public-input-freeze-v1',
        'dispatch_pair_count': len(public_rows), 'selected_product_count': 46,
        'selected_case_count': 569, 'row_count': 13159, 'task_count': 62252,
        'option_count': sum(len(r['full_payload']['candidate_vector']) for r in public_rows),
        'public_input_sha256': VOLUME.sha_file(output / 'public-paired-inputs.jsonl'),
        'public_input_bytes': (output / 'public-paired-inputs.jsonl').stat().st_size,
        'private_labels_or_task_bindings_included': False,
        'whole_option_vectors_and_opposite_selected_facts_preserved': True, **FLAGS})
    ordered_original_refs = [row for row in sorted(refs, key=lambda r: r['pair_ref']) if row['pair_id'] in dispatch]
    original_options = [{'pair_ref': row['pair_ref'],
        'axis_name': source['payload_by_id'][row['pair_id']]['context']['hypothesis']['axis_name'],
        'option_values': source['pair_by_id'][row['pair_id']]['option_values']} for row in ordered_original_refs]
    option_count = sum(len(row['option_values']) for row in original_options)
    if option_count != 4268 or source['counts']['row_count'] != 13159 or len(bodies) != 92:
        raise ValueError('original option/row/body population differs from completed volume')
    def body_vector(variant):
        return [{'pair_ref': row['pair_ref'],
                 'eligible_source_bodies': row[variant + '_payload']['context']['eligible_source_bodies'],
                 'eligible_target_body': row[variant + '_payload']['context']['eligible_target_body']}
                for row in public_rows]
    public_candidate = {'schema_version': 'sku-nextdata-description-public-population-candidate-v1',
        'pair_count': len(ordered_original_refs), 'product_count': source['counts']['selected_product_count'],
        'case_count': source['counts']['selected_case_count'], 'row_count': source['counts']['row_count'],
        'task_count': source['counts']['task_count'], 'option_prediction_count': 2 * option_count,
        'single_variant_option_count': option_count,
        'pair_ref_vector_sha256': VOLUME.digest_canonical([row['pair_ref'] for row in ordered_original_refs]),
        'option_vector_sha256': VOLUME.digest_canonical(original_options),
        'full_body_vector_sha256': VOLUME.digest_canonical(body_vector('full')),
        'description_body_vector_sha256': VOLUME.digest_canonical(body_vector('table')),
        'public_input_sha256': VOLUME.sha_file(output / 'public-paired-inputs.jsonl'),
        'sourceproof_receipt_sha256': VOLUME.sha_file(output / 'public-description-sourceproof.json') if description_proof else None,
        'population_derived_from_original_sources': True, 'root_verified': False, **FLAGS}
    write_json(output / 'candidate-public-population-freeze.json', public_candidate)
    policy = {'schema_version': 'sku-nextdata-table-selection-policy-v1',
              'main_policy': "first 12 products sorted by sha256('nextdata-table-pilot-v1|' + product_id); all cases and whole-case dispatch pairs",
              'error_policy': "first 8 previous FN cases sorted by (whole-case required dispatch pair count, sha256('nextdata-table-error-v1|' + case_id))",
              'main_comparison': 'fresh table Luna vs historical completed full-body first pass; not fresh paired main',
              'error_comparison': 'fresh full and fresh table Luna; overlap uses the same single table first pass',
              'gpu_comparison': 'all original 569 whole cases; paired full/table inputs; scope-relaxed diagnostic',
              'source_mode': 'cleaned_description_fields_with_full_fallback' if live_adapter else 'historical_api_field_control',
              'public_table_payload_alias': 'cleaned_description_fields_with_full_fallback' if live_adapter else 'historical_table_subset',
              'table_policy': ('exact user collector across extraItemComment/extraItemCommentWithVideo/itemComment/detailComment; exact-HTML deduplication, preserved cell/line boundaries and media metadata; original full-body input fallback when no description text'
                  if live_adapter else 'retain existing visible BODY-v3 text blocks beneath historical API extraItemComment table DOM nodes'),
              'original_sku_facts_inventory_and_whole_options_changed': False,
              'reference_kind': 'claude-haiku-5-5 machine annotation; not human gold', **FLAGS}
    write_json(output / 'selection-policy.json', policy)
    selection = {'main_product_ids': products, 'main_case_ids': sorted(main_cases),
                 'error_case_ids': errors, 'main_pair_refs': sorted(main_refs), 'error_pair_refs': sorted(error_refs),
                 'table_pair_refs': sorted(main_refs | error_refs), 'full_pair_refs': sorted(error_refs),
                 'gpu_pair_refs': [row['pair_ref'] for row in public_rows], **FLAGS}
    write_json(output / 'private-selection.json', selection)
    # The historical control includes complete original rows/tasks, not just winners.
    for name in ('task-resolutions.jsonl', 'all-row-results.jsonl', 'case-decisions.jsonl'):
        shutil.copyfile(BASELINE / name, output / ('historical-full-' + name))
    table_packets = make_packets(output, 'table', public_rows, main_refs | error_refs)
    full_packets = make_packets(output, 'full', public_rows, error_refs)
    dependency_paths = [p for directory in (bundle.prepared, bundle.scoped, bundle.proofs,
                        VOLUME.IDENTITY_PLAN_DIR) for p in sorted(directory.rglob('*')) if p.is_file()]
    dependency_paths += [bundle.source_inventory, bundle.raw_capture_inventory_path]
    dependency_paths += [bundle.source_root / row['path'] for row in bundle.raw_capture_inventory['captures']]
    dependencies = {str(p.resolve()): pin(p) for p in dependency_paths}
    dependencies.update({'runner_config': pin(config), 'old_private_grade': pin(OLD_GRADE),
                         'old_baseline_tasks': pin(BASELINE / 'task-resolutions.jsonl'),
                         'old_baseline_rows': pin(BASELINE / 'all-row-results.jsonl'),
                         'old_baseline_decisions': pin(BASELINE / 'case-decisions.jsonl'),
                         'old_grading_freeze': pin(OLD_EXECUTION / 'grading-freeze-v1.json')})
    code_paths = [Path(__file__), HERE / 'sku_nextdata_table_view_v1.py', GUIDANCE,
                  HERE / 'prepare_body_indexed_volume_packets_v1.py', HERE / 'run_indexed_volume_test_v1.py',
                  HERE / 'sku_body_invariant_indexed_transport_v1.py', HERE / 'sku_body_invariant_choice_v3.py',
                  HERE / 'sku_body_invariant_choice_v3.schema.json', HERE / 'evaluate_dev_source_bound_whole_sku_gate_v1.py',
                  HERE / 'grade_final_test_subset_v1.py', bundle.scope_module_path]
    original_core_audit = VOLUME.read_json(OLD_EXECUTION / 'root-raw-classifier-hash-audit-v1.json')
    for relative, recorded in original_core_audit['checks'].items():
        path = ROOT / relative
        if VOLUME.sha_file(path) != recorded['raw_sha256']:
            raise ValueError('original semantic core changed since completed volume: ' + relative)
        code_paths.append(path)
    dependencies.update({p.name: pin(p) for p in code_paths})
    if live_adapter is not None:
        dependencies['live_source_adapter'] = pin(Path(live_adapter.__file__))
        dependencies.update({'live/' + p.relative_to(live_inventory.parent).as_posix(): pin(p)
                             for p in sorted(live_inventory.parent.rglob('*')) if p.is_file()})
        for path in (HERE / 'collect_au_description_v1.cjs',
                     ROOT / '.deps/sku-nextdata-cheerio/package.json',
                     ROOT / '.deps/sku-nextdata-cheerio/package-lock.json',
                     live_inventory.parent.parent / 'provided-description-extractor-results-v1.json'):
            dependencies[str(path.resolve())] = pin(path)
    sourcefiles = {str(p.resolve()): pin(p) for p in sorted(output.rglob('*')) if p.is_file()}
    freeze = {'schema_version': 'sku-nextdata-table-input-freeze-v1',
              'all_inputs_and_selection_frozen': True, 'runner_config': str(config.resolve()),
              'source_mode': policy['source_mode'],
              'live_capture_inventory': str(live_inventory.resolve()) if live_inventory else None,
              'full_payload_is_original_baseline': True,
              'prepared_pair_count': len(source['pair_by_id']), 'dispatch_pair_count': len(public_rows),
              'original_source_body_count': len(bodies), **source['counts'],
              'main_product_count': len(products), 'main_case_count': len(main_cases),
              'main_dispatch_pair_count': len(main_refs), 'error_case_count': len(errors),
              'error_dispatch_pair_count': len(error_refs), 'table_pair_count': len(main_refs | error_refs),
              'full_pair_count': len(error_refs), 'table_packet_count': table_packets['packet_count'],
              'full_packet_count': full_packets['packet_count'],
              'original_private_grade_read_for_error_selection': True,
              'runtime_versions': {'python': sys.version.split()[0],
                  'node': subprocess.run(['node', '--version'], capture_output=True, text=True, check=True).stdout.strip()
                      if live_adapter is not None else None,
                  'cheerio': description_proof.get('cheerio_version') if description_proof else None},
              'new_model_inference_run': False, 'files': sourcefiles, 'dependencies': dependencies, **FLAGS}
    write_json(output / 'input-freeze.json', freeze)
    return {k: v for k, v in freeze.items() if k not in {'files', 'dependencies'}} | {
        'public_input_path': str(output / 'public-paired-inputs.jsonl'),
        'input_freeze_sha256': VOLUME.sha_file(output / 'input-freeze.json')}


def load_experiment(experiment: Path):
    frozen = VOLUME.read_json(experiment / 'input-freeze.json')
    if frozen.get('independent_test') is not False or frozen.get('consumed_test_diagnostic') is not True:
        raise ValueError('experiment must be an explicitly consumed-TEST diagnostic')
    verify_pins(frozen['files'])
    verify_pins(frozen['dependencies'])
    bundle = VOLUME.Bundle(Path(frozen['runner_config']))
    public = {r['pair_ref']: r for r in VOLUME.read_rows(experiment / 'public-paired-inputs.jsonl')}
    maps = {r['pair_ref']: r for r in VOLUME.read_rows(experiment / 'private-pair-body-index-maps.jsonl')}
    return frozen, bundle, public, maps, VOLUME.read_json(experiment / 'private-selection.json')


def remap_table_response(raw: dict, table_payload: dict, original_payload: dict,
                         pair: dict, ref: int, mapping: dict) -> dict:
    # First validate against precisely the supplied subset. This prevents a
    # quotation or positive invariant from silently citing omitted full text.
    VOLUME.INDEXED.render_indexed_response(raw, pair, table_payload, ref)
    changed = deepcopy(raw)
    target_sha = original_payload['context']['target_document_sha256']
    target_map = mapping['body_index_maps'].get(target_sha)
    if target_map is None:  # Rakuten target was not filtered.
        return changed
    by_local = {row['local_index']: row for row in target_map}
    for decision in changed['decisions']:
        for quote in decision['body_quote_refs']:
            index = quote['block_index']
            if type(index) is not int or index not in by_local:
                raise ValueError('citation index was not supplied in table view')
            quote['block_index'] = by_local[index]['original_body_index']
    return changed


def subset_source(bundle, case_ids: set[str]) -> dict:
    source = bundle.source
    cohort = source['cohort_rows']
    tasks = [r for r in cohort['tasks.jsonl'] if r['case_id'] in case_ids]
    ids = {r['task_id'] for r in tasks}
    return {'cases': [r for r in cohort['case-plans.jsonl'] if r['case_id'] in case_ids],
            'rows': [r for r in cohort['row-plans.jsonl'] if r['case_id'] in case_ids],
            'membership': [r for r in cohort['membership.jsonl'] if r['case_id'] in case_ids],
            'tasks': tasks, 'bindings': [r for r in source['bindings'] if r['task_binding']['task_id'] in ids],
            'proofs': [r for r in source['literal_states'] if r['task_id'] in ids],
            'receipts': [r for r in source['proof_rows']['task-source-verification.jsonl'] if r['task_id'] in ids]}


def save_gate(output: Path, subset: dict, resolutions: list[dict], method: str) -> dict:
    gate = VOLUME.EVAL.finish_whole_sku_gate('choice_nli_full', subset['cases'], subset['rows'],
                                           subset['tasks'], resolutions)
    gate['method'] = method
    gate.update(FLAGS)
    write_rows(output / 'task-resolutions.jsonl', resolutions)
    write_rows(output / 'all-row-results.jsonl', gate['rows'])
    write_rows(output / 'case-decisions.jsonl', gate['decisions'])
    write_rows(output / 'membership.jsonl', subset['membership'])
    return {'case_count': len(gate['decisions']), 'row_count': len(gate['rows']),
            'task_count': len(resolutions), 'task_state_counts': gate['task_state_counts'],
            'row_state_counts': gate['row_state_counts'], 'decision_counts': gate['decision_counts'],
            'method': method}


def freeze_collection(output: Path, experiment: Path, receipt: dict) -> dict:
    write_json(output / 'collection-receipt.json', {**receipt, **FLAGS})
    freeze = {'schema_version': 'sku-nextdata-table-output-freeze-v1',
              'all_predictions_and_gates_frozen': True, 'labels_read_for_new_grade': False,
              'input_freeze': pin(experiment / 'input-freeze.json'),
              'files': {str(p.resolve()): pin(p) for p in sorted(output.rglob('*')) if p.is_file()}, **FLAGS}
    write_json(output / 'output-freeze.json', freeze)
    return {**receipt, **FLAGS, 'output_freeze_sha256': VOLUME.sha_file(output / 'output-freeze.json')}


def collect_luna(experiment: Path, variant: str, drafts: Path, output: Path) -> dict:
    _, bundle, public, maps, selection = load_experiment(experiment)
    index = VOLUME.read_json(experiment / 'packets' / variant / 'index.json')
    output = fresh_dir(output)
    bodies = VOLUME.unique_index(bundle.source['scoped_rows']['source-bodies.jsonl'], 'document_sha256', 'bodies')
    validated, issues, raw_receipts, rendered_responses, citation_metadata = [], [], [], [], []
    for packet in index['packets']:
        path = drafts / (Path(packet['file']).stem + '.jsonl')
        assigned = packet['pair_refs']
        if not path.is_file():
            issues.extend({'pair_ref': ref, 'status': 'missing_unknown'} for ref in assigned)
            continue
        raw = path.read_bytes()
        dest = output / 'raw' / path.name
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(raw)
        raw_receipts.append({'packet_file': packet['file'], 'original_path': str(path.resolve()), **pin(dest)})
        try:
            rows = parse_assigned_draft(raw, assigned)
        except (ValueError, TypeError, UnicodeError) as exc:
            issues.extend({'pair_ref': ref, 'status': 'invalid_packet_unknown', 'error': str(exc)} for ref in assigned)
            continue
        for raw_response, ref in zip(rows, assigned):
            pub = public[ref]
            pair_id = pub['pair_id']
            pair = bundle.source['pair_by_id'][pair_id]
            try:
                rendered, evidence = validate_luna_response(raw_response, pair, pub, ref, maps[ref],
                                                            bundle, bodies, variant)
                if evidence.get('source_citations_verified') is not True:
                    raise ValueError('source provenance validator did not verify supplied citations')
                judgments = [{**d, 'citations_verified': 2 * len(d.get('target_fact_indices', []))
                              + len(d.get('body_quote_refs', []))} for d in rendered['decisions']]
                validated.append({'pair_id': pair_id, 'cache_key': pair['cache_key'], 'judgments': judgments,
                    'source_citations_verified': evidence.get('source_citations_verified') is True,
                    'semantic_truth_verified': False, 'sourceproof_certified': False,
                    'production_adoption_allowed': False})
                rendered_responses.append(rendered)
                citation_metadata.extend(evidence.get('citations', []))
            except (KeyError, ValueError, TypeError, OSError, VOLUME.JsonSchemaValidationError) as exc:
                issues.append({'pair_ref': ref, 'status': 'invalid_response_unknown', 'error': str(exc)})
    write_rows(output / 'pair-failures.jsonl', issues)
    write_rows(output / 'original-firstpass-receipts.jsonl', raw_receipts)
    write_rows(output / 'validated-pairs.jsonl', validated)
    write_rows(output / 'rendered-v3-responses.jsonl', rendered_responses)
    write_rows(output / 'verified-source-citation-metadata.jsonl', citation_metadata)
    case_ids = set(selection['main_case_ids']) | set(selection['error_case_ids']) if variant == 'table' else set(selection['error_case_ids'])
    subset = subset_source(bundle, case_ids)
    resolutions = VOLUME.EVAL.build_choice_nli_task_resolutions(subset['tasks'], subset['bindings'], validated,
                                                              subset['proofs'], subset['receipts'])
    gate = save_gate(output, subset, resolutions,
        'frozen_luna_semantic_schema_whole_gate_with_description_provenance_adapter'
        if variant == 'table' and any(m.get('live_validation_context') is not None for m in maps.values())
        else 'unchanged_luna_source_citation_scope_and_whole_sku_gate')
    return freeze_collection(output, experiment, {'model_route': 'hosted-gpt-6-luna', 'variant': variant,
              'assigned_pair_count': index['pair_count'], 'validated_pair_count': len(validated),
              'missing_or_invalid_pair_count': len(issues), 'semantic_retries': 0,
              'public_table_payload_alias': 'cleaned_description_fields_with_full_fallback'
                  if any(m.get('live_validation_context') is not None for m in maps.values()) else 'historical_table_subset',
              'gate': gate})


def validate_luna_response(raw: dict, pair: dict, public: dict, ref: int, mapping: dict,
                           bundle, source_bodies: dict, variant: str) -> tuple[dict, dict]:
    payload = public[variant + '_payload']
    body = source_bodies[payload['context']['target_document_sha256']]
    live_context = mapping.get('live_validation_context')
    if variant == 'table' and live_context is not None:
        # The model quotes precisely the normalized sections it was supplied.
        # A separate provenance adapter verifies those sections and their raw
        # JSON leaves; no quote is rewritten into an older body representation.
        rendered = VOLUME.INDEXED.render_indexed_response(raw, pair, payload, ref)
        adapter = importlib.import_module('sku_live_nextdata_body_adapter_v1')
        evidence = adapter.validate_live_source_response(rendered, pair, payload, body,
            bundle.source_root, live_context, variant='description')
    else:
        original = public['full_payload']
        authored = (remap_table_response(raw, payload, original, pair, ref, mapping)
                    if variant == 'table' else raw)
        rendered = VOLUME.INDEXED.render_indexed_response(authored, pair, original, ref)
        evidence = bundle.scope.validate_source_response(rendered, pair, original, body, bundle.source_root)
    return rendered, evidence


def parse_assigned_draft(raw: bytes, assigned: list[int]) -> list[dict]:
    if not raw or not raw.endswith(b'\n') or any(not line.strip() for line in raw.splitlines()):
        raise ValueError('first draft must be nonempty LF-terminated JSONL without blank rows')
    rows = [PACKETS.strict_loads(line) for line in raw.splitlines()]
    if any(not isinstance(row, dict) or type(row.get('pair_ref')) is not int for row in rows):
        raise ValueError('first draft pair_ref must be an integer')
    if [row['pair_ref'] for row in rows] != assigned:
        raise ValueError('first draft does not preserve complete assigned pair refs and order')
    return rows


def small_pair_states(row: dict, pair: dict, expected_ref: int) -> list[str]:
    if type(row.get('pair_ref')) is not int or row['pair_ref'] != expected_ref:
        raise ValueError('small-model pair_ref differs')
    if row.get('pair_id', pair['pair_id']) != pair['pair_id']:
        raise ValueError('small-model pair_id differs')
    decisions = row.get('decisions')
    if not isinstance(decisions, list) or len(decisions) != len(pair['option_values']):
        raise ValueError('small-model decisions must cover every whole original option')
    if any(not isinstance(d, dict) or type(d.get('option_index')) is not int for d in decisions):
        raise ValueError('small-model option index is not an integer')
    if [d['option_index'] for d in decisions] != list(range(len(decisions))):
        raise ValueError('small-model option indices differ from full original order')
    states = [d.get('state') for d in decisions]
    if any(s not in {'support', 'explicit_conflict', 'unknown'} for s in states):
        raise ValueError('small-model state outside support/explicit_conflict/unknown')
    return ['conflict' if s == 'explicit_conflict' else s for s in states]


def small_model_resolutions(subset: dict, states_by_pair: dict[str, list[str]], pairs: dict[str, dict]) -> list[dict]:
    """Categorical diagnostic only: never synthesize semantic scope or citations."""
    binding_by_task = {r['task_binding']['task_id']: r for r in subset['bindings']}
    proof_by_task = VOLUME.EVAL.index(subset['proofs'], 'task_id', 'proof')
    receipt_by_task = VOLUME.EVAL.index(subset['receipts'], 'task_id', 'receipt')
    results = []
    for task in subset['tasks']:
        tid = task['task_id']
        literal = VOLUME.EVAL.source_literal_state(task, proof_by_task[tid], receipt_by_task[tid])
        if literal is not None:
            results.append(literal)
            continue
        binding = binding_by_task[tid]
        pair_id = binding.get('pair_id')
        desired = task['raw_condition_ref']['value']
        states = states_by_pair.get(pair_id)
        state = 'unknown'
        if states is not None and binding.get('preparation_status') == 'ready':
            if (binding.get('source_selected_fact') or {}).get('value') != desired:
                raise ValueError('small-model private selected option differs from original task')
            options = pairs[pair_id]['option_values']
            found = [i for i, option in enumerate(options) if option == desired]
            if len(found) == 1:
                state = states[found[0]]
        results.append({'task_id': tid, 'request_id': task['request_id'], 'case_id': task['case_id'],
                        'au_row_key': task['au_row_key'], 'direction': task['direction'],
                        'axis_name': task['raw_condition_ref']['axis_name'], 'selected_value': desired,
                        'pair_id': pair_id, 'state': state, 'source': 'small_model_relaxed_scope_diagnostic',
                        'source_citations_verified': False, **FLAGS})
    return results


def collect_gpu(experiment: Path, variant: str, raw_path: Path, output: Path, model_id: str) -> dict:
    _, bundle, public, _, _ = load_experiment(experiment)
    output = fresh_dir(output)
    shutil.copyfile(raw_path, output / 'original-small-model-firstpass.jsonl')
    states, failures, seen = {}, [], set()
    for ordinal, line in enumerate(raw_path.read_bytes().splitlines(), 1):
        ref = None
        try:
            row = PACKETS.strict_loads(line)
            ref = row.get('pair_ref')
            if type(ref) is not int or ref not in public or ref in seen:
                raise ValueError('small-model reference missing, foreign, or duplicate')
            seen.add(ref)
            pair_id = public[ref]['pair_id']
            states[pair_id] = small_pair_states(row, bundle.source['pair_by_id'][pair_id], ref)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            # A duplicated ref invalidates that ref as well as the duplicate.
            if type(ref) is int and ref in public:
                states.pop(public[ref]['pair_id'], None)
            failures.append({'line': ordinal, 'status': 'invalid_unknown', 'error': str(exc)})
    subset = subset_source(bundle, {r['case_id'] for r in bundle.source['cohort_rows']['membership.jsonl']})
    resolutions = small_model_resolutions(subset, states, bundle.source['pair_by_id'])
    gate = save_gate(output, subset, resolutions, 'small_model_relaxed_scope_diagnostic_same_whole_sku_gate')
    if (gate['case_count'], gate['row_count'], gate['task_count']) != (569, 13159, 62252):
        raise ValueError('GPU whole-case population differs from original complete source')
    write_rows(output / 'pair-failures.jsonl', failures)
    return freeze_collection(output, experiment, {'variant': variant, 'model_id': model_id,
              'valid_pair_count': len(states), 'expected_pair_count': len(public),
              'missing_or_invalid_pair_count': len(public) - len(states),
              'raw_file': pin(output / 'original-small-model-firstpass.jsonl'),
              'scope_validation_relaxed': True, 'source_citations_manufactured': False, 'gate': gate})


def derive_reference_cache(old_grade: dict, historical: list[dict], membership: list[dict]) -> dict:
    """Reconstruct the already-consumed fixed annotations, then reconcile scorer.

    Correct historical accepts bind the exact reference row because the old
    scorer independently validated it. All differing/unknown cases use the
    original stored postfreeze diagnostic. No labels are invented for unknowns.
    """
    scored = old_grade['variants']['indexed-body-invariant-v3']
    differences = VOLUME.unique_index(scored['differences'], 'case_id', 'old difference')
    unknown = set(scored['unknown_reference_case_ids'])
    references = {}
    for prediction in historical:
        cid = prediction['case_id']
        if cid in unknown:
            references[cid] = {'label': 'unknown', 'matched_au_row_keys': None}
        elif cid in differences:
            difference = differences[cid]
            references[cid] = {'label': difference['label'], 'matched_au_row_keys': difference['reference_row_keys']}
        elif prediction['decision'] == 'accept':
            references[cid] = {'label': 'matched', 'matched_au_row_keys': [prediction['top_row_key']]}
        elif prediction['decision'] == 'drop':
            references[cid] = {'label': 'unmatched', 'matched_au_row_keys': []}
        else:
            raise ValueError('old prediction cannot reconstruct a fixed reference')
    reproduced = VOLUME.SCORE_ORIGINAL.score_variant(membership, historical, references)
    for key in ('metrics', 'row_metrics', 'known_reference_case_count', 'unknown_reference_count'):
        if reproduced[key] != scored[key]:
            raise ValueError('reconstructed references do not reproduce original scoring: ' + key)
    return references


def paired_change(old: list[dict], new: list[dict], references: dict) -> dict:
    before = VOLUME.unique_index(old, 'case_id', 'old predictions')
    after = VOLUME.unique_index(new, 'case_id', 'new predictions')
    if set(before) != set(after):
        raise ValueError('paired comparison membership differs')
    counts = Counter()
    for cid in before:
        label, rows = VOLUME.SCORE_ORIGINAL.canonical_reference(references[cid])
        if label not in {'matched', 'unmatched'}:
            continue
        def correct(pred):
            decision, key = VOLUME.SCORE_ORIGINAL.canonical_decision(pred)
            return (decision == 'matched' and rows is not None and key in rows) if label == 'matched' else decision == 'unmatched'
        a, b = correct(before[cid]), correct(after[cid])
        counts['recovered' if b and not a else 'lost' if a and not b else 'unchanged_correct' if a else 'unchanged_incorrect'] += 1
    return {key: counts[key] for key in ('recovered', 'lost', 'unchanged_correct', 'unchanged_incorrect')}


def grade(experiment: Path, collections: list[Path], output: Path) -> dict:
    frozen, bundle, public, _, selection = load_experiment(experiment)
    collection_freezes = []
    for collection in collections:
        freeze = VOLUME.read_json(collection / 'output-freeze.json')
        if freeze.get('all_predictions_and_gates_frozen') is not True or freeze['input_freeze'] != pin(experiment / 'input-freeze.json'):
            raise ValueError('new predictions/gates are not bound to this input freeze')
        verify_pins(freeze['files'])
        collection_freezes.append(pin(collection / 'output-freeze.json'))
    # New output/code/source pins have all passed before accessing the old grade.
    historical = VOLUME.read_rows(experiment / 'historical-full-case-decisions.jsonl')
    refs = derive_reference_cache(VOLUME.read_json(OLD_GRADE), historical, bundle.source['cohort_rows']['membership.jsonl'])
    baseline_by_id = VOLUME.unique_index(historical, 'case_id', 'historical baseline')
    output = fresh_dir(output)
    results, comparable = {}, []
    audit_by_ref = {row['pair_ref']: row['audit'] for row in VOLUME.read_rows(experiment / 'public-table-audits.jsonl')}
    dispatched = set(bundle.source['partition']['dispatch_pair_ids'])
    refs_by_pair = {row['pair_id']: ref for ref, row in public.items()}
    pairs_by_case = case_dispatch_pairs(bundle.source['bindings'], dispatched)
    def table_stratum(cid):
        pair_ids = pairs_by_case.get(cid, set())
        if not pair_ids:
            return 'literal_only_no_dispatch'
        return 'with_tables' if any(audit_by_ref[refs_by_pair[pid]].get('table_count', 0) > 0 for pid in pair_ids) else 'without_tables'
    for collection in collections:
        receipt = VOLUME.read_json(collection / 'collection-receipt.json')
        predictions = VOLUME.read_rows(collection / 'case-decisions.jsonl')
        membership = VOLUME.read_rows(collection / 'membership.jsonl')
        cidset = {r['case_id'] for r in membership}
        metrics = VOLUME.SCORE_ORIGINAL.score_variant(membership, predictions, {cid: refs[cid] for cid in cidset})
        metrics['independent_test'] = False
        metrics['consumed_test_diagnostic'] = True
        unknown = set(metrics['unknown_reference_case_ids'])
        metrics['unknown_accepted_count'] = sum(p['decision'] == 'accept' and p['case_id'] in unknown for p in predictions)
        positive = metrics['metrics']['tp'] + metrics['metrics']['fn']
        metrics['exact_row_recall'] = metrics['row_metrics'].get('correct_accepted_row', 0) / positive if positive else None
        known = metrics['metrics']['scored_case_count']
        metrics['exact_row_known_accuracy'] = (metrics['row_metrics'].get('correct_accepted_row', 0)
            + metrics['metrics']['tn']) / known if known else None
        metrics['known_wrong_accepted_count'] = metrics['metrics']['fp'] + metrics['row_metrics'].get('wrong_accepted_row', 0)
        metrics['paired_change_vs_historical_full'] = paired_change([baseline_by_id[c] for c in cidset], predictions, refs)
        write_json(output / (collection.name + '-private-score.json'), metrics)
        aggregate = {k: v for k, v in metrics.items() if k not in {'excluded', 'differences', 'unknown_reference_case_ids'}}
        aggregate['model_route'] = receipt.get('model_route', receipt.get('model_id'))
        aggregate['variant'] = receipt['variant']
        aggregate['gate'] = receipt['gate']
        aggregate['table_presence_subsets'] = {}
        for table_group in ('with_tables', 'without_tables', 'literal_only_no_dispatch'):
            chosen = {cid for cid in cidset if table_stratum(cid) == table_group}
            if chosen:
                scored = VOLUME.SCORE_ORIGINAL.score_variant(
                    [m for m in membership if m['case_id'] in chosen],
                    [p for p in predictions if p['case_id'] in chosen], {c: refs[c] for c in chosen})
                aggregate['table_presence_subsets'][table_group] = {'case_count': len(chosen),
                    'metrics': scored['metrics'], 'row_metrics': scored['row_metrics'],
                    'unknown_reference_count': scored['unknown_reference_count']}
        # Main and error cohorts remain separately interpretable even with overlap.
        for stratum, key in (('main', 'main_case_ids'), ('error', 'error_case_ids')):
            chosen = cidset & set(selection[key])
            if not chosen:
                continue
            members = [m for m in membership if m['case_id'] in chosen]
            preds = [p for p in predictions if p['case_id'] in chosen]
            scored = VOLUME.SCORE_ORIGINAL.score_variant(members, preds, {c: refs[c] for c in chosen})
            aggregate[stratum] = {'case_count': len(chosen), 'metrics': scored['metrics'], 'row_metrics': scored['row_metrics'],
                'unknown_reference_count': scored['unknown_reference_count'],
                'paired_change_vs_historical_full': paired_change([baseline_by_id[c] for c in chosen], preds, refs)}
        results[collection.name] = aggregate
        comparable.append({'name': collection.name, 'route': aggregate['model_route'],
                           'variant': receipt['variant'], 'predictions': predictions})
    fresh_paired_comparisons = []
    for before in comparable:
        for after in comparable:
            if before['route'] != after['route'] or before['variant'] != 'full' or after['variant'] != 'table':
                continue
            before_ids = {p['case_id'] for p in before['predictions']}
            after_ids = {p['case_id'] for p in after['predictions']}
            ids = before_ids & after_ids
            if ids:
                fresh_paired_comparisons.append({'model_route': before['route'],
                    'full_collection': before['name'], 'table_collection': after['name'],
                    'case_count': len(ids), 'paired_change': paired_change(
                        [p for p in before['predictions'] if p['case_id'] in ids],
                        [p for p in after['predictions'] if p['case_id'] in ids], refs)})
    summary = {'schema_version': 'sku-nextdata-table-grade-v1', **FLAGS,
               'reference_kind': 'claude-haiku-5-5 machine annotation; not human gold',
               'new_predictions_and_gates_verified_before_reference_reconstruction': True,
               'source_labels_previously_consumed': True, 'references_reconstructed_from_fixed_previous_grade': True,
               'collection_freezes': collection_freezes, 'input_freeze': pin(experiment / 'input-freeze.json'),
               'main_comparison_is_historical_full_control': True, 'results': results}
    summary['fresh_full_vs_table_paired_comparisons'] = fresh_paired_comparisons
    write_json(output / 'aggregate-score.json', summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    prep.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    prep.add_argument('--live-inventory', type=Path)
    for name in ('collect-luna', 'collect-gpu'):
        sub = commands.add_parser(name)
        sub.add_argument('--experiment', type=Path, default=DEFAULT_OUTPUT)
        sub.add_argument('--variant', choices=('full', 'table'), required=True)
        sub.add_argument('--output', type=Path, required=True)
        if name == 'collect-luna':
            sub.add_argument('--drafts', type=Path, required=True)
        else:
            sub.add_argument('--raw-jsonl', type=Path, required=True)
            sub.add_argument('--model-id', required=True)
    scorer = commands.add_parser('grade')
    scorer.add_argument('--experiment', type=Path, default=DEFAULT_OUTPUT)
    scorer.add_argument('--collection', type=Path, action='append', required=True)
    scorer.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output, args.config, args.live_inventory)
    elif args.command == 'collect-luna':
        result = collect_luna(args.experiment, args.variant, args.drafts, args.output)
    elif args.command == 'collect-gpu':
        result = collect_gpu(args.experiment, args.variant, args.raw_jsonl, args.output, args.model_id)
    else:
        result = grade(args.experiment, args.collection, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
