"""Boundary tests for native spaCy input-local phrase components."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))

try:
    import spacy
    from library_local_phrase_components_v1 import LocalPhraseCatalog
except ModuleNotFoundError as error:
    if error.name in {"spacy", "library_local_phrase_components_v1"}:
        spacy = None
        LocalPhraseCatalog = None
    else:
        raise

try:
    import run_library_components_v1 as component_runner
except ModuleNotFoundError as error:
    if error.name in {"run_library_components_v1", "spacy"}:
        component_runner = None
    else:
        raise


@unittest.skipIf(spacy is None, "install spaCy in the dedicated venv")
class LocalPhraseCatalogTests(unittest.TestCase):
    def setUp(self):
        self.nlp = spacy.blank("ja")

    def test_black_and_crew_form_a_lexical_compound_across_two_axes(self):
        catalog = LocalPhraseCatalog(self.nlp, {
            "Color": ["black"],
            "Neck": ["crew"],
        })
        result = catalog.analyze("black crew")
        self.assertTrue(result["unique_full_compound"])
        self.assertEqual(result["ambiguity_reasons"], [])
        self.assertEqual([(hit["axis_name"], hit["raw_option_value"], hit["quote"])
                          for hit in result["matches"]], [
                              ("Color", "black", "black"),
                              ("Neck", "crew", "crew"),
                          ])
        self.assertEqual(result["matched_axes"], [
            {"axis_name": "Color", "raw_option_values": ["black"]},
            {"axis_name": "Neck", "raw_option_values": ["crew"]},
        ])
        self.assertTrue(result["lexical_components_only"])
        self.assertFalse(result["production_adoption_allowed"])

    def test_unmatched_digits_and_model_code_fragments_block_complete_coverage(self):
        catalog = LocalPhraseCatalog(self.nlp, {
            "Color": ["black"],
            "Neck": ["crew"],
        })
        text = "black crew ZX9000"
        result = catalog.analyze(text)
        self.assertEqual([hit["quote"] for hit in result["matches"]], ["black", "crew"])
        self.assertFalse(result["unique_full_compound"])
        self.assertFalse(result["unique_full_compound_ignoring_native_punctuation"])
        self.assertIn("unmatched_substantive_tokens", result["ambiguity_reasons"])
        unmatched = result["unmatched_substantive_spans"]
        self.assertEqual([item["text"] for item in unmatched], ["ZX", "9000"])
        self.assertEqual([item["text"] for item in unmatched], result["unmatched_substantive_text"])
        for item in unmatched:
            start, end = item["source_span"]
            self.assertEqual(item["text"], text[start:end])

    def test_parentheses_only_clear_secondary_punctuation_diagnostic(self):
        text = "black (crew)"
        result = LocalPhraseCatalog(self.nlp, {
            "Color": ["black"], "Neck": ["crew"],
        }).analyze(text)
        self.assertFalse(result["unique_full_compound"])
        self.assertTrue(result["unique_full_compound_ignoring_native_punctuation"])
        punctuation = result["ignored_native_punctuation_spans"]
        self.assertEqual([item["text"] for item in punctuation], ["(", ")"])
        for item in punctuation:
            start, end = item["source_span"]
            self.assertEqual(text[start:end], item["text"])

    def test_plus_and_pipe_remain_unmatched_nonpunctuation_in_both_diagnostics(self):
        for text, symbol in (("black+crew", "+"), ("black|crew", "|")):
            with self.subTest(text=text):
                result = LocalPhraseCatalog(self.nlp, {
                    "Color": ["black"], "Neck": ["crew"],
                }).analyze(text)
                self.assertFalse(result["unique_full_compound"])
                self.assertFalse(result["unique_full_compound_ignoring_native_punctuation"])
                self.assertIn(symbol, result["unmatched_substantive_text"])
                self.assertEqual(result["ignored_native_punctuation_spans"], [])

    def test_same_native_phrase_across_two_axes_retains_both_and_is_ambiguous(self):
        result = LocalPhraseCatalog(self.nlp, {
            "Color": ["black"],
            "Style": ["black"],
            "Neck": ["crew"],
        }).analyze("black crew")
        black_hits = [hit for hit in result["matches"] if hit["quote"] == "black"]
        self.assertEqual({hit["axis_name"] for hit in black_hits}, {"Color", "Style"})
        self.assertTrue(all(hit["ambiguous_across_axes"] for hit in black_hits))
        self.assertTrue(all(hit["axes_for_phrase"] == ["Color", "Style"]
                            for hit in black_hits))
        self.assertIn("same_native_phrase_maps_to_distinct_axes", result["ambiguity_reasons"])
        self.assertFalse(result["unique_full_compound"])

    def test_distinct_values_for_one_axis_remain_visible_and_block_compound_claim(self):
        result = LocalPhraseCatalog(self.nlp, {
            "Color": ["black", "white"],
            "Neck": ["crew"],
        }).analyze("black crew white")
        colors = {hit["raw_option_value"] for hit in result["matches"]
                  if hit["axis_name"] == "Color"}
        self.assertEqual(colors, {"black", "white"})
        self.assertIn("multiple_different_values_matched_on_same_axis",
                      result["ambiguity_reasons"])
        self.assertFalse(result["unique_full_compound"])

    def test_overlapping_phrase_hits_are_both_retained_but_never_complete(self):
        result = LocalPhraseCatalog(self.nlp, {
            "Prefix": ["red black"],
            "Suffix": ["black crew"],
        }).analyze("red black crew")
        self.assertEqual([hit["quote"] for hit in result["matches"]],
                         ["red black", "black crew"])
        self.assertTrue(result["overlapping_matches"])
        overlap = result["overlapping_matches"][0]
        self.assertEqual(overlap["span"], [4, 9])
        self.assertIn("overlapping_distinct_matches", result["ambiguity_reasons"])
        self.assertFalse(result["unique_full_compound"])

    def test_native_normalization_collision_does_not_collapse_raw_catalog_entries(self):
        catalog = LocalPhraseCatalog(self.nlp, {
            "Color": ["black"],
            "Style": ["ｂｌａｃｋ"],
        })
        result = catalog.analyze("black")
        self.assertEqual(len(result["matches"]), 2)
        self.assertEqual({hit["raw_option_value"] for hit in result["matches"]},
                         {"black", "ｂｌａｃｋ"})
        self.assertTrue(all(hit["normalized_catalog_collision"] for hit in result["matches"]))
        self.assertIn("matched_normalized_catalog_collision", result["ambiguity_reasons"])
        self.assertTrue(result["normalized_catalog_collisions"])
        self.assertFalse(result["unique_full_compound"])

    def test_single_atom_is_an_observation_not_a_compound(self):
        result = LocalPhraseCatalog(self.nlp, {
            "Color": ["black"],
            "Neck": ["crew"],
        }).analyze("black")
        self.assertEqual(result["matched_atom_count"], 1)
        self.assertEqual(result["matched_axes"], [
            {"axis_name": "Color", "raw_option_values": ["black"]},
        ])
        self.assertIn("fewer_than_two_distinct_axes", result["ambiguity_reasons"])
        self.assertFalse(result["unique_full_compound"])

    def test_arbitrary_new_input_values_need_no_external_dictionary_rules(self):
        # Both patterns are generated directly from this request's catalog; the
        # words are intentionally unrelated to the fixed showcase values above.
        catalogs = {"色": ["珊瑚ミント"], "形": ["折り畳みZ"]}
        result = LocalPhraseCatalog(self.nlp, catalogs).analyze("珊瑚ミント 折り畳みZ")
        self.assertEqual({hit["raw_option_value"] for hit in result["matches"]},
                         {"珊瑚ミント", "折り畳みZ"})
        self.assertTrue(result["unique_full_compound"])
        self.assertFalse(result["catalog_metadata"]["custom_grammar_or_alias_rules"])

    def test_unicode_source_spans_and_token_offsets_quote_original_text_exactly(self):
        text = "🧵black crew"
        result = LocalPhraseCatalog(self.nlp, {
            "Color": ["black"], "Neck": ["crew"],
        }).analyze(text)
        self.assertEqual([(hit["quote"], hit["source_span"]) for hit in result["matches"]], [
            ("black", [1, 6]), ("crew", [7, 11]),
        ])
        for hit in result["matches"]:
            start, end = hit["source_span"]
            self.assertEqual(hit["quote"], text[start:end])
        emoji = next(token for token in result["raw_tokens"] if token["text"] == "🧵")
        self.assertEqual(emoji["source_span"], [0, 1])
        self.assertIn("🧵", result["unmatched_substantive_text"])
        self.assertFalse(result["unique_full_compound"])

    def test_native_tokenizer_view_has_no_trained_components(self):
        catalog = LocalPhraseCatalog(self.nlp, {"Color": ["black"], "Neck": ["crew"]})
        result = catalog.analyze("black crew")
        self.assertEqual(self.nlp.pipe_names, [])
        self.assertEqual(result["catalog_metadata"]["trained_components"], [])
        self.assertEqual(result["catalog_metadata"]["matcher"], "spacy.matcher.PhraseMatcher")
        self.assertEqual(result["catalog_metadata"]["matcher_attr"], "NORM")


@unittest.skipIf(component_runner is None, "install spaCy in the dedicated venv")
class ComponentRouteIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.nlp = spacy.blank("ja")

    def evaluate(self, compound_values, *, target_fields, atom_axes=None,
                 independent_size=True):
        dossier = "dossier-1"
        atom_axes = atom_axes or {"Color": ["black", "white"],
                                  "Neck": ["crew", "high"]}
        values = list(dict.fromkeys(
            [value for options in atom_axes.values() for value in options]
            + list(compound_values)
            + (["S", "M"] if independent_size else [])
        ))
        raw_view = {value: value for value in values}
        families = {}

        def add_family(side, axis, options):
            families[dossier, side, axis] = {
                "dossier_id": dossier, "side": side, "axis_name": axis,
                "raw_values": list(options), "view_values": list(options),
                "request_ids": [f"{side}-{axis}"], "valid": True,
                "invalid_reason": None,
            }

        for axis, options in atom_axes.items():
            add_family("rakuten", axis, options)
        # The target catalog under Style is a compound family whose members are
        # individually assembled from the source side's current option catalogs.
        add_family("au", "Style", compound_values)
        if independent_size:
            add_family("au", "Size", ["S", "M"])
        if any(entry.get("axis_name") == "Graphic" for entry in target_fields):
            add_family("au", "Graphic", compound_values)

        analyzer = LocalPhraseCatalog(self.nlp, atom_axes)
        analyzer.metadata["catalog_id"] = f"{dossier}:rakuten"
        request = {
            "direction": "rakuten_to_au", "axis_name": "Color",
            "selected_value": "black", "option_values": ["black", "white"],
            "source_family_status": "complete",
            "target_selected_axes": list(target_fields),
        }
        doc = {"dossier_id": dossier}
        return component_runner.evaluate_request(
            request, doc, families, {(dossier, "rakuten"): analyzer}, raw_view,
            self.nlp, {}, {}, {"misses": 0, "hits": 0, "lookups": 0}, False)

    def evaluate_route_a(self, compound_values, target_fields):
        """Source AU carries a compound Color family; target Rakuten is atomic."""
        dossier = "route-a-dossier"
        atom_axes = {"Color": ["black", "white"], "Fur": ["crew", "high"]}
        values = list(dict.fromkeys(
            [value for options in atom_axes.values() for value in options]
            + list(compound_values) + ["S", "M"]
        ))
        raw_view = {value: value for value in values}
        families = {}

        def add_family(side, axis, options):
            families[dossier, side, axis] = {
                "dossier_id": dossier, "side": side, "axis_name": axis,
                "raw_values": list(options), "view_values": list(options),
                "request_ids": [f"{side}-{axis}"], "valid": True,
                "invalid_reason": None,
            }

        add_family("au", "Color", compound_values)
        for axis, options in atom_axes.items():
            add_family("rakuten", axis, options)
        add_family("rakuten", "Size", ["S", "M"])
        analyzer = LocalPhraseCatalog(self.nlp, atom_axes)
        analyzer.metadata["catalog_id"] = f"{dossier}:rakuten"
        request = {
            "direction": "au_to_rakuten", "axis_name": "Color",
            "selected_value": compound_values[0], "option_values": list(compound_values),
            "source_family_status": "complete",
            "target_selected_axes": list(target_fields),
        }
        doc = {"dossier_id": dossier}
        return component_runner.evaluate_request(
            request, doc, families, {(dossier, "rakuten"): analyzer}, raw_view,
            self.nlp, {}, {}, {"misses": 0, "hits": 0, "lookups": 0}, False)

    def test_route_b_finds_compound_target_even_with_independent_size_field(self):
        result = self.evaluate(
            ["black crew", "black high", "white crew", "white high"],
            target_fields=[{"axis_name": "Size", "value": "M"},
                           {"axis_name": "Style", "value": "black crew"}],
        )
        self.assertEqual(result["hypothesis_state"], "support")
        route_b = [route for route in result["applicable_routes"]
                   if route["route"] == "target_compound_to_source_atomic"]
        self.assertEqual(len(route_b), 1)
        self.assertEqual(route_b[0]["target_axis_name"], "Style")
        self.assertEqual(route_b[0]["component_axes"], ["Color", "Neck"])
        self.assertFalse(result["production_adoption_allowed"])

    def test_two_applicable_compound_fields_hold_even_when_both_support(self):
        compounds = ["black crew", "black high", "white crew", "white high"]
        result = self.evaluate(compounds, target_fields=[
            {"axis_name": "Style", "value": "black crew"},
            {"axis_name": "Graphic", "value": "black crew"},
        ])
        self.assertEqual(result["hypothesis_state"], "unknown")
        self.assertEqual(result["reason"], "multiple_target_compound_fields_include_request_axis")
        self.assertEqual(result["applicable_routes"], [])

    def test_diagonal_only_compound_domain_without_observed_two_by_two_holds(self):
        result = self.evaluate(
            ["black crew", "white high"],
            target_fields=[{"axis_name": "Style", "value": "black crew"}],
        )
        self.assertEqual(result["hypothesis_state"], "unknown")
        self.assertEqual(result["applicable_routes"], [])
        self.assertIn("no_unique_repeat_supported_target_compound_field", result["reason"])

    def test_three_atom_witness_identifies_the_fixed_axis_by_component_label(self):
        atom_axes = {"Color": ["black", "white"], "Fabric": ["cotton"],
                     "Neck": ["crew", "high"]}
        compounds = ["black crew cotton", "black high cotton",
                     "white crew cotton", "white high cotton"]
        result = self.evaluate(compounds,
            target_fields=[{"axis_name": "Style", "value": "black crew cotton"}],
            atom_axes=atom_axes, independent_size=False)
        self.assertEqual(result["hypothesis_state"], "support")
        route = result["applicable_routes"][0]
        self.assertEqual(route["component_axes"], ["Color", "Fabric", "Neck"])
        witness = route["repeat_witness"]
        self.assertIsNotNone(witness)
        self.assertEqual(witness["varying_axis_indices"], [0, 2])
        fixed = witness["other_atoms_fixed"]
        self.assertEqual(len(fixed), 1)
        # Witness positions are interpreted against the recorded stable axis
        # list; this guards against relabeling the fixed Fabric atom as Color/Neck.
        for position, atom_value in fixed.items():
            self.assertEqual(route["component_axes"][int(position)], "Fabric")
            self.assertEqual(atom_value, "cotton")

    def test_route_a_accepts_required_atoms_while_preserving_independent_target_size(self):
        compounds = ["black crew", "black high", "white crew", "white high"]
        result = self.evaluate_route_a(compounds, [
            {"axis_name": "Color", "value": "black"},
            {"axis_name": "Fur", "value": "crew"},
            {"axis_name": "Size", "value": "M"},
        ])
        self.assertEqual(result["hypothesis_state"], "support")
        route = next(route for route in result["applicable_routes"]
                     if route["route"] == "source_compound_to_target_atomic")
        self.assertEqual(route["component_axes"], ["Color", "Fur"])

    def test_route_a_missing_required_atom_holds_even_with_independent_size(self):
        compounds = ["black crew", "black high", "white crew", "white high"]
        result = self.evaluate_route_a(compounds, [
            {"axis_name": "Color", "value": "black"},
            {"axis_name": "Size", "value": "M"},
        ])
        self.assertEqual(result["hypothesis_state"], "unknown")
        self.assertEqual(result["applicable_routes"], [])

    def test_route_a_duplicate_component_selection_holds(self):
        compounds = ["black crew", "black high", "white crew", "white high"]
        result = self.evaluate_route_a(compounds, [
            {"axis_name": "Color", "value": "black"},
            {"axis_name": "Color", "value": "white"},
            {"axis_name": "Fur", "value": "crew"},
            {"axis_name": "Size", "value": "M"},
        ])
        self.assertEqual(result["hypothesis_state"], "unknown")
        self.assertEqual(result["applicable_routes"], [])

    def test_route_a_conflicts_when_one_required_component_value_differs(self):
        compounds = ["black crew", "black high", "white crew", "white high"]
        result = self.evaluate_route_a(compounds, [
            {"axis_name": "Color", "value": "white"},
            {"axis_name": "Fur", "value": "crew"},
            {"axis_name": "Size", "value": "M"},
        ])
        self.assertEqual(result["hypothesis_state"], "conflict")
        route = next(route for route in result["applicable_routes"]
                     if route["route"] == "source_compound_to_target_atomic")
        self.assertEqual(route["state"], "conflict")

    def test_route_a_strict_compound_reading_holds_on_parentheses(self):
        compounds = ["black (crew)", "black (high)",
                     "white (crew)", "white (high)"]
        result = self.evaluate_route_a(compounds, [
            {"axis_name": "Color", "value": "black"},
            {"axis_name": "Fur", "value": "crew"},
            {"axis_name": "Size", "value": "M"},
        ])
        self.assertEqual(result["hypothesis_state"], "unknown")
        self.assertEqual(result["applicable_routes"], [])


if __name__ == "__main__":
    unittest.main()
