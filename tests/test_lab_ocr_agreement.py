"""Prevent selective OCR acceptance rates from being confused with full coverage."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

EXPERIMENT = Path(__file__).resolve().parents[1] / 'experiments/captcha-small-model'
spec = importlib.util.spec_from_file_location('ocr_alternatives_summary', EXPERIMENT / 'summarize_ocr_alternatives.py')
module = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(EXPERIMENT))
try:
    spec.loader.exec_module(module)
finally:
    sys.path.remove(str(EXPERIMENT))


def row(index, label, answer, error=None):
    return {'id': str(index), 'sha256': f'{index:064x}', 'source': 'fixture',
            'label': label, 'answer': answer, 'error': error}


class OCRAgreementTests(unittest.TestCase):
    def test_agreement_accepts_shared_errors_and_keeps_abstentions_in_denominator(self):
        left = [row(1, 'Ab0', 'Ab0'), row(2, 'cD1', 'c01'), row(3, 'Ef2', 'Ef2'), row(4, 'Gh3', '')]
        right = [row(1, 'Ab0', 'Ab0'), row(2, 'cD1', 'c01'), row(3, 'Ef2', 'ef2'), row(4, 'Gh3', '')]
        score = module.agreement(left, right)
        self.assertEqual(score['accepted_samples'], 2)
        self.assertEqual(score['accepted_incorrect'], 1)
        self.assertEqual(score['abstained_samples'], 2)
        self.assertEqual(score['coverage'], 0.5)
        self.assertEqual(score['accepted_exact_match'], 0.5)
        self.assertEqual(score['correct_accepted_fraction_of_all'], 0.25)

    def test_labels_do_not_choose_acceptance_and_errors_are_not_accepted(self):
        left = [row(1, 'gold', 'same'), row(2, 'same', 'same', {'type': 'failure'})]
        right = [row(1, 'gold', 'same'), row(2, 'same', 'same')]
        original = module.agreement(left, right)
        left[0]['label'] = right[0]['label'] = 'same'
        changed = module.agreement(left, right)
        self.assertEqual(original['accepted_samples'], changed['accepted_samples'])
        self.assertEqual(changed['accepted_samples'], 1)
        self.assertEqual(original['accepted_correct'], 0)
        self.assertEqual(changed['accepted_correct'], 1)

    def test_empty_acceptance_and_misaligned_inputs_are_explicit(self):
        score = module.agreement([row(1, 'a', 'a')], [row(1, 'a', 'b')])
        self.assertEqual(score['coverage'], 0)
        self.assertIsNone(score['accepted_exact_match'])
        with self.assertRaisesRegex(ValueError, 'lengths'):
            module.agreement([row(1, 'a', 'a')], [])
        with self.assertRaisesRegex(ValueError, 'id mismatch'):
            module.agreement([row(1, 'a', 'a')], [row(2, 'a', 'a')])


if __name__ == '__main__':
    unittest.main()
