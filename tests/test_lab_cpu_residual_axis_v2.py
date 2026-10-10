"""Pure source-task and decision guards for the CPU residual-axis trial."""
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "sku-matching" / "trial_cpu_residual_axis_v2.py"
spec = importlib.util.spec_from_file_location("sku_cpu_residual_trial_test", SCRIPT)
assert spec and spec.loader
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def axis(name, value, key, normalized):
    return {"axis_name_raw": name, "value_raw": value, "semantic_key": key,
            "normalized_value": normalized, "source_ref": {"json_path": "test"}}


def entity(row_key, attrs, unknown=None, conflicts=None):
    return {"row_key": row_key, "raw_sku": row_key, "attrs": attrs,
            "unknown_fields": unknown or [], "source_conflicts": conflicts or [],
            "unresolved_axes": [], "evidence": {}}


class CpuResidualAxisTests(unittest.TestCase):
    def test_string_common_axis_mismatch_is_review_not_proven_conflict(self):
        task = {"rakuten": {"axes": [axis("色", "スモークグレー", "color", "smoke_gray")]},
                "au_sku_rows": [{"row_key": "au:1", "axes": [axis("色", "シルバー", "color", "silver")] }]}
        plan = trial.common_axis_plan(task)
        self.assertEqual(plan["candidate_row_keys"], [])
        self.assertEqual(plan["candidate_common_unknowns"][0]["reason"], "common_axis_unknown_or_conflicting_within_row")

    def test_numeric_common_axis_mismatch_is_excluded(self):
        task = {"rakuten": {"axes": [axis("枚数", "2", "count", 2)]},
                "au_sku_rows": [{"row_key": "au:1", "axes": [axis("枚数", "1", "count", 1)] }]}
        self.assertEqual(trial.common_axis_plan(task)["excluded_common_value_conflicts"][0]["row_key"], "au:1")

    def test_row_metric_counts_wrong_row_and_gold_review_in_precision_denominator(self):
        labels = [{"decision": "matched", "matching_au_row_keys": ["au:right"]},
                  {"decision": "review", "matching_au_row_keys": []}]
        preds = [{"decision": "matched", "candidate_row_keys_after_legacy_guard": ["au:wrong"]},
                 {"decision": "matched", "candidate_row_keys_after_legacy_guard": ["au:maybe"]}]
        metrics = trial._row_aware_metric(labels, preds)
        self.assertEqual(metrics["false_match_wrong_or_missing_row_count"], 1)
        self.assertEqual(metrics["accepted_gold_review_count"], 1)
        self.assertEqual(metrics["matched_precision_including_gold_review"], 0.0)

    def test_fallback_does_not_clip_relevant_source_blocks(self):
        product = {"description_blocks": [
            {"text": f"レース付属 条件{index}", "scope": "", "block_index": index}
            for index in range(9)]}
        blocks = trial.fallback_blocks(product, axis("レースカーテン", "あり", "lace", True), {})
        self.assertEqual(len(blocks), 9)

    def test_conditional_block_requires_all_size_numbers(self):
        product = {"description_blocks": [
            {"text": "【幅150×丈200cm】", "scope": "", "block_index": 0},
            {"text": "レースカーテン付き", "scope": "", "block_index": 1}]}
        blocks = trial.fallback_blocks(product, axis("レースカーテン", "あり", "lace", True), {"size": [100, 200]})
        self.assertEqual(blocks, [])

    def test_prompt_cache_reuses_only_exact_text_hash_and_stage(self):
        prompt = "同じ入力"
        row = {"title": {"stage": "fixed_title_primary", "model_input": prompt,
                         "raw_output": {"relation": {"label": "固定条件から不明", "confidence": 0.8}}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.jsonl"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            frozen = {"reused_prompt_source": {"path": str(path.resolve()),
                                                "sha256": trial.sha256(path)}}
            cache = trial._prompt_cache(path, frozen)
        key = ("fixed_title_primary", hashlib.sha256(prompt.encode()).hexdigest())
        self.assertIn(key, cache)
        self.assertNotIn(("conditional_description_secondary", key[1]), cache)

    def test_common_axes_filter_first_and_keep_axis_name_with_r_only_value(self):
        task = {"rakuten": {"axes": [
            axis("サイズ", "100×185cm", "size", "100×185"),
            axis("カラー", "コールブラック", "color", "コールブラック"),
            axis("レースカーテン", "あり", "lace", True),
        ]}, "au_sku_rows": [
            {"row_key": "same", "axes": [axis("カラー", "コールブラック", "color", "コールブラック"),
                                           axis("サイズ", "幅100×丈185cm(4枚組)", "size", "100×185")]},
            {"row_key": "wrong-color", "axes": [axis("カラー", "ベージュ", "color", "ベージュ"),
                                                   axis("サイズ", "幅100×丈185cm(4枚組)", "size", "100×185")]},
        ]}
        plan = trial.common_axis_plan(task)
        self.assertEqual(plan["common_fields"], ["color", "size"])
        self.assertEqual(plan["candidate_row_keys"], ["same"])
        self.assertEqual(plan["residual_axes"][0]["axis_name_raw"], "レースカーテン")
        self.assertEqual(plan["residual_axes"][0]["value_raw"], "あり")
        self.assertEqual(plan["candidate_common_unknowns"][0]["row_key"], "wrong-color")

    def test_a_present_but_different_au_axis_is_not_residualized(self):
        task = {"rakuten": {"axes": [axis("カラー", "赤", "color", "赤")]},
                "au_sku_rows": [{"row_key": "blue", "axes": [axis("カラー", "青", "color", "青")]}]}
        plan = trial.common_axis_plan(task)
        self.assertEqual(plan["residual_axes"], [])
        self.assertEqual(plan["candidate_row_keys"], [])
        self.assertEqual(plan["candidate_common_unknowns"][0]["reason"], "common_axis_unknown_or_conflicting_within_row")

    def test_au_only_axis_is_retained_as_review(self):
        task = {"rakuten": {"axes": [axis("カラー", "黒", "color", "黒")]},
                "au_sku_rows": [{"row_key": "one", "axes": [axis("カラー", "黒", "color", "黒"),
                                                               axis("高さ", "100cm", "axis:高さ", "100cm")] }]}
        plan = trial.common_axis_plan(task)
        self.assertEqual(plan["au_only_axes"][0]["semantic_key"], "axis:高さ")
        result = trial.decide_case(task, {}, {})
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["reason"], "common_axis_or_au_only_condition_unresolved")

    def test_residual_title_can_resolve_only_its_unknown_and_unknown_is_preserved(self):
        r = entity("r", {"size": "100×185", "color": "黒", "lace": True})
        a = entity("a", {"size": "100×185", "color": "黒", "lace": None}, unknown=["lace"])
        r["axes"] = [axis("サイズ", "100×185cm", "size", "100×185"),
                     axis("カラー", "黒", "color", "黒"),
                     axis("レースカーテン", "あり", "lace", True)]
        task = {"case_id": "case-1", "rakuten": r, "current_page_context": {},
                "au_sku_rows": [{"row_key": "a", "axes": [axis("色", "黒", "color", "黒"),
                                                               axis("サイズ", "100×185", "size", "100×185")],
                                 "current_entity": a}]}
        product = {"title_raw": "カーテン レースカーテンセット", "description_blocks": []}
        rel = {"lace": {"relation": "entailed", "residual_id": "res-1"}}
        out = trial.decide_case(task, product, rel)
        self.assertEqual(out["decision"], "matched")
        self.assertIn("residual_unknown_retained", out["legacy_guards"][0]["legacy_guard"])
        self.assertEqual(a["unknown_fields"], ["lace"])

    def test_title_unknown_does_not_discard_existing_cited_attribute_match(self):
        r = entity("r", {"size": "100×185", "color": "黒", "lace": True})
        r["axes"] = [axis("サイズ", "100×185cm", "size", "100×185"),
                     axis("カラー", "黒", "color", "黒"),
                     axis("レースカーテン", "あり", "lace", True)]
        a = entity("a", {"size": "100×185", "color": "黒", "lace": True})
        a["evidence"] = {"lace": [{"quote": "レースカーテンセット", "source_ref": {"json_path": "title"}}]}
        task = {"case_id": "case-1", "rakuten": r, "current_page_context": {},
                "au_sku_rows": [{"row_key": "a", "axes": [axis("色", "黒", "color", "黒"),
                                                               axis("サイズ", "100×185", "size", "100×185")],
                                 "current_entity": a}]}
        out = trial.decide_case(task, {"title_raw": "カーテン", "description_blocks": []},
                                {"lace": {"relation": "unknown", "residual_id": "res-1"}})
        self.assertEqual(out["decision"], "matched")
        self.assertEqual(out["reason"], "existing_cited_attribute_gate_preserved_after_title_unknown")
        self.assertEqual(out["legacy_guards"][0]["retained_residual_evidence"]["lace"][0]["quote"],
                         "レースカーテンセット")

    def test_known_conflicts_across_candidate_pool_remain_unmatched(self):
        r = entity("r", {"size": "100"})
        r["axes"] = [axis("サイズ", "100", "size", "100")]
        a = entity("a", {"size": "120"})
        task = {"case_id": "case-1", "rakuten": r, "current_page_context": {},
                "au_sku_rows": [{"row_key": "a", "axes": [axis("サイズ", "100", "size", "100")],
                                 "current_entity": a}]}
        out = trial.decide_case(task, {"title_raw": "カーテン"}, {})
        self.assertEqual(out["decision"], "unmatched")
        self.assertEqual(out["reason"], "all_common_rows_have_known_attribute_conflicts")

    def test_source_conflict_cannot_be_waived_by_title(self):
        r = entity("r", {"lace": True})
        a = entity("a", {"lace": None}, unknown=["lace"],
                   conflicts=[{"field": "lace", "values": [False, True]}])
        ok, result = trial.legacy_guard({"rakuten": r}, a, {"lace"})
        self.assertFalse(ok)
        self.assertEqual(result["legacy_guard"], "source_conflict_preserved")

    def test_legacy_rule_unwraps_fixed_product_candidate_entity(self):
        r = entity("r", {"color": "黒"})
        a = entity("a", {"color": "黒"})
        task = {"rakuten": r, "au_sku_rows": [{"row_key": "a", "current_entity": a}]}
        self.assertEqual(trial.legacy_decision(task)["decision"], "matched")

    def test_series_context_is_inherited_and_not_used_as_fallback(self):
        product = {"description_source_ref": {"raw_file": "au.json"}, "description_blocks": [
            {"text": "セットでおすすめシリーズ", "scope": "product_page_extra_comment", "block_index": 5},
            {"text": "毛布2枚合わせ", "scope": "product_page_extra_comment", "block_index": 7},
            {"text": "商品詳細", "scope": "product_page_extra_comment", "block_index": 8},
            {"text": "レースカーテン付き", "scope": "product_page_comment", "block_index": 9},
        ]}
        got = trial.fallback_blocks(product, axis("レースカーテン", "あり", "lace", True), {})
        self.assertEqual([x["text"] for x in got], ["レースカーテン付き"])


if __name__ == "__main__":
    unittest.main()
