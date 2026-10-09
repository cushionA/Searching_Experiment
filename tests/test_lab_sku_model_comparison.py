"""Regression checks for guarded SKU model-comparison metrics and ranking denominators."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
except ImportError:
    np = None

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


comparison = load_module(EXPERIMENT / "compare_models.py", "sku_compare_models_test")


def sku(site: str, *, lace: bool | None = True, width: int = 100):
    lace_label = " / あり" if lace is True else " / なし" if lace is False else ""
    label = f"幅{width}×丈80cm(4枚組) / ベージュ{lace_label}"
    return {
        "sku_id": f"{site}-sku",
        "product_id": f"{site}-product",
        "product_title": "カーテン 4枚組 レースセット",
        "sku_label": label,
        "url": f"https://example.test/{site}",
        "attributes": {
            "width_cm": width,
            "height_cm": 80,
            "color": "ベージュ",
            "lace": lace,
            "pieces": 4,
        },
    }


def case(case_id: str, scenario: str, expected: str, au: dict, rakuten: dict):
    return {"case_id": case_id, "scenario": scenario, "expected_decision": expected,
            "au": au, "rakuten": rakuten}


class HighScoreReranker:
    """A deterministic fake: all rows pass every inspected threshold."""
    MAX_LENGTH = 512

    def score_pairs(self, pairs, batch_size=8):
        return np.full(len(pairs), 100.0, dtype=np.float32)

    def token_lengths_pairs(self, pairs):
        return np.full(len(pairs), 12, dtype=np.int32)


class SkuModelComparisonTests(unittest.TestCase):
    def test_review_cases_are_excluded_from_verified_different_fpr_and_counted_as_false_accepts(self):
        expected = ["unmatched", "unmatched", "review", "review", "matched"]
        predicted = ["matched", "unmatched", "matched", "review", "matched"]

        metrics = comparison.classification_metrics(expected, predicted)

        self.assertEqual(metrics["confusion_matrix"]["unmatched"]["matched"], 1)
        self.assertEqual(metrics["confusion_matrix"]["review"]["matched"], 1)
        self.assertEqual(metrics["verified_different_fpr"], 0.5)  # 1 false accept / 2 verified-different
        self.assertEqual(metrics["review_false_accept_rate"], 0.5)  # review has its own 2-case denominator
        self.assertEqual(metrics["accepted_review_cases"], 1)

    @unittest.skipIf(np is None, "optional numpy runtime is not installed")
    def test_guard_composition_keeps_contradictions_unmatched_and_missing_lace_in_review(self):
        controls = [
            case("wrong-width", "verified_width_contradiction", "unmatched",
                 sku("au"), sku("rakuten", width=150)),
            case("unknown-lace", "negative_missing_lace_evidence", "review",
                 sku("au", lace=True), sku("rakuten", lace=None)),
            case("same", "positive_format_variation", "matched", sku("au"), sku("rakuten")),
        ]
        with tempfile.TemporaryDirectory() as temp:
            result = comparison.evaluate(
                HighScoreReranker(), controls, mode="sku", batch=8,
                reranker=True, output=Path(temp),
            )

        # The fake gives every pair a high score, so model-only accepts all three.
        # The provided-attribute guard must override a contradiction and preserve review.
        for threshold in result["threshold_grid"]:
            self.assertEqual(threshold["model_only"]["accepted_count"], 3)
            guarded = threshold["model_plus_provided_attribute_guard"]
            self.assertEqual(guarded["confusion_matrix"]["unmatched"]["unmatched"], 1)
            self.assertEqual(guarded["confusion_matrix"]["review"]["review"], 1)
            self.assertEqual(guarded["confusion_matrix"]["matched"]["matched"], 1)
            self.assertEqual(guarded["accepted_review_cases"], 0)

        # The guard contract remains directly inspectable, independent of model score.
        self.assertEqual(comparison.provided_attribute_guard(controls[0]["au"], controls[0]["rakuten"])[0],
                         "unmatched")
        status, reasons = comparison.provided_attribute_guard(controls[1]["au"], controls[1]["rakuten"])
        self.assertEqual(status, "review")
        self.assertIn("lace", reasons)

    def test_missing_true_candidate_stays_in_present_query_recall_denominator(self):
        metrics = comparison.ranking_metrics([1, None, 6], present_queries=3, absent_queries=2)

        self.assertEqual(metrics["present_queries"], 3)
        self.assertEqual(metrics["absent_queries"], 2)
        self.assertAlmostEqual(metrics["recall"]["at_1"], 1 / 3)
        self.assertAlmostEqual(metrics["recall"]["at_5"], 1 / 3)
        self.assertAlmostEqual(metrics["recall"]["at_10"], 2 / 3)
        self.assertAlmostEqual(metrics["mrr"], (1 + 1 / 6) / 3)


if __name__ == "__main__":
    unittest.main()
