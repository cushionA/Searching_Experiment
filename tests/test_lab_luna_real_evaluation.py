"""Contract checks for the Luna-annotated real SKU evaluation adapter."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))
spec = importlib.util.spec_from_file_location(
    "luna_real_sku_evaluation_test", EXPERIMENT / "evaluate_luna_real_skus.py")
evaluation = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(evaluation)


class LunaRealEvaluationTests(unittest.TestCase):
    def case(self, case_id, decision, shard="dev", group="g1", product="p1", url="u1"):
        return {"case_id": case_id, "decision": decision, "shard_id": shard,
                "group_id": group, "product_id": product, "rakuten_url": url,
                "gold_row_keys": ["au:p1:1:0:0"] if decision == "matched" else []}

    def test_review_truth_is_unknown_and_reported_separately(self):
        cases = [self.case("pos", "matched"), self.case("neg", "unmatched"),
                 self.case("rev", "review")]
        predictions = [
            {"decision": "matched", "top_row_key": "au:p1:1:0:0"},
            {"decision": "unmatched", "top_row_key": "au:p1:1:0:0"},
            {"decision": "matched", "top_row_key": "au:p1:1:0:0"},
        ]
        result = evaluation.metrics(cases, predictions)
        self.assertEqual(result["known_case_precision"], 1.0)
        self.assertEqual(result["review_unknown_accept_count"], 1)
        self.assertEqual(result["review_gold_unknown_count"], 1)

    def test_wrong_top_sku_is_separate_from_matched_detection(self):
        cases = [self.case("pos", "matched")]
        prediction = [{"decision": "matched", "top_row_key": "wrong-au-row"}]
        result = evaluation.metrics(cases, prediction)
        self.assertEqual(result["matched_detection_recall"], 1.0)
        self.assertEqual(result["known_case_recall"], 0.0)
        self.assertEqual(result["wrong_au_sku_selected_after_matched_detection"], 1)
        self.assertEqual(result["gold_matched_candidate_miss_count"], 1)

    def test_same_group_same_au_product_or_rakuten_url_cannot_cross_shards(self):
        checks = [
            [self.case("a", "matched", "dev"), self.case("b", "matched", "test")],
            [self.case("a", "matched", "dev", url="u1"), self.case("b", "matched", "test", group="g2", product="p1", url="u2")],
            [self.case("a", "matched", "dev", url="u1"), self.case("b", "matched", "test", group="g2", product="p2", url="u1")],
        ]
        for cases in checks:
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                evaluation.validate_group_splits(cases)

    def test_dev_threshold_cannot_claim_unmet_precision_goal(self):
        cases = [self.case("pos", "matched"), self.case("neg", "unmatched")]
        # Both cases have identical scores, so no fixed threshold can separate them.
        raw = [{"score": 0.8, "top_row_key": "au:p1:1:0:0"},
               {"score": 0.8, "top_row_key": "au:p1:1:0:0"}]
        chosen = evaluation.select_threshold(cases, raw)
        self.assertEqual(chosen["selection_status"], "hold_precision_target_not_met")
        self.assertIn("no population-level precision guarantee", chosen["claim"])

    def test_model_inputs_use_only_prepared_title_and_sku_axis_strings(self):
        case = {
            "query_sku": "色=赤 / サイズ=幅100cm",
            "query_title_sku": "商品名 / 色=赤 / サイズ=幅100cm",
            "price": 1234, "stock": {"isSoldOut": True},
            "evidence": "注釈用証拠", "gold_row_keys": ["annotated-key"],
            "candidates": [{"text_sku": "色=赤 / サイズ=幅100cm",
                            "text_title_sku": "AU商品 / 色=赤 / サイズ=幅100cm",
                            "price": 5678, "stock": {"isSoldOut": True}}],
        }
        query, candidates = evaluation.model_input_texts(case, "sku")
        self.assertEqual(query, "色=赤 / サイズ=幅100cm")
        self.assertEqual(candidates, ["色=赤 / サイズ=幅100cm"])
        query, candidates = evaluation.model_input_texts(case, "title-sku")
        self.assertNotIn("1234", query)
        self.assertNotIn("5678", candidates[0])
        self.assertNotIn("annotated-key", query + " ".join(candidates))
        self.assertNotIn("証拠", query + " ".join(candidates))

    def test_pre_and_post_snapshots_detect_a_changed_frozen_input(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            inputs = base / "inputs"
            labels_dir = base / "labels"
            dossiers = inputs / "dossiers"
            dossiers.mkdir(parents=True)
            labels_dir.mkdir()
            for name in ("cases.jsonl", "eligibility.jsonl", "au_eligibility.jsonl",
                         "dossier_index.json", "TASK_SPEC.txt"):
                (inputs / name).write_text("stable\n")
            (dossiers / "d.json").write_text("{}\n")
            (inputs / "manifest.json").write_text("{}\n")
            (labels_dir / "labels.jsonl").write_text("{}\n")
            (labels_dir / "manifest.json").write_text("{}\n")
            arrays = base / "au_product_sku_arrays.jsonl"
            arrays.write_text("{}\n")
            before = evaluation.collect_input_snapshot(inputs, labels_dir / "labels.jsonl", arrays)
            (inputs / "cases.jsonl").write_text("changed\n")
            after = evaluation.collect_input_snapshot(inputs, labels_dir / "labels.jsonl", arrays)
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
