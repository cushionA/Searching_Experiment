"""Complete-array decisions must not confuse a bad proposal with no match."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
from evaluate_fixed_pool_hybrid import fixed_pool_prediction


class FixedPoolTests(unittest.TestCase):
    def test_different_named_choices_can_reject_but_same_physical_shape_requires_review(self):
        result = self.run_pool({"named_size": "シングル"}, [self.candidate("double", named_size="ダブル")], "double")
        self.assertEqual(result["decision"], "unmatched")
        result = self.run_pool({"named_size": "シングル", "size_cm": [100, 200]},
                              [self.candidate("double", named_size="ダブル", size_cm=[100, 200])], "double")
        self.assertEqual(result["decision"], "review")

    def test_distinct_measurement_objects_cannot_use_size_difference_alone(self):
        result = self.run_pool({"size_cm": [45, 45], "dimension_role": "クッションカバー"},
                              [self.candidate("blanket", size_cm=[70, 100])], "blanket")
        self.assertEqual(result["decision"], "review")

    def test_named_sku_size_proof_keeps_unknown_dimensions_in_source_metadata(self):
        query = {"attrs": {"color": "赤", "named_size": "キング"}, "required_fields": ["color", "named_size"],
                 "unknown_fields": ["size_cm"]}
        candidate = {"row_key": "one", "attrs": {"color": "赤", "named_size": "キング"},
                     "required_fields": ["color"], "unknown_fields": ["size_cm"]}
        result = fixed_pool_prediction({"case_id": "fixture", "rakuten": query, "au_candidates": [candidate]},
                                       {"case_id": "fixture", "top_row_key": "one"})
        self.assertEqual(result["decision"], "matched")
        self.assertEqual(query["unknown_fields"], ["size_cm"])
        candidate["attrs"].pop("named_size")
        result = fixed_pool_prediction({"case_id": "fixture", "rakuten": query, "au_candidates": [candidate]},
                                       {"case_id": "fixture", "top_row_key": "one"})
        self.assertEqual(result["decision"], "review")

    def test_optional_page_omission_differs_from_missing_selected_axis(self):
        query = {"attrs": {"color": "赤", "size_cm": [100, 80]}, "required_fields": ["color", "size_cm"]}
        candidate = {"row_key": "one", "attrs": {"color": "赤", "size_cm": [100, 80], "folded_dimensions_cm": [10, 8]},
                     "required_fields": ["color"]}
        ranking = {"case_id": "fixture", "top_row_key": "one"}
        result = fixed_pool_prediction({"case_id": "fixture", "rakuten": query, "au_candidates": [candidate]}, ranking)
        self.assertEqual(result["decision"], "matched")
        del candidate["attrs"]["size_cm"]
        result = fixed_pool_prediction({"case_id": "fixture", "rakuten": query, "au_candidates": [candidate]}, ranking)
        self.assertEqual(result["decision"], "review")

    def candidate(self, key, **attrs):
        return {"row_key": key, "attrs": attrs}

    def run_pool(self, query, candidates, top):
        return fixed_pool_prediction({"case_id": "fixture", "rakuten": {"attrs": query},
                                     "au_candidates": candidates},
                                    {"case_id": "fixture", "top_row_key": top, "score": .01})

    def test_wrong_top1_cannot_discard_proven_match(self):
        result = self.run_pool({"color": "赤", "lace": False},
                              [self.candidate("wrong", color="青", lace=False),
                               self.candidate("right", color="赤", lace=False)], "wrong")
        self.assertEqual((result["decision"], result["top_row_key"]), ("matched", "right"))
        self.assertTrue(result["model_top1_overridden"])

    def test_all_candidates_must_contradict_to_reject(self):
        result = self.run_pool({"size_cm": [100, 80], "lace": True},
                              [self.candidate("wrong", size_cm=[100, 80], lace=False),
                               self.candidate("unknown", size_cm=[100, 80])], "wrong")
        self.assertEqual(result["decision"], "review")

    def test_scoped_numeric_size_difference_is_rejected(self):
        result = self.run_pool({"size_cm": [100, 80]},
                              [self.candidate("wrong", size_cm=[100, 90])], "wrong")
        self.assertEqual(result["decision"], "unmatched")

    def test_reversed_unlabelled_dimensions_need_role_review(self):
        result = self.run_pool({"size_cm": [48, 73]},
                              [self.candidate("other-order", size_cm=[73, 48])], "other-order")
        self.assertEqual(result["decision"], "review")

    def test_lace_and_set_counts_never_overridden_by_model(self):
        result = self.run_pool({"size_cm": [100, 80], "lace": True, "lace_count": 2},
                              [self.candidate("no-lace", size_cm=[100, 80], lace=False, lace_count=0)], "no-lace")
        self.assertEqual(result["decision"], "unmatched")


if __name__ == "__main__":
    unittest.main()
