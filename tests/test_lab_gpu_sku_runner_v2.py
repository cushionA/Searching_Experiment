import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import kaggle_gpu_sku_runner_v2 as runner


def fixture():
    rows = [
        {"alias": "a000", "row_key": "full:key:0", "sku": "枚数=21枚セット(パネル20枚＋ドア1枚)"},
        {"alias": "a001", "row_key": "full:key:1", "sku": "タイプ=スリム / グレー"},
    ]
    reg = [
        {"id": "rt", "side": "rakuten", "field": "title", "scope": "title", "quote": "楽天の生タイトル", "source_ref": {"raw_sha256": "abc", "quote_verbatim": True}},
        {"id": "rs", "side": "rakuten", "field": "selected_sku", "scope": "sku", "quote": "枚数=21枚セット", "source_ref": {"derived": True}},
        {"id": "at", "side": "au", "field": "title", "scope": "title", "quote": "AUの生タイトル", "source_ref": {"raw_sha256": "def", "quote_verbatim": True}},
        {"id": "a000", "side": "au", "field": "selected_option", "scope": "sku_row", "quote": rows[0]["sku"], "row_key": rows[0]["row_key"], "source_ref": {"derived": True, "row_key": rows[0]["row_key"]}},
        {"id": "a001", "side": "au", "field": "selected_option", "scope": "sku_row", "quote": rows[1]["sku"], "row_key": rows[1]["row_key"], "source_ref": {"derived": True, "row_key": rows[1]["row_key"]}},
    ]
    return {"case_id": "case-x", "rakuten": {"sku": "枚数=21枚セット"}, "au": {"sku_rows": rows},
            "evidence_registry": reg, "source_texts": {"rakuten": ["楽天の生タイトル", "枚数=21枚セット"],
            "au": ["AUの生タイトル", rows[0]["sku"], rows[1]["sku"]]}}


class RunnerV2Tests(unittest.TestCase):
    def test_all_rows_aliases_and_compound_value_survive_prompt(self):
        c = fixture()
        prompt = runner.build_prompt(c)
        self.assertIn("a000 [selected_option|sku_row]\t枚数=21枚セット(パネル20枚＋ドア1枚)", prompt)
        self.assertIn("a001 [selected_option|sku_row]\tタイプ=スリム / グレー", prompt)
        self.assertIn("全行", prompt)
        self.assertIn("[title|title]", prompt)
        self.assertIn("series/sibling/navigation/related", prompt)

    def test_alias_resolves_to_original_row_key_and_source_quotes(self):
        c = fixture()
        raw = json.dumps({"decision":"matched", "au_row_alias":"a000", "evidence_ids":["rs","a000"], "reason":"選択SKUとAU行の全ての選択条件を確認できました"}, ensure_ascii=False)
        got = runner.resolve_prediction(raw, c)
        self.assertTrue(got["valid"], got)
        self.assertEqual(got["prediction"]["au_row_key"], "full:key:0")
        self.assertEqual({x["side"] for x in got["prediction"]["evidence"]}, {"au", "rakuten"})
        duplicate_row = json.dumps({"decision":"matched", "au_row_alias":"a000", "evidence_ids":["rs","a000","a001"],
                                    "reason":"選択SKUとAU行の全ての選択条件を確認できました"}, ensure_ascii=False)
        self.assertEqual(runner.resolve_prediction(duplicate_row,c)["error"], "matched_requires_rs_and_chosen_row_evidence")

    def test_unmatched_requires_null_alias_and_both_sides(self):
        c = fixture()
        raw = json.dumps({"decision":"unmatched", "au_row_alias":None, "evidence_ids":["rs","a000"], "reason":"楽天の選択した数量はAUの候補行と明確に矛盾しています"}, ensure_ascii=False)
        self.assertTrue(runner.resolve_prediction(raw,c)["valid"])
        bad = json.dumps({"decision":"unmatched", "au_row_alias":"a000", "evidence_ids":["rs","a000"], "reason":"楽天の選択した数量はAUの候補行と明確に矛盾しています"})
        self.assertEqual(runner.resolve_prediction(bad,c)["error"], "invalid_row_alias")

    def test_invalid_json_unknown_id_and_one_sided_evidence_rejected(self):
        c = fixture()
        self.assertFalse(runner.resolve_prediction("{",c)["valid"])
        x = {"decision":"review", "au_row_alias":None, "evidence_ids":["missing"], "reason":"条件が曖昧なので元ページの適用範囲を確認します"}
        self.assertEqual(runner.resolve_prediction(json.dumps(x),c)["error"], "invalid_evidence_ids")
        x.update(decision="matched", au_row_alias="a000", evidence_ids=["rs"])
        self.assertEqual(runner.resolve_prediction(json.dumps(x),c)["error"], "matched_requires_rs_and_chosen_row_evidence")

    def test_unhashable_fields_fail_as_contract_errors(self):
        c = fixture()
        x = {"decision": [], "au_row_alias": None, "evidence_ids": [], "reason": "条件が曖昧なので元ページの適用範囲を確認します"}
        self.assertEqual(runner.resolve_prediction(json.dumps(x),c)["error"], "invalid_decision")
        x = {"decision": "review", "au_row_alias": None, "evidence_ids": [[]], "reason": "条件が曖昧なので元ページの適用範囲を確認します"}
        self.assertEqual(runner.resolve_prediction(json.dumps(x),c)["error"], "invalid_evidence_ids")

    def test_evidence_text_must_resolve_exactly(self):
        c = fixture()
        c["evidence_registry"][0]["quote"] = "言い換え"
        self.assertEqual(runner.resolve_prediction(json.dumps({"decision":"review", "au_row_alias":None,
            "evidence_ids":["rt"], "reason":"条件が曖昧なので元ページの適用範囲を確認します"}),c)["error"], "evidence_not_resolvable")

    def test_non_authority_scope_cannot_support_decision_but_can_be_reviewed(self):
        c = fixture()
        c["evidence_registry"][0]["scope"] = "series_or_sibling_context"
        decisive = {"decision":"matched", "au_row_alias":"a000", "evidence_ids":["rs","a000","rt"],
                    "reason":"選択SKUとAU行の全ての選択条件を確認できました"}
        self.assertEqual(runner.resolve_prediction(json.dumps(decisive,ensure_ascii=False),c)["error"], "non_product_scope_cannot_support_decision")
        review = {"decision":"review", "au_row_alias":None, "evidence_ids":["rt"],
                  "reason":"系列情報しかなく現在商品の適用条件を判断できません"}
        self.assertTrue(runner.resolve_prediction(json.dumps(review,ensure_ascii=False),c)["valid"])

    def test_duplicate_registry_ids_and_alias_row_key_mismatch_fail(self):
        c = fixture()
        c["evidence_registry"].append(dict(c["evidence_registry"][0]))
        with self.assertRaisesRegex(ValueError,"unique"):
            runner.build_prompt(c)
        c = fixture(); c["evidence_registry"][-1]["row_key"] = "wrong"
        with self.assertRaisesRegex(ValueError,"does not match"):
            runner.build_prompt(c)

    def test_short_review_reason_is_valid(self):
        c = fixture()
        raw = json.dumps({"decision":"review", "au_row_alias":None, "evidence_ids":[], "reason":"不明"}, ensure_ascii=False)
        self.assertTrue(runner.resolve_prediction(raw,c)["valid"])

    def test_model_pin_is_4b_nf4_only(self):
        self.assertEqual(runner.MODEL["revision"], "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a")
        self.assertEqual(runner.MODEL["load"], "nf4")

    def test_frozen_smoke_input_integrity_and_full_pools(self):
        d = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v8/dataset-upload"
        if not (d/"inputs.jsonl").exists():
            self.skipTest("frozen v8 input package not present")
        manifest = json.loads((d/"manifest.json").read_text())
        import hashlib
        actual = hashlib.sha256((d/"inputs.jsonl").read_bytes()).hexdigest()
        self.assertEqual(actual, manifest["inputs_sha256"])
        cases = runner.read_jsonl(d/"inputs.jsonl")
        self.assertEqual(len(cases), 14)
        self.assertEqual(sum(len(runner.candidate_pool(c)) for c in cases if len(c["au"]["sku_rows"]) == 153), 765)
        self.assertEqual(len({c["case_id"] for c in cases}), 14)
        self.assertTrue(all(c["evidence_registry"] for c in cases))


if __name__ == "__main__":
    unittest.main()
