from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
from composition_source_blocks_v1 import build_rakuten_blocks


class CompositionSourceBlocksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.prepared = Path(self.temp.name)
        (self.prepared / "raw-captures").mkdir()
        self.html = ("<html><body><table><tr><th>内容</th><th>数量</th></tr>"
                     "<tr><td>カバー付きセット</td><td>2枚</td></tr></table>"
                     "<p>セット商品です。</p></body></html>")
        self.raw = self.html.encode("euc_jp")
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.capture = self.prepared / "raw-captures" / (self.digest + ".html")
        self.capture.write_bytes(self.raw)
        self.raw_alias = r".lab-output\source\item.html"
        registry = {"raw_file": ".lab-output/source/item.html",
                    "original_aliases": [self.raw_alias], "sha256": self.digest,
                    "bytes": len(self.raw), "bundle_file": "raw-captures/" + self.capture.name,
                    "original_sources": []}
        (self.prepared / "raw-source-registry.jsonl").write_text(
            json.dumps(registry) + "\n", encoding="utf-8")
        self.visible = "内容\n数量\nカバー付きセット\n2枚\nセット商品です。"
        self.document = {
            "side": "rakuten", "document_id": "rakuten:test",
            "document_sha256": "doc-sha", "title": {"text": "test", "source_refs": [
                {"raw_file": ".lab-output/source/item.html", "sha256": self.digest,
                 "locator": {"kind": "html_text", "path": "html/head/title", "encoding": "EUC-JP"}}
            ]},
            "description": {"text": self.visible, "segments": [{"start": 0,
                "end": len(self.visible), "source_refs": [{"raw_file": self.raw_alias,
                "sha256": self.digest, "locator": "whole_document_visible_text"}]}]},
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_table_rows_bind_to_same_source_and_keep_raw_metadata(self):
        blocks, excluded, files = build_rakuten_blocks(self.prepared, [self.document])
        self.assertTrue(any(block["kind"] == "table_row" and "カバー付きセット" in block["text"]
                            for block in blocks))
        self.assertEqual(excluded, [])
        row = next(block for block in blocks if "カバー付きセット" in block["text"])
        self.assertEqual(row["scope_status"], "unproved")
        self.assertEqual(row["source_ref"]["sha256"], self.digest)
        self.assertEqual(row["source_ref"]["locator"], {
            "kind": "html_text", "json_path": "whole_document_visible_text", "encoding": "EUC-JP"})
        self.assertEqual(row["quote_refs"][0]["quote"], "カバー付きセット")
        self.assertEqual(files, {str(self.capture.resolve()): self.digest})

    def test_same_capture_bound_to_two_documents_has_distinct_block_identities(self):
        second = json.loads(json.dumps(self.document))
        second["document_id"] = "rakuten:other"
        second["document_sha256"] = "other-doc-sha"
        blocks, excluded, files = build_rakuten_blocks(self.prepared, [self.document, second])
        self.assertEqual(excluded, [])
        self.assertEqual(len(blocks), len({block["block_id"] for block in blocks}))
        self.assertEqual({block["target_document_id"] for block in blocks}, {"rakuten:test", "rakuten:other"})
        self.assertEqual(len(files), 1)

    def test_missing_description_source_is_recorded_without_guessing_from_title(self):
        empty = json.loads(json.dumps(self.document))
        empty["description"]["segments"] = []
        blocks, exclusions, files = build_rakuten_blocks(self.prepared, [empty])
        self.assertEqual(blocks, [])
        self.assertEqual(files, {})
        self.assertEqual(exclusions[0]["exclusion_reason"], "no_bound_whole_document_description_source")

    def test_each_table_cell_has_literal_document_quote_reference(self):
        blocks, _, _ = build_rakuten_blocks(self.prepared, [self.document])
        row = next(block for block in blocks if block["kind"] == "table_row"
                   and "カバー付きセット" in block["text"])
        quotes = {ref["quote"] for ref in row["quote_refs"]}
        self.assertTrue({"カバー付きセット", "2枚"}.issubset(quotes))
        self.assertTrue(all(ref["document_id"] == self.document["document_id"] for ref in row["quote_refs"]))

    def test_bad_raw_sha_or_size_fails_closed(self):
        self.capture.write_bytes(self.raw + b" ")
        with self.assertRaisesRegex(ValueError, "size/SHA mismatch"):
            build_rakuten_blocks(self.prepared, [self.document])

    def test_unmatched_visible_part_excludes_entire_block(self):
        altered = json.loads(json.dumps(self.document))
        altered["description"]["text"] = "本文に表の値がありません"
        altered["description"]["segments"][0]["end"] = len(altered["description"]["text"])
        blocks, exclusions, _ = build_rakuten_blocks(self.prepared, [altered])
        self.assertEqual(blocks, [])
        self.assertTrue(exclusions)
        self.assertTrue(all(item["exclusion_reason"] ==
                            "not_all_visible_parts_bind_same_source_document" for item in exclusions))


if __name__ == "__main__":
    unittest.main()
