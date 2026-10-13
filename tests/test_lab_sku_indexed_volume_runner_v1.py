"""Small identity-only contract tests for the variable-size TEST runner."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "experiments/sku-matching/run_indexed_volume_test_v1.py"
spec = importlib.util.spec_from_file_location("volume_runner_contract", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(RUNNER)
PARTIAL_HELPER_PATH = ROOT / "experiments/sku-matching/prepare_indexed_volume_partial_source_v1.py"
PARTIAL = RUNNER.load_module(PARTIAL_HELPER_PATH, "volume_partial_source_contract")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path, rows: list[dict]) -> None:
    path.write_bytes(b"".join(json.dumps(r, sort_keys=True).encode() + b"\n" for r in rows))


class VolumeRunnerIdentityTests(unittest.TestCase):
    def test_variable_count_exclusions_and_exact_membership_join(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".lab-output") as rawtmp:
            plan = Path(rawtmp)
            selected = [
                {"case_id": "c1", "au_product_id": "p1"},
                {"case_id": "c2", "au_product_id": "p1"},
                {"case_id": "c3", "au_product_id": "p2"},
                {"case_id": "c4", "au_product_id": "p3"},
                {"case_id": "c5", "au_product_id": "p3"},
            ]
            jsonl(plan / "selected-membership.jsonl", selected)
            consumed = plan / "consumed.jsonl"
            jsonl(consumed, [{"case_id": "old1", "au_product_id": "used"},
                             {"case_id": "old2", "au_product_id": "used"}])
            manifest = {
                "schema_version": "sku-body-final-test-identity-plan-v1",
                "status": "identity_only_subset_plan_no_test_evidence_read",
                "test_condition_text_body_labels_gold_predictions_read": False,
                "selected_case_count": 5, "selected_product_count": 3,
                "outputs_sha256": {"selected-membership.jsonl": sha(plan / "selected-membership.jsonl")},
                "excluded_consumed_memberships": [{"path": consumed.relative_to(ROOT).as_posix(),
                                                   "sha256": sha(consumed)}],
                "excluded_unique_test_product_count": 1,
                "excluded_products_disjoint_from_selected": True,
                "unused_case_count": 5, "unused_product_count": 3,
                "all_unused_product_cases_included": True,
            }
            (plan / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            config = {
                "identity_plan_dir": plan.relative_to(ROOT).as_posix(),
                "identity_plan_manifest_sha256": sha(plan / "manifest.json"),
                "selected_membership_sha256": sha(plan / "selected-membership.jsonl"),
                "identity_bridge_sha256": "c" * 64,
                "selected_product_count": 3, "selected_case_count": 5,
            }
            RUNNER.configure_identity(config)
            identity = RUNNER.verify_test_identity_plan(plan)
            RUNNER.validate_test_population({"selected_case_count": 5,
                "selected_product_count": 3}, selected, identity)
            # The literal gate remains source-derived: an unknown task is not protected.
            task = {"task_id": "t1"}
            state = {"task_id": "t1", "status": "unknown"}
            receipt = {"task_id": "t1"}
            adapted = RUNNER.adapt_literal_proofs([task], [state], [receipt], [])
            self.assertEqual(adapted, [state])
            partition = RUNNER.derive_partition(
                [task], [{"task_binding": {"task_id": "t1"}, "pair_id": "pair1",
                          "preparation_status": "ready"}],
                [{"pair_id": "pair1"}], adapted, [receipt])
            self.assertEqual(partition["dispatch_pair_ids"], ["pair1"])
            self.assertEqual(partition["protected_pair_ids"], [])

    def test_identity_count_mismatch_fails_closed(self):
        RUNNER.IDENTITY_PRODUCT_COUNT = 2
        RUNNER.IDENTITY_CASE_COUNT = 3
        with self.assertRaises(ValueError):
            RUNNER.verify_selected_test_membership(
                [{"case_id": "c1", "au_product_id": "p1"}],
                {"case_ids": {"c1"}, "product_ids": {"p1"}})

    def test_scoped_population_uses_case_grain_separate_from_document_grain(self):
        planned = [{"case_id": f"c{i}", "au_product_id": f"p{i % 3}"} for i in range(5)]
        rows = {
            "source-membership.jsonl": list(planned),
            "source-case-plans.jsonl": list(planned),
            "source-tasks.jsonl": [{"task_id": "t1"}, {"task_id": "t2"}],
            "source-row-plans.jsonl": [{"row": i} for i in range(3)],
            "source-requests.jsonl": [{"request_id": "r1"}],
            "model-pairs-v3.jsonl": [{"pair_id": "pair1"}],
            "model-payloads-v3.jsonl": [{"pair_id": "pair1"}],
            "validation-bindings.jsonl": [{"binding": i} for i in range(2)],
            "source-bodies.jsonl": [{"document_id": "d1"}, {"document_id": "d2"}],
        }
        counts = {"selected_case_count": 5, "task_count": 2, "row_count": 3,
            "derived_request_count": 1}
        native_manifest = {"prepared_pair_count": 1, "payload_count": 1,
            "private_validation_binding_count": 2, "document_count": 2}
        RUNNER.validate_scoped_source_populations(native_manifest, rows, counts, planned)
        bad_cases = dict(rows)
        bad_cases["source-case-plans.jsonl"] = [*planned[:-1],
            {"case_id": "foreign", "au_product_id": "p0"}]
        with self.assertRaisesRegex(ValueError, "case/product identities"):
            RUNNER.validate_scoped_source_populations(native_manifest, bad_cases, counts, planned)

    def test_dynamic_counts_flow_into_receipt_and_packetizer_contract(self):
        packetizer_path = ROOT / "experiments/sku-matching/prepare_body_indexed_volume_packets_v1.py"
        packetizer = packetizer_path.read_text(encoding="utf-8")
        self.assertIn('"selected_product_count": RUNNER.IDENTITY_PRODUCT_COUNT', packetizer)
        self.assertIn('"selected_case_count": RUNNER.IDENTITY_CASE_COUNT', packetizer)
        self.assertIn('IDENTITY_PRODUCT_COUNT', RUNNER_PATH.read_text(encoding="utf-8"))
        self.assertNotIn('!= 20', RUNNER_PATH.read_text(encoding="utf-8"))
        self.assertNotIn('!= 40', RUNNER_PATH.read_text(encoding="utf-8"))

    def test_volume_identity_metadata_is_required_at_freeze_boundary(self):
        RUNNER.IDENTITY_MANIFEST_SHA256 = "a" * 64
        RUNNER.IDENTITY_MEMBERSHIP_SHA256 = "b" * 64
        RUNNER.IDENTITY_PRODUCT_COUNT = 46
        RUNNER.IDENTITY_CASE_COUNT = 569
        receipt = {"status": "complete_both_whole_gate_variants_frozen",
            "labels_read": False, "scores_created": False, "independent_test": True,
            "test_split_read": True, "identity_plan_manifest_sha256": "a" * 64,
            "selected_membership_sha256": "b" * 64, "selected_product_count": 46,
            "selected_case_count": 569}
        readiness_fields = {"source_family_unknown_task_count": 1,
            "contextless_task_count": 2, "preparation_error_count": 2}
        receipt.update(readiness_fields)
        inventory = {"schema_version": "indexed-test-assembly-inventory-v1",
            "independent_test": True, "test_split_read": True}
        self.assertTrue(RUNNER.assembly_metadata_is_freeze_compatible(receipt, inventory, readiness_fields))
        receipt["selected_membership_sha256"] = "c" * 64
        self.assertFalse(RUNNER.assembly_metadata_is_freeze_compatible(receipt, inventory, readiness_fields))

    def _unknown_ledger_fixture(self):
        task_id = "unknown-task"
        receipt = {"task_id": task_id, "status": "unknown", "reason": "empty family"}
        readiness = {"unknown_task_ids": [task_id], "unknown_count": 1, "verified_count": 0,
            "unknown_task_ids_sha256": PARTIAL.digest([task_id]),
            "unknown_receipt_ledger_sha256": PARTIAL.digest([receipt])}
        error = {"task_id": task_id,
            "error": "full original option family missing or malformed"}
        tasks = [{"task_id": task_id}]
        bindings = [{"task_binding": {"task_id": task_id}, "pair_id": None,
            "preparation_status": "unknown_invalid_source_context", "preparation_errors": [error["error"]]}]
        classified = PARTIAL.classify_expected_contextless_errors([error], readiness, tasks, [], [])
        scoped = {
            "source_family_completeness": "partial_unknown_retained",
            "source_family_complete_all_tasks": False,
            "source_family_verified_task_count": 0,
            "source_family_unknown_task_count": 1,
            "source_family_unknown_task_ids_sha256": readiness["unknown_task_ids_sha256"],
            "source_family_unknown_receipt_ledger_sha256": readiness["unknown_receipt_ledger_sha256"],
            "source_family_unknown_task_ids": [task_id],
            "source_fact_errors_preserved": 1,
            "contextless_unknown_binding_count": 1,
            "target_fact_context_missing_task_count": 0,
            "target_fact_context_missing_task_ids": [],
            "target_fact_context_missing_task_ids_sha256": PARTIAL.digest([]),
            "contextless_task_ids": [task_id],
            "contextless_task_ids_sha256": classified["contextless_task_ids_sha256"],
            "contextless_error_ledger_sha256": classified["contextless_error_ledger_sha256"],
            "unknown_tasks_have_no_model_pair": True,
            "preparation_error_count": 1,
        }
        return readiness, error, tasks, bindings, scoped, {"source_fact_errors": 1}

    def test_partial_source_ledger_accepts_only_proven_null_pair_error(self):
        readiness, error, tasks, bindings, scoped, freeze = self._unknown_ledger_fixture()
        receipt = RUNNER.validate_partial_contextless_ledger(
            PARTIAL, readiness, scoped, freeze, [error], tasks, [], [], bindings, [])
        self.assertEqual(receipt["preparation_error_count"], 1)
        self.assertEqual(receipt["contextless"]["contextless_count"], 1)

    def test_partial_source_ledger_rejects_bad_binding_foreign_error_and_gap(self):
        readiness, error, tasks, bindings, scoped, freeze = self._unknown_ledger_fixture()
        for bad_bindings, bad_errors in (
            ([{**bindings[0], "pair_id": "unexpected-pair"}], [error]),
            (bindings, [{**error, "task_id": "foreign-task"}]),
            ([], [error]),
        ):
            with self.subTest(binding_count=len(bad_bindings), error_id=bad_errors[0]["task_id"]):
                with self.assertRaises(ValueError):
                    RUNNER.validate_partial_contextless_ledger(
                        PARTIAL, readiness, scoped, freeze, bad_errors, tasks, [], [], bad_bindings, [])

    def test_partial_source_unknown_request_must_be_empty(self):
        rows = {
            "documents.jsonl": [{"document_id": "doc"}],
            "requests.jsonl": [{"request_id": "req", "source_family_status": "unknown",
                "option_values": ["must-not-be-present"], "source_family_receipt": None,
                "model_input_id": None, "production_adoption_allowed": False}],
            "tasks.jsonl": [{"task_id": "task", "request_id": "req"}],
            "row-plans.jsonl": [],
            "case-plans.jsonl": [{"case_id": "case", "au_product_id": "product"}],
            "membership.jsonl": [{"case_id": "case", "au_product_id": "product"}],
            "task-request-family-audit.jsonl": [{"task_id": "task", "derived_request_id": "req",
                "status": "unknown", "reason": "unknown", "raw_family": None, "member_spans": None}],
            "request-model-input-map.jsonl": [], "baseline-model-inputs.jsonl": [],
        }
        manifest = {"document_count": 1, "derived_request_count": 1, "task_count": 1,
            "row_count": 0, "selected_case_count": 1, "selected_product_count": 1}
        with self.assertRaisesRegex(ValueError, "explicitly empty"):
            PARTIAL.validate_source_records(rows, manifest)


if __name__ == "__main__":
    unittest.main()
