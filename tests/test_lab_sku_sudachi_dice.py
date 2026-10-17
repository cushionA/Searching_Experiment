"""Meaningful ranking/protection checks independent of measured SKU labels."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments/sku-matching'))
from run_sudachi_dice_v1 import dice, normalize, protected, ranks
from library_train_dictionary_v1 import dictionary_request_state


class SudachiDiceTests(unittest.TestCase):
    def test_close_numeric_values_are_not_exact_matches(self):
        self.assertAlmostEqual(dice('26.5', '25.5'), 1 / 3)
        self.assertTrue(protected('26.5'))
        self.assertTrue(protected('２６．５'))
        self.assertTrue(protected('A12'))
        self.assertTrue(protected('2枚セット'))

    def test_set_not_repeated_bigram_count(self):
        self.assertEqual(dice('aaaa', 'aa'), 1)

    def test_short_strings_empty_and_codepoints(self):
        self.assertEqual(dice('', ''), 0)
        self.assertEqual(dice('青', '青'), 1)
        self.assertEqual(dice('青', '赤'), 0)
        self.assertAlmostEqual(dice('😀青白', '😀青赤'), 0.5)

    def test_normalization_retains_numeric_difference_and_punctuation(self):
        self.assertEqual(normalize(' ＡＢＣ\u0085 '), 'abc')
        self.assertNotEqual(normalize('26.5'), normalize('25.5'))
        self.assertNotEqual(normalize('A-B'), normalize('AB'))

    def test_ranking_tie_uses_original_order(self):
        query = [{'value': '青'}]
        rows = [{'row_key': 'first', 'axes': [{'value': '青'}]},
                {'row_key': 'second', 'axes': [{'value': '青'}]}]
        self.assertEqual([r['row_key'] for r in ranks(query, rows, {'青': '青'})], ['first', 'second'])

    def test_normalization_collision_keeps_unknown(self):
        req = {'direction': 'rakuten_to_au', 'target_document_id': 'au',
               'axis_name': '付属品', 'option_values': ['附属', '別物'],
               'selected_value': '附属', 'target_selected_axes': [{'axis_name': '付属品', 'value': '付属'}]}
        docs = {'au': {'dossier_id': 'd', 'side': 'au'}}
        catalog = {('d', 'rakuten', '付属品'): {('附属', '別物')},
                   ('d', 'au', '付属品'): {('附属', '付属', '別物')}}
        state, reason, _ = dictionary_request_state(req, docs, catalog,
            {'accepted_alias_groups': []}, {'附属': '付属', '付属': '付属', '別物': '別物'})
        self.assertEqual(state, 'unknown')
        self.assertEqual(reason, 'dictionary_catalog_value_collision')


if __name__ == '__main__':
    unittest.main()
