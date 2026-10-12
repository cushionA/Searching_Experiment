from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import sku_choice_refids_protocol_v1 as protocol  # noqa: E402
import sku_directional_choice_nli_v1 as nli  # noqa: E402


def span(raw, start, quote):
    return {"raw_file": "synthetic.json", "sha256": "a" * 64,
            "locator": {"kind": "json_leaf", "json_path": "$.x"},
            "start": start, "end": start + len(quote), "quote": quote}


class ReferenceIdTransportTests(unittest.TestCase):
    def setUp(self):
        hspan = span("red / blue", 0, "red")
        blue_span = span("red / blue", 6, "blue")
        label = span("Color", 0, "Color")
        value = span("Red", 0, "Red")
        fact_id = nli._span_id(value)
        self.pair = {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic",
            "protocol": nli.PROTOCOL, "preparation_status": "ready",
            "evaluation_instruction": "Assess each complete option independently.",
            "candidate_vector_order": "exact original raw family order", "scope_status": "model_assessed_unproved",
            "option_values": ["red", "blue"], "candidate_vector": [
                {"option_index": 0, "whole_option": "red", "option_evidence_id": nli._span_id(hspan)},
                {"option_index": 1, "whole_option": "blue", "option_evidence_id": nli._span_id(blue_span)}]}
        self.context = {"direction": "rakuten_to_au", "target_document_sha256": "doc-target",
            "hypothesis": {"axis_name": "Color", "value": "red",
                "axis_name_span": label, "value_span": hspan,
                "family_values": ["red", "blue"],
                "family_members": [{"index": 0, "whole_option": "red", "evidence_id": nli._span_id(hspan), "span": hspan},
                                   {"index": 1, "whole_option": "blue", "evidence_id": nli._span_id(blue_span), "span": blue_span}],
                "identity": {"task_id": "hidden", "case_id": "hidden", "source_sku_key": "hidden"}},
            "target_selected_facts": [{"fact_id": fact_id, "axis_name": "Color", "value": "Red",
                "axis_name_span": label, "value_span": value}], "source_body_refs": []}
        self.source_docs = {}

    def _decision(self, i, state="support", refs=None, **updates):
        result = {"option_index": i, "whole_value_assessed": True,
            "state": state, "scope_assessment": "same_condition",
            "semantic_relation": state if state != "unknown" else "uncertain",
            "scope_and_coordinate_explanation": "The complete option is assessed against the paired condition.",
            "option_relation_explanation": "The source facts describe this whole option.",
            "exclusivity_assessment": "model_assessed", "target_fact_indices": [0] if refs is None else refs}
        result.update(updates)
        return result

    def test_payload_adds_indices_without_changing_semantic_source_fields_or_hidden_identity(self):
        pair = copy.deepcopy(self.pair)
        context = copy.deepcopy(self.context)
        original_context = copy.deepcopy(context)
        source_docs = {}
        payload = protocol.build_refid_model_payload(pair, context, source_docs)
        self.assertEqual(payload["context"]["hypothesis"]["value"], "red")
        self.assertNotIn("identity", payload["context"]["hypothesis"])
        self.assertEqual(payload["context"]["target_selected_facts"][0]["value"], "Red")
        self.assertEqual(payload["context"]["target_selected_facts"][0]["target_fact_index"], 0)
        self.assertEqual([x["option_index"] for x in payload["candidate_vector"]], [0, 1])
        self.assertEqual(pair, self.pair)
        self.assertEqual(context, original_context)
        self.assertIn("target_fact_indices", payload["model_output_schema"]["decisions"][0])

    def test_renderer_projects_only_model_selected_semantics_and_binds_exact_spans(self):
        output = {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic",
                  "decisions": [self._decision(0), self._decision(1, "unknown", [])]}
        response = protocol.render_v2_response(self.pair, self.context, self.source_docs, output)
        self.assertEqual(response["protocol"], nli.PROTOCOL)
        self.assertEqual([j["whole_option"] for j in response["judgments"]], ["red", "blue"])
        self.assertEqual(response["judgments"][0]["target_fact_ids"], [self.context["target_selected_facts"][0]["fact_id"]])
        self.assertEqual(response["judgments"][0]["option_evidence_id"], nli._span_id(self.context["hypothesis"]["family_members"][0]["span"]))
        self.assertEqual(response["judgments"][0]["citation_ids"], ["c0000", "c0001", "c0002"])
        self.assertEqual(len({c["citation_id"] for c in response["citation_table"]}),
                         len(response["citation_table"]))
        self.assertEqual(response["sourceproof_certified"], False)
        self.assertEqual(response["production_adoption_allowed"], False)

    def test_missing_duplicate_wrong_index_or_wrong_pair_fail_closed(self):
        cases = [
            {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic", "decisions": [self._decision(0)]},
            {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic", "decisions": [self._decision(0), self._decision(0)]},
            {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic", "decisions": [self._decision(0), {**self._decision(1), "target_fact_indices": [99]}]},
            {"pair_id": "other", "cache_key": "cache-synthetic", "decisions": [self._decision(0), self._decision(1)]},
            {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic", "decisions": [self._decision(1), self._decision(0)]},
        ]
        for output in cases:
            with self.subTest(output=output):
                response = protocol.render_v2_response(self.pair, self.context, self.source_docs, output)
                expected = ["unknown", "unknown"] if (output.get("pair_id") != "pair-synthetic"
                    or len(output["decisions"]) != 2
                    or [d["option_index"] for d in output["decisions"]] != [0, 1]) else ["support", "unknown"]
                self.assertEqual([j["state"] for j in response["judgments"]], expected)

    def test_malformed_body_ref_does_not_create_an_unbound_citation_or_semantic_claim(self):
        bad = self._decision(0)
        bad["body_block_refs"] = [{"body_index": 0, "block_index": 0,
                                    "quote": "invented", "start": 0, "end": 8}]
        output = {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic",
                  "decisions": [bad, self._decision(1, "unknown", [])]}
        response = protocol.render_v2_response(self.pair, self.context, self.source_docs, output)
        self.assertEqual(response["judgments"][0]["state"], "unknown")
        self.assertFalse(any(c["evidence_id"].startswith("body-block:") for c in response["citation_table"]))

    def test_missing_scope_exclusivity_or_decisive_fact_selection_abstains(self):
        missing_scope = self._decision(0); missing_scope.pop("scope_assessment")
        missing_exclusivity = self._decision(1); missing_exclusivity.pop("exclusivity_assessment")
        missing_whole_assessment = self._decision(0); missing_whole_assessment.pop("whole_value_assessed")
        no_target_fact = self._decision(0, refs=[])
        output = {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic",
                  "decisions": [missing_scope, missing_exclusivity]}
        response = protocol.render_v2_response(self.pair, self.context, self.source_docs, output)
        self.assertEqual([j["state"] for j in response["judgments"]], ["unknown", "unknown"])
        output["decisions"] = [self._decision(0, "explicit_conflict", [0]), self._decision(1, "unknown", [])]
        response = protocol.render_v2_response(self.pair, self.context, self.source_docs, output)
        self.assertEqual(response["judgments"][0]["state"], "unknown")
        output["decisions"] = [missing_whole_assessment, self._decision(1, "unknown", [])]
        response = protocol.render_v2_response(self.pair, self.context, self.source_docs, output)
        self.assertEqual(response["judgments"][0]["state"], "unknown")
        output["decisions"] = [no_target_fact, self._decision(1, "unknown", [])]
        response = protocol.render_v2_response(self.pair, self.context, self.source_docs, output)
        self.assertEqual(response["judgments"][0]["state"], "unknown")

    def test_each_model_selected_target_fact_cites_axis_and_value_as_separate_spans(self):
        second_label = span("Size", 0, "Size")
        second_value = span("Large", 0, "Large")
        context = copy.deepcopy(self.context)
        context["target_selected_facts"].append({"fact_id": nli._span_id(second_value), "axis_name": "Size", "value": "Large",
            "axis_name_span": second_label, "value_span": second_value})
        output = {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic",
                  "decisions": [self._decision(0, refs=[0, 1]), self._decision(1, "unknown", [])]}
        response = protocol.render_v2_response(self.pair, context, self.source_docs, output)
        first = response["judgments"][0]
        citations = {row["evidence_id"]: row for row in response["citation_table"]}
        self.assertEqual(first["target_fact_ids"], [f["fact_id"] for f in context["target_selected_facts"]])
        self.assertEqual(len(first["citation_ids"]), 5)
        self.assertEqual(citations[nli._span_id(second_label)]["quote"], "Size")
        self.assertEqual(citations[nli._span_id(second_value)]["quote"], "Large")

    def test_malformed_prepared_candidate_vector_abstains_for_every_option(self):
        output = {"pair_id": "pair-synthetic", "cache_key": "cache-synthetic",
                  "decisions": [self._decision(0), self._decision(1)]}
        for candidate in (
            [self.pair["candidate_vector"][0]],
            [self.pair["candidate_vector"][1], self.pair["candidate_vector"][0]],
            [{**self.pair["candidate_vector"][0], "option_evidence_id": "wrong"},
             self.pair["candidate_vector"][1]],
        ):
            pair = copy.deepcopy(self.pair)
            pair["candidate_vector"] = candidate
            response = protocol.render_v2_response(pair, self.context, self.source_docs, output)
            self.assertEqual([j["state"] for j in response["judgments"]], ["unknown", "unknown"])
            self.assertEqual([j["whole_option"] for j in response["judgments"]], ["red", "blue"])

    def test_loader_rejects_unpinned_split_or_independence_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [
                {"schema_version": nli.PROTOCOL,
                 "dataset": "sku-next-dev-coordinate-refids-20261013-v1",
                 "independent_test_set": False},
                {"schema_version": nli.PROTOCOL,
                 "dataset": "sku-next-dev-coordinate-refids-20261013-v1",
                 "selected_split": "dev"},
                {"schema_version": nli.PROTOCOL,
                 "dataset": "sku-next-dev-coordinate-refids-20261013-v1",
                 "selected_split": "test", "independent_test_set": False},
            ]
            for freeze in cases:
                Path(tmp, "freeze.json").write_text(__import__("json").dumps(freeze), encoding="utf-8")
                with self.subTest(freeze=freeze), self.assertRaises(ValueError):
                    protocol.load_source_only_inputs(Path(tmp))


if __name__ == "__main__":
    unittest.main()
