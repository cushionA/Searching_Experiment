from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "experiments/sku-matching/grade_final_test_subset_v1.py"
SPEC = importlib.util.spec_from_file_location("grade_final_test_subset_v1", MODULE_PATH)
GRADER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GRADER)


class FinalTestSubsetGraderTests(unittest.TestCase):
    def setUp(self):
        self.membership = [
            {"case_id": "c1", "source_version": "v2"},
            {"case_id": "c2", "source_version": "v2"},
            {"case_id": "c3", "source_version": "v3"},
        ]

    def test_unknown_reference_remains_in_total_denominator_and_wrong_row_is_counted(self):
        predictions = [
            {"case_id": "c1", "decision": "matched", "top_row_key": "wrong"},
            {"case_id": "c2", "decision": "unmatched"},
            {"case_id": "c3", "decision": "matched", "top_row_key": "r3"},
        ]
        references = {
            "c1": {"case_id": "c1", "decision": "matched", "matching_au_row_keys": ["right"], "_reference_split": "v2"},
            "c3": {"case_id": "c3", "decision": "review", "_reference_split": "v3"},
        }
        result = GRADER.score_variant(self.membership, predictions, references)
        self.assertEqual(result["total_case_count"], 3)
        self.assertEqual(result["known_reference_case_count"], 1)
        self.assertEqual(result["unknown_reference_count"], 2)
        self.assertEqual(result["metrics"]["scored_case_count"], 1)
        self.assertEqual(result["row_metrics"]["wrong_accepted_row"], 1)
        self.assertEqual(result["exact_membership_coverage"]["case_omission_count"], 0)

    def test_prediction_case_omission_fails_closed(self):
        predictions = [{"case_id": "c1", "decision": "unmatched"}, {"case_id": "c2", "decision": "unmatched"}]
        with self.assertRaisesRegex(ValueError, "exactly match frozen membership"):
            GRADER.score_variant(self.membership, predictions, {})

    def test_duplicate_case_ids_fail_closed(self):
        predictions = [
            {"case_id": "c1", "decision": "unmatched"},
            {"case_id": "c1", "decision": "matched", "top_row_key": "r"},
            {"case_id": "c3", "decision": "unmatched"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate case_id"):
            GRADER.score_variant(self.membership, predictions, {})

    def test_freeze_guard_precedes_any_label_archive_access(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            membership_path = base / "membership.jsonl"
            receipt_path = base / "receipt.json"
            membership_path.write_text("{}\n", encoding="utf-8")
            receipt_path.write_text(json.dumps({"freeze_status": "inference_running"}), encoding="utf-8")
            with self.assertRaises(PermissionError):
                GRADER.grade_from_files(
                    root_go=False,
                    membership_path=membership_path,
                    receipt_path=receipt_path,
                    variant_paths={},
                    gate_paths=[],
                    archive_v2=base / "must-not-open-v2.zip",
                    archive_v3=base / "must-not-open-v3.zip",
                )
            with self.assertRaisesRegex(ValueError, "all predictions and gates are frozen"):
                GRADER.validate_freeze_receipt(receipt_path, membership_path, {}, [])

    def test_frozen_artifact_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            artifact = base / "prediction.jsonl"
            artifact.write_text('{"case_id":"c1"}\n', encoding="utf-8")
            receipt_path = base / "receipt.json"
            receipt = {
                "freeze_status": "all_predictions_and_gates_frozen",
                "all_predictions_frozen": True,
                "all_gates_frozen": True,
                "independent_test": True,
                "membership_sha256": GRADER.EXPECTED_MEMBERSHIP_SHA256,
                "frozen_files": {"prediction.jsonl": GRADER.sha(artifact.read_bytes())},
            }
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            artifact.write_text('{"case_id":"c1","decision":"matched"}\n', encoding="utf-8")
            membership_path = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/plan/subset-membership.jsonl"
            with self.assertRaisesRegex(ValueError, "artifact absent from freeze receipt or SHA mismatch"):
                GRADER.validate_freeze_receipt(receipt_path, membership_path, {"baseline": artifact}, [])

    def test_reviewed_grader_pin_mismatch_fails_before_any_label_open(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            membership_path = base / "membership.jsonl"
            membership_path.write_text("{}\n", encoding="utf-8")
            receipt_path = base / "receipt.json"
            receipt_path.write_text(json.dumps({
                "freeze_status": "all_predictions_and_gates_frozen",
                "all_predictions_frozen": True,
                "all_gates_frozen": True,
                "independent_test": True,
                "membership_sha256": GRADER.EXPECTED_MEMBERSHIP_SHA256,
                "grader_code_sha256": "0" * 64,
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "grader code SHA differs"):
                GRADER.validate_freeze_receipt(receipt_path, membership_path, {}, [])

    def _write_reference_archive(self, path, split, rows, answers):
        prefix = f".lab-output/sku-independent-real-20261011/eval-{split}/labels-token/"
        answer_name = "answers-001.jsonl"
        answer_bytes = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in answers)
        resolved_bytes = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows)
        manifest = {"answers_sha256": {answer_name: hashlib.sha256(answer_bytes).hexdigest()},
                    "resolved_sha256": hashlib.sha256(resolved_bytes).hexdigest()}
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(prefix + "answers-001.jsonl", answer_bytes)
            zf.writestr(prefix + "private/resolved.jsonl", resolved_bytes)
            zf.writestr(prefix + "private/label-manifest.json", json.dumps(manifest))

    @staticmethod
    def _resolved(case_id, split, expected, answer_line=1, row_keys=None):
        return {"case_id": case_id, "dossier_id": "d-" + case_id, "unit": "unit-1",
                "sku_ref": "ref-" + case_id, "label_source": "frozen-label-api",
                "is_independent_human_gold": True, "expected": expected,
                "au_row_keys": row_keys or [], "data_quality_flag": None,
                "rationale": [], "answer_file": "answers-001.jsonl", "answer_line": answer_line}

    @staticmethod
    def _answer(expected, *, au_rows=None, extra=None):
        row = {"s": "opaque-source-sku", "expected": expected,
               "au_rows": au_rows or [], "data_quality_flag": None, "rationale": []}
        return {**row, **(extra or {})}

    def test_actual_token_envelope_uses_exact_resolved_line_binding_and_preserves_raw_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            v2, v3 = base / "v2.zip", base / "v3.zip"
            self._write_reference_archive(v2, "v2", [self._resolved("c2", "v2", "accept", row_keys=["rk2"])],
                                          [self._answer("accept", au_rows=["a001"])])
            self._write_reference_archive(v3, "v3", [self._resolved("c3", "v3", None)],
                                          [self._answer(None)])
            with patch.object(GRADER, "V2_LABEL_ARCHIVE_SHA256", GRADER.sha(v2.read_bytes())), \
                 patch.object(GRADER, "V3_LABEL_ARCHIVE_SHA256", GRADER.sha(v3.read_bytes())):
                refs = GRADER.load_token_reference_shards(
                    v2, v3, {"c2", "c3"}, {"c2": "v2", "c3": "v3"},
                    {"c2": {"rk2"}, "c3": set()})
            self.assertEqual(set(refs), {"c2", "c3"})
            self.assertEqual(refs["c2"]["label"], "matched")
            self.assertEqual(refs["c3"]["label"], "review")
            self.assertEqual(refs["c2"]["au_row_keys"], ["rk2"])
            self.assertEqual(refs["c2"]["source_answer_record"]["au_rows"], ["a001"])
            self.assertNotIn("case_id", refs["c2"]["source_answer_record"])
            self.assertEqual(refs["c2"]["source_answer_route"]["answer_line"], 1)

    def test_duplicate_source_answer_pointer_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            v2, v3 = base / "v2.zip", base / "v3.zip"
            self._write_reference_archive(v2, "v2", [
                self._resolved("c1", "v2", "accept"), self._resolved("c2", "v2", "accept")],
                [self._answer("accept")])
            self._write_reference_archive(v3, "v3", [self._resolved("c3", "v3", "drop")],
                                          [self._answer("drop")])
            with patch.object(GRADER, "V2_LABEL_ARCHIVE_SHA256", GRADER.sha(v2.read_bytes())), \
                 patch.object(GRADER, "V3_LABEL_ARCHIVE_SHA256", GRADER.sha(v3.read_bytes())):
                with self.assertRaisesRegex(ValueError, "duplicate selected source answer pointer"):
                    GRADER.load_token_reference_shards(
                        v2, v3, {"c1", "c2", "c3"}, {"c1": "v2", "c2": "v2", "c3": "v3"},
                        {"c1": set(), "c2": set(), "c3": set()})

    def test_missing_mapping_and_non_input_row_key_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            v2, v3 = base / "v2.zip", base / "v3.zip"
            self._write_reference_archive(v2, "v2", [self._resolved("c2", "v2", "accept", row_keys=["fake"])],
                                          [self._answer("accept")])
            self._write_reference_archive(v3, "v3", [self._resolved("c3", "v3", "drop")],
                                          [self._answer("drop")])
            with patch.object(GRADER, "V2_LABEL_ARCHIVE_SHA256", GRADER.sha(v2.read_bytes())), \
                 patch.object(GRADER, "V3_LABEL_ARCHIVE_SHA256", GRADER.sha(v3.read_bytes())):
                with self.assertRaisesRegex(ValueError, "row keys are absent from frozen case inputs"):
                    GRADER.load_token_reference_shards(
                        v2, v3, {"c2", "c3"}, {"c2": "v2", "c3": "v3"},
                        {"c2": {"real"}, "c3": set()})
                with self.assertRaisesRegex(ValueError, "does not exactly cover selected cases"):
                    GRADER.load_token_reference_shards(
                        v2, v3, {"c2", "missing"}, {"c2": "v2", "missing": "v2"},
                        {"c2": {"fake"}, "missing": set()})

    def test_unexpected_answer_envelope_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            v2, v3 = base / "v2.zip", base / "v3.zip"
            self._write_reference_archive(v2, "v2", [self._resolved("c2", "v2", "accept")],
                                          [self._answer("accept", extra={"decision": "matched"})])
            self._write_reference_archive(v3, "v3", [self._resolved("c3", "v3", "drop")],
                                          [self._answer("drop")])
            with patch.object(GRADER, "V2_LABEL_ARCHIVE_SHA256", GRADER.sha(v2.read_bytes())), \
                 patch.object(GRADER, "V3_LABEL_ARCHIVE_SHA256", GRADER.sha(v3.read_bytes())):
                with self.assertRaisesRegex(ValueError, "unexpected frozen answer envelope schema"):
                    GRADER.load_token_reference_shards(
                        v2, v3, {"c2", "c3"}, {"c2": "v2", "c3": "v3"},
                        {"c2": set(), "c3": set()})


if __name__ == "__main__":
    unittest.main()
