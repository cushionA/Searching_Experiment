"""Replay integrity tests using invented fixtures only, never captured SKU data."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ranked_relation_contract", ROOT / "experiments/sku-matching/score_ranked_relation_probe_v1.py")
REPLAY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REPLAY)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def write_rows(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


class InventedReranker:
    max_length = 512

    def token_lengths_pairs(self, pairs):
        return [20] * len(pairs)

    def score_pairs(self, pairs, batch_size):
        return [10.0 - i for i in range(len(pairs))]


class RankedRelationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.run = self.base / "invented-relation-run"
        self.ranking = self.base / "invented-ranking-run"
        self.run.mkdir()
        self.ranking.mkdir()
        options = ["青（特別）・予備2個", "赤（限定）/予備なし"]
        self.requests, self.empty = [], []
        for task, window, quote in (("fixture-task", 0, "架空の仕様引用 A"),
                                    ("fixture-task", 1, "架空の仕様引用 B"),
                                    ("fixture-empty-task", 0, "  \n")):
            for index, value in enumerate(options):
                p = {"task_id": task, "source_request_id": f"{task}:window:{window}",
                     "case_id": task + "-case", "au_row_key": "fixture-row", "axis_name": "色・付属品（複合）",
                     "selected_value": options[0], "option_values": options,
                     "candidate_option_index": index, "candidate_option_value": value,
                     "original_relation_request_id": f"{task}:old:{window}:{index}",
                     "hypothesis_style": "quote_axis", "fixed_au_product_ref": "fixture://never-real",
                     "selected_au_row": {"row_key": "fixture-row", "axes": [{"axis_name": "包装", "value": "2個（全体）"}]},
                     "window": {"quote": quote, "field_kind": "invented-body"}, "scope_proven": False}
                row = {"id": f"{task}:{window}:{index}", "premise": quote,
                       "hypothesis": f"色・付属品（複合）は{value}です。", "provenance": p}
                (self.requests if quote.strip() else self.empty).append(row)
        self._write_ranking()
        self.predictions = []
        for request in self.requests:
            p = request["provenance"]
            relation = "support" if p["window"]["quote"].endswith("A") else "conflict"
            if p["candidate_option_index"]:
                relation = "unknown"
            probs = {k: (0.96 if k == relation else 0.02) for k in ("support", "conflict", "unknown")}
            self.predictions.append({**deepcopy(request), "probabilities": probs})
        self._write_relation()
        self.labels = self.base / "invented-labels.jsonl"
        labels = []
        for task, relation in (("fixture-task", "support"), ("fixture-empty-task", "unknown")):
            p = next(r["provenance"] for r in self.requests + self.empty if r["provenance"]["task_id"] == task)
            labels.append({**{k: p[k] for k in ("case_id", "au_row_key", "axis_name", "selected_value")}, "relation": relation})
        write_rows(self.labels, labels)

    def _code(self, directory):
        (directory / "code").mkdir(exist_ok=True)
        path = directory / "code" / "fixture.py"
        path.write_text("# invented frozen code\n")
        return {"fixture.py": REPLAY.sha(path)}

    def _write_ranking(self):
        write_rows(self.ranking / "source-requests.jsonl", self.requests)
        write_rows(self.ranking / "source-empty-quote-requests.jsonl", self.empty)
        manifest = {"request_count": len(self.requests), "empty_literal_quote_request_count": len(self.empty),
                    "output_sha256": {n: REPLAY.sha(self.ranking / ("source-" + n))
                                      for n in ("requests.jsonl", "empty-quote-requests.jsonl")}}
        write_json(self.ranking / "source-manifest.json", manifest)
        rows = REPLAY.RANKING.prepare_rows(self.requests, self.empty)
        predictions, selections = REPLAY.RANKING.score_rows(rows, InventedReranker())
        write_rows(self.ranking / "ranking-requests.jsonl", rows)
        write_rows(self.ranking / "rankings.jsonl", predictions)
        write_rows(self.ranking / "top-k-windows.jsonl", selections)
        freeze = {"input_sha256": {n: REPLAY.sha(self.ranking / ("source-" + n))
                                    for n in ("requests.jsonl", "empty-quote-requests.jsonl", "manifest.json")},
                  "code_sha256": self._code(self.ranking), "top_ks_for_diagnostics": [1, 3, 5],
                  "ranking_requests_sha256": REPLAY.sha(self.ranking / "ranking-requests.jsonl"),
                  "score_is_condition_confidence": False, "scope_proven": False, "query_style": "axis_options"}
        write_json(self.ranking / "freeze.json", freeze)
        self._refresh_ranking_summary(freeze)

    def _refresh_ranking_summary(self, freeze=None):
        freeze = freeze or json.loads((self.ranking / "freeze.json").read_text())
        write_json(self.ranking / "summary.json", {**freeze, "output_sha256": {
            p.name: REPLAY.sha(p) for p in self.ranking.iterdir() if p.is_file() and p.name != "summary.json"}})

    def _write_relation(self):
        requests = [{k: v for k, v in p.items() if k != "probabilities"} for p in self.predictions]
        write_rows(self.run / "requests.jsonl", requests)
        write_rows(self.run / "predictions.jsonl", self.predictions)
        write_json(self.run / "manifest.json", {"invented_fixture": True})
        freeze = {"input_sha256": {n: REPLAY.sha(self.run / n) for n in ("requests.jsonl", "manifest.json")},
                  "code_sha256": self._code(self.run)}
        write_json(self.run / "freeze.json", freeze)
        summary = {"primary_threshold": 0.9, "thresholds_for_diagnostics": [0.5, 0.7, 0.9],
                   "output_sha256": {n: REPLAY.sha(self.run / n) for n in ("requests.jsonl", "predictions.jsonl", "freeze.json", "manifest.json")}}
        write_json(self.run / "summary.json", summary)

    def score(self):
        return REPLAY.score(self.run, self.ranking, [self.labels], self.base / "score.json")

    def test_top1_support_survives_and_top3_conflicting_quote_suppresses_it(self):
        result = self.score()
        self.assertEqual(result["diagnostics"]["1"]["0.9"]["true_support"], 1)
        self.assertEqual(result["diagnostics"]["3"]["0.9"]["true_support"], 0)
        condition = next(r for r in result["diagnostics"]["3"]["0.9"]["conditions"] if r["task_id"] == "fixture-task")
        self.assertEqual(condition["relation"], "unknown")
        self.assertTrue(condition["support_evidence_ids"])
        self.assertTrue(condition["conflict_evidence_ids"])
        self.assertFalse(result["production_eligible"])
        self.assertFalse(result["scope_proven"])

    def test_all_empty_task_is_unknown_and_never_silently_omitted(self):
        result = self.score()
        for values in result["diagnostics"].values():
            for diagnostic in values.values():
                self.assertEqual(diagnostic["count"], 2)
                empty = next(r for r in diagnostic["conditions"] if r["task_id"] == "fixture-empty-task")
                self.assertEqual(empty["relation"], "unknown")
                self.assertEqual(empty["available_ranked_window_count"], 0)
                self.assertEqual(empty["excluded_windows"][0]["status"], "empty_literal_quote")

    def test_relevance_logit_never_becomes_condition_confidence(self):
        for p in self.predictions:
            p["probabilities"] = None  # Invented explicit not-inferred outputs.
        self._write_relation()
        result = self.score()
        self.assertEqual(result["diagnostics"]["1"]["0.5"]["predicted_support"], 0)
        self.assertIsNone(result["diagnostics"]["1"]["0.5"]["support_precision"])

    def test_missing_required_window_and_partial_alternative_output_are_refused(self):
        original = deepcopy(self.predictions)
        for mode in ("window", "alternative"):
            with self.subTest(mode=mode):
                self.predictions = deepcopy(original[:-2] if mode == "window" else original[:-1])
                self._write_relation()
                with self.assertRaisesRegex(ValueError, "missing a required|only part"):
                    self.score()

    def test_complete_top5_subset_can_leave_other_original_windows_uninferred(self):
        for window in range(2, 6):
            for index in range(2):
                request = deepcopy(self.requests[index])
                request["id"] = f"fixture-task:{window}:{index}"
                request["premise"] = f"架空の仕様引用 {window}"
                p = request["provenance"]
                p["source_request_id"] = f"fixture-task:window:{window}"
                p["original_relation_request_id"] = f"fixture-task:old:{window}:{index}"
                p["window"]["quote"] = request["premise"]
                self.requests.append(request)
                if window < 5:
                    self.predictions.append({**deepcopy(request), "probabilities": {
                        "support": 0.02, "conflict": 0.02, "unknown": 0.96}})
        self._write_ranking()
        self._write_relation()
        result = self.score()
        self.assertEqual(result["diagnostics"]["1"]["0.9"]["count"], 2)
        row = next(r for r in result["diagnostics"]["5"]["0.9"]["conditions"] if r["task_id"] == "fixture-task")
        self.assertEqual(len(row["selected_source_request_ids"]), 5)
        self.assertNotIn("fixture-task:window:5", row["selected_source_request_ids"])

    def test_ranking_hash_error_precedes_malformed_annotation_read(self):
        self.labels.write_text("not json")
        with (self.ranking / "rankings.jsonl").open("a") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            self.score()

    def test_changed_ranked_source_binding_is_rejected_even_with_new_receipt(self):
        records = REPLAY.read(self.ranking / "rankings.jsonl")
        records[0]["provenance"]["selected_value"] = "invented changed composite"
        write_rows(self.ranking / "rankings.jsonl", records)
        self._refresh_ranking_summary()
        with self.assertRaisesRegex(ValueError, "window/alternative binding"):
            self.score()

    def test_changed_topk_selection_is_rejected_even_with_new_receipt(self):
        rows = REPLAY.read(self.ranking / "top-k-windows.jsonl")
        next(r for r in rows if r["task_id"] == "fixture-task")["top_k_source_request_ids"]["1"] = ["fixture-task:window:1"]
        write_rows(self.ranking / "top-k-windows.jsonl", rows)
        self._refresh_ranking_summary()
        with self.assertRaisesRegex(ValueError, "topK selection"):
            self.score()

    def test_existing_output_is_never_replaced(self):
        output = self.base / "score.json"
        output.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.score()
        self.assertEqual(output.read_text(), "keep")

    def test_invalid_relation_probabilities_are_rejected(self):
        self.predictions[0]["probabilities"]["support"] = float("nan")
        self._write_relation()
        with self.assertRaisesRegex(ValueError, "invalid relation probabilities"):
            self.score()

    def test_unsupported_frozen_topk_is_not_tuned_from_labels(self):
        freeze = json.loads((self.ranking / "freeze.json").read_text())
        freeze["top_ks_for_diagnostics"] = [1, 2, 5]
        write_json(self.ranking / "freeze.json", freeze)
        self._refresh_ranking_summary(freeze)
        with self.assertRaisesRegex(ValueError, "topKs differ"):
            self.score()


if __name__ == "__main__":
    unittest.main()
