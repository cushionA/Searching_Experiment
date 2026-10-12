"""Anchor text is retained for audit but excluded from support evidence."""
import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

B = load("anchor_evidence_blocks", "build_scoped_html_blocks_v1.py")
P = load("anchor_evidence_preparation", "prepare_scoped_html_trial_v1.py")


class AnchorEvidence(unittest.TestCase):
    def blocks(self, html):
        source = {"raw_file": "fixture.json", "sha256": "a" * 64,
                  "locator": {"json_path": "$.itemInfo.extraItemComment"}}
        return B.extract_blocks(html, source)

    def document(self, block, text):
        field = block["source_ref"]["locator"]["json_path"]
        return {"document_id": "d", "document_sha256": "b" * 64,
                "description": {"text": text, "segments": [{"start": 0, "end": len(text),
                "source_refs": [{"sha256": "a" * 64, "locator": field}]}]}}

    def test_nested_anchor_and_entity_removed_but_neighbor_and_audit_refs_remain(self):
        html = "<p>前&amp;<a href='/other'><b>別商品</b></a>後</p>"
        block = self.blocks(html)[0]
        self.assertEqual(block["text"], "前&別商品後")
        self.assertEqual(block["support_text"], "前&\n後")
        self.assertEqual(block["support_visible_parts"], ["前", "&", "後"])
        self.assertEqual(block["links"][0]["text"], "別商品")
        self.assertEqual(block["links"][0]["source_ref"]["quote"], "<a href='/other'><b>別商品</b></a>")
        doc = self.document(block, block["text"])
        refs, failures = P.literal_refs(block, doc)
        self.assertFalse(failures)
        self.assertEqual([r["quote"] for r in refs], ["前", "&", "後"])
        rendered = P.render_passage(block)
        self.assertIn("前&", rendered)
        self.assertIn("後", rendered)
        self.assertNotIn("別商品", rendered)
        self.assertNotIn("/other", rendered)

    def test_context_headings_and_table_rows_exclude_anchors_but_keep_multi_value_specs(self):
        html = ("<h2>サイズ<a href='/other'>別商品の案内</a></h2><table>"
                "<caption>構成<a href='/x'>別セット</a></caption>"
                "<tr><th>内容</th><td>幅100cm<a href='/other'>他商品のリンク</a><br>"
                "本体 2枚<br>レース 2枚</td></tr></table>")
        row = next(b for b in self.blocks(html) if b["kind"] == "table_row")
        self.assertEqual(row["support_text"], "内容\n幅100cm\n本体 2枚\nレース 2枚")
        self.assertEqual(row["context"]["headings"][0]["support_text"], "サイズ")
        self.assertEqual(row["context"]["table"]["captions"][0]["support_text"], "構成")
        doc_text = "サイズ\n構成\n" + row["text"]
        doc = self.document(row, doc_text)
        # This fixture models one source segment; citation matching remains literal.
        refs, _ = P.literal_refs(row, doc)
        self.assertIn("本体 2枚", [r["quote"] for r in refs])
        passage = P.render_passage(row)
        self.assertNotIn("他商品のリンク", passage)
        self.assertNotIn("別セット", passage)

    def test_link_only_block_has_no_support_and_legacy_fails_closed(self):
        block = self.blocks("<p><a href='/other'>他商品はこちら</a></p>")[0]
        self.assertEqual(block["support_text"], "")
        refs, failures = P.literal_refs(block, self.document(block, "他商品はこちら"))
        self.assertEqual(refs, [])
        self.assertEqual(failures, ["no_support_text"])
        legacy = {k: v for k, v in block.items() if not k.startswith("support_")}
        with self.assertRaises(ValueError):
            P.render_passage(legacy)
        self.assertEqual(P.literal_refs(legacy, self.document(block, "他商品はこちら")),
                         ([], ["legacy_block_requires_rebuild"]))


if __name__ == "__main__":
    unittest.main()
