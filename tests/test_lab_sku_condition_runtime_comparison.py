"""Runtime comparison contracts; synthetic unit fixtures are not SKU samples."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("runtime_comparison", ROOT / "experiments/sku-matching/compare_full_condition_runtime_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def data():
    model = {"id": "synthetic-request", "question": "現在の仕様？", "state": "原商品🙂",
             "choices": [{"label": "あり", "description": "この商品の仕様はあり。"},
                         {"label": "なし", "description": "この商品の仕様はなし。"}]}
    score = {**model, "model_id": "pinned-test-model", "model_revision": "frozen-revision", "precision": "fp32",
             "format_version": "frozen-format", "status": "ok", "argmax_label": "あり",
             "logits": {"あり": 2.0, "なし": 1.0}, "probabilities": {"あり": .7, "なし": .3},
             "token_counts": {"あり": 30, "なし": 30}}
    source = {"request_id": model["id"], "request_sha256": "a" * 64, "option_values": ["あり", "なし"]}
    prediction = {"request_id": model["id"], "request_sha256": "a" * 64,
                  "source_request": source, "state": "support", "winning_value": "あり"}
    return ({model["id"]: model}, {model["id"]: score}, {model["id"]: prediction})


def write_run(directory, names, run):
    directory.mkdir()
    for name, rows in zip(names, run, strict=True):
        with (directory / name).open("w", encoding="utf-8") as handle:
            for row in rows.values():
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class RuntimeComparisonContracts(unittest.TestCase):
    def test_exact_same_runtime_is_diagnostic_identity(self):
        report = R.compare_rows(data(), data())
        self.assertEqual(report["comparison_count"], 1)
        self.assertEqual(report["state_change_count"], 0)
        self.assertEqual(report["argmax_change_count"], 0)
        self.assertEqual(report["max_absolute_logit_difference"], 0)

    def test_numeric_gpu_difference_is_allowed_and_all_labels_are_reported(self):
        old, new = data(), data()
        new[1]["synthetic-request"]["logits"]["なし"] += .025
        new[1]["synthetic-request"]["probabilities"].update({"あり": .69, "なし": .31})
        report = R.compare_rows(old, new)
        self.assertAlmostEqual(report["max_absolute_logit_difference"], .025)
        self.assertAlmostEqual(report["max_absolute_probability_difference"], .01)
        self.assertEqual([r["label"] for r in report["comparisons"][0]["logits"]["values"]], ["あり", "なし"])
        self.assertEqual(report["argmax_change_count"], 0)

    def test_argmax_and_state_changes_are_counted_separately(self):
        old, new = data(), data()
        new[1]["synthetic-request"]["argmax_label"] = "なし"
        new[2]["synthetic-request"]["state"] = "unknown"
        report = R.compare_rows(old, new)
        self.assertEqual((report["state_change_count"], report["argmax_change_count"]), (1, 1))
        self.assertEqual(report["changed_request_ids"], ["synthetic-request"])

    def test_any_model_input_difference_fails_before_numeric_interpretation(self):
        for field, value in (("question", "違う質問"), ("state", "別商品"), ("choices", [])):
            old, new = data(), data()
            new[0]["synthetic-request"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "exact model"):
                R.compare_rows(old, new)

    def test_score_record_must_match_its_exact_model_input(self):
        old, new = data(), data()
        new[1]["synthetic-request"]["question"] = "別の質問"
        with self.assertRaisesRegex(ValueError, "score record differs"):
            R.compare_rows(old, new)

    def test_complete_source_binding_difference_fails_fast(self):
        old, new = data(), data()
        new[2]["synthetic-request"]["source_request"]["selected_value"] = "別の値"
        with self.assertRaisesRegex(ValueError, "complete source_request"):
            R.compare_rows(old, new)

    def test_missing_old_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing old subset"):
            R.compare_rows(data(), ({}, {}, {}))

    def test_model_revision_change_is_not_classed_as_runtime_numeric_drift(self):
        old, new = data(), data()
        new[1]["synthetic-request"]["model_revision"] = "other-revision"
        with self.assertRaisesRegex(ValueError, "model identity"):
            R.compare_rows(old, new)

    def test_runtime_errors_are_reported_with_unavailable_numbers(self):
        old, new = data(), data()
        new[1]["synthetic-request"].update(status="inference_error", logits=None, probabilities=None, argmax_label=None)
        new[2]["synthetic-request"]["state"] = "unknown"
        report = R.compare_rows(old, new)
        self.assertEqual(report["numeric_unavailable_request_count"], 1)
        self.assertIsNone(report["max_absolute_logit_difference"])
        self.assertEqual(report["status_change_count"], 1)

    def test_missing_or_nonfinite_label_scores_are_rejected(self):
        for values in ({"あり": 1}, {"あり": float("nan"), "なし": 2}, {"あり": True, "なし": 2}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                R.numeric_differences({"あり": 2, "なし": 1}, values, ["あり", "なし"], "logits")

    def test_duplicate_runtime_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rows.jsonl"
            path.write_text('{"id":"same"}\n{"id":"same"}\n')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                R.rows(path, "id")

    def test_output_is_exclusive_and_records_non_scoring_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_dir, new_dir = root / "old", root / "new"
            write_run(old_dir, R.OLD_NAMES, data())
            write_run(new_dir, R.NEW_NAMES, data())
            output = root / "comparison.json"
            report = R.run(new_dir, output, old_run_dir=old_dir)
            self.assertFalse(report["labels_read"])
            self.assertFalse(report["model_inference_run"])
            self.assertFalse(report["full_cohort_scoring_run"])
            self.assertFalse(report["cpu_gpu_bit_identity_required"])
            self.assertEqual(len(report["file_sha256"]), 6)
            with self.assertRaises(FileExistsError):
                R.run(new_dir, output, old_run_dir=old_dir)

    def test_optional_fusion_keeps_the_explicit_old_subset_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_dir, new_dir = root / "old", root / "new"
            old_dir.mkdir()
            new_dir.mkdir()
            prediction = data()[2]["synthetic-request"]
            prediction["fused_scores"] = {"あり": .6, "なし": .4}
            (old_dir / "predictions.jsonl").write_text(json.dumps(prediction) + "\n")
            other = {**deepcopy(prediction), "request_id": "other-request"}
            (new_dir / "fusion-predictions.jsonl").write_text(json.dumps(prediction) + "\n" + json.dumps(other) + "\n")
            report = R.optional_fusion(old_dir, new_dir)
            self.assertEqual((report["comparison_count"], report["state_change_count"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
