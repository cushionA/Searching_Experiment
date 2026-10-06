"""Integrity checks for the frozen ImageNet backbone feature extractor."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = (Path(__file__).resolve().parents[1] /
          'experiments/captcha-small-model/extract_image_backbone_features.py')
SPEC = importlib.util.spec_from_file_location('extract_image_backbone_features', SCRIPT)
extractor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(extractor)


class ImageBackboneTests(unittest.TestCase):
    def test_manifest_images_are_unique_in_sample_then_board_order(self):
        manifest = {
            'samples': [
                {'id': 's1', 'path': 'sample-a.png', 'sha256': 'a' * 64},
                {'id': 's2', 'path': 'sample-b.png', 'sha256': 'b' * 64},
            ],
            'cases': [{
                'id': 'board', 'tile_paths': ['board-a.png', 'board-b.png'],
                'tile_sha256': ['a' * 64, 'c' * 64],
            }],
        }
        rows = extractor.manifest_images(manifest)
        self.assertEqual([row['sha256'] for row in rows], ['a' * 64, 'b' * 64, 'c' * 64])
        self.assertEqual([row['path'] for row in rows], ['sample-a.png', 'sample-b.png', 'board-b.png'])

    def test_pins_are_immutable_safetensors_with_valid_sha_and_size(self):
        for name, spec in extractor.MODEL_SPECS.items():
            with self.subTest(model=name):
                self.assertEqual(spec['filename'], 'model.safetensors')
                self.assertEqual(len(spec['revision']), 40)
                self.assertEqual(len(spec['sha256']), 64)
                self.assertGreater(spec['bytes'], 1_000_000)

    def test_installed_timm_resolves_each_pinned_architecture_offline(self):
        for name in extractor.MODEL_SPECS:
            with self.subTest(model=name):
                cfg = extractor.resolve_pretrained_cfg(name)
                self.assertEqual(cfg.hf_hub_id, extractor.MODEL_SPECS[name]['hf_repo'])
                self.assertEqual(cfg.tag, extractor.MODEL_SPECS[name]['tag'])
                self.assertEqual(cfg.num_classes, 1000)


if __name__ == '__main__':
    unittest.main()
