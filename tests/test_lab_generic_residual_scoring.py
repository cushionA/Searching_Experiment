from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "residual_scoring", ROOT / "experiments/sku-matching/score_generic_residual_diagnostics_v1.py")
SCORER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORER)


class ResidualScoringTests(unittest.TestCase):
    def fixture(self, root):
        run = root / "run"
        run.mkdir()
        label = {"task_id": "t", "case_id": "c", "au_row_key": "a", "condition_id": "x", "label": "support"}
        prediction = {**label, "condition_text": "original", "diagnostic_relation": "unknown"}
        prediction.pop("label")
        (run / "condition-diagnostics.jsonl").write_text(json.dumps(prediction) + "\n")
        (run / "predictions.jsonl").write_text(json.dumps({"task_id": "t", "purpose": "condition", "relation": "unknown"}) + "\n")
        (run / "requests.jsonl").write_text("{}\n")
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        freeze = {"request_sha256": sha(run / "requests.jsonl"), "input_sha256": {"tasks.jsonl": "input-pin"}}
        (run / "freeze.json").write_text(json.dumps(freeze))
        summary = {**freeze, "backend": "test", "diagnostic_relations": {"unknown": 1},
                   "output_sha256": {name: sha(run / name) for name in ("predictions.jsonl", "condition-diagnostics.jsonl")}}
        (run / "summary.json").write_text(json.dumps(summary))
        annotations = root / "annotations.jsonl"
        annotations.write_text(json.dumps(label) + "\n")
        manifest = {"outputs": {annotations.name: sha(annotations)}, "inputs": {"tasks.jsonl": "input-pin"},
                    "sample_method": {"subset_task_ids": ["t"]}, "counters": {"annotated": 1}}
        (root / "manifest.json").write_text(json.dumps(manifest))
        return run, annotations, manifest

    def test_all_unknown_has_zero_support_recall_and_undefined_precision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, annotations, _ = self.fixture(root)
            output = root / "score.json"
            SCORER.score([run], annotations, output)
            report = json.loads(output.read_text())
            self.assertFalse(report["sku_accuracy_measured"])
            self.assertEqual(report["models"][0]["support_recall"], 0)
            self.assertIsNone(report["models"][0]["support_precision"])

    def test_changed_annotations_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, annotations, _ = self.fixture(root)
            annotations.write_text("{}\n")
            with self.assertRaisesRegex(ValueError, "annotation manifest hash"):
                SCORER.score([run], annotations, root / "score.json")

    def test_different_frozen_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, annotations, manifest = self.fixture(root)
            manifest["inputs"]["tasks.jsonl"] = "different"
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "different frozen inputs"):
                SCORER.score([run], annotations, root / "score.json")

    def test_changed_requests_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, annotations, _ = self.fixture(root)
            (run / "requests.jsonl").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "request freeze mismatch"):
                SCORER.score([run], annotations, root / "score.json")

    def test_unfinished_predictions_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run, annotations, _ = self.fixture(root)
            (run / "summary.json").unlink()
            with self.assertRaisesRegex(ValueError, "complete before reading labels"):
                SCORER.score([run], annotations, root / "score.json")
