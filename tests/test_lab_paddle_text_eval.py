"""Integrity and interruption checks for the recognition-only offline evaluator."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_missing = [] if importlib.util.find_spec('PIL') is not None else ['Pillow']


ROOT = Path(__file__).resolve().parents[1]
class FakeOCR:
    predictions = {1: "ab1", 2: " E ", 3: "X"}
    interrupt_pixel = None

    def __init__(self, model_path, model):
        self.load_seconds = 0.125
        self.providers = ["CPUExecutionProvider"]
        self.model_metadata = {"test_model": True}

    def predict(self, image):
        pixel = image.getpixel((0, 0))[0]
        if pixel == self.interrupt_pixel:
            raise KeyboardInterrupt()
        return self.predictions[pixel], 0.001


@unittest.skipIf(_missing, f"requires optional dependencies: {', '.join(_missing)}")
class PaddleEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global Image, module
        from PIL import Image
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            'paddle_text_eval', root / 'experiments/captcha-small-model/evaluate_paddle_text.py')
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        model_path = self.root / "test.onnx"
        model_path.write_bytes(b"offline-test-model")
        self.model = {"filename": "test.onnx", "version": "test", "size": "test", "language": "test",
                      "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest()}
        samples = []
        for i, label in enumerate(("Ab1", "E", "X"), 1):
            path = self.root / f"{i}.png"
            Image.new("RGB", (8, 4), (i, 0, 0)).save(path)
            samples.append({"id": str(i), "path": path.name, "label": label, "source": "test-source",
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps({"samples": samples}), encoding="utf-8")
        self.output = self.root / "results"
        self.addCleanup(patch.stopall)
        patch.dict(module.MODELS, {"test": self.model}).start()
        patch.object(module, "runtime_versions", return_value={"test": "1"}).start()
        patch.object(module, "PaddleTextOCR", FakeOCR).start()
        FakeOCR.interrupt_pixel = None

    def run_evaluation(self, **kwargs):
        return module.evaluate(self.manifest_path, self.output, "test", self.root, checkpoint_every=100, **kwargs)

    def read_results(self):
        summary = json.loads(self.output.with_suffix(".json").read_text())
        rows = [json.loads(line) for line in self.output.with_suffix(".jsonl").read_text().splitlines()]
        return summary, rows

    def test_raw_low_confidence_answers_and_casefold_are_scored_without_filtering(self):
        self.run_evaluation()
        summary, rows = self.read_results()
        self.assertEqual([row["answer"] for row in rows], ["ab1", " E ", "X"])
        self.assertEqual([row["distance"] for row in rows], [1, 2, 0])
        self.assertEqual(summary["literal_exact_match"], 1 / 3)
        self.assertEqual(summary["case_insensitive_exact_match"], 2 / 3)
        self.assertEqual(summary["character_error_rate"], 3 / 5)
        self.assertEqual(summary["failed_samples"], 0)
        self.assertEqual(summary["by_source"]["test-source"]["latency"]["samples"], 3)
        self.assertEqual(summary["status"], "complete")
        with self.assertRaises(FileExistsError):
            self.run_evaluation()

    def test_corrupt_image_is_an_explicit_empty_prediction_and_failure(self):
        damaged = self.root / "2.png"
        damaged.write_bytes(b"not-an-image")
        manifest = json.loads(self.manifest_path.read_text())
        manifest["samples"][1]["sha256"] = hashlib.sha256(damaged.read_bytes()).hexdigest()
        self.manifest_path.write_text(json.dumps(manifest))
        self.run_evaluation()
        summary, rows = self.read_results()
        self.assertEqual(rows[1]["answer"], "")
        self.assertEqual(rows[1]["distance"], 1)
        self.assertEqual(rows[1]["error"]["stage"], "image_open")
        self.assertEqual(summary["failed_samples"], 1)
        self.assertEqual(summary["by_source"]["test-source"]["failed_samples"], 1)

    def test_interrupted_checkpoint_resumes_and_rejects_tampered_manifest_or_rows(self):
        FakeOCR.interrupt_pixel = 3
        with self.assertRaises(KeyboardInterrupt):
            self.run_evaluation()
        summary, rows = self.read_results()
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["status"], "incomplete")
        original_predictions = self.output.with_suffix(".jsonl").read_bytes()
        rows[0]["label"] = "different"
        self.output.with_suffix(".jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(ValueError, "mismatched label"):
            self.run_evaluation(resume=True)
        self.output.with_suffix(".jsonl").write_bytes(original_predictions)
        original_manifest = self.manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        manifest["samples"][0]["label"] = "changed"
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "resume identity mismatch"):
            self.run_evaluation(resume=True)
        self.manifest_path.write_bytes(original_manifest)
        FakeOCR.interrupt_pixel = None
        self.run_evaluation(resume=True)
        summary, resumed_rows = self.read_results()
        self.assertEqual(summary["completed"], 3)
        self.assertEqual(resumed_rows[:2], rows[:0] + [json.loads(line) for line in original_predictions.splitlines()])
        self.assertEqual([session["processed_samples"] for session in summary["sessions"]], [2, 1])
        self.run_evaluation(resume=True)
        self.assertEqual(summary, self.read_results()[0])

    def test_model_and_sample_sha256_mismatches_fail_before_inference(self):
        (self.root / "test.onnx").write_bytes(b"wrong-model")
        with self.assertRaisesRegex(ValueError, "model SHA-256 mismatch"):
            self.run_evaluation()
        (self.root / "test.onnx").write_bytes(b"offline-test-model")
        (self.root / "1.png").write_bytes(b"wrong-image")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.run_evaluation()
        self.assertFalse(self.output.with_suffix(".json").exists())


if __name__ == "__main__":
    unittest.main()
