import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("body_indexed_packetizer", ROOT / "experiments/sku-matching/prepare_body_indexed_packets_v1.py")
PACKETS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKETS)


class PacketPersistenceTests(unittest.TestCase):
    def test_parser_rejects_duplicate_keys_and_trailing_commas(self):
        for source in (b'{"pair_ref":1,"pair_ref":2}', b'{"pair_ref":1,}'):
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                PACKETS.strict_loads(source)

    def test_worker_container_index_maps_to_canonical_flat_body_index(self):
        pair = {"pair_id": "p", "cache_key": "c", "option_values": ["red"],
                "candidate_vector": [{"option_evidence_id": "e-red"}]}
        context = {"target_document_sha256": "a" * 64, "target_selected_facts": [],
            "target_native_sku_inventory": {"status": "verified_native_axes", "all_option_states": [
                {"member_evidence_ids": ["e-red"]}]},
            "eligible_target_body": {"document_sha256": "a" * 64, "status": "eligible_blocks",
                "blocks": [{"block_id": "b0", "source_sha256": "b" * 64,
                    "source_alias": "alias0", "support_text": "Other."},
                    {"block_id": "b1", "source_sha256": "c" * 64,
                    "source_alias": "alias1", "support_text": "Prefix: The product is steel."}]}}
        payload = {"pair_id": "p", "cache_key": "c", "candidate_vector": pair["candidate_vector"],
                   "context": context}
        quote = "The product is steel."
        start = len("Prefix: ")
        invariant = {"attribute": "material", "attribute_quote": "steel", "value": "steel",
            "value_quote": "steel", "role": "product_attribute", "role_explanation": "Product attribute.",
            "subject_resolution": "target_product", "subject_scope": "The product.",
            "target_identity_explanation": "The sentence identifies the product.", "quantifier_scope": "All product states.",
            "invariance_basis": "explicit_universal_statement", "family_audit": [
                {"state_index": 0, "applicability": "covered", "explanation": "Covered."}],
            "semantic_explanation": "The attribute applies.", "scope_evidence_quote": quote,
            "positive_scope_explanation": "The statement positively describes the product.",
            "product_scope_assessment": "target_product_level", "whole_attribute_assessed": True}
        decision = {"option_index": 0, "whole_value_assessed": True, "state": "support",
            "scope_assessment": "same_condition", "semantic_relation": "support",
            "scope_and_coordinate_explanation": "The condition aligns.",
            "option_relation_explanation": "The product attribute supports this value.",
            "exclusivity_assessment": "uncertain", "exclusivity_explanation": "No conflict assessed.",
            "grounding_route": "body_invariant", "target_fact_indices": [], "body_invariant": invariant,
            "body_quote_refs": [{"body_container_index": 0, "block_index": 1, "quote": quote,
                "start": start, "end": start + len(quote)}], "body_condition_bindings": [],
            "direct_contradiction_explanation": ""}
        raw = {"protocol": PACKETS.INDEXED.PROTOCOL, "pair_ref": 7, "decisions": [decision]}
        rendered = PACKETS.INDEXED.render_indexed_response(raw, pair, payload, 7)
        self.assertEqual(rendered["decisions"][0]["body_quote_refs"][0]["body_index"], 1)
        self.assertEqual(rendered["decisions"][0]["body_quote_refs"][0]["start"], start)
        legacy = json.loads(json.dumps(raw))
        legacy["decisions"][0]["body_quote_refs"][0]["body_index"] = legacy["decisions"][0]["body_quote_refs"][0].pop("body_container_index")
        with self.assertRaises(ValueError):
            PACKETS.INDEXED.render_indexed_response(legacy, pair, payload, 7)

    def test_persistence_preserves_bad_firstpass_and_does_not_retry(self):
        pair_a = {"pair_id": "p-a", "cache_key": "c-a", "option_values": ["red"],
                  "candidate_vector": [{"option_evidence_id": "e-red"}]}
        pair_b = {"pair_id": "p-b", "cache_key": "c-b", "option_values": ["blue"],
                  "candidate_vector": [{"option_evidence_id": "e-blue"}]}
        def payload(pair):
            return {"pair_id": pair["pair_id"], "cache_key": pair["cache_key"],
                "candidate_vector": pair["candidate_vector"], "context": {
                    "target_document_sha256": "a" * 64, "target_selected_facts": [],
                    "target_native_sku_inventory": {"status": "verified_native_axes", "all_option_states": []},
                    "eligible_target_body": {"document_sha256": "a" * 64,
                        "status": "no_eligible_blocks", "blocks": []}}}
        pairs = {"p-a": pair_a, "p-b": pair_b}
        payloads = {key: payload(pair) for key, pair in pairs.items()}
        refs = {"p-a": {"pair_ref": 5, "pair_id": "p-a"}, "p-b": {"pair_ref": 9, "pair_id": "p-b"}}
        source = {"pairs": pairs, "payloads": payloads, "refs": refs,
                  "dispatch_ids": ["p-a", "p-b"], "by_number": {5: refs["p-a"], 9: refs["p-b"]}}
        index_dir = ROOT / ".lab-output" / "synthetic-body-indexed-packet-index"
        with tempfile.TemporaryDirectory(dir=ROOT / ".lab-output") as tmp:
            root = Path(tmp)
            index_dir = root / "index"
            index_dir.mkdir()
            (index_dir / "packet-index.json").write_bytes(b'{"synthetic":true}\n')
            (index_dir / "packet-01.json").write_bytes(json.dumps({"pairs": [
                {"pair_ref": 5}, {"pair_ref": 9}]}).encode() + b"\n")
            raw_path = root / "authored.jsonl"
            valid_decision = {"option_index": 0, "whole_value_assessed": True, "state": "unknown",
                "scope_assessment": "uncertain", "semantic_relation": "uncertain",
                "scope_and_coordinate_explanation": "Assessed whole option.",
                "option_relation_explanation": "No evidence resolves it.",
                "exclusivity_assessment": "uncertain", "exclusivity_explanation": "Uncertain.",
                "grounding_route": "none", "target_fact_indices": [], "body_invariant": None,
                "body_quote_refs": [], "body_condition_bindings": [], "direct_contradiction_explanation": ""}
            good = {"protocol": PACKETS.INDEXED.PROTOCOL, "pair_ref": 5, "decisions": [valid_decision]}
            bad = {"protocol": PACKETS.INDEXED.PROTOCOL, "pair_ref": 9,
                   "decisions": [{**valid_decision, "option_index": 0, "semantic_guess": "support"}]}
            raw_bytes = (json.dumps(good, ensure_ascii=False, separators=(", ", ": ")).encode() + b"\n"
                         + json.dumps(bad, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
            raw_path.write_bytes(raw_bytes)
            original_check, original_sha = PACKETS._verify_packet_index, PACKETS.sha_file
            try:
                synthetic_index = {"dispatch_pair_count": 2,
                    "packets": [{"file": "packet-01.json", "pair_refs": [5, 9]}]}
                PACKETS._verify_packet_index = lambda _path: (synthetic_index, source)
                draft_receipt = PACKETS.validate_packet_draft(raw_path, index_dir, "packet-01.json")
                self.assertEqual(draft_receipt["assigned_pair_refs"], [5, 9])
                self.assertEqual(draft_receipt["raw_sha256"], PACKETS.sha_bytes(raw_bytes))
                self.assertEqual(draft_receipt["responses"][1]["status"], "invalid_unknown_firstpass_preserved")
                reversed_path = root / "reversed.jsonl"
                reversed_path.write_bytes(raw_bytes.splitlines(keepends=True)[1] + raw_bytes.splitlines(keepends=True)[0])
                with self.assertRaises(ValueError):
                    PACKETS.validate_packet_draft(reversed_path, index_dir, "packet-01.json")
                receipt = PACKETS.persist(raw_path, index_dir, root / "persisted")
            finally:
                PACKETS._verify_packet_index, PACKETS.sha_file = original_check, original_sha
            out = root / "persisted"
            self.assertEqual((out / "raw-indexed-responses.jsonl").read_bytes(), raw_bytes)
            self.assertEqual(receipt["structurally_rendered_count"], 1)
            self.assertEqual(receipt["invalid_unknown_count"], 1)
            statuses = [json.loads(x) for x in (out / "firstpass-status.jsonl").read_text().splitlines()]
            self.assertEqual([x["semantic_retry_performed"] for x in statuses], [False, False])
            self.assertEqual(statuses[1]["status"], "invalid_unknown_firstpass_preserved")
            with self.assertRaises(FileExistsError):
                PACKETS.persist(raw_path, index_dir, out)


if __name__ == "__main__":
    unittest.main()
