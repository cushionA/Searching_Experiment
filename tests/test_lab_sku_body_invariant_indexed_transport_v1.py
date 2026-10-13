import copy
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import sku_body_invariant_choice_v3 as BODY
import sku_body_invariant_indexed_transport_v1 as INDEXED


class IndexedTransportTests(unittest.TestCase):
    def setUp(self):
        self.pair = {"pair_id": "p-1", "cache_key": "c-1", "option_values": ["red", "blue"],
            "candidate_vector": [{"option_evidence_id": "e-red"}, {"option_evidence_id": "e-blue"}]}
        self.payload = {"pair_id": "p-1", "cache_key": "c-1", "candidate_vector": self.pair["candidate_vector"],
            "context": {"target_document_sha256": "a" * 64,
            "target_selected_facts": [], "target_native_sku_inventory": {
                "status": "verified_native_axes", "all_option_states": []},
            "eligible_target_body": {"document_sha256": "a" * 64, "status": "eligible_blocks", "blocks": []}}}

    def decision(self, index, **overrides):
        value = {"option_index": index, "whole_value_assessed": True, "state": "unknown",
            "scope_assessment": "uncertain", "semantic_relation": "uncertain",
            "scope_and_coordinate_explanation": "The complete option was assessed.",
            "option_relation_explanation": "The supplied evidence does not establish a relation.",
            "exclusivity_assessment": "uncertain", "exclusivity_explanation": "Uncertain.",
            "grounding_route": "none", "target_fact_indices": [], "body_invariant": None,
            "body_quote_refs": [], "body_condition_bindings": [],
            "direct_contradiction_explanation": ""}
        value.update(overrides)
        return value

    def raw(self, decisions):
        return {"protocol": INDEXED.PROTOCOL, "pair_ref": 3, "decisions": decisions}

    def test_semantics_pass_through_without_renderer_fill(self):
        decisions = [self.decision(i, option_relation_explanation=f"Authored explanation {i}.")
                     for i in range(2)]
        before = copy.deepcopy(decisions)
        result = INDEXED.render_indexed_response(self.raw(decisions), self.pair, self.payload, 3)
        for i in range(2):
            for key, value in before[i].items():
                if key != "option_index":
                    self.assertEqual(result["decisions"][i][key], value)
        self.assertEqual([d["option_evidence_id"] for d in result["decisions"]], ["e-red", "e-blue"])
        self.assertNotIn("pair_ref", result)

    def test_rejects_incomplete_duplicate_reordered_boolean_and_foreign_fields(self):
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([self.decision(0)]), self.pair, self.payload, 3)
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([self.decision(0), self.decision(0)]), self.pair, self.payload, 3)
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([self.decision(True), self.decision(1)]), self.pair, self.payload, 3)
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([self.decision(0, guessed_state="support"), self.decision(1)]), self.pair, self.payload, 3)

    def test_frozen_v3_validator_rejects_missing_model_semantics(self):
        d = self.decision(0)
        del d["scope_and_coordinate_explanation"]
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([d, self.decision(1)]), self.pair, self.payload, 3)

    def test_exact_body_quote_and_complete_family_audit_are_required(self):
        first = "Introductory text."
        text = "The product is stainless steel."
        second = "Prefix material: " + text
        blocks = [{"block_id": "b0", "source_sha256": hashlib.sha256(b"raw0").hexdigest(),
            "source_alias": "body-source:aaaaaaaaaaaaaaaa:0", "support_text": first},
            {"block_id": "b1", "source_sha256": hashlib.sha256(b"raw1").hexdigest(),
            "source_alias": "body-source:aaaaaaaaaaaaaaaa:1", "support_text": second}]
        self.payload["context"]["eligible_target_body"]["blocks"] = blocks
        state = {"axis_name": "size", "value": "large", "member_evidence_ids": ["e-large"]}
        self.payload["context"]["target_native_sku_inventory"]["all_option_states"] = [state]
        inv = {"attribute": "material", "attribute_quote": "stainless steel", "value": "stainless steel",
            "value_quote": "stainless steel", "role": "product_attribute", "role_explanation": "Product attribute.",
            "subject_resolution": "target_product", "subject_scope": "The product.",
            "target_identity_explanation": "Target product.", "quantifier_scope": "All product states.",
            "invariance_basis": "explicit_universal_statement", "family_audit": [
                {"state_index": 0, "applicability": "covered", "explanation": "Covered."}],
            "semantic_explanation": "Attribute applies.", "scope_evidence_quote": "The product is stainless steel.",
            "positive_scope_explanation": "Positive product scope.", "product_scope_assessment": "target_product_level",
            "whole_attribute_assessed": True}
        quote = "stainless steel"
        quote_start = len("Prefix material: ")
        quote_ref = {"body_container_index": 0, "block_index": 1, "quote": text,
                     "start": quote_start, "end": quote_start + len(text)}
        d0 = self.decision(0, state="support", scope_assessment="same_condition", semantic_relation="support",
            grounding_route="body_invariant", body_invariant=inv, body_quote_refs=[quote_ref])
        authored_invariant = copy.deepcopy(inv)
        d1 = self.decision(1)
        result = INDEXED.render_indexed_response(self.raw([d0, d1]), self.pair, self.payload, 3)
        audit = result["decisions"][0]["body_invariant"]["family_audit"][0]
        self.assertEqual(audit["whole_state"], state)
        self.assertEqual(audit["member_evidence_ids"], ["e-large"])
        rendered_invariant = result["decisions"][0]["body_invariant"]
        for key, value in authored_invariant.items():
            if key != "family_audit":
                self.assertEqual(rendered_invariant[key], value)
        self.assertEqual(rendered_invariant["family_audit"][0]["applicability"],
                         authored_invariant["family_audit"][0]["applicability"])
        self.assertEqual(rendered_invariant["family_audit"][0]["explanation"],
                         authored_invariant["family_audit"][0]["explanation"])
        self.assertEqual(result["decisions"][0]["body_quote_refs"][0]["body_index"], 1)
        self.assertEqual(result["decisions"][0]["body_quote_refs"][0]["source_alias"], blocks[1]["source_alias"])
        self.assertEqual(result["decisions"][0]["body_quote_refs"][0]["start"], quote_start)
        self.assertIn(quote, result["decisions"][0]["body_quote_refs"][0]["quote"])
        bad = copy.deepcopy(d0)
        bad["body_quote_refs"][0]["quote"] = "forged quote"
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([bad, d1]), self.pair, self.payload, 3)
        bad_payload = copy.deepcopy(self.payload)
        bad_payload["context"]["eligible_target_body"]["blocks"][1]["source_sha256"] = "g" * 64
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([d0, d1]), self.pair, bad_payload, 3)
        bad = copy.deepcopy(d0)
        bad["body_invariant"]["family_audit"] = []
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([bad, d1]), self.pair, self.payload, 3)

    def test_foreign_metadata_and_mismatched_payload_identity_are_rejected(self):
        text = "The product is stainless steel."
        block = {"block_id": "b0", "source_sha256": hashlib.sha256(b"raw").hexdigest(),
            "source_alias": "body-source:aaaaaaaaaaaaaaaa:0", "support_text": text}
        self.payload["context"]["eligible_target_body"]["blocks"] = [block]
        state = {"member_evidence_ids": ["e1"]}
        self.payload["context"]["target_native_sku_inventory"]["all_option_states"] = [state]
        inv = {"attribute": "material", "attribute_quote": "steel", "value": "steel", "value_quote": "steel",
            "role": "product_attribute", "role_explanation": "Attribute.", "subject_resolution": "target_product",
            "subject_scope": "Product.", "target_identity_explanation": "Target.", "quantifier_scope": "All.",
            "invariance_basis": "explicit_universal_statement", "family_audit": [
                {"state_index": 0, "applicability": "covered", "explanation": "All."}],
            "semantic_explanation": "Applies.", "scope_evidence_quote": text,
            "positive_scope_explanation": "Positive scope.", "product_scope_assessment": "target_product_level",
            "whole_attribute_assessed": True}
        quote_ref = {"body_container_index": 0, "block_index": 0, "quote": text, "start": 0, "end": len(text)}
        d = self.decision(0, body_invariant=inv, body_quote_refs=[quote_ref])
        d["body_invariant"]["target_document_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([d, self.decision(1)]), self.pair, self.payload, 3)
        d["body_invariant"].pop("target_document_sha256")
        d["body_invariant"]["unexpected_source_claim"] = "forged"
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([d, self.decision(1)]), self.pair, self.payload, 3)
        bad_payload = copy.deepcopy(self.payload)
        bad_payload["pair_id"] = "other"
        with self.assertRaises(ValueError):
            INDEXED.render_indexed_response(self.raw([self.decision(0), self.decision(1)]), self.pair, bad_payload, 3)


if __name__ == "__main__":
    unittest.main()
