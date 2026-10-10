from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "sku-matching" / "evaluate_frozen_sku_accuracy.py"
spec = importlib.util.spec_from_file_location("frozen_sku_accuracy", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(evaluation)


class FrozenSkuAccuracyTests(unittest.TestCase):
    def grade(self, predictions, labels, candidates):
        return evaluation.score(predictions, labels, candidates)

    def test_wrong_au_row_is_not_accepted_precision(self):
        r = self.grade([{"case_id": "x", "decision": "adopt", "row_key": "grid:2:0"}],
                       [{"case_id": "x", "decision": "matched", "matching_au_row_keys": ["grid:1:0"]}],
                       {"x": {"grid:1:0", "grid:2:0"}})
        self.assertEqual(r["metrics"]["row_match_recall"]["rate"], 0.0)
        self.assertEqual(r["metrics"]["accepted_known_row_precision"]["rate"], 0.0)
        self.assertEqual(r["metrics"]["wrong_row_accepts"], 1)

    def test_same_grid_id_different_coordinates_is_a_distinct_row(self):
        with self.assertRaises(ValueError):
            self.grade([{"case_id": "x", "decision": "adopt", "row_key": "grid:4:1"}],
                       [{"case_id": "x", "decision": "matched", "matching_au_row_keys": ["grid:4:0"]}],
                       {"x": {"grid:4:0"}})

    def test_multiple_matching_rows_are_valid(self):
        r = self.grade([{"case_id": "x", "decision": "adopt", "row_key": "r2"}],
                       [{"case_id": "x", "label": "matched", "matching_au_row_keys": ["r1", "r2"]}],
                       {"x": {"r1", "r2"}})
        self.assertTrue(r["cases"][0]["correct"])

    def test_unknown_unlabelled_pending_and_not_evaluated(self):
        preds = [{"case_id": "rev", "decision": "adopt", "row_key": "r"},
                 {"case_id": "none", "decision": "pending", "row_key": None},
                 {"case_id": "wait", "decision": "not_evaluated", "row_key": None}]
        labels = [{"case_id": "rev", "decision": "review", "matching_au_row_keys": []},
                  {"case_id": "wait", "decision": "unmatched", "matching_au_row_keys": []}]
        r = self.grade(preds, labels, {"rev": {"r"}, "none": set(), "wait": set()})
        m = r["metrics"]
        self.assertEqual(m["unknown_truth_count"], 1)
        self.assertEqual(m["unknown_truth_accepted"], 1)
        self.assertEqual(m["unlabelled_count"], 1)
        self.assertEqual(m["known_case_accuracy"]["numerator"], 0)
        self.assertEqual(m["known_case_accuracy"]["denominator"], 1)
        self.assertEqual(m["decision_counts"]["pending"], 1)
        self.assertEqual(m["decision_counts"]["not_evaluated"], 1)
        self.assertEqual(m["positive_abstentions"], 0)

    def test_positive_abstention_and_full_confusion_columns(self):
        r = self.grade([{"case_id": "p", "decision": "pending", "row_key": None}],
                       [{"case_id": "p", "decision": "matched", "matching_au_row_keys": ["r"]}],
                       {"p": {"r"}})
        self.assertEqual(r["metrics"]["positive_abstentions"], 1)
        self.assertEqual(r["metrics"]["missed_positive"], 0)
        self.assertEqual(r["cases"][0]["failure_type"], "positive_abstention")
        self.assertEqual(r["metrics"]["confusion_matrix"]["matched"]["pending"], 1)
        self.assertEqual(set(r["metrics"]["confusion_matrix"]["matched"]),
                         {"adopt", "exclude", "pending", "not_evaluated"})

    def test_conflicting_label_fields_and_unpredicted_invalid_label_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.grade([], [{"case_id": "x", "decision": "matched", "label": "unmatched",
                             "matching_au_row_keys": ["r"]}], {"x": {"r"}})
        with self.assertRaisesRegex(ValueError, "invalid label"):
            self.grade([], [{"case_id": "outside", "decision": "invalid", "matching_au_row_keys": []}], {})
        with self.assertRaisesRegex(ValueError, "no matching AU row"):
            self.grade([], [{"case_id": "outside", "decision": "matched", "matching_au_row_keys": []}], {})

    def test_duplicate_input_row_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "inputs.json"
            p.write_text(json.dumps([{"case_id": "x", "au_rows": [{"row_key": "r"}, {"row_key": "r"}]}]))
            with self.assertRaisesRegex(ValueError, "duplicate AU row_key"):
                evaluation._inputs([p])

    def test_duplicate_label_conflicts_rejected(self):
        labels = [{"case_id": "x", "decision": "matched", "matching_au_row_keys": ["r"]},
                  {"case_id": "x", "decision": "unmatched", "matching_au_row_keys": []}]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.grade([{"case_id": "x", "decision": "exclude", "row_key": None}], labels, {"x": {"r"}})

    def test_bad_labels_predictions_and_candidate_types_rejected(self):
        base = [{"case_id": "x", "decision": "exclude", "row_key": None}]
        for labels, candidates in [
            ([{"case_id": "x", "decision": "matched", "matching_au_row_keys": []}], {"x": set()}),
            ([{"case_id": "x", "decision": "unmatched", "matching_au_row_keys": ["r"]}], {"x": {"r"}}),
            ([{"case_id": "x", "decision": "matched", "matching_au_row_keys": ["out"]}], {"x": {"r"}}),
        ]:
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                self.grade(base, labels, candidates)
        for pred in ([{"case_id": "x", "decision": "adopt", "row_key": None}],
                     [{"case_id": "x", "decision": "wat", "row_key": None}],
                     [{"case_id": "x", "decision": "adopt", "row_key": "out"}],
                     [{"case_id": "x", "decision": "exclude", "row_key": "r"}]):
            with self.subTest(pred=pred), self.assertRaises(ValueError):
                self.grade(pred, [], {"x": {"r"}})
        with self.assertRaises(ValueError):
            self.grade(base, [], {"x": ["r"]})

    def test_zero_denominators_are_null(self):
        m = self.grade([], [], {})["metrics"]
        for key in ("known_case_accuracy", "known_decided_accuracy", "accepted_known_row_precision",
                    "row_match_recall", "known_coverage"):
            self.assertIsNone(m[key]["rate"])

    def test_cli_hashes_inputs_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            predictions = base / "pred.json"
            labels = base / "labels.json"
            inputs = base / "inputs.json"
            output = base / "out"
            predictions.write_text(json.dumps({"cases": [{"case_id": "x", "decision": "exclude", "row_key": None}]}))
            labels.write_text(json.dumps([{"case_id": "x", "decision": "unmatched", "matching_au_row_keys": []}]))
            inputs.write_text(json.dumps({"cases": [{"case_id": "x", "au_rows": []}]}))
            originals = [p.read_bytes() for p in (predictions, labels, inputs)]
            self.assertEqual(evaluation.main(["--predictions", str(predictions), "--labels", str(labels),
                                              "--inputs", str(inputs), "--output", str(output)]), 0)
            self.assertEqual(originals, [p.read_bytes() for p in (predictions, labels, inputs)])
            summary = json.loads((output / "summary.json").read_text())
            self.assertIn("grading-only", summary["mode"])
            self.assertEqual(summary["input_sha256_pre"], summary["input_sha256_post"])
            self.assertTrue((output / "case-results.jsonl").is_file())
            with self.assertRaisesRegex(ValueError, "already exists"):
                evaluation.main(["--predictions", str(predictions), "--labels", str(labels),
                                 "--inputs", str(inputs), "--output", str(output)])

    def test_cli_reads_two_jsonl_label_and_input_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            predictions = base / "predictions.json"
            labels = base / "labels.jsonl"
            inputs = base / "inputs.jsonl"
            output = base / "graded"
            predictions.write_text(json.dumps({"cases": [
                {"case_id": "a", "decision": "exclude", "row_key": None},
                {"case_id": "b", "decision": "exclude", "row_key": None},
            ]}))
            labels.write_text(
                '{"case_id":"a","decision":"unmatched","matching_au_row_keys":[]}\n'
                '{"case_id":"b","decision":"unmatched","matching_au_row_keys":[]}\n')
            inputs.write_text(
                '{"case_id":"a","au_rows":[]}\n'
                '{"case_id":"b","au_rows":[]}\n')
            self.assertEqual(evaluation.main(["--predictions", str(predictions), "--labels", str(labels),
                                              "--inputs", str(inputs), "--output", str(output)]), 0)
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["metrics"]["known_case_accuracy"]["rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
