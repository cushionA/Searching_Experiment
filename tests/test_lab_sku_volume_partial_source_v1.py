from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/prepare_indexed_volume_partial_source_v1.py"
SPEC = importlib.util.spec_from_file_location("volume_partial_source", MODULE_PATH)
partial = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(partial)


class PartialSourceReadinessTests(unittest.TestCase):
    def setUp(self):
        family = {"ordered_native_family": ["red", "blue"], "raw_file": "raw.html", "raw_sha256": "a" * 64}
        self.rows = {
            "documents.jsonl": [{"document_id": "d1"}],
            "requests.jsonl": [
                {"request_id": "rv", "option_values": ["red", "blue"], "source_family_receipt": family,
                 "production_adoption_allowed": False},
                {"request_id": "ru", "option_values": [], "source_family_status": "unknown",
                 "source_family_receipt": None, "model_input_id": None, "production_adoption_allowed": False},
            ],
            "tasks.jsonl": [{"task_id": "tv", "request_id": "rv"}, {"task_id": "tu", "request_id": "ru"}],
            "row-plans.jsonl": [{"row": "r1"}, {"row": "r2"}],
            "case-plans.jsonl": [{"case_id": "c1", "au_product_id": "p1"},
                                 {"case_id": "c2", "au_product_id": "p1"}],
            "membership.jsonl": [{"case_id": "c1", "au_product_id": "p1"},
                                 {"case_id": "c2", "au_product_id": "p1"}],
            "task-request-family-audit.jsonl": [
                {"task_id": "tv", "derived_request_id": "rv", "status": "verified_complete_ordered_native_family",
                 "raw_family": family, "member_spans": [
                     {"index": 0, "value": "red", "span": {"start": 0}},
                     {"index": 1, "value": "blue", "span": {"start": 1}}]},
                {"task_id": "tu", "derived_request_id": "ru", "status": "unknown", "reason": "incomplete coordinate"},
            ],
            "request-model-input-map.jsonl": [{"request_id": "rv", "model_input_id": "mi1"}],
            "baseline-model-inputs.jsonl": [{"model_input_id": "mi1"}],
        }
        self.manifest = {"document_count": 1, "derived_request_count": 2, "task_count": 2,
                         "row_count": 2, "selected_case_count": 2, "selected_product_count": 1}

    def test_accepts_only_explicit_empty_unknown_and_preserves_all_task_ids(self):
        result = partial.validate_source_records(self.rows, self.manifest)
        self.assertEqual(result["verified_count"], 1)
        self.assertEqual(result["unknown_count"], 1)
        self.assertEqual(result["unknown_task_ids"], ["tu"])
        self.assertEqual(len(result["verified_task_ids"]) + len(result["unknown_task_ids"]), 2)

    def test_rejects_unknown_with_option_values_or_model_binding(self):
        self.rows["requests.jsonl"][1]["option_values"] = ["red"]
        with self.assertRaisesRegex(ValueError, "explicitly empty"):
            partial.validate_source_records(self.rows, self.manifest)

        self.setUp()
        self.rows["request-model-input-map.jsonl"].append({"request_id": "ru", "model_input_id": "mi2"})
        with self.assertRaisesRegex(ValueError, "still bound to a model input"):
            partial.validate_source_records(self.rows, self.manifest)

    def test_rejects_promoted_unknown_with_incomplete_verified_receipt(self):
        self.rows["task-request-family-audit.jsonl"][1]["status"] = "verified_complete_ordered_native_family"
        self.rows["task-request-family-audit.jsonl"][1]["raw_family"] = {"ordered_native_family": ["red"]}
        self.rows["task-request-family-audit.jsonl"][1]["member_spans"] = []
        with self.assertRaisesRegex(ValueError, "verified source-family receipt is incomplete"):
            partial.validate_source_records(self.rows, self.manifest)

    def test_requires_contextless_binding_and_no_model_pair_for_unknown(self):
        binding = {"task_binding": {"task_id": "tu"}, "pair_id": None,
                   "preparation_status": "unknown_invalid_source_context", "preparation_errors": ["source family unknown"]}
        partial.validate_contextless_unknowns(["tu"], [binding], [])
        with self.assertRaisesRegex(ValueError, "contextless binding"):
            partial.validate_contextless_unknowns(["tu"], [{**binding, "pair_id": "pair1"}], [{"pair_id": "pair1"}])

    def test_allows_only_exact_same_row_target_fact_dependents(self):
        unknown_task = {"task_id": "tu", "direction": "rakuten_to_au", "case_id": "c1",
            "au_row_key": "row1", "raw_condition_ref": {"axis_name": "color", "value": "blue"}}
        dependent_task = {"task_id": "td", "direction": "au_to_rakuten", "case_id": "c1",
            "au_row_key": "row1", "request_id": "rd"}
        errors = [{"task_id": "tu", "error": "full original option family missing or malformed"},
                  {"task_id": "td", "error": "exact target selected axis fact: expected one source coordinate, found 0"}]
        readiness = {"unknown_task_ids": ["tu"]}
        requests = [{"request_id": "rd", "target_document_id": "dr",
                     "target_selected_axes": [{"axis_name": "color", "value": "blue"}]}]
        documents = [{"document_id": "dr", "side": "rakuten"}]
        result = partial.classify_expected_contextless_errors(errors, readiness,
            [unknown_task, dependent_task], requests, documents)
        self.assertEqual(result["contextless_count"], 2)
        self.assertEqual(result["dependent_contextless_task_ids"], ["td"])

        dependent_task["au_row_key"] = "other-row"
        with self.assertRaisesRegex(ValueError, "not uniquely linked"):
            partial.classify_expected_contextless_errors(errors, readiness,
                [unknown_task, dependent_task], requests, documents)

    def test_rejects_missing_or_extra_source_receipts(self):
        self.rows["task-request-family-audit.jsonl"].pop()
        with self.assertRaisesRegex(ValueError, "artifact count mismatch"):
            partial.validate_source_records(self.rows, self.manifest)


if __name__ == "__main__":
    unittest.main()
