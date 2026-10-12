from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
SPEC = importlib.util.spec_from_file_location(
    "fresh_v7_adapter", ROOT / "experiments/sku-matching/prepare_fresh_dev_v7_gate_v1.py")
adapter = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(adapter)
VSPEC = importlib.util.spec_from_file_location(
    "fresh_v7_validator", ROOT / "experiments/sku-matching/validate_fresh_dev_v7_gate_v1.py")
validator = importlib.util.module_from_spec(VSPEC)
assert VSPEC and VSPEC.loader
VSPEC.loader.exec_module(validator)


BASE = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1"
ARTIFACT = BASE / "full-gate-inputs-v8"
SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
BUNDLE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
RUNTIME = ROOT / ".lab-output/sku-next-dev-v7-runtime-20261013-v1"


class FreshDevV7GatePrepareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded = adapter.load_source(SOURCE, BUNDLE)
        (cls.source_manifest, _, cls.cases, cls.source_rows, cls.tasks,
         cls.requests, _, *_) = loaded
        cls.selected_cases = {row["case_id"] for row in adapter.read_rows(BASE / "membership.jsonl")}

    def test_frozen_artifact_preserves_complete_source_task_population(self):
        counts = validator.validate_prepared_artifact(ARTIFACT, SOURCE, BUNDLE, BASE)
        self.assertEqual(counts["selected_case_count"], 40)
        self.assertEqual(counts["selected_product_count"], 20)
        self.assertEqual(counts["selected_au_row_count"], 504)
        self.assertEqual(counts["selected_bidirectional_task_count"], 1924)
        self.assertEqual(counts["selected_trace_row_count"], 504)
        self.assertEqual(counts["task_hint_join_count"], 1924)
        self.assertEqual(counts["whole_gate_evaluation_run"], False)

    def test_trace_coverage_rejects_missing_and_extra_identities(self):
        expected = {("case-a", "row-1"), ("case-a", "row-2")}
        with self.assertRaisesRegex(ValueError, "exact expected"):
            validator.validate_trace_coverage(
                [{"case_id": "case-a", "au_row_key": "row-1"}], expected)
        with self.assertRaisesRegex(ValueError, "exact expected"):
            validator.validate_trace_coverage(
                [{"case_id": "case-a", "au_row_key": "row-1"},
                 {"case_id": "case-a", "au_row_key": "row-2"},
                 {"case_id": "case-a", "au_row_key": "row-3"}], expected)

    def _selected_handoff(self, predicate):
        trace_path = RUNTIME / "out/trace/all-row-trace.jsonl"
        with trace_path.open(encoding="utf-8") as source:
            for line in source:
                handoff = json.loads(line)
                if handoff["case_id"] in self.selected_cases and predicate(handoff):
                    return handoff
        self.fail("frozen fresh producer trace has no selected handoff matching the fixture")

    def test_wrong_case_row_identity_and_raw_value_are_rejected(self):
        handoff = self._selected_handoff(lambda row: True)
        wrong_identity = copy.deepcopy(handoff)
        wrong_identity["au_row_key"] = "not-the-source-row"
        with self.assertRaisesRegex(ValueError, "unknown fixed source AU row"):
            adapter.condition_index(wrong_identity, self.source_rows, self.tasks, self.requests)

        mutated_value = copy.deepcopy(handoff)
        axis_value = (next(iter(mutated_value["rakuten_axes"].values()))
                      if isinstance(mutated_value["rakuten_axes"], dict)
                      else mutated_value["rakuten_axes"][0])
        axis_value["value"] = "mutated producer value"
        with self.assertRaisesRegex(ValueError, "differs from frozen whole source"):
            adapter.condition_index(mutated_value, self.source_rows, self.tasks, self.requests)

    def test_numeric_core_or_residual_remains_an_untrusted_hint(self):
        handoff = self._selected_handoff(
            lambda row: any(c.get("core") is not None or c.get("residual") is not None
                            for c in row.get("conditions", [])))
        indexed = adapter.condition_index(handoff, self.source_rows, self.tasks, self.requests)
        related = [hint for hints in indexed.values() for hint in hints
                   if hint["producer_condition"].get("core") is not None
                   or hint["producer_condition"].get("residual") is not None]
        self.assertTrue(related)
        self.assertTrue(all(hint["producer_status_is_proof"] is False
                            and hint["automatic_support_allowed"] is False
                            and hint["automatic_conflict_allowed"] is False
                            and hint["core_residual_are_untrusted_hints"] is True
                            for hint in related))

    def test_wrong_source_input_hash_binding_is_rejected(self):
        trace_manifest = RUNTIME / "out/trace/manifest.json"
        freeze = RUNTIME / "experiment-freeze.json"
        input_freeze = RUNTIME / "input-freeze.json"
        receipt = RUNTIME / "run-receipt.json"
        original = adapter.read_json(trace_manifest)
        changed = copy.deepcopy(original)
        first = next(iter(changed["source_case_record_line_bindings"].values()))
        first["source_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            bad_manifest = Path(directory) / "manifest.json"
            bad_manifest.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unpinned source input"):
                adapter.validate_runtime_manifest(
                    bad_manifest, freeze, input_freeze, receipt,
                    self.source_manifest, BUNDLE,
                    RUNTIME / "out/trace/all-row-trace.jsonl", RUNTIME)


if __name__ == "__main__":
    unittest.main()
