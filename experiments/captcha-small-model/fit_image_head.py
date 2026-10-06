#!/usr/bin/env python3
"""Fit a frozen-feature linear image head with label-masked supervision.

The encoder is never loaded or trained here. Features must come from an
independently prepared feature_cache.safetensors and its JSON index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return value


def feature_index_rows(index: dict[str, Any]) -> list[str]:
    rows = index.get('image_sha256')
    if not isinstance(rows, list) or not all(isinstance(x, str) and len(x) == 64 for x in rows):
        raise ValueError('feature cache index must contain ordered image_sha256 strings')
    return rows


def manifest_images(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for sample in manifest.get('samples', []):
        rows.append((sample['id'], sample['sha256']))
    for case in manifest.get('cases', []):
        hashes = case.get('tile_sha256')
        if not isinstance(hashes, list) or len(hashes) != len(case.get('tile_paths', [])):
            raise ValueError(f"Invalid tile hash list for board {case.get('id')}")
        rows.extend((f"{case['id']}:{i}", digest) for i, digest in enumerate(hashes))
    return rows


def verify_data(manifest_path: Path, split_paths: dict[str, Path], features_path: Path, index_path: Path):
    raw_manifest = manifest_path.read_bytes()
    manifest_sha = sha256_bytes(raw_manifest)
    manifest = json.loads(raw_manifest)
    if not isinstance(manifest, dict):
        raise ValueError('Input manifest must be a JSON object')
    index = read_json(index_path)
    if index.get('input_manifest_sha256') != manifest_sha:
        raise ValueError('Feature cache input_manifest_sha256 does not match the supplied manifest')
    if index.get('features_sha256') != sha256_file(features_path):
        raise ValueError('Feature cache tensor hash does not match its index')
    image_hashes = feature_index_rows(index)
    all_images = manifest_images(manifest)
    all_identities = [identity for identity, _ in all_images]
    if len(all_identities) != len(set(all_identities)):
        raise ValueError('Manifest image IDs must be unique')
    expected_hashes = list(dict.fromkeys(digest for _, digest in all_images))
    if image_hashes != expected_hashes:
        raise ValueError('Feature cache ordered unique image_sha256 does not match manifest sample/tile order')
    cache_row = {digest: i for i, digest in enumerate(image_hashes)}
    row_by_id = {identity: cache_row[digest] for identity, digest in all_images}
    if index.get('features_filename') and index['features_filename'] != features_path.name:
        raise ValueError('Feature cache filename differs from the index')
    if index.get('tensor') not in (None, 'embeddings'):
        raise ValueError('Feature cache index names an unexpected tensor')
    if index.get('dtype') not in (None, 'float32'):
        raise ValueError('Feature cache must contain float32 embeddings')
    if 'shape' in index and index['shape'][0] != len(image_hashes):
        raise ValueError('Feature cache shape disagrees with ordered image hashes')
    splits: dict[str, dict[str, Any]] = {}
    seen: dict[str, str] = {}
    byte_fold: dict[str, str] = {}
    pixel_fold: dict[str, str] = {}
    all_ids = set(row_by_id)
    sample_by_id = {r['id']: r for r in manifest.get('samples', [])}
    case_by_id = {r['id']: r for r in manifest.get('cases', [])}
    if len(sample_by_id) != len(manifest.get('samples', [])) or len(case_by_id) != len(manifest.get('cases', [])):
        raise ValueError('Manifest sample and board IDs must each be unique')
    for fold, path in split_paths.items():
        split = read_json(path)
        marker = split.get('split', {})
        if marker.get('fold') != fold:
            raise ValueError(f'{path} declares fold {marker.get("fold")!r}, expected {fold!r}')
        if marker.get('parent_manifest_sha256', marker.get('source_manifest_sha256')) != manifest_sha:
            raise ValueError(f'{path} was created from a different input manifest')
        split_samples = split.get('samples', [])
        split_cases = split.get('cases', [])
        for row in split_samples:
            original = sample_by_id.get(row.get('id'))
            if original is None or canonical_sha256(row) != canonical_sha256(original):
                raise ValueError(f"{path} sample row differs from the source manifest: {row.get('id')}")
            if not isinstance(row.get('sha256'), str) or not isinstance(row.get('pixel_sha256'), str):
                raise ValueError(f"{path} sample lacks byte/pixel hashes: {row.get('id')}")
        for row in split_cases:
            original = case_by_id.get(row.get('id'))
            if original is None or canonical_sha256(row) != canonical_sha256(original):
                raise ValueError(f"{path} board row differs from the source manifest: {row.get('id')}")
        ids = [r['id'] for r in split_samples]
        ids += [f"{r['id']}:{i}" for r in split_cases for i in range(len(r.get('tile_paths', [])))]
        unknown = set(ids) - all_ids
        if unknown:
            raise ValueError(f'{path} contains IDs absent from the full manifest: {sorted(unknown)[:3]}')
        if len(ids) != len(set(ids)):
            raise ValueError(f'{path} contains duplicate image IDs')
        for identity in ids:
            if identity in seen:
                raise ValueError(f'Image {identity} occurs in both {seen[identity]} and {fold}')
            seen[identity] = fold
        for row in split_samples:
            digests = (row['sha256'], row['pixel_sha256'])
            for digest, owner, name in ((digests[0], byte_fold, 'byte'), (digests[1], pixel_fold, 'decoded-pixel')):
                prior = owner.get(digest)
                if prior is not None and prior != fold:
                    raise ValueError(f'{name} duplicate image crosses folds: {prior} and {fold}')
                owner[digest] = fold
        for row in split_cases:
            if len(row.get('tile_sha256', [])) != len(row.get('tile_paths', [])) or len(row.get('tile_pixel_sha256', [])) != len(row.get('tile_paths', [])):
                raise ValueError(f"{path} board tile hash lists are inconsistent: {row.get('id')}")
            for byte_hash, pixel_hash in zip(row['tile_sha256'], row['tile_pixel_sha256'], strict=True):
                for digest, owner, name in ((byte_hash, byte_fold, 'byte'), (pixel_hash, pixel_fold, 'decoded-pixel')):
                    prior = owner.get(digest)
                    if prior is not None and prior != fold:
                        raise ValueError(f'{name} duplicate image crosses folds: {prior} and {fold}')
                    owner[digest] = fold
        splits[fold] = split
    if set(seen) != all_ids:
        raise ValueError(f'Splits do not cover all manifest images ({len(all_ids - set(seen))} missing)')
    split_fingerprint = canonical_sha256({fold: sha256_file(path) for fold, path in split_paths.items()})
    return manifest, splits, index, features_path, row_by_id, manifest_sha, split_fingerprint


def build_observations(manifest: dict[str, Any], split: dict[str, Any], class_names: list[str],
                       row_by_id: dict[str, int], classification_weight: float = 1.0,
                       board_weight: float = 1.0) -> tuple[list[int], list[list[float]], list[dict[str, Any]]]:
    """Build masked BCE targets; only explicitly known labels get mask=1."""
    class_ix = {name: i for i, name in enumerate(class_names)}
    indices: list[int] = []
    targets: list[list[float]] = []
    masks: list[list[float]] = []
    metadata: list[dict[str, Any]] = []
    for sample in split.get('samples', []):
        label = sample['label']
        allowed = set(sample.get('source_allowed_labels') or [])
        if label not in class_ix or label not in allowed:
            raise ValueError(f"Source label is outside the fixed head labels: {label}")
        target = [0.0] * len(class_names)
        mask = [0.0] * len(class_names)
        # A source image is a mutually exclusive class annotation among the
        # source-trained classes. Labels outside that source set stay unknown.
        for name in allowed:
            if name in class_ix:
                mask[class_ix[name]] = classification_weight
        target[class_ix[label]] = 1.0
        indices.append(row_by_id[sample['id']]); targets.append(target); masks.append(mask)
        metadata.append({'kind': 'classification', 'id': sample['id'], 'label': label,
                        'allowed_labels': sorted(allowed)})
    for case in split.get('cases', []):
        target_name = case['target']
        if target_name not in class_ix:
            raise ValueError(f"Board target is outside the fixed head labels: {target_name}")
        positives = set(case.get('gold_selected_indices', case.get('correct_answers', case.get('reference_selection', []))))
        if any(not isinstance(i, int) or i < 0 or i >= len(case['tile_paths']) for i in positives):
            raise ValueError(f"Invalid gold tile indices in board {case['id']}")
        for i in range(len(case['tile_paths'])):
            # Only this board's queried target is observed. Other logits have
            # mask=0, including every other class's unknown board status.
            target = [0.0] * len(class_names)
            mask = [0.0] * len(class_names)
            target[class_ix[target_name]] = float(i in positives)
            mask[class_ix[target_name]] = board_weight
            indices.append(row_by_id[f"{case['id']}:{i}"])
            targets.append(target); masks.append(mask)
            metadata.append({'kind': 'board', 'id': case['id'], 'tile_index': i,
                             'target': target_name, 'gold': i in positives})
    return indices, [targets, masks], metadata


def masked_bce(logits, targets, masks):
    import torch
    import torch.nn.functional as F
    element = F.binary_cross_entropy_with_logits(logits, targets, reduction='none')
    denominator = masks.sum()
    if denominator.item() <= 0:
        raise ValueError('No observed labels in fold')
    loss = (element * masks).sum() / denominator
    if not torch.isfinite(loss).item():
        raise FloatingPointError('Masked BCE is non-finite')
    return loss


def predict_allowed_class(row, classes: list[str], allowed_labels: list[str]) -> str:
    """Argmax only over the row's explicitly allowed class labels."""
    if not allowed_labels or not set(allowed_labels).issubset(classes):
        raise ValueError('Classification row has invalid allowed labels')
    active = [classes.index(name) for name in allowed_labels]
    return allowed_labels[int(row[active].argmax())]


def fold_metrics(logits, indices: list[int], observations, classes: list[str], split: dict[str, Any],
                 classification_labels: list[str]):
    import torch
    import torch.nn.functional as F
    targets, masks, records = observations
    ix = torch.tensor(indices, dtype=torch.long)
    y = torch.tensor(targets, dtype=torch.float32)
    m = torch.tensor(masks, dtype=torch.float32)
    loss = masked_bce(logits[ix], y, m).item()
    probabilities = logits[ix].sigmoid()
    predictions = (probabilities >= 0.5).to(torch.int64)
    observed = m.bool()
    label_correct = (predictions == y.to(torch.int64)) & observed
    per_label = {}
    for col, name in enumerate(classes):
        n = int(observed[:, col].sum().item())
        if n:
            per_label[name] = {'observations': n, 'accuracy_at_0_5': float(label_correct[:, col].sum().item() / n)}
    source_records = [i for i, r in enumerate(records) if r['kind'] == 'classification']
    class_correct = 0
    class_coverage: dict[str, dict[str, Any]] = {
        label: {'samples': 0, 'correct': 0, 'accuracy': None} for label in classification_labels
    }
    for j in source_records:
        row = logits[indices[j]]
        allowed = records[j]['allowed_labels']
        predicted = predict_allowed_class(row, classes, allowed)
        label = records[j]['label']
        class_correct += int(predicted == label)
        slot = class_coverage[label]
        slot['samples'] += 1; slot['correct'] += int(predicted == label)
    for slot in class_coverage.values():
        if slot['samples']:
            slot['accuracy'] = slot['correct'] / slot['samples']
    board_results = []
    case_by_id = {case['id']: case for case in split.get('cases', [])}
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for j, rec in enumerate(records):
        if rec['kind'] == 'board':
            grouped.setdefault(rec['id'], []).append((j, rec))
    exact = 0
    board_target_coverage: dict[str, dict[str, Any]] = {
        label: {'boards': 0, 'exact': 0, 'whole_board_exact_match': None, 'evaluated': False}
        for label in classes
    }
    for case_id, members in grouped.items():
        chosen = sorted(rec['tile_index'] for j, rec in members
                        if probabilities[j, classes.index(rec['target'])].item() >= 0.5)
        gold = sorted(case_by_id[case_id].get('gold_selected_indices', case_by_id[case_id].get('correct_answers', case_by_id[case_id].get('reference_selection', []))))
        match = chosen == gold
        exact += int(match)
        slot = board_target_coverage[members[0][1]['target']]
        slot['boards'] += 1; slot['exact'] += int(match)
        board_results.append({'id': case_id, 'target': members[0][1]['target'], 'gold': gold,
                              'predicted': chosen, 'exact_match': match})
    n_class = len(source_records)
    for slot in board_target_coverage.values():
        if slot['boards']:
            slot['whole_board_exact_match'] = slot['exact'] / slot['boards']
            slot['evaluated'] = True
    return {'masked_bce': loss, 'observed_label_accuracy_at_0_5': per_label,
            'classification': {'samples': n_class, 'allowed_classes': classification_labels,
                               'class_coverage': class_coverage,
                               'accuracy': class_correct / n_class if n_class else None},
            'boards': {'count': len(grouped), 'whole_board_exact_match': exact / len(grouped) if grouped else None,
                       'target_coverage': board_target_coverage, 'predictions': board_results}}


def train(args):
    try:
        import torch
        from safetensors.torch import load_file, save_file
    except ImportError as exc:
        raise SystemExit('Install torch and safetensors to fit or smoke-test the head') from exc
    if args.output.exists():
        raise FileExistsError(args.output)
    random.seed(args.seed); torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    manifest, splits, index, tensor_path, row_by_id, manifest_sha, split_sha = verify_data(
        args.manifest, {'train': args.train_split, 'validation': args.validation_split, 'test': args.test_split}, args.features, args.feature_index)
    tensors = load_file(str(tensor_path), device='cpu')
    if set(tensors) != {'embeddings'}:
        raise ValueError("feature_cache.safetensors must contain only tensor 'embeddings'")
    features = tensors['embeddings'].float().contiguous()
    if features.ndim != 2 or features.shape[0] != len(feature_index_rows(index)) or not torch.isfinite(features).all():
        raise ValueError('Feature matrix must be finite [manifest_images, feature_dim]')
    if index.get('shape') not in (None, list(features.shape)):
        raise ValueError('Feature tensor dimensions disagree with cache index')
    norms = features.norm(dim=1)
    if not torch.allclose(norms, torch.ones_like(norms), atol=2e-3, rtol=2e-3):
        raise ValueError('Feature rows are expected to be L2 normalized')
    classes = manifest.get('classes')
    if isinstance(classes, list) and classes and isinstance(classes[0], dict):
        classes = [item['label'] for item in classes]
    if not isinstance(classes, list) or len(classes) != 16 or len(set(classes)) != 16:
        raise ValueError('Manifest must define exactly 16 ordered class labels')
    classification_labels = sorted(set().union(*(set(row.get('source_allowed_labels') or []) for row in manifest.get('samples', []))))
    if not classification_labels or not set(classification_labels).issubset(classes):
        raise ValueError('Manifest source samples must declare their allowed classification labels')
    built = {fold: build_observations(manifest, splits[fold], classes, row_by_id,
                                      args.classification_weight, args.board_weight)
             for fold in ('train', 'validation', 'test')}
    class Head(torch.nn.Module):
        def __init__(self, dim: int, count: int):
            super().__init__(); self.linear = torch.nn.Linear(dim, count)
        def forward(self, x): return self.linear(x)
    head = Head(features.shape[1], len(classes))
    optimizer = torch.optim.AdamW(head.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    train_idx, (train_y, train_m), _ = built['train']
    val_idx, (val_y, val_m), _ = built['validation']
    train_ix = torch.tensor(train_idx, dtype=torch.long)
    train_targets = torch.tensor(train_y, dtype=torch.float32)
    train_masks = torch.tensor(train_m, dtype=torch.float32)
    val_ix = torch.tensor(val_idx, dtype=torch.long)
    val_targets = torch.tensor(val_y, dtype=torch.float32)
    val_masks = torch.tensor(val_m, dtype=torch.float32)
    best_loss = math.inf; best_epoch = 0; best_state = None; stale = 0; history = []
    for epoch in range(1, args.epochs + 1):
        head.train(); optimizer.zero_grad(set_to_none=True)
        loss = masked_bce(head(features[train_ix]), train_targets, train_masks)
        loss.backward(); optimizer.step()
        head.eval()
        with torch.no_grad():
            val_loss = masked_bce(head(features[val_ix]), val_targets, val_masks).item()
        history.append({'epoch': epoch, 'train_masked_bce': float(loss.item()), 'validation_masked_bce': val_loss})
        if val_loss < best_loss - args.min_delta:
            best_loss = val_loss; best_epoch = epoch; stale = 0
            best_state = {k: v.detach().cpu().clone() for k, v in head.state_dict().items()}
        else:
            stale += 1
        if stale >= args.patience:
            break
    if best_state is None:
        raise RuntimeError('No finite validation checkpoint was selected')
    head.load_state_dict(best_state); head.eval()
    # Test is read only after selecting the best validation checkpoint.
    with torch.no_grad():
        logits = head(features)
    if not torch.isfinite(logits).all().item():
        raise FloatingPointError('Linear head produced non-finite logits')
    metrics = {fold: fold_metrics(logits, built[fold][0],
                                  [built[fold][1][0], built[fold][1][1], built[fold][2]], classes, splits[fold],
                                  classification_labels)
               for fold in ('train', 'validation', 'test')}
    args.output.mkdir(parents=True)
    state_path = args.output / 'linear_head.safetensors'
    save_file(best_state, str(state_path))
    predictions_path = args.output / 'test_predictions.jsonl'
    test_board_ids = {rec['id'] for rec in built['test'][2] if rec['kind'] == 'board'}
    with predictions_path.open('w', encoding='utf-8') as stream:
        for i, rec in enumerate(built['test'][2]):
            row = logits[built['test'][0][i]]
            board_target = rec.get('target')
            pred_class = None
            if rec['kind'] == 'classification':
                pred_class = predict_allowed_class(row, classes, rec['allowed_labels'])
            pred = {'kind': rec['kind'], 'id': rec['id'], 'logits': row.tolist(), 'probabilities': row.sigmoid().tolist()}
            if pred_class is not None: pred.update({'gold_label': rec['label'], 'predicted_label': pred_class})
            if board_target is not None: pred.update({'tile_index': rec['tile_index'], 'target': board_target,
                                                       'gold': rec['gold'], 'predicted_positive': bool(row[classes.index(board_target)].sigmoid() >= 0.5)})
            stream.write(json.dumps(pred, ensure_ascii=False) + '\n')
    baseline_result = None
    if args.baseline_jsonl:
        baseline = {}
        with args.baseline_jsonl.open(encoding='utf-8') as stream:
            for line in stream:
                if line.strip():
                    item = json.loads(line); baseline[item['id']] = item
        # Preserve only records that belong to held-out test boards/samples.
        matched = test_board_ids & set(baseline)
        comparisons = []
        for board_id in sorted(matched):
            case = next(x for x in splits['test'].get('cases', []) if x['id'] == board_id)
            item = baseline[board_id]
            predicted = sorted(item.get('selection', []))
            gold = sorted(case.get('gold_selected_indices', case.get('correct_answers', case.get('reference_selection', []))))
            comparisons.append({'id': board_id, 'baseline_exact_match': predicted == gold,
                                'head_exact_match': next(x['exact_match'] for x in metrics['test']['boards']['predictions'] if x['id'] == board_id)})
        baseline_result = {'path': str(args.baseline_jsonl), 'test_board_denominator': len(test_board_ids),
                           'matched_boards': len(matched), 'missing_test_boards': sorted(test_board_ids - set(baseline)),
                           'baseline_whole_board_exact_match': (sum(x['baseline_exact_match'] for x in comparisons) / len(comparisons) if comparisons else None),
                           'head_whole_board_exact_match_same_boards': (sum(x['head_exact_match'] for x in comparisons) / len(comparisons) if comparisons else None),
                           'paired_board_comparisons': comparisons}
    metadata = {
        'method': 'frozen encoder linear probe; encoder features are fixed and only a 16-logit linear head is trained',
        'architecture': {'type': 'linear', 'input_dim': int(features.shape[1]), 'output_dim': 16,
                         'labels': classes, 'output': 'independent sigmoid logits'},
        'selection': {'criterion': 'validation masked BCE', 'best_epoch': best_epoch,
                      'best_validation_masked_bce': best_loss, 'early_stopping_patience': args.patience,
                      'epochs_run': len(history), 'fixed_board_threshold': 0.5,
                      'classification_rule': 'argmax among labels observed in classification training data',
                      'requested_count_not_used': True},
        'training': {'optimizer': 'AdamW', 'learning_rate': args.learning_rate, 'weight_decay': args.weight_decay,
                     'max_epochs': args.epochs, 'seed': args.seed, 'threads': args.threads,
                     'masked_loss_normalization': 'weighted mean over observed label entries; masked unknowns contribute zero',
                     'classification_observation_weight': args.classification_weight,
                     'board_observation_weight': args.board_weight},
        'provenance': {'input_manifest_sha256': manifest_sha, 'split_manifests_sha256': split_sha,
                       'feature_index_sha256': sha256_file(args.feature_index),
                       'features_sha256': index['features_sha256'], 'head_sha256': sha256_file(state_path),
                       'script_sha256': sha256_file(Path(__file__)), 'seed': args.seed,
                       'feature_cache_index_path': str(args.feature_index),
                       'features_path': str(args.features)},
        'metrics': metrics, 'baseline_comparison': baseline_result, 'history': history,
        'warning': 'Grouped holdouts derive from images previously used in model selection; these metrics are exploratory, not a fresh final evaluation.'
    }
    (args.output / 'metadata.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'best_epoch': best_epoch,
                      'test_classification': metrics['test']['classification'],
                      'test_boards': {k: v for k, v in metrics['test']['boards'].items() if k != 'predictions'}}, ensure_ascii=False))


def smoke_test() -> None:
    """Tiny CPU checks for unknown-label masking and checkpoint round trip."""
    try:
        import torch
        from safetensors.torch import load_file, save_file
    except ImportError as exc:
        raise SystemExit('Install torch and safetensors to fit or smoke-test the head') from exc
    logits = torch.tensor([[0.0, 99.0]], requires_grad=True)
    target = torch.tensor([[1.0, 0.0]])
    mask = torch.tensor([[1.0, 0.0]])
    loss = masked_bce(logits, target, mask)
    assert abs(loss.item() - math.log(2)) < 1e-6
    loss.backward()
    assert logits.grad[0, 1].item() == 0.0
    with __import__('tempfile').TemporaryDirectory() as tmp:
        path = Path(tmp) / 'head.safetensors'
        module = torch.nn.Linear(2, 2)
        save_file(module.state_dict(), str(path))
        restored = torch.nn.Linear(2, 2)
        restored.load_state_dict(load_file(str(path)))
        assert all(torch.equal(a, b) for a, b in zip(module.state_dict().values(), restored.state_dict().values(), strict=True))
    print('smoke tests passed')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--features', type=Path)
    parser.add_argument('--feature-index', type=Path)
    parser.add_argument('--train-split', type=Path)
    parser.add_argument('--validation-split', type=Path)
    parser.add_argument('--test-split', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--baseline-jsonl', type=Path)
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--patience', type=int, default=10)
    parser.add_argument('--min-delta', type=float, default=1e-6)
    parser.add_argument('--learning-rate', type=float, default=1e-3)
    parser.add_argument('--weight-decay', type=float, default=1e-4)
    parser.add_argument('--classification-weight', type=float, default=1.0)
    parser.add_argument('--board-weight', type=float, default=1.0)
    parser.add_argument('--seed', type=int, default=20261006)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    if args.smoke_test:
        smoke_test(); return
    required = ('manifest', 'features', 'feature_index', 'train_split', 'validation_split', 'test_split', 'output')
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        parser.error('required unless --smoke-test: ' + ', '.join('--' + x.replace('_', '-') for x in missing))
    if args.epochs < 1 or args.patience < 1 or args.threads < 1 or args.learning_rate <= 0 or args.weight_decay < 0 or args.classification_weight < 0 or args.board_weight < 0 or args.classification_weight + args.board_weight <= 0:
        parser.error('epochs, patience, threads and learning rate must be positive; weight decay cannot be negative')
    train(args)


if __name__ == '__main__':
    main()
