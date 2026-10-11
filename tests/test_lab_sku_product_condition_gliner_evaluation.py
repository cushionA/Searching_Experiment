"""Synthetic fixtures check fixed configuration and freeze-before-reference order."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import evaluate_product_condition_gliner_v1 as R

SPEC = importlib.util.spec_from_file_location(
    "gliner_eval_fixtures", ROOT / "tests/test_lab_sku_product_condition_share_evaluation.py")
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)


class ProductConditionGlinerEvaluationTests(unittest.TestCase):
    def test_fixed_variants_and_driver_frozen_before_references(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = F.prepared_fixture(root)
            for variant in R.VARIANTS[1:]:
                shutil.copyfile(args[1] / "structured-jev-predictions.jsonl",
                                args[1] / (variant + "-predictions.jsonl"))
            output = root / "evaluation"
            previous = R.E.VARIANTS
            route = R.E.E.route_references
            def checked(*a, **kw):
                frozen = json.loads((output / "driver-freeze.json").read_text())
                self.assertEqual(frozen["variants"], list(R.VARIANTS))
                self.assertFalse(frozen["labels_read"])
                self.assertTrue((output / "fixed-gates/all-gates-frozen.json").is_file())
                self.assertTrue(all((output / "fixed-gates" / v / "manifest.json").is_file()
                                    for v in R.VARIANTS))
                return route(*a, **kw)
            with patch.object(R.E.E, "route_references", side_effect=checked):
                result = R.run(*args, output)
            self.assertEqual(R.E.VARIANTS, previous)
            self.assertEqual(set(result["variants"]), set(R.VARIANTS))
            self.assertFalse(result["full_dataset_accuracy_measured"])
            self.assertTrue(result["driver_configuration_frozen_before_labels"])

    def test_missing_variant_restores_configuration_without_reading_labels(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = F.prepared_fixture(root)
            previous = R.E.VARIANTS
            with patch.object(R.E.E, "route_references") as route, self.assertRaises(FileNotFoundError):
                R.run(*args, root / "evaluation")
            route.assert_not_called()
            self.assertEqual(R.E.VARIANTS, previous)


if __name__ == "__main__":
    unittest.main()
