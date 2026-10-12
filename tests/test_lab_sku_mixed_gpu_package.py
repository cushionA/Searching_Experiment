"""Synthetic packaging boundary checks; no private records or model weights."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKU = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU))
SPEC = importlib.util.spec_from_file_location("package_mixed_gpu_sku_v1", SKU / "package_mixed_gpu_sku_v1.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MixedGpuPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prepared = self.root / "prepared"
        self.prepared.mkdir()
        self.contract = self.root / "contract.json"
        self.contract.write_text(json.dumps({"basis": "user_asserted", "eligible_forward_request_ids": ["r1"],
            "sold_out_rows_excluded_upstream": False}), encoding="utf-8")
        output_sha = {}
        for name in P.SOURCE_NAMES[1:]:
            path = self.prepared / name
            path.write_text("synthetic source placeholder\n", encoding="utf-8")
            output_sha[name] = sha(path)
        (self.prepared / "membership.jsonl").write_text('{"private":"host-only"}\n', encoding="utf-8")
        (self.prepared / "labels.jsonl").write_text('{"label":"host-only"}\n', encoding="utf-8")
        (self.prepared / "manifest.json").write_text(json.dumps({"schema_version": "fixture-real-v1",
            "synthetic": False, "labels_read": False, "labels_in_model_inputs": False,
            "independent_test": False, "output_sha256": output_sha}), encoding="utf-8")
        self.output = self.root / "package"

    def tearDown(self):
        self.tmp.cleanup()

    def _package(self):
        with patch.object(P.RUNNER, "development_loader", return_value=lambda _: ([{"request_id":"r1", "direction":"rakuten_to_au"}],)), \
             patch.object(P.RUNNER.TASK.SHARING, "_validate_contract", return_value={"r1"}), \
             patch.object(P.RUNNER.TASK, "verify_contract_sources", return_value=None):
            return P.package(self.prepared, self.contract, self.output,
                dataset_id="owner/mixed-input", notebook_id="owner/mixed-gpu-run")

    def test_private_bundle_has_exact_allowlisted_inputs_code_and_no_labels_or_host_audit(self):
        receipt = self._package()
        payload = self.output / "payload"
        manifest = json.loads((payload / "BUNDLE_MANIFEST.json").read_text())
        expected = {"input/" + name for name in P.SOURCE_NAMES}
        expected.add("eligible-requests.json")
        expected.update("code/" + name for name in P.CODE_NAMES)
        expected.add("code/" + P.RERANKER_MANIFEST)
        self.assertEqual(set(manifest["files"]), expected)
        self.assertNotIn("input/membership.jsonl", manifest["files"])
        self.assertNotIn("input/labels.jsonl", manifest["files"])
        self.assertFalse(manifest["labels_included"])
        self.assertFalse(manifest["synthetic_data"])
        self.assertTrue(manifest["private_dataset"])
        self.assertEqual(receipt["source_file_names"], list(P.SOURCE_NAMES))
        with zipfile.ZipFile(self.output / receipt["archive"]) as archive:
            self.assertEqual(set(archive.namelist()), expected | {"BUNDLE_MANIFEST.json"})
            self.assertNotIn("input/membership.jsonl", archive.namelist())
            self.assertNotIn("input/labels.jsonl", archive.namelist())

    def test_notebook_payload_is_small_and_metadata_is_private_gpu_internet_dataset_bound(self):
        receipt = self._package()
        notebook_path = self.output / "notebook-upload/mixed_gpu_sku.ipynb"
        metadata = json.loads((self.output / "notebook-upload/kernel-metadata.json").read_text())
        notebook = json.loads(notebook_path.read_text())
        self.assertLess(receipt["notebook_bytes"], 1024 * 1024)
        self.assertTrue(metadata["is_private"])
        self.assertTrue(metadata["enable_gpu"])
        self.assertFalse(metadata["enable_tpu"])
        self.assertTrue(metadata["enable_internet"])
        self.assertEqual(metadata["dataset_sources"], ["owner/mixed-input"])
        self.assertEqual(json.loads((self.output / "notebook-upload/training-params.json").read_text()),
                         {"timeout_seconds": 3600})
        code = "".join(notebook["cells"][1]["source"])
        for required in ("BUNDLE_MANIFEST.json", "sha(path)", "transformers==5.17.0",
                         "tokenizers==0.23.3", "onnxruntime==1.23.2", "/tmp/mixed-gpu-sku",
                         "stage_begin", "stage_end", "parity44"):
            self.assertIn(required, code)
        self.assertNotIn("/kaggle/working/product-condition-weights", code)

    def test_prepared_source_sha_mismatch_fails_closed(self):
        (self.prepared / P.SOURCE_NAMES[1]).write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self._package()
        self.assertFalse(self.output.exists())

    def test_optional_parity44_inputs_are_packed_separately_without_label_files(self):
        parity = self.root / "parity"
        parity.mkdir()
        output_sha = {}
        for name in P.SOURCE_NAMES[1:]:
            path = parity / name
            path.write_text("parity source\n", encoding="utf-8")
            output_sha[name] = sha(path)
        (parity / "manifest.json").write_text(json.dumps({"schema_version":"parity-fixture-v1",
            "synthetic":False, "labels_read":False, "labels_in_model_inputs":False,
            "independent_test":False, "output_sha256":output_sha}), encoding="utf-8")
        parity_contract = self.root / "parity-contract.json"
        parity_contract.write_text(self.contract.read_text())
        with patch.object(P.RUNNER, "development_loader", return_value=lambda _: ([{"request_id":"r1", "direction":"rakuten_to_au"}],)), \
             patch.object(P.RUNNER.TASK.SHARING, "_validate_contract", return_value={"r1"}), \
             patch.object(P.RUNNER.TASK, "verify_contract_sources", return_value=None):
            P.package(self.prepared, self.contract, self.output, dataset_id="owner/mixed-input",
                notebook_id="owner/mixed-gpu-run", parity_input_dir=parity,
                parity_contract_path=parity_contract)
        manifest = json.loads((self.output / "payload/BUNDLE_MANIFEST.json").read_text())
        self.assertIn("parity44/input/manifest.json", manifest["files"])
        self.assertIn("parity44/eligible-requests.json", manifest["files"])
        self.assertFalse(any("membership" in name or "labels" in name for name in manifest["files"]))


if __name__ == "__main__":
    unittest.main()
