#!/usr/bin/env python3
"""Independent audit of fixed image outputs and frozen-feature learned heads.

Read-only with respect to model artifacts. Partial/failed slots remain in the
receipt. Baseline/head comparisons always use the same held-out IDs, never the
historical full-board aggregate as a denominator.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys

PROJECT = Path('/workspace/Searching_Experiment/experiments/captcha-small-model')
sys.path.insert(0, str(PROJECT))
import summarize_public_eval as prior

PRIMARY_MODELS = ['MobileCLIP2-S2', 'MobileCLIP2-S3', 'PE-Core-S16-384', 'MobileCLIP2-B', 'MobileCLIP2-S4']
MANIFEST_SHA = '0d1a8999f091dbb574ef7c8d730b78a8eba37fbd645b436d64b1c7037dfc3c65'
SPLIT_SHA = {
 'train': '12f615e0c3690db0c7fdbc0d455b82c2648d929c339d287eb75f7ad99693ba6d',
 'validation': 'c20e2cf792b859d927b2431bcc3f576b60ba61f80e04ce73553dab8b6efc9beb',
 'test': '4a3c6073df0eec0ce462e968fc63f44910809caffdcb2850243aa88d972615c8',
}


def require(value, message):
    if not value: raise ValueError(message)


def sha(path):
    return prior.sha256_file(Path(path))


def load(path):
    return json.loads(Path(path).read_text())


def rows(path):
    return prior.jsonl_rows(Path(path))


def close(actual, expected, name, tolerance=1e-7):
    require(isinstance(actual, (float, int)) and math.isfinite(actual), f'{name}: non-finite value')
    require(math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance),
            f'{name}: stored {actual}, recomputed {expected}')


def literal_constant(path, name):
    for statement in ast.parse(Path(path).read_text()).body:
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target] if isinstance(statement, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(statement.value)
    raise ValueError(f'Missing source pin constant {name}')


def verify_inputs(manifest_path, recheck_images=True):
    require(sha(manifest_path) == MANIFEST_SHA, 'Unexpected restored manifest SHA')
    manifest = load(manifest_path)
    require((len(manifest['samples']), len(manifest['cases'])) == (4068, 1000), 'Unexpected input counts')
    labels = [r['label'] for r in manifest['classes']]
    require(len(labels) == len(set(labels)) == 16, 'Unexpected class list')
    all_rows = {r['id']: r for r in manifest['samples'] + manifest['cases']}
    require(len(all_rows) == 5068, 'Duplicate sample/board IDs')
    refs = [(r['path'], r['sha256']) for r in manifest['samples']]
    refs += [(path, digest) for case in manifest['cases'] for path, digest in zip(case['tile_paths'], case['tile_sha256'], strict=True)]
    unique = dict()
    path_hash = {}
    for path, digest in refs:
        require(path_hash.setdefault(path, digest) == digest, 'Path with conflicting image hash')
        unique.setdefault(digest, path)
    require((len(path_hash), len(unique)) == (15434, 15420), 'Unexpected unique image identities')
    if recheck_images:
        for path, digest in path_hash.items():
            require(sha(manifest_path.parent / path) == digest, f'Image SHA mismatch: {path}')
    splits = {}; ids = {}; content_folds = {}
    for fold, digest in SPLIT_SHA.items():
        path = manifest_path.parent / f'grouped-{fold}.json'
        require(sha(path) == digest, f'Unexpected {fold} split SHA')
        part = load(path)
        require(part['split']['fold'] == fold and part['split']['parent_manifest_sha256'] == MANIFEST_SHA, 'Split provenance mismatch')
        for row in part['samples'] + part['cases']:
            require(row == all_rows.get(row['id']), f'Split truth changed: {row["id"]}')
            require(row['id'] not in ids, f'Cross-fold ID: {row["id"]}')
            ids[row['id']] = fold
            hashes = [('pixels', row['pixel_sha256']), ('bytes', row['sha256'])] if 'path' in row else (
                [('pixels', x) for x in row['tile_pixel_sha256']] + [('bytes', x) for x in row['tile_sha256']])
            for key in hashes:
                require(content_folds.setdefault(key, fold) == fold, 'Identical image crosses folds')
        splits[fold] = part
    require(set(ids) == set(all_rows), 'Splits do not exactly cover input')
    require((len(splits['test']['samples']), len(splits['test']['cases'])) == (815, 203), 'Unexpected heldout denominator')
    return manifest, splits, labels, unique


def check_pin(model, provenance):
    if model.startswith('MobileCLIP2-'):
        expected = literal_constant(PROJECT / 'benchmark_embeddings.py', 'MOBILE_MODELS')[model]
    elif model == 'PE-Core-S16-384':
        expected = literal_constant(PROJECT / 'benchmark_pe_core.py', 'WEIGHT_SPEC')
    else:
        expected = literal_constant(PROJECT / 'extract_image_backbone_features.py', 'MODEL_SPECS')[model]
    for key in ('revision', 'filename', 'sha256'):
        require(provenance.get(key) == expected[key], f'{model}: declared weight pin mismatch {key}')
    actual_hash = provenance.get('file_sha256', provenance.get('local_checkpoint_sha256'))
    require(actual_hash == expected['sha256'], f'{model}: declared loaded checkpoint hash mismatch')
    return {key: expected[key] for key in ('revision', 'filename', 'sha256')}


def verify_zero_shot(model, folder, manifest, labels, validation_split):
    jsonl = folder / f'{model}.jsonl'
    protocol_path = folder / 'run_protocol.json'
    if not jsonl.exists(): return {'status': 'missing'}, None
    raw = rows(jsonl)
    protocol = load(protocol_path)
    require(protocol['input_sha256'] == MANIFEST_SHA and protocol['models'] == [model], 'Zero-shot protocol provenance mismatch')
    require(protocol['class_labels'] == labels, 'Zero-shot labels mismatch')
    require(protocol['source_classes'] == manifest['source_classes'], 'Zero-shot allowed label classes mismatch')
    samples = prior.check_sample_rows(raw, manifest['samples'], labels, model, manifest['source_classes'])
    boards = prior.check_board_rows(raw, manifest['cases'], model)
    require(len(raw) == 5068, 'Unknown/extra raw zero-shot rows')
    sample_metrics = prior.sample_metrics(samples, labels)
    board_metrics = prior.board_metrics(boards)
    aggregate_path = folder / 'aggregate.json'
    result = {'status': 'verified_saved_outputs', 'raw_predictions_sha256': sha(jsonl),
              'run_protocol_sha256': sha(protocol_path), 'classification': sample_metrics,
              'boards': board_metrics, 'process_completion_is_separate_from_output_integrity': True}
    if aggregate_path.exists():
        aggregate = load(aggregate_path)
        summary = prior.check_aggregate(aggregate, protocol, model, MANIFEST_SHA, labels)
        close(summary['accuracy'], sample_metrics['accuracy'], f'{model}/aggregate accuracy')
        require(summary['unique_images_encoded'] == 15420, 'Encoded unique image count mismatch')
        result.update({'aggregate_sha256': sha(aggregate_path), 'weight_pin': check_pin(model, summary['weights']),
                       'timing': {k: summary[k] for k in ('preprocessing_ms_total', 'image_encoder_and_scoring_ms_total', 'image_parameters', 'total_parameters', 'cuda_peak_allocated_mib')}})
    validation_sample_ids = {r['id'] for r in validation_split['samples']}
    validation_board_ids = {r['id'] for r in validation_split['cases']}
    result['validation'] = {
        'classification': prior.sample_metrics([r for r in samples if r['id'] in validation_sample_ids], labels),
        'boards': prior.board_metrics([r for r in boards if r['id'] in validation_board_ids]),
        'selection_role': 'candidate selection uses validation fold only; test is not used for candidate ranking',
    }
    require(result['validation']['classification']['count'] == 406 and result['validation']['boards']['count'] == 97, 'Validation denominators mismatch')
    result['boards_by_target'] = {label: prior.board_metrics([r for r in boards if r['target'] == label]) for label in sorted(set(r['target'] for r in manifest['cases']))}
    return result, {'samples': {r['id']: r for r in samples}, 'boards': {r['id']: r for r in boards}}


def verify_features(model, index_path, tensor_path, unique):
    import torch
    from safetensors.torch import load_file
    index = load(index_path)
    require(index['model'] == model and index['input_manifest_sha256'] == MANIFEST_SHA, 'Feature index identity mismatch')
    require(index['image_sha256'] == list(unique), 'Feature ordered unique SHA mapping mismatch')
    require(index['relative_paths'] == list(unique.values()), 'Feature canonical paths mismatch')
    require(index['features_filename'] == tensor_path.name and index['features_sha256'] == sha(tensor_path), 'Feature cache hash mismatch')
    require(index['tensor'] == 'embeddings' and index['dtype'] == 'float32', 'Feature tensor schema mismatch')
    tensors = load_file(str(tensor_path), device='cpu')
    require(set(tensors) == {'embeddings'}, 'Unexpected feature tensors')
    feature = tensors['embeddings']
    require(feature.dtype == torch.float32 and feature.ndim == 2 and list(feature.shape) == index['shape'] and feature.shape[0] == 15420, 'Feature dimensions/dtype mismatch')
    require(torch.isfinite(feature).all().item(), 'Non-finite features')
    norms = feature.norm(dim=1)
    require(torch.allclose(norms, torch.ones_like(norms), atol=.002, rtol=.002), 'Features not L2 normalized')
    proof = {'index_sha256': sha(index_path), 'features_sha256': sha(tensor_path), 'shape': list(feature.shape),
             'ordered_unique_image_identities_verified': 15420, 'finite_and_l2_normalized': True,
             'evaluator_sha256_declared': index.get('benchmark_evaluator_sha256')}
    if index.get('weight_provenance'): proof['weight_pin'] = check_pin(model, index['weight_provenance'])
    return feature, {digest: i for i, digest in enumerate(unique)}, proof


def paired(old, new):
    require(set(old) == set(new), 'Paired comparison IDs differ')
    counts = Counter((bool(old[key]), bool(new[key])) for key in old)
    n = len(old)
    base_correct = sum(old.values()); head_correct = sum(new.values())
    return {'count': n, 'baseline_correct': base_correct, 'head_correct': head_correct,
            'baseline_accuracy': base_correct/n if n else None, 'head_accuracy': head_correct/n if n else None,
            'delta_percentage_points': 100*(head_correct-base_correct)/n if n else None,
            'improved': counts[(False, True)], 'worsened': counts[(True, False)],
            'both_correct': counts[(True, True)], 'both_wrong': counts[(False, False)]}


def metrics_from_weights(logits, probabilities, split, labels, row_ix, weights):
    import torch
    import torch.nn.functional as F
    sample_correct = {}; board_correct = {}; board_selections = {}; by_class = {}
    observed_logits = []; targets = []; masks = []
    for sample in split['samples']:
        score = logits[row_ix[sample['sha256']]]
        allowed = sample['source_allowed_labels']; active = [labels.index(x) for x in allowed]
        pred = allowed[int(score[active].argmax().item())]
        sample_correct[sample['id']] = pred == sample['label']
        item = by_class.setdefault(sample['label'], {'count': 0, 'correct': 0})
        item['count'] += 1; item['correct'] += int(pred == sample['label'])
        target = torch.zeros(len(labels)); target[labels.index(sample['label'])] = 1
        mask = torch.zeros(len(labels)); mask[active] = weights[0]
        observed_logits.append(score); targets.append(target); masks.append(mask)
    by_board = {label: {'count': 0, 'correct': 0, 'accuracy': None, 'evaluated': False} for label in labels}
    for case in split['cases']:
        col = labels.index(case['target'])
        selected = [i for i, digest in enumerate(case['tile_sha256']) if probabilities[row_ix[digest], col].item() >= .5]
        gold = case['gold_selected_indices']
        board_selections[case['id']] = selected
        board_correct[case['id']] = selected == gold
        item = by_board[case['target']]; item['count'] += 1; item['correct'] += int(selected == gold)
        for i, digest in enumerate(case['tile_sha256']):
            target = torch.zeros(len(labels)); target[col] = int(i in gold)
            mask = torch.zeros(len(labels)); mask[col] = weights[1]
            observed_logits.append(logits[row_ix[digest]]); targets.append(target); masks.append(mask)
    for item in by_class.values(): item['accuracy'] = item['correct']/item['count']
    for item in by_board.values():
        if item['count']: item.update({'accuracy': item['correct']/item['count'], 'evaluated': True})
    mask = torch.stack(masks)
    loss = (F.binary_cross_entropy_with_logits(torch.stack(observed_logits), torch.stack(targets), reduction='none')*mask).sum()/mask.sum()
    return {'classification': {'count': len(sample_correct), 'correct': sum(sample_correct.values()),
                              'accuracy': sum(sample_correct.values())/len(sample_correct), 'by_target': by_class},
            'boards': {'count': len(board_correct), 'correct': sum(board_correct.values()),
                       'accuracy': sum(board_correct.values())/len(board_correct), 'by_target': by_board},
            'masked_bce': loss.item()}, sample_correct, board_correct, board_selections


def verify_head(model, head_dir, feature, feature_proof, row_ix, splits, labels, baseline):
    import torch
    import torch.nn.functional as F
    from safetensors.torch import load_file
    meta_path = head_dir / 'metadata.json'; prediction_path = head_dir / 'test_predictions.jsonl'; state_path = head_dir / 'linear_head.safetensors'
    meta = load(meta_path); provenance = meta['provenance']
    require(provenance['input_manifest_sha256'] == MANIFEST_SHA, 'Head input SHA mismatch')
    fingerprint = hashlib.sha256(json.dumps(SPLIT_SHA, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    require(provenance['split_manifests_sha256'] == fingerprint, 'Head split fingerprint mismatch')
    require(provenance['feature_index_sha256'] == feature_proof['index_sha256'] and provenance['features_sha256'] == feature_proof['features_sha256'], 'Head feature identities mismatch')
    require(provenance['head_sha256'] == sha(state_path), 'Head checkpoint SHA mismatch')
    require(meta['architecture']['labels'] == labels and meta['architecture']['output_dim'] == 16, 'Head labels mismatch')
    require(meta['architecture']['input_dim'] == feature.shape[1], 'Head input dimension mismatch')
    require(meta['selection']['fixed_board_threshold'] == .5 and meta['selection']['requested_count_not_used'] is True, 'Head selection rule changed')
    history = meta['history']; selection = meta['selection']; best_epoch = selection['best_epoch']
    require(len(history) == selection['epochs_run'] and all(r['epoch'] == i+1 for i, r in enumerate(history)), 'Head training history incomplete')
    best = next((r for r in history if r['epoch'] == best_epoch), None)
    require(best is not None and selection['criterion'] == 'validation masked BCE', 'Head checkpoint selection rule mismatch')
    close(selection['best_validation_masked_bce'], best['validation_masked_bce'], 'Chosen validation checkpoint')
    require(best['validation_masked_bce'] <= min(r['validation_masked_bce'] for r in history)+1.1e-6, 'Chosen checkpoint not validation minimum')
    state = load_file(str(state_path), device='cpu')
    require(set(state) == {'linear.weight', 'linear.bias'}, 'Unexpected head state tensors')
    require(tuple(state['linear.weight'].shape) == (16, feature.shape[1]) and tuple(state['linear.bias'].shape) == (16,), 'Head parameter shape mismatch')
    with torch.inference_mode():
        logits = F.linear(feature, state['linear.weight'], state['linear.bias'])
        probabilities = logits.sigmoid()
    require(torch.isfinite(logits).all().item(), 'Non-finite checkpoint logits')
    weights = (meta['training']['classification_observation_weight'], meta['training']['board_observation_weight'])
    computed = {}; fold_correct = {}
    for fold, split in splits.items():
        recomputed, class_ok, board_ok, selections = metrics_from_weights(logits, probabilities, split, labels, row_ix, weights)
        stored = meta['metrics'][fold]
        close(stored['classification']['accuracy'], recomputed['classification']['accuracy'], f'{fold} classification')
        close(stored['boards']['whole_board_exact_match'], recomputed['boards']['accuracy'], f'{fold} boards')
        close(stored['masked_bce'], recomputed['masked_bce'], f'{fold} masked BCE', 2e-6)
        require(stored['classification']['samples'] == recomputed['classification']['count'] and stored['boards']['count'] == recomputed['boards']['count'], 'Stored fold denominator mismatch')
        for label, expected_class in recomputed['classification']['by_target'].items():
            stored_class = stored['classification']['class_coverage'][label]
            require(stored_class['samples'] == expected_class['count'] and stored_class['correct'] == expected_class['correct'], 'Stored classification coverage mismatch')
            close(stored_class['accuracy'], expected_class['accuracy'], f'{fold}/{label} classification coverage')
        board_by_id = {case['id']: case for case in split['cases']}
        declared_boards = stored['boards']['predictions']
        require(len(declared_boards) == len(board_by_id) and {r['id'] for r in declared_boards} == set(board_by_id), 'Stored metadata board IDs incomplete/duplicate')
        for prediction in declared_boards:
            reference = board_by_id[prediction['id']]
            require(prediction['target'] == reference['target'] and prediction['gold'] == reference['gold_selected_indices'], 'Stored metadata board annotations changed')
            require(prediction['predicted'] == selections[reference['id']] and prediction['exact_match'] is board_ok[reference['id']], 'Stored metadata board selections differ from saved state')
        for label in labels:
            slot = stored['boards']['target_coverage'][label]; expected = recomputed['boards']['by_target'][label]
            require(slot['boards'] == expected['count'] and slot['exact'] == expected['correct'] and slot['evaluated'] == expected['evaluated'], 'Stored target coverage count mismatch')
            if expected['count']: close(slot['whole_board_exact_match'], expected['accuracy'], f'{fold}/{label} coverage')
            else: require(slot['whole_board_exact_match'] is None, 'Absent board target must have null accuracy')
        computed[fold] = recomputed; fold_correct[fold] = (class_ok, board_ok, selections)
    raw = rows(prediction_path)
    expected = {('classification', r['id'], None): (r['sha256'], r) for r in splits['test']['samples']}
    expected.update({('board', c['id'], i): (digest, c) for c in splits['test']['cases'] for i, digest in enumerate(c['tile_sha256'])})
    require(len(raw) == len(expected), 'Heldout raw row count mismatch')
    seen = set()
    for record in raw:
        key = (record['kind'], record['id'], record.get('tile_index'))
        require(key in expected and key not in seen, 'Unknown or duplicate heldout raw ID')
        seen.add(key); digest, ref = expected[key]; ix = row_ix[digest]
        actual_logits = torch.tensor(record['logits'], dtype=torch.float32); actual_probs = torch.tensor(record['probabilities'], dtype=torch.float32)
        require(actual_logits.shape == actual_probs.shape == (16,), 'Raw head vector dimension mismatch')
        require(torch.allclose(actual_logits, logits[ix], atol=2e-5, rtol=2e-5) and torch.allclose(actual_probs, probabilities[ix], atol=2e-6, rtol=2e-6), 'Raw logits/probabilities differ from saved checkpoint')
        if record['kind'] == 'classification':
            active = [labels.index(x) for x in ref['source_allowed_labels']]
            predicted = ref['source_allowed_labels'][int(actual_logits[active].argmax().item())]
            require(record['gold_label'] == ref['label'] and record['predicted_label'] == predicted, 'Raw classification truth/allowed-label argmax mismatch')
            require((record['predicted_label'] == ref['label']) == fold_correct['test'][0][ref['id']], 'Raw and saved-state classification predictions differ')
        else:
            col = labels.index(ref['target']); index = record['tile_index']
            require(record['target'] == ref['target'] and record['gold'] is (index in ref['gold_selected_indices']), 'Raw board annotation changed')
            require(record['predicted_positive'] is (actual_probs[col].item() >= .5), 'Raw board decision does not use fixed threshold')
            require(record['predicted_positive'] is (index in fold_correct['test'][2][ref['id']]), 'Raw and saved-state board decisions differ')
    require(set(expected) == seen, 'Missing heldout raw IDs')
    result = {'status': 'verified', 'metrics': computed, 'raw_prediction_rows': len(raw),
              'test_predictions_sha256': sha(prediction_path), 'metadata_sha256': sha(meta_path), 'head_sha256': sha(state_path),
              'script_sha256_declared': provenance['script_sha256'], 'best_epoch': best_epoch,
              'same_state_recomputed_all_folds': True, 'board_threshold': .5, 'uses_gold_count_for_prediction': False}
    if baseline:
        class_ok, board_ok, _ = fold_correct['test']
        result['paired_test_baseline'] = {'classification': paired({key: baseline['samples'][key]['correct_recomputed'] for key in class_ok}, class_ok),
                                        'boards': paired({key: baseline['boards'][key]['board_exact_recomputed'] for key in board_ok}, board_ok)}
        supplied = meta.get('baseline_comparison')
        if supplied:
            require(supplied['matched_boards'] == 203 and supplied['missing_test_boards'] == [], 'Head baseline not complete heldout comparison')
            close(supplied['baseline_whole_board_exact_match'], result['paired_test_baseline']['boards']['baseline_accuracy'], 'Stored paired baseline')
            close(supplied['head_whole_board_exact_match_same_boards'], result['paired_test_baseline']['boards']['head_accuracy'], 'Stored paired head')
    else: result['paired_test_baseline'] = None
    return result


def audit_slots(args):
    import torch
    torch.set_num_threads(2)
    manifest, splits, labels, unique = verify_inputs(args.manifest, not args.skip_image_recheck)
    summary_path = args.artifact_root / 'public-eval-summary.json'
    process_summary = load(summary_path) if summary_path.exists() else {}
    configs = load(args.slot_config) if args.slot_config else {}
    models = list(configs) if configs else args.models.split(',')
    out = {'scope': 'offline supplied public annotations; exploratory grouped holdouts reused in prior model selection',
           'historical_per_image_identity': 'unverified', 'input_manifest_sha256': MANIFEST_SHA,
           'split_manifests_sha256': SPLIT_SHA, 'input_images_rehashed': not args.skip_image_recheck,
           'unique_raw_images': 15420, 'referenced_image_paths': 15434, 'expected_slots': models,
           'audit_runtime': {'torch': torch.__version__, 'safetensors': importlib.metadata.version('safetensors'),
                             'python': sys.version.split()[0], 'cpu_threads': 2,
                             'head_logit_tolerance_atol_rtol': 2e-5, 'head_probability_tolerance_atol_rtol': 2e-6},
           'process_summary': process_summary, 'models': {}, 'audit_errors': {}}
    for model in models:
        slug = model.lower().replace('-', '_'); cfg = configs.get(model, {})
        zero_folder = Path(cfg.get('zero_dir', args.artifact_root / f'public-eval-result-{slug}'))
        head_dir = Path(cfg.get('head_dir', (args.head_root or args.artifact_root) / f'public-eval-head-{slug}'))
        index_path = Path(cfg.get('feature_index', zero_folder / f'{model}.features.index.json'))
        tensor_path = Path(cfg.get('features', index_path.with_name(f'{model}.features.safetensors')))
        slot = {'process_status': process_summary.get('models', {}).get(model),
                'head_process_status': process_summary.get('heads', {}).get(model),
                'zero_shot': {'status': 'not_applicable' if cfg.get('kind') == 'image_only_backbone' else 'missing'},
                'features': {'status': 'missing'}, 'head': {'status': 'not_requested' if args.raw_only or cfg.get('head_requested') is False else 'missing'}}
        out['models'][model] = slot
        baseline = None
        try:
            if cfg.get('kind') != 'image_only_backbone': slot['zero_shot'], baseline = verify_zero_shot(model, zero_folder, manifest, labels, splits['validation'])
        except Exception as exc:
            message = f'{type(exc).__name__}: {exc}'; slot['zero_shot'] = {'status': 'integrity_failed_or_incomplete', 'error': message}
            out['audit_errors'][model+'/zero_shot'] = message
        try:
            if index_path.exists() and tensor_path.exists():
                feature, row_ix, proof = verify_features(model, index_path, tensor_path, unique)
                slot['features'] = {'status': 'verified', **proof}
                if slot['head']['status'] != 'not_requested' and (head_dir / 'metadata.json').exists():
                    slot['head'] = verify_head(model, head_dir, feature, proof, row_ix, splits, labels, baseline)
                del feature
        except Exception as exc:
            message = f'{type(exc).__name__}: {exc}'
            stage = 'head' if slot['features']['status'] == 'verified' else 'features'
            slot[stage] = {'status': 'integrity_failed_or_incomplete', 'error': message}
            out['audit_errors'][model+'/'+stage] = message
    out['all_present_artifacts_passed'] = not out['audit_errors']
    out['all_requested_slots_complete'] = all(slot['features']['status'] == 'verified' and slot['head']['status'] in ('verified', 'not_requested')
        and slot['zero_shot']['status'] in ('verified_saved_outputs', 'not_applicable') for slot in out['models'].values())
    out['auditor_sha256'] = sha(Path(__file__))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('/workspace/work/image-finetune/eval-data/public_full.json'))
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--head-root', type=Path)
    parser.add_argument('--slot-config', type=Path, help='JSON mapping model names to explicit artifact paths; optional kind=image_only_backbone')
    parser.add_argument('--models', default=','.join(PRIMARY_MODELS))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--skip-image-recheck', action='store_true')
    parser.add_argument('--raw-only', action='store_true', help='Audit all frozen raw candidates without requesting any learned head')
    args = parser.parse_args()
    require(not args.output.exists(), f'Refusing existing audit receipt: {args.output}')
    result = audit_slots(args)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    compact = {}
    for model, slot in result['models'].items():
        validation = slot['zero_shot'].get('validation')
        brief_validation = None if validation is None else {
            'classification': {key: validation['classification'][key] for key in ('count', 'correct', 'accuracy')},
            'boards': {key: validation['boards'][key] for key in ('count', 'board_exact_count', 'board_exact_rate')},
        }
        compact[model] = {'zero_shot': slot['zero_shot']['status'], 'features': slot['features']['status'],
            'head': slot['head']['status'], 'validation': brief_validation,
            'test': slot['head'].get('metrics', {}).get('test'),
            'paired_test_baseline': slot['head'].get('paired_test_baseline')}
    print(json.dumps({'models': compact, 'audit_errors': result['audit_errors'],
                      'all_requested_slots_complete': result['all_requested_slots_complete']}, ensure_ascii=False, indent=2))
    if result['audit_errors']: raise SystemExit(1)


if __name__ == '__main__': main()
