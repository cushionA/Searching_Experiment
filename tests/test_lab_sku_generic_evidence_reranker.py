"""Artificial contract fixtures for label-free whole-axis evidence ranking."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evidence_ranking_trial", ROOT / "experiments/sku-matching/trial_generic_evidence_reranker_v1.py")
PROBE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROBE)


def requests(source="window:0", quote="現在の商品仕様について。", options=None, target="余剰条件", row_value="100×80cm(4枚組) / ブルー"):
    options = options or ["あり", "なし"]
    result = []
    for index, option in enumerate(options):
        provenance = {"task_id": "task:0", "source_request_id": source, "axis_name": "選択区分", "option_values": options,
                      "selected_value": target, "au_row_key": "au:1:0", "selected_au_row": {
                          "row_key": "au:1:0", "axes": [{"axis_name": "寸法・色", "value": row_value}]},
                      "window": {"quote": quote, "source_ref": {"raw_file": "fixture.json", "sha256": "fixture"},
                                 "structural_context": [{"role": "heading", "text": "別商品案内"}]},
                      "candidate_option_index": index, "candidate_option_value": option,
                      "original_relation_request_id": source + ":old:" + str(index), "scope_proven": False}
        result.append({"id": source + ":option:" + str(index), "premise": quote, "provenance": provenance})
    return result


class FakeModel:
    max_length = 512

    def __init__(self, lengths=None, scores=None):
        self.lengths = lengths
        self.scores = scores
        self.scored_pairs = None

    def token_lengths_pairs(self, pairs):
        return self.lengths or [10] * len(pairs)

    def score_pairs(self, pairs, batch_size=8):
        self.scored_pairs = pairs
        return self.scores or [0.25] * len(pairs)


class GenericEvidenceRerankerTests(unittest.TestCase):
    def test_one_original_window_all_alternatives_same_query(self):
        rows = PROBE.prepare_rows(requests())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_option_indices"], [0, 1])
        self.assertEqual(len(rows[0]["nli_source_ids"]), 2)
        self.assertIn("あり / なし", rows[0]["query"])
        self.assertNotIn("余剰条件", rows[0]["query"])
        changed = requests(target="新しい対象")
        self.assertEqual(rows[0]["query"], PROBE.prepare_rows(changed)[0]["query"])

    def test_full_raw_composite_row_and_original_choices_are_not_decomposed(self):
        original = requests(options=["45×45cm（カバーのみ）", "70×100cm（本体＋付属品）"])
        row = PROBE.prepare_rows(original)[0]
        self.assertIn("100×80cm(4枚組) / ブルー", row["query"])
        self.assertIn("45×45cm（カバーのみ） / 70×100cm（本体＋付属品）", row["query"])
        self.assertEqual(row["passage"], original[0]["premise"])
        self.assertNotIn("別商品案内", row["passage"])
        self.assertEqual(row["provenance"]["window"]["structural_context"], original[0]["provenance"]["window"]["structural_context"])

    def test_target_appears_only_as_one_of_all_original_alternatives(self):
        row = PROBE.prepare_rows(requests(target="あり"))[0]
        self.assertEqual(row["query"].count("あり"), 1)
        self.assertNotIn("selected_value", row["query"])

    def test_axis_only_query_keeps_choices_in_metadata_and_full_row(self):
        original = requests(options=["選択値A", "選択値B"], target="選択値A")
        row = PROBE.prepare_rows(original, query_style="axis_only")[0]
        self.assertNotIn("選択値A", row["query"])
        self.assertNotIn("選択値B", row["query"])
        self.assertIn("選択区分", row["query"])
        self.assertIn("100×80cm(4枚組) / ブルー", row["query"])
        self.assertEqual(row["provenance"]["option_values"], ["選択値A", "選択値B"])
        original[0]["provenance"]["selected_value"] = "選択値B"
        original[1]["provenance"]["selected_value"] = "選択値B"
        self.assertEqual(row["query"], PROBE.prepare_rows(original, query_style="axis_only")[0]["query"])

    def test_duplicate_window_binding_mismatch_rejected(self):
        for field, value in (("au_row_key", "au:other"), ("axis_name", "異なる軸"), ("selected_value", "異なる選択値")):
            with self.subTest(field=field):
                original = requests()
                original[1]["provenance"][field] = value
                with self.assertRaises(ValueError):
                    PROBE.prepare_rows(original)
        original = requests()
        original[1]["provenance"]["window"]["source_ref"]["raw_file"] = "other.json"
        with self.assertRaises(ValueError):
            PROBE.prepare_rows(original)

    def test_incomplete_alternatives_and_duplicate_relation_ids_rejected(self):
        with self.assertRaises(ValueError):
            PROBE.prepare_rows(requests()[:1])
        with self.assertRaises(ValueError):
            PROBE.prepare_rows(requests() + requests())

    def test_overlimit_pairs_are_never_submitted_to_scoring(self):
        rows = PROBE.prepare_rows(requests("window:0", "長文") + requests("window:1", "短文"))
        model = FakeModel(lengths=[513, 512], scores=[0.9])
        predictions, selected = PROBE.score_rows(rows, model)
        self.assertEqual(len(model.scored_pairs), 1)
        self.assertEqual(model.scored_pairs[0][1], "短文")
        self.assertEqual(predictions[0]["status"], "input_too_long")
        self.assertIsNone(predictions[0]["score"])
        self.assertEqual(predictions[0]["token_count_untruncated"], 513)
        self.assertEqual(predictions[1]["rank"], 1)
        self.assertEqual(selected[0]["top_k_source_request_ids"]["5"], ["window:1"])

    def test_stable_tie_breaking_by_original_window_id(self):
        rows = PROBE.prepare_rows(requests("window:z") + requests("window:a"))
        predictions, selections = PROBE.score_rows(rows[::-1], FakeModel())
        ranks = {r["source_request_id"]: r["rank"] for r in predictions}
        self.assertEqual(ranks, {"window:z": 2, "window:a": 1})
        self.assertEqual(selections[0]["top_k_source_request_ids"]["1"], ["window:a"])

    def test_empty_literal_windows_remain_unknown_and_are_not_scored(self):
        rows = PROBE.prepare_rows(requests("window:0"), requests("window:empty", "\n \r\n"))
        model = FakeModel()
        predictions, _ = PROBE.score_rows(rows, model)
        self.assertEqual(len(model.scored_pairs), 1)
        empty = next(r for r in predictions if r["source_request_id"] == "window:empty")
        self.assertEqual(empty["status"], "empty_literal_quote")
        self.assertIsNone(empty["rank"])
        self.assertFalse(empty["scope_proven"])

    def test_existing_output_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(FileExistsError):
                PROBE.run(path / "missing", path, path / "model")

    def test_input_and_policy_are_frozen_before_model_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir()
            PROBE.write(input_dir / "requests.jsonl", requests())
            PROBE.write(input_dir / "empty-quote-requests.jsonl", [])
            manifest = {"request_count": 2, "empty_literal_quote_request_count": 0,
                        "output_sha256": {name: PROBE.sha(input_dir / name) for name in ("requests.jsonl", "empty-quote-requests.jsonl")}}
            (input_dir / "manifest.json").write_text(json.dumps(manifest))

            def factory(model_dir, threads, verify_files):
                self.assertTrue(verify_files)
                self.assertEqual(threads, 1)
                freeze = json.loads((output_dir / "freeze.json").read_text())
                self.assertEqual(freeze["top_ks_for_diagnostics"], [1, 3, 5])
                self.assertFalse(freeze["labels_read"])
                self.assertFalse(freeze["scope_proven"])
                self.assertEqual(freeze["ranking_requests_sha256"], PROBE.sha(output_dir / "ranking-requests.jsonl"))
                self.assertEqual(PROBE.sha(output_dir / "source-requests.jsonl"), manifest["output_sha256"]["requests.jsonl"])
                return FakeModel()

            summary = PROBE.run(input_dir, output_dir, root / "unused-model", model_factory=factory)
            self.assertEqual(summary["status_counts"], {"ok": 1})
            self.assertEqual(summary["unique_original_window_count"], 1)

    def test_manifest_tampering_is_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir()
            PROBE.write(input_dir / "requests.jsonl", requests())
            PROBE.write(input_dir / "empty-quote-requests.jsonl", [])
            (input_dir / "manifest.json").write_text(json.dumps({"output_sha256": {"requests.jsonl": "bad"}}))
            with self.assertRaises(ValueError):
                PROBE.run(input_dir, output_dir, root / "unused-model")
            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
