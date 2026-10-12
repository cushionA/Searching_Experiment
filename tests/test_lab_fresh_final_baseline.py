import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
import run_fresh_baseline_final_subset_v1 as runtime


class FreshFinalBaselineInputTests(unittest.TestCase):
    def fixture(self, root):
        doc = {"document_id": "doc-1", "side": "au", "title": {"text": "品名"},
               "description": {"text": "本文", "complete": True}}
        doc["document_sha256"] = runtime.digest(doc)
        request = {
            "request_id": "", "request_sha256": "", "direction": "rakuten_to_au",
            "axis_name": "色", "selected_value": "青", "option_values": ["青", "赤"],
            "target_document_id": "doc-1", "target_document_sha256": doc["document_sha256"],
            "target_selected_axes": [{"axis_name": "色", "value": "青"}],
            "target_selected_axes_text": "色：青", "production_adoption_allowed": False,
            "selected_value_is_binding_only": True,
        }
        request_fields = {key: request[key] for key in runtime.FULL.validate_request.__globals__["SEMANTIC_FIELDS"]}
        request_sha = runtime.digest(request_fields)
        request["request_sha256"] = request_sha
        request["request_id"] = "generic-description:" + request_sha
        del request["selected_value"]
        model_fields = ("direction", "axis_name", "option_values", "target_document_id",
                        "target_document_sha256", "target_selected_axes", "target_selected_axes_text")
        model_sha = runtime.digest({field: request[field] for field in model_fields})
        model_id = "condition-model-input:" + model_sha
        request["model_input_id"] = model_id
        task = {"task_id": "task-1", "request_id": request["request_id"],
                "model_input_id": model_id, "direction": request["direction"], "status": "not_evaluated",
                "raw_condition_ref": {"axis_label": request["axis_name"], "value": "青"},
                "production_adoption_allowed": False}
        input_row = {"model_input_id": model_id, "model_input_sha256": model_sha,
                     "model_input_version": "fixed-condition-whole-options-v2",
                     "direction": request["direction"], "axis_name": request["axis_name"],
                     "option_values": request["option_values"], "selected_value_in_model_input": False,
                     "target_document_id": request["target_document_id"],
                     "target_document_sha256": request["target_document_sha256"],
                     "target_selected_axes": request["target_selected_axes"],
                     "target_selected_axes_text": request["target_selected_axes_text"]}
        mapping = {"request_id": request["request_id"], "model_input_id": model_id}
        rows = {"requests.jsonl": [request], "documents.jsonl": [doc], "tasks.jsonl": [task],
                "request-model-input-map.jsonl": [mapping], "baseline-model-inputs.jsonl": [input_row]}
        hashes = {}
        for name, values in rows.items():
            path = root / name
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values), encoding="utf-8")
            hashes[name] = runtime.sha(path)
        manifest = {"schema_version": "full-condition-inputs-v2", "labels_read": False,
                    "labels_in_model_inputs": False, "model_inference_run": False,
                    "request_count": 1, "document_count": 1, "task_count": 1,
                    "output_sha256": hashes}
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return request, rows

    def test_accepts_exact_adapter_model_input_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.fixture(path)
            requests, docs, bindings, manifest, names = runtime.load_adapter_v2_inputs(path)
            self.assertEqual(len(requests), 1)
            self.assertEqual(docs["doc-1"]["title"]["text"], "品名")
            self.assertEqual(requests[0]["selected_value"], "青")
            self.assertEqual(bindings[requests[0]["request_id"]][0]["task_id"], "task-1")
            self.assertEqual(names, ("requests.jsonl", "documents.jsonl", "tasks.jsonl"))
            self.assertFalse(manifest["labels_read"])

    def test_rejects_changed_model_input_options(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            request, rows = self.fixture(path)
            input_row = rows["baseline-model-inputs.jsonl"][0]
            input_row["option_values"] = ["青", "緑"]
            file_path = path / "baseline-model-inputs.jsonl"
            file_path.write_text(json.dumps(input_row, ensure_ascii=False) + "\n", encoding="utf-8")
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["output_sha256"]["baseline-model-inputs.jsonl"] = runtime.sha(file_path)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen baseline model input"):
                runtime.load_adapter_v2_inputs(path)

    def test_rejects_disagreeing_binding_only_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.fixture(path)
            task_path = path / "tasks.jsonl"
            task = json.loads(task_path.read_text(encoding="utf-8"))
            other = {**task, "task_id": "task-2",
                     "raw_condition_ref": {"axis_label": "色", "value": "赤"}}
            task_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in (task, other)) + "\n",
                                 encoding="utf-8")
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["task_count"] = 2
            manifest["output_sha256"]["tasks.jsonl"] = runtime.sha(task_path)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "request axis/value"):
                runtime.load_adapter_v2_inputs(path)


if __name__ == "__main__":
    unittest.main()
