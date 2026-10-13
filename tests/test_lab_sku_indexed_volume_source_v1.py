from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/prepare_indexed_volume_source_v1.py"
SPEC = importlib.util.spec_from_file_location("volume_source", MODULE_PATH)
volume = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(volume)


def put_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, sort_keys=True) + "\n", encoding="utf-8")


def put_rows(path: Path, items) -> None:
    path.write_text("".join(json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n" for x in items), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class VolumeSourceScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "source"
        self.input.mkdir()
        self.products = [f"p{i:02d}" for i in range(46)]
        self.cases = [{"case_id": f"c{i:03d}", "au_product_id": self.products[i % 46]} for i in range(569)]
        self.tasks = [{"task_id": f"t{i:04d}", "case_id": c["case_id"], "au_row_key": f"row{i}",
                       "request_id": f"r{i:04d}", "direction": "au_to_rakuten", "axis_key": f"axis{i}"}
                      for i, c in enumerate(self.cases)]
        self.rowplans = [{"case_id": c["case_id"], "au_row_key": f"row{i}", "bindings": [{
            "task_id": f"t{i:04d}", "request_id": f"r{i:04d}",
            "direction": "au_to_rakuten", "axis_key": f"axis{i}"}]} for i, c in enumerate(self.cases)]
        self.membership = [{"case_id": c["case_id"], "au_product_id": c["au_product_id"]} for c in self.cases]
        self.files = {
            "case-plans.jsonl": self.cases,
            "membership.jsonl": self.membership,
            "row-plans.jsonl": self.rowplans,
            "tasks.jsonl": self.tasks,
            "requests.jsonl": [{"request_id": f"r{i:04d}"} for i in range(569)],
            "documents.jsonl": [{"document_id": "d", "document_sha256": "a" * 64}],
        }
        for name, values in self.files.items():
            put_rows(self.input / name, values)
        put_json(self.input / "selection-policy.json", {"labels_read": False})
        put_json(self.input / "selection-plan.json", {"identity_only": True})
        self.config = {"dataset": "sku-next-body-indexed-test-20261013-v1",
            "selected_product_count": 46, "selected_case_count": 569,
            "identity_plan_manifest_sha256": "1" * 64,
            "selected_membership_sha256": "2" * 64,
            "identity_bridge_sha256": "3" * 64}
        self.manifest = {"schema_version": "sku-next-body-indexed-test-source-v1",
            "dataset": self.config["dataset"], "split": "test", "independent_test": True,
            "selected_product_count": 46, "selected_case_count": 569,
            "row_count": 569, "task_count": 569, "derived_request_count": 569,
            "document_count": 1, "outputs_sha256": {name: digest(self.input / name) for name in self.files},
            "selection_policy_sha256": digest(self.input / "selection-policy.json"),
            "selection_plan_sha256": digest(self.input / "selection-plan.json"),
            "identity_plan_manifest_sha256": self.config["identity_plan_manifest_sha256"],
            "selected_membership_sha256": self.config["selected_membership_sha256"],
            "identity_bridge_sha256": self.config["identity_bridge_sha256"],
            "labels_read": False, "predictions_read": False, "model_inference_run": False,
            "scores_created": False, "test_split_read": True, "production_adoption_allowed": False}
        put_json(self.input / "manifest.json", self.manifest)

    def test_accepts_variable_volume_and_preserves_dynamic_identity(self):
        result = volume.scope_source_manifest(self.input, self.config)
        self.assertEqual((result["selected_product_count"], result["selected_case_count"]), (46, 569))
        self.assertEqual(result["schema_version"], volume.PROTOCOL)
        self.assertEqual(result["identity_plan_manifest_sha256"], "1" * 64)
        self.assertEqual(result["selected_membership_sha256"], "2" * 64)
        self.assertEqual(result["identity_bridge_sha256"], "3" * 64)

    def test_rejects_wrong_identity_sha_and_tampered_source_file(self):
        bad_config = dict(self.config, selected_membership_sha256="f" * 64)
        with self.assertRaisesRegex(ValueError, "selected_membership_sha256"):
            volume.scope_source_manifest(self.input, bad_config)
        with (self.input / "tasks.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("{}\n")
        with self.assertRaisesRegex(ValueError, "output SHA mismatch"):
            volume.scope_source_manifest(self.input, self.config)

    def test_rejects_duplicate_or_excluded_membership_and_missing_task_coverage(self):
        duplicate = self.membership + [self.membership[0]]
        put_rows(self.input / "membership.jsonl", duplicate)
        # Refresh its output pin so this tests identity semantics, not hash detection.
        self.manifest["outputs_sha256"]["membership.jsonl"] = digest(self.input / "membership.jsonl")
        put_json(self.input / "manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "membership"):
            volume.scope_source_manifest(self.input, self.config)

        put_rows(self.input / "membership.jsonl", self.membership)
        self.manifest["outputs_sha256"]["membership.jsonl"] = digest(self.input / "membership.jsonl")
        put_rows(self.input / "row-plans.jsonl", self.rowplans[:-1])
        self.manifest["outputs_sha256"]["row-plans.jsonl"] = digest(self.input / "row-plans.jsonl")
        self.manifest["row_count"] = 568
        put_json(self.input / "manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "row/task source coverage"):
            volume.scope_source_manifest(self.input, self.config)

    def test_rejects_missing_all_task_source_coverage(self):
        altered = [dict(row, bindings=[]) if i == 17 else row for i, row in enumerate(self.rowplans)]
        put_rows(self.input / "row-plans.jsonl", altered)
        self.manifest["outputs_sha256"]["row-plans.jsonl"] = digest(self.input / "row-plans.jsonl")
        put_json(self.input / "manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "row/task source coverage"):
            volume.scope_source_manifest(self.input, self.config)

    def test_scoped_manifest_stamps_all_identity_and_capture_pins(self):
        report = {"schema_version": volume.PROTOCOL}
        source_manifest = {"raw_source_capture_inventory_sha256": "4" * 64}
        volume._stamp_scoped_manifest(report, self.config, source_manifest)
        self.assertEqual(report["identity_plan_manifest_sha256"], "1" * 64)
        self.assertEqual(report["selected_membership_sha256"], "2" * 64)
        self.assertEqual(report["identity_bridge_sha256"], "3" * 64)
        self.assertEqual(report["raw_source_capture_inventory_sha256"], "4" * 64)


if __name__ == "__main__":
    unittest.main()
