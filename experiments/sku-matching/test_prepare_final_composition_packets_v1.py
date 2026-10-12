from __future__ import annotations

import unittest

import composition_context_v1 as CONTEXT
import composition_evidence_v1 as EVIDENCE
import prepare_final_composition_packets_v1 as PREP


class FinalCompositionPacketTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "direction": "rakuten_to_au",
            "axis_name": "色",
            "option_values": ["白", "黒"],
            "target_document_sha256": "a" * 64,
            "target_selected_axes": [{"axis_name": "型", "value": "A"}],
        }

    def test_binding_value_does_not_change_exact_context_key(self):
        first = {**self.request, "selected_value": "白"}
        second = {**self.request, "selected_value": "黒"}
        self.assertEqual(PREP.cache_key(first), PREP.cache_key(second))

    def test_ordered_options_and_exact_axes_are_part_of_context_key(self):
        self.assertNotEqual(PREP.cache_key(self.request),
                            PREP.cache_key({**self.request, "option_values": ["黒", "白"]}))
        self.assertNotEqual(PREP.cache_key(self.request),
                            PREP.cache_key({**self.request,
                                            "target_selected_axes": [{"axis_name": "型", "value": "B"}]}))

    def test_packet_keeps_quote_refs_while_compact_payload_omits_them_and_binding(self):
        unit = {
            "group_id": "cache:example", "kind": "paired", "axis_name": "色",
            "option_values": ["白", "黒"], "target_document_id": "doc:1",
            "target_selected_axes_text": "型：A",
            "source_request": {"request_id": "cache:example", "axis_name": "色",
                               "option_values": ["白", "黒"], "target_document_id": "doc:1",
                               "target_selected_axes_text": "型：A", "selected_value": "白"},
        }
        block = {"target_document_id": "doc:1", "target_document_sha256": "b" * 64,
                 "block_id": "block:1", "support_text": "セット内容 白", "text": "セット内容 白",
                 "links": [], "kind": "text", "html_start": 0, "html_end": 10,
                 "model_passage": "セット内容 白", "quote_refs": [{"document_id": "doc:1",
                     "document_sha256": "b" * 64, "field": "description", "start": 0,
                     "end": 6, "quote": "セット内容"}]}
        packet = CONTEXT.build_packet(unit, [block])
        payload = CONTEXT.model_payload(packet)
        self.assertEqual(packet["blocks"][0]["quote_refs"], block["quote_refs"])
        self.assertNotIn("selected_value", packet)
        self.assertNotIn("selected_value", payload)
        self.assertNotIn("title", payload)
        self.assertNotIn("quote_refs", payload["blocks"][0])

    def test_original_block_exclusions_remain_unchanged(self):
        self.assertEqual(EVIDENCE.exclusion_reason({"links": ["x"]}), "block_has_links")
        self.assertEqual(EVIDENCE.exclusion_reason({"kind": "table_row", "context": {}}),
                         "unbound_table_context")
        self.assertEqual(EVIDENCE.exclusion_reason({"kind": "table_row", "context": {
            "table": {"source_ref": {"quote": "<a href='x'>linked</a>"}}}}),
                         "linked_table_context")
        self.assertEqual(EVIDENCE.exclusion_reason({"text": "キーワード：青"}),
                         "explicit_keyword_block")


if __name__ == "__main__":
    unittest.main()
