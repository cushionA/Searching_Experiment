"""CPU-free structural contracts; synthetic fixtures are not evaluation data."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKU_DIR = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU_DIR))
SPEC = importlib.util.spec_from_file_location("scoped_nli_choice", SKU_DIR / "trial_scoped_condition_nli_choice_v1.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def record(value, support, **updates):
    return {"option_value": value, "probabilities": {"support": support, "conflict": 0, "unknown": 1 - support},
            "token_count_untruncated": 30, "error_type": None, **updates}


class ScopedNliChoiceContracts(unittest.TestCase):
    def test_full_alternatives_enter_hypotheses_equally_without_selected_hint(self):
        doc = {"document_id": "synthetic-target", "title": {"text": "原商品名"}}
        req = {"request_id": "synthetic-request", "request_sha256": "a" * 64, "direction": "rakuten_to_au",
               "axis_name": "販売仕様", "selected_value": "幅100×80cm / レースあり", "target_document_id": doc["document_id"],
               "option_values": ["幅100×80cm / レースあり", "幅100×80cm / レースなし"],
               "target_selected_axes_text": "カラー：青\nサイズ：幅100×80cm"}
        first = RUNNER.pairs_for([req], {doc["document_id"]: doc}, {req["request_id"]: [{"source_sku_key": "raw-key"}]})
        second = RUNNER.pairs_for([{**req, "selected_value": req["option_values"][1]}], {doc["document_id"]: doc}, {req["request_id"]: []})
        self.assertEqual([(p["premise"], p["hypothesis"]) for p in first], [(p["premise"], p["hypothesis"]) for p in second])
        self.assertEqual([p["option_value"] for p in first], req["option_values"])
        self.assertIn(req["option_values"][0], first[0]["hypothesis"])
        self.assertNotIn(req["selected_value"], first[0]["premise"])
        self.assertNotIn("引用本文", first[0]["premise"])
        self.assertEqual(first[0]["source_task_bindings"], [{"source_sku_key": "raw-key"}])

    def test_absolute_support_is_distinct_from_relative_diagnostic(self):
        result = RUNNER.choose(["A", "B"], "A", [record("A", .90), record("B", .10)])
        self.assertEqual((result["state"], result["reason"]), ("support", "absolute_entailment"))
        result = RUNNER.choose(["A", "B"], "A", [record("A", .06), record("B", .04)])
        self.assertEqual((result["state"], result["reason"]), ("support", "relative_choice_diagnostic"))
        self.assertAlmostEqual(result["relative_entailment_scores"]["A"], .6)

    def test_alternate_winner_is_unknown_not_contradiction(self):
        result = RUNNER.choose(["A", "B"], "A", [record("A", .04), record("B", .06)])
        self.assertEqual((result["state"], result["reason"], result["winning_value"]), ("unknown", "alternative_value_selected", "B"))

    def test_high_absolute_support_cannot_override_a_stronger_competing_option(self):
        result = RUNNER.choose(["A", "B"], "A", [record("A", .92), record("B", .95)])
        self.assertEqual((result["state"], result["reason"], result["winning_value"]), ("unknown", "alternative_value_selected", "B"))

    def test_single_option_cannot_gain_support_from_relative_ratio(self):
        result = RUNNER.choose(["A"], "A", [record("A", .001)])
        self.assertEqual((result["state"], result["reason"]), ("unknown", "single_option_without_absolute_entailment"))
        self.assertEqual(RUNNER.choose(["A"], "A", [record("A", .90)])["state"], "support")

    def test_tied_leader_is_unknown_even_at_high_absolute_probability(self):
        result = RUNNER.choose(["A", "B"], "A", [record("A", .95), record("B", .95)])
        self.assertEqual((result["state"], result["reason"]), ("unknown", "top_support_probability_tie"))

    def test_all_original_candidates_must_have_valid_complete_scores(self):
        cases = ([record("A", .99)], [record("A", .99), record("B", .01, token_count_untruncated=513)],
                 [record("A", .99), record("B", .01, error_type="RuntimeError")],
                 [record("A", .99), record("B", .01, probabilities=None)])
        for records in cases:
            with self.subTest(records=records):
                self.assertEqual(RUNNER.choose(["A", "B"], "A", records)["state"], "unknown")

    def test_no_mass_and_insufficient_relative_mass_are_unknown(self):
        self.assertEqual(RUNNER.choose(["A", "B"], "A", [record("A", 0), record("B", 0)])["reason"], "no_positive_entailment_mass")
        result = RUNNER.choose(["A", "B", "C"], "A", [record("A", .04), record("B", .035), record("C", .025)])
        self.assertEqual((result["state"], result["reason"]), ("unknown", "relative_mass_below_threshold"))

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError):
                RUNNER.run(Path(td), Path(td), Path(td), Path("not-loaded"))


if __name__ == "__main__":
    unittest.main()
