"""Pure contract tests for the Kaggle GPU SKU runner."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/kaggle_gpu_sku_runner.py"
spec = importlib.util.spec_from_file_location("kaggle_gpu_sku_runner_test", SCRIPT)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class GpuSkuRunnerTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "case_id": "real-case-1", "dossier_id": "dossier-1", "split": "test",
            "source_category": "curtain",
            "rakuten": {"title": "遮光カーテン", "sku": "幅100cm 丈178cm", "description": "2枚組。レースなし。"},
            "au": {"title": "遮光カーテン", "description": "サイズ表 幅100cm 丈178cm", "sku_rows": [
                {"row_key": "row-a", "sku": "幅100cm 丈178cm 2枚"},
                {"row_key": "row-b", "sku": "幅100cm 丈178cm 1枚 レース付き"},
            ]},
        }

    def test_prompt_keeps_full_pool_and_excludes_routing_metadata(self):
        prompt = runner.build_prompt(self.case)
        self.assertIn('"row-a"', prompt)
        self.assertIn('"row-b"', prompt)
        self.assertIn("レース有無", prompt)
        self.assertIn("上流工程ですでに", prompt)
        self.assertIn("unmatched", prompt)
        self.assertIn("同一ページ内の矛盾", prompt)
        self.assertNotIn("real-case-1", prompt)
        self.assertNotIn("curtain", prompt)
        self.assertNotIn("https://", prompt)

    def test_pool_lookup_accepts_only_actual_full_pool_row_key(self):
        valid = '{"decision":"matched","au_row_key":"row-b","reason":"選択値が一致","evidence":[{"side":"au","quote":"幅100cm 丈178cm 1枚 レース付き"},{"side":"rakuten","quote":"幅100cm 丈178cm"}]}'
        self.assertTrue(runner.validate_prediction(valid, self.case)["valid"])
        bad_key = valid.replace("row-b", "invented")
        self.assertEqual(runner.validate_prediction(bad_key, self.case)["error"], "matched_row_key_outside_pool")

    def test_decisive_output_requires_literal_evidence_from_both_sources(self):
        missing = '{"decision":"unmatched","au_row_key":null,"reason":"選択条件の矛盾","evidence":[{"side":"au","quote":"幅100cm 丈178cm 1枚 レース付き"}]}'
        self.assertEqual(runner.validate_prediction(missing, self.case)["error"], "decisive_output_requires_both_source_quotes")

    def test_quotes_must_be_exact_source_substrings(self):
        output = '{"decision":"review","au_row_key":null,"reason":"根拠不足","evidence":[{"side":"rakuten","quote":"幅100cm"}]}'
        self.assertTrue(runner.validate_prediction(output, self.case)["valid"])
        fabricated = output.replace("幅100cm", "幅100cm 丈99cm")
        self.assertEqual(runner.validate_prediction(fabricated, self.case)["error"], "quote_not_literal_source_substring")

    def test_nonmatched_must_have_null_key_and_invalid_json_is_not_retried(self):
        self.assertEqual(runner.validate_prediction("not json", self.case)["valid"], False)
        bad = '{"decision":"unmatched","au_row_key":"row-a","reason":"差異","evidence":[]}'
        self.assertEqual(runner.validate_prediction(bad, self.case)["error"], "nonmatched_row_key_must_be_null")
        malformed_key = '{"decision":"matched","au_row_key":[],"reason":"同じ","evidence":[]}'
        self.assertEqual(runner.validate_prediction(malformed_key, self.case)["error"], "matched_row_key_outside_pool")

    def test_duplicate_or_missing_pool_keys_are_rejected(self):
        self.case["au"]["sku_rows"][1]["row_key"] = "row-a"
        with self.assertRaisesRegex(ValueError, "duplicate AU row_key"):
            runner.build_prompt(self.case)

    def test_default_model_is_pinned_qwen35_9b_nf4_and_rejects_old_qwen3(self):
        self.assertEqual(runner.DEFAULT_MODELS, [{
            "name": "Qwen/Qwen3.5-9B",
            "revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
            "load": "nf4",
        }])
        runner.validate_model_specs(runner.DEFAULT_MODELS)
        with self.assertRaisesRegex(ValueError, "only Qwen3.5"):
            runner.validate_model_specs([{"name": "Qwen/Qwen3-8B", "revision": "b" * 40, "load": "nf4"}])
    def test_qwen35_4b_may_run_alone_or_follow_9b_with_its_pin(self):
        four_b = {"name": "Qwen/Qwen3.5-4B", "revision": runner.QWEN35_4B_REVISION, "load": "nf4"}
        runner.validate_model_specs([four_b])
        runner.validate_model_specs(runner.DEFAULT_MODELS + [{
            "name": "Qwen/Qwen3.5-4B", "revision": runner.QWEN35_4B_REVISION, "load": "nf4",
        }])
        with self.assertRaisesRegex(ValueError, "pinned revision"):
            runner.validate_model_specs(runner.DEFAULT_MODELS + [{
                "name": "Qwen/Qwen3.5-4B", "revision": "a" * 40, "load": "nf4",
            }])


if __name__ == "__main__":
    unittest.main()
