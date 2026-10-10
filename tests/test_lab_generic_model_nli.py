from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/trial_generic_model_nli_v1.py"
SPEC = importlib.util.spec_from_file_location("generic_model_nli_v1", SCRIPT)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def pair(pair_id="p1", premise="引用原文", hypothesis="この商品には付属品がある。", **meta):
    return {"id": pair_id, "premise": premise, "hypothesis": hypothesis, **meta}


def successful_inference(pairs, model_dir, threads, batch_size, on_ready):
    records = [{"task_id": p["cache_key"],
                "probabilities": {"support": 0.96, "conflict": 0.02, "unknown": 0.02},
                "token_count_untruncated": 30, "latency_seconds": 0.1, "note": None}
               for p in pairs]
    pins = {"model_id": RUNNER.MODEL_ID, "revision": RUNNER.MODEL_REVISION,
            "parameter_count": RUNNER.FP32_PARAMETER_COUNT}
    runtime = {"inference_seconds": 0.1, "precision": "fp32"}
    on_ready(pins, runtime)
    return records, pins, runtime


class GenericModelNliTests(unittest.TestCase):
    def test_condition_quote_keeps_literal_raw_references_and_scope_flag(self):
        binding = {"quote": " 色A  ／  部品無し ",
                   "span": {"quote": " 色A  ／  部品無し ", "start": 12, "end": 27,
                            "raw_file": "raw/item.json", "sha256": "a" * 64},
                   "source_scope": {"verified": False, "model_scope_id": "scope-1"}}
        metadata = {"case_id": "c1", "au_row_key": "r1"}
        item = RUNNER.condition_pair("p1", "付属品はある。", binding, metadata)
        with patch.object(RUNNER, "_infer", side_effect=successful_inference):
            output = RUNNER.run_pairs([item])
        self.assertEqual(output["records"][0]["premise"], binding["quote"])
        self.assertEqual(output["records"][0]["verified_quote"], binding)
        self.assertEqual(output["records"][0]["fixed_row_meta"], metadata)
        self.assertFalse(output["records"][0]["verified_quote"]["source_scope"]["verified"])
        self.assertFalse(output["labels_read"])

    def test_exact_repeated_pairs_deduplicate_across_rows_and_keep_input_order(self):
        items = [pair("z", fixed_row_meta={"row": 1}), pair("a", fixed_row_meta={"row": 2})]
        with patch.object(RUNNER, "_infer", side_effect=successful_inference) as infer:
            result = RUNNER.run_pairs(items, threads=4)
        self.assertEqual(len(infer.call_args.args[0]), 1)
        self.assertEqual([r["id"] for r in result["records"]], ["z", "a"])
        self.assertEqual([r["fixed_row_meta"]["row"] for r in result["records"]], [1, 2])
        self.assertEqual(result["runtime"]["unique_pair_count"], 1)
        self.assertEqual([r["cache_reused"] for r in result["records"]], [False, True])

    def test_hash_does_not_normalize_opaque_raw_values(self):
        first = RUNNER.pair_key("寸法：100×200cm。", "寸法は100×200cmです。")
        second = RUNNER.pair_key("寸法：100x200cm。", "寸法は100×200cmです。")
        third = RUNNER.pair_key("寸法：100×200cm。 ", "寸法は100×200cmです。")
        self.assertEqual(len({first, second, third}), 3)

    def test_persistent_cache_reuses_probabilities_but_preserves_new_metadata(self):
        with patch.object(RUNNER, "_infer", side_effect=successful_inference):
            original = RUNNER.run_pairs([pair("old", fixed_row_meta={"row": 1})])["records"][0]
        cache = {original["cache_key"]: original}
        with patch.object(RUNNER, "_infer") as infer:
            result = RUNNER.run_pairs([pair("new", fixed_row_meta={"row": 2})], cache=cache)
        infer.assert_not_called()
        record = result["records"][0]
        self.assertEqual(record["id"], "new")
        self.assertEqual(record["fixed_row_meta"], {"row": 2})
        self.assertEqual(record["relation"], "support")
        self.assertEqual(result["runtime"]["cached_unique_pair_count"], 1)

    def test_wrong_model_or_raw_text_cannot_be_used_as_cache(self):
        with patch.object(RUNNER, "_infer", side_effect=successful_inference):
            record = RUNNER.run_pairs([pair()])["records"][0]
        for update in ({"model_revision": "different"}, {"precision": "int8"}, {"premise": "別の引用"}):
            with self.subTest(update=update):
                cache = {record["cache_key"]: {**record, **update}}
                with self.assertRaisesRegex(ValueError, "model identity|raw text"):
                    RUNNER.run_pairs([pair()], cache=cache)

    def test_quote_binding_mismatch_is_rejected_before_loading(self):
        binding = {"quote": "原文", "span": {"quote": "改変"}, "source_scope": {"verified": True}}
        with self.assertRaisesRegex(ValueError, "exact quote"):
            RUNNER.condition_pair("p1", "条件", binding, {"row": 1})
        with patch.object(RUNNER, "_infer") as infer:
            with self.assertRaisesRegex(ValueError, "premise differs"):
                RUNNER.run_pairs([pair(verified_quote={"quote": "違う文"})])
        infer.assert_not_called()

    def test_duplicate_ids_are_rejected_even_when_raw_pairs_differ(self):
        with patch.object(RUNNER, "_infer") as infer:
            with self.assertRaisesRegex(ValueError, "duplicate pair id"):
                RUNNER.run_pairs([pair(), pair(hypothesis="別の条件")])
        infer.assert_not_called()

    def test_uncertain_probabilities_keep_raw_scores_and_yield_unknown(self):
        def uncertain(*args):
            records, pins, runtime = successful_inference(*args)
            records[0]["probabilities"] = {"support": 0.85, "conflict": 0.1, "unknown": 0.05}
            return records, pins, runtime
        with patch.object(RUNNER, "_infer", side_effect=uncertain):
            record = RUNNER.run_pairs([pair()])["records"][0]
        self.assertEqual(record["relation"], "unknown")
        self.assertEqual(record["predicted_class"], "support")
        self.assertEqual(record["probabilities"]["support"], 0.85)

    def test_long_inputs_and_failed_inference_remain_unknown(self):
        for token_count, note in ((513, "input exceeds 512 tokens; no truncation; unknown"),
                                  (30, "inference error: RuntimeError")):
            def unknown(*args):
                records, pins, runtime = successful_inference(*args)
                records[0].update(probabilities=None, token_count_untruncated=token_count, note=note)
                return records, pins, runtime
            with self.subTest(token_count=token_count), patch.object(RUNNER, "_infer", side_effect=unknown):
                record = RUNNER.run_pairs([pair(premise="長い原文" * 1000)])["records"][0]
            self.assertEqual(record["relation"], "unknown")
            self.assertIsNone(record["probabilities"])
            self.assertEqual(record["token_count_untruncated"], token_count)
            self.assertEqual(record["premise"], "長い原文" * 1000)
            self.assertEqual(record["note"], note)
        self.assertFalse(RUNNER.exceeds_token_limit(512))
        self.assertTrue(RUNNER.exceeds_token_limit(513))

    def test_overlong_cached_probabilities_cannot_propose_support(self):
        with patch.object(RUNNER, "_infer", side_effect=successful_inference):
            record = RUNNER.run_pairs([pair()])["records"][0]
        record["token_count_untruncated"] = 513
        result = RUNNER.run_pairs([pair()], cache={record["cache_key"]: record})
        self.assertEqual(result["records"][0]["relation"], "unknown")

    def test_nonfinite_or_nonexclusive_probabilities_are_rejected(self):
        for values in ((float("nan"), 0, 1), (1, 0, 1)):
            def invalid(*args):
                records, pins, runtime = successful_inference(*args)
                records[0]["probabilities"] = dict(zip(("support", "conflict", "unknown"), values))
                return records, pins, runtime
            with self.subTest(values=values), patch.object(RUNNER, "_infer", side_effect=invalid):
                with self.assertRaises(ValueError):
                    RUNNER.run_pairs([pair()])

    def test_fp32_parameter_count_pin_is_enforced(self):
        def wrong_pin(*args):
            records, pins, runtime = successful_inference(*args)
            pins["parameter_count"] = 123
            return records, pins, runtime
        with patch.object(RUNNER, "_infer", side_effect=wrong_pin):
            with self.assertRaisesRegex(RuntimeError, "parameter count"):
                RUNNER.run_pairs([pair()])


if __name__ == "__main__":
    unittest.main()
