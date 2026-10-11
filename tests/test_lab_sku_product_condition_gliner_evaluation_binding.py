"""Integrity tests use synthetic outputs only, without a model or reference labels."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import evaluate_product_condition_gliner_v2 as R


def completed_fixture(root):
    run = root / "run"
    run.mkdir()
    required = {"sharing-plan.json", "sharing-contract.json", "model-requests.jsonl"}
    required.update(v + "-predictions.jsonl" for v in R.VARIANTS)
    required.update(v + "-group-results.jsonl" for v in R.VARIANTS[1:])
    for name in required:
        (run / name).write_text("{}\n")
    freeze = {"schema_version": "product-condition-gliner-v1", "labels_read": False,
              "variants": list(R.VARIANTS[1:]), "code_sha256": {},
              "model_requests_sha256": R.E.sha(run / "model-requests.jsonl"),
              "new_sharing_plan_sha256": R.E.sha(run / "sharing-plan.json")}
    R.E.write_json(run / "freeze.json", freeze)
    R.E.write_json(run / "summary.json", {**freeze,
        "input_and_code_sha256_verified_after_inference": True,
        "output_sha256": {p.name: R.E.sha(p) for p in run.iterdir() if p.is_file()}})
    return run


class ProductGlinerEvaluationBindingTests(unittest.TestCase):
    def test_completed_manifest_verified_without_labels(self):
        with tempfile.TemporaryDirectory() as td:
            result = R.verify_completed_run(completed_fixture(Path(td)))
            self.assertTrue(result["output_manifest_sha256_verified"])
            self.assertFalse(result["labels_read"])

    def test_pre_evaluation_modified_prediction_is_rejected_before_driver(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = completed_fixture(root)
            (run / "product-top1-gliner-predictions.jsonl").write_text('{"changed":true}\n')
            with patch.object(R.DRIVER, "run") as driver, self.assertRaisesRegex(ValueError, "output SHA256"):
                R.run(root / "unused", run, root / "unused-label", root / "unused-label", root / "evaluation")
            driver.assert_not_called()
            self.assertFalse((root / "evaluation").exists())

    def test_manifest_cannot_omit_variant_or_replace_freeze(self):
        for omit in (True, False):
            with tempfile.TemporaryDirectory() as td:
                run = completed_fixture(Path(td))
                summary = json.loads((run / "summary.json").read_text())
                if omit:
                    del summary["output_sha256"]["product-top1-gliner-predictions.jsonl"]
                else:
                    summary["variants"] = ["different-policy"]
                (run / "summary.json").write_text(json.dumps(summary))
                with self.assertRaises(ValueError):
                    R.verify_completed_run(run)

    def test_binding_freezes_before_driver_and_reports_fixed_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run, output = completed_fixture(root), root / "evaluation"
            def driver(*args):
                frozen = json.loads((output / "completed-run-binding-freeze.json").read_text())
                self.assertFalse(frozen["labels_read"])
                self.assertTrue(frozen["output_manifest_sha256_verified"])
                args[-1].mkdir()
                return {"full_dataset_accuracy_measured": False, "variants": {}}
            with patch.object(R.DRIVER, "run", side_effect=driver):
                result = R.run(root / "unused", run, root / "labels", root / "labels", output)
            self.assertTrue(result["completed_run_binding"]["output_manifest_sha256_verified"])
            self.assertFalse(result["full_dataset_accuracy_measured"])


if __name__ == "__main__":
    unittest.main()
