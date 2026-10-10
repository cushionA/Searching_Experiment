"""Label-free v12 decision-ablation guards."""
from __future__ import annotations
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/trial_cpu_residual_axis_v3.py"
spec = importlib.util.spec_from_file_location("cpu_residual_v3_test", SCRIPT)
assert spec and spec.loader
v3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v3)
v2 = v3._v2_module()


def axis(name, value, key, normalized):
    return {"axis_name_raw": name, "value_raw": value, "semantic_key": key,
            "normalized_value": normalized, "source_ref": {"json_path": "fixture"}}


def setup_case(extra_rows=None, selected_axes=None, residual_ids=None):
    task = {"case_id": "case-a", "rakuten": {"axes": [axis("サイズ", "100", "size", 100)]}}
    selected = {"row_key": "au:1", "axes": selected_axes if selected_axes is not None else [axis("サイズ", "100", "size", 100)],
                "current_entity": {"row_key": "au:1", "attrs": {"size": 100}, "evidence": {"size": [{"quote": "100"}]}}}
    products = {"au_sku_rows": [selected, *(extra_rows or [])]}
    sourcegate = {"case_id": "case-a", "decision": "matched", "top_row_key": "au:1",
                  "reason": "unique_source_proven_match", "gate": {"decision": "matched"}}
    return task, products, sourcegate, residual_ids or []


class V12AblationTests(unittest.TestCase):
    def test_source_pool_mapping_preserves_required_fields_and_evidence(self):
        row = {"row_key": "au:1", "required_fields": ["size", "color"],
               "attrs": {"size": 100}, "evidence": {"size": [{"quote": "100"}]}}
        product = {"au_sku_rows": [{"row_key": "au:1", "current_entity": row}]}
        candidates = v3._source_candidates(product)
        self.assertEqual(candidates[0]["required_fields"], ["size", "color"])
        self.assertEqual(candidates[0]["evidence"]["size"][0]["quote"], "100")

    def test_recomputed_gate_separates_prior_ranking_from_gate_decision(self):
        task, product, prior, _ = setup_case()
        prior["model_top_row_key"] = "au:1"
        product["au_sku_rows"][0]["current_entity"].update(
            {"required_fields": ["size"], "attrs": {"size": 100, "category": "other"},
             "unknown_fields": [], "source_conflicts": [], "unresolved_axes": []})
        task["rakuten"].update({"attrs": {"size": 100, "category": "other"},
                                "required_fields": ["size"], "unknown_fields": [],
                                "source_conflicts": [], "unresolved_axes": []})
        result = v3._recompute_sourcegates(task, product, prior, v3._hybrid_module(), v3._v2_module().current_eval)
        self.assertEqual(result["hybrid_recomputed"]["decision"], "matched")
        self.assertEqual(result["legacy_recomputed"]["decision"], "matched")
        self.assertIs(result["prior_prediction_diagnostic"], prior)

    def test_model_unknown_preserves_directly_supported_source_fact(self):
        task = {"rakuten": {"required_fields": ["product_variant"],
                            "axes": [axis("タイプ", "もことろん毛布", "product_variant", "もことろん毛布")]}}
        entity = {"attrs": {"product_variant": "もことろん毛布"}, "evidence": {
            "product_variant": [{"quote": "もことろん毛布2枚合わせ ダブル ×1", "source_ref": {"json_path": "block"}}]}}
        residual = {"semantic_key": "product_variant", "effective_relation": "unknown"}
        result = v3._source_fact_supports_unknown(v2, task, entity, residual)
        self.assertIsNotNone(result)
        self.assertEqual(result["supporting_evidence"][0]["source_ref"]["json_path"], "block")

    def test_model_unknown_does_not_promote_unsupported_negative_fact(self):
        task = {"rakuten": {"required_fields": ["lace"],
                            "axes": [axis("レースカーテン", "なし", "lace", False)]}}
        entity = {"attrs": {"lace": False}, "evidence": {"lace": [
            {"quote": "遮光カーテン 2枚"}, {"quote": "タッセル 2枚"}]}}
        residual = {"semantic_key": "lace", "effective_relation": "unknown"}
        self.assertIsNone(v3._source_fact_supports_unknown(v2, task, entity, residual))

    def _curtain_fixture(self, root, *, option_value="幅100×丈220cm(2枚)",
                         contents="【幅100cm】<br>遮光カーテン 2枚<br>タッセル 2枚<br>カーテンフック 14個<br>【幅150cm】<br>遮光カーテン 1枚"):
        sku_path = root / "skus.jsonl"
        sku = {"item_id": "704500131", "sku_id": 7, "row_index": 0, "column_index": 1,
               "column_option_name": "サイズ", "column_option_value": option_value}
        sku_path.write_text(json.dumps(sku, ensure_ascii=False) + "\n", encoding="utf-8")
        page_path = root / "page.json"
        page = {"itemInfo": {"extraItemComment": f"<table><tr><td>内容</td><td>{contents}</td></tr></table>"}}
        page_bytes = json.dumps(page, ensure_ascii=False).encode()
        page_path.write_bytes(page_bytes)
        axis_value = option_value
        selected = {"axes": [{"semantic_key": "size", "value_raw": axis_value,
                              "source_ref": {"raw_file": "skus.jsonl", "source_grain": {
                                  "line": 1, "sku_id": 7, "row_index": 0, "column_index": 1}}}],
                    "current_entity": {"contents_list_assumption":
                        "explicit contents list treated as exhaustive; not absence of title keyword"}}
        product = {"description_source_ref": {"raw_file": "page.json",
                                               "sha256": hashlib.sha256(page_bytes).hexdigest()}}
        task = {"product_id": "704500131", "rakuten": {"axes": [
            axis("レースカーテン", "なし", "lace", False)]}}
        return task, product, selected

    def test_count_conservation_resolves_explicit_width_scoped_lace_absence(self):
        with tempfile.TemporaryDirectory() as td:
            task, product, selected = self._curtain_fixture(Path(td))
            proof = v3._curtain_contents_resolves(task, product, selected,
                {"semantic_key": "lace", "effective_relation": "unknown"}, Path(td))
        self.assertEqual(proof["relation"], "entailed")
        self.assertEqual(proof["lace_panel_count_in_same_width_contents_scope"], 0)
        self.assertEqual(proof["total_panel_count_from_selected_sku"], 2)
        self.assertEqual(proof["source_refs"]["fixed_product_contents"]["json_path"],
                         "$.itemInfo.extraItemComment")

    def test_count_conservation_requires_exact_width_and_quantity_balance(self):
        with tempfile.TemporaryDirectory() as td:
            task, product, selected = self._curtain_fixture(Path(td), contents=
                "【幅150cm】<br>遮光カーテン 2枚")
            self.assertIsNone(v3._curtain_contents_resolves(task, product, selected,
                {"semantic_key": "lace", "effective_relation": "unknown"}, Path(td)))
        with tempfile.TemporaryDirectory() as td:
            task, product, selected = self._curtain_fixture(Path(td), contents=
                "【幅100cm】<br>遮光カーテン 2枚<br>レースカーテン 1枚")
            self.assertIsNone(v3._curtain_contents_resolves(task, product, selected,
                {"semantic_key": "lace", "effective_relation": "unknown"}, Path(td)))

    def test_count_conservation_can_contradict_lace_positive_without_absence_word(self):
        with tempfile.TemporaryDirectory() as td:
            task, product, selected = self._curtain_fixture(Path(td))
            task["rakuten"]["axes"][0]["normalized_value"] = True
            proof = v3._curtain_contents_resolves(task, product, selected,
                {"semantic_key": "lace", "effective_relation": "unknown"}, Path(td))
        self.assertEqual(proof["relation"], "contradicted")

    def test_unique_proven_row_survives_unrelated_unknown_candidate(self):
        unrelated = {"row_key": "au:2", "axes": [axis("サイズ", "unknown", "size", None)],
                     "current_entity": {"row_key": "au:2", "attrs": {"size": None}, "unknown_fields": ["size"]}}
        task, product, gate, ids = setup_case(extra_rows=[unrelated])
        out = v3._case_decision(v2, task, product, gate, ids, {})
        self.assertEqual(out["decision"], "matched")
        self.assertEqual(out["top_row_key"], "au:1")

    def test_residual_unknown_holds_only_the_previously_matched_case(self):
        task, product, gate, ids = setup_case(residual_ids=["res-1"])
        raw = {"res-1": {"residual_id": "res-1", "semantic_key": "lace", "effective_relation": "unknown",
                          "title": {"relation": "unknown"}}}
        out = v3._case_decision(v2, task, product, gate, ids, raw)
        self.assertEqual(out["decision"], "review")
        self.assertEqual(out["top_row_key"], "au:1")

    def test_all_residual_conditions_must_be_entailed(self):
        task, product, gate, ids = setup_case(residual_ids=["res-1", "res-2"])
        raw = {"res-1": {"semantic_key": "lace", "effective_relation": "entailed"},
               "res-2": {"semantic_key": "set", "effective_relation": "unknown"}}
        self.assertEqual(v3._case_decision(v2, task, product, gate, ids, raw)["decision"], "review")
        raw["res-2"]["effective_relation"] = "entailed"
        self.assertEqual(v3._case_decision(v2, task, product, gate, ids, raw)["decision"], "matched")

    def test_same_row_proven_conflict_is_not_accepted(self):
        task, product, gate, ids = setup_case(selected_axes=[axis("サイズ", "150", "size", 150)])
        out = v3._case_decision(v2, task, product, gate, ids, {})
        self.assertEqual(out["decision"], "review")
        self.assertEqual(out["reason"], "sourcegate_match_conflicts_with_same_row_selected_axis_audit")

    def test_au_only_axis_on_selected_row_is_mandatory_and_unknown(self):
        task, product, gate, ids = setup_case(selected_axes=[axis("サイズ", "100", "size", 100),
                                                            axis("タイプ", "スリム", "product_variant", "スリム")])
        out = v3._case_decision(v2, task, product, gate, ids, {})
        self.assertEqual(out["decision"], "review")
        self.assertEqual(out["row_axis_check"]["au_only_fields"], ["product_variant"])

    def test_existing_sourcegate_review_is_never_promoted(self):
        task, product, gate, ids = setup_case()
        gate["decision"] = "review"
        gate["reason"] = "at_least_one_unresolved_candidate"
        out = v3._case_decision(v2, task, product, gate, ids, {})
        self.assertEqual(out["decision"], "review")

    def test_existing_sourcegate_unmatched_is_not_overridden_by_model(self):
        task, product, gate, ids = setup_case(residual_ids=["res-1"])
        gate["decision"] = "unmatched"
        raw = {"res-1": {"semantic_key": "lace", "effective_relation": "entailed"}}
        self.assertEqual(v3._case_decision(v2, task, product, gate, ids, raw)["decision"], "unmatched")


if __name__ == "__main__":
    unittest.main()
