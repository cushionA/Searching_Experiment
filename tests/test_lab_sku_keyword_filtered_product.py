from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "experiments/sku-matching"))

import trial_keyword_filtered_product_v1 as EXP
import trial_generic_model_jev_v1 as JEV


def jev_record(request, logits):
    labels = [choice["label"] for choice in request["choices"]]
    values = [logits[label] for label in labels]
    maximum = max(values)
    exp_values = [math.exp(value - maximum) for value in values]
    total = sum(exp_values)
    probabilities = [value / total for value in exp_values]
    winner = max(range(len(values)), key=lambda index: values[index])
    key = JEV.request_key(request["question"], request["state"], request["choices"])
    return {**request, "request": request, "cache_key": key,
            "model_id": JEV.MODEL_ID, "model_revision": JEV.MODEL_REVISION,
            "precision": "fp32", "format_version": JEV.FORMAT_VERSION,
            "status": "ok", "logits": dict(zip(labels, values)),
            "probabilities": dict(zip(labels, probabilities)),
            "argmax_label": labels[winner], "argmax_index": winner,
            "argmax_probability": probabilities[winner],
            "token_counts": dict.fromkeys(labels, 12), "latency_seconds": 0.0,
            "note": None, "error_type": None, "pins": JEV._pins()}


class KeywordFilteredProductTests(unittest.TestCase):
    def test_empty_evidence_group_stays_empty_and_score_changes_are_rejected(self):
        selections = {"empty": {"ordered_block_ids": []},
                      "usable": {"ordered_block_ids": ["b"]}}
        scores = {"usable": {"b": {"raw_logit": 0.4, "status": "ok"}}}
        rankings, _ = EXP.filtered_rankings(selections, {"b": {"text": "内容"}}, scores)
        source = {"selections": selections, "scores": scores}
        EXP.validate_filtered_rankings(source, rankings)
        self.assertEqual(rankings["empty"]["selected_block_ids"], [])
        rankings["usable"]["ordered_scored_blocks"][0]["raw_logit"] = 9.0
        with self.assertRaisesRegex(ValueError, "changed an old reranker score"):
            EXP.validate_filtered_rankings(source, rankings)

    def test_only_explicit_keyword_headings_are_excluded(self):
        self.assertTrue(EXP.is_explicit_keyword_block({"text": "▼検索ワード\nレースカーテン 2枚セット"}))
        self.assertTrue(EXP.is_explicit_keyword_block({"text": "【関連キーワード】毛布セット"}))
        self.assertTrue(EXP.is_explicit_keyword_block({"text": "  検索キーワード：暖房 寝具"}))
        # Category terms, counts, and an unproved scope status are not filters.
        self.assertFalse(EXP.is_explicit_keyword_block({"text": "商品説明\nレースカーテン 2枚セット"}))
        self.assertFalse(EXP.is_explicit_keyword_block({"text": "内容\n毛布セット × 1", "scope_status": "unproved"}))
        self.assertFalse(EXP.is_explicit_keyword_block({"text": "本文中の検索ワードという言い回し"}))

    def test_filtered_rankings_keep_original_scores_and_promote_next_rows(self):
        group = "g1"
        blocks = {
            "keyword": {"block_id": "keyword", "text": "▼検索ワード\nカーテン 2枚"},
            "content": {"block_id": "content", "text": "内容\n遮光カーテン 2枚"},
            "category": {"block_id": "category", "text": "レースカーテンあり"},
            "unproved": {"block_id": "unproved", "text": "兄弟商品の説明", "scope_status": "unproved"},
        }
        scores = {group: {
            "keyword": {"status": "ok", "raw_logit": 9.4},
            "content": {"status": "ok", "raw_logit": 8.1},
            "category": {"status": "ok", "raw_logit": 6.2},
            "unproved": {"status": "ok", "raw_logit": 2.3},
        }}
        selections = {group: {"request_id": group,
                              "ordered_block_ids": ["keyword", "content", "category", "unproved"]}}

        filtered, excluded = EXP.filtered_rankings(selections, blocks, scores)

        self.assertEqual(filtered[group]["ordered_block_ids"], ["content", "category", "unproved"])
        self.assertEqual(filtered[group]["selected_block_ids"], ["content", "category", "unproved"])
        self.assertEqual([r["raw_logit"] for r in filtered[group]["ordered_scored_blocks"]], [8.1, 6.2, 2.3])
        self.assertEqual([r["old_rank"] for r in filtered[group]["ordered_scored_blocks"]], [2, 3, 4])
        self.assertEqual([(r["block_id"], r["old_rank"], r["raw_logit"]) for r in excluded],
                         [("keyword", 1, 9.4)])

    def test_old_cache_reuses_only_exact_raw_question_state_and_ordered_choices(self):
        old = {"id": "old-id", "question": "What is included?", "state": "This SKU includes a pad.",
               "choices": [{"label": "yes", "description": "Pad included"},
                           {"label": "unknown", "description": "unknown"}]}
        changed = {**old, "id": "new-id", "state": "This SKU includes a pad and blanket."}
        # Product JSONL uses sort_keys, so score-map order on disk need not
        # match the raw choice order consumed by JEV.run_choices.
        frozen_record = json.loads(json.dumps(
            jev_record(old, {"yes": 1.0, "unknown": 0.0}), sort_keys=True))
        cache = EXP.validated_jev_cache([frozen_record])
        seen_inference = []

        def fake_infer(pending, model_dir, threads, batch_size, on_ready):
            seen_inference.extend(row["cache_key"] for row in pending)
            pins = JEV._pins()
            records = []
            for request in pending:
                labels = [c["label"] for c in request["choices"]]
                records.append({"cache_key": request["cache_key"], "status": "ok",
                    "logits": dict.fromkeys(labels, 0.0),
                    "probabilities": dict.fromkeys(labels, 1.0 / len(labels)),
                    "argmax_label": labels[0], "argmax_index": 0,
                    "argmax_probability": 1.0 / len(labels),
                    "token_counts": dict.fromkeys(labels, 12), "latency_seconds": 0.0,
                    "note": None, "error_type": None})
            on_ready(pins, {"device": "cpu", "precision": "fp32"})
            return records, pins, {"device": "cpu", "precision": "fp32",
                "inference_seconds": 0.1, "model_load_seconds": 0.1,
                "tokenization_seconds": 0.1, "file_verification_seconds": 0.1}

        with patch.object(JEV, "_infer", side_effect=fake_infer):
            result, hits, pending = EXP.score_filtered_choices([old, changed], cache, Path("unused"))

        self.assertEqual(len(hits), 1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(seen_inference, [JEV.request_key(changed["question"], changed["state"], changed["choices"])])
        self.assertEqual(result["runtime"]["inferred_unique_request_count"], 1)
        self.assertEqual(result["runtime"]["cached_unique_request_count"], 1)
        self.assertTrue(result["records"][0]["cache_reused"])
        self.assertFalse(result["records"][1]["cache_reused"])

    def test_source_hash_drift_is_rejected_before_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            product_dir = Path(tmp)
            (product_dir / "tampered.json").write_text("changed", encoding="utf-8")
            summary = {"schema_version": "product-condition-models-v1",
                "labels_read": False, "labels_in_model_inputs": False,
                "development_only": True, "errors_only": False, "independent_test": False,
                "synthetic_data": False, "production_adoption_allowed": False,
                "device": "cpu", "jev_precision": "fp32", "reranker_precision": "int8", "top_k": 3,
                "sharing_savings": {"shared_group_count": 29},
                "input_sha256": {}, "code_sha256": {},
                "sharing_plan_sha256": "plan", "eligible_requests_sha256": "contract",
                "output_sha256": {"tampered.json": "0" * 64}}
            freeze = {"schema_version": summary["schema_version"],
                "input_sha256": summary["input_sha256"], "code_sha256": summary["code_sha256"],
                "sharing_plan_sha256": summary["sharing_plan_sha256"],
                "eligible_requests_sha256": summary["eligible_requests_sha256"]}
            (product_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            (product_dir / "freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source output SHA mismatch"):
                EXP.verify_source(product_dir)

    def test_execution_freeze_detects_source_and_scoring_input_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source-input.json"
            code = root / "scorer.py"
            requests = root / "classification-requests.jsonl"
            source.write_text('{"source":1}', encoding="utf-8")
            code.write_text("PINNED = 1", encoding="utf-8")
            requests.write_text('{"id":"request-1"}\n', encoding="utf-8")
            frozen = EXP.snapshot_paths({"source_artifact": source,
                "current_code": code, "classification_requests": requests})

            # This check is the pre-inference gate: all exact inputs still match.
            EXP.verify_snapshot(frozen, phase="before scoring")
            source.write_text('{"source":2}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed before scoring: source_artifact"):
                EXP.verify_snapshot(frozen, phase="before scoring")

            # Re-freeze a clean set; mutations after the model call are also fatal.
            source.write_text('{"source":1}', encoding="utf-8")
            frozen = EXP.snapshot_paths({"source_artifact": source,
                "current_code": code, "classification_requests": requests})
            EXP.verify_snapshot(frozen, phase="after scoring")
            requests.write_text('{"id":"request-2"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed after scoring: classification_requests"):
                EXP.verify_snapshot(frozen, phase="after scoring")


if __name__ == "__main__":
    unittest.main()
