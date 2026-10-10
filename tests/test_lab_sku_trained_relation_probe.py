"""Frozen-input and backend-parity safeguards without model inference."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "trained_relation_probe_tests", ROOT / "experiments/sku-matching/trial_trained_relation_probe_v1.py")
TRIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIAL)


class TrainedRelationProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input"
        self.input.mkdir()
        # Artificial contract fixture, never a collected SKU or gold label.
        self.request = {
            "id": "synthetic-contract:1", "question": "この仮説は状況から導けますか？",
            "state": "架空の原文  そのまま\n保持", "premise": "架空の原文  そのまま\n保持",
            "hypothesis": "架空の商品は特殊な仕様である。",
            "choices": [{"label": "含意", "description": ""},
                        {"label": "中立", "description": ""},
                        {"label": "矛盾", "description": ""}],
            "provenance": {"case_id": "synthetic-case", "au_row_key": "fixed:row",
                           "fixed_au_url": "https://example.invalid/fixed-product",
                           "raw_value": "仕様（全部）", "all_alternatives": ["仕様（全部）", "別仕様"],
                           "span": {"start": 4, "end": 12, "quote": "架空の仕様"}},
        }
        self.freeze_input([self.request])
        # Poison files establish that execution never tries to parse labels.
        (self.input / "annotations.jsonl").write_text("not JSON", encoding="utf-8")
        (self.input / "labels.json").write_text("not JSON", encoding="utf-8")

    def freeze_input(self, requests):
        path = self.input / "requests.jsonl"
        path.unlink(missing_ok=True)
        TRIAL.write(path, requests)
        (self.input / "manifest.json").write_text(json.dumps({
            "output_sha256": {"requests.jsonl": TRIAL.sha(path)}, "labels_read": False,
        }), encoding="utf-8")

    def stub(self, output, backend, mutate=None):
        def predict(requests, **kwargs):
            frozen = json.loads((output / "freeze.json").read_text())
            self.assertEqual(frozen["input_sha256"]["requests.jsonl"], TRIAL.sha(self.input / "requests.jsonl"))
            self.assertEqual(frozen["input_sha256"]["manifest.json"], TRIAL.sha(self.input / "manifest.json"))
            self.assertEqual((output / "requests.jsonl").read_bytes(), (self.input / "requests.jsonl").read_bytes())
            self.assertEqual((output / "manifest.json").read_bytes(), (self.input / "manifest.json").read_bytes())
            for name, digest in frozen["code_sha256"].items():
                self.assertEqual(TRIAL.sha(output / "code" / name), digest)
            self.assertFalse(frozen["labels_read"])
            self.assertFalse(frozen["scope_proven"])
            self.assertFalse(frozen["production_eligible"])
            self.assertEqual(kwargs["threads"], 2)
            self.assertEqual(kwargs["batch_size"], 8)
            self.assertEqual(requests[0]["provenance"], self.request["provenance"])
            self.assertEqual(requests[0]["question"], self.request["question"])
            self.assertEqual(requests[0]["choices"], self.request["choices"])
            self.assertEqual(requests[0]["premise"], self.request["premise"])
            self.assertEqual(requests[0]["hypothesis"], self.request["hypothesis"])
            if backend == "nli":
                self.assertEqual(requests[0]["id"], requests[0]["request_id"])
                self.assertEqual(frozen["pair_sha256"], TRIAL.sha(output / "pairs.jsonl"))
            rows = [{**deepcopy(request), "status": "ok"} for request in requests]
            if mutate:
                mutate(rows)
            return {"records": rows, "pins": {"model_id": "mock-pin"}, "runtime": {"device": "cpu"}}
        return SimpleNamespace(**{"run_choices" if backend == "jev" else "run_pairs": predict})

    def test_both_backends_preserve_identical_source_ids_text_and_metadata(self):
        predictions = {}
        for backend in ("jev", "nli"):
            output = self.root / backend
            with patch.object(TRIAL, "load", return_value=self.stub(output, backend)) as loader:
                result = TRIAL.run(self.input, output, backend=backend)
            loader.assert_called_once_with(TRIAL.BACKENDS[backend])
            predictions[backend] = TRIAL.read(output / "predictions.jsonl")
            self.assertEqual(result["request_count"], 1)
            self.assertEqual(result["prediction_count"], 1)
            self.assertEqual(result["thresholds_for_diagnostics"], [0.5, 0.7, 0.9])
            self.assertEqual(result["primary_threshold"], 0.9)
            self.assertFalse((output / "annotations.jsonl").exists())
            self.assertFalse((output / "labels.json").exists())
            for name, digest in result["output_sha256"].items():
                self.assertEqual(TRIAL.sha(output / name), digest)
        for field, value in self.request.items():
            self.assertEqual(predictions["jev"][0][field], value)
            self.assertEqual(predictions["nli"][0][field], value)

    def test_request_hash_failure_prevents_output_and_backend_loading(self):
        with (self.input / "requests.jsonl").open("a") as stream:
            stream.write("{}\n")
        output = self.root / "run"
        with patch.object(TRIAL, "load") as loader:
            with self.assertRaisesRegex(ValueError, "request manifest SHA"):
                TRIAL.run(self.input, output)
        loader.assert_not_called()
        self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self):
        output = self.root / "run"
        output.mkdir()
        marker = output / "retained.txt"
        marker.write_text("keep")
        with patch.object(TRIAL, "load") as loader:
            with self.assertRaises(FileExistsError):
                TRIAL.run(self.input, output)
        loader.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")

    def test_duplicate_source_ids_rejected_before_output_creation(self):
        self.freeze_input([self.request, self.request])
        output = self.root / "run"
        with self.assertRaisesRegex(ValueError, "duplicate request id"):
            TRIAL.run(self.input, output)
        self.assertFalse(output.exists())

    def test_nonfinite_metadata_rejected_before_output_creation(self):
        raw = json.dumps([{**self.request, "bad_metadata": float("nan")}][0], ensure_ascii=False) + "\n"
        path = self.input / "requests.jsonl"
        path.write_text(raw, encoding="utf-8")
        (self.input / "manifest.json").write_text(json.dumps({"output_sha256": {"requests.jsonl": TRIAL.sha(path)}}))
        output = self.root / "run"
        with self.assertRaisesRegex(ValueError, "nonfinite JSON"):
            TRIAL.run(self.input, output)
        self.assertFalse(output.exists())

    def test_backend_cannot_change_fixed_au_binding_or_composite_value(self):
        output = self.root / "run"
        def mutate(rows):
            rows[0]["provenance"]["raw_value"] = "仕様"
            rows[0]["provenance"]["fixed_au_url"] = "https://example.invalid/sibling"
        with patch.object(TRIAL, "load", return_value=self.stub(output, "jev", mutate)):
            with self.assertRaisesRegex(ValueError, "backend changed.*provenance"):
                TRIAL.run(self.input, output)
        self.assertTrue((output / "freeze.json").exists())
        self.assertFalse((output / "predictions.jsonl").exists())
        self.assertFalse((output / "summary.json").exists())

    def test_invalid_runtime_or_backend_rejected_before_output_creation(self):
        for kwargs in ({"threads": 0}, {"batch_size": 0}, {"threads": True}, {"backend": "other"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TRIAL.run(self.input, self.root / "run", **kwargs)
        self.assertFalse((self.root / "run").exists())

    def test_request_pair_binding_is_one_to_one_not_per_choice(self):
        output = self.root / "run"
        with patch.object(TRIAL, "load", return_value=self.stub(output, "nli")):
            TRIAL.run(self.input, output, backend="nli")
        pairs = TRIAL.read(output / "pairs.jsonl")
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["id"], self.request["id"])
        self.assertEqual(pairs[0]["request_id"], self.request["id"])


if __name__ == "__main__":
    unittest.main()
