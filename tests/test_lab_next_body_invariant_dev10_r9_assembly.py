import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))

import assemble_next_body_invariant_dev10_r9_v1 as ASSEMBLER
import grade_next_body_invariant_dev10_r9_v1 as GRADER


class BodyDev10AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            {"task_id": "t1", "raw_condition_ref": {"value": "Small"}},
            {"task_id": "t2", "raw_condition_ref": {"value": "Large"}},
        ]
        self.proof_id = "t1|t2"
        self.states = [
            {"task_id": "t1", "status": "support", "reason": "same exact value",
             "proof_pair_id": self.proof_id},
            {"task_id": "t2", "status": "unknown", "reason": "not uniquely paired"},
        ]
        self.receipts = [
            {"task_id": "t1", "status": "verified_complete_ordered_raw_family",
             "ordered_family": ["Small", "Large"], "selected_value": "Small"},
            {"task_id": "t2", "status": "verified_complete_ordered_raw_family",
             "ordered_family": ["Small", "Large"], "selected_value": "Large"},
        ]
        self.pairs = [{"forward_task_id": "t1", "reverse_task_id": "t2",
                       "status": "support", "ordered_family": ["Small", "Large"]}]

    def test_literal_proofs_bind_exact_task_value_and_verified_pair(self):
        with patch.object(ASSEMBLER, "TASK_COUNT", 2):
            adapted = ASSEMBLER.adapt_literal_proofs_for_eval(
                self.tasks, self.states, self.receipts, self.pairs)
        by_id = {row["task_id"]: row for row in adapted}
        self.assertTrue(by_id["t1"]["literal_proof_verified"])
        self.assertEqual(by_id["t1"]["selected_value"], "Small")
        self.assertEqual(by_id["t1"]["proof_pair_id"], self.proof_id)
        self.assertNotIn("literal_proof_verified", by_id["t2"])

    def test_literal_pair_mismatch_is_rejected(self):
        pairs = copy.deepcopy(self.pairs)
        pairs[0]["reverse_task_id"] = "foreign-task"
        with self.assertRaises(ValueError):
            ASSEMBLER.adapt_literal_proofs_for_eval(self.tasks, self.states, self.receipts, pairs)

    def test_task_population_must_be_exact(self):
        states = self.states + [{"task_id": "extra", "status": "unknown"}]
        with patch.object(ASSEMBLER, "TASK_COUNT", 2), self.assertRaises(ValueError):
            ASSEMBLER.adapt_literal_proofs_for_eval(self.tasks, states, self.receipts, self.pairs)

    def test_full_bindings_keep_contextless_original_tasks(self):
        tasks = [{"task_id": "a"}, {"task_id": "b"}]
        bindings = [
            {"task_binding": {"task_id": "a"}, "preparation_status": "ready"},
            {"task_binding": {"task_id": "b"}, "preparation_status": "abstention"},
        ]
        with patch.object(ASSEMBLER, "EXPECTED_TASKS", 2), patch.object(ASSEMBLER, "TASK_COUNT", 2):
            with patch.object(ASSEMBLER, "read_rows", return_value=bindings):
                self.assertEqual(ASSEMBLER.load_complete_bindings(tasks, Path("/unused")), bindings)
            with patch.object(ASSEMBLER, "read_rows", return_value=bindings[:-1]):
                with self.assertRaises(ValueError):
                    ASSEMBLER.load_complete_bindings(tasks, Path("/unused"))

    def test_source_validation_adapter_preserves_model_semantics(self):
        decision = {"whole_option": "Small", "state": "support", "scope_assessment": "same_condition",
                    "semantic_relation": "support", "target_fact_indices": [1],
                    "body_quote_refs": [{"body_index": 0, "quote": "x"}]}
        response = {"pair_id": "p", "decisions": [decision]}
        with patch.object(ASSEMBLER.NATIVE, "validate_source_response", return_value={
                "source_citations_verified": True, "citations": [{"quote": "x"}]}):
            validated, citations = ASSEMBLER.adapt_source_validated_pair(
                response, {"pair_id": "p", "cache_key": "c", "option_values": ["Small"]}, {}, {}, Path("."))
        self.assertEqual(validated["judgments"][0]["state"], decision["state"])
        self.assertEqual(validated["judgments"][0]["semantic_relation"], decision["semantic_relation"])
        self.assertEqual(validated["judgments"][0]["citations_verified"], 3)
        self.assertEqual(citations, [{"quote": "x"}])
        self.assertFalse(validated["semantic_truth_verified"])

    def test_pair_partition_protects_only_fully_source_proved_fanout(self):
        tasks = [
            {"task_id": "t1", "request_id": "r1", "case_id": "c", "au_row_key": "row",
             "direction": "rakuten_to_au", "raw_condition_ref": {"value": "red", "axis_name": "color"}},
            {"task_id": "t2", "request_id": "r2", "case_id": "c", "au_row_key": "row",
             "direction": "au_to_rakuten", "raw_condition_ref": {"value": "blue", "axis_name": "color"}},
            {"task_id": "t3", "request_id": "r3", "case_id": "c", "au_row_key": "row",
             "direction": "rakuten_to_au", "raw_condition_ref": {"value": "green", "axis_name": "color"}},
        ]
        family = ["red", "blue"]
        proof_states = [
            {"task_id": "t1", "status": "support", "literal_proof_verified": True,
             "raw_source_family_verified": True, "selected_value": "red", "ordered_family": family},
            {"task_id": "t2", "status": "support", "literal_proof_verified": True,
             "raw_source_family_verified": True, "selected_value": "blue", "ordered_family": family},
            {"task_id": "t3", "status": "unknown"},
        ]
        receipts = [{"task_id": task["task_id"], "status": "verified_complete_ordered_raw_family",
                     "ordered_family": family, "selected_value": task["raw_condition_ref"]["value"]}
                    for task in tasks]
        bindings = [{"preparation_status": "ready", "pair_id": pid,
                     "task_binding": {"task_id": tid}}
                    for pid, tids in (("p1", ["t1", "t2"]), ("p2", ["t3"])) for tid in tids]
        pair_proofs = [{"pair_id": "p1"}, {"pair_id": "p2"}]
        result = ASSEMBLER.derive_pair_partition(tasks, bindings, pair_proofs, proof_states, receipts)
        self.assertEqual(result["protected_pair_ids"], ["p1"])
        self.assertEqual(result["dispatch_pair_ids"], ["p2"])
        self.assertEqual(result["protected_task_count"], 2)

    def test_archive_split_adapter_preserves_dev_membership(self):
        members = [{"case_id": "c1", "split": "dev", "au_product_id": "p1"}]
        adapted = GRADER.score_membership_for_archive(members, {"c1": {"source_version": "v2"}})
        self.assertEqual(members[0]["split"], "dev")
        self.assertEqual(adapted[0]["split"], "v2")
        self.assertEqual(adapted[0]["source_version"], "v2")

    def test_grading_requires_root_go_before_freeze_or_archives(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            go = Path(directory) / "no-go.json"
            go.write_text(json.dumps({"root_go_for_body_dev10_grading": False}), encoding="utf-8")
            with self.assertRaises(PermissionError):
                GRADER.grade(go, Path("/missing/freeze.json"), Path("/missing/membership.jsonl"),
                             Path("/missing/output.json"), Path("/missing/v2.zip"), Path("/missing/v3.zip"))

    def test_missing_firstpass_response_is_unknown_and_must_be_ledgered(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT / ".lab-output") as directory:
            folder = Path(directory)
            raw = folder / "responses.jsonl"
            raw.write_text("", encoding="utf-8")
            failure = folder / "failures.jsonl"
            failure_row = {"pair_id": "p1", "status": "missing_firstpass_response_unknown",
                           "model_judgment_recorded": False, "semantic_retry_performed": False}
            failure.write_text(ASSEMBLER.BODY.canon(failure_row).decode() + "\n", encoding="utf-8")
            receipt_path = folder / "receipt.json"
            receipt = {"schema_version": "sku-next-body-invariant-dev10-r9-literal-snapshot-v1",
                "status": "complete_firstpass_snapshot_frozen", "all_predictions_frozen": True,
                "labels_read": False, "test_labels_read": False, "scores_created": False,
                "production_adoption_allowed": False, "prepared_pair_count": 2,
                "expected_dispatched_pair_count": 1,
                "expected_dispatched_pair_ids_sha256": ASSEMBLER.BODY.sha_bytes(ASSEMBLER.BODY.canon(["p1"])),
                "literal_protected_pair_count": 1,
                "literal_protected_pair_ids_sha256": ASSEMBLER.BODY.sha_bytes(ASSEMBLER.BODY.canon(["protected1"])),
                "pair_partition_receipt_sha256": "f" * 64,
                "response_count": 0, "failure_count": 1,
                "snapshot_path": ASSEMBLER.relpath(raw), "snapshot_bytes": raw.stat().st_size,
                "snapshot_sha256": ASSEMBLER.sha_file(raw),
                "response_pair_ids_sha256": ASSEMBLER.BODY.sha_bytes(ASSEMBLER.BODY.canon([])),
                "failure_ledger_path": ASSEMBLER.relpath(failure),
                "failure_ledger_bytes": failure.stat().st_size,
                "failure_ledger_sha256": ASSEMBLER.sha_file(failure)}
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            _raw, failures, _receipt = ASSEMBLER.validate_snapshot(
                raw, receipt_path, {"p1"}, {"protected1"}, "f" * 64)
            self.assertEqual(failures, [failure_row])
            failure.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                ASSEMBLER.validate_snapshot(raw, receipt_path, {"p1"}, {"protected1"}, "f" * 64)


if __name__ == "__main__":
    unittest.main()
