from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/composition_source_blocks_v3.py"
SPEC = importlib.util.spec_from_file_location("composition_source_blocks_v3", SCRIPT)
V3 = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = V3
SPEC.loader.exec_module(V3)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FrozenFixture:
    def __init__(self, raw_name="capture.html", html=None):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "raw-captures").mkdir()
        self.raw_name = raw_name
        self.html = html or (
            "<main><h2>Allowed heading</h2><table>"
            "<tr><th>Choice</th></tr>"
            "<tr><td>Variant A</td><td><a href='/other'>Linked option</a></td></tr>"
            "</table><table><tr><td>Separate table value</td></tr></table></main>"
        )
        data = self.html.encode("utf-8")
        (self.root / "raw-captures" / raw_name).write_bytes(data)
        self.sha = digest(data)
        (self.root / "raw-source-registry.jsonl").write_text(json.dumps({
            "raw_file": raw_name, "bundle_file": f"raw-captures/{raw_name}",
            "bytes": len(data), "sha256": self.sha, "original_aliases": []
        }) + "\n", encoding="utf-8")
        self.store = V3.BundleStore(self.root)

    def close(self):
        self.temp.cleanup()

    def html_document(self, text_parts, *, raw_name=None):
        raw_name = raw_name or self.raw_name
        document_text = "\n".join(text_parts)
        segments, offset = [], 0
        for line_no, part in enumerate(text_parts, 1):
            raw_start = self.html.index(part)
            segments.append({"start": offset, "end": offset + len(part), "source_refs": [{
                "raw_file": raw_name, "sha256": self.sha,
                "locator": {"kind": "html_text", "encoding": "utf-8",
                            "path": f"item_desc[line={line_no}]"},
                "start": raw_start, "end": raw_start + len(part), "quote": part
            }]})
            offset += len(part) + 1
        return {"document_id": "fixed-rakuten:test", "document_sha256": "d" * 64,
                "side": "rakuten", "description": {"text": document_text, "segments": segments}}


class CompositionSourceBlocksV3Tests(unittest.TestCase):
    def setUp(self):
        self.fx = FrozenFixture()

    def tearDown(self):
        self.fx.close()

    def test_restores_typed_item_desc_spans_and_preserves_link_table_policy(self):
        doc = self.fx.html_document(["Allowed heading", "Choice", "Variant A",
                                     "Separate table value"])
        blocks, exclusions, receipts, pins, coverage = V3.restore(
            self.fx.root, [doc], {doc["document_id"]}, set())
        self.assertGreaterEqual(len(blocks), 3)
        self.assertTrue(all(b["scope_status"] == "unproved" for b in blocks))
        self.assertTrue(all(r["verified"] for r in receipts))
        self.assertTrue(pins)
        passages = [b["model_passage"] for b in blocks]
        self.assertTrue(any("Variant A" in p and "Choice" in p for p in passages))
        self.assertTrue(all("Linked option" not in p for p in passages))
        separate = next(p for p in passages if "Separate table value" in p)
        self.assertNotIn("Choice", separate)
        self.assertGreater(coverage[doc["document_id"]]["new_block_count"], 0)

    def test_rejects_split_block_when_any_visible_part_lacks_route(self):
        self.fx.close()
        self.fx = FrozenFixture(html="<main><p>Bound text <b>Unbound text</b></p></main>")
        raw_start = self.fx.html.index("Bound text")
        doc = {"document_id": "fixed-rakuten:split", "document_sha256": "e" * 64,
               "side": "rakuten", "description": {"text": "Bound text", "segments": [{
                   "start": 0, "end": 10, "source_refs": [{"raw_file": self.fx.raw_name,
                       "sha256": self.fx.sha, "locator": {"kind": "html_text", "encoding": "utf-8",
                       "path": "item_desc[line=1]"}, "start": raw_start,
                       "end": raw_start + 10, "quote": "Bound text"}]}]}}
        blocks, exclusions, *_ = V3.restore(self.fx.root, [doc], {doc["document_id"]}, set())
        self.assertFalse(blocks)
        self.assertTrue(any("not_fully_bound" in x["exclusion_reason"] for x in exclusions))

    def test_table_run_fallback_preserves_unbound_row_context_as_exclusion(self):
        self.fx.close()
        html = "<table><tr><td>Bound line</td><td>Unbound neighboring cell</td></tr></table>"
        self.fx = FrozenFixture(html=html)
        raw_start = html.index("Bound line")
        doc = {"document_id": "fixed-rakuten:line-span", "document_sha256": "b" * 64,
               "side": "rakuten", "description": {"text": "Bound line", "segments": [{
                   "start": 0, "end": 10, "source_refs": [{"raw_file": self.fx.raw_name,
                       "sha256": self.fx.sha, "locator": {"kind": "html_text", "encoding": "utf-8",
                       "path": "item_desc[line=1]"}, "start": raw_start,
                       "end": raw_start + 10, "quote": "Bound line"}]}]}}
        blocks, exclusions, receipts, *_ = V3.restore(
            self.fx.root, [doc], {doc["document_id"]}, set())
        span_blocks = [b for b in blocks if b["kind"] == "description_text_span"]
        self.assertEqual(len(span_blocks), 0)
        self.assertTrue(any(x.get("exclusion_reason") == "fallback_preserved_original_structure_exclusion"
                            and "fallback_table_context_unbound" in x.get("structure_exclusions", [])
                            for x in exclusions))
        self.assertTrue(any("not_fully_bound" in x["exclusion_reason"] for x in exclusions))
        self.assertTrue(all(r["verified"] for r in receipts))

    def test_rejects_alternative_raw_file_even_when_bytes_match(self):
        alt = self.fx.root / "raw-captures/alternate.html"
        alt.write_bytes(self.fx.html.encode())
        entry = {"raw_file": "alternate.html", "bundle_file": "raw-captures/alternate.html",
                 "bytes": alt.stat().st_size, "sha256": self.fx.sha, "original_aliases": []}
        with (self.fx.root / "raw-source-registry.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
        store = V3.BundleStore(self.fx.root)
        doc = self.fx.html_document(["Variant A"])
        route, refs = next(iter(V3._document_routes(doc)[0].items()))
        html, identity = V3._raw_route_html(store, route)
        original = V3.extract_blocks(html, {**identity, "raw_file": "alternate.html"})[0]
        candidate = V3._as_html_source_refs(original, "utf-8")
        _, failures, _ = V3.literal_refs(candidate, route, refs, doc, store)
        self.assertTrue(any(value.endswith("visible_run_route_identity_mismatch") for value in failures))

    def test_rejects_tampered_frozen_capture(self):
        capture = self.fx.root / "raw-captures" / self.fx.raw_name
        capture.write_bytes(capture.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "size/SHA mismatch"):
            self.fx.store.raw(self.fx.raw_name, self.fx.sha)

    def test_unbound_description_route_is_not_guessed(self):
        doc = {"document_id": "fixed-rakuten:unbound", "document_sha256": "f" * 64,
               "side": "rakuten", "description": {"text": "Variant A", "segments": [{
                   "start": 0, "end": 9, "source_refs": []}]}}
        blocks, exclusions, *_ = V3.restore(self.fx.root, [doc], {doc["document_id"]}, set())
        self.assertFalse(blocks)
        self.assertTrue(any(x["exclusion_reason"] == "no_typed_description_source_route_in_frozen_PREP"
                            for x in exclusions))

    def test_restores_api_video_field_from_explicit_json_leaf(self):
        self.fx.close()
        self.fx = FrozenFixture(raw_name="api.json")
        body = {"itemInfo": {"extraItemCommentWithVideo": "<p>Video detail text</p>"}}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        capture = self.fx.root / "raw-captures/api.json"
        capture.write_bytes(data)
        self.fx.sha = digest(data)
        (self.fx.root / "raw-source-registry.jsonl").write_text(json.dumps({
            "raw_file": "api.json", "bundle_file": "raw-captures/api.json",
            "bytes": len(data), "sha256": self.fx.sha, "original_aliases": []
        }) + "\n", encoding="utf-8")
        self.fx.store = V3.BundleStore(self.fx.root)
        path = "$.itemInfo.extraItemCommentWithVideo"
        doc = {"document_id": "fixed-au:video", "document_sha256": "a" * 64, "side": "au",
               "description": {"text": "Video detail text", "segments": [{"start": 0, "end": 17,
                   "source_refs": [{"raw_file": "api.json", "sha256": self.fx.sha,
                                    "locator": path}]}]}}
        blocks, exclusions, receipts, *_ = V3.restore(
            self.fx.root, [doc], {doc["document_id"]}, set())
        self.assertTrue(blocks)
        self.assertIn(path, blocks[0]["model_passage"])
        self.assertTrue(receipts)
        self.assertTrue(all(x["verified"] for x in receipts))

    def test_api_run_fallback_does_not_detach_unbound_table_cell(self):
        self.fx.close()
        self.fx = FrozenFixture(raw_name="api.json")
        html = "<table><tr><td>Bound API text</td><td>Unbound API text</td></tr></table>"
        path = "$.itemInfo.extraItemCommentWithVideo"
        body = {"itemInfo": {"extraItemCommentWithVideo": html}}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        capture = self.fx.root / "raw-captures/api.json"
        capture.write_bytes(data)
        self.fx.sha = digest(data)
        (self.fx.root / "raw-source-registry.jsonl").write_text(json.dumps({
            "raw_file": "api.json", "bundle_file": "raw-captures/api.json",
            "bytes": len(data), "sha256": self.fx.sha, "original_aliases": []
        }) + "\n", encoding="utf-8")
        store = V3.BundleStore(self.fx.root)
        start = html.index("Bound API text")
        doc = {"document_id": "fixed-au:api-line", "document_sha256": "c" * 64, "side": "au",
               "description": {"text": "Bound API text", "segments": [{"start": 0, "end": 14,
                   "source_refs": [{"raw_file": "api.json", "sha256": self.fx.sha,
                       "locator": {"kind": "json_leaf", "json_path": path},
                       "start": start, "end": start + 14, "quote": "Bound API text"}]}]}}
        blocks, exclusions, receipts, *_ = V3.restore(
            self.fx.root, [doc], {doc["document_id"]}, set())
        spans = [b for b in blocks if b["kind"] == "description_text_span"]
        self.assertEqual(len(spans), 0)
        self.assertTrue(any(x.get("exclusion_reason") == "fallback_preserved_original_structure_exclusion"
                            for x in exclusions))
        self.assertFalse(receipts)

    def test_plain_run_fallback_rejects_anchor_in_original_ancestor_leaf(self):
        self.fx.close()
        self.fx = FrozenFixture(html="<div><p>Bound text</p><a href='/x'>linked sibling</a></div>")
        raw_start = self.fx.html.index("Bound text")
        doc = {"document_id": "fixed-rakuten:ancestor-link", "document_sha256": "9" * 64,
               "side": "rakuten", "description": {"text": "Bound text", "segments": [{
                   "start": 0, "end": 10, "source_refs": [{"raw_file": self.fx.raw_name,
                       "sha256": self.fx.sha, "locator": {"kind": "html_text", "encoding": "utf-8",
                       "path": "item_desc[line=1]"}, "start": raw_start,
                       "end": raw_start + 10, "quote": "Bound text"}]}]}}
        routes, _, _ = V3._document_routes(doc)
        route, _ = next(iter(routes.items()))
        html, identity = V3._raw_route_html(V3.BundleStore(self.fx.root), route)
        source_blocks = V3.extract_blocks(html, identity)
        self.assertEqual(V3._fallback_structure_exclusion(
            route, source_blocks[0], V3.BundleStore(self.fx.root)),
            "fallback_anchor_in_raw_leaf_context_excluded")


if __name__ == "__main__":
    unittest.main()
