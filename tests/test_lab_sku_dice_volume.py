"""Regression tests for frozen gate replay and Kaggle's automatic gzip expansion."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / 'experiments/sku-matching'
sys.path.insert(0, str(EXPERIMENT))
from benchmark_sudachi_dice_volume_v1 import frozen_functions, main


class DiceVolumeTests(unittest.TestCase):
    def test_original_gate_keeps_unknown_competitor(self):
        ns = frozen_functions(EXPERIMENT)
        rows = [{'case_id': 'c', 'au_row_key': 'a', 'bindings': [{'request_id': 'a'}]},
                {'case_id': 'c', 'au_row_key': 'b', 'bindings': [{'request_id': 'b'}]}]
        self.assertEqual(ns['assemble_mode'](rows, [], {'a': 'support', 'b': 'unknown'})[1][0]['disposition'], 'review')
        self.assertEqual(ns['assemble_mode'](rows, [], {'a': 'conflict', 'b': 'conflict'})[1][0]['disposition'], 'reject')

    def test_expanded_payload_requires_matching_raw_hash(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / 'payload.json').write_text('{}')
            (folder / 'MANIFEST.json').write_text(json.dumps({'files': {'payload.json.gz': 'original-compressed-sha'}}))
            with self.assertRaisesRegex(ValueError, 'original raw SHA256'):
                main(folder, folder / 'out', 'wrong')

    def test_valid_expansion_is_verified_before_population_validation(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            raw = json.dumps({'plan': {}, 'cases': [], 'rows': [], 'requests': [], 'products': []}).encode()
            (folder / 'payload.json').write_bytes(raw)
            (folder / 'MANIFEST.json').write_text(json.dumps({'files': {'payload.json.gz': 'compressed-sha-not-used-for-expanded-file'}}))
            for name in ['replay_dev_nli_policies_v1.py', 'run_installed_library_selection_v1.py',
                         'library_train_dictionary_v1.py', 'run_library_axis_domains_v1.py', 'run_sudachi_dice_v1.py']:
                shutil.copyfile(EXPERIMENT / name, folder / name)
            with self.assertRaisesRegex(ValueError, 'wrong complete population'):
                main(folder, folder / 'out', hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
