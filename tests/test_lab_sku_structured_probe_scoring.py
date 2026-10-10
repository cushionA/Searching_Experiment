"""Frozen-input and ambiguous-window safeguards, without loading a model."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "experiments/sku-matching" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCORE = load("score_structured_choice_probe_v1")
TRIAL = load("trial_structured_choice_probe_v1")


class StructuredProbeScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "code").mkdir()
        (self.run / "code" / "frozen.py").write_text("# frozen\n")
        self.label = {"case_id": "case", "au_row_key": "fixed:1", "axis_name": "仕様",
                      "selected_value": "色（素材）", "relation": "support"}
        self.annotations = self.root / "annotations.jsonl"
        TRIAL.write(self.annotations, [self.label])
        self.output = self.root / "score.json"
        self.freeze([("option:0", 0.99)])

    def freeze(self, winners):
        requests, predictions = [], []
        for i, (winner, probability) in enumerate(winners):
            identity = f"request:{i}"
            requests.append({"id": identity, "provenance": {
                "case_id": "case", "au_row_key": "fixed:1", "axis_name": "仕様", "arm": "structured",
                "selected_value": "色（素材）", "option_values": ["色（素材）", "別の仕様"],
                "window": {"field_kind": "plain_title" if i == 0 else "description_source_field"}}})
            predictions.append({"id": identity, "argmax_label": winner, "argmax_probability": probability})
        for name, rows in (("requests.jsonl", requests), ("predictions.jsonl", predictions)):
            (self.run / name).unlink(missing_ok=True)
            TRIAL.write(self.run / name, rows)
        self.frozen = {"backend": "jev", "thresholds_for_diagnostics": [0.9], "primary_threshold": 0.9,
                       "code_sha256": {"frozen.py": SCORE.sha(self.run / "code" / "frozen.py")}}
        (self.run / "freeze.json").write_text(json.dumps(self.frozen))
        summary = {**self.frozen, "pins": {}, "output_sha256": {
            name: SCORE.sha(self.run / name) for name in ("requests.jsonl", "predictions.jsonl", "freeze.json")}}
        (self.run / "summary.json").write_text(json.dumps(summary))

    def score(self):
        return SCORE.score([self.run], self.annotations, self.output)

    def test_partial_text_is_not_treated_as_the_whole_composite_value(self):
        self.freeze([("option:1", 0.99)])
        measurement = self.score()["models"][0]["measurements"][0]
        self.assertEqual(measurement["rows"][0]["predicted"], "conflict")
        self.assertFalse(measurement["rows"][0]["scope_proven"])

    def test_multiple_window_values_remain_unknown(self):
        self.freeze([("option:0", 0.99), ("option:1", 0.99)])
        self.assertEqual(self.score()["models"][0]["measurements"][0]["rows"][0]["predicted"], "unknown")

    def test_low_confidence_does_not_become_support(self):
        self.freeze([("option:0", 0.89)])
        self.assertEqual(self.score()["models"][0]["measurements"][0]["true_support"], 0)

    def test_modified_predictions_are_rejected_before_labels_are_read(self):
        with (self.run / "predictions.jsonl").open("a") as stream:
            stream.write("{}\n")
        self.annotations.unlink()
        with self.assertRaisesRegex(ValueError, "output SHA"):
            self.score()

    def test_modified_code_is_rejected_before_labels_are_read(self):
        (self.run / "code" / "frozen.py").write_text("# changed\n")
        self.annotations.unlink()
        with self.assertRaisesRegex(ValueError, "code SHA"):
            self.score()

    def test_mismatched_selected_value_cannot_score_as_correct(self):
        self.annotations.unlink()
        TRIAL.write(self.annotations, [{**self.label, "selected_value": "別の仕様"}])
        with self.assertRaisesRegex(ValueError, "selected value"):
            self.score()

    def test_request_hash_failure_prevents_model_loading(self):
        inputs = self.root / "inputs"
        inputs.mkdir()
        (inputs / "requests.jsonl").write_text("{}\n")
        (inputs / "manifest.json").write_text(json.dumps({"output_sha256": {"requests.jsonl": "incorrect"}}))
        with self.assertRaisesRegex(ValueError, "request manifest SHA"):
            TRIAL.run(inputs, self.root / "inference")
        self.assertFalse((self.root / "inference").exists())


if __name__ == "__main__":
    unittest.main()
