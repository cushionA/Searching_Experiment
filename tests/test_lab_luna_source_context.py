import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
from sku_luna_source_context import build_source_contexts


class LunaSourceContextTest(unittest.TestCase):
    def make_zip(self, raw, expected=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "inputs.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("raw.json", raw)
        return path, {"raw_file": "raw.json", "sha256": expected or hashlib.sha256(raw).hexdigest(),
                      "locator": {"json_path": "$.itemInfo.extraItemComment"}}

    def test_other_item_link_wins_even_when_visible_text_matches(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": '<h2>サイズ</h2><table><tr><th>商品</th><td><a href="/item/other">4段</a></td></tr><tr><th>色</th><td>白</td></tr></table>'}}).encode()
        path, ref = self.make_zip(raw)
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S1", "kind": "description", "text": "4段", "source": [ref]}]}, path)["S1"]
        self.assertEqual(result["scope"], "other_product")
        self.assertEqual(result["text"], "4段")
        self.assertTrue(any("商品" in c and "4段" in c for c in result["contexts"]))
        self.assertTrue(any("サイズ" in c for c in result["contexts"]))
        self.assertEqual(result["context_provenance"][0]["json_path"], "$.itemInfo.extraItemComment")

    def test_entities_inline_markup_and_duplicate_context_dedup(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": '<p>見出し &amp; 詳細</p><p><b>白</b> &amp; 黒</p>'}}).encode()
        path, ref = self.make_zip(raw)
        sources = [{"source_id": "S2", "kind": "description", "text": "白 & 黒", "source": [ref, ref]}]
        result = build_source_contexts({"au_product_id": "fixed", "sources": sources}, path)["S2"]
        self.assertEqual(result["scope"], "fixed_product")
        self.assertIn("白 & 黒", result["contexts"])
        self.assertEqual(len(result["contexts"]), len(set(result["contexts"])))

    def test_missing_or_hash_mismatched_evidence_cannot_be_fixed(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": "3段"}}).encode()
        path, ref = self.make_zip(raw, "0" * 64)
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S0", "kind": "title", "text": "3段", "source": ref}]}, path)["S0"]
        self.assertEqual(result["scope"], "unresolved")
        self.assertEqual(result["contexts"], [])

    def test_hidden_content_is_ignored_and_non_item_link_is_ambiguous(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": '<script>HIDDEN_SECRET</script><a href="https://example.org/help">value</a>'}}).encode()
        path, ref = self.make_zip(raw)
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S1", "kind": "description", "text": "value", "source": [ref]}]}, path)["S1"]
        self.assertEqual(result["scope"], "ambiguous")
        self.assertFalse(any("HIDDEN_SECRET" in c for c in result["contexts"]))

    def test_unrelated_attribute_does_not_apply_special_rules(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": '<p data-company="anything">一般的な説明</p>'}}).encode()
        path, ref = self.make_zip(raw)
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S3", "kind": "description", "text": "一般的な説明", "source": [ref]}]}, path)["S3"]
        self.assertEqual(result["scope"], "fixed_product")
        self.assertIn("一般的な説明", result["contexts"])

    def test_unrelated_item_link_does_not_taint_unlinked_spec(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": '<p><a href="/item/other">関連商品を見る</a></p><table><tr><th>段数</th><td>4段</td></tr></table>'}}).encode()
        path, ref = self.make_zip(raw)
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S4", "kind": "description", "text": "4段", "source": [ref]}]}, path)["S4"]
        self.assertEqual(result["scope"], "fixed_product")
        self.assertEqual(len(result["contexts"]), 1)
        self.assertIn("段数", result["contexts"][0])

    def test_same_phrase_plain_and_other_item_link_is_ambiguous(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": '<p>本商品は4段です。</p><p><a href="/item/other">4段</a></p>'}}).encode()
        path, ref = self.make_zip(raw)
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S5", "kind": "description", "text": "4段", "source": [ref]}]}, path)["S5"]
        self.assertEqual(result["scope"], "ambiguous")
        self.assertLessEqual(len(result["contexts"]), 4)

    def test_same_phrase_linked_to_current_and_other_items_is_ambiguous(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": '<p><a href="/item/fixed">4段</a></p><p><a href="https://wowma.jp/item/other">4段</a></p>'}}).encode()
        path, ref = self.make_zip(raw)
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S6", "kind": "description", "text": "4段", "source": [ref]}]}, path)["S6"]
        self.assertEqual(result["scope"], "ambiguous")

    def test_jsonl_line_and_bracket_path_locator(self):
        raw = '{"rows":[{"text":"old"}]}\n{"rows":[{"text":"現行"}]}'.encode()
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "inputs.zip"
        with zipfile.ZipFile(path, "w") as zf: zf.writestr("raw.jsonl", raw)
        ref = {"raw_file": "raw.jsonl", "sha256": hashlib.sha256(raw).hexdigest(),
               "locator": {"line": 2, "json_path": "$.rows[0].text"}}
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S7", "kind": "description", "text": "現行", "source": ref}]}, path)["S7"]
        self.assertEqual(result["scope"], "fixed_product")
        self.assertEqual(result["context_provenance"][0]["line"], 2)

    def test_hidden_void_does_not_hide_following_spec(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": '<img hidden><br style="display:none"><p>本体は3段</p>'}}).encode()
        path, ref = self.make_zip(raw)
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S8", "kind": "description", "text": "本体は3段", "source": ref}]}, path)["S8"]
        self.assertEqual(result["scope"], "fixed_product")

    def test_invalid_path_suffix_cannot_resolve_as_valid_prefix(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": "本体は3段"}}).encode()
        path, ref = self.make_zip(raw)
        ref["locator"]["json_path"] += "[0]junk"
        result = build_source_contexts({"au_product_id": "fixed", "sources": [{"source_id": "S8", "kind": "description", "text": "本体は3段", "source": ref}]}, path)["S8"]
        self.assertEqual(result["scope"], "unresolved")


if __name__ == "__main__":
    unittest.main()
