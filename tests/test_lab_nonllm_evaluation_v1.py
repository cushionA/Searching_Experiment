import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "experiments/sku-matching/evaluate_nonllm_binary_gate_v1.py"
spec = importlib.util.spec_from_file_location("nonllm_evaluation_v1", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def prediction(case_id, decision="accept", row="right"):
    return {"case_id": case_id, "decision": decision, "au_row_key": row,
            "requirements": [{"component": "lace"}]}


class NonllmEvaluationTests(unittest.TestCase):
    def test_wrong_row_accept_remains_in_gold_matched_recall_denominator(self):
        labels = {
            "right": {"case_id": "right", "decision": "matched", "matching_au_row_keys": ["right"]},
            "wrong": {"case_id": "wrong", "decision": "matched", "matching_au_row_keys": ["gold-row"]},
        }
        result, errors = evaluation.evaluate_file("p.jsonl", [prediction("right"), prediction("wrong", row="bad")], labels)
        self.assertEqual(result["counts"]["correct_accept"], 1)
        self.assertEqual(result["counts"]["accept_labeled_matched"], 1)
        self.assertEqual(result["match_recall_vs_reused_labels"], 0.5)
        self.assertEqual(result["accepted_row_precision_vs_reused_labels"], 0.5)
        self.assertEqual(len(errors), 1)

    def test_duplicate_prediction_case_is_rejected(self):
        labels = {"a": {"case_id": "a", "decision": "unmatched"}}
        with self.assertRaisesRegex(ValueError, "duplicate prediction"):
            evaluation.evaluate_file("p.jsonl", [prediction("a"), prediction("a")], labels)

    def test_missing_prediction_case_is_rejected(self):
        labels = {"a": {"case_id": "a", "decision": "matched"}, "b": {"case_id": "b", "decision": "unmatched"}}
        with self.assertRaisesRegex(ValueError, "case sets differ"):
            evaluation.evaluate_file("p.jsonl", [prediction("a")], labels)

    def test_all_requested_hashes_checked_before_labels_opened(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "predictions").mkdir()
            first, second = evaluation.LEGACY_FILES
            (root / first).write_text('{"case_id":"a"}\n')
            (root / second).write_text('{"case_id":"b"}\n')
            manifest = {"files": {first: hashlib.sha256((root / first).read_bytes()).hexdigest(), second: "wrong"}}
            with self.assertRaisesRegex(ValueError, "Frozen predictions changed"):
                evaluation.verify_predictions(root, [first, second], manifest)

    def test_duplicate_label_case_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "predictions").mkdir()
            files = ["predictions/test.jsonl"]
            pred_path = root / files[0]
            pred_path.write_text(json.dumps(prediction("a")) + "\n")
            (root / "manifest.json").write_text(json.dumps({"files": {files[0]: hashlib.sha256(pred_path.read_bytes()).hexdigest()}}))
            labels_path = root / "labels.jsonl"
            label = {"case_id": "a", "decision": "matched", "matching_au_row_keys": ["right"]}
            labels_path.write_text(json.dumps(label) + "\n" + json.dumps(label) + "\n")
            with self.assertRaisesRegex(ValueError, "duplicate label"):
                evaluation.run_evaluation(root, labels_path, files, "result.json", "test")

    def test_novel_label_schema_is_adapted_without_mutating_source(self):
        source = {"case_id": "a", "label": "matched", "matching_au_row_keys": ["r"]}
        adapted = evaluation.adapt_label(source)
        self.assertEqual(adapted["decision"], "matched")
        self.assertNotIn("decision", source)
        self.assertEqual(source["label"], "matched")

    def test_conflicting_legacy_and_novel_fields_are_rejected(self):
        source = {"case_id": "a", "decision": "matched", "label": "unmatched"}
        with self.assertRaisesRegex(ValueError, "conflicting"):
            evaluation.adapt_label(source)

    def test_unknown_label_enum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown label enum"):
            evaluation.adapt_label({"case_id": "a", "label": "maybe"})


if __name__ == "__main__":
    unittest.main()
