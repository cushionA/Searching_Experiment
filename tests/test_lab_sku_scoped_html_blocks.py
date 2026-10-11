"""Synthetic DOM/binding fixtures only; these are not SKU evaluation examples."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scoped_html_blocks", ROOT / "experiments/sku-matching/build_scoped_html_blocks_v1.py")
BLOCKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BLOCKS)


def source(path="$.itemInfo.extraItemComment"):
    return {"raw_file": "synthetic-only/item.json", "sha256": "a" * 64,
            "locator": {"kind": "json_leaf", "json_path": path}}


class ScopedHtmlBlockContracts(unittest.TestCase):
    def test_table_content_cell_keeps_all_inline_conditions_and_items(self):
        html = ('<h2>商品詳細</h2><table><caption>セット構成</caption>'
                '<tr><th colspan="2">販売内容</th></tr>'
                '<tr><th>内容</th><td>【幅100cm】<br>遮光カーテン 2枚<br>レースカーテン 2枚<br>'
                '<br>【幅150cm】<br>遮光カーテン 1枚<br>レースカーテン 1枚</td></tr></table>')
        blocks = BLOCKS.extract_blocks(html, source())
        row = next(block for block in blocks if block["kind"] == "table_row" and len(block["cells"]) == 2)
        self.assertEqual([cell["tag"] for cell in row["cells"]], ["th", "td"])
        self.assertEqual(row["cells"][1]["text"],
                         "【幅100cm】\n遮光カーテン 2枚\nレースカーテン 2枚\n【幅150cm】\n遮光カーテン 1枚\nレースカーテン 1枚")
        self.assertEqual(row["context"]["headings"][0]["text"], "商品詳細")
        self.assertEqual(row["context"]["table"]["captions"][0]["text"], "セット構成")
        self.assertEqual(row["context"]["table"]["preceding_header_rows"][0]["text"], "販売内容")
        self.assertFalse(row["context"]["applicability_inferred"])
        self.assertEqual(row["visible_parts"], ["内容", "【幅100cm】", "遮光カーテン 2枚", "レースカーテン 2枚",
                                                "【幅150cm】", "遮光カーテン 1枚", "レースカーテン 1枚"])

    def test_linked_other_product_is_retained_as_link_not_current_specification(self):
        html = ('<table><tr><td>別構成はこちら</td></tr><tr><td>'
                '<a href="/item/other"><img alt="別セット" src="image">レース付きセット</a>'
                '</td></tr></table><table><tr><th>内容</th><td>本体 2枚</td></tr></table>')
        blocks = BLOCKS.extract_blocks(html, source(), title="現在の商品")
        linked = next(block for block in blocks if block["links"])
        current = next(block for block in blocks if "本体 2枚" in block["text"])
        self.assertEqual(linked["links"][0]["href"], "/item/other")
        self.assertIn("レース付きセット", linked["links"][0]["text"])
        self.assertNotEqual(linked["context"]["table"]["table_id"], current["context"]["table"]["table_id"])
        self.assertEqual(current["links"], [])
        self.assertNotIn("レース付きセット", current["text"])
        self.assertEqual(current["context"]["source_title_context_only"], "現在の商品")
        self.assertNotIn("nearby_before", current["context"]["table"])

    def test_exact_offsets_are_decoded_json_codepoints_not_utf8_bytes(self):
        html = '日本語\r\n<p>青 &amp; 白<br>Ａ\u3000Ｂ</p>'
        blocks = BLOCKS.extract_blocks(html, source())
        paragraph = next(block for block in blocks if block["kind"] == "paragraph")
        self.assertEqual(paragraph["html_start"], html.index("<p>"))
        self.assertEqual(paragraph["source_ref"]["quote"], '<p>青 &amp; 白<br>Ａ\u3000Ｂ</p>')
        entity = next(run for run in paragraph["text_runs"] if run["kind"] == "entity")
        self.assertEqual(entity["text"], "&")
        self.assertEqual(entity["source_ref"]["quote"], "&amp;")
        self.assertIn("Ａ\u3000Ｂ", paragraph["visible_parts"])
        self.assertFalse(paragraph["text_is_exact_quote"])
        BLOCKS.verify_blocks(html, blocks)

    def test_script_and_style_are_not_visible_but_purchase_cart_specs_survive(self):
        html = ('<p>ペットカート仕様。購入時注意：高さ50cmのシルバーには持ち手がありません。'
                '<script>架空の商品</script><style>.x{content:"嘘"}</style></p>')
        blocks = BLOCKS.extract_blocks(html, source())
        text = "\n".join(block["text"] for block in blocks)
        self.assertIn("ペットカート仕様", text)
        self.assertIn("高さ50cmのシルバーには持ち手がありません", text)
        self.assertNotIn("架空の商品", text)
        self.assertNotIn("content", text)

    def test_optional_cell_and_row_end_tags_recover_exact_source_fragments(self):
        html = '<table><tr><th>名称<td>本体<tr><th>内容<td>2枚</table>'
        blocks = BLOCKS.extract_blocks(html, source())
        rows = [block for block in blocks if block["kind"] == "table_row"]
        self.assertEqual(len(rows), 2)
        self.assertEqual([cell["text"] for cell in rows[0]["cells"]], ["名称", "本体"])
        self.assertEqual([cell["text"] for cell in rows[1]["cells"]], ["内容", "2枚"])
        self.assertEqual(rows[0]["source_ref"]["quote"], '<tr><th>名称<td>本体')
        self.assertEqual(rows[0]["source_ref"]["closure"], "implicit_before_start_tag")
        BLOCKS.verify_blocks(html, blocks)

    def test_nested_table_has_distinct_owner_and_no_wrapper_flattening(self):
        html = ('<div><p>現在の商品</p><table><tr><td>主条件'
                '<table><tr><td>詳細条件</td></tr></table></td></tr></table><p>注意</p></div>')
        blocks = BLOCKS.extract_blocks(html, source())
        self.assertEqual([block["text"] for block in blocks if block["kind"] == "paragraph"], ["現在の商品", "注意"])
        rows = [block for block in blocks if block["kind"] == "table_row"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["context"]["table"]["table_id"], rows[1]["context"]["table"]["table_id"])
        self.assertEqual(len(rows[0]["cells"]), 1)
        self.assertEqual(len(rows[1]["cells"]), 1)

    def test_visible_search_keywords_are_preserved_with_separate_field_identity(self):
        html = '▼検索ワード<br>2枚 4枚 レースあり レースなし'
        short = BLOCKS.extract_blocks(html, source("$.itemInfo.itemComment"))
        long = BLOCKS.extract_blocks(html, source("$.itemInfo.extraItemComment"))
        self.assertEqual(short[0]["text"], "▼検索ワード\n2枚 4枚 レースあり レースなし")
        self.assertNotEqual(short[0]["block_id"], long[0]["block_id"])
        self.assertEqual(short[0]["source_ref"]["locator"]["json_path"], "$.itemInfo.itemComment")

    def test_parent_heading_hierarchy_keeps_structural_scope(self):
        html = '<h1>シリーズ</h1><h2>商品A</h2><p>A仕様</p><h2>商品B</h2><p>B仕様</p>'
        blocks = BLOCKS.extract_blocks(html, source())
        paragraphs = [block for block in blocks if block["kind"] == "paragraph"]
        self.assertEqual([heading["text"] for heading in paragraphs[0]["context"]["headings"]], ["シリーズ", "商品A"])
        self.assertEqual([heading["text"] for heading in paragraphs[1]["context"]["headings"]], ["シリーズ", "商品B"])

    def test_rowspan_context_keeps_origin_cell_for_later_row(self):
        html = ('<table><tr><th rowspan="2">条件A</th><td>第一仕様</td></tr>'
                '<tr><td>第二仕様</td></tr><tr><th>条件B</th><td>第三仕様</td></tr></table>')
        rows = [block for block in BLOCKS.extract_blocks(html, source()) if block["kind"] == "table_row"]
        inherited = rows[1]["context"]["table"]["inherited_rowspan_cells"]
        self.assertEqual(inherited[0]["text"], "条件A")
        self.assertEqual(inherited[0]["column_index"], 0)
        self.assertEqual(inherited[0]["source_ref"]["quote"], '<th rowspan="2">条件A</th>')
        self.assertEqual(rows[2]["context"]["table"]["inherited_rowspan_cells"], [])

    def test_reference_tampering_in_context_and_runs_is_rejected(self):
        html = '<h2>見出し</h2><table><tr><td>本体</td></tr></table>'
        blocks = BLOCKS.extract_blocks(html, source())
        for path in ("block", "heading", "run", "json_path"):
            copied = copy.deepcopy(blocks)
            row = next(block for block in copied if block["kind"] == "table_row")
            ref = row["source_ref"] if path == "block" else row["context"]["headings"][0]["source_ref"] if path == "heading" else row["text_runs"][0]["source_ref"]
            if path == "json_path":
                ref["locator"]["json_path"] = "$.itemInfo.invalidField"
            else:
                ref["quote"] = "存在しない"
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "binding differs"):
                BLOCKS.verify_blocks(html, copied)

    def test_missing_source_identity_is_rejected_and_blocks_are_deterministic(self):
        with self.assertRaises(ValueError):
            BLOCKS.extract_blocks("<p>本体</p>", {})
        html = '<p>本体</p>'
        self.assertEqual(BLOCKS.extract_blocks(html, source()), BLOCKS.extract_blocks(html, source()))
        self.assertEqual(BLOCKS.extract_blocks("", source()), [])

    def test_file_helper_reads_three_fields_and_binds_raw_file_sha(self):
        data = {"itemInfo": {"itemTitle": "合成テスト", "itemComment": "短い説明",
                            "extraItemComment": '<table><tr><td>本文</td></tr></table>',
                            "extraItemCommentWithVideo": '<p>動画側説明</p>'}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "item.json"
            body = json.dumps(data, ensure_ascii=False).encode()
            path.write_bytes(body)
            blocks = BLOCKS.blocks_from_item_file(path, raw_file="synthetic-only/item.json")
            self.assertEqual({block["source_ref"]["locator"]["json_path"] for block in blocks},
                             {"$.itemInfo." + field for field in BLOCKS.FIELDS})
            self.assertEqual({block["source_ref"]["sha256"] for block in blocks}, {BLOCKS.sha(body)})
            self.assertEqual({block["source_ref"]["raw_file"] for block in blocks}, {"synthetic-only/item.json"})


if __name__ == "__main__":
    unittest.main()
