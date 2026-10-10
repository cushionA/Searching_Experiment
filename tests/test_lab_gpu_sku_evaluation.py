"""Focused contract tests for local scoring of frozen GPU SKU outputs."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/evaluate_gpu_sku_trial.py"
spec = importlib.util.spec_from_file_location("evaluate_gpu_sku_trial_test", SCRIPT)
assert spec and spec.loader
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


class GpuSkuEvaluationTests(unittest.TestCase):
    def test_invalid_and_missing_outputs_are_retained_as_review(self):
        task = {"case_id": "a", "dossier_id": "d", "split": "dev", "au_candidates": [{"row_key": "r"}]}
        gold = {"a": {"decision": "matched", "matching_au_row_keys": ["r"]}}
        scored, metrics = evaluator.score_rows([task], gold, [{"case_id": "a", "status": "invalid_output", "parsed": None}])
        self.assertEqual(scored[0]["prediction"]["decision"], "review")
        self.assertEqual(metrics["deferred_matched_to_review"], 1)
        self.assertEqual(metrics["invalid_or_error_count"], 1)

    def test_precision_counts_wrong_selected_row_and_gold_review_acceptance(self):
        rows = [
            {"gold_decision": "matched", "gold_row_keys": ["right"], "prediction": {"decision": "matched", "au_row_key": "wrong"}, "raw_record": {"status": "ok"}},
            {"gold_decision": "review", "gold_row_keys": [], "prediction": {"decision": "matched", "au_row_key": "x"}, "raw_record": {"status": "ok"}},
        ]
        m = evaluator.aggregate(rows)
        self.assertEqual(m["accepted_precision_including_wrong_rows"], 0)
        self.assertEqual(m["wrong_row_accept_count"], 1)
        self.assertEqual(m["accepted_gold_review_count"], 1)

    def test_decisive_gpu_output_requires_the_fixed_pool_gate(self):
        task = {"rakuten": {"attrs": {"lace_count": 0}}, "au_candidates": [
            {"row_key": "r1", "attrs": {"lace_count": 0}},
            {"row_key": "r2", "attrs": {"lace_count": 1}},
        ]}
        self.assertEqual(evaluator.constrained_prediction(task, {"status": "ok", "parsed": {"decision": "matched", "au_row_key": "r1"}})["decision"], "matched")
        # One explicit match plus one conflict means a model's page-level unmatched claim is not proven.
        self.assertEqual(evaluator.constrained_prediction(task, {"status": "ok", "parsed": {"decision": "unmatched"}})["decision"], "review")

    def test_latency_summary_uses_measured_rows_only(self):
        m = evaluator.latency_stats([{"latency_seconds": 1.0, "input_tokens": 3, "output_tokens": 2, "peak_vram_bytes": 100},
                                     {"latency_seconds": 3.0, "input_tokens": 4, "output_tokens": 1, "peak_vram_bytes": 120},
                                     {"status": "error"}])
        self.assertEqual(m["median_seconds"], 2.0)
        self.assertEqual(m["p95_seconds"], 3.0)
        self.assertEqual(m["peak_vram_bytes_max"], 120)

    def test_full_task_and_cpu_sources_can_be_selected_in_gpu_input_order(self):
        full = [{"case_id": "a"}, {"case_id": "b"}, {"case_id": "c"}]
        sample = [{"case_id": "c"}, {"case_id": "a"}]
        self.assertEqual([r["case_id"] for r in evaluator.select_by_ids(full, sample, "tasks")], ["c", "a"])
        with self.assertRaisesRegex(ValueError, "does not cover"):
            evaluator.select_by_ids(full, [{"case_id": "missing"}], "CPU")

    def test_full_label_file_may_cover_cases_outside_gpu_sample(self):
        tasks = [
            {"case_id": "sample", "au_candidates": [{"row_key": "r"}]},
            {"case_id": "outside", "au_candidates": [{"row_key": "x"}]},
        ]
        labels = [
            {"case_id": "sample", "decision": "matched", "matching_au_row_keys": ["r"]},
            {"case_id": "outside", "decision": "unmatched", "matching_au_row_keys": []},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in labels), encoding="utf-8")
            joined = evaluator.gold_join(tasks, path, {"sample"})
        self.assertEqual(set(joined), {"sample"})

    def test_cpu_adapter_preserves_decision_and_selected_key(self):
        pred = evaluator.prediction_for({"case_id": "cpu", "decision": "matched", "top_row_key": "au-row"})
        self.assertEqual(pred, {"decision": "matched", "au_row_key": "au-row"})

    def test_allowlist_accepts_current_9b_and_future_4b_pins_only(self):
        for model in ("Qwen/Qwen3.5-9B", "Qwen/Qwen3.5-4B"):
            config_model = {"name": model, **evaluator.MODEL_ALLOWLIST[model],
                            "quantization_origin": "official base checkpoint quantized at load time with bitsandbytes NF4"}
            self.assertEqual(evaluator.configured_models({"models": [config_model]}), [config_model])
        self.assertEqual(evaluator.model_slug("Qwen/Qwen3.5-9B"), "qwen-qwen3-5-9b")
        self.assertEqual(evaluator.model_slug("Qwen/Qwen3.5-4B"), "qwen-qwen3-5-4b")
        with self.assertRaises(ValueError):
            evaluator.configured_models({"models": [{"name": "Qwen/Qwen3-8B", "revision": "old", "load": "nf4"}]})

    def test_actual_nf4_requires_quantizer_and_linear4bit_evidence(self):
        result = {"runtime_quantization": "nf4", "bnb_4bit_quant_type": "nf4",
                  "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True,
                  "is_loaded_in_4bit": True,
                  "config_name": "Qwen/Qwen3.5-9B", "config_revision": "rev",
                  "hf_quantization_config": {"load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
                      "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True},
                  "model_config_sha256": "a" * 64, "nf4_linear4bit_module_count": 4,
                  "nf4_logical_parameter_count": 500, "base_logical_parameter_count": 1000,
                  "nf4_coverage_ratio": 0.5}
        evaluator.validate_actual_nf4(result, "Qwen/Qwen3.5-9B", "rev")
        result["nf4_linear4bit_module_count"] = 0
        with self.assertRaisesRegex(ValueError, "lacks evidence"):
            evaluator.validate_actual_nf4(result, "Qwen/Qwen3.5-9B", "rev")

    def test_automatic_coverage_differs_from_accept_rate(self):
        row = {"gold_decision": "matched", "gold_row_keys": ["r"],
               "prediction": {"decision": "unmatched", "au_row_key": None},
               "raw_record": {"status": "ok"}}
        m = evaluator.aggregate([row])
        self.assertEqual(m["accepted_rate"], 0)
        self.assertEqual(m["automatic_decision_coverage"], 1)
        self.assertEqual(m["false_reject_matched_to_unmatched"], 1)

    def test_curtain_strata_comes_from_gpu_sample_source_metadata(self):
        tasks = [{"case_id": "c", "dossier_id": "d1", "source_category": "curtain"},
                 {"case_id": "n", "dossier_id": "d2", "source_category": "non_curtain"}]
        scored = []
        for task in tasks:
            scored.append({"case_id": task["case_id"], "dossier_id": task["dossier_id"],
                           "gold_decision": "review", "gold_row_keys": [],
                           "prediction": {"decision": "review", "au_row_key": None},
                           "raw_record": {"status": "ok"}})
        strata = evaluator.strata_metrics(tasks, scored)["strata"]
        self.assertEqual(strata["curtain"]["case_count"], 1)
        self.assertEqual(strata["noncurtain"]["case_count"], 1)


if __name__ == "__main__":
    unittest.main()
