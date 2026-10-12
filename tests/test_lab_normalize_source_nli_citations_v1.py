from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import normalize_source_nli_citations_v1 as normalizer  # noqa: E402
import sku_directional_choice_nli_v1 as nli  # noqa: E402

PREPARED = ROOT / ".lab-output/sku-next-dev-choice-nli-20261013-v2"
SOURCE_ROOT = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"


class SourceCitationIdNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.freeze, cls.pairs, cls.contexts = normalizer.load_source_contexts(PREPARED)
        cls.pair_id, cls.pair = next((pid, pair) for pid, pair in cls.pairs.items()
                                     if len(cls.contexts[pair["context_id"]]["target_selected_facts"]) >= 2)
        cls.context = cls.contexts[cls.pair["context_id"]]
        cls.store = nli.SOURCES.RawStore(SOURCE_ROOT)

    def _response(self, citation, target_fact_ids):
        return {
            "protocol": normalizer.PROTOCOL,
            "pair_id": self.pair_id,
            "cache_key": self.pair["cache_key"],
            "scope_status": "model_assessed_unproved",
            "sourceproof_certified": False,
            "production_adoption_allowed": False,
            "citation_table": [{"citation_id": "cite-1", **citation}],
            "judgments": [{
                "option_index": 0,
                "whole_option": self.pair["option_values"][0],
                "option_evidence_id": self.pair["candidate_vector"][0]["option_evidence_id"],
                "whole_value_assessed": True,
                "state": "explicit_conflict",
                "scope_assessment": "same_condition",
                "semantic_relation": "explicit_conflict",
                "scope_and_coordinate_explanation": "Synthetic exact-span citation normalization case.",
                "option_relation_explanation": "Keep this whole-option explanation unchanged.",
                "exclusivity_assessment": "model_assessed",
                "exclusivity_explanation": "Keep this exclusivity explanation unchanged.",
                "target_fact_ids": list(target_fact_ids),
                "citation_ids": ["cite-1"],
            }],
        }

    @staticmethod
    def _span_citation(span, evidence_id):
        return {"evidence_id": evidence_id, "source_sha256": span["sha256"],
                "start": span["start"], "end": span["end"], "quote": span["quote"]}

    @staticmethod
    def _without_evidence_ids(response):
        value = copy.deepcopy(response)
        for citation in value["citation_table"]:
            citation.pop("evidence_id", None)
        return value

    def test_target_label_id_is_normalized_by_fact_scope_and_exact_raw_tuple(self):
        fact = self.context["target_selected_facts"][0]
        span = fact["axis_name_span"]
        response = self._response(self._span_citation(span, fact["fact_id"]), [fact["fact_id"]])
        before = self._without_evidence_ids(response)
        updated, report = normalizer.normalize_response(response, self.pairs, self.contexts, self.store)
        self.assertEqual(updated["citation_table"][0]["evidence_id"], nli._span_id(span))
        self.assertEqual(self._without_evidence_ids(updated), before)
        self.assertEqual(report[0]["status"], "normalized_exact_label_id")
        nli._verify_span(self.store, span, span["quote"], "test target axis label")

    def test_hypothesis_axis_alias_normalizes_only_when_its_exact_source_tuple_matches(self):
        span = self.context["hypothesis"]["axis_name_span"]
        response = self._response(self._span_citation(span, "source-axis:synthetic"), [])
        updated, report = normalizer.normalize_response(response, self.pairs, self.contexts, self.store)
        self.assertEqual(updated["citation_table"][0]["evidence_id"], nli._span_id(span))
        self.assertEqual(report[0]["span_role"], "hypothesis_axis_label")
        nli._verify_span(self.store, span, span["quote"], "test hypothesis axis label")

    def test_supplemental_registry_validates_source_label_without_changing_semantics(self):
        target = self.context["target_selected_facts"][0]
        hypothesis_span = self.context["hypothesis"]["axis_name_span"]
        member = self.pair["candidate_vector"][0]
        member_span = nli._context_raw_spans(self.context)[member["option_evidence_id"]]
        table = []

        def add(cid, span, evidence_id):
            table.append({"citation_id": cid, **self._span_citation(span, evidence_id)})

        add("target-axis", target["axis_name_span"], nli._span_id(target["axis_name_span"]))
        add("target-value", target["value_span"], target["fact_id"])
        add("candidate-option", member_span, member["option_evidence_id"])
        add("hyp-axis", hypothesis_span, "source-axis:synthetic-batch-id")
        judgments = []
        for candidate in self.pair["candidate_vector"]:
            judgment = {"option_index": candidate["option_index"],
                        "whole_option": candidate["whole_option"],
                        "option_evidence_id": candidate["option_evidence_id"],
                        "whole_value_assessed": True, "state": "unknown",
                        "scope_assessment": "uncertain", "semantic_relation": "uncertain",
                        "scope_and_coordinate_explanation": "Fixture leaves scope unresolved.",
                        "option_relation_explanation": "Fixture leaves this whole value unresolved.",
                        "exclusivity_assessment": "uncertain", "exclusivity_explanation": "Unresolved.",
                        "target_fact_ids": [], "citation_ids": []}
            if candidate["option_index"] == 0:
                judgment.update({"state": "support", "scope_assessment": "same_condition",
                                 "semantic_relation": "support",
                                 "scope_and_coordinate_explanation": "Fixture states the paired coordinate relation.",
                                 "option_relation_explanation": "Fixture assesses the exact whole option.",
                                 "exclusivity_assessment": "source_family",
                                 "target_fact_ids": [target["fact_id"]],
                                 "citation_ids": ["target-axis", "target-value", "candidate-option", "hyp-axis"]})
            judgments.append(judgment)
        response = {"protocol": normalizer.PROTOCOL, "pair_id": self.pair_id,
                    "cache_key": self.pair["cache_key"],
                    "scope_status": "model_assessed_unproved", "sourceproof_certified": False,
                    "production_adoption_allowed": False, "citation_table": table,
                    "judgments": judgments}
        before = self._without_evidence_ids(response)
        with self.assertRaisesRegex(ValueError, "citation"):
            nli.validate_response(PREPARED, response, SOURCE_ROOT)
        normalized, _ = normalizer.normalize_response(response, self.pairs, self.contexts, self.store)
        checked = normalizer.validate_normalized_response(PREPARED, normalized, SOURCE_ROOT)
        self.assertTrue(checked["source_citations_verified"])
        self.assertFalse(checked["semantic_truth_verified"])
        self.assertEqual(self._without_evidence_ids(normalized), before)

    def test_wrong_quote_or_offset_is_left_unchanged_for_frozen_validator(self):
        fact = self.context["target_selected_facts"][0]
        span = fact["axis_name_span"]
        citation = self._span_citation(span, fact["fact_id"])
        citation["quote"] += " forged"
        response = self._response(citation, [fact["fact_id"]])
        updated, report = normalizer.normalize_response(response, self.pairs, self.contexts, self.store)
        self.assertEqual(updated, response)
        self.assertEqual(report[0]["status"], "no_exact_source_match")
        self.assertEqual(updated["judgments"][0]["state"], "explicit_conflict")

    def test_ambiguous_tuple_across_distinct_prepared_coordinates_is_not_rekeyed(self):
        context = copy.deepcopy(self.context)
        facts = context["target_selected_facts"]
        self.assertGreaterEqual(len(facts), 2)
        span1 = copy.deepcopy(facts[0]["axis_name_span"])
        span2 = copy.deepcopy(span1)
        span2["raw_file"] = span2["raw_file"] + ".other-coordinate"
        span2["locator"] = {"json_path": "$.synthetic.otherAxis", "kind": "json_leaf"}
        facts[1]["axis_name_span"] = span2
        context_map = dict(self.contexts)
        context_map[context["context_id"]] = context
        citation = self._span_citation(span1, "wrong-but-plausible-label-id")
        response = self._response(citation, [facts[0]["fact_id"], facts[1]["fact_id"]])
        updated, report = normalizer.normalize_response(response, self.pairs, context_map, self.store)
        self.assertEqual(updated, response)
        self.assertEqual(report[0]["status"], "ambiguous_exact_source_match")
        self.assertEqual(len(report[0]["candidate_evidence_ids"]), 2)

    def test_loader_uses_no_selected_fact_bindings_or_role_markers(self):
        with patch.object(nli, "_load_prepared", side_effect=AssertionError("validator bindings must not be opened")):
            _freeze, pairs, contexts = normalizer.load_source_contexts(PREPARED)
        pair = pairs[self.pair_id]
        context = contexts[pair["context_id"]]
        self.assertNotIn("source_selected_fact", context)
        self.assertTrue(all("role" not in m and "selected" not in m
                            for m in context["hypothesis"]["family_members"]))
        for forbidden in ("source_sku_key", "task_id", "request_id", "case_id"):
            self.assertNotIn(forbidden, pair)
            self.assertNotIn(forbidden, context)


if __name__ == "__main__":
    unittest.main()
