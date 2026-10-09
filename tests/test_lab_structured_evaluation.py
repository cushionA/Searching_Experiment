"""Identity-gate checks for configuration mismatches and uncertain sources."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
from evaluate_structured_skus import gate, feature_text, rule_prediction


class StructuredEvaluationTests(unittest.TestCase):
    def entity(self, **attrs):
        return {"attrs": attrs, "raw_sku": "実選択肢", "unknown_fields": [], "unresolved_axes": []}

    def test_same_size_color_does_not_override_lace_conflict(self):
        task = {"rakuten": self.entity(size="100×80cm", color="ベージュ", lace=True)}
        result = gate(task, self.entity(size="100×80cm", color="ベージュ", lace=False))
        self.assertEqual(result["decision"], "unmatched")
        self.assertEqual(result["conflicting_fields"], ["lace"])

    def test_missing_sales_condition_cannot_be_accepted(self):
        task = {"rakuten": self.entity(size="100×80cm", color="ベージュ", lace=True)}
        result = gate(task, self.entity(size="100×80cm", color="ベージュ"))
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["missing_fields"], ["lace"])

    def test_page_measurement_and_accessory_discrepancies_remain_review(self):
        for key, left, right in (("hook_count", 7, 9), ("page_dimensions_cm", [66, 57, 70], [50, 57, 70])):
            with self.subTest(key=key):
                result = gate({"rakuten": self.entity(**{key: left})}, self.entity(**{key: right}))
                self.assertEqual(result["decision"], "review")

    def test_internal_source_conflict_blocks_otherwise_exact_match(self):
        candidate = self.entity(color="シルバー")
        candidate["source_conflicts"] = [{"field": "handle_material", "values": ["ポリエステル", "プラスチック"]}]
        self.assertEqual(gate({"rakuten": self.entity(color="シルバー")}, candidate)["decision"], "review")

    def test_similar_spelling_does_not_assert_color_alias(self):
        result = gate({"rakuten": self.entity(color="カフェオレブラウン")}, self.entity(color="ブラウン"))
        self.assertEqual(result["decision"], "review")

    def test_model_text_excludes_metadata_prices_inventory_and_labels(self):
        row = self.entity(color="赤", lace=False)
        row.update(price=123456, stock="soldout", gold="matched", evidence="秘密の引用", url="https://private.example")
        text = feature_text(row)
        self.assertIn("false", text)
        for marker in ("123456", "soldout", "matched", "秘密", "private.example"):
            self.assertNotIn(marker, text)

    def test_normalization_ambiguity_in_full_candidate_pool_abstains(self):
        task = {"case_id": "fixture", "rakuten": self.entity(color="赤"), "au_candidates": [
            {"row_key": "one", **self.entity(color="赤")}, {"row_key": "two", **self.entity(color="赤")}]}
        self.assertEqual(rule_prediction(task)["decision"], "review")


if __name__ == "__main__":
    unittest.main()
