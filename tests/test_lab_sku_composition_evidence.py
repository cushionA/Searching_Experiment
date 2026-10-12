"""Focused tests for composition evidence input construction."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "experiments/sku-matching/composition_evidence_v1.py"
SPEC = importlib.util.spec_from_file_location("composition_evidence_v1", MODULE_PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def block(**updates):
    value = {
        "model_passage": "商品1点、付属品：ケース",
        "support_text": "商品1点、付属品：ケース",
        "quote_refs": [{"quote": "商品1点、付属品：ケース", "field": "description"}],
        "links": [],
        "context": {},
    }
    value.update(updates)
    return value


class CompositionEvidenceTests(unittest.TestCase):
    def test_linked_table_context_excludes_every_row_even_if_anchor_is_elsewhere(self):
        table = {"source_ref": {"quote": '<TABLE><tr><td><A href="x">link</A></td></tr></TABLE>'}}
        first_row = block(context={"table": deepcopy(table)}, text="商品名")
        other_row = block(context={"table": deepcopy(table)}, text="数量：2")
        self.assertEqual(M.exclusion_reason(first_row), "linked_table_context")
        self.assertEqual(M.exclusion_reason(other_row), "linked_table_context")

    def test_nonempty_block_links_exclude_paragraph(self):
        self.assertEqual(M.exclusion_reason(block(links=[{"href": "https://example.test"}])), "block_has_links")

    def test_unbound_table_context_is_excluded_but_bound_link_free_table_is_valid(self):
        unbound = block(context={"table": {"rows": 2}})
        bound = block(context={"table": {"source_ref": {"quote": "<table><tr><td>付属品：ケース</td></tr></table>"}}})
        self.assertEqual(M.exclusion_reason(unbound), "unbound_table_context")
        self.assertIsNone(M.exclusion_reason(bound))

    def test_table_row_without_table_context_is_unbound(self):
        self.assertEqual(M.exclusion_reason(block(kind="table_row", context={})), "unbound_table_context")

    def test_keyword_heading_and_empty_evidence_are_excluded(self):
        self.assertEqual(M.exclusion_reason(block(text="\n▼検索ワード\nカメラ")), "explicit_keyword_block")
        self.assertEqual(M.exclusion_reason(block(support_text="  ")), "missing_support_text")
        self.assertEqual(M.exclusion_reason(block(quote_refs=[])), "missing_quote_refs")

    def test_builder_preserves_whole_raw_options_unknown_and_refs_without_wanted_value(self):
        options = ["本体＋ケース 2個", "本体／替え芯×３"]
        refs = [{"quote": "元引用", "start": 0, "end": 3}]
        source = block(model_passage="<表>  本体＋ケース 2個\n数量：２", quote_refs=refs)
        request = M.choice_request("sku:g1:b1", "セット内容", options, "不明（診断用）", source,
                                   metadata={"group_id": "g1", "title": "must not enter", "id": "bad",
                                             "question": "bad", "state": "bad", "choices": [], "nested": {"a": 1}})
        self.assertEqual([c["label"] for c in request["choices"]], options + ["不明（診断用）"])
        self.assertEqual([c["description"] for c in request["choices"][:2]], [
            "購入対象の〈セット内容〉は〈本体＋ケース 2個〉である。",
            "購入対象の〈セット内容〉は〈本体／替え芯×３〉である。",
        ])
        self.assertEqual(request["choices"][-1], {
            "label": "不明（診断用）", "description": "この記載から購入対象の構成を特定できない。"})
        self.assertEqual(request["quote_refs"], refs)
        self.assertIsNot(request["quote_refs"], refs)
        self.assertEqual(request["id"], "sku:g1:b1")
        self.assertEqual(request["question"], "購入対象の仕様・セット内容について、〈セット内容〉はどの選択肢に該当しますか。")
        self.assertNotIn("must not enter", request["state"])
        self.assertIn("<表>  本体＋ケース 2個\n数量：２", request["state"])
        self.assertNotIn("selected_value", request)

    def test_query_and_state_include_selected_axes_only_for_paired_request(self):
        axes = "色：赤\n容量：500ml"
        options = ["標準", "増量版"]
        standalone = M.query_for("内容", options)
        paired = M.query_for("内容", options, axes)
        self.assertNotIn(axes, standalone)
        self.assertIn("元の確認軸：内容", standalone)
        self.assertIn("元の選択肢（原文・順序保持）：\n標準\n増量版", standalone)
        self.assertIn(axes, paired)

        req = M.choice_request("paired", "内容", options, "unknown", block(), axes)
        self.assertTrue(req["state"].startswith("対象SKUの選択軸：\n" + axes + "\n"))
        self.assertIn("この商品の記載された仕様・セット内容：\n商品1点、付属品：ケース", req["state"])

    def test_metadata_and_source_quote_refs_are_deep_copied_and_core_fields_win(self):
        metadata = {"id": "wrong", "question": "wrong", "state": "wrong", "choices": [],
                    "extra": {"refs": [{"x": 1}]}}
        source = block()
        req = M.choice_request("right", "数量", ["1"], "unknown", source, metadata=metadata)
        req["extra"]["refs"][0]["x"] = 9
        req["quote_refs"][0]["quote"] = "changed"
        self.assertEqual(metadata["id"], "wrong")
        self.assertEqual(metadata["extra"]["refs"][0]["x"], 1)
        self.assertEqual(source["quote_refs"][0]["quote"], "商品1点、付属品：ケース")
        self.assertEqual(req["id"], "right")

    def test_empty_duplicate_and_non_string_options_or_unknown_collision_are_rejected(self):
        for options in ([""], ["あり", "あり"], ["あり", True]):
            with self.subTest(options=options), self.assertRaises(ValueError):
                M.query_for("内容", options)
        with self.assertRaisesRegex(ValueError, "collide"):
            M.choice_request("x", "内容", ["あり", "なし"], "あり", block())
        with self.assertRaises(ValueError):
            M.choice_request("x", "内容", ["あり"], True, block())


if __name__ == "__main__":
    unittest.main()
