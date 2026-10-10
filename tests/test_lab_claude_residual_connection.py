from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/prepare_claude_residual_evidence_v1.py"
SPEC = importlib.util.spec_from_file_location("claude_residual_connection", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def producer(directory, forward_direction="rakuten_to_au"):
    # Synthetic source-binding fixture only; never mixed with collected SKU data.
    directory.mkdir()
    bodies = {"cards.jsonl": (json.dumps({"case_id": "fixture", "direction": forward_direction}) + "\n").encode(),
              "reverse_conditions.jsonl": (json.dumps({"case_id": "fixture", "direction": "au_to_rakuten"}) + "\n").encode()}
    for name, body in bodies.items():
        (directory / name).write_bytes(body)
    manifest = {"code_sha256": "synthetic-code", "output_sha256": {
        name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class ConnectionTests(unittest.TestCase):
    def test_changed_complete_axis_is_rejected_even_on_existing_row(self):
        axis = {"axis_index": 0, "axis_label": "形式", "value": "A / B", "family_values": ["A / B", "C"],
                "value_span": {"quote": "A / B"}}
        au_axis = {"axis_name": "形式", "value": "A / B", "value_span": {"quote": "A / B"}}
        cases = [{"case_id": "fixture", "dossier_id": "fixture-pair", "au_product_id": "fixture-au",
                  "rakuten": {"source_sku_key": "fixture-sku", "axes": [axis]}}]
        products = [{"dossier_id": "fixture-pair", "au": {"rows": [{"row_key": "row", "axes": [au_axis]}]}}]
        card = {"case_id": "fixture", "raw_condition": axis, "condition_id": "claude-whole-axis:0",
                "axis_name": "形式", "selected_value": "A / B", "option_values": ["A / B", "C"],
                "source_refs": [None, axis["value_span"]]}
        MODULE.validate_whole_conditions([card], [], cases, products)
        with self.assertRaisesRegex(ValueError, "value/options/source"):
            MODULE.validate_whole_conditions([{**card, "selected_value": "A"}], [], cases, products)
        with self.assertRaisesRegex(ValueError, "complete source Rakuten axis"):
            MODULE.validate_whole_conditions([{**card, "raw_condition": {**axis, "value": "A"}}], [], cases, products)
        reverse = {"case_id": "fixture", "au_row_key": "row", "au_product_id": "fixture-au", "source_sku_key": "fixture-sku",
                   "raw_condition": au_axis, "axis_name": "形式", "selected_value": "A / B", "option_values": ["A / B"],
                   "source_refs": [None, au_axis["value_span"]]}
        MODULE.validate_whole_conditions([], [reverse], cases, products)
        with self.assertRaisesRegex(ValueError, "complete AU axis"):
            MODULE.validate_whole_conditions([], [{**reverse, "selected_value": "B"}], cases, products)

    def test_source_package_and_direction_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "producer"
            producer(directory)
            forward, reverse, source = MODULE.package_rows(directory, "reverse_conditions.jsonl")
            self.assertEqual(forward[0]["direction"], "rakuten_to_au")
            self.assertEqual(reverse[0]["direction"], "au_to_rakuten")
            self.assertEqual(forward[0]["producer_package_ref"]["directory"], str(directory.resolve()))
            self.assertEqual(forward[0]["producer_package_ref"]["manifest_sha256"], source["manifest_sha256"])

    def test_altered_producer_and_wrong_direction_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            changed, wrong = root / "changed", root / "wrong"
            producer(changed)
            (changed / "cards.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA differs"):
                MODULE.package_rows(changed, "reverse_conditions.jsonl")
            producer(wrong, "au_to_rakuten")
            with self.assertRaisesRegex(ValueError, "wrong evidence direction"):
                MODULE.package_rows(wrong, "reverse_conditions.jsonl")

    def test_original_and_recovery_overlap_fails_before_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original, recovery = root / "original", root / "recovery"
            producer(original)
            manifest = producer(recovery)
            (recovery / "reverse_conditions.jsonl").rename(recovery / "reverse-conditions.jsonl")
            manifest["output_sha256"]["reverse-conditions.jsonl"] = manifest["output_sha256"].pop("reverse_conditions.jsonl")
            (recovery / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "disjoint cases"):
                MODULE.prepare(original, recovery, root / "unused", root / "output")
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
