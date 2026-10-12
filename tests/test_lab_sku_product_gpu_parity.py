"""Synthetic CPU/CUDA parity checks; never reads model labels."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("product_gpu_cpu_parity", HERE / "compare_product_gpu_cpu_v1.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class ProductGpuCpuParityTests(unittest.TestCase):
    def test_fp32_score_deltas_and_argmax_flips_are_measured_without_bitwise_gate(self):
        cpu = {
            "key-a": {"logits": {"yes": 1.0, "no": 0.8}, "probabilities": {"yes": .55, "no": .45},
                      "argmax_label": "yes", "status": "ok"},
            "key-b": {"logits": {"yes": .2, "no": .3}, "probabilities": {"yes": .49, "no": .51},
                      "argmax_label": "no", "status": "ok"},
        }
        gpu = {
            "key-a": {"logits": {"yes": 1.00001, "no": .79999}, "probabilities": {"yes": .5501, "no": .4499},
                      "argmax_label": "yes", "status": "ok"},
            "key-b": {"logits": {"yes": .30001, "no": .3}, "probabilities": {"yes": .51, "no": .49},
                      "argmax_label": "yes", "status": "ok"},
        }
        result = P.compare_score_maps(cpu, gpu, "jev", value_fields=("logits", "probabilities"),
                                      argmax_field="argmax_label")
        self.assertEqual(result["cache_key_count"], 2)
        self.assertEqual(result["argmax_flip_count"], 1)
        self.assertAlmostEqual(result["numerical"]["logits"]["max_abs"], .10001)
        self.assertEqual(result["numerical"]["probabilities"]["compared_values"], 4)
        self.assertFalse(result["records"][0]["argmax_flip"])
        self.assertTrue(result["records"][1]["argmax_flip"])

    def test_input_source_hash_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            cpu, gpu = Path(td) / "cpu", Path(td) / "gpu"
            cpu.mkdir(); gpu.mkdir()
            source = {name: "a" * 64 for name in
                      ("requests.jsonl", "documents.jsonl", "tasks.jsonl", "blocks.jsonl", "retrieval-requests.jsonl",
                       "baseline-predictions.jsonl", "case_plans.jsonl", "row_plans.jsonl")}
            for directory in (cpu, gpu):
                (directory / "source-manifest.json").write_text(
                    __import__("json").dumps({"output_sha256": source}), encoding="utf-8")
                (directory / "sharing-contract.json").write_text("{}", encoding="utf-8")
                freeze = {"input_sha256": {name: "b" * 64 for name in
                     ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl",
                     "baseline-predictions.jsonl", "blocks.jsonl", "retrieval-requests.jsonl")}}
                (directory / "freeze.json").write_text(__import__("json").dumps(freeze), encoding="utf-8")
            changed = __import__("json").loads((gpu / "source-manifest.json").read_text())
            changed["output_sha256"]["requests.jsonl"] = "c" * 64
            (gpu / "source-manifest.json").write_text(__import__("json").dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source file SHA drift"):
                P.compare_exact_files(cpu, gpu)

    def test_model_file_pin_and_device_precision_are_enforced(self):
        cpu_summary = {"model_identity": {
            "jev": {"model_id": "m", "revision": "r", "file_sha256": {"w": "a" * 64},
                    "device": "cpu", "precision": "fp32", "context_template": "ctx"},
            "nli": {"model_id": "n", "revision": "s", "files_sha256": {"w": "b" * 64},
                    "device": "cpu", "precision": "fp32"}}}
        gpu_summary = {
            "verified_jev_pin": {"model_id": "m", "revision": "r", "file_sha256": {"w": "z" * 64},
                                 "device": "cuda", "precision": "fp32", "context_template": "ctx"},
            "verified_nli_pin": {"model_id": "n", "revision": "s", "files_sha256": {"w": "b" * 64},
                                 "device": "cuda", "precision": "fp32"}}
        with self.assertRaisesRegex(ValueError, "jev model revision/file pin drift"):
            P.verify_pins(cpu_summary, gpu_summary, {})

    def test_binary_gate_disagreement_count_and_cases(self):
        cpu = {"a": {"decision": "accept", "top_row_key": "row-1"},
               "b": {"decision": "drop"}, "c": {"decision": "drop"}}
        gpu = {"a": {"decision": "drop"}, "b": {"decision": "drop"},
               "c": {"decision": "accept", "top_row_key": "row-3"}}
        diffs = P.gate_binary_differences(cpu, gpu)
        self.assertEqual(len(diffs), 2)
        self.assertEqual({row["case_id"] for row in diffs}, {"a", "c"})

    def test_runtime_comparison_keeps_total_job_boundary_separate(self):
        result = P.runtime_table(
            {"total_seconds": 379.572, "reranker_runtime": {"inference_seconds": 10},
             "jev_runtime": {"inference_seconds": 20}, "nli_runtime": {"inference_seconds": 30}},
            {"total_gpu_job_seconds": 1000, "reranker_runtime": {"inference_seconds": 10},
             "gpu_stage_telemetry": {"jev": {"inference_seconds": 5}, "nli": {"inference_seconds": 8}}})
        self.assertEqual(result["inference_seconds_sum"], {"cpu": 60, "gpu": 23})
        self.assertEqual(result["cpu_total_seconds"], 379.572)
        self.assertEqual(result["gpu_total_job_seconds"], 1000)
        self.assertIn("different boundaries", result["interpretation"])


if __name__ == "__main__":
    unittest.main()
