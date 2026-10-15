"""Focused tests for predeclared generic axis aliases only."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))

try:
    import run_classical_axis_alias_v1 as aliases
except ModuleNotFoundError as error:
    if error.name in {"jaconv", "pint"}:
        aliases = None
    else:
        raise


def fixture(source_axis="color", target_axis="色", source_values=None,
            target_values=None, selected_source="red", selected_target="red",
            target_axes=None):
    source_values = list(source_values or ["red", "blue"])
    target_values = list(target_values or ["red", "blue"])
    docs = [
        {"document_id": "rak-1", "dossier_id": "case-1", "side": "rakuten"},
        {"document_id": "au-1", "dossier_id": "case-1", "side": "au"},
    ]
    requests = [
        {"request_id": "r1", "target_document_id": "au-1",
         "direction": "rakuten_to_au", "axis_name": source_axis,
         "selected_value": selected_source, "option_values": source_values,
         "target_selected_axes": target_axes if target_axes is not None else
             [{"axis_name": target_axis, "value": selected_target}]},
        {"request_id": "r2", "target_document_id": "rak-1",
         "direction": "au_to_rakuten", "axis_name": target_axis,
         "selected_value": selected_target, "option_values": target_values,
         "target_selected_axes": [{"axis_name": source_axis, "value": selected_source}]},
    ]
    return requests, docs


@unittest.skipIf(aliases is None, "install jaconv and Pint in the dedicated venv")
class ClassicalAxisAliasTests(unittest.TestCase):
    def evaluate_r1(self, requests, docs):
        families = aliases.build_families(requests, docs)
        return families, aliases.evaluate_request(requests[0], families, mode="axis_alias")

    def test_declared_cross_label_alias_preserves_raw_labels_and_supports(self):
        requests, docs = fixture(source_axis="color", target_axis="色")
        families, result = self.evaluate_r1(requests, docs)
        self.assertEqual(result["state"], "support")
        self.assertEqual(result["axis_name_raw"], "color")
        self.assertEqual(result["axis_name_normalized"], "カラー")
        self.assertEqual(result["target_selected_axes_raw"], [{"axis_name": "色", "value": "red"}])
        self.assertEqual(result["target_selected_axes_normalized"],
                         [{"axis_name": "カラー", "value": "red"}])
        mapping = families["axis_alias"]["request_pairs"]["r1"]["axis_mapping"]
        self.assertEqual(mapping["source_axis_name_raw"], "color")
        self.assertEqual(mapping["target_selected_axis_labels"], ["色"])

    def test_cross_label_selected_value_conflict_requires_complete_family_mapping(self):
        requests, docs = fixture(source_axis="color", target_axis="カラー",
                                 selected_source="red", selected_target="blue")
        _, result = self.evaluate_r1(requests, docs)
        self.assertEqual(result["family_state"], "support")
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(result["axis_name_raw"], "color")
        self.assertEqual(result["axis_name_normalized"], "カラー")

    def test_alias_does_not_bypass_complete_whole_family_bijection(self):
        requests, docs = fixture(source_axis="color", target_axis="色",
                                 source_values=["red", "blue"],
                                 target_values=["red", "cyan"],
                                 selected_source="red", selected_target="red")
        _, result = self.evaluate_r1(requests, docs)
        self.assertEqual(result["family_state"], "unknown")
        self.assertEqual(result["state"], "unknown")

    def test_same_document_alias_class_collision_is_unknown(self):
        requests, docs = fixture(source_axis="color", target_axis="色")
        duplicate_axis_request = {**requests[0], "request_id": "r1b", "axis_name": "colour"}
        requests.insert(1, duplicate_axis_request)
        families, result = self.evaluate_r1(requests, docs)
        source_axes = families["axis_alias"]["source_families"]["rak-1"]
        self.assertFalse(source_axes["カラー"]["valid"])
        self.assertIn("canonical_axis_label_collision", source_axes["カラー"]["reasons"])
        self.assertEqual(result["state"], "unknown")
        self.assertIn("source_canonical_axis_label_collision", result["reasons"])

    def test_target_selected_alias_collision_is_unknown(self):
        requests, docs = fixture(source_axis="color", target_axis="色",
            target_axes=[{"axis_name": "色", "value": "red"},
                         {"axis_name": "カラー", "value": "red"}])
        _, result = self.evaluate_r1(requests, docs)
        self.assertEqual(result["state"], "unknown")
        self.assertIn("target_selected_axis_alias_collision", result["reasons"])

    def test_unconfigured_width_aliases_stay_distinct_and_unmatched(self):
        requests, docs = fixture(source_axis="幅", target_axis="横幅")
        families, result = self.evaluate_r1(requests, docs)
        record = families["axis_alias"]["request_pairs"]["r1"]
        self.assertEqual(record["axis_mapping"]["alias_group"], None)
        self.assertEqual(record["axis_mapping"]["source_axis_name_normalized"], "幅")
        self.assertEqual(record["normalized_target_selected_axes"][0]["axis_name"], "横幅")
        self.assertNotIn("axis_alias_candidates", result["candidate_suggestions"])
        self.assertEqual(result["state"], "unknown")

    def test_advisory_axis_candidates_cannot_change_unmatched_result(self):
        requests, docs = fixture(source_axis="color", target_axis="色",
                                 source_values=["red", "blue"],
                                 target_values=["red", "cyan"],
                                 selected_source="red", selected_target="red")
        _, result = self.evaluate_r1(requests, docs)
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(result["suggestions_affect_state"])
        self.assertTrue(result["candidate_suggestions"]["axis_mapping_advisory_only"])


if __name__ == "__main__":
    unittest.main()
