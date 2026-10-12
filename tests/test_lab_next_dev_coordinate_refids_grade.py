"""Tests for the new identity-disjoint 20-case DEV grading guard."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "experiments/sku-matching/grade_next_dev_coordinate_refids_v1.py"
SPEC = importlib.util.spec_from_file_location("next_dev_coord_grader", PATH)
grader = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = grader
SPEC.loader.exec_module(grader)


class NewCoordinateDevMembershipTests(unittest.TestCase):
    def setUp(self):
        self.source = ROOT / ".lab-output/sku-next-dev-coordinate-refids-20261013-v1"

    def test_exact_frozen_twenty_case_cohort_source_lines_and_all_rows(self):
        membership, case_plans, row_keys = grader.validate_dev_membership(
            self.source / "membership.jsonl", self.source / "case-plans.jsonl",
            self.source / "row-plans.jsonl", self.source / "selection-plan.json")
        self.assertEqual(len(membership), 20)
        self.assertEqual(len(case_plans), 20)
        self.assertEqual(sum(map(len, row_keys.values())), 320)
        self.assertEqual(len({row["au_product_id"] for row in membership}), 10)
        self.assertTrue(all(len(v) > 0 for v in row_keys.values()))
        self.assertTrue(all(len(case["raw_selected_sku_source_sha256"]) == 64 for case in case_plans))

    def test_membership_must_match_frozen_sha_before_policy_or_reference_use(self):
        with tempfile.TemporaryDirectory() as temp:
            altered = Path(temp) / "membership.jsonl"
            data = (self.source / "membership.jsonl").read_bytes() + b"\n"
            altered.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "membership SHA"):
                grader.validate_dev_membership(altered, self.source / "case-plans.jsonl",
                                               self.source / "row-plans.jsonl", self.source / "selection-plan.json")

    def test_changed_selection_policy_plan_is_rejected_before_any_reference_read(self):
        with tempfile.TemporaryDirectory() as temp:
            changed = Path(temp) / "selection-plan.json"
            changed.write_bytes((self.source / "selection-plan.json").read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "selection-plan bytes"):
                grader.validate_dev_membership(self.source / "membership.jsonl", self.source / "case-plans.jsonl",
                                               self.source / "row-plans.jsonl", changed)

    def test_missing_original_row_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            altered = Path(temp) / "row-plans.jsonl"
            rows = (self.source / "row-plans.jsonl").read_bytes().splitlines(keepends=True)
            altered.write_bytes(b"".join(rows[1:]))
            with self.assertRaisesRegex(ValueError, "complete row-key set"):
                grader.validate_dev_membership(self.source / "membership.jsonl", self.source / "case-plans.jsonl",
                                               altered, self.source / "selection-plan.json")

    def test_original_test_cohort_is_source_provenance_only_and_disjoint(self):
        test_cases = grader.read_jsonl(grader.TEST_CASE_PLANS)
        selected = {x["au_product_id"] for x in grader.read_jsonl(self.source / "membership.jsonl")}
        test_products = {x["au_product_id"] for x in test_cases}
        self.assertEqual(len(test_cases), 1292)
        self.assertEqual(len(test_products), 106)
        self.assertFalse(selected & test_products)

    def test_explicit_root_go_guard_precedes_all_file_or_archive_reads(self):
        with self.assertRaisesRegex(PermissionError, "explicit --root-go"):
            grader.grade_from_files(
                root_go=False, membership_path=Path("missing"), case_plans_path=Path("missing"),
                row_plans_path=Path("missing"), selection_plan_path=Path("missing"),
                receipt_path=Path("missing"), archive_v2=Path("missing-v2.zip"),
                archive_v3=Path("missing-v3.zip"))

    def test_frozen_outputs_require_all_cases_and_rows_before_reference_open(self):
        membership = [{"case_id": f"case-{i}"} for i in range(20)]
        row_keys = {f"case-{i}": {f"row-{i}-a", f"row-{i}-b"} for i in range(20)}
        predictions = {"coordinate-refids-choice-nli": [{"case_id": f"case-{i}"} for i in range(20)],
                       "literal-only": [{"case_id": f"case-{i}"} for i in range(20)]}
        gates = []
        for variant in predictions:
            gates.append((f"{variant}/case-decisions.jsonl", [{"case_id": f"case-{i}"} for i in range(20)]))
            gates.append((f"{variant}/all-row-results.jsonl",
                          [{"case_id": case, "au_row_key": key} for case, keys in row_keys.items() for key in keys]))
        grader.validate_frozen_output_coverage(membership, row_keys, predictions, gates)
        broken = dict(predictions)
        broken["literal-only"] = broken["literal-only"][:-1]
        with self.assertRaisesRegex(ValueError, "predictions do not exactly cover"):
            grader.validate_frozen_output_coverage(membership, row_keys, broken, gates)
        incomplete_gates = gates[:-1] + [(gates[-1][0], gates[-1][1][:-1])]
        with self.assertRaisesRegex(ValueError, "320 selected AU rows"):
            grader.validate_frozen_output_coverage(membership, row_keys, predictions, incomplete_gates)

    def test_grade_does_not_open_any_reference_until_freeze_receipt_validation(self):
        with patch.object(grader, "validate_dev_freeze_receipt", side_effect=ValueError("receipt not frozen")), \
             patch.object(grader, "read_label_archives", side_effect=AssertionError("must stay closed")):
            with self.assertRaisesRegex(ValueError, "receipt not frozen"):
                grader.grade_from_files(
                    root_go=True, membership_path=Path("membership"), case_plans_path=Path("case-plans"),
                    row_plans_path=Path("rows"), selection_plan_path=Path("selection"),
                    receipt_path=Path("receipt"), archive_v2=Path("v2.zip"), archive_v3=Path("v3.zip"))


if __name__ == "__main__":
    unittest.main()
