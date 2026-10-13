"""Synthetic contract checks for the generic indexed DEV runner; no cohort labels or responses."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("generic_indexed_dev_runner", HERE / "run_next_body_indexed_dev_generic_v1.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def task(tid: str, pair_value: str | None) -> dict:
    return {"task_id": tid, "case_id": "case-a", "request_id": "request-" + tid,
            "au_row_key": "row-a", "direction": "forward",
            "raw_condition_ref": {"axis_name": "size", "value": "M"}}


def binding(tid: str, pair_id: str | None, ready: bool = True) -> dict:
    return {"pair_id": pair_id, "preparation_status": "ready" if ready else "contextless",
            "task_binding": {"task_id": tid}, "request_binding": {"request_id": "request-" + tid}}


def state(tid: str, value: str) -> dict:
    return {"task_id": tid, "status": value, "literal_proof_verified": True,
            "raw_source_family_verified": True, "selected_value": "M", "ordered_family": ["S", "M"],
            "proof_pair_id": "proof-" + tid}


def receipt(tid: str) -> dict:
    return {"task_id": tid, "status": "verified_complete_ordered_raw_family", "selected_value": "M",
            "ordered_family": ["S", "M"]}


class GenericPartitionTests(unittest.TestCase):
    def test_exact_partition_and_contextless_original_task_retained(self):
        tasks = [task("t1", "p1"), task("t2", "p2"), task("t3", "p2"), task("t4", None)]
        bindings = [binding("t1", "p1"), binding("t2", "p2"), binding("t3", "p2"),
                    binding("t4", None, ready=False)]
        pairs = [{"pair_id": "p1"}, {"pair_id": "p2"}]
        states = [state("t1", "support"), state("t2", "support"),
                  {"task_id": "t3", "status": "unknown"}, {"task_id": "t4", "status": "unknown"}]
        receipts = [receipt("t1"), receipt("t2"), {"task_id": "t3"}, {"task_id": "t4"}]
        result = RUNNER.derive_partition(tasks, bindings, pairs, states, receipts)
        self.assertEqual(result["protected_pair_ids"], ["p1"])
        self.assertEqual(result["dispatch_pair_ids"], ["p2"])
        self.assertEqual(result["protected_task_count"], 1)
        self.assertEqual(len(bindings), len(tasks))

    def test_duplicate_binding_fails_closed(self):
        tasks = [task("t1", "p1")]
        with self.assertRaises(ValueError):
            RUNNER.derive_partition(tasks, [binding("t1", "p1"), binding("t1", "p1")],
                [{"pair_id": "p1"}], [{"task_id": "t1", "status": "unknown"}], [{"task_id": "t1"}])

    def test_duplicate_pair_ref_response_rejected_by_identity_contract(self):
        # Generic snapshot logic requires one raw response per pair; check the common uniqueness guard.
        with self.assertRaises(ValueError):
            RUNNER.unique_index([{"pair_id": "p1"}, {"pair_id": "p1"}], "pair_id", "synthetic response")

    def test_firstpass_missing_ledger_equals_only_missing_dispatched_pairs(self):
        with tempfile.TemporaryDirectory(dir=RUNNER.ROOT / ".lab-output") as tmp:
            root = Path(tmp)
            pre = root / "preflight"
            pre.mkdir()
            dispatch = ["p1", "p2"]
            protected = ["p3"]
            (pre / "dispatch-pair-ids.jsonl").write_bytes(b'{"pair_id":"p1"}\n{"pair_id":"p2"}\n')
            (pre / "literal-protected-pair-ids.jsonl").write_bytes(b'{"pair_id":"p3"}\n')
            partition_path = pre / "source-partition-receipt.json"
            partition_path.write_text("{}\n")
            inventory_path = pre / "preflight-inventory.json"
            inventory_path.write_text("{}\n")
            raw_path = root / "raw.jsonl"
            raw_bytes = RUNNER.BODY.canon({"pair_ref": 0, "model": "actual synthetic response"}) + b"\n"
            raw_path.write_bytes(raw_bytes)
            failure_path = root / "failures.jsonl"
            failure_path.write_bytes(RUNNER.BODY.canon({"pair_id": "p2", "status": "missing_firstpass_response_unknown",
                "semantic_retry_performed": False}) + b"\n")
            receipt_path = root / "snapshot.json"
            refs = [{"pair_ref": 0, "pair_id": "p1"}, {"pair_ref": 1, "pair_id": "p2"},
                    {"pair_ref": 2, "pair_id": "p3"}]
            receipt = {"schema_version": "indexed-dev-raw-snapshot-v1", "status": "complete_firstpass_snapshot_frozen",
                "raw_path": raw_path.relative_to(RUNNER.ROOT).as_posix(), "raw_bytes": len(raw_bytes),
                "raw_sha256": RUNNER.sha_file(raw_path), "partition_receipt_sha256": RUNNER.sha_file(partition_path),
                "preflight_inventory_sha256": RUNNER.sha_file(inventory_path),
                "dispatch_pair_count": len(dispatch), "dispatch_pair_ids_sha256": RUNNER.digest_canonical(dispatch),
                "literal_protected_pair_count": len(protected), "literal_protected_pair_ids_sha256": RUNNER.digest_canonical(protected),
                "failure_ledger_bytes": failure_path.stat().st_size, "failure_ledger_sha256": RUNNER.sha_file(failure_path),
                "response_count": 1, "response_pair_ids_sha256": RUNNER.digest_canonical(["p1"]),
                "failure_count": 1, "all_predictions_frozen": True, "labels_read": False, "scores_created": False}
            receipt_path.write_text(json.dumps(receipt) + "\n")
            raw, failures, _ = RUNNER.validate_raw_snapshot(raw_path, receipt_path, failure_path,
                {}, refs, pre)
            self.assertEqual([r["pair_ref"] for r in raw], [0])
            self.assertEqual([r["pair_id"] for r in failures], ["p2"])

    def test_archive_adapter_changes_only_empty_split_guard(self):
        inspect = __import__("inspect")
        source = inspect.getsource(RUNNER.SCORE_ORIGINAL.load_token_reference_shards)
        clone = RUNNER.cloned_selected_label_reader()
        self.assertTrue(callable(clone))
        adapted = clone.__globals__["load_token_reference_shards"]
        self.assertIn('if not selected:', source)
        self.assertIn('frozen subset has no cases for required split', source)
        self.assertNotIn('frozen subset has no cases for required split', adapted.__code__.co_consts)
        self.assertIs(clone.__globals__["score_variant"], RUNNER.SCORE_ORIGINAL.score_variant)


if __name__ == "__main__":
    unittest.main()
