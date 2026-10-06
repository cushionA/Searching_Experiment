"""Check that training splits preserve transitive source-image/board grouping."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'experiments/captcha-small-model/prepare_public_image_split.py'
SPEC = importlib.util.spec_from_file_location('prepare_public_image_split', SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def digest(number: int) -> str:
    return f'{number:064x}'


def sample(identifier: str, pixel: int, label: str = 'bus') -> dict:
    return {'id': identifier, 'pixel_sha256': digest(pixel), 'source': 'hcaptcha', 'label': label}


def board(identifier: str, pixels: list[int]) -> dict:
    return {'id': identifier, 'tile_pixel_sha256': [digest(x) for x in pixels],
            'source': 'recaptcha', 'source_type': 'Type A', 'target': 'bus'}


class PublicImageSplitTests(unittest.TestCase):
    def test_shared_tiles_link_boards_transitively_and_include_duplicate_samples(self):
        manifest = {'samples': [sample('same-tile', 3), sample('separate', 20)],
                    'cases': [board('a', [1, 2]), board('b', [2, 3]), board('c', [3, 4])]}
        groups = module.grouped_rows(manifest)
        actual = {frozenset(item['row']['id'] for item in group) for group in groups}
        self.assertEqual(actual, {frozenset({'a', 'b', 'c', 'same-tile'}), frozenset({'separate'})})
        split = module.partition(manifest)
        linked_folds = {row['fold'] for row in split['groups']
                        if {'a', 'b', 'c', 'same-tile'} & set(row['sample_ids'] + row['board_ids'])}
        self.assertEqual(len(linked_folds), 1)

    def test_raw_byte_duplicates_are_grouped_even_with_different_pixel_metadata(self):
        a, b = sample('a', 1), sample('b', 2)
        a['sha256'] = b['sha256'] = digest(9)
        groups = module.grouped_rows({'samples': [a, b], 'cases': []})
        self.assertEqual(len(groups), 1)

    def test_conflicting_labels_on_same_pixels_do_not_leak_across_folds(self):
        manifest = {'samples': [sample('a', 1, 'bus'), sample('b', 1, 'train')], 'cases': []}
        self.assertEqual(len(module.grouped_rows(manifest)), 1)
        split = module.partition(manifest)
        self.assertEqual(split['largest_component_rows'], 2)

    def test_frozen_split_is_complete_disjoint_deterministic_and_label_preserving(self):
        manifest = {'samples': [sample(f's-{i}', i + 1) for i in range(100)], 'cases': [],
                    'classes': [{'label': 'bus'}]}
        result = module.partition(manifest, seed=27)
        self.assertEqual(result, module.partition(manifest, seed=27))
        groups = [set(row['id'] for row in part['samples']) for part in result['manifests'].values()]
        self.assertEqual(len(set.union(*groups)), 100)
        self.assertTrue(all(not a & b for i, a in enumerate(groups) for b in groups[i + 1:]))
        self.assertEqual([len(part['samples']) for part in result['manifests'].values()], [70, 10, 20])
        for part in result['manifests'].values():
            self.assertEqual(part['classes'], manifest['classes'])
            self.assertTrue(all(row['label'] == 'bus' for row in part['samples']))

    def test_duplicate_ids_and_missing_grouping_metadata_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            module.grouped_rows({'samples': [sample('a', 1)], 'cases': [board('a', [2])]})
        with self.assertRaisesRegex(ValueError, 'grouping hashes'):
            module.grouped_rows({'samples': [dict(sample('a', 1), pixel_sha256='')], 'cases': []})


if __name__ == '__main__':
    unittest.main()
