import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "experiments/sku-matching/evaluate_final_full_sku_gate_v1.py"
spec = importlib.util.spec_from_file_location("final_full_sku_gate", MODULE_PATH)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class FinalFullSkuGateTests(unittest.TestCase):
    def test_baseline_requires_every_exact_source_binding(self):
        req = {"request_id": "r1", "request_sha256": "sha", "selected_value": "red",
               "direction": "rakuten_to_au", "option_values": ["red", "blue"],
               "source_task_bindings": [{"task_id": "t1"}]}
        pred = {"request_id": "r1", "request_sha256": "sha", "selected_value": "red",
                "state": "support", "winning_value": "red"}
        self.assertEqual(gate.enrich_baseline([pred], {"r1": req})["r1"]["source_task_bindings"],
                         [{"task_id": "t1"}])
        with self.assertRaisesRegex(ValueError, "no original source task binding"):
            gate.enrich_baseline([pred], {"r1": {**req, "source_task_bindings": []}})

    def test_missing_and_extra_fanout_requests_fail_exact_coverage(self):
        req = {"r1": {"request_id": "r1", "request_sha256": "s1", "selected_value": "x",
                       "direction": "rakuten_to_au", "option_values": ["x"],
                       "source_task_bindings": [{"task_id": "t1"}]}}
        pred = {"request_id": "r1", "request_sha256": "s1", "selected_value": "x",
                "state": "unknown", "winning_value": None}
        with self.assertRaisesRegex(ValueError, "coverage differs"):
            gate.enrich_baseline([pred], {**req, "r2": {**req["r1"], "request_id": "r2"}})

    def test_raw_support_and_conflict_require_full_axis_quote_and_verified_pairs(self):
        request = {"request_id": "r", "selected_value": "red", "target_document_id": "d",
                   "target_selected_axes_text": "Color: red", "source_task_bindings": [{"task_id": "t"}]}
        baseline = {"r": {"state": "unknown", "winning_value": None}}
        status = [{"request_id": "r", "eligible": True, "status": "support", "proof_ids": ["t"]}]
        pair = {"forward_task_id": "t", "reverse_task_id": "u", "status": "support",
                "raw_span_receipts": {"a": [1]}}
        with patch.object(gate.GATE, "verify_quote", return_value=True):
            overlay, ids = gate.apply_literal_proofs(baseline, {"r": request}, {"d": {}}, status,
                                                      [pair], "m", "p")
        self.assertEqual(ids, {"r"})
        self.assertEqual(overlay["r"]["winning_value"], "red")
        self.assertEqual(len(overlay["r"]["literal_family_proof"]["task_proof_records"]), 1)
        with patch.object(gate.GATE, "verify_quote", return_value=False), self.assertRaises(ValueError):
            gate.apply_literal_proofs(baseline, {"r": request}, {"d": {}}, status,
                                      [pair], "m", "p")

    def test_unknown_or_uncovered_raw_pair_cannot_protect_request(self):
        baseline = {"r": {"state": "unknown", "winning_value": None}}
        request = {"request_id": "r", "selected_value": "red", "target_document_id": "d",
                   "target_selected_axes_text": "Color: red"}
        status = [{"request_id": "r", "eligible": True, "status": "conflict", "proof_ids": ["t"]}]
        unknown_pair = {"forward_task_id": "t", "reverse_task_id": "u", "status": "unknown"}
        with patch.object(gate.GATE, "verify_quote", return_value=True), self.assertRaisesRegex(
                ValueError, "no verified literal proof records"):
            gate.apply_literal_proofs(baseline, {"r": request}, {"d": {}}, status,
                                      [unknown_pair], "m", "p")

    def test_verified_raw_conflict_has_no_model_alternative_winner(self):
        request = {"request_id": "r", "selected_value": "red", "target_document_id": "d",
                   "target_selected_axes_text": "Color: blue"}
        baseline = {"r": {"state": "support", "winning_value": "blue"}}
        status = [{"request_id": "r", "eligible": True, "status": "conflict", "proof_ids": ["t"]}]
        pair = {"forward_task_id": "t", "reverse_task_id": "u", "status": "conflict",
                "raw_span_receipts": {"a": [1]}}
        with patch.object(gate.GATE, "verify_quote", return_value=True):
            overlay, ids = gate.apply_literal_proofs(baseline, {"r": request}, {"d": {}}, status,
                                                      [pair], "m", "p")
        self.assertEqual(ids, {"r"})
        self.assertEqual(overlay["r"]["state"], "conflict")
        self.assertIsNone(overlay["r"]["winning_value"])

    def test_exact_pair_scoped_cache_fanout_preserves_complete_bindings(self):
        request = {"direction": "au_to_rakuten", "axis_name": "Size",
                   "option_values": ["S", "M"], "target_document_sha256": "docsha",
                   "target_selected_axes": [{"axis_name": "Color", "value": "Blue"}],
                   "target_selected_axes_text": "Color: Blue", "target_document_id": "doc"}
        key = {"direction": request["direction"], "axis_name": request["axis_name"],
               "option_values": request["option_values"],
               "target_document_sha256": request["target_document_sha256"],
               "target_selected_axes": request["target_selected_axes"]}
        key_sha = gate.hashlib.sha256(gate.json.dumps(
            key, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        unit = "final-exact-context:" + key_sha
        packet = {"unit_id": unit, "kind": "paired", "axis_name": "Size",
                  "option_values": ["S", "M"], "target_document_id": "doc",
                  "target_selected_axes_text": "Color: Blue",
                  "blocks": [{"block_id": "b", "model_passage": "source text", "quote_refs": []}]}
        packet["packet_sha256"] = gate.CONTEXT._canonical_sha(packet)
        payload = gate.CONTEXT.model_payload(packet)
        fanout = {"context_unit_id": unit, "cache_key_sha256": key_sha,
                  "request_ids": ["r1", "r2"], "selected_task_ids": ["t1", "t2"]}
        result = gate.validate_source_fanout([packet], [payload], [fanout],
                                             {"r1": request, "r2": dict(request)})
        self.assertEqual(set(result[2]), {unit})
        with self.assertRaisesRegex(ValueError, "cache key"):
            gate.validate_source_fanout([packet], [payload], [{**fanout, "cache_key_sha256": "other"}],
                                        {"r1": request, "r2": dict(request)})
        alternate = {**request, "target_document_sha256": "other-doc"}
        alternate_key = {**key, "target_document_sha256": "other-doc"}
        alternate_sha = gate.hashlib.sha256(gate.json.dumps(
            alternate_key, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        alternate_unit = "final-exact-context:" + alternate_sha
        alternate_packet = {**packet, "unit_id": alternate_unit, "target_document_id": "other"}
        unsigned = {k: v for k, v in alternate_packet.items() if k != "packet_sha256"}
        alternate_packet["packet_sha256"] = gate.CONTEXT._canonical_sha(unsigned)
        alternate_payload = gate.CONTEXT.model_payload(alternate_packet)
        with self.assertRaisesRegex(ValueError, "overlap"):
            gate.validate_source_fanout([packet, alternate_packet], [payload, alternate_payload],
                                        [fanout, {"context_unit_id": alternate_unit,
                                                  "cache_key_sha256": alternate_sha,
                                                  "request_ids": ["r1"], "selected_task_ids": ["t3"]}],
                                        {"r1": request, "r2": dict(request)})


if __name__ == "__main__":
    unittest.main()
