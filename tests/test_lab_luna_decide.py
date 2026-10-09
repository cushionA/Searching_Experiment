"""Contract tests for the sampled real-data GLiNER Decide diagnostic."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
spec = importlib.util.spec_from_file_location(
    "luna_decide_test", EXPERIMENT / "evaluate_luna_decide.py")
decide = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(decide)


class LunaDecideTests(unittest.TestCase):
    def test_sample_is_stable_bounded_and_preserves_split(self):
        cases = [
            {"case_id": f"case-{i:02}", "dossier_id": f"d-{i}", "group_id": "family-1",
             "split": "dev" if i % 2 == 0 else "test"}
            for i in range(60)
        ]
        dossiers = {
            f"d-{i}": {"dossier_id": f"d-{i}",
                       "au_product": {"product_id": f"product-{i}", "title_raw": "Example product"}}
            for i in range(60)
        }
        first = decide.select_sample(cases, dossiers)
        second = decide.select_sample(list(reversed(cases)), dossiers)
        self.assertEqual([x["case_id"] for x in first], [x["case_id"] for x in second])
        with_late_labels = [{**x, "decision": "matched" if i % 2 else "unmatched",
                             "prediction": "needs human review"}
                            for i, x in enumerate(cases)]
        self.assertEqual([x["case_id"] for x in first],
                         [x["case_id"] for x in decide.select_sample(with_late_labels, dossiers)])
        self.assertEqual(len(first), 16)
        self.assertEqual({x["split"] for x in first}, {"dev", "test"})
        self.assertTrue(all(x["_sample_hash"] == decide.stable_case_hash(x["case_id"])
                            for x in first))

    def test_curtain_family_uses_larger_cap(self):
        cases = [{"case_id": f"case-{i:02}", "dossier_id": f"d-{i}",
                  "group_id": "curtain-family", "split": "dev"}
                 for i in range(40)]
        dossiers = {f"d-{i}": {"au_product": {"product_id": f"curtain-{i}",
                                                  "title_raw": "遮光カーテン"}}
                    for i in range(40)}
        selected = decide.select_sample(cases, dossiers)
        self.assertEqual(len(selected), 32)
        self.assertTrue(all(x["sample_family_cap"] == 32 for x in selected))

    def test_pair_template_uses_plain_pair_and_excludes_metric_fields(self):
        case = {
            "rakuten": {"title_raw": "Observed Rakuten title"},
            "query_sku": "color=blue", "top_au_sku": "color=blue",
            "gold_decision": "matched", "gold_matching_au_row_keys": ["gold"],
            "price": 999, "stock": "available",
        }
        text = decide.model_pair_text(case, "Observed AU title / color=blue", "title-sku")
        self.assertEqual(text, "商品A: Observed Rakuten title / color=blue\n商品B: Observed AU title / color=blue")
        self.assertNotIn("matched", text)
        self.assertNotIn("gold", text)
        self.assertNotIn("999", text)
        self.assertNotIn("available", text)

    def test_rakuten_axis_names_match_embedding_adapter_labels(self):
        rakuten = {
            "axes_labels": [
                {"key": "color", "label": "カラー"},
                {"key": "sections", "label": "段数"},
            ],
            "option_values": [
                {"axis_key": "color", "value": "黒"},
                {"axis_key": "sections", "value": "3段"},
            ],
        }
        self.assertEqual(decide.rakuten_sku_text(rakuten), "カラー=黒 / 段数=3段")

    def test_top1_retrieval_miss_is_counted_as_end_to_end_wrong_accept(self):
        cases = [{"gold_decision": "matched", "top1_retrieval_hit": False}]
        predictions = [{"prediction": decide.LABELS[0]}]
        result = decide.metric_summary(cases, predictions)
        self.assertEqual(result["ruri_top1_retrieval_miss_count_on_luna_matched"], 1)
        self.assertEqual(result["accepted_wrong_top1_same_sku_count"], 1)
        self.assertEqual(result["end_to_end_correct_same_sku_count"], 0)
        self.assertEqual(result["known_luna_matched_precision_including_wrong_top1_as_error"], 0.0)


if __name__ == "__main__":
    unittest.main()
