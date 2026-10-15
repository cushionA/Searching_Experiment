"""Synthetic boundary tests for source-bound product specification tables."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))

import sku_spec_table_first_v1 as tables


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SpecTableFirstTests(unittest.TestCase):
    def source_metadata(self, html: str) -> dict:
        lexeme = json.dumps(html, ensure_ascii=False)
        return {
            "raw_file": "synthetic.json",
            "sha256": "a" * 64,
            "locator": "$.itemInfo.itemComment",
            "encoding": "utf-8",
            "html_sha256": sha(html.encode("utf-8")),
            "json_leaf_start": 0,
            "json_leaf_end": len(lexeme),
            "json_lexeme": lexeme,
        }

    def capture_document(self, root: Path, html: str, *, side="au", filename="capture.json",
                         locator="$.itemInfo.itemComment", full_body="FULL DESCRIPTION",
                         raw_encoding="utf-8", declared_encoding="utf-8") -> dict:
        if side == "au":
            raw = json.dumps({"itemInfo": {"itemComment": html}}, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        else:
            raw = html.encode(raw_encoding)
            locator = {"encoding": declared_encoding, "field": "synthetic-description"}
        (root / filename).write_bytes(raw)
        digest = sha(raw)
        return {
            "document_id": "synthetic-product",
            "document_sha256": "d" * 64,
            "side": side,
            "description": {
                "text": full_body,
                "segments": [{"source_refs": [{"raw_file": filename, "sha256": digest,
                                                  "locator": locator}]}],
            },
        }

    def test_two_key_value_rows_are_selected_without_surrounding_body_noise(self):
        html = ("<p>Before noise</p><table><tr><th>色</th><td>青</td></tr>"
                "<tr><th>サイズ</th><td>M</td></tr></table><p>After noise</p>")
        selected = tables.select_html(html, self.source_metadata(html))
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["text"], "色: 青\nサイズ: M")
        self.assertNotIn("noise", selected[0]["text"])

    def test_layout_banner_and_link_cards_without_spec_structure_are_excluded(self):
        cases = {
            "layout": "<table><tr><td>left</td><td>right</td></tr><tr><td>nav</td><td>footer</td></tr></table>",
            "banner": "<table><tr><td colspan='4'>Campaign banner</td></tr></table>",
            "link cards": ("<table><tr><td><a href='/a'>First link</a></td></tr>"
                           "<tr><td><a href='/b'>Second link</a></td></tr></table>"),
        }
        for label, html in cases.items():
            with self.subTest(label=label):
                self.assertEqual(tables.select_html(html, self.source_metadata(html)), [])

    def test_rakuten_tables_outside_item_desc_are_not_selected(self):
        html = ("<table><tr><th>Navigation</th><td>Outside</td></tr>"
                "<tr><th>Campaign</th><td>Banner</td></tr></table>"
                "<span class='item_desc'><table><tr><th>Material</th><td>Cotton</td></tr>"
                "<tr><th>Size</th><td>Large</td></tr></table></span>")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = self.capture_document(root, html, side="rakuten", filename="rakuten.html")
            result = tables.select_document(document, root)
        self.assertEqual(result["mode"], "spec_table")
        self.assertEqual(len(result["tables"]), 1)
        self.assertIn("Material: Cotton", result["text"])
        self.assertNotIn("Navigation", result["text"])

    def test_item_desc_table_is_kept_inside_an_ancestor_layout_table(self):
        html = ("<table class='page-layout'><tr><td><nav>Page links</nav>"
                "<span class='item_desc'><table><tr><th>素材</th><td>綿</td></tr>"
                "<tr><th>サイズ</th><td>大</td></tr></table></span>"
                "</td></tr></table>")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = self.capture_document(root, html, side="rakuten", filename="nested.html")
            result = tables.select_document(document, root)
        self.assertEqual(result["mode"], "spec_table")
        self.assertEqual(len(result["tables"]), 1)
        self.assertEqual(result["tables"][0]["rows"][0]["cells"][0]["text"], "素材")
        self.assertNotIn("Page links", result["text"])

    def test_rakuten_utf8_and_declared_euc_jp_bytes_keep_hash_and_span_audit(self):
        html = ("<span class='item_desc'><table><tr><th>素材</th><td>綿</td></tr>"
                "<tr><th>色</th><td>青</td></tr></table></span>")
        for raw_encoding, declared_encoding, expected_encoding in (
                ("utf-8", "euc_jp", "utf-8"),
                ("euc_jp", "euc_jp", "euc_jp")):
            with self.subTest(raw_encoding=raw_encoding):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    document = self.capture_document(
                        root, html, side="rakuten", filename="encoded.html",
                        raw_encoding=raw_encoding, declared_encoding=declared_encoding)
                    raw = (root / "encoded.html").read_bytes()
                    result = tables.select_document(document, root)
                self.assertEqual(result["mode"], "spec_table")
                source = result["raw_sources"][0]
                self.assertEqual(source["sha256"], sha(raw))
                self.assertEqual(source["encoding"], expected_encoding)
                self.assertEqual(source["html_sha256"], sha(html.encode("utf-8")))
                source_ref = result["tables"][0]["source_ref"]
                self.assertEqual(source_ref["encoding"], expected_encoding)
                self.assertEqual(source_ref["quote"], html[source_ref["start"]:source_ref["end"]])

    def test_td_only_rows_need_spec_caption_or_heading(self):
        html = ("<table><tr><td>Color</td><td>Red</td></tr>"
                "<tr><td>Size</td><td>Medium</td></tr></table>")
        self.assertEqual(tables.select_html(html, self.source_metadata(html)), [])

    def test_explicit_product_spec_caption_allows_td_only_key_value_rows(self):
        html = ("<table><caption>商品仕様</caption><tr><td>色</td><td>紺</td></tr>"
                "<tr><td>材質</td><td>綿</td></tr></table>")
        selected = tables.select_html(html, self.source_metadata(html))
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["caption"], "商品仕様")
        self.assertIn("色: 紺", selected[0]["text"])

    def test_arbitrary_coupon_colspan_heading_does_not_qualify_td_only_rows(self):
        html = ("<table><tr><td colspan='2'>クーポン配布</td></tr>"
                "<tr><td>対象</td><td>購入者</td></tr>"
                "<tr><td>期限</td><td>6月末</td></tr></table>")
        self.assertEqual(tables.select_html(html, self.source_metadata(html)), [])

    def test_spec_colspan_heading_can_qualify_td_only_rows(self):
        html = ("<table><tr><td colspan='2'>商品仕様</td></tr>"
                "<tr><td>色</td><td>青</td></tr><tr><td>素材</td><td>綿</td></tr></table>")
        selected = tables.select_html(html, self.source_metadata(html))
        self.assertEqual(len(selected), 1)
        self.assertIn("色: 青", selected[0]["text"])

    def test_condition_composite_value_and_unit_are_retained_verbatim(self):
        html = ("<table><tr><th>着用条件</th><td>未開封・未使用</td></tr>"
                "<tr><th>内容</th><td>【2枚セット】・Lサイズ（単位：cm）</td></tr></table>")
        selected = tables.select_html(html, self.source_metadata(html))
        self.assertEqual(len(selected), 1)
        self.assertIn("着用条件: 未開封・未使用", selected[0]["text"])
        self.assertIn("【2枚セット】・Lサイズ（単位：cm）", selected[0]["text"])
        self.assertEqual(selected[0]["rows"][1]["cells"][1]["text"], "【2枚セット】・Lサイズ（単位：cm）")

    def test_raw_json_hash_and_exact_field_span_provenance_are_checked(self):
        # A non-ASCII field verifies Unicode offsets and the exact source lexeme.
        html = "<table><tr><th>色</th><td>青</td></tr><tr><th>柄</th><td>格子</td></tr></table>"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            doc = self.capture_document(root, html)
            result = tables.select_document(doc, root)
            self.assertEqual(result["mode"], "spec_table")
            table_ref = result["tables"][0]["source_ref"]
            self.assertEqual(table_ref["quote"], html[table_ref["start"]:table_ref["end"]])
            self.assertEqual(table_ref["html_leaf"], ["itemInfo", "itemComment"])
            wrong_sha = dict(doc)
            wrong_sha["description"] = json.loads(json.dumps(doc["description"]))
            wrong_sha["description"]["segments"][0]["source_refs"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "raw source SHA256 mismatch"):
                tables.select_document(wrong_sha, root)
            wrong_field = dict(doc)
            wrong_field["description"] = json.loads(json.dumps(doc["description"]))
            wrong_field["description"]["segments"][0]["source_refs"][0]["locator"] = "$.itemInfo.other"
            with self.assertRaises((KeyError, ValueError)):
                tables.select_document(wrong_field, root)

    def test_complex_rowspan_falls_back_to_exact_full_body(self):
        html = ("<table><tr><th rowspan='2'>Color</th><td>Red</td></tr>"
                "<tr><td>Blue</td></tr><tr><th>Size</th><td>Large</td></tr></table>")
        fallback = "  Full description\nwith original spacing.  "
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = self.capture_document(root, html, full_body=fallback)
            result = tables.select_document(document, root)
        self.assertEqual(result["mode"], "full_body_fallback")
        self.assertEqual(result["text"], fallback)
        self.assertEqual(result["tables"], [])
        self.assertEqual(result["policy"]["reason"], "complex_table_span")

    def test_duplicate_table_content_is_deduplicated_with_both_provenances(self):
        table_html = "<table><tr><th>材質</th><td>綿</td></tr><tr><th>色</th><td>白</td></tr></table>"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.capture_document(root, table_html, filename="first.json")
            second = self.capture_document(root, table_html, filename="second.json")
            document = json.loads(json.dumps(first))
            second_ref = second["description"]["segments"][0]["source_refs"][0]
            document["description"]["segments"].append({"source_refs": [second_ref]})
            result = tables.select_document(document, root)
        self.assertEqual(result["mode"], "spec_table")
        self.assertEqual(len(result["tables"]), 1)
        self.assertEqual(len(result["tables"][0]["provenance"]), 2)
        self.assertEqual({ref["raw_file"] for ref in result["tables"][0]["provenance"]},
                         {"first.json", "second.json"})
        self.assertEqual(result["text"].count("材質: 綿"), 1)


if __name__ == "__main__":
    unittest.main()
