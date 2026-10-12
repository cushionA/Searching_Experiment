from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "experiments/sku-matching/grade_unused_holdout_sku_gate_v1.py"
SPEC = importlib.util.spec_from_file_location("grade_unused_holdout_sku_gate_v1", MODULE_PATH)
GRADER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GRADER)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_bytes(b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in rows))


class UnusedHoldoutGraderTests(unittest.TestCase):
    def _selection_fixture(self, base: Path, *, omit_row=False, wrong_split=False, consume_product=False):
        members = [
            {"case_id": "c1", "au_product_id": "p1", "dossier_id": "d1", "source_version": "v2"},
            {"case_id": "c2", "au_product_id": "p1", "dossier_id": "d2", "source_version": "v3"},
        ]
        cases = [
            {**members[0], "split": "test", "row_keys": ["r1"], "all_original_au_rows_covered": True},
            {**members[1], "split": "dev" if wrong_split else "test", "row_keys": ["r2"], "all_original_au_rows_covered": True},
        ]
        rows = [{"case_id": "c1", "au_row_key": "r1"}]
        if not omit_row:
            rows.append({"case_id": "c2", "au_row_key": "r2"})
        old_members = [{"au_product_id": "p1" if consume_product else "old"}]
        membership_path, cases_path, rows_path = base / "membership.jsonl", base / "cases.jsonl", base / "rows.jsonl"
        policy_path, old_path, receipts_path = base / "policy.json", base / "old.jsonl", base / "line-receipts.jsonl"
        write_jsonl(membership_path, members)
        write_jsonl(cases_path, cases)
        write_jsonl(rows_path, rows)
        write_jsonl(old_path, old_members)
        write_jsonl(receipts_path, [
            {"case_id": "c1", "split": "test", "original_case_line_sha256": "1" * 64},
            {"case_id": "c2", "split": "test", "original_case_line_sha256": "2" * 64},
        ])
        policy = {"schema_version": "unused-holdout-choice-nli-selection-policy-v1",
                  "source_files": {"old_subset_membership_sha256": digest(old_path.read_bytes())},
                  "scope_guards": {"labels_read": False, "gold_or_references_read": False,
                                   "predictions_or_scores_read": False, "classification_or_scoring_run": False}}
        canonical = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        policy_sha = digest(canonical)
        policy.update({"policy_sha256": policy_sha, "status": "frozen_before_selection"})
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        source_manifest = base / "source-manifest.json"
        source_manifest.write_text(json.dumps({"schema_version": "full-condition-inputs-v2", "split": "test",
            "independent_test": True, "labels_read": False,
            "labels_gold_references_predictions_scores_read": False,
            "all_rows_for_selected_cases_retained": True, "all_original_task_bindings_retained": True,
            "selection_policy_sha256": policy_sha, "selected_membership_sha256": digest(membership_path.read_bytes()),
            "outputs_sha256": {"case-plans.jsonl": digest(cases_path.read_bytes()),
                               "row-plans.jsonl": digest(rows_path.read_bytes())}}), encoding="utf-8")
        old_sha = digest(old_path.read_bytes())
        member_sha = digest(membership_path.read_bytes())
        with patch.object(GRADER, "CASE_COUNT", 2), patch.object(GRADER, "PRODUCT_COUNT", 1), \
             patch.object(GRADER, "EXPECTED_MEMBERSHIP_SHA256", member_sha), \
             patch.object(GRADER, "EXPECTED_POLICY_SHA256", policy_sha), \
             patch.object(GRADER, "EXPECTED_OLD_MEMBERSHIP_SHA256", old_sha):
            return (membership_path, cases_path, rows_path, policy_path, old_path, receipts_path, source_manifest,
                    member_sha, policy_sha)

    def test_selection_rejects_consumed_product_dev_case_and_omitted_source_row(self):
        for kwargs, message in [({"consume_product": True}, "consumed by the old test cohort"),
                                ({"wrong_split": True}, "not explicitly TEST"),
                                ({"omit_row": True}, "omits or synthesizes an original AU row")]:
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as temp:
                paths = self._selection_fixture(Path(temp), **kwargs)
                with patch.object(GRADER, "CASE_COUNT", 2), patch.object(GRADER, "PRODUCT_COUNT", 1), \
                     patch.object(GRADER, "EXPECTED_MEMBERSHIP_SHA256", paths[7]), \
                     patch.object(GRADER, "EXPECTED_POLICY_SHA256", paths[8]), \
                     patch.object(GRADER, "SOURCE_INPUT_MANIFEST", paths[6]), \
                     patch.object(GRADER, "EXPECTED_OLD_MEMBERSHIP_SHA256", digest(paths[4].read_bytes())):
                    with self.assertRaisesRegex(ValueError, message):
                        GRADER.validate_selection(*paths[:6])

    def _archive(self, path: Path, version: str, record: dict, answer: dict) -> None:
        prefix = f".lab-output/sku-independent-real-20261011/eval-{version}/labels-token/"
        answers = (json.dumps(answer, separators=(",", ":")) + "\n").encode()
        resolved = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        manifest = {"answers_sha256": {"answer.jsonl": digest(answers)}, "resolved_sha256": digest(resolved)}
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(prefix + "answer.jsonl", answers)
            zf.writestr(prefix + "private/resolved.jsonl", resolved)
            zf.writestr(prefix + "private/label-manifest.json", json.dumps(manifest))

    def test_archive_parser_binds_exact_answer_line_and_rejects_non_source_row_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            v2, v3 = base / "v2.zip", base / "v3.zip"
            record = {"case_id": "c1", "expected": "accept", "data_quality_flag": None,
                      "au_row_keys": ["actual-row"], "answer_file": "answer.jsonl", "answer_line": 1}
            answer = {"s": "opaque", "expected": "accept", "au_rows": ["raw-au-id"],
                      "data_quality_flag": None, "rationale": []}
            self._archive(v2, "v2", record, answer)
            members = [{"case_id": "c1", "source_version": "v2"}]
            with patch.object(GRADER, "V2_LABEL_ARCHIVE_SHA256", digest(v2.read_bytes())):
                refs = GRADER.load_selected_references(v2, v3, members, {"c1": {"actual-row"}})
                self.assertEqual(refs["c1"]["source_answer_route"]["answer_file"], "answer.jsonl")
                self.assertEqual(refs["c1"]["source_answer_route"]["answer_line"], 1)
                self.assertEqual(refs["c1"]["source_answer_sha256"], digest(json.dumps(answer, separators=(",", ":")).encode()))
                self.assertEqual(refs["c1"]["au_row_keys"], ["actual-row"])
                with self.assertRaisesRegex(ValueError, "not actual frozen AU rows"):
                    GRADER.load_selected_references(v2, v3, members, {"c1": {"different-row"}})

    def test_bad_freeze_receipt_rejects_before_label_archive_open(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            receipt = base / "freeze.json"
            receipt.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
            with patch.object(GRADER, "load_selected_references", side_effect=AssertionError("archive opened")):
                with self.assertRaisesRegex(ValueError, "freeze receipt does not bind"):
                    GRADER.grade_from_files(root_go=True, membership_path=base / "m", case_plans_path=base / "c",
                        row_plans_path=base / "r", selection_policy_path=base / "p", old_membership_path=base / "old",
                        original_case_receipts_path=base / "lines", receipt_path=receipt,
                        archive_v2=base / "should-not-open-v2", archive_v3=base / "should-not-open-v3")


if __name__ == "__main__":
    unittest.main()
