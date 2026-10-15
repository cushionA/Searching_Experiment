"""Pure-function tests for the frozen v7 single-model NLI preparation seam."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
EXPERIMENTS = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(EXPERIMENTS))

import kaggle_v7_small_nli_single_v1 as worker
import prepare_v7_small_nli_single_v1 as prep


class CharacterTokenizer:
    """Deterministic tokenizer stand-in: one token per character plus pair cost."""

    def __call__(self, premise, hypothesis, **kwargs):
        return {"input_ids": list(range(len(premise) + len(hypothesis) + 3))}


def context(body, *, prefix="title / row", options=None):
    return {
        "context_ref": 7,
        "axis_name": "色",
        "option_values": options or ["赤", "青", "緑"],
        "prefix": prefix,
        "body_text": body,
    }


def pair_record(pair, probabilities, **extra):
    return {"id": pair["id"], "probabilities": probabilities,
            "token_count_untruncated": 20, **extra}


class V7SmallNLISingleWorkerTests(unittest.TestCase):
    def test_body_chunks_cover_every_character_once_and_keep_all_options(self):
        body = "色とりどりの商品本文。" * 145
        pairs, issues = worker.prepare_context_pairs(context(body), CharacterTokenizer())
        self.assertEqual(issues, [])
        body_pairs = [pair for pair in pairs if pair["view"] == "body"]
        spans = {}
        for pair in body_pairs:
            spans.setdefault(pair["chunk_index"], (pair["source_start"], pair["source_end"], set()))
            start, end, options = spans[pair["chunk_index"]]
            self.assertEqual((pair["source_start"], pair["source_end"]), (start, end))
            options.add(pair["option_index"])
            self.assertLessEqual(len(pair["premise"]) + len(pair["hypothesis"]) + 3,
                                 worker.MAX_TOKENS)
        ordered = [spans[index] for index in sorted(spans)]
        self.assertGreater(len(ordered), 1)
        self.assertEqual(ordered[0][0], 0)
        self.assertEqual(ordered[-1][1], len(body))
        for expected_index, (start, end, option_indices) in enumerate(ordered):
            self.assertEqual(start, 0 if expected_index == 0 else ordered[expected_index - 1][1])
            self.assertGreater(end, start)
            self.assertEqual(option_indices, set(range(len(context(body)["option_values"]))))
            chunk_text = body[start:end]
            self.assertEqual(sum(len(body[a:b]) for a, b, _ in ordered[:expected_index + 1]), end)
            self.assertIn(chunk_text, [p["premise"].split("引用本文：\n", 1)[1]
                                       for p in body_pairs if p["chunk_index"] == expected_index])

    def test_body_only_change_preserves_prefix_and_hypotheses(self):
        first, second = context("短い本文"), context("別の本文")
        first_pairs, first_issues = worker.prepare_context_pairs(first, CharacterTokenizer())
        second_pairs, second_issues = worker.prepare_context_pairs(second, CharacterTokenizer())
        self.assertEqual(first_issues, [])
        self.assertEqual(second_issues, [])
        self.assertEqual(first["prefix"], second["prefix"])
        for view in worker.VIEWS:
            left = [p for p in first_pairs if p["view"] == view]
            right = [p for p in second_pairs if p["view"] == view]
            self.assertEqual([p["hypothesis"] for p in left], [p["hypothesis"] for p in right])
        left_body = next(p for p in first_pairs if p["view"] == "body")
        right_body = next(p for p in second_pairs if p["view"] == "body")
        fixed_prefix = first["prefix"] + "\n引用本文：\n"
        self.assertTrue(left_body["premise"].startswith(fixed_prefix))
        self.assertTrue(right_body["premise"].startswith(fixed_prefix))
        self.assertNotEqual(left_body["premise"], right_body["premise"])

    def test_over_512_metadata_is_explicit_unknown(self):
        oversized = context("body", prefix="x" * 600, options=["赤"])
        pairs, issues = worker.prepare_context_pairs(oversized, CharacterTokenizer())
        self.assertEqual(pairs, [])
        self.assertTrue(any(issue["reason"] == "metadata_exceeds_512_tokens"
                            for issue in issues))
        states = worker.aggregate_option_states(oversized, pairs, [], issues)
        self.assertTrue(states)
        self.assertTrue(all(row["state"] == "unknown" for row in states))

    def test_mixed_support_conflict_missing_output_and_technical_output_abstain(self):
        ctx = context("body", options=["赤", "青", "緑"])
        pairs = [
            {"id": "support", "view": "body", "option_index": 0},
            {"id": "conflict", "view": "body", "option_index": 0},
            {"id": "other-option", "view": "body", "option_index": 1},
            {"id": "missing", "view": "body", "option_index": 2},
            {"id": "technical", "view": "title_row", "option_index": 0},
        ]
        records = [
            pair_record(pairs[0], {"support": .95, "conflict": .02, "unknown": .03}),
            pair_record(pairs[1], {"support": .01, "conflict": .96, "unknown": .03}),
            pair_record(pairs[2], {"support": .93, "conflict": .02, "unknown": .05}),
            pair_record(pairs[4], {"support": .97, "conflict": .01, "unknown": .02},
                        error_type="RuntimeError"),
        ]
        with mock.patch.object(worker.NLI, "_probabilities", side_effect=lambda row: row["probabilities"]), \
                mock.patch.object(worker.NLI, "THRESHOLD", .8):
            states = worker.aggregate_option_states(ctx, pairs, records)
        by_key = {(row["view"], row["option_index"]): row for row in states}
        self.assertEqual(by_key[("body", 0)]["state"], "unknown")
        self.assertIn("support_conflict_coexist",
                      [reason["reason"] for reason in by_key[("body", 0)]["unknown_reasons"]])
        self.assertEqual(by_key[("body", 1)]["state"], "support")
        self.assertEqual(by_key[("body", 2)]["state"], "unknown")
        self.assertIn("missing_model_output",
                      [reason["reason"] for reason in by_key[("body", 2)]["unknown_reasons"]])
        self.assertEqual(by_key[("title_row", 0)]["state"], "unknown")
        self.assertIn("technical_or_invalid_model_output",
                      [reason["reason"] for reason in by_key[("title_row", 0)]["unknown_reasons"]])

    def test_other_option_support_does_not_create_a_conflict(self):
        ctx = context("body", options=["赤", "青"])
        pairs = [{"id": "neutral-red", "view": "body", "option_index": 0},
                 {"id": "support-blue", "view": "body", "option_index": 1}]
        records = [
            pair_record(pairs[0], {"support": .1, "conflict": .1, "unknown": .8}),
            pair_record(pairs[1], {"support": .95, "conflict": .02, "unknown": .03}),
        ]
        with mock.patch.object(worker.NLI, "_probabilities", side_effect=lambda row: row["probabilities"]), \
                mock.patch.object(worker.NLI, "THRESHOLD", .8):
            states = worker.aggregate_option_states(ctx, pairs, records)
        body = {row["option_index"]: row["state"] for row in states if row["view"] == "body"}
        self.assertEqual(body, {0: "unknown", 1: "support"})

    def test_run_checks_package_counts_before_weight_acquisition(self):
        class ReachedWeightAcquisition(Exception):
            pass

        ctx = context("body", options=["赤", "青"])
        ctx["document_sha256"] = "a" * 64
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle_dir = root / "bundle"
            bundle_dir.mkdir()
            contexts_path = bundle_dir / "contexts.jsonl"
            contexts_path.write_text(json.dumps(ctx, ensure_ascii=False) + "\n", encoding="utf-8")
            manifest_path = bundle_dir / "BUNDLE_MANIFEST.json"
            manifest = {"context_count": 1, "option_count": 2}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            verified = {"files": {"contexts.jsonl": {}}, "manifest_sha256": "b" * 64}

            output_ok = root / "valid-output"
            with mock.patch.object(worker.GPU, "verify_bundle", return_value=verified), \
                    mock.patch.object(worker.tempfile, "mkdtemp", return_value=str(root / "weights")), \
                    mock.patch.object(worker, "acquire_nli_weights",
                                      side_effect=ReachedWeightAcquisition) as acquire:
                with self.assertRaises(ReachedWeightAcquisition):
                    worker.run(bundle_dir, output_ok)
            self.assertTrue(output_ok.is_dir())
            acquire.assert_called_once()

            output_missing = root / "missing-count-output"
            manifest_path.write_text(json.dumps({"context_count": 1}), encoding="utf-8")
            with mock.patch.object(worker.GPU, "verify_bundle", return_value=verified), \
                    mock.patch.object(worker.tempfile, "mkdtemp", return_value=str(root / "weights")), \
                    mock.patch.object(worker, "acquire_nli_weights") as acquire:
                with self.assertRaisesRegex(ValueError, "BUNDLE_MANIFEST option_count"):
                    worker.run(bundle_dir, output_missing)
            self.assertFalse(output_missing.exists())
            acquire.assert_not_called()


class V7SmallNLISinglePrepTests(unittest.TestCase):
    def setUp(self):
        self.doc = {
            "document_id": "doc-1", "document_sha256": "a" * 64,
            "title": {"text": "品名"}, "description": {"text": "説明"},
        }
        self.request = {
            "request_id": "req-1", "target_document_id": "doc-1",
            "target_document_sha256": "a" * 64, "axis_name": "色",
            "option_values": ["赤", "青"], "selected_value": "赤",
            "target_selected_axes_text": "色：赤",
        }

    def context_bytes(self, contexts):
        return b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")) + "\n").encode("utf-8")
                        for row in contexts)

    def test_desired_selection_only_changes_host_binding(self):
        first_contexts, first_bindings = prep.make_contexts([self.request], {"doc-1": self.doc})
        changed = copy.deepcopy(self.request)
        changed["selected_value"] = "青"
        second_contexts, second_bindings = prep.make_contexts([changed], {"doc-1": self.doc})
        self.assertEqual(self.context_bytes(first_contexts), self.context_bytes(second_contexts))
        self.assertEqual(first_bindings[0]["context_ref"], second_bindings[0]["context_ref"])
        self.assertNotEqual(first_bindings[0]["selected_option_index"],
                            second_bindings[0]["selected_option_index"])

    def test_changed_actual_selected_row_changes_public_context(self):
        first_contexts, _ = prep.make_contexts([self.request], {"doc-1": self.doc})
        changed = copy.deepcopy(self.request)
        changed["target_selected_axes_text"] = "色：青"
        second_contexts, _ = prep.make_contexts([changed], {"doc-1": self.doc})
        self.assertNotEqual(self.context_bytes(first_contexts), self.context_bytes(second_contexts))
        self.assertNotEqual(first_contexts[0]["prefix"], second_contexts[0]["prefix"])


if __name__ == "__main__":
    unittest.main()
