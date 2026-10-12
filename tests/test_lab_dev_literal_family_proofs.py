from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
PREP_SPEC = importlib.util.spec_from_file_location(
    "dev_literal_prep", ROOT / "experiments/sku-matching/prepare_dev_literal_family_proofs_v1.py")
prep = importlib.util.module_from_spec(PREP_SPEC)
assert PREP_SPEC and PREP_SPEC.loader
PREP_SPEC.loader.exec_module(prep)
VALID_SPEC = importlib.util.spec_from_file_location(
    "dev_literal_validator", ROOT / "experiments/sku-matching/validate_dev_literal_family_proofs_v1.py")
validator = importlib.util.module_from_spec(VALID_SPEC)
assert VALID_SPEC and VALID_SPEC.loader
VALID_SPEC.loader.exec_module(validator)


OUTPUT = ROOT / ".lab-output/sku-next-dev-literal-family-proofs-20261013-v1"
SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
BUNDLE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"


class DevLiteralFamilyProofTests(unittest.TestCase):
    def test_complete_raw_source_families_and_protected_subset_validate(self):
        counts = validator.validate()
        self.assertEqual(counts["case_count"], 40)
        self.assertEqual(counts["row_count"], 504)
        self.assertEqual(counts["task_count"], 1924)
        self.assertEqual(counts["task_source_family_status_counts"],
                         {"verified_complete_ordered_raw_family": 1924})
        self.assertEqual(counts["eligible_request_status_counts"], {"conflict": 414, "support": 172})
        self.assertEqual(counts["unknown_request_status_count"], 459)
        self.assertEqual(counts["protected_nli_context_count"], 305)

    def test_wrong_original_source_foreign_key_is_rejected(self):
        source_manifest = prep.read_json(SOURCE / "manifest.json")
        source_cases = prep.read_rows(OUTPUT / "inputs/source-cases.jsonl")
        gate_cases = {row["case_id"]: row for row in prep.read_rows(
            ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/case-plans.jsonl")}
        case = json.loads(json.dumps(source_cases[0]["record"]))
        case["dossier_id"] = "foreign-dossier"
        with self.assertRaisesRegex(ValueError, "does not bind to its exact original DEV records"):
            prep.load_original_records(BUNDLE, source_manifest, {case["case_id"]: {
                "source_version": gate_cases[case["case_id"]]["source_version"],
                "dossier_id": "foreign-dossier", "au_product_id": case["au_product_id"]}})

    def test_wrong_request_task_fanout_is_rejected(self):
        request = {"request_id": "request-a", "direction": "rakuten_to_au", "selected_value": "v",
                   "option_values": ["v"], "request_sha256": "sha", "model_input_id": "ctx",
                   "axis_name": "カラー", "target_document_id": "doc", "target_document_sha256": "d"}
        task = {"task_id": "task-a", "request_id": "request-a", "direction": "au_to_rakuten",
                "case_id": "case-a", "au_row_key": "row-a",
                "raw_condition_ref": {"value": "v", "axis_name": "カラー"}}
        with tempfile.TemporaryDirectory() as directory:
            proof_path = Path(directory) / "proofs.jsonl"
            proof_path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "selected-value or direction fanout differs"):
                prep.aggregate_request_statuses(Path(directory), {
                    "selected_tasks": {"task-a": task}, "selected_requests": {"request-a": request},
                    "task_source_receipts": []}, proof_path)

    def test_unpaired_task_remains_unknown_not_conflict(self):
        request = {"request_id": "request-a", "direction": "rakuten_to_au", "selected_value": "v",
                   "option_values": ["v"], "request_sha256": "sha", "model_input_id": "ctx",
                   "axis_name": "カラー", "target_document_id": "doc", "target_document_sha256": "d",
                   "target_selected_axes": [{"axis_name": "カラー", "value": "other"}]}
        task = {"task_id": "task-a", "request_id": "request-a", "direction": "rakuten_to_au",
                "case_id": "case-a", "au_row_key": "row-a",
                "raw_condition_ref": {"value": "v", "axis_name": "カラー"}}
        receipt = {"task_id": "task-a", "status": "verified_complete_ordered_raw_family",
                   "ordered_family": ["v"]}
        with tempfile.TemporaryDirectory() as directory:
            proof_path = Path(directory) / "proofs.jsonl"
            proof_path.write_text("", encoding="utf-8")
            prep.aggregate_request_statuses(Path(directory), {
                "selected_tasks": {"task-a": task}, "selected_requests": {"request-a": request},
                "task_source_receipts": [receipt]}, proof_path)
            status = json.loads((Path(directory) / "request-proof-statuses.jsonl").read_text().strip())
            self.assertEqual(status["status"], "unknown")
            self.assertFalse(status["eligible"])
            self.assertIsNone(status["winning_value"])

    def test_v7_core_or_residual_fields_cannot_leak_into_literal_status(self):
        with self.assertRaisesRegex(ValueError, "untrusted v7 heuristic fields"):
            validator.assert_no_untrusted_producer_fields({"request_id": "r", "core": {"identical": True}})


if __name__ == "__main__":
    unittest.main()
