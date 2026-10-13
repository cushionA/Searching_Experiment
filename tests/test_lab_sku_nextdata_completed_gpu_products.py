"""Prevent partial SKU rows or partial model coverage from entering a cohort."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments/sku-matching'))
import collect_nextdata_completed_gpu_products_v1 as C


class CompletedProductCollectorTests(unittest.TestCase):
    def test_missing_one_model_key_excludes_entire_product_but_retains_literal_only(self):
        db = C.R.connect(':memory:')
        db.executescript('CREATE TABLE cache(kind TEXT,exact_key TEXT); CREATE TABLE occurrences(kind TEXT,exact_key TEXT,pair_ref TEXT);')
        db.executemany('INSERT INTO occurrences VALUES (?,?,?)', [('jev', 'j', '7'), ('nli', 'n', '7')])
        db.execute('INSERT INTO cache VALUES (?,?)', ('jev', 'j'))
        products = [{'product_id': 'ml', 'pair_refs': [7]}, {'product_id': 'literal', 'pair_refs': []}]
        with patch.object(C.R, 'validate_private_closure'):
            self.assertEqual(C.complete_products(db, {'products': products}), products[1:])
            db.execute('INSERT INTO cache VALUES (?,?)', ('nli', 'n'))
            self.assertEqual(C.complete_products(db, {'products': products}), products)
        db.close()

    def test_original_row_and_task_vectors_required_and_protected_pairs_need_no_model_output(self):
        products = [{'case_ids': ['c'], 'pair_refs': [7], 'row_keys': [['c', 'r1'], ['c', 'r2']],
                     'task_ids': [['c', 't1'], ['c', 't2']]}]
        subset = {'membership': [{'case_id': 'c'}], 'rows': [{'case_id': 'c', 'au_row_key': r} for r in ('r1', 'r2')],
                  'tasks': [{'case_id': 'c', 'task_id': t} for t in ('t1', 't2')],
                  'bindings': [{'pair_id': 'model'}, {'pair_id': 'protected-literal'}]}
        public = {7: {'pair_id': 'model'}}
        with patch.object(C.E, 'subset_source', return_value=subset):
            ids, refs, got = C.checked_subset(SimpleNamespace(), products, public)
            self.assertEqual((ids, refs), ({'c'}, {7}))
            self.assertIs(got, subset)
        for key in ('rows', 'tasks'):
            trimmed = copy.deepcopy(subset)
            trimmed[key].pop()
            with patch.object(C.E, 'subset_source', return_value=trimmed):
                with self.assertRaisesRegex(ValueError, 'trimmed'):
                    C.checked_subset(SimpleNamespace(), products, public)

    def test_all_options_and_all_pairs_required_and_unknown_is_preserved(self):
        row = {'pair_ref': 7, 'decisions': [{'option_index': 0, 'state': 'support'}, {'option_index': 1, 'state': 'unknown'}],
               'scope_status': 'unproved', 'citation_semantics_supported': False, 'production_adoption_allowed': False}
        public = {7: {'pair_id': 'p'}, 8: {'pair_id': 'q'}}
        pairs = {'p': {'pair_id': 'p', 'option_values': ['L', 'M']}, 'q': {'pair_id': 'q', 'option_values': ['L', 'M']}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'prediction.jsonl'
            path.write_text(json.dumps(row) + '\n')
            self.assertEqual(C.read_complete_states(path, {7}, public, pairs), {'p': ['support', 'unknown']})
            with self.assertRaisesRegex(ValueError, 'every pair'):
                C.read_complete_states(path, {7, 8}, public, pairs)
            path.write_text((json.dumps(row) + '\n') * 2)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                C.read_complete_states(path, {7}, public, pairs)
            row['decisions'].pop()
            path.write_text(json.dumps(row) + '\n')
            with self.assertRaisesRegex(ValueError, 'every whole original option'):
                C.read_complete_states(path, {7}, public, pairs)

    def test_partial_tail_and_fabricated_scope_are_rejected(self):
        row = {'pair_ref': 7, 'decisions': [{'option_index': 0, 'state': 'unknown'}], 'scope_status': 'unproved',
               'citation_semantics_supported': False, 'production_adoption_allowed': False}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'prediction.jsonl'
            path.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError, 'tail'):
                C.read_complete_states(path, {7}, {}, {})
            row['scope_status'] = 'verified'
            path.write_text(json.dumps(row) + '\n')
            with self.assertRaisesRegex(ValueError, 'unproved scope'):
                C.read_complete_states(path, {7}, {}, {})


if __name__ == '__main__':
    unittest.main()
