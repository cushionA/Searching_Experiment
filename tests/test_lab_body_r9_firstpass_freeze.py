"""Synthetic checks for R9 first-pass snapshot failure semantics; no real responses read."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".lab-output/sku-next-body-invariant-native-execution-20261013-v1/freeze_body_r9_firstpass_v1.py"
SPEC = importlib.util.spec_from_file_location("freeze_body_r9_firstpass_v1", SCRIPT)
FREEZER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(FREEZER)


class FirstpassFreezeSyntheticTests(unittest.TestCase):
    def test_frozen_packet_index_remains_pre_inference_after_root_go(self):
        go = {"schema_version": "sku-body-native-r9-root-classification-go-v1",
              "root_authorization": True}
        FREEZER.verify_packet_index_authorization({"inference_run": False,
            "labels_read": False, "scores_created": False}, go)
        with self.assertRaises(ValueError):
            FREEZER.verify_packet_index_authorization({"inference_run": True,
                "labels_read": False, "scores_created": False}, go)

    def test_tool_ledger_must_cover_all_spawns_on_trusted_route(self):
        go = {"schema_version": "sku-body-native-r9-root-classification-go-v1",
              "root_authorization": True, "requested_model_selector": "gpt-6-luna",
              "trusted_selector_route": "trusted-spawn-route"}
        records = []
        for i in range(1, 65):
            common = {"packet_id": f"packet-{i:03d}",
                      "requested_model_selector": "gpt-6-luna",
                      "trusted_selector_route": "trusted-spawn-route"}
            if i <= 39 or i >= 43:
                records.append({**common, "tool_name": "collaboration.spawn_agent",
                    "fork_turns": "none", "returned_task_name": f"/root/body_r9_packet{i:03d}"})
            else:
                records.append({**common, "tool_name": "collaboration.followup_task",
                    "target_task_name": f"/root/body_r9_packet{i-39:03d}",
                    "origin_spawn_task_name": f"/root/body_r9_packet{i-39:03d}",
                    "origin_requested_model_selector": "gpt-6-luna",
                    "worker_history_retained": True,
                    "retained_history_disclosed_to_worker": True})
        ledger = {"schema_version": "sku-body-native-r9-root-tool-execution-ledger-v1",
                  "status": "complete_root_tool_execution_ledger", "packet_count": 64,
                  "packet_records": records,
                  "attempt_records": [
                      {"packet_id": "packet-040", "tool_name": "collaboration.spawn_agent",
                       "status": "failed", "classification_recorded": False},
                      {"packet_id": "packet-043", "tool_name": "collaboration.followup_task",
                       "status": "failed", "classification_recorded": False}]}
        FREEZER.verify_tool_evidence(ledger, go)
        with self.assertRaises(ValueError):
            FREEZER.verify_tool_evidence({**ledger, "packet_records": records[:-1]}, go)
        tampered = [*records]
        tampered[0] = {**tampered[0], "trusted_selector_route": "self-reported-route"}
        with self.assertRaises(ValueError):
            FREEZER.verify_tool_evidence({**ledger, "packet_records": tampered}, go)
        tampered = [*records]
        tampered[0] = {**tampered[0], "returned_task_name": "/root/unrelated_task"}
        with self.assertRaises(ValueError):
            FREEZER.verify_tool_evidence({**ledger, "packet_records": tampered}, go)
        tampered = [*records]
        tampered[39] = {**tampered[39], "target_task_name": "/root/unrelated_task"}
        with self.assertRaises(ValueError):
            FREEZER.verify_tool_evidence({**ledger, "packet_records": tampered}, go)
        tampered = [*records]
        tampered[39] = {**tampered[39], "retained_history_disclosed_to_worker": False}
        with self.assertRaises(ValueError):
            FREEZER.verify_tool_evidence({**ledger, "packet_records": tampered}, go)
        tampered = [*records]
        tampered[40] = {**tampered[40], "origin_spawn_task_name": "/root/body_r9_packet001"}
        with self.assertRaises(ValueError):
            FREEZER.verify_tool_evidence({**ledger, "packet_records": tampered}, go)
        arbitrary_route = [*records]
        arbitrary_route[-1] = {"packet_id": "packet-064", "tool_name": "collaboration.followup_task",
            "requested_model_selector": "gpt-6-luna", "trusted_selector_route": "trusted-spawn-route",
            "target_task_name": "/root/body_r9_packet001",
            "origin_spawn_task_name": "/root/body_r9_packet001",
            "origin_requested_model_selector": "gpt-6-luna", "worker_history_retained": True,
            "retained_history_disclosed_to_worker": True}
        FREEZER.verify_tool_evidence({**ledger, "packet_records": arbitrary_route}, go)
        with self.assertRaises(ValueError):
            FREEZER.verify_tool_evidence({**ledger, "packet_records": [*records[:-1], records[0]]}, go)

    def test_unaccepted_draft_judgment_is_preserved_but_not_registered(self):
        rows = [
            {"pair_id": "p1", "draft_path": "drafts/packet-001.jsonl", "draft_sha256": "a" * 64,
             "model_semantic_judgments_present_in_unaccepted_draft": True},
            {"pair_id": "p2", "draft_path": "drafts/packet-002.jsonl", "draft_sha256": "b" * 64,
             "model_semantic_judgments_present_in_unaccepted_draft": False},
        ]
        ledger = FREEZER.make_failure_ledger({"p1", "p2", "p3"}, {"p3"}, rows)
        self.assertEqual({r["pair_id"] for r in ledger}, {"p1", "p2"})
        failed_judgment = next(r for r in ledger if r["pair_id"] == "p1")
        self.assertFalse(failed_judgment["model_judgment_recorded"])
        self.assertFalse(failed_judgment["registered_complete_response_recorded"])
        self.assertTrue(failed_judgment["unaccepted_firstpass_literal_present"])
        self.assertTrue(failed_judgment["model_semantic_judgments_present_in_unaccepted_draft"])
        self.assertFalse(failed_judgment["semantic_retry_performed"])

    def test_failure_ledger_rejects_missing_or_extra_draft_members(self):
        record = {"pair_id": "p1", "draft_path": "drafts/packet-001.jsonl",
                  "draft_sha256": "a" * 64,
                  "model_semantic_judgments_present_in_unaccepted_draft": False}
        with self.assertRaises(ValueError):
            FREEZER.make_failure_ledger({"p1", "p2"}, set(), [record])
        with self.assertRaises(ValueError):
            FREEZER.make_failure_ledger({"p1"}, set(), [record, {**record, "pair_id": "foreign"}])

    def test_accepted_pair_cannot_be_misreported_as_failure(self):
        record = {"pair_id": "p1", "draft_path": "drafts/packet-001.jsonl",
                  "draft_sha256": "a" * 64,
                  "model_semantic_judgments_present_in_unaccepted_draft": True}
        with self.assertRaises(ValueError):
            FREEZER.make_failure_ledger({"p1"}, {"p1"}, [record])


if __name__ == "__main__":
    unittest.main()
