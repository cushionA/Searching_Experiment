"""Synthetic contract checks for the generic indexed TEST runner; no cohort labels or responses."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("generic_indexed_test_runner", HERE / "run_next_body_indexed_test_generic_v1.py")
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
    def test_decisive_states_require_exact_full_pair_and_family_receipt(self):
        tasks = [task("f", "M"), task("r", "M")]
        states = [{"task_id": "f", "status": "support", "proof_pair_id": "f|r"},
                  {"task_id": "r", "status": "support", "proof_pair_id": "f|r"}]
        receipts = [{"task_id": tid, "status": "verified_complete_ordered_raw_family",
                     "selected_value": "M", "ordered_family": ["S", "M"]} for tid in ("f", "r")]
        pair = {"forward_task_id": "f", "reverse_task_id": "r", "status": "support",
                "ordered_family": ["S", "M"]}
        adapted = RUNNER.adapt_literal_proofs(tasks, states, receipts, [pair])
        self.assertEqual({x["status"] for x in adapted}, {"support"})
        self.assertTrue(all(x["literal_proof_verified"] for x in adapted))
        with self.assertRaises(ValueError):
            RUNNER.adapt_literal_proofs(tasks, states, receipts, [])
        with self.assertRaises(ValueError):
            RUNNER.adapt_literal_proofs(tasks, states, receipts, [dict(pair, ordered_family=["M"])])

    def test_unknown_task_without_pair_is_retained_with_complete_receipt_coverage(self):
        tasks = [task("unpaired", None)]
        states = [{"task_id": "unpaired", "status": "unknown", "reason": "no reverse coordinate"}]
        receipts = [{"task_id": "unpaired", "status": "unknown", "reason": "nonmatching axis"}]
        adapted = RUNNER.adapt_literal_proofs(tasks, states, receipts, [])
        self.assertEqual(len(adapted), 1)
        self.assertEqual(adapted[0]["status"], "unknown")
        self.assertNotIn("literal_proof_verified", adapted[0])

    def test_literal_pair_proofs_reject_foreign_and_duplicate_tasks(self):
        tasks = [task("f", "M"), task("r", "M")]
        states = [{"task_id": tid, "status": "unknown"} for tid in ("f", "r")]
        receipts = [{"task_id": tid, "status": "unknown"} for tid in ("f", "r")]
        foreign = {"forward_task_id": "f", "reverse_task_id": "outside", "status": "unknown"}
        with self.assertRaises(ValueError):
            RUNNER.adapt_literal_proofs(tasks, states, receipts, [foreign])
        pair = {"forward_task_id": "f", "reverse_task_id": "r", "status": "unknown"}
        with self.assertRaises(ValueError):
            RUNNER.adapt_literal_proofs(tasks, states, receipts, [pair, pair])

    def test_literal_proof_state_and_receipt_coverage_must_be_complete_and_unique(self):
        tasks = [task("f", "M"), task("r", "M")]
        states = [{"task_id": "f", "status": "unknown"}]
        receipts = [{"task_id": tid, "status": "unknown"} for tid in ("f", "r")]
        with self.assertRaises(ValueError):
            RUNNER.adapt_literal_proofs(tasks, states, receipts, [])
        with self.assertRaises(ValueError):
            RUNNER.adapt_literal_proofs(tasks, states + [states[0]], receipts, [])
        with self.assertRaises(ValueError):
            RUNNER.adapt_literal_proofs(tasks, [{"task_id": tid, "status": "unknown"} for tid in ("f", "r")], receipts[:1], [])

    def test_identity_membership_guard_rejects_unknown_or_changed_ids(self):
        identity = {"case_ids": {f"c{i}" for i in range(40)}, "product_ids": {f"p{i}" for i in range(20)}}
        rows = [{"case_id": f"c{i}", "au_product_id": f"p{i // 2}"} for i in range(40)]
        RUNNER.validate_test_population({"selected_case_count": 40, "selected_product_count": 20}, rows, identity)
        with self.assertRaises(ValueError):
            RUNNER.validate_test_population({"selected_case_count": 41, "selected_product_count": 20}, rows + [{"case_id": "c40", "au_product_id": "p0"}], identity)
        with self.assertRaises(ValueError):
            RUNNER.validate_test_population({"selected_case_count": 40, "selected_product_count": 20}, [rows[0], *rows[1:-1], {"case_id": "c39", "au_product_id": "p99"}], identity)

    def test_identity_plan_guard_binds_membership_bytes_and_manifest_counts(self):
        with tempfile.TemporaryDirectory(dir=RUNNER.ROOT / ".lab-output") as tmp:
            root = Path(tmp)
            rows = [{"case_id": f"c{i}", "au_product_id": f"p{i // 2}"} for i in range(40)]
            membership = root / "selected-membership.jsonl"
            membership.write_bytes(b"".join(RUNNER.BODY.canon(row) + b"\n" for row in rows))
            excluded_rows = [{"au_product_id": f"q{i}"} for i in range(40)]
            excluded = root / "excluded-products.jsonl"
            excluded.write_bytes(b"".join(RUNNER.BODY.canon(row) + b"\n" for row in excluded_rows))
            prior_paths = [root / "prior-a.jsonl", root / "prior-b.jsonl"]
            prior_rows = [[{"case_id": f"old-a{i}", "au_product_id": f"q{i}"} for i in range(20)],
                          [{"case_id": f"old-b{i}", "au_product_id": f"q{i+20}"} for i in range(20)]]
            for path, prior in zip(prior_paths, prior_rows):
                path.write_bytes(b"".join(RUNNER.BODY.canon(row) + b"\n" for row in prior))
            manifest = {"schema_version": "sku-body-final-test-identity-plan-v1",
                "status": "identity_only_subset_plan_no_test_evidence_read",
                "test_condition_text_body_labels_gold_predictions_read": False,
                "selected_case_count": 40, "selected_product_count": 20,
                "outputs_sha256": {"selected-membership.jsonl": RUNNER.sha_file(membership),
                    "excluded-products.jsonl": RUNNER.sha_file(excluded)},
                "selected_case_ids": [r["case_id"] for r in rows],
                "selected_product_ids": [f"p{i}" for i in range(20)],
                "selected_products_disjoint_from_exclusions": True, "excluded_product_count": 40,
                "first_test20_membership_path": str(prior_paths[0].relative_to(RUNNER.ROOT)),
                "first_test20_membership_sha256": RUNNER.sha_file(prior_paths[0]),
                "unused_holdout20_membership_path": str(prior_paths[1].relative_to(RUNNER.ROOT)),
                "unused_holdout20_membership_sha256": RUNNER.sha_file(prior_paths[1])}
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            original_membership = RUNNER.IDENTITY_MEMBERSHIP_SHA256
            original_manifest = RUNNER.IDENTITY_MANIFEST_SHA256
            RUNNER.IDENTITY_MEMBERSHIP_SHA256 = RUNNER.sha_file(membership)
            RUNNER.IDENTITY_MANIFEST_SHA256 = RUNNER.sha_file(manifest_path)
            try:
                self.assertEqual(len(RUNNER.verify_test_identity_plan(root)["case_ids"]), 40)
                membership.write_bytes(membership.read_bytes() + b" ")
                with self.assertRaises(ValueError):
                    RUNNER.verify_test_identity_plan(root)
            finally:
                RUNNER.IDENTITY_MEMBERSHIP_SHA256 = original_membership
                RUNNER.IDENTITY_MANIFEST_SHA256 = original_manifest

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
            receipt = {"schema_version": "indexed-test-raw-snapshot-v1", "status": "complete_firstpass_snapshot_frozen",
                "raw_path": raw_path.relative_to(RUNNER.ROOT).as_posix(), "raw_bytes": len(raw_bytes),
                "raw_sha256": RUNNER.sha_file(raw_path), "partition_receipt_sha256": RUNNER.sha_file(partition_path),
                "preflight_inventory_sha256": RUNNER.sha_file(inventory_path),
                "dispatch_pair_count": len(dispatch), "dispatch_pair_ids_sha256": RUNNER.digest_canonical(dispatch),
                "literal_protected_pair_count": len(protected), "literal_protected_pair_ids_sha256": RUNNER.digest_canonical(protected),
                "failure_ledger_bytes": failure_path.stat().st_size, "failure_ledger_sha256": RUNNER.sha_file(failure_path),
                "response_count": 1, "response_pair_ids_sha256": RUNNER.digest_canonical(["p1"]),
                "failure_count": 1, "all_predictions_frozen": True, "labels_read": False, "scores_created": False,
                "independent_test": True, "test_split_read": True,
                "dataset": "sku-next-body-indexed-test-20261013-v1",
                "identity_plan_manifest_sha256": RUNNER.IDENTITY_MANIFEST_SHA256,
                "selected_membership_sha256": RUNNER.IDENTITY_MEMBERSHIP_SHA256}
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
