"""Unit-level guards for short structured SKU reranker trial behavior."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments" / "sku-matching"
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("sku_structured_reranker_test", HERE / "trial_structured_reranker.py")
trial = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(trial)


class FakeModel:
    max_length = 512

    def token_lengths_pairs(self, pairs):
        return [10] * len(pairs)

    def score_pairs(self, pairs, batch_size=8):
        self.scored = list(pairs)
        self.batch_size = batch_size
        return [float(i) for i in range(len(pairs))]


class StructuredRerankerTests(unittest.TestCase):
    def _task(self, case_id="c1"):
        return {
            "case_id": case_id, "split": "dev", "group_id": "g", "dossier_id": "d",
            "rakuten": {"raw_sku": "100cm / navy", "attrs": {"size": "100cm", "color": "navy"},
                        "facts": {}, "evidence": {"ignored": "quote"}},
            "page_context": {"attrs": {"components": "2枚"}},
            "au_candidates": [
                {"row_key": "a", "raw_sku": "100cm / navy", "attrs": {"size": "100cm", "color": "navy"},
                 "facts": {}, "unknown_fields": [], "unresolved_axes": []},
                {"row_key": "b", "raw_sku": "150cm / red", "attrs": {"size": "150cm", "color": "red"},
                 "facts": {}, "unknown_fields": [], "unresolved_axes": []},
            ],
        }

    def test_only_bekko_top10_is_scored_and_identical_pairs_are_deduplicated(self):
        tasks = [self._task("c1"), self._task("c2")]
        rankings = [{"case_id": "c1", "top10": [{"row_key": "a", "score": 0.8}, {"row_key": "b", "score": 0.7}]},
                    {"case_id": "c2", "top10": [{"row_key": "a", "score": 0.9}]}]

        def feature_text(entity, mode="canonical"):
            self.assertEqual(mode, "canonical")
            if "row_key" not in entity:
                return "Q:" + entity["attrs"]["size"]
            return "C:" + entity["attrs"]["size"]

        plans, pairs, _ = trial.build_pairs(tasks, rankings, feature_text)

        self.assertEqual(len(plans), 2)
        self.assertEqual(len(pairs), 2)  # common query/candidate pair is encoded once
        self.assertEqual([item["row_key"] for item in plans[0]["bekko_top10"]], ["a", "b"])

    def test_raw_logits_rank_candidates_and_gate_is_attached_to_selected_top1(self):
        model = FakeModel()
        task = self._task()
        ranking = [{"case_id": "c1", "top10": [{"row_key": "a", "score": 0.8}, {"row_key": "b", "score": 0.7}]}]
        calls = []

        def feature_text(entity, mode="canonical"):
            return "query" if "row_key" not in entity else entity["attrs"]["size"]

        def gate(given_task, candidate):
            calls.append(candidate["row_key"])
            return ("review", ["missing_evidence"]) if candidate["row_key"] == "a" else ("unmatched", ["contradiction"])

        raw, info = trial.raw_rank_predictions(model, [task], ranking, feature_text, gate)

        self.assertEqual(raw[0]["top_row_key"], "b")  # fake scores preserve unique-input order
        self.assertEqual(raw[0]["score_kind"], "raw_relevance_logit_not_identity_probability")
        self.assertEqual(raw[0]["strict_gate_decision"], "unmatched")
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(info["unique_pair_count"], 2)

    def test_metrics_separate_candidate_recall_from_strict_gated_correct_row(self):
        raw = [
            {"case_id": "m", "split": "dev", "top_row_key": "wrong", "top10": [
                {"row_key": "gold"}, {"row_key": "wrong"}], "strict_gate_decision": "matched"},
            {"case_id": "u", "split": "test", "top_row_key": "x", "top10": [
                {"row_key": "x"}], "strict_gate_decision": "unmatched"},
        ]
        labels = [{"case_id": "m", "decision": "matched", "matching_au_row_keys": ["gold"]},
                  {"case_id": "u", "decision": "unmatched", "matching_au_row_keys": []}]

        result = trial.label_metrics(raw, labels)

        self.assertEqual(result["candidate_recall_at_10_before"]["value"], 1.0)
        self.assertEqual(result["reranker_top1_after"]["value"], 0.0)
        self.assertEqual(result["strict_gate_end_to_end"]["correct_match_and_row"], 0)
        self.assertEqual(result["strict_gate_end_to_end"]["false_accept"], 1)
        self.assertEqual(result["strict_gate_end_to_end"]["missed_match_or_review_or_wrong_row"], 1)
        self.assertTrue(result["test_is_reused_diagnostic_only"])

    def test_prediction_reader_supports_canonical_parallel_key_score_lists(self):
        entries = trial.top10_entries({"case_id": "c", "top10row_key": ["r1", "r2"],
                                       "top10score": [0.2, 0.1]})

        self.assertEqual(entries, [{"row_key": "r1", "bekko_score": 0.2},
                                   {"row_key": "r2", "bekko_score": 0.1}])


if __name__ == "__main__":
    unittest.main()
