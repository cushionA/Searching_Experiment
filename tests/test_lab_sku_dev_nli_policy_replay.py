"""Offline regression contracts for the frozen DEV NLI policy replay."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKU_DIR = ROOT / "experiments/sku-matching"
if str(SKU_DIR) not in sys.path:
    sys.path.insert(0, str(SKU_DIR))
SPEC = importlib.util.spec_from_file_location(
    "replay_dev_nli_policies_v1", SKU_DIR / "replay_dev_nli_policies_v1.py")
REPLAY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REPLAY)


def _request():
    return {
        "request_id": "req-1",
        "request_sha256": "a" * 64,
        "direction": "synthetic",
        "axis_name": "色",
        "selected_value": "赤",
        "target_document_id": "doc-1",
        "option_values": ["赤", "青"],
        "target_selected_axes_text": "色：赤",
    }


def _candidate_records(request=None, document=None, bindings=None):
    request = request or _request()
    document = document or {"document_id": "doc-1", "title": {"text": "synthetic title"}}
    bindings = bindings or {"req-1": [{"task_id": "task-1"}]}
    from trial_scoped_condition_nli_choice_v1 import pairs_for

    pair_list = pairs_for([request], {"doc-1": document}, bindings)
    records = []
    for pair in pair_list:
        records.append({
            "id": pair["id"],
            "request_id": pair["request_id"],
            "request_sha256": pair["request_sha256"],
            "option_index": pair["option_index"],
            "option_value": pair["option_value"],
            "premise": pair["premise"],
            "hypothesis": pair["hypothesis"],
            "model_id": REPLAY.NLI.MODEL_ID,
            "model_revision": REPLAY.NLI.MODEL_REVISION,
            "precision": "fp32",
            "cache_key": REPLAY.NLI.pair_key(pair["premise"], pair["hypothesis"]),
            "token_count_untruncated": 20,
            "error_type": None,
            "probabilities": {"support": 0.95, "conflict": 0.02, "unknown": 0.03},
        })
    return request, {"doc-1": document}, bindings, pair_list, records


class DevNliPolicyReplayTests(unittest.TestCase):
    def test_unknown_competing_row_prevents_acceptance(self):
        rows = [
            {"case_id": "case-1", "au_row_key": "candidate", "bindings": [{"request_id": "support"}]},
            {"case_id": "case-1", "au_row_key": "uncertain", "bindings": [{"request_id": "unknown"}]},
        ]

        row_outputs, decisions = REPLAY.assemble(
            rows, [], {"support": "support", "unknown": "unknown"})

        self.assertEqual([r["state"] for r in row_outputs], ["support", "unknown"])
        self.assertEqual(decisions, [{"case_id": "case-1", "decision": "drop",
                                      "au_row_key": None}])

    def test_incomplete_all_option_scores_require_an_original_skip(self):
        request, documents, bindings, _, records = _candidate_records()
        with self.assertRaises(ValueError):
            REPLAY.validate_records(records[:1], [request], documents, bindings)
        grouped = REPLAY.validate_records([], [request], documents, bindings, {request['request_id']})
        self.assertEqual(dict(grouped), {})

    def test_accepted_row_identity_is_visible_to_existing_grader(self):
        rows = [{'case_id': 'case-1', 'au_row_key': 'accepted', 'bindings': [{'request_id': 's'}]}]
        _, decisions = REPLAY.assemble(rows, [], {'s': 'support'})
        self.assertEqual(REPLAY.GRADE.canonical_decision(decisions[0]), ('matched', 'accepted'))

    def test_pruning_counts_every_positive_row_and_distinguishes_partial_from_total_loss(self):
        rows = [
            {"case_id": "partial", "au_row_key": "p1", "state": "conflict"},
            {"case_id": "partial", "au_row_key": "p2", "state": "support"},
            {"case_id": "total", "au_row_key": "t1", "state": "conflict"},
            {"case_id": "total", "au_row_key": "t2", "state": "conflict"},
        ]
        references = {
            "partial": {"label": "matched", "matched_au_row_keys": ["p1", "p2"]},
            "total": {"label": "matched", "matched_au_row_keys": ["t1", "t2"]},
        }

        metrics = REPLAY.pruning_metrics(rows, references)

        self.assertEqual(metrics["known_matching_row_count"], 4)
        self.assertEqual(metrics["matching_rows_marked_conflict"], 3)
        self.assertEqual(metrics["matching_row_conflict_rate"], 0.75)
        self.assertEqual(metrics["positive_cases_losing_all_matching_rows"], ["total"])

    def test_absolute_policy_returns_unknown_for_long_error_and_malformed_records(self):
        base = {"token_count_untruncated": 20, "error_type": None,
                "probabilities": {"support": 0.95, "conflict": 0.02, "unknown": 0.03}}
        bad_records = [
            {**base, "token_count_untruncated": 513},
            {**base, "error_type": "RuntimeError"},
            {**base, "probabilities": {"support": 0.95}},
            {**base, "probabilities": None},
        ]

        for record in bad_records:
            with self.subTest(record=record):
                self.assertEqual(REPLAY.absolute_state(record), "unknown")

    def test_saved_scores_reject_changed_text_and_model_or_cache_identity(self):
        request, documents, bindings, _, records = _candidate_records()
        mutations = [
            ("premise", "altered exact premise"),
            ("hypothesis", "altered exact hypothesis"),
            ("model_id", "different-model"),
            ("model_revision", "different-revision"),
            ("precision", "bf16"),
            ("cache_key", "0" * 64),
        ]

        for field, value in mutations:
            with self.subTest(field=field):
                changed = [dict(record) for record in records]
                changed[0][field] = value
                with self.assertRaises(ValueError):
                    REPLAY.validate_records(changed, [request], documents, bindings)


if __name__ == "__main__":
    unittest.main()
