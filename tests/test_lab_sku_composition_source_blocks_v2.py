from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import composition_source_blocks_v1 as V1
import composition_source_blocks_v2 as V2
import sku_gate_sources as SOURCES


class CompositionSourceBlocksV2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.prepared = Path(self.temp.name) / "prepared"
        (self.prepared / "raw-captures").mkdir(parents=True)
        self.html = ("<html><body><h2>商品内容</h2><p>カバー付きセット</p>"
                     "<table><tr><th>内容</th><th>数量</th></tr>"
                     "<tr><td>カバー</td><td>2枚</td></tr></table></body></html>")
        self.raw = self.html.encode("euc_jp")
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.capture = self.prepared / "raw-captures" / (self.digest + ".html")
        self.capture.write_bytes(self.raw)
        alias = ".lab-output/source/item.html"
        rec = {"raw_file": alias, "original_aliases": [alias.replace("/", "\\")],
               "sha256": self.digest, "bytes": len(self.raw),
               "bundle_file": "raw-captures/" + self.capture.name, "original_sources": []}
        (self.prepared / "raw-source-registry.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
        self.visible = "商品内容\nカバー付きセット\n内容\n数量\nカバー\n2枚"
        self.document = {"side": "rakuten", "document_id": "rakuten:test",
            "document_sha256": "doc-sha", "title": {"text": "test", "source_refs": [
                {"raw_file": alias, "sha256": self.digest,
                 "locator": {"kind": "html_text", "path": "html/head/title", "encoding": "EUC-JP"}}]},
            "description": {"text": self.visible, "segments": [{"start": 0,
                "end": len(self.visible), "source_refs": [{"raw_file": alias,
                "sha256": self.digest, "locator": "whole_document_visible_text"}]}]}}

    def tearDown(self):
        self.temp.cleanup()

    def test_nested_table_cell_heading_spans_are_rawstore_verified(self):
        blocks, exclusions, pins = V2.build_rakuten_blocks(self.prepared, [self.document])
        self.assertFalse(exclusions)
        self.assertTrue(blocks)
        store = V2.BundleStore(self.prepared)
        heading = next(b for b in blocks if "商品内容" in b["text"])
        table = next(b for b in blocks if b["kind"] == "table_row" and "カバー" in b["text"])
        for block in (heading, table):
            spans = list(V2._iter_spans(block))
            self.assertTrue(spans)
            self.assertTrue(all(SOURCES.verify_span(store, span) for _, span in spans))
            self.assertTrue(all(span["locator"]["kind"] == "html_text" and
                                span["locator"]["encoding"] == "EUC-JP" for _, span in spans))
        self.assertTrue(any("cells[0].source_ref" in path for path, _ in V2._iter_spans(table)))
        self.assertEqual(set(pins.values()), {self.digest})

    def test_offsets_raw_metadata_and_model_inputs_match_v1(self):
        old_blocks, _, _ = V1.build_rakuten_blocks(self.prepared, [self.document])
        blocks, _, _ = V2.build_rakuten_blocks(self.prepared, [self.document])
        old = {b["block_id"]: b for b in old_blocks}
        new = {b["block_id"]: b for b in blocks}
        self.assertEqual(old.keys(), new.keys())
        for block_id, current in new.items():
            before = old[block_id]
            for key in ("model_passage", "quote_refs", "block_id", "source_html_block_id"):
                self.assertEqual(current[key], before[key])
            old_spans = {path: span for path, span in V2._iter_spans(before)}
            new_spans = {path: span for path, span in V2._iter_spans(current)}
            self.assertEqual(old_spans.keys(), new_spans.keys())
            for path, new_span in new_spans.items():
                old_span = old_spans[path]
                for key in ("raw_file", "sha256", "start", "end", "quote", "html_sha256"):
                    if key in old_span or key in new_span:
                        self.assertEqual(new_span.get(key), old_span.get(key), (block_id, path, key))

    def test_bad_nested_span_excludes_entire_block(self):
        original, excluded, pins = V1.build_rakuten_blocks(self.prepared, [self.document])
        corrupted = json.loads(json.dumps(original[0]))
        corrupted["text_runs"][0]["source_ref"]["quote"] = "bad quote"
        with patch.object(V1, "build_rakuten_blocks", return_value=([corrupted], excluded, pins)):
            blocks, failed, _ = V2.build_rakuten_blocks(self.prepared, [self.document])
        self.assertEqual(blocks, [])
        self.assertTrue(any(row.get("exclusion_reason") == "v2_raw_revalidation_failed" for row in failed))

    def test_cli_artifact_writes_pins_receipts_and_refuses_overwrite(self):
        (self.prepared / "documents.jsonl").write_text(json.dumps(self.document, ensure_ascii=False) + "\n",
                                                        encoding="utf-8")
        output = Path(self.temp.name) / "output"
        validation = V2.build_artifact(self.prepared, output)
        self.assertGreater(validation["verified_span_count"], 0)
        for name in ("source-blocks.jsonl", "validation.json", "span-receipts.jsonl", "raw-pins.jsonl", "code"):
            self.assertTrue((output / name).exists())
        with self.assertRaises(FileExistsError):
            V2.build_artifact(self.prepared, output)


if __name__ == "__main__":
    unittest.main()
