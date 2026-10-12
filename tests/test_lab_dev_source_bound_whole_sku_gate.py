from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "source_bound_dev_gate", ROOT / "experiments/sku-matching/evaluate_dev_source_bound_whole_sku_gate_v1.py")
gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(gate)


class SourceBoundWholeSkuGateTests(unittest.TestCase):
    def test_unanimous_literal_proof_overlays_baseline_and_unknown_keeps_baseline(self):
        requests = [
            {"request_id": "r1", "selected_value": "wanted", "option_values": ["wanted", "other"]},
            {"request_id": "r2", "selected_value": "wanted", "option_values": ["wanted", "other"]},
        ]
        proofs = [
            {"request_id": "r1", "selected_value": "wanted", "option_values": ["wanted", "other"],
             "eligible": True, "status": "conflict", "proof_pair_ids": ["p1"]},
            {"request_id": "r2", "selected_value": "wanted", "option_values": ["wanted", "other"],
             "eligible": False, "status": "unknown"},
        ]
        baseline = {
            "r1": {"state": "support", "winning_value": "wanted", "source_citations_verified": True},
            "r2": {"state": "support", "winning_value": "wanted", "source_citations_verified": True},
        }
        projected = gate.project_baseline_literal_overlay(requests, proofs, baseline)
        self.assertEqual(projected["r1"]["state"], "conflict")
        self.assertEqual(projected["r2"]["state"], "support")

    def test_alternate_whole_option_winner_never_becomes_conflict(self):
        request = {"request_id": "r", "selected_value": "selected", "option_values": ["selected", "other"]}
        proof = {"request_id": "r", "selected_value": "selected", "option_values": request["option_values"],
                 "eligible": False, "status": "unknown"}
        state = gate.project_baseline_literal_overlay([request], [proof], {
            "r": {"state": "support", "winning_value": "other", "source_citations_verified": True}})["r"]
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(state["reason"], "alternate_option_is_not_conflict_proof")

    def test_nli_semantic_conflict_does_not_require_equal_axis_names(self):
        task = {"task_id": "t", "request_id": "r", "case_id": "c", "au_row_key": "row",
                "direction": "rakuten_to_au", "raw_condition_ref": {"axis_name": "サイズ", "value": "80 cm"}}
        binding = {"pair_id": "p", "preparation_status": "ready",
            "exact_name_binding_status": "none",
            "task_binding": {"task_id": "t", "request_id": "r", "case_id": "c",
                              "au_row_key": "row", "direction": "rakuten_to_au"},
            "request_binding": {"request_id": "r"},
            "source_selected_fact": {"value": "80 cm"}}
        validated = {"pair_id": "p", "source_citations_verified": True,
            "semantic_truth_verified": False, "sourceproof_certified": False,
            "production_adoption_allowed": False,
            "judgments": [{"whole_option": "80 cm", "state": "explicit_conflict",
                           "scope_assessment": "same_condition", "semantic_relation": "explicit_conflict",
                           "citations_verified": 3}]}
        result = gate.nli_task_state(task, binding, validated)
        self.assertEqual(result["state"], "conflict")
        self.assertFalse(result["semantic_truth_verified"])

    def test_case_accept_requires_one_supported_row_and_every_other_row_conflict(self):
        tasks, rows = [], []
        states = {}
        for row_key, row_state in (("row-1", "support"), ("row-2", "conflict")):
            task_ids = []
            for direction in ("rakuten_to_au", "au_to_rakuten"):
                tid = row_key + ":" + direction
                task_ids.append(tid)
                tasks.append({"task_id": tid, "case_id": "case", "au_row_key": row_key,
                    "direction": direction, "request_id": tid,
                    "raw_condition_ref": {"axis_name": "axis", "value": "value"}})
                states[tid] = {"task_id": tid, "state": row_state}
            rows.append({"case_id": "case", "au_row_key": row_key, "rakuten_axis_count": 1,
                "au_axis_count": 1, "bindings": [{"task_id": tid} for tid in task_ids]})
        case = {"case_id": "case", "dossier_id": "d", "au_product_id": "p",
                "row_keys": ["row-1", "row-2"], "total_au_rows": 2}
        result = gate.finish_whole_sku_gate("choice_nli_full", [case], rows, tasks, list(states.values()))
        self.assertEqual(result["decisions"][0]["decision"], "accept")

        states["row-2:rakuten_to_au"]["state"] = "unknown"
        states["row-2:au_to_rakuten"]["state"] = "unknown"
        result = gate.finish_whole_sku_gate("choice_nli_full", [case], rows, tasks, list(states.values()))
        self.assertEqual(result["decisions"][0]["decision"], "drop")
        self.assertEqual(result["decisions"][0]["reason"], "unknown_competing_row")

    def test_whole_sku_assembly_rejects_missing_bidirectional_tasks(self):
        case = {"case_id": "case", "dossier_id": "d", "au_product_id": "p",
                "row_keys": ["row-1"], "total_au_rows": 1}
        row = {"case_id": "case", "au_row_key": "row-1", "rakuten_axis_count": 1,
               "au_axis_count": 1, "bindings": [{"task_id": "only-one"}]}
        task = {"task_id": "only-one", "case_id": "case", "au_row_key": "row-1",
                "direction": "rakuten_to_au"}
        with self.assertRaisesRegex(ValueError, "every original axis in both directions"):
            gate.finish_whole_sku_gate("baseline_literal_overlay", [case], [row], [task],
                                       [{"task_id": "only-one", "state": "support"}])


if __name__ == "__main__":
    unittest.main()
