"""Invented replay fixtures, isolated from captured SKU/model/annotation data."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


REPLAY = module("ranked_gliner_contract", ROOT / "experiments/sku-matching/score_ranked_gliner_probe_v1.py")
FIXTURE = module("ranked_gliner_invented_fixture", ROOT / "tests/test_lab_sku_gliner_choice_diagnostics.py")


class InventedReranker:
    max_length = 512

    def token_lengths_pairs(self, pairs):
        return [20] * len(pairs)

    def score_pairs(self, pairs, batch_size):
        return [10.0 - i for i in range(len(pairs))]


class RankedGlinerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.run, self.ranking = self.base / "invented-gliner", self.base / "invented-ranking"
        self.run.mkdir()
        self.ranking.mkdir()
        self.original = FIXTURE.sources("w0", "description") + FIXTURE.sources("w1", "description")
        empty = FIXTURE.sources("empty-window", "description", "empty-task")
        for source in empty:
            source["provenance"].update(case_id="empty-case", condition_id="empty-condition")
            source["provenance"]["window"]["quote"] = " \n"
            source["premise"] = source["premise"].partition("引用本文：")[0] + "引用本文： \n"
        self.original += empty
        self.requests = FIXTURE.RUNNER.prepare(self.original, "all_windows")
        self.predictions = [FIXTURE.inferred(request, 1 if request["id"] == "w1" else 0)
                            for request in self.requests]
        self._write_gliner()
        self._write_ranking()
        self.labels = self.base / "invented-annotations.jsonl"
        rows = []
        for task, relation in (("t0", "support"), ("empty-task", "unknown")):
            p = next(r["provenance"] for r in self.original if r["provenance"]["task_id"] == task)
            rows.append({**{k: p[k] for k in ("case_id", "au_row_key", "axis_name", "selected_value")}, "relation": relation})
        FIXTURE.write_rows(self.labels, rows)

    def _code(self, directory):
        (directory / "code").mkdir(exist_ok=True)
        code = directory / "code" / "invented.py"
        code.write_text("# Artificial contract source\n")
        return {code.name: REPLAY.sha(code)}

    def _write_gliner(self):
        FIXTURE.write_rows(self.run / "source_requests.jsonl", self.original)
        FIXTURE.write_rows(self.run / "requests.jsonl", self.requests)
        FIXTURE.write_rows(self.run / "predictions.jsonl", self.predictions)
        freeze = {"labels_read": False, "production_eligible": False, "scope_proven": False,
                  "input_scope": "all_windows", "source_input_sha256": REPLAY.sha(self.run / "source_requests.jsonl"),
                  "requests_sha256": REPLAY.sha(self.run / "requests.jsonl"), "source_request_count": len(self.original),
                  "request_count": len(self.requests), "schema_transport": {"reserved_tokens": list(REPLAY.GLINER.RESERVED)},
                  "code_sha256": self._code(self.run), "preflight": {"max_tokens": 512, "max_len": None,
                      "includes_schema": True, "actual_encoder_input_token_counts": [r["actual_encoder_input_token_count"] for r in self.predictions]}}
        FIXTURE.write_json(self.run / "freeze.json", freeze)
        from collections import Counter
        summary = {**freeze, "status_counts": dict(Counter(r["status"] for r in self.predictions)), "inference_seconds": 0.0,
                   "output_sha256": {n: REPLAY.sha(self.run / n) for n in
                                     ("source_requests.jsonl", "requests.jsonl", "predictions.jsonl", "freeze.json")}}
        FIXTURE.write_json(self.run / "summary.json", summary)

    def _write_ranking(self):
        quotes, empty = [], []
        for original in self.original:
            source = deepcopy(original)
            if source["provenance"]["window"]["quote"].strip():
                source["provenance"].update(original_relation_request_id=original["id"],
                                             original_relation_premise=original["premise"], hypothesis_style="quote_axis")
                source["id"] += ":quote"
                source["premise"] = source["provenance"]["window"]["quote"]
                quotes.append(source)
            else:
                source.update(not_inferred_reason="literal_quote_contains_only_whitespace", relation="unknown", scope_proven=False)
                empty.append(source)
        FIXTURE.write_rows(self.ranking / "source-requests.jsonl", quotes)
        FIXTURE.write_rows(self.ranking / "source-empty-quote-requests.jsonl", empty)
        FIXTURE.write_json(self.ranking / "source-manifest.json", {
            "request_count": len(quotes), "empty_literal_quote_request_count": len(empty),
            "output_sha256": {n: REPLAY.sha(self.ranking / ("source-" + n)) for n in
                              ("requests.jsonl", "empty-quote-requests.jsonl")}})
        rows = REPLAY.RANKING.RANKING.prepare_rows(quotes, empty)
        rankings, selections = REPLAY.RANKING.RANKING.score_rows(rows, InventedReranker())
        FIXTURE.write_rows(self.ranking / "ranking-requests.jsonl", rows)
        FIXTURE.write_rows(self.ranking / "rankings.jsonl", rankings)
        FIXTURE.write_rows(self.ranking / "top-k-windows.jsonl", selections)
        freeze = {"input_sha256": {n: REPLAY.sha(self.ranking / ("source-" + n)) for n in
                                    ("requests.jsonl", "empty-quote-requests.jsonl", "manifest.json")},
                  "code_sha256": self._code(self.ranking), "top_ks_for_diagnostics": [1, 3, 5],
                  "ranking_requests_sha256": REPLAY.sha(self.ranking / "ranking-requests.jsonl"),
                  "score_is_condition_confidence": False, "scope_proven": False, "query_style": "axis_options"}
        FIXTURE.write_json(self.ranking / "freeze.json", freeze)
        FIXTURE.write_json(self.ranking / "summary.json", {**freeze, "output_sha256": {
            p.name: REPLAY.sha(p) for p in self.ranking.iterdir() if p.is_file() and p.name != "summary.json"}})

    def score(self):
        return REPLAY.score(self.run, self.ranking, [self.labels], self.base / "score.json")

    def bound(self):
        _, records, _ = REPLAY.GLINER.load_verified(self.run)
        _, rankings, selections, sources = REPLAY.RANKING.load_rankings_verified(self.ranking)
        return records, rankings, selections, sources

    def test_prequote_alternative_ids_bind_without_suffix_casts_and_competition_abstains(self):
        result = self.score()
        self.assertEqual(result["diagnostics"]["1"]["0.9"]["metrics"]["true_support"], 1)
        self.assertEqual(result["diagnostics"]["3"]["0.9"]["metrics"]["true_support"], 0)
        condition = next(r for r in result["diagnostics"]["3"]["0.9"]["conditions"] if r["task_id"] == "t0")
        self.assertEqual(condition["relation"], "unknown")
        self.assertTrue(condition["competing_support_window_ids"])
        self.assertFalse(result["scope_proven"])
        self.assertFalse(result["production_eligible"])

    def test_empty_retrieval_task_is_preserved_even_if_gliner_chose_a_class(self):
        result = self.score()
        for grid in result["diagnostics"].values():
            for diagnostic in grid.values():
                self.assertEqual(diagnostic["metrics"]["count"], 2)
                row = next(r for r in diagnostic["conditions"] if r["task_id"] == "empty-task")
                self.assertEqual(row["relation"], "unknown")
                self.assertEqual(row["available_ranked_window_count"], 0)
                self.assertEqual(row["excluded_retrieval_windows"][0]["status"], "empty_literal_quote")

    def test_length_skips_keep_the_condition_and_undefined_precision(self):
        for record in self.predictions:
            record.update(status="input_too_long", chosen_value=None, confidence=None, probabilities=None,
                          actual_encoder_input_token_count=513)
            record.pop("schema_probabilities")
            record.pop("chosen_schema_label")
        self._write_gliner()
        result = self.score()
        metric = result["diagnostics"]["5"]["0.5"]["metrics"]
        self.assertEqual((metric["count"], metric["predicted_support"]), (2, 0))
        self.assertIsNone(metric["support_precision"])

    def test_missing_selected_window_is_a_join_failure_before_labels(self):
        records, rankings, selections, sources = self.bound()
        with self.assertRaisesRegex(ValueError, "missing a required"):
            REPLAY.bind_rows([r for r in records if r["id"] != "w1"], rankings, selections, sources, "premise")

    def test_literal_window_and_full_context_mutations_are_rejected(self):
        original, rankings, selections, sources = self.bound()
        for kind in ("literal", "context", "row", "option", "source_id"):
            with self.subTest(kind=kind):
                records = deepcopy(original)
                r = records[0]
                if kind == "literal":
                    r["provenance"]["window"]["quote"] += "改変"
                elif kind == "context":
                    r["source_premise"] += "架空の別引用"
                elif kind == "row":
                    r["provenance"]["selected_au_row"]["axes"][0]["value"] = "赤"
                elif kind == "option":
                    r["provenance"]["option_values"][0] = "21枚"
                else:
                    r["source_request_ids"][0] += ":invented"
                with self.assertRaises(ValueError):
                    REPLAY.bind_rows(records, rankings, selections, sources, "premise")

    def test_quote_source_id_form_preserves_the_original_relation_id_metadata(self):
        records, rankings, selections, sources = self.bound()
        source_by_id = {r["id"]: r for r in sources}
        rank_by_id = {r["source_request_id"]: r for r in rankings}
        for record in records:
            rank = rank_by_id[record["id"]]
            record.update(source_request_ids=rank["source_relation_request_ids"],
                          source_original_relation_request_ids=[source_by_id[i]["provenance"]["original_relation_request_id"]
                                                                if "original_relation_request_id" in source_by_id[i]["provenance"]
                                                                else i for i in rank["source_relation_request_ids"]],
                          source_input_premise=rank["passage"])
        REPLAY.bind_rows(records, rankings, selections, sources, "original_relation_premise")
        records[0]["source_original_relation_request_ids"][0] = "different-original"
        with self.assertRaisesRegex(ValueError, "original relation ID"):
            REPLAY.bind_rows(records, rankings, selections, sources, "original_relation_premise")

    def test_numeric_task_id_is_not_coerced_into_a_matching_string(self):
        records, rankings, selections, sources = self.bound()
        records[0]["provenance"]["task_id"] = 0
        with self.assertRaisesRegex(ValueError, "source window ID"):
            REPLAY.bind_rows(records, rankings, selections, sources, "premise")

    def test_other_whole_alternative_only_never_proves_conflict(self):
        for i, request in enumerate(self.requests):
            self.predictions[i] = FIXTURE.inferred(request, 1)
        self._write_gliner()
        result = self.score()
        for diagnostic in result["diagnostics"]["5"].values():
            self.assertEqual(diagnostic["metrics"]["predicted_support"], 0)
            self.assertTrue(all(r["relation"] == "unknown" for r in diagnostic["conditions"]))

    def test_integrity_rejection_precedes_malformed_annotation_read(self):
        self.labels.write_text("not JSON")
        with (self.run / "predictions.jsonl").open("a") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(ValueError, "output SHA"):
            self.score()

    def test_existing_output_is_not_replaced(self):
        path = self.base / "score.json"
        path.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.score()
        self.assertEqual(path.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
