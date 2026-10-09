import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/enrich_page_sku_attributes.py"
SPEC = importlib.util.spec_from_file_location("enrich_page_sku_attributes", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def product(title="", blocks=()):
    return {"title_raw": title, "source": {"raw_file": "source.json", "sha256": "abc"},
            "description": {"source": {"raw_file": "source.json", "sha256": "abc"},
                            "blocks": list(blocks)}}


def block(text, scope="product_page_detail_comment"):
    return {"scope": scope, "source_field": "details", "text": text}


def candidate_task(attrs):
    return {"rakuten": {}, "au_candidates": [{"attrs": attrs}]}


def resolve_candidate(attrs, page):
    task = candidate_task(attrs)
    result = MODULE.enrich_task(task, {"rakuten_product": {}, "au_product": page})
    return result["au_candidates"][0]["resolved_identity"]


class EnrichedSkuAttributesTest(unittest.TestCase):
    def test_explicit_alias_from_confirmed_product_pair_cites_its_source(self):
        au = product("", [block("こちらのページはダブルサイズです\nサイズ\nD:幅180×長さ200cm")])
        rak = {"description": {"individual_description_excerpt": "サイズ\nD(ダブル):幅180×長さ200cm"}}
        result = MODULE.enrich_task({"rakuten": None, "au_candidates": [{"attrs": {"color": "赤"}}]},
                                   {"au_product": au, "rakuten_product": rak})["au_candidates"][0]["resolved_identity"]
        self.assertEqual(result["attrs"]["size_cm"], [180.0, 200.0])
        self.assertIn("D(ダブル)", result["evidence"]["source_size_aliases"][0]["quote"])

    def test_named_size_rows_preserve_coordinates_without_matching_inside_prefix(self):
        rak = {"description": {"individual_description_excerpt": "サイズ\nシングル:縦幅150cm×横幅210cm\nダブル:縦幅190cm×横幅210cm"}}
        identity = MODULE.enrich_task({"rakuten": {"attrs": {"size": "ダブル"}}, "au_candidates": []},
                                     {"rakuten_product": rak})["rakuten"]["resolved_identity"]
        self.assertEqual(identity["attrs"]["size_cm"], [210.0, 190.0])
        self.assertFalse(identity["source_conflicts"])

    def test_layer_construction_is_not_sold_quantity_or_guessed_variant(self):
        resolved = resolve_candidate({}, product("", [block("内容\nもことろん毛布2枚合わせ XS ×1\n収納袋 ×1")]))
        self.assertEqual(resolved["attrs"]["product_variant"], "もことろん毛布")
        self.assertEqual(resolved["attrs"]["construction_layers"], 2)
        self.assertNotIn("bundle_count", resolved["attrs"])

    def test_scoped_named_size_and_seo_dimensions_remain_separate(self):
        resolved = resolve_candidate({}, product("毛布 QT", [
            block("こちらのページは【QTサイズ】です\nサイズ/重さ\nQT:幅70×長さ100cm\n重さ\n1.6kg"),
            {"scope": "product_page_comment", "source_field": "itemComment", "text": "▼検索ワード\n幅60 幅90"}]))
        self.assertEqual(resolved["attrs"]["size_cm"], [70.0, 100.0])
        self.assertNotIn("width_cm", resolved["attrs"])

    def test_inline_diameter_has_explicit_selected_role(self):
        resolved = resolve_candidate({"size": "80cm(直径)"}, product())
        self.assertEqual(resolved["attrs"]["diameter_cm"], 80.0)
        self.assertNotIn("size", resolved["attrs"])

    def test_rakuten_title_does_not_select_first_advertised_size(self):
        page = {"title_raw": "敷きパッド シングル セミダブル ダブル",
                "description": {"individual_description_excerpt": "サイズ\n【ダブル】\n幅140cm×長さ205cm"}}
        result = MODULE.enrich_task({"rakuten": {"attrs": {"size": "ダブル"}}, "au_candidates": []},
                                   {"rakuten_product": page, "au_product": {}})
        identity = result["rakuten"]["resolved_identity"]
        self.assertEqual(identity["attrs"]["size_cm"], [140.0, 205.0])
        self.assertFalse(identity["source_conflicts"])
        self.assertIsNone(identity["title_selector"])

    def test_title_selects_only_explicit_body_size_crosswalk_and_copies(self):
        task = candidate_task({"サイズ": "D"})
        original = __import__("copy").deepcopy(task)
        dossier = {"rakuten_product": {}, "au_product": product("敷きパッド ダブル 吸水速乾", [
            block("サイズ"), block("（約）幅140cm×長さ205cm（ダブルサイズ）")])}
        resolved = MODULE.enrich_task(task, dossier)["au_candidates"][0]["resolved_identity"]
        self.assertEqual(task, original)
        self.assertEqual(resolved["attrs"]["size_cm"], [140.0, 205.0])
        self.assertTrue(resolved["evidence"]["size_title_selector"])
        self.assertEqual(resolved["unknown_fields"], [])

    def test_no_unstated_size_alias(self):
        resolved = resolve_candidate({"サイズ": "セミダブル"}, product("マットレス セミダブル", [
            block("サイズ"), block("【SD】幅120cm×長さ195cm")]))
        self.assertNotIn("size_cm", resolved["attrs"])
        self.assertIn("size_cm", resolved["unknown_fields"])
        self.assertTrue(resolved["needs_review"])

    def test_dimension_roles_remain_separate(self):
        resolved = resolve_candidate({}, product("", [
            block("サイズ"), block("本体サイズ：幅40×奥行30×高さ50cm"),
            block("折りたたみサイズ：幅40×奥行10×高さ60cm")]))
        self.assertEqual(resolved["attrs"]["body_dimensions_cm"], [40.0, 30.0, 50.0])
        self.assertEqual(resolved["attrs"]["folded_dimensions_cm"], [40.0, 10.0, 60.0])

    def test_wrong_conditional_tier_does_not_borrow_dimensions(self):
        resolved = resolve_candidate({"段数": "3段"}, product("", [
            block("サイズ"), block("2段：幅40×奥行30×高さ90cm"),
            block("3段：幅40×奥行30×高さ120cm")]))
        self.assertEqual(resolved["attrs"]["page_dimensions_cm"], [40.0, 30.0, 120.0])
        self.assertEqual(resolved["unknown_fields"], [])

    def test_unmapped_axis_is_retained_and_reviewed(self):
        resolved = resolve_candidate({"axis:unknown-colorway": "special"}, product())
        self.assertEqual(resolved["attrs"]["axis:unknown-colorway"], "special")
        self.assertIn("axis:unknown-colorway", resolved["unknown_fields"])

    def test_source_conflict_does_not_overwrite_selected_dimensions(self):
        resolved = resolve_candidate({"page_dimensions_cm": [40, 30, 100]}, product("", [
            block("サイズ"), block("幅40×奥行30×高さ120cm")]))
        self.assertEqual(resolved["attrs"]["page_dimensions_cm"], [40, 30, 100])
        self.assertTrue(any(x["field"] == "page_dimensions_cm" for x in resolved["source_conflicts"]))

    def test_components_are_extracted_from_explicit_content_section(self):
        resolved = resolve_candidate({"枚数": "13枚セット"}, product("", [
            block("内容"), block("パネル×12枚"), block("ドアパーツ×1個")]))
        self.assertEqual(resolved["attrs"]["bundle_components"], [
            {"name": "ドアパーツ", "count": 1.0, "unit": "個"},
            {"name": "パネル", "count": 12.0, "unit": "枚"}])


if __name__ == "__main__":
    unittest.main()
