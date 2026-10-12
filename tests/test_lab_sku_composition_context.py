"""Contracts for complete source-bound composition model packets."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "composition_context_v1", HERE / "composition_context_v1.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


DOC = "doc-fixed-au"
DOC_SHA = "a" * 64
OPTIONS = ["本体＋ケース 2個", "本体のみ", "セット（限定版）"]


def quote(text, start=0):
    return {"document_id": DOC, "document_sha256": DOC_SHA,
            "field": "description", "start": start,
            "end": start + len(text), "quote": text}


def block(block_id, passage, start, *, text=None, kind="text", links=None,
          table=None, doc=DOC, refs=None):
    text = text if text is not None else passage
    context = {"table": table} if table is not None else {}
    return {"block_id": block_id, "target_document_id": doc,
            "target_document_sha256": DOC_SHA, "kind": kind,
            "html_start": start, "html_end": start + len(passage),
            "model_passage": passage, "support_text": text, "text": text,
            "quote_refs": refs if refs is not None else [quote(text, start)],
            "links": links or [], "context": context}


def shared_unit():
    unit_id = "product-condition:unit-1"
    return {"group_id": unit_id, "kind": "shared", "axis_name": "セット構成",
            "option_values": list(OPTIONS), "target_document_id": DOC,
            "target_selected_axes_text": None,
            "title": "これは決してモデル入力へ渡さないtitle",
            "description": "旧説明全文はpacketへ複製しない",
            "model_request": {"id": unit_id, "choices": [
                {"label": value, "description": "任意"} for value in OPTIONS
            ] + [{"label": M.UNKNOWN_LABEL, "description": "任意"}]}}


def paired_unit():
    unit_id = "generic-description:unit-2"
    axes = "色：スモークグレー\nサイズ：幅100×丈220cm(2枚)"
    source_request = {"request_id": unit_id, "axis_name": "サイズ",
                      "option_values": list(OPTIONS), "selected_value": OPTIONS[0],
                      "target_document_id": DOC, "target_selected_axes_text": axes}
    return {"group_id": unit_id, "kind": "paired", "axis_name": "サイズ",
            "option_values": list(OPTIONS), "target_document_id": DOC,
            "target_selected_axes_text": axes, "source_request": source_request}


class CompositionContextTests(unittest.TestCase):
    def test_packet_keeps_exact_raw_options_and_bound_source_without_title_or_label_leak(self):
        unit = shared_unit()
        source = block("b-1", "商品説明：本体＋ケース 2個を販売。", 31)
        packet = M.build_packet(unit, [source])
        self.assertEqual(packet["unit_id"], unit["group_id"])
        self.assertEqual(packet["kind"], "shared")
        self.assertEqual(packet["axis_name"], unit["axis_name"])
        self.assertEqual(packet["option_values"], OPTIONS)
        self.assertEqual(packet["target_document_id"], DOC)
        self.assertEqual(packet["blocks"][0]["model_passage"], source["model_passage"])
        self.assertEqual(packet["blocks"][0]["quote_refs"], source["quote_refs"])
        serialized = repr(packet)
        self.assertNotIn("title", serialized)
        self.assertNotIn("これは決して", serialized)
        self.assertNotIn("selected_value", serialized)
        self.assertNotIn("model_request", serialized)
        self.assertEqual(packet["packet_sha256"], M._canonical_sha(
            {key: value for key, value in packet.items() if key != "packet_sha256"}))

    def test_all_eligible_blocks_are_ordered_by_source_spans_without_top_k_truncation(self):
        unit = shared_unit()
        sources = [block(f"b-{n}", f"本文{n}", start)
                   for n, start in [(0, 40), (1, 5), (2, 40), (3, 90), (4, 15)]]
        packet = M.build_packet(unit, sources)
        self.assertEqual([b["block_id"] for b in packet["blocks"]],
                         ["b-1", "b-4", "b-0", "b-2", "b-3"])
        self.assertEqual(len(packet["blocks"]), len(sources))
        payload = M.model_payload(packet)
        self.assertEqual(len(payload["blocks"]), len(sources))
        self.assertTrue(all(set(b) == {"block_id", "model_passage"}
                            for b in payload["blocks"]))
        self.assertNotIn("quote_refs", repr(payload))
        self.assertEqual(payload["model_input_sha256"], M._canonical_sha(
            {key: value for key, value in payload.items() if key != "model_input_sha256"}))

    def test_existing_exclusion_predicate_removes_keyword_links_and_linked_table_rows(self):
        unit = shared_unit()
        valid = block("valid", "商品説明：本体1個。", 1)
        keyword = block("keyword", "▼検索ワード\nケース 2個", 2)
        linked = block("linked", "仕様：ケース付属", 3, links=[{"href": "/item/2"}])
        table = block("table", "ケース付属", 4, kind="table_row",
                      table={"source_ref": {"quote": '<table><a href="/x">別商品</a></table>'}})
        self.assertIsNone(__import__("composition_evidence_v1").exclusion_reason(valid))
        self.assertEqual(__import__("composition_evidence_v1").exclusion_reason(keyword),
                         "explicit_keyword_block")
        self.assertEqual(__import__("composition_evidence_v1").exclusion_reason(linked),
                         "block_has_links")
        self.assertEqual(__import__("composition_evidence_v1").exclusion_reason(table),
                         "linked_table_context")
        packet = M.build_packet(unit, [valid, keyword, linked, table])
        self.assertEqual([row["block_id"] for row in packet["blocks"]], ["valid"])

    def test_multi_document_inventory_never_mixes_other_product_and_quote_refs_are_bound(self):
        unit = shared_unit()
        other = block("other", "別商品の説明", 0, doc="another-doc")
        good = block("good", "対象商品の説明", 2)
        packet = M.build_packet(unit, [other, good])
        self.assertEqual([b["block_id"] for b in packet["blocks"]], ["good"])
        bad = block("bad", "対象商品の説明", 2,
                    refs=[{**quote("対象商品の説明"), "document_id": "another-doc"}])
        with self.assertRaisesRegex(ValueError, "target document"):
            M.build_packet(unit, [bad])
        bad_field = block("bad-field", "対象商品の説明", 2,
                          refs=[{**quote("対象商品の説明"), "field": "title"}])
        with self.assertRaisesRegex(ValueError, "description refs"):
            M.build_packet(unit, [bad_field])
        bad_sha = block("bad-sha", "対象商品の説明", 2,
                        refs=[{**quote("対象商品の説明"), "document_sha256": "b" * 64}])
        with self.assertRaisesRegex(ValueError, "document SHA"):
            M.build_packet(unit, [bad_sha])

    def test_paired_packet_carries_exact_axes_but_not_desired_value(self):
        unit = paired_unit()
        packet = M.build_packet(unit, [block("b", "サイズ情報：幅100×丈220cm。", 0)])
        self.assertEqual(packet["target_selected_axes_text"], unit["target_selected_axes_text"])
        self.assertNotIn("selected_value", repr(packet))
        payload = M.model_payload(packet)
        self.assertEqual(payload["target_selected_axes_text"], unit["target_selected_axes_text"])
        mismatch = deepcopy(unit)
        mismatch["source_request"]["target_selected_axes_text"] += "\n別軸：値"
        with self.assertRaisesRegex(ValueError, "axes differ"):
            M.build_packet(mismatch, [block("b", "本文", 0)])
        mismatch = deepcopy(unit)
        mismatch["source_request"]["selected_value"] = "幅220cm"
        with self.assertRaisesRegex(ValueError, "original raw option"):
            M.build_packet(mismatch, [block("b", "本文", 0)])

    def test_mismatched_shared_choices_duplicate_ids_and_empty_unit_fail_closed(self):
        unit = shared_unit()
        mismatch = deepcopy(unit)
        mismatch["model_request"]["choices"][0]["label"] = "本体のみ"
        with self.assertRaisesRegex(ValueError, "options differ"):
            M.build_packet(mismatch, [block("b", "本文", 0)])
        with self.assertRaisesRegex(ValueError, "IDs must be unique"):
            M.build_packet(unit, [block("same", "本文1", 0), block("same", "本文2", 5)])
        self.assertIsNone(M.build_packet(unit, [
            block("excluded", "▼検索ワード\n本体", 0)]))
        with self.assertRaisesRegex(ValueError, "empty packets"):
            M.model_payload({"unit_id": "u", "kind": "shared", "axis_name": "a",
                             "option_values": ["x"], "target_document_id": DOC,
                             "blocks": [], "packet_sha256": M._canonical_sha({
                                 "unit_id": "u", "kind": "shared", "axis_name": "a",
                                 "option_values": ["x"], "target_document_id": DOC,
                                 "blocks": []})})

    def test_packet_and_model_payload_detect_post_freeze_mutation(self):
        packet = M.build_packet(shared_unit(), [block("b", "本文", 0)])
        packet["blocks"][0]["model_passage"] = "差し替えられた本文"
        with self.assertRaisesRegex(ValueError, "packet SHA"):
            M.model_payload(packet)


if __name__ == "__main__":
    unittest.main()
