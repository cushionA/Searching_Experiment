"""Contracts for per-original-request scoped composition units."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "composition_scoped_units_v1", HERE / "composition_scoped_units_v1.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


DOC = "fixed-au:example"
DOC_SHA = "d" * 64
OPTIONS = ["A＋付属品 2個", "Aのみ", "セット（限定版）"]


def request(rid="generic-description:req-1", *, axes="色：青\nサイズ：M",
            selected=OPTIONS[0], case="case-one", extra=None):
    value = {
        "request_id": rid,
        "axis_name": "構成",
        "option_values": list(OPTIONS),
        "selected_value": selected,
        "target_document_id": DOC,
        "target_document_sha256": DOC_SHA,
        "target_selected_axes_text": axes,
        "direction": "rakuten_to_au",
        "origin": "one_sided",
        "case_bindings": [{"case_id": case, "axis_key": "構成"}],
        "source_model": "must-not-enter-model-input",
    }
    if extra:
        value.update(extra)
    return value


def source_block(block_id="b1", text="内容：A＋付属品 2個。", start=1):
    return {
        "block_id": block_id,
        "target_document_id": DOC,
        "target_document_sha256": DOC_SHA,
        "kind": "text",
        "html_start": start,
        "html_end": start + len(text),
        "model_passage": text,
        "support_text": text,
        "text": text,
        "quote_refs": [{
            "document_id": DOC, "document_sha256": DOC_SHA,
            "field": "description", "start": start,
            "end": start + len(text), "quote": text,
        }],
        "links": [],
        "context": {},
    }


class CompositionScopedUnitsTests(unittest.TestCase):
    def test_one_request_gets_one_distinct_paired_unit_with_exact_bindings(self):
        original = request()
        before = deepcopy(original)
        units = M.build_units([original], set())
        unit = units[original["request_id"]]
        self.assertEqual(unit["kind"], "paired")
        self.assertEqual(unit["group_id"], original["request_id"])
        self.assertEqual(unit["source_request"], original)
        self.assertEqual(unit["axis_name"], original["axis_name"])
        self.assertEqual(unit["option_values"], OPTIONS)
        self.assertEqual(unit["target_document_id"], DOC)
        self.assertEqual(unit["target_selected_axes_text"], original["target_selected_axes_text"])
        self.assertEqual(original, before)

    def test_different_original_requests_are_never_shared_or_fanned_out(self):
        left = request("generic-description:left", case="case-one")
        right = request("generic-description:right", case="case-two",
                        axes="色：赤\nサイズ：L", selected=OPTIONS[1])
        units = M.build_units([left, right], set())
        self.assertEqual(set(units), {left["request_id"], right["request_id"]})
        self.assertIsNot(units[left["request_id"]], units[right["request_id"]])
        self.assertNotEqual(units[left["request_id"]]["group_id"],
                            units[right["request_id"]]["group_id"])
        self.assertEqual(units[right["request_id"]]["source_request"]["case_bindings"],
                         right["case_bindings"])

    def test_paired_packet_and_payload_use_exact_axes_and_raw_options_without_desired_value(self):
        req = request(extra={"request_labels": ["label-must-not-enter-payload"]})
        unit = M.build_units([req], set())[req["request_id"]]
        packet = M.build_packet(unit, [source_block()])
        payload = M.model_payload(packet)
        self.assertEqual(payload["target_selected_axes_text"], req["target_selected_axes_text"])
        self.assertEqual(payload["option_values"], OPTIONS)
        self.assertEqual(payload["unit_id"], req["request_id"])
        serialized = repr(payload)
        self.assertNotIn("selected_value", payload)
        for forbidden in ("one_sided", "source_model",
                          "case_bindings", "label-must-not-enter-payload"):
            self.assertNotIn(forbidden, serialized)

    def test_axis_free_request_is_global_singleton_without_desired_value_or_request_binding(self):
        req = request("generic-description:global", axes=None, selected=OPTIONS[2])
        unit = M.build_units([req], set())[req["request_id"]]
        self.assertEqual(unit["kind"], "shared")
        self.assertEqual(unit["group_id"], req["request_id"])
        self.assertNotIn("source_request", unit)
        self.assertIsNone(unit["target_selected_axes_text"])
        self.assertNotIn("selected_value", unit["model_request"])
        self.assertEqual([x["label"] for x in unit["model_request"]["choices"]],
                         OPTIONS + [M.UNKNOWN_LABEL])
        payload = M.model_payload(M.build_packet(unit, [source_block()]))
        self.assertNotIn("target_selected_axes_text", payload)
        self.assertNotIn("selected_value", repr(payload))
        self.assertNotIn("case_bindings", repr(payload))

    def test_proof_request_is_excluded_for_unchanged_typed_proof_path(self):
        proof = request("generic-description:typed-proof")
        regular = request("generic-description:regular")
        units = M.build_units([proof, regular], {proof["request_id"]})
        self.assertEqual(list(units), [regular["request_id"]])
        self.assertNotIn(proof["request_id"], units)

    def test_coverage_matches_requests_minus_proof_and_repeated_ids_fail_closed(self):
        requests = [request(f"generic-description:{i}") for i in range(134)]
        proof_ids = {"generic-description:proof"}
        requests[0]["request_id"] = "generic-description:proof"
        self.assertEqual(len(M.build_units(requests, proof_ids)), 133)
        with self.assertRaisesRegex(ValueError, "IDs must be unique"):
            M.build_units([request(), request()], set())

    def test_bad_axes_or_selected_value_do_not_get_repaired(self):
        req = request(axes=[])  # malformed input must not be coerced into a shared unit
        with self.assertRaisesRegex(ValueError, "must be a string or empty"):
            M.build_units([req], set())
        req = request(selected="not-an-original-option")
        with self.assertRaisesRegex(ValueError, "original raw option"):
            M.build_units([req], set())


if __name__ == "__main__":
    unittest.main()
