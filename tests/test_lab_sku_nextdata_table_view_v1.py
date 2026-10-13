from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENTS))

import build_scoped_html_blocks_v1 as HTML_BLOCKS  # noqa: E402
import sku_nextdata_table_view_v1 as TABLE_VIEW  # noqa: E402


class TableViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source_root = Path(self.tmp.name)
        (self.source_root / "caps").mkdir()

    def make_body(self, name: str, extra: str | None, item_comment: str = ""):
        item = {"itemComment": item_comment}
        if extra is not None:
            item["extraItemComment"] = extra
        raw = json.dumps({"itemInfo": item}, ensure_ascii=False, separators=(",", ":")).encode()
        raw_file = f"caps/{name}.json"
        path = self.source_root / raw_file
        path.write_bytes(raw)
        raw_sha = hashlib.sha256(raw).hexdigest()
        refs = [{"raw_file": raw_file, "sha256": raw_sha, "locator": "$.itemInfo.itemComment"}]
        if extra is not None:
            refs.append({"raw_file": raw_file, "sha256": raw_sha, "locator": "$.itemInfo.extraItemComment"})
        blocks = []
        for field, value in (("itemComment", item_comment), ("extraItemComment", extra)):
            if value is None:
                continue
            src = {"raw_file": raw_file, "sha256": raw_sha,
                   "json_path": f"$.itemInfo.{field}"}
            extracted = HTML_BLOCKS.extract_blocks(value, src)
            for block in extracted:
                if not block.get("support_text"):
                    continue
                blocks.append({"block_id": f"{field}:{block['block_id']}",
                               "field": "native_description", "passage": "",
                               "support_text": block["support_text"],
                               "source_ref": block["source_ref"],
                               "support_text_runs": block["support_text_runs"],
                               "document_sha256": hashlib.sha256(name.encode()).hexdigest(),
                               "links_present": bool(block.get("links"))})
        doc_sha = hashlib.sha256(name.encode()).hexdigest()
        return {"side": "au", "document_id": name, "document_sha256": doc_sha,
                "status": "eligible_blocks" if blocks else "no_eligible_native_blocks",
                "source_document_binding": {"description_refs": refs}, "blocks": blocks}

    def public_body(self, body):
        return {"document_sha256": body["document_sha256"], "status": body["status"],
                "blocks": [{"body_index": i, "block_id": b["block_id"], "field": b["field"],
                            "passage": b["passage"], "support_text": b["support_text"],
                            "source_sha256": b["source_ref"]["sha256"],
                            "source_alias": f"body-source:{body['document_sha256'][:16]}:{i}",
                            "links_present": b["links_present"]}
                           for i, b in enumerate(body["blocks"])]}

    def test_nested_table_filter_preserves_text_and_maps_original_indices(self):
        html = ("<p>lead</p><table><tr><td>outer<table><tr><td>inner</td></tr>"
                "</table>tail</td></tr></table><p>end</p>")
        au = self.make_body("nested", html)
        rakuten = {"side": "rakuten", "document_id": "raku",
                   "document_sha256": hashlib.sha256(b"raku").hexdigest(),
                   "status": "eligible_blocks", "blocks": []}
        payload = {"pair_id": "pair", "context": {
            "eligible_source_bodies": [self.public_body(au), self.public_body(rakuten)],
            "eligible_target_body": self.public_body(au)}}
        sources = {au["document_sha256"]: au, rakuten["document_sha256"]: rakuten}

        out, index_map, audit = TABLE_VIEW.apply_table_view(payload, sources, self.source_root)

        target = out["context"]["eligible_target_body"]
        selected = target["blocks"]
        self.assertEqual(len(selected), 2)
        self.assertTrue(all("/table[" in next(b for b in au["blocks"] if b["block_id"] == x["block_id"])
                            ["source_ref"]["dom_path"] for x in selected))
        self.assertEqual([b["body_index"] for b in selected], [0, 1])
        self.assertEqual([x["source_alias"] for x in selected],
                         [m["source_alias"] for m in index_map[au["document_sha256"]]])
        self.assertEqual([m["original_body_index"] for m in index_map[au["document_sha256"]]],
                         [i for i, b in enumerate(au["blocks"]) if "/table[" in b["source_ref"].get("dom_path", "")
                          and b["source_ref"]["locator"]["json_path"] == "$.itemInfo.extraItemComment"])
        self.assertEqual(out["context"]["eligible_source_bodies"][1], payload["context"]["eligible_source_bodies"][1])
        self.assertEqual(audit["au_document_count"], 1)
        self.assertEqual(audit["au_documents_with_tables"], 1)
        doc_audit = audit["documents"][au["document_sha256"]]
        self.assertEqual(doc_audit["table_count"], 2)
        self.assertEqual(doc_audit["tables"][1]["ancestor_table_ordinals_outer_to_self"], [1, 2])
        self.assertEqual(payload["context"]["eligible_target_body"]["blocks"], self.public_body(au)["blocks"])

    def test_missing_table_uses_no_fallback_and_empty_body_status(self):
        au = self.make_body("no-table", "<p>plain description</p>",
                            item_comment="<table><tr><td>must not leak</td></tr></table>")
        payload = {"context": {"eligible_source_bodies": [],
                               "eligible_target_body": self.public_body(au)}}
        out, index_map, audit = TABLE_VIEW.apply_table_view(
            payload, {au["document_sha256"]: au}, self.source_root)
        target = out["context"]["eligible_target_body"]
        self.assertEqual(target["blocks"], [])
        self.assertEqual(target["status"], "no_eligible_native_blocks")
        self.assertEqual(index_map[au["document_sha256"]], [])
        self.assertEqual(audit["au_documents_without_tables"], 1)
        self.assertEqual(audit["documents"][au["document_sha256"]]["table_count"], 0)

    def test_bad_raw_capture_or_span_fails_closed(self):
        au = self.make_body("bad-source", "<table><tr><td>value</td></tr></table>")
        selected = next(b for b in au["blocks"] if b["source_ref"]["locator"]["json_path"]
                        == "$.itemInfo.extraItemComment")
        broken = json.loads(json.dumps(au))
        block = next(b for b in broken["blocks"] if b["block_id"] == selected["block_id"])
        block["source_ref"]["quote"] = "tampered"
        with self.assertRaises(ValueError):
            TABLE_VIEW.table_block_ids(broken, self.source_root)

        source_ref = au["source_document_binding"]["description_refs"][0]
        source_ref["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            TABLE_VIEW.table_block_ids(au, self.source_root)


if __name__ == "__main__":
    unittest.main()
