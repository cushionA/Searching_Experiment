from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments/sku-matching/kaggle_gpu_sku_runner_v3.py"
SPEC = importlib.util.spec_from_file_location("gpu_sku_runner_v3", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


def case_fixture():
    rows = [
        {"alias": "a000", "row_key": "row-0", "sku": "タイプ=スリム / カラー=グレー"},
        {"alias": "a001", "row_key": "row-1", "sku": "タイプ=ワイド / カラー=グレー"},
    ]
    cards = [
        {"id": "at", "side": "au", "field": "title", "scope": "fixed_page_title", "quote": "遮光カーテン", "source_ref": {"derived": False}},
        {"id": "rt", "side": "rakuten", "field": "title", "scope": "product_title", "quote": "同じ商品", "source_ref": {"derived": False}},
        {"id": "rs", "side": "rakuten", "field": "selected_sku", "scope": "selected_option", "quote": "タイプ=スリム / カラー=グレー", "source_ref": {"derived": True}},
        {"id": "rd0001", "side": "rakuten", "field": "description", "scope": "current_product", "quote": "2枚組が必要です。", "source_ref": {"derived": True}},
        {"id": "ad0001", "side": "au", "field": "description", "scope": "series_or_sibling", "quote": "別サイズは毛布セット。", "source_ref": {"derived": True}},
        {"id": "a000", "side": "au", "field": "selected_option", "scope": "sku_row", "quote": rows[0]["sku"], "row_key": rows[0]["row_key"], "source_ref": {"derived": True}},
        {"id": "a001", "side": "au", "field": "selected_option", "scope": "sku_row", "quote": rows[1]["sku"], "row_key": rows[1]["row_key"], "source_ref": {"derived": True}},
    ]
    return {"case_id": "case-x", "rakuten": {"sku": "タイプ=スリム / カラー=グレー"},
            "au": {"sku_rows": rows}, "evidence_registry": cards,
            "source_texts": {"au": ["遮光カーテン", "別サイズは毛布セット。", rows[0]["sku"], rows[1]["sku"]],
                             "rakuten": ["同じ商品", "タイプ=スリム / カラー=グレー", "2枚組が必要です。"]}}


class RunnerV3ContractTests(unittest.TestCase):
    def test_candidate_pool_requires_bijection_and_source_alignment(self):
        case = case_fixture()
        self.assertEqual([r["alias"] for r in runner.candidate_pool(case)], ["a000", "a001"])
        case["evidence_registry"][-1]["row_key"] = "wrong-row"
        with self.assertRaisesRegex(ValueError, "mapping mismatch"):
            runner.candidate_pool(case)

    def test_prompts_retain_compound_full_pool_and_scope_labels(self):
        case = case_fixture()
        for mode in runner.MODES:
            prompt = runner.build_prompt(case, mode)
            self.assertIn("タイプ=スリム / カラー=グレー", prompt)
            self.assertIn("rd0001 [rakuten|description|current_product]: 2枚組が必要です。", prompt)
            self.assertIn("ad0001 [au|description|series_or_sibling]: 別サイズは毛布セット。", prompt)
            self.assertIn("a000\tタイプ=スリム / カラー=グレー", prompt)
            self.assertIn("a001\tタイプ=ワイド / カラー=グレー", prompt)
            if mode == "simple":
                self.assertIn("共通series説明や別size headingは支持に使えません", prompt)
        self.assertIn("inputrefsであり含意・意味証拠ではありません", runner.build_prompt(case, "simple"))
        self.assertNotIn("evidence_ids", runner.build_prompt(case, "simple"))

    def test_strict_and_simple_contracts_keep_host_refs_non_evidentiary(self):
        case = case_fixture()
        strict = json.dumps({"decision": "matched", "au_row_alias": "a000", "reason": "条件を確認", "evidence_ids": ["rs", "a000", "rt"]}, ensure_ascii=False)
        parsed = runner.parse_prediction(strict, case, "strict")
        self.assertTrue(parsed["valid"], parsed)
        self.assertEqual(parsed["prediction"]["au_row_key"], "row-0")
        self.assertEqual([x["evidence_id"] for x in parsed["prediction"]["evidence"]], ["rs", "a000", "rt"])
        simple = json.dumps({"decision": "matched", "au_row_alias": "a000", "reason": "条件を確認"}, ensure_ascii=False)
        parsed_simple = runner.parse_prediction(simple, case, "simple")
        self.assertTrue(parsed_simple["valid"], parsed_simple)
        pred = parsed_simple["prediction"]
        self.assertEqual(pred["evidence"], [])
        self.assertEqual(pred["inputrefs"], {"kind": "inputrefs_not_entailment_evidence", "row_key": "row-0", "sku": "タイプ=スリム / カラー=グレー"})

    def test_decisive_strict_scope_and_row_contracts(self):
        case = case_fixture()
        bad_scope = json.dumps({"decision": "matched", "au_row_alias": "a000", "reason": "一致", "evidence_ids": ["rs", "a000", "ad0001"]}, ensure_ascii=False)
        self.assertEqual(runner.parse_prediction(bad_scope, case, "strict")["error"], "non_product_scope_cannot_support_decision")
        other_alias = json.dumps({"decision": "matched", "au_row_alias": "a000", "reason": "一致", "evidence_ids": ["rs", "a000", "a001"]}, ensure_ascii=False)
        self.assertEqual(runner.parse_prediction(other_alias, case, "strict")["error"], "matched_requires_rs_and_chosen_row")
        no_rs = json.dumps({"decision": "unmatched", "au_row_alias": None, "reason": "矛盾", "evidence_ids": ["rt", "a001"]}, ensure_ascii=False)
        self.assertEqual(runner.parse_prediction(no_rs, case, "strict")["error"], "unmatched_requires_rs_and_au_conflict_row")
        unhashable_decision = json.dumps({"decision": [], "au_row_alias": None, "reason": "確認", "evidence_ids": []})
        self.assertEqual(runner.parse_prediction(unhashable_decision, case, "strict")["error"], "invalid_decision")
        review = json.dumps({"decision": "review", "au_row_alias": None, "reason": "適用条件が不明", "evidence_ids": []}, ensure_ascii=False)
        self.assertTrue(runner.parse_prediction(review, case, "strict")["valid"])

    def test_smoke_bundle_copies_v8_inputs_and_source_cases_byte_for_byte(self):
        parent = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v8/dataset-upload"
        if not parent.exists():
            self.skipTest("frozen v8 dataset bundle is not present in this checkout")
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "prepared"
            manifest = runner.prepare_smoke_bundle(parent, dest)
            self.assertEqual((dest / "inputs.jsonl").read_bytes(), (parent / "inputs.jsonl").read_bytes())
            self.assertEqual((dest / "source-cases.jsonl").read_bytes(), (parent / "source-cases.jsonl").read_bytes())
            self.assertEqual(manifest["case_count"], 14)
            self.assertEqual(manifest["au_row_count"], 798)
            cfg = json.loads((dest / "config.json").read_text())
            self.assertEqual(cfg["inference_count"], 28)
            with self.assertRaises(FileExistsError):
                runner.prepare_smoke_bundle(parent, dest)

    def test_native_qwen35_chunked_prefill_logit_and_greedy_equivalence(self):
        try:
            import torch
            from transformers.models.qwen3_5.configuration_qwen3_5 import Qwen3_5Config, Qwen3_5TextConfig, Qwen3_5VisionConfig
            from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5ForConditionalGeneration
        except Exception as exc:
            self.skipTest(f"local CPU Qwen3.5 implementation unavailable: {type(exc).__name__}: {exc}")
        def new_model():
            text = Qwen3_5TextConfig(vocab_size=64, hidden_size=32, intermediate_size=64,
                num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1, head_dim=16,
                linear_num_key_heads=2, linear_num_value_heads=4, linear_key_head_dim=8,
                linear_value_head_dim=8, layer_types=["linear_attention", "full_attention"], eos_token_id=2)
            vision = Qwen3_5VisionConfig(depth=1, hidden_size=32, intermediate_size=64,
                num_heads=2, patch_size=2, spatial_merge_size=1, temporal_patch_size=1,
                out_hidden_size=32, num_position_embeddings=16)
            cfg = Qwen3_5Config(text_config=text, vision_config=vision, image_token_id=60,
                video_token_id=61, vision_start_token_id=62, vision_end_token_id=63,
                eos_token_id=2, pad_token_id=0)
            return Qwen3_5ForConditionalGeneration(cfg).eval()

        torch.manual_seed(1701)
        baseline_model = new_model()
        ids = torch.randint(4, 59, (1, 129))
        mask = torch.ones_like(ids)
        with torch.inference_mode():
            reference = baseline_model.generate(input_ids=ids, attention_mask=mask, max_new_tokens=4,
                do_sample=False, use_cache=True, logits_to_keep=1)
            for chunk_size in (4, 64):
                torch.manual_seed(1701)
                model = new_model()
                full = model(input_ids=ids, attention_mask=mask, use_cache=True, logits_to_keep=1)
                chunked, cache = runner.chunked_prefill(model, ids, mask, chunk_size=chunk_size)
                self.assertEqual(cache.get_seq_length(), ids.shape[1])
                self.assertTrue(torch.allclose(full.logits, chunked.logits, atol=2e-5, rtol=2e-5))
                actual = runner.chunked_greedy_generate(model, ids, mask, max_new_tokens=4, chunk_size=chunk_size)
                self.assertTrue(torch.equal(reference, actual))
                self.assertEqual(actual.shape[-1] - ids.shape[-1], 4)


if __name__ == "__main__":
    unittest.main()
