"""Fail-closed validation tests for source-grounded composition LLM replies."""
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("composition_language_model_v1", HERE / "composition_language_model_v1.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def fixture(kind="shared"):
    passage = "本体1個。ケース付属。"
    quote = "本体1個"
    block = {"block_id": "block-1", "model_passage": passage,
             "quote_refs": [{"document_id": "doc-1", "field": "description", "start": 0,
                             "end": len(quote), "quote": quote}]}
    packet = {"unit_id": "unit-1", "kind": kind, "axis_name": "セット内容",
              "option_values": ["本体＋ケース 2個", "本体のみ"],
              "target_document_id": "doc-1", "blocks": [block]}
    request = None
    if kind == "paired":
        axes = "色：赤\n容量：500ml"
        packet["target_selected_axes_text"] = axes
        request = {"target_document_id": "doc-1", "target_selected_axes_text": axes}
    documents = {"doc-1": {"description": {"text": "本体1個。ケース付属。"}}}
    response = {"unit_id": "unit-1", "winning_value": "本体＋ケース 2個",
                "evidence_block_ids": ["block-1"], "reason": "記載に一致"}
    return packet, response, request, documents


class CompositionLanguageModelTests(unittest.TestCase):
    def test_exact_raw_winner_requires_and_returns_verified_current_quote(self):
        packet, response, request, docs = fixture()
        result = M.validate_response(packet, response, request, docs)
        self.assertEqual(result["status"], "support")
        self.assertEqual(result["winning_value"], "本体＋ケース 2個")
        self.assertEqual(result["quote_refs"], packet["blocks"][0]["quote_refs"])
        self.assertEqual(result["scope_status"], "unproved")
        self.assertFalse(result["production_adoption_allowed"])

    def test_partial_or_altered_winner_is_rejected(self):
        packet, response, request, docs = fixture()
        response["winning_value"] = "本体"
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            M.validate_response(packet, response, request, docs)

    def test_foreign_evidence_block_and_nonliteral_quote_are_rejected(self):
        packet, response, request, docs = fixture()
        response["evidence_block_ids"] = ["foreign"]
        with self.assertRaisesRegex(ValueError, "outside the packet"):
            M.validate_response(packet, response, request, docs)

        packet, response, request, docs = fixture()
        packet["blocks"][0]["quote_refs"][0]["quote"] = "ケース付属"
        with self.assertRaisesRegex(ValueError, "invalid current-document quote"):
            M.validate_response(packet, response, request, docs)

    def test_null_winner_is_unknown_without_citations(self):
        packet, response, request, docs = fixture()
        response.update(winning_value=None, evidence_block_ids=[])
        result = M.validate_response(packet, response, request, docs)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["winning_value"])
        self.assertEqual(result["quote_refs"], [])
        response["evidence_block_ids"] = ["block-1"]
        with self.assertRaisesRegex(ValueError, "null winner"):
            M.validate_response(packet, response, request, docs)

    def test_shared_packet_rejects_selected_axes(self):
        packet, response, request, docs = fixture()
        packet["target_selected_axes_text"] = "色：赤"
        with self.assertRaisesRegex(ValueError, "shared packet"):
            M.validate_response(packet, response, request, docs)

    def test_paired_response_requires_original_axes_and_adds_verified_axis_ref(self):
        packet, response, request, docs = fixture("paired")
        result = M.validate_response(packet, response, request, docs)
        self.assertEqual(result["quote_refs"][-1], {
            "document_id": "doc-1", "field": "selected_axes", "start": 0,
            "end": len(packet["target_selected_axes_text"]),
            "quote": packet["target_selected_axes_text"],
        })
        wrong_request = {**request, "target_selected_axes_text": "色：青"}
        with self.assertRaisesRegex(ValueError, "differ from the original request"):
            M.validate_response(packet, response, wrong_request, docs)


if __name__ == "__main__":
    unittest.main()
