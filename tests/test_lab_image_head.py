"""Small integrity checks for the frozen-feature image head."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import torch
from safetensors.torch import load_file, save_file


SCRIPT = (Path(__file__).resolve().parents[1] /
          'experiments/captcha-small-model/fit_image_head.py')
SPEC = importlib.util.spec_from_file_location('fit_image_head', SCRIPT)
fit_image_head = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(fit_image_head)


class ImageHeadTests(unittest.TestCase):
    def test_unknown_board_logits_do_not_affect_masked_loss_or_gradient(self):
        logits = torch.tensor([[0.0, -100.0, 100.0]], requires_grad=True)
        target = torch.tensor([[1.0, 0.0, 1.0]])
        mask = torch.tensor([[1.0, 0.0, 0.0]])
        loss = fit_image_head.masked_bce(logits, target, mask)
        self.assertAlmostEqual(loss.item(), 0.693147, places=5)
        loss.backward()
        self.assertEqual(logits.grad[0, 1:].tolist(), [0.0, 0.0])

    def test_linear_head_safetensors_reload_preserves_predictions(self):
        head = torch.nn.Linear(3, 16)
        features = torch.tensor([[0.25, -0.5, 0.75]])
        expected = head(features)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'head.safetensors'
            save_file(head.state_dict(), str(path))
            restored = torch.nn.Linear(3, 16)
            restored.load_state_dict(load_file(str(path)))
        torch.testing.assert_close(restored(features), expected)

    def test_allowed_label_argmax_maps_subset_position_to_global_class(self):
        classes = [f'class-{i}' for i in range(16)]
        row = torch.zeros(16)
        row[2] = 1.0
        row[12] = 2.0
        self.assertEqual(fit_image_head.predict_allowed_class(row, classes, ['class-2', 'class-12']), 'class-12')

    def test_observations_mask_unrequested_board_classes(self):
        classes = [f'class-{i}' for i in range(16)]
        sample = {'id': 's', 'label': 'class-2', 'source_allowed_labels': ['class-2', 'class-12']}
        board = {'id': 'b', 'target': 'class-14', 'tile_paths': ['a.png', 'b.png'],
                 'gold_selected_indices': [1]}
        _, (targets, masks), records = fit_image_head.build_observations(
            {'samples': [], 'cases': []}, {'samples': [sample], 'cases': [board]}, classes,
            {'s': 0, 'b:0': 1, 'b:1': 2})
        self.assertEqual([i for i, value in enumerate(masks[0]) if value], [2, 12])
        self.assertEqual([i for i, value in enumerate(masks[1]) if value], [14])
        self.assertEqual([i for i, value in enumerate(masks[2]) if value], [14])
        self.assertEqual(targets[2][14], 1.0)
        self.assertEqual(records[0]['allowed_labels'], ['class-12', 'class-2'])

    def test_fold_metrics_map_subset_logits_and_handle_board_only_fold(self):
        classes = [f'class-{i}' for i in range(16)]
        logits = torch.zeros(3, 16)
        logits[0, 2] = 1.0; logits[0, 12] = 3.0
        logits[1, 14] = -1.0; logits[2, 14] = 1.0
        records = [
            {'kind': 'classification', 'id': 'sample', 'label': 'class-12',
             'allowed_labels': ['class-2', 'class-12']},
            {'kind': 'board', 'id': 'board', 'tile_index': 0, 'target': 'class-14', 'gold': False},
            {'kind': 'board', 'id': 'board', 'tile_index': 1, 'target': 'class-14', 'gold': True},
        ]
        targets = [[0.0] * 16 for _ in records]
        masks = [[0.0] * 16 for _ in records]
        targets[0][12] = 1.0; masks[0][2] = masks[0][12] = 1.0
        targets[2][14] = 1.0; masks[1][14] = masks[2][14] = 1.0
        split = {'samples': [{'id': 'sample', 'label': 'class-12'}],
                 'cases': [{'id': 'board', 'target': 'class-14', 'tile_paths': ['a', 'b'],
                            'gold_selected_indices': [1]}]}
        mixed = fit_image_head.fold_metrics(logits, [0, 1, 2], [targets, masks, records], classes,
                                            split, ['class-2', 'class-12'])
        self.assertEqual(mixed['classification']['accuracy'], 1.0)
        self.assertEqual(mixed['classification']['allowed_classes'], ['class-2', 'class-12'])
        self.assertEqual(mixed['boards']['whole_board_exact_match'], 1.0)

        board_records = records[1:]
        board_only = fit_image_head.fold_metrics(logits, [1, 2], [targets[1:], masks[1:], board_records],
            classes, {'samples': [], 'cases': split['cases']}, ['class-2', 'class-12'])
        self.assertIsNone(board_only['classification']['accuracy'])
        self.assertEqual(board_only['classification']['samples'], 0)
        self.assertTrue(all(v['samples'] == 0 and v['accuracy'] is None
                            for v in board_only['classification']['class_coverage'].values()))

    def _verify_fixture(self, root, samples, split_samples):
        cache = root / 'cache'; cache.mkdir()
        classes = [{'label': f'class-{i}'} for i in range(16)]
        manifest = {'classes': classes, 'source_classes': {}, 'samples': samples, 'cases': []}
        raw = json.dumps(manifest).encode()
        manifest_path = root / 'full.json'; manifest_path.write_bytes(raw)
        features_path = cache / 'm.features.safetensors'
        unique = list(dict.fromkeys(row['sha256'] for row in samples))
        save_file({'embeddings': torch.ones(len(unique), 2) / (2 ** 0.5)}, str(features_path))
        index_path = cache / 'm.features.index.json'
        index_path.write_text(json.dumps({'input_manifest_sha256': hashlib.sha256(raw).hexdigest(),
                                          'features_filename': features_path.name,
                                          'features_sha256': fit_image_head.sha256_file(features_path),
                                          'image_sha256': unique, 'shape': [len(unique), 2],
                                          'tensor': 'embeddings', 'dtype': 'float32'}))
        splits = {}
        for fold in ('train', 'validation', 'test'):
            path = root / f'{fold}.json'
            path.write_text(json.dumps({'split': {'fold': fold, 'parent_manifest_sha256': hashlib.sha256(raw).hexdigest()},
                                        'samples': split_samples.get(fold, []), 'cases': []}))
            splits[fold] = path
        return manifest_path, splits, features_path, index_path

    def test_split_overlap_is_rejected_before_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_sha = hashlib.sha256(b'image bytes').hexdigest()
            sample = {'id': 'sample-1', 'path': 'image.png', 'sha256': image_sha,
                      'pixel_sha256': image_sha, 'label': 'class-0',
                      'source_allowed_labels': ['class-0']}
            manifest_path, folds, features_path, index_path = self._verify_fixture(
                root, [sample], {'train': [sample], 'validation': [sample], 'test': []})
            with self.assertRaisesRegex(ValueError, 'occurs in both'):
                fit_image_head.verify_data(manifest_path, folds, features_path, index_path)

    def test_decoded_pixel_alias_cannot_cross_folds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pixel_sha = hashlib.sha256(b'same decoded pixels').hexdigest()
            first = {'id': 'sample-a', 'path': 'a.png', 'sha256': hashlib.sha256(b'a').hexdigest(),
                     'pixel_sha256': pixel_sha, 'label': 'class-0', 'source_allowed_labels': ['class-0']}
            second = {'id': 'sample-b', 'path': 'b.png', 'sha256': hashlib.sha256(b'b').hexdigest(),
                      'pixel_sha256': pixel_sha, 'label': 'class-1', 'source_allowed_labels': ['class-0', 'class-1']}
            args = self._verify_fixture(root, [first, second], {'train': [first], 'validation': [second]})
            with self.assertRaisesRegex(ValueError, 'decoded-pixel duplicate'):
                fit_image_head.verify_data(*args)

    def test_split_label_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_sha = hashlib.sha256(b'image').hexdigest()
            sample = {'id': 'sample', 'path': 'image.png', 'sha256': image_sha,
                      'pixel_sha256': image_sha, 'label': 'class-0', 'source_allowed_labels': ['class-0']}
            mutated = dict(sample, label='class-1')
            args = self._verify_fixture(root, [sample], {'train': [mutated]})
            with self.assertRaisesRegex(ValueError, 'differs from the source manifest'):
                fit_image_head.verify_data(*args)


if __name__ == '__main__':
    unittest.main()
