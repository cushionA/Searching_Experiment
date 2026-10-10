import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import prepare_generic_html_scope_packets_v1 as exporter


class GenericHtmlScopePacketTests(unittest.TestCase):
    HTML = ('<h2>商品 &amp; 注意</h2>\n<p>購入前にご確認ください。返品不可。</p>'
            '<p>こちらは2段カバーなしタイプのページです。</p>'
            '<div><a href="/one?a=1&amp;b=2">2段カバー付き</a>'
            '<a href="/two">2段カバーなし</a><a href="/three">3段カバー付き</a>'
            '<a href="/four">3段カバーなし</a></div>'
            '<table><tr><th>内容</th><td>100本セット &lt;固定&gt;'
            '<a href="/other">別商品のセット</a></td></tr></table>'
            '<ul><li>ペットカート <img alt="赤 &amp; 青" src="/image"></li></ul>'
            '<iframe src="https://example.invalid/frame"></iframe>'
            '<script>hidden &amp; data</script><!-- preserved -->')

    def fixture(self, root):
        raw_path = root / "raw.json"
        raw = {"itemInfo": {"itemComment": self.HTML, "plainComment": "購入時の連絡。ペットカート。",
                            "itemTitle": "固定AU &amp; タイトル"}}
        raw_path.write_text(json.dumps(raw, ensure_ascii=True), encoding="utf-8")
        source_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        source = {"raw_file": "raw.json", "sha256": source_sha,
                  "locator": {"kind": "json_leaf", "json_path": "$.itemInfo.itemComment"}}
        plain_source = {**source, "locator": "$.itemInfo.plainComment"}
        title_source = {**source, "locator": {"kind": "json_leaf", "json_path": "$.itemInfo.itemTitle"},
                        "start": 0, "end": len(raw["itemInfo"]["itemTitle"]), "quote": raw["itemInfo"]["itemTitle"]}
        row = {"row_key": "au:100:0", "axes": [{"axis_name": "セット", "value": "１００本 セット(赤＆青)"}]}
        au = {"product_id": "100", "title": raw["itemInfo"]["itemTitle"], "title_source": title_source,
              "rows": [row, {"row_key": "au:100:1", "axes": []}],
              "descriptions": [{"text": "購入前にご確認ください。返品不可。", "source_refs": [source]},
                               {"text": "2段カバー付き", "source_refs": [source]},
                               {"text": raw["itemInfo"]["plainComment"], "source_ref": plain_source}],
              "iframe_urls": ["https://example.invalid/frame"]}
        document = {"document_id": "fixed-au:d1:100", "dossier_id": "d1", "au_product_id": "100", "au": au,
                    "description_document": {"text": "legacy stripped text", "windows": [{"text": "legacy"}]}}
        task = {"task_id": "t1", "dossier_id": "d1", "au_product_id": "100", "au_row_key": row["row_key"],
                "fixed_au_product_ref": {"document_id": document["document_id"], "dossier_id": "d1", "product_id": "100"},
                "selected_au_row": copy.deepcopy(row), "selected_value": "１００本 セット(赤＆青)",
                "option_values": ["１００本 セット(赤＆青)", "", "別セット", "別セット"]}
        input_dir = root / "input"
        input_dir.mkdir()
        # Deliberate spacing/newline layout proves byte preservation.
        (input_dir / "documents.jsonl").write_text(json.dumps(document, ensure_ascii=False) + "\n\n", encoding="utf-8")
        (input_dir / "tasks.jsonl").write_text(json.dumps(task, ensure_ascii=False) + "\n", encoding="utf-8")
        (input_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
        return input_dir, document, task

    def test_full_fields_preserve_purchase_cart_links_table_and_plain_title(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir, document, task = self.fixture(root)
            manifest = exporter.prepare(input_dir, root / "out", root)
            packet = exporter.read_jsonl(root / "out/packets.jsonl")[0]
            field = packet["fields"][0]
            self.assertEqual(field["raw_html"], self.HTML)
            self.assertIn("購入前にご確認ください。返品不可。", field["text"])
            self.assertIn("ペットカート", field["text"])
            self.assertIn("100本セット <固定>", field["text"])
            self.assertNotIn("hidden", field["text"])
            self.assertIn("hidden &amp; data", field["raw_html"])
            self.assertNotIn("赤 & 青", field["text"])
            images = [b for b in field["blocks"] if b["role"] == "image"]
            attribute = images[0]["derived_attributes"][0]
            self.assertEqual(attribute["name"], "alt")
            self.assertEqual(attribute["text"], "赤 & 青")
            self.assertEqual(attribute["text_kind"], "derived_decoded_html_attribute")
            self.assertEqual(attribute["span_scope"], "whole_start_tag_not_attribute_value")
            title = next(f for f in packet["fields"] if f["field_kind"] == "plain_title")
            self.assertEqual(title["text"], "固定AU &amp; タイトル")
            self.assertEqual(len(title["blocks"]), 1)
            self.assertEqual(field["old_description_text_refs"][0]["text"], document["au"]["descriptions"][0]["text"])
            self.assertEqual(len(field["old_description_text_refs"]), 2)
            self.assertEqual(manifest["source_file_count"], 1)
            self.assertEqual(manifest["field_count"], 3)
            self.assertEqual(manifest["external_fetches"], 0)
            self.assertFalse(manifest["content_filters"])
            self.assertFalse(manifest["condition_normalization"])
            for name in ("documents.jsonl", "tasks.jsonl"):
                self.assertEqual((input_dir / name).read_bytes(), (root / "out" / name).read_bytes())
            self.assertEqual(exporter.read_jsonl(root / "out/tasks.jsonl")[0], task)

    def test_hyperlink_siblings_and_table_relationships_remain_structural(self):
        parser = exporter.ScopeParser(self.HTML, "field", {"locator": {"json_path": "$.leaf"}})
        result = parser.finish()
        blocks = {b["block_id"]: b for b in result["blocks"]}
        links = [b for b in result["blocks"] if b["role"] == "link"]
        self.assertEqual([b["text"] for b in links[:4]], ["2段カバー付き", "2段カバーなし", "3段カバー付き", "3段カバーなし"])
        self.assertEqual(len({b["parent_block_id"] for b in links[:4]}), 1)
        self.assertEqual(links[0]["derived_attributes"][0]["text"], "/one?a=1&b=2")
        self.assertIsNotNone(links[0]["context"]["preceding_heading_block_id"])
        table_link = links[4]
        self.assertEqual(len(table_link["context"]["table_row_block_ids"]), 1)
        cell = blocks[table_link["context"]["table_cell_block_ids"][0]]
        row = blocks[table_link["context"]["table_row_block_ids"][0]]
        self.assertEqual(cell["tag"], "td")
        self.assertIn("100本セット <固定>", cell["text"])
        self.assertEqual([blocks[key]["tag"] for key in row["child_block_ids"]], ["th", "td"])
        self.assertEqual(cell["parent_block_id"], row["block_id"])

    def test_unicode_and_entity_spans_are_decoded_field_offsets_not_raw_json(self):
        raw = "見出し\n<p>赤 &amp; 青 &#x1f431; &#65; &amp 通販</p>"
        result = exporter.ScopeParser(raw, "leaf", {"locator": {"json_path": "$.leaf"}}).finish()
        self.assertEqual(result["text"], "見出し\n赤 & 青 🐱 A & 通販")
        coverage = set()
        for token in result["tokens"]:
            span = token["source_html_span"]
            self.assertEqual(span["raw_html"], raw[span["start"]:span["end"]])
            self.assertEqual(span["offset_basis"], "json_decoded_source_field_string")
            self.assertEqual(span["offset_space"], "leaf")
            coverage.update(range(span["start"], span["end"]))
        self.assertEqual(coverage, set(range(len(raw))))
        expanded = next(t for t in result["tokens"] if t["text"] == "🐱")
        self.assertEqual(expanded["source_html_span"]["raw_html"], "&#x1f431;")
        self.assertNotEqual(expanded["text"], expanded["source_html_span"]["raw_html"])
        for block in result["blocks"]:
            span = block["source_html_span"]
            self.assertEqual(span["raw_html"], raw[span["start"]:span["end"]])

    def test_malformed_tail_and_unclosed_element_retain_full_raw_field(self):
        raw = "<div>購入 ペットカート <unfinished"
        result = exporter.ScopeParser(raw, "leaf", {"locator": "$.leaf"}).finish()
        self.assertEqual(result["raw_html"], raw)
        self.assertTrue(result["coverage"]["raw_coverage_complete"])
        coverage = set()
        for token in result["tokens"]:
            span = token["source_html_span"]
            coverage.update(range(span["start"], span["end"]))
        self.assertEqual(coverage, set(range(len(raw))))
        div = next(b for b in result["blocks"] if b["tag"] == "div")
        self.assertEqual(div["closure"], "end_of_field")
        self.assertEqual(div["source_html_span"]["end"], len(raw))

    def test_source_sha_mismatch_and_existing_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir, _, _ = self.fixture(root)
            before = (input_dir / "documents.jsonl").read_bytes()
            raw_path = root / "raw.json"
            raw_path.write_text(raw_path.read_text() + " ", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                exporter.prepare(input_dir, root / "out", root)
            self.assertFalse((root / "out").exists())
            self.assertEqual(before, (input_dir / "documents.jsonl").read_bytes())
            (root / "out").mkdir()
            with self.assertRaises(FileExistsError):
                exporter.prepare(input_dir, root / "out", root)

    def test_selected_fixed_au_row_and_product_must_match(self):
        for field in ("selected_au_row", "au_product_id"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                input_dir, _, task = self.fixture(root)
                task[field] = {"row_key": "au:100:1", "axes": []} if field == "selected_au_row" else "200"
                (input_dir / "tasks.jsonl").write_text(json.dumps(task) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "AU identity or full row mismatch"):
                    exporter.prepare(input_dir, root / "out", root)
                self.assertFalse((root / "out").exists())

    def test_json_leaf_paths_are_explicit_and_string_only(self):
        self.assertEqual(exporter.json_leaf({"a": [{"x.y": "literal"}]}, '$.a[0]["x.y"]'), "literal")
        with self.assertRaises(ValueError):
            exporter.json_leaf({"a": 1}, "$.a")
        with self.assertRaises(ValueError):
            exporter.json_leaf({"a": "literal"}, "$.a[*]")


if __name__ == "__main__":
    unittest.main()
