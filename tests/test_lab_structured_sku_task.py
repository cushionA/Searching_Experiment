from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "structured_sku_task", ROOT / "experiments/sku-matching/structured_sku_task.py")
task = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(task)


class StructuredSkuTaskTests(unittest.TestCase):
    def test_selected_axis_origin_survives_page_spec_inheritance(self):
        entity = task._axes([("カラー", "赤")], {"json_path": "sku"})
        task._inherit(entity, {"contents": [], "dimensions": [{"condition": None, "values": [80, 40, 183],
                         "citation": {"quote": "幅80×奥行40×高さ183cm"}}],
                         "internal_conflicts": [], "conditional_exceptions": []}, "au")
        self.assertEqual(entity["selected_fields"], ["color"])
        self.assertIn("page_dimensions_cm", entity["attrs"])

    def _fixture(self, *, selected_lace="なし", title="カーテン 4枚セット レースカーテン"):
        case = {
            "case_id": "case-fixture", "query_sku": f"サイズ=100×220cm / カラー=スモークグレー / レースカーテン={selected_lace}",
            "rakuten": {"option_values": [
                {"axis_key": "サイズ", "value": "100 × 220 cm"},
                {"axis_key": "カラー", "value": "スモークグレー"},
                {"axis_key": "レースカーテン", "value": selected_lace},
                {"axis_key": "Mystery", "value": "option-z"}],
                "axes_labels": [{"key": x, "label": x} for x in ("サイズ", "カラー", "レースカーテン", "Mystery")]},
            "candidates": [
                {"row_key": "au:fixture:1", "text_sku": "カラー=スモークグレー"},
                {"row_key": "au:fixture:2", "text_sku": "カラー=ホワイト"}],
        }
        dossier = {
            "rakuten_product": {"title_raw": title, "source": {"raw_file": "rak.html", "json_path": "sku"},
                "description": {"source": {"raw_file": "rak.html", "json_path": "desc"},
                    "individual_description_excerpt": "カーテン 4枚セット レースカーテン\n商品仕様\n内容\nドレープカーテン2枚、レースカーテン2枚\nサイズ\n幅100×丈220cm\n注意事項\nシリーズ品のレースカーテン"}},
            "au_product": {"source": {"raw_file": "au.json", "json_path": "$.desc"}, "description": {
                "source": {"raw_file": "au.json", "json_paths": ["$.desc"]}, "blocks": [
                    {"source_field": "$.desc", "scope": "product_page_extra_comment", "text": "商品仕様"},
                    {"source_field": "$.desc", "scope": "product_page_extra_comment", "text": "内容"},
                    {"source_field": "$.desc", "scope": "product_page_extra_comment", "text": "ドレープカーテン2枚、レースカーテンなし"},
                    {"source_field": "$.desc", "scope": "series_or_sibling_context", "text": "レースカーテン2枚セット"}] }},
            "au_rows": [
                {"row_key": "au:fixture:1", "axes_raw": [{"axis_name_raw": "カラー", "value_raw": "スモークグレー"}, {"axis_name_raw": "サイズ", "value_raw": "幅100 × 丈220 cm (2枚)"}]},
                {"row_key": "au:fixture:2", "axes_raw": [{"axis_name_raw": "カラー", "value_raw": "ホワイト"}]},
            ]}
        return case, dossier

    def test_selected_axes_keep_unknown_and_lace_is_explicit_boolean(self):
        case, dossier = self._fixture()
        result = task.build_task(case, dossier)
        self.assertEqual(result["rakuten"]["attrs"]["size"], "100×220cm")
        self.assertIs(result["rakuten"]["attrs"]["lace"], False)
        self.assertEqual(result["rakuten"]["attrs"]["axis:Mystery"], "option-z")
        self.assertIn("axis:Mystery", result["rakuten"]["unresolved_axes"])
        self.assertEqual([x["row_key"] for x in result["au_candidates"]], ["au:fixture:1", "au:fixture:2"])
        self.assertEqual(result["au_candidates"][0]["attrs"]["size"], "100×220cm")
        self.assertEqual(result["au_candidates"][0]["attrs"]["panel_count"], 2)

    def test_title_cannot_supply_lace_or_count_and_sibling_scope_is_ignored(self):
        case, dossier = self._fixture(selected_lace="", title="カーテン 4枚 レースカーテン")
        case["rakuten"]["option_values"] = case["rakuten"]["option_values"][:2]
        result = task.build_task(case, dossier)
        # Explicit individual set contents, unlike the title, can establish it.
        self.assertIs(result["rakuten"]["attrs"]["lace"], True)
        self.assertEqual(result["rakuten"]["attrs"]["drape_count"], 2)
        self.assertIs(result["au_candidates"][0]["attrs"]["lace"], False)
        dossier["rakuten_product"]["description"]["individual_description_excerpt"] = "レースカーテンという商品名だけ"
        result = task.build_task(case, dossier)
        self.assertIsNone(result["rakuten"]["attrs"].get("lace"))
        self.assertIsNone(result["rakuten"]["attrs"].get("drape_count"))

    def test_width_condition_and_selected_lace_option_control_component_counts(self):
        case, dossier = self._fixture(selected_lace="なし")
        case["rakuten"]["option_values"] = case["rakuten"]["option_values"][:3]
        dossier["rakuten_product"]["description"]["individual_description_excerpt"] = (
            "商品詳細\n内容\n【幅100cm】\nカーテン2枚\nフック14個\n"
            "レースカーテン2個 ※レースカーテン付きを選択の場合\n"
            "【幅150cm】\nカーテン1枚\nフック9個\n"
            "レースカーテン1個 ※レースカーテン付きを選択の場合\nカラー\n赤")
        result = task.build_task(case, dossier)
        self.assertEqual(result["rakuten"]["attrs"]["drape_count"], 2)
        self.assertEqual(result["rakuten"]["attrs"]["lace_count"], 0)
        self.assertEqual(result["rakuten"]["attrs"]["hook_count"], 14)
        case["rakuten"]["option_values"][0]["value"] = "150×200cm"
        case["rakuten"]["option_values"][2]["value"] = "あり"
        result = task.build_task(case, dossier)
        self.assertEqual(result["rakuten"]["attrs"]["drape_count"], 1)
        self.assertEqual(result["rakuten"]["attrs"]["lace_count"], 1)
        self.assertEqual(result["rakuten"]["attrs"]["hook_count"], 9)

    def test_exception_is_inherited_only_for_selected_height_and_color(self):
        case, dossier = self._fixture()
        case["rakuten"]["option_values"] = [{"axis_key": "高さ", "value": "50cm"}, {"axis_key": "カラー", "value": "シルバー"}]
        dossier["rakuten_product"]["description"]["individual_description_excerpt"] = (
            "注意事項\n天板高さ50cmのシルバーには持ち手がありません。予めご了承の上、ご購入をお願いいたします。")
        result = task.build_task(case, dossier)
        self.assertIs(result["rakuten"]["attrs"]["handle_included"], False)
        self.assertIn("ご購入", result["rakuten"]["evidence"]["handle_included"][0]["quote"])
        case["rakuten"]["option_values"][1]["value"] = "ブラック"
        result = task.build_task(case, dossier)
        self.assertNotIn("handle_included", result["rakuten"]["attrs"])


if __name__ == "__main__":
    unittest.main()
