import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKU = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(SKU))
import run_fresh_dev_baseline_v1 as fresh
import trial_generic_model_jev_v1 as JEV
import trial_generic_model_nli_v1 as NLI

INPUTS = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v2"
JEV_DIR = ROOT / ".deps/sku-fixed-models/jev"
NLI_DIR = ROOT / ".deps/sku-fixed-models/nli"


class FreshDevBaselineTests(unittest.TestCase):
    def test_frozen_subset_is_dev_and_source_binding_hydrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepared = pathlib.Path(tmp) / "inputs"
            receipt = fresh.prepare_dev_baseline_inputs(INPUTS, prepared)
            self.assertEqual(receipt["split"], "dev")
            self.assertFalse(receipt["independent_test"])
            self.assertFalse(receipt["labels_read"])
            self.assertEqual(receipt["selected_case_count"], 40)
            self.assertEqual(receipt["selected_row_count"], 504)
            self.assertEqual(receipt["selected_task_count"], 1924)
            self.assertEqual(receipt["unique_request_count"], 1045)
            self.assertFalse(receipt["target_selected_value_in_model_input"])

            requests, documents, bindings, manifest, _ = fresh.load_adapter_v2_inputs(prepared)
            self.assertEqual(len(requests), 1045)
            self.assertEqual(len(documents), 40)
            self.assertFalse(manifest["labels_read"])
            by_id = {r["request_id"]: r for r in requests}
            for request_id, tasks in bindings.items():
                req = by_id[request_id]
                conditions = {t["raw_condition_ref"]["value"] for t in tasks}
                self.assertEqual(conditions, {req["selected_value"]})
                self.assertTrue(req["selected_value_is_binding_only"])
            # Model-facing identity excludes the desired source value entirely.
            model_inputs = fresh.read(prepared / "baseline-model-inputs.jsonl")
            self.assertTrue(all(x.get("selected_value_in_model_input") is False for x in model_inputs))
            self.assertTrue(all("selected_value" not in x for x in model_inputs))

    def test_dev_membership_guard_rejects_test_split_read(self):
        manifest = json.loads((INPUTS / "manifest.json").read_text(encoding="utf-8"))
        valid = fresh.validate_dev_subset(INPUTS, manifest)
        self.assertEqual(valid["split"], "dev")
        self.assertFalse(valid["independent_test"])
        invalid = dict(manifest)
        invalid["test_split_read"] = True
        with self.assertRaisesRegex(ValueError, "explicit label/test-read"):
            fresh.validate_dev_subset(INPUTS, invalid)

    def test_exact_local_jev_nli_model_pins(self):
        pins = fresh.verify_local_models(JEV_DIR, NLI_DIR)
        self.assertEqual(pins["device"], "cpu")
        self.assertEqual(pins["precision"], "fp32")
        jev_pin = JEV.verify_model_files(JEV_DIR)
        self.assertEqual(jev_pin["model_id"], JEV.MODEL_ID)
        self.assertEqual(jev_pin["revision"], JEV.MODEL_REVISION)
        self.assertEqual(jev_pin["file_sha256"], JEV.JEV_FILE_SHA256)
        for name, expected in NLI.NLI_FILE_SHA256.items():
            self.assertTrue((NLI_DIR / name).is_file())
            self.assertEqual(fresh.sha(NLI_DIR / name), expected, name)


if __name__ == "__main__":
    unittest.main()
