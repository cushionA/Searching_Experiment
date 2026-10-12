"""Contracts for auxiliary original-title composition packets and citations."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "composition_title_context_v1", HERE / "composition_title_context_v1.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

DOC, DOC_SHA = "doc-au-1", "d" * 64
TITLE = "【選べる17サイズ】カーテン 2枚セット 検索語 遮光"
BODY = "対象仕様：幅100×丈220cmを2枚。"
OPTIONS = ["幅100×丈220cm(2枚)", "幅150×丈200cm(1枚)"]


def span(quote, *, start=0, locator=None, raw_file="raw/title.json", sha="a" * 64):
    return {"raw_file": raw_file, "sha256": sha,
            "locator": locator or {"kind": "json_leaf", "json_path": "$.item.title"},
            "start": start, "end": start + len(quote), "quote": quote}


class FakeStore:
    def __init__(self, expected=None):
        self.expected = span(TITLE) if expected is None else expected

    def verify(self, value):
        return value == self.expected


def unit(kind="shared"):
    unit_id = "group-abc" if kind == "shared" else "request-abc"
    result = {"group_id": unit_id, "kind": kind, "axis_name": "サイズ",
              "option_values": list(OPTIONS), "target_document_id": DOC,
              "target_selected_axes_text": None}
    if kind == "shared":
        result["model_request"] = {"id": unit_id,
             "choices": [{"label": x, "description": ""} for x in OPTIONS + [M.CONTEXT.UNKNOWN_LABEL]]}
    else:
        axes = "カラー：ネイビー\nサイズ：幅100×丈220cm(2枚)"
        result["target_selected_axes_text"] = axes
        result["source_request"] = {"request_id": unit_id, "target_document_id": DOC,
            "axis_name": "サイズ", "option_values": list(OPTIONS),
            "selected_value": OPTIONS[0], "target_selected_axes_text": axes}
    return result


def document(doc_id=DOC, *, refs=None, title=TITLE):
    return {"document_id": doc_id, "document_sha256": DOC_SHA,
            "title": {"text": title, "source_refs": [span(title)] if refs is None else refs},
            "description": {"text": BODY}}


def body_block():
    return {"block_id": "body-original", "target_document_id": DOC,
            "target_document_sha256": DOC_SHA, "kind": "text",
            "html_start": 0, "html_end": len(BODY), "support_text": BODY,
            "model_passage": BODY,
            "quote_refs": [{"document_id": DOC, "document_sha256": DOC_SHA,
                 "field": "description", "start": 0, "end": len(BODY), "quote": BODY}]}


def build(unit_value=None, blocks=None, doc=None, store=None):
    return M.build_packet(unit_value or unit(), [body_block()] if blocks is None else blocks,
                          doc or document(), FakeStore() if store is None else store)


def title_id(packet):
    return next(b["block_id"] for b in packet["blocks"] if b.get("evidence_role") == "title")


def response(packet, *, winner=OPTIONS[0], ids=None):
    return {"unit_id": packet["unit_id"], "winning_value": winner,
            "evidence_block_ids": [title_id(packet)] if ids is None and winner is not None else (ids or []),
            "reason": "原文を候補と照合"}


class CompositionTitleContextTests(unittest.TestCase):
    def setUp(self):
        self.verify_patch = patch.object(M.SOURCES.SOURCES, "verify_span",
                                         side_effect=lambda store, ref: store.verify(ref))
        self.verify_patch.start()
        self.addCleanup(self.verify_patch.stop)

    def test_full_body_and_verified_title_are_separate_and_payload_hides_locator_quotes(self):
        source = body_block()
        packet = build(blocks=[source])
        self.assertEqual(packet["blocks"][0], {k: source[k] for k in ("block_id", "model_passage", "quote_refs")})
        title = next(b for b in packet["blocks"] if b.get("evidence_role") == "title")
        self.assertEqual(title["model_passage"], TITLE)
        self.assertEqual(title["quote_refs"][0]["field"], "title")
        self.assertEqual(title["block_id"], title_id(packet))
        payload = M.model_payload(packet)
        self.assertEqual(len(payload["blocks"]), 2)
        self.assertEqual([b["evidence_role"] for b in payload["blocks"]], ["description", "title"])
        self.assertNotIn("quote_refs", repr(payload))
        self.assertNotIn("locator", repr(payload))
        self.assertNotIn('"title":', repr(payload))
        self.assertIn("選べる", M.PROTOCOL)
        self.assertIn("検索語", M.PROTOCOL)
        self.assertIn("selected SKU proofなし", M.PROTOCOL)
        for inherited in ("他のファイル", "外部サイト", "ラベル", "確率", "価格", "在庫", "画面UI"):
            self.assertIn(inherited, M.PROTOCOL)

    def test_unbound_or_tampered_title_is_omitted_but_body_packet_survives(self):
        malformed = [
            span("別のタイトル"),
            {**span(TITLE), "sha256": "c" * 64},
            {**span(TITLE), "locator": {"kind": "unknown", "path": "x"}},
            {**span(TITLE), "start": 1, "end": len(TITLE) + 1},
        ]
        for bad in malformed:
            with self.subTest(bad=bad):
                packet = build(doc=document(refs=[bad]))
                self.assertEqual([b.get("evidence_role", "description") for b in packet["blocks"]],
                                 ["description"])
        unbound = build(doc=document(refs=[]))
        self.assertEqual(len(unbound["blocks"]), 1)
        self.assertEqual(unbound["blocks"][0]["block_id"], "body-original")

    def test_title_only_packet_needs_verified_original_title_span(self):
        packet = build(blocks=[])
        self.assertIsNotNone(packet)
        self.assertEqual([b["evidence_role"] for b in packet["blocks"]], ["title"])
        self.assertIsNone(build(blocks=[], doc=document(refs=[])))

    def test_title_citation_is_returned_as_title_and_remains_unproved(self):
        packet = build()
        result = M.validate_response(packet, response(packet), None,
                                     {DOC: document()})
        self.assertEqual(result["status"], "support")
        self.assertEqual(result["quote_refs"][0]["field"], "title")
        self.assertEqual(result["quote_refs"][0]["quote"], TITLE)
        self.assertEqual(result["scope_status"], "unproved")
        self.assertFalse(result["production_adoption_allowed"])
        self.assertEqual(result["source_model_response"], response(packet))

    def test_nonzero_raw_html_offset_maps_to_document_title_quote_and_retains_raw_span(self):
        raw = span(TITLE, start=892, locator={"kind": "html_text", "encoding": "utf-8",
                                              "path": "html/head/title"},
                   raw_file="raw/item.html", sha="e" * 64)
        doc = document(refs=[raw])
        packet = build(blocks=[], doc=doc, store=FakeStore(raw))
        title_block = packet["blocks"][0]
        title_ref = title_block["quote_refs"][0]
        self.assertEqual((title_ref["field"], title_ref["start"], title_ref["end"], title_ref["quote"]),
                         ("title", 0, len(TITLE), TITLE))
        self.assertEqual(title_block["raw_title_source_refs"], [raw])
        self.assertEqual(title_block["raw_title_source_refs"][0]["start"], 892)
        self.assertTrue(M.LM.GATE.verify_quote(title_ref, {"target_document_id": DOC}, {DOC: doc}))
        self.assertNotIn("raw_title_source_refs", M.model_payload(packet)["blocks"][0])

    def test_multi_sku_title_is_not_automatically_selected_sku_proof(self):
        packet = build(blocks=[])
        self.assertIn("選べる17サイズ", packet["blocks"][0]["model_passage"])
        self.assertIn("選べる", M.PROTOCOL)
        self.assertIn("null", M.PROTOCOL)
        result = M.validate_response(packet, response(packet, winner=None, ids=[]), None, {DOC: document()})
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["winning_value"])
        self.assertEqual(result["quote_refs"], [])

    def test_target_document_mismatch_and_invalid_title_quote_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "document target"):
            build(doc=document("other"))
        packet = build()
        bad_docs = {DOC: {**document(), "title": {"text": "改変", "source_refs": []}}}
        with self.assertRaisesRegex(ValueError, "invalid current-document quote"):
            M.validate_response(packet, response(packet), None, bad_docs)

    def test_shared_context_never_leaks_selected_axes_and_rejects_axes_citation(self):
        packet = build()
        payload = M.model_payload(packet)
        self.assertNotIn("target_selected_axes_text", payload)
        self.assertNotIn("selected_value", repr(payload))
        self.assertNotIn("title", {k for k in payload if k == "title"})
        bad = deepcopy(packet)
        bad["blocks"][0]["quote_refs"].append({"document_id": DOC, "field": "selected_axes",
            "start": 0, "end": 1, "quote": "x"})
        bad["packet_sha256"] = M._canonical_sha({k:v for k,v in bad.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(ValueError, "selected-axis"):
            M.validate_response(bad, response(bad, ids=["body-original"]), None, {DOC: document()})

    def test_paired_axes_and_raw_options_are_exact_and_uncut(self):
        u = unit("paired")
        packet = build(u, blocks=[])
        payload = M.model_payload(packet)
        self.assertEqual(payload["option_values"], OPTIONS)
        self.assertEqual(payload["target_selected_axes_text"], u["target_selected_axes_text"])
        result = M.validate_response(packet, response(packet), u["source_request"], {DOC: document()})
        self.assertEqual(result["quote_refs"][-1]["field"], "selected_axes")
        wrong = deepcopy(u["source_request"])
        wrong["target_selected_axes_text"] += "\n別軸：値"
        with self.assertRaisesRegex(ValueError, "axes differ"):
            M.validate_response(packet, response(packet), wrong, {DOC: document()})

    def test_null_with_citation_and_unknown_block_ids_are_rejected(self):
        packet = build()
        with self.assertRaisesRegex(ValueError, "null winner"):
            M.validate_response(packet, response(packet, winner=None, ids=[title_id(packet)]), None, {DOC: document()})
        with self.assertRaisesRegex(ValueError, "outside the packet"):
            M.validate_response(packet, response(packet, ids=["not-bound"]), None, {DOC: document()})

    def test_frozen_packet_detects_passage_mutation_and_does_not_truncate_raw_values(self):
        packet = build()
        payload = M.model_payload(packet)
        self.assertEqual(payload["option_values"], OPTIONS)
        self.assertTrue(all(value in repr(payload) for value in OPTIONS))
        packet["blocks"][-1]["model_passage"] += "改変"
        with self.assertRaisesRegex(ValueError, "packet SHA"):
            M.model_payload(packet)


if __name__ == "__main__":
    unittest.main()
