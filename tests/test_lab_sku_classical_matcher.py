"""Whole-family and fail-closed tests for classical option matching."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))

try:
    import sku_classical_matcher_v1 as matcher
except ModuleNotFoundError as error:
    if error.name in {"jaconv", "pint"}:
        matcher = None
    else:
        raise


def family(document_id, side, values, *, axis="Size", dossier="case-1"):
    return {
        "document_id": document_id,
        "dossier_id": dossier,
        "side": side,
        "axis_name": axis,
        "family_values": list(values),
        "valid": True,
    }


def request_pair(source_values, target_values, source_selected, target_selected,
                 *, axis="Size", suggestions=None):
    return {
        "request_id": "r1",
        "axis_name": axis,
        "source_family": family("source", "rakuten", source_values, axis=axis),
        "target_family": family("target", "au", target_values, axis=axis),
        "target_selected_values_for_axis": [target_selected],
        "target_selected_value": target_selected,
        "suggestions": suggestions or {"advisory_only": True},
        "errors": [],
    }


def request(selected, *, axis="Size", target_selected=None):
    return {"request_id": "r1", "axis_name": axis, "selected_value": selected,
            "target_selected_axes": [{"axis_name": axis, "value": target_selected or "1m"}]}


@unittest.skipIf(matcher is None, "install jaconv and Pint in the dedicated venv")
class ClassicalMatcherTests(unittest.TestCase):
    def test_build_families_reconstructs_both_native_families_from_reverse_requests(self):
        documents = [
            {"document_id": "rakuten-1", "dossier_id": "case-1", "side": "rakuten"},
            {"document_id": "au-1", "dossier_id": "case-1", "side": "au"},
        ]
        requests = [
            {"request_id": "r1", "target_document_id": "au-1",
             "direction": "rakuten_to_au", "axis_name": "Size",
             "selected_value": "100cm", "option_values": ["100cm", "200cm"],
             "target_selected_axes": [{"axis_name": "Size", "value": "1m"}]},
            {"request_id": "r2", "target_document_id": "rakuten-1",
             "direction": "au_to_rakuten", "axis_name": "Size",
             "selected_value": "1m", "option_values": ["1m", "2m"],
             "target_selected_axes": [{"axis_name": "Size", "value": "100cm"}]},
        ]
        families = matcher.build_families(requests, documents)
        pair = families["request_pairs"]["r1"]
        self.assertEqual(pair["source_family"]["document_id"], "rakuten-1")
        self.assertEqual(pair["source_family"]["family_values"], ["100cm", "200cm"])
        self.assertEqual(pair["target_family"]["document_id"], "au-1")
        self.assertEqual(pair["target_family"]["family_values"], ["1m", "2m"])
        result = matcher.evaluate_request(requests[0], families)
        self.assertEqual(result["family_state"], "support")
        self.assertEqual(result["state"], "support")

    def test_conflicting_native_family_vectors_fail_closed(self):
        documents = [
            {"document_id": "rakuten-1", "dossier_id": "case-1", "side": "rakuten"},
            {"document_id": "au-1", "dossier_id": "case-1", "side": "au"},
            {"document_id": "au-2", "dossier_id": "case-1", "side": "au"},
        ]
        requests = [
            {"request_id": "r1", "target_document_id": "au-1",
             "direction": "rakuten_to_au", "axis_name": "Size",
             "selected_value": "100cm", "option_values": ["100cm", "200cm"],
             "target_selected_axes": [{"axis_name": "Size", "value": "1m"}]},
            {"request_id": "r2", "target_document_id": "au-2",
             "direction": "rakuten_to_au", "axis_name": "Size",
             "selected_value": "100cm", "option_values": ["100cm", "300cm"],
             "target_selected_axes": [{"axis_name": "Size", "value": "1m"}]},
        ]
        families = matcher.build_families(requests, documents)
        source_family = families["request_pairs"]["r1"]["source_family"]
        self.assertFalse(source_family["valid"])
        self.assertIn("conflicting_option_values_vectors", source_family["reasons"])
        result = matcher.evaluate_request(requests[0], families)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["family_state"], "unknown")

    def test_complete_unique_family_bijection_supports_typed_equivalents(self):
        pair = request_pair(["100cm", "200cm"], ["1m", "2m"], "100cm", "1m")
        result = matcher.evaluate_request(request("100cm"), {"request_pairs": {"r1": pair}})
        self.assertEqual(result["family_state"], "support")
        self.assertEqual(result["state"], "support")
        self.assertEqual(result["family_mapping"], [
            {"source_index": 0, "source_value": "100cm", "target_index": 0, "target_value": "1m"},
            {"source_index": 1, "source_value": "200cm", "target_index": 1, "target_value": "2m"},
        ])

    def test_literal_baseline_does_not_inherit_typed_unit_equivalence(self):
        pair = request_pair(["100cm", "200cm"], ["1m", "2m"], "100cm", "1m")
        families = {"request_pairs": {"r1": pair}}
        typed = matcher.evaluate_request(request("100cm"), families, mode="typed")
        literal = matcher.evaluate_request(request("100cm"), families, mode="literal")
        self.assertEqual(typed["state"], "support")
        self.assertEqual(literal["state"], "unknown")
        self.assertEqual(literal["family_state"], "unknown")

    def test_conflict_requires_a_complete_family_mapping_and_wrong_target_member(self):
        pair = request_pair(["100cm", "200cm"], ["1m", "2m"], "100cm", "2m")
        result = matcher.evaluate_request(request("100cm", target_selected="2m"),
                                          {"request_pairs": {"r1": pair}})
        self.assertEqual(result["family_state"], "support")
        self.assertEqual(result["state"], "conflict")

    def test_duplicate_canonical_family_values_are_not_a_unique_bijection(self):
        pair = request_pair(["100cm", "1m"], ["100cm", "200cm"], "100cm", "100cm")
        result = matcher.evaluate_request(request("100cm"), {"request_pairs": {"r1": pair}})
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["family_state"], "unknown")
        self.assertIn("canonical_family_collision", result["family_reasons"])

    def test_selected_exact_match_is_not_enough_when_another_family_member_is_unresolved(self):
        # Red is a complete exact selected-value match, while blue has only an
        # advisory similarity to the competing light-blue row. The unresolved
        # second member prevents acceptance of the otherwise matching request.
        suggestions = {"advisory_only": True,
                       "value_suggestions": [{"source_value": "青",
                                              "fuzzy_candidates": [{"value": "水色", "score": 60.0}]}]}
        pair = request_pair(["赤", "青"], ["赤", "水色"], "赤", "赤", suggestions=suggestions)
        result = matcher.evaluate_request(request("赤", target_selected="赤"),
                                          {"request_pairs": {"r1": pair}})
        self.assertEqual(result["family_state"], "unknown")
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["suggestions_affect_state"], False)
        self.assertTrue(result["candidate_suggestions"]["advisory_only"])

    def test_generated_rapidfuzz_candidates_never_turn_a_nonmatch_into_acceptance(self):
        documents = [
            {"document_id": "rakuten-1", "dossier_id": "case-1", "side": "rakuten"},
            {"document_id": "au-1", "dossier_id": "case-1", "side": "au"},
        ]
        requests = [
            {"request_id": "r1", "target_document_id": "au-1",
             "direction": "rakuten_to_au", "axis_name": "Color",
             "selected_value": "blu", "option_values": ["blu", "red"],
             "target_selected_axes": [{"axis_name": "Color", "value": "blue"}]},
            {"request_id": "r2", "target_document_id": "rakuten-1",
             "direction": "au_to_rakuten", "axis_name": "Color",
             "selected_value": "blue", "option_values": ["blue", "red"],
             "target_selected_axes": [{"axis_name": "Color", "value": "blu"}]},
        ]
        families = matcher.build_families(requests, documents)
        result = matcher.evaluate_request(requests[0], families)
        self.assertTrue(result["candidate_suggestions"]["advisory_only"])
        self.assertFalse(result["suggestions_affect_state"])
        suggestions = result["candidate_suggestions"]["value_suggestions"]
        blu = next(item for item in suggestions if item["source_value"] == "blu")
        self.assertIn("blue", [candidate["value"] for candidate in blu["fuzzy_candidates"]])
        expected_method = ("rapidfuzz_ratio" if importlib.util.find_spec("rapidfuzz")
                           else "difflib_ratio_fallback")
        self.assertIn(expected_method, [candidate["method"] for candidate in blu["fuzzy_candidates"]])
        self.assertEqual(result["family_state"], "unknown")
        self.assertEqual(result["state"], "unknown")

    def test_duplicate_target_axis_rows_are_unknown_even_with_a_family_mapping(self):
        pair = request_pair(["100cm", "200cm"], ["1m", "2m"], "100cm", "1m")
        pair["target_selected_values_for_axis"] = ["1m", "2m"]
        result = matcher.evaluate_request(request("100cm"), {"request_pairs": {"r1": pair}})
        self.assertEqual(result["family_state"], "support")
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(any("ambiguous" in reason or "missing" in reason
                            for reason in result["reasons"]))

    def test_axis_label_alias_is_advisory_and_does_not_cross_bind(self):
        pair = request_pair(["100cm", "200cm"], ["1m", "2m"], "100cm", "1m", axis="幅")
        pair["target_family"]["axis_name"] = "横幅"
        result = matcher.evaluate_request(request("100cm", axis="幅"),
                                          {"request_pairs": {"r1": pair}})
        self.assertEqual(result["state"], "unknown")
        self.assertIn("axis_name_not_identical", result["family_reasons"])


if __name__ == "__main__":
    unittest.main()
