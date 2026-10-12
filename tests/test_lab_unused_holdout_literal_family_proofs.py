from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "unused_holdout_literal_proofs",
    ROOT / "experiments/sku-matching/prepare_unused_holdout_literal_family_proofs_v1.py")
proofs = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(proofs)


class UnusedHoldoutLiteralProofPreparationTests(unittest.TestCase):
    def test_source_manifest_rejects_wrong_split_and_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs, source, nli = root / "inputs", root / "source", root / "nli"
            inputs.mkdir(); source.mkdir(); nli.mkdir()
            (inputs / "manifest.json").write_text(json.dumps({
                "schema_version": "full-condition-inputs-v2", "split": "dev",
                "independent_test": False, "selected_product_count": 20,
                "selected_case_count": 40, "selected_row_count": 394,
                "selected_task_count": 1598, "labels_read": False,
                "model_inference_run": False, "production_adoption_allowed": False,
                "all_rows_for_selected_cases_retained": True,
                "all_original_task_bindings_retained": True}), encoding="utf-8")
            (source / "manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen 20-product x 2-case TEST subset"):
                proofs.validate_source_manifest(inputs, source, nli)

    def test_foreign_task_binding_in_complete_row_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "case-plans.jsonl").write_text(json.dumps({
                "case_id": "c1", "au_product_id": "p1", "split": "test",
                "row_keys": ["r1"], "total_au_rows": 1}) + "\n", encoding="utf-8")
            (path / "tasks.jsonl").write_text(json.dumps({
                "task_id": "t1", "case_id": "c1", "au_row_key": "r1",
                "request_id": "q1", "direction": "rakuten_to_au"}) + "\n", encoding="utf-8")
            (path / "requests.jsonl").write_text(json.dumps({"request_id": "q1"}) + "\n", encoding="utf-8")
            (path / "row-plans.jsonl").write_text(json.dumps({
                "case_id": "c1", "au_row_key": "r1", "rakuten_axis_count": 1,
                "au_axis_count": 0, "bindings": [{"task_id": "foreign"}]}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fixed row/task/request source binding mismatch"):
                proofs.validate_full_coverage(path)

    def test_row_direction_counts_must_cover_both_source_axes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "case-plans.jsonl").write_text(json.dumps({
                "case_id": "c1", "au_product_id": "p1", "split": "test",
                "row_keys": ["r1"], "total_au_rows": 1}) + "\n", encoding="utf-8")
            (path / "tasks.jsonl").write_text(json.dumps({
                "task_id": "t1", "case_id": "c1", "au_row_key": "r1",
                "request_id": "q1", "direction": "rakuten_to_au"}) + "\n", encoding="utf-8")
            (path / "requests.jsonl").write_text(json.dumps({"request_id": "q1"}) + "\n", encoding="utf-8")
            (path / "row-plans.jsonl").write_text(json.dumps({
                "case_id": "c1", "au_row_key": "r1", "rakuten_axis_count": 1,
                "au_axis_count": 1, "bindings": [{"task_id": "t1"}]}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "full source task bindings"):
                proofs.validate_full_coverage(path)


if __name__ == "__main__":
    unittest.main()
