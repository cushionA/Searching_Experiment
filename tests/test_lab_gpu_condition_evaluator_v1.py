import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "experiments/sku-matching/evaluate_gpu_condition_tasks_v1.py"
SPEC = importlib.util.spec_from_file_location("condition_evaluator_v1", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def scenario():
    req = {"axis_name_raw": "レース", "selected_value_raw": "あり",
           "atom_kind": "component_presence", "atom_value": {"component": "lace", "value": True}}
    packet = {"packet_id": "case:row0:r:00", "case_id": "case", "au_row_key": "row0",
              "condition": {"side": "rakuten", **req}, "evidence": [{"quote": "レース付き"}]}
    host = {"case_id": "case", "au_rows": [{"row_key": "row0"}, {"row_key": "row1"}],
            "focus_row_key": "row0", "required_conditions": [req], "blocking_issues": [],
            "row_condition_statuses": [
                {"row_key": "row0", "condition_status": [{"condition_index": 0, "status": "unknown_or_missing"}],
                 "au_only_or_unmatched_atoms": []},
                {"row_key": "row1", "condition_status": [{"condition_index": 0, "status": "typed_conflict"}],
                 "au_only_or_unmatched_atoms": []}]}
    return host, packet


def prediction(packet):
    perms = []
    for rotation in range(3):
        mapping = {r: str((i + rotation) % 3) for i, r in enumerate(evaluation.RELATIONS)}
        digit = mapping["support"]
        perms.append({"status": "ok", "relation_to_digit": mapping, "restricted_argmax_digit": digit,
                      "permutation_relation": "support", "raw_digit_logits": {d: float(d == digit) for d in "012"},
                      "full_vocab_argmax_is_legal_digit": True})
    record = {**{k: packet[k] for k in ("packet_id", "case_id", "au_row_key")},
              "input_sha256": "input", "packet_sha256": evaluation.canonical_sha(packet),
              "model_revision": evaluation.MODEL_REVISION, "evidence_refs": packet["evidence"],
              "status": "ok", "permutations": perms, "aggregate_relation": "support"}
    record["record_sha256"] = evaluation.canonical_sha(record)
    return record


class ConditionHostEvaluatorTests(unittest.TestCase):
    def test_lace_conflict_cannot_match_fixed_au_url(self):
        host, packet = scenario()
        result = evaluation.aggregate_case(host, [packet], {packet["packet_id"]: "conflict"})
        self.assertEqual(result["decision"], "unmatched")
        self.assertIsNone(result["au_row_key"])

    def test_unresolved_other_row_prevents_unique_match(self):
        host, packet = scenario()
        host["row_condition_statuses"][1]["condition_status"][0]["status"] = "unknown_or_missing"
        result = evaluation.aggregate_case(host, [packet], {packet["packet_id"]: "support"})
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], "other_full_pool_rows_unresolved")

    def test_missing_au_only_fabric_is_not_dropped(self):
        host, packet = scenario()
        host["row_condition_statuses"][0]["au_only_or_unmatched_atoms"] = [
            {"axis_name_raw": "カラー", "atom_kind": "fabric", "atom_value": "パイル"}]
        result = evaluation.aggregate_case(host, [packet], {packet["packet_id"]: "support"})
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], "missing_mandatory_au_only_packets")

    def test_quote_omission_forces_review(self):
        host, packet = scenario()
        host["blocking_issues"] = [{"reason": "quote_overflow"}]
        self.assertEqual(evaluation.aggregate_case(host, [packet], {packet["packet_id"]: "support"})["decision"], "review")

    def test_valid_permutations_and_exact_binding(self):
        _, packet = scenario()
        self.assertEqual(evaluation.proposal_relation(packet, prediction(packet), "input"), ("support", []))

    def test_stale_row_even_with_valid_record_hash_is_rejected(self):
        _, packet = scenario()
        record = prediction(packet)
        record["au_row_key"] = "another-url-row"
        record["record_sha256"] = evaluation.canonical_sha({k: v for k, v in record.items() if k != "record_sha256"})
        relation, errors = evaluation.proposal_relation(packet, record, "input")
        self.assertEqual(relation, "unknown")
        self.assertIn("binding_mismatch:au_row_key", errors)

    def test_restricted_softmax_is_not_enough_when_model_wants_another_token(self):
        _, packet = scenario()
        record = prediction(packet)
        record["permutations"][1]["full_vocab_argmax_is_legal_digit"] = False
        record["record_sha256"] = evaluation.canonical_sha({k: v for k, v in record.items() if k != "record_sha256"})
        self.assertEqual(evaluation.proposal_relation(packet, record, "input")[0], "unknown")

    def test_answer_symbol_bias_is_unknown(self):
        _, packet = scenario()
        record = prediction(packet)
        item = record["permutations"][2]
        item["restricted_argmax_digit"] = "0"
        item["raw_digit_logits"] = {"0": 2.0, "1": 1.0, "2": 0.0}
        item["permutation_relation"] = "conflict"
        record["aggregate_relation"] = "unknown"
        record["record_sha256"] = evaluation.canonical_sha({k: v for k, v in record.items() if k != "record_sha256"})
        self.assertEqual(evaluation.proposal_relation(packet, record, "input")[0], "unknown")


if __name__ == "__main__":
    unittest.main()
