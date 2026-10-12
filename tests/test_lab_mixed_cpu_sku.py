"""Synthetic CPU fallback contract tests with no models or labels."""
import importlib.util
import json
import inspect
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("mixed_cpu_runner", HERE / "trial_mixed_cpu_sku_v1.py")
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


class MixedCpuSkuTests(unittest.TestCase):
    def make_inputs(self, root):
        inputs = root / "prepared"
        inputs.mkdir()
        source_names = C.V1.SOURCE_NAMES
        content = {
            "requests.jsonl": [{"request_id": "r1"}, {"request_id": "r2"}],
            "documents.jsonl": [{"document_id": "d"}],
            "tasks.jsonl": [{"task_id": "t"}],
            "blocks.jsonl": [{"block_id": "b"}],
            "retrieval-requests.jsonl": [{"id": "q"}],
            "baseline-predictions.jsonl": [
                {"request_id": "r1", "source_request": {"request_id": "r1"}, "source_task_bindings": [{"task_id": "t1"}]},
                {"request_id": "r2", "source_request": {"request_id": "r2"}, "source_task_bindings": [{"task_id": "t2"}]},
            ],
        }
        for name in source_names:
            C.write_rows(inputs / name, content[name])
        manifest = {"labels_read": False, "labels_in_model_inputs": False, "independent_test": False,
                    "synthetic": False, "development_only": True, "production_adoption_allowed": False,
                    "errors_only": False, "output_sha256": {name: C.sha(inputs / name) for name in source_names}}
        C.write_json(inputs / "manifest.json", manifest)
        contract = {"basis": "user_asserted", "eligible_forward_request_ids": [],
                    "source_sha256": {"prepared": {name: C.sha(inputs / name) for name in
                        ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl")}}}
        contract_path = root / "eligible-requests.json"
        C.write_json(contract_path, contract)
        return inputs, contract_path, content

    def test_fresh_baseline_rebuild_requires_complete_original_request_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inputs, contract, content = self.make_inputs(root)
            predictions = [
                {**row, "state": "unknown"}
                for row in content["baseline-predictions.jsonl"]
            ]
            refreshed, refreshed_contract, refresh = C.assemble_refreshed_inputs(
                inputs, contract, root / "refresh/prepared", predictions, {"request_count": 2})
            output = {row["request_id"]: row for row in C.rows(refreshed / "baseline-predictions.jsonl")}
            self.assertEqual(set(output), {"r1", "r2"})
            self.assertEqual(output["r2"]["source_task_bindings"], [{"task_id": "t2"}])
            self.assertEqual(refresh["device"], "cpu")
            self.assertEqual(refresh["request_count"], 2)
            manifest = json.loads((refreshed / "manifest.json").read_text())
            self.assertFalse(manifest["baseline_must_be_regenerated_for_all_requests"])
            new_contract = json.loads(refreshed_contract.read_text())
            self.assertEqual(new_contract["source_sha256"]["prepared"]["baseline-predictions.jsonl"],
                             C.sha(refreshed / "baseline-predictions.jsonl"))
            with self.assertRaisesRegex(ValueError, "every original request"):
                C.assemble_refreshed_inputs(inputs, contract, root / "short/prepared", predictions[:1],
                                            {"request_count": 1})

    def test_duplicate_or_changed_source_baseline_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inputs, contract, content = self.make_inputs(root)
            original = content["baseline-predictions.jsonl"]
            duplicate = [{**original[0], "state": "unknown"}, {**original[0], "state": "support"}]
            with self.assertRaisesRegex(ValueError, "duplicate or out-of-scope"):
                C.assemble_refreshed_inputs(inputs, contract, root / "duplicate/prepared", duplicate,
                                            {"request_count": 2})
            changed = [{**original[0], "source_request": {"request_id": "wrong"}},
                       {**original[1], "state": "support"}]
            with self.assertRaisesRegex(ValueError, "exact source request"):
                C.assemble_refreshed_inputs(inputs, contract, root / "changed/prepared", changed,
                                            {"request_count": 2})

    def test_run_executes_cpu_baseline_then_product_on_refreshed_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inputs, contract, content = self.make_inputs(root)
            observed = {}

            def baseline_runner(input_dir, output_dir, **kwargs):
                observed["baseline_input"] = Path(input_dir)
                output_dir.mkdir(parents=True)
                predictions = [{**row, "state": "unknown"} for row in content["baseline-predictions.jsonl"]]
                C.write_rows(output_dir / "fusion-predictions.jsonl", predictions)
                C.write_json(output_dir / "summary.json", {"request_count": 2})
                return {"request_count": 2}

            self.assertEqual(len(inspect.signature(C.PRODUCT.run).bind(None, None, None).arguments), 3)

            def product_runner(input_dir, eligible_path, output_dir, **kwargs):
                observed["product_input"] = Path(input_dir)
                observed["product_contract"] = Path(eligible_path)
                self.assertNotEqual(Path(input_dir), inputs)
                self.assertEqual(len(list(C.rows(Path(input_dir) / "baseline-predictions.jsonl"))), 2)
                output_dir.mkdir(parents=True)
                for variant in C.PRODUCT.VARIANTS:
                    C.write_rows(output_dir / (variant + "-predictions.jsonl"), [])
                for name, value in (("product-groups.jsonl", []), ("sharing-plan.json", {}),
                                    ("sharing-contract.json", {})):
                    if name.endswith(".jsonl"):
                        C.write_rows(output_dir / name, value)
                    else:
                        C.write_json(output_dir / name, value)
                summary = {"labels_read": False, "independent_test": False, "request_count": 2,
                           "prediction_states": {}, "verified_jev_pin": {}, "verified_nli_pin": {}}
                C.write_json(output_dir / "summary.json", summary)
                return summary

            with patch.object(C, "widened_loader", return_value=lambda *_: None), \
                 patch.object(C.PRODUCT, "verify_contract_sources", return_value=None):
                result = C.run(inputs, contract, root / "cpu-run", baseline_runner=baseline_runner,
                               product_runner=product_runner)
            self.assertEqual(result["baseline_mode"], "fresh-cpu-fusion")
            self.assertEqual(result["stage_devices"], {"reranker": "cpu", "jev": "cpu", "nli": "cpu"})
            self.assertEqual(result["baseline_refresh_summary"]["request_count"], 2)
            self.assertTrue((root / "cpu-run/cpu-source-freeze.json").exists())
            self.assertTrue((root / "cpu-run/execution-freeze.json").exists())
            self.assertEqual(observed["product_input"], root / "cpu-run/baseline-refresh/prepared")
            self.assertEqual(observed["product_contract"], root / "cpu-run/baseline-refresh/eligible-requests.json")
            self.assertIn("trial_mixed_cpu_sku_v1.py",
                          json.loads((root / "cpu-run/cpu-source-freeze.json").read_text())["code_sha256"])


if __name__ == "__main__":
    unittest.main()
