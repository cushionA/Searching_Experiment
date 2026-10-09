"""Pure input-contract checks for the structured GLiNER Decide trial."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "sku-matching" / "trial_structured_decide.py"
spec = importlib.util.spec_from_file_location("sku_structured_decide_trial_test", SCRIPT)
assert spec and spec.loader
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class StructuredDecideInputTests(unittest.TestCase):
    def setUp(self):
        self.task = {
            "case_id": "case-1",
            "task_version": "structured-sku-task-v1",
            "rakuten": {
                "raw_sku": "R-001",
                "attrs": {"width_cm": 100, "height_cm": 178, "color": "ベージュ", "pieces": 2,
                          "price": 999, "stock": 3},
                "unknown_fields": ["lace"],
                "source_conflicts": [{"field": "color", "values": ["red", "blue"],
                                      "evidence": [{"source_ref": "https://private.test"}] }],
                "evidence": {"color": [{"quote": "ベージュ", "source_ref": "https://secret.test"}]},
            },
            "au_candidates": [{
                "row_key": "au:page:sku-1", "raw_sku": "AU-001",
                "attrs": {"width_cm": 100, "height_cm": 178, "color": "ベージュ", "lace": False,
                          "page_dimensions_cm": [40, 30, 20],
                          "availability": "in stock"},
                "unknown_fields": ["lace"],
                "evidence": {"color": [{"quote": "ベージュ", "source_ref": "https://secret.test"}]},
            }],
            "page_context": {"category": "遮光カーテン", "attrs": {"pieces": 2},
                              "evidence": {}, "unknown_fields": []},
            "strata": ["must not appear"],
        }

    def test_japanese_prompt_contains_only_selected_structured_facts_and_explicit_unknown(self):
        text = trial.build_input(self.task, "au:page:sku-1", "ja")
        self.assertIn("幅=100", text)
        self.assertIn("丈=178", text)
        self.assertIn("枚数=2", text)  # fixed AU page inherited attribute
        self.assertIn("レース=不明", text)
        self.assertIn("page_dimensions_cm", text)
        self.assertIn("color=red / blue", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("999", text)
        self.assertNotIn("stock", text.casefold())
        self.assertNotIn("must not appear", text)
        self.assertNotIn("ベージュ\",\"source_ref", text)

    def test_english_field_form_uses_same_facts_and_unknown_marker(self):
        text = trial.build_input(self.task, "au:page:sku-1", "english")
        self.assertIn("width_cm=100", text)
        self.assertIn("height_cm=178", text)
        self.assertIn("pieces=2", text)
        self.assertIn("lace=unknown", text)
        self.assertIn("lace=false", text)
        self.assertIn("page_dimensions_cm=40×30×20", text)
        self.assertIn("color=red / blue", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("999", text)

    def test_candidate_key_must_be_in_the_fixed_au_page_pool(self):
        with self.assertRaisesRegex(ValueError, "absent from AU candidates"):
            trial.build_input(self.task, "au:other", "ja")

    def test_metrics_exclude_review_truth_from_known_accuracy(self):
        result = trial.metrics(["matched", "unmatched", "review"],
                               ["matched", "matched", "matched"], [True, False, False])
        self.assertEqual(result["known_case_count"], 2)
        self.assertEqual(result["known_label_accuracy_excluding_review"], 0.5)
        self.assertEqual(result["accepted_review_count"], 1)
        self.assertEqual(result["correct_selection_precision"], 1 / 3)

    def test_gate_never_accepts_unknowns_and_sends_internal_conflicts_to_review(self):
        decision, reasons = trial.deterministic_gate(self.task, "au:page:sku-1", "matched")
        self.assertEqual(decision, "review")
        self.assertIn("within_source_conflict", reasons)
        self.task["rakuten"].pop("source_conflicts")
        decision, reasons = trial.deterministic_gate(self.task, "au:page:sku-1", "matched")
        self.assertEqual(decision, "review")  # lace is unknown on Rakuten
        self.assertTrue(reasons[0].startswith("missing_or_unknown"))


if __name__ == "__main__":
    unittest.main()
