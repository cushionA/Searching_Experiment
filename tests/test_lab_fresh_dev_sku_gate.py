"""Synthetic-only tests for the selected DEV grader; never open real labels."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GRADER_PATH = ROOT / "experiments/sku-matching/grade_fresh_dev_sku_gate_v1.py"
SPEC = importlib.util.spec_from_file_location("fresh_dev_grader_under_test", GRADER_PATH)
grader = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = grader
SPEC.loader.exec_module(grader)

sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import grade_final_test_subset_v1 as existing_grader  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data) -> bytes:
    raw = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def fixture_membership():
    membership, case_plans, row_plans = [], [], []
    for product_index in range(20):
        product = f"product-{product_index:02d}"
        for case_index in range(2):
            case_id = f"case-{product_index:02d}-{case_index}"
            version = "v2" if product_index % 2 == 0 else "v3"
            row_key = f"au:{product_index}:{case_index}"
            membership.append({"case_id": case_id, "dossier_id": f"dossier-{product_index}",
                               "au_product_id": product, "source_version": version,
                               "source_case_input_sha256": "a" * 64,
                               "source_case_line_sha256": (f"{product_index * 2 + case_index:064x}")})
            case_plans.append({"case_id": case_id, "dossier_id": f"dossier-{product_index}",
                               "au_product_id": product, "source_version": version, "split": "dev",
                               "row_keys": [row_key], "all_original_au_rows_covered": True})
            row_plans.append({"case_id": case_id, "au_row_key": row_key})
    return membership, case_plans, row_plans


def write_membership_files(directory: Path, membership, case_plans, row_plans):
    mem_bytes = b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in membership)
    paths = [directory / name for name in ("membership.jsonl", "case-plans.jsonl", "row-plans.jsonl")]
    for path, data in zip(paths, (mem_bytes,
                                  b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in case_plans),
                                  b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in row_plans))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    plan = {"schema_version": "sku-next-dev-v7-subset-plan-v1", "seed": grader.SEED,
            "source_split": "dev", "selected_case_count": 40, "selected_product_count": 20,
            "membership_sha256": digest(mem_bytes), "labels_read": False, "label_archive_read": False,
            "test_split_read": False, "predictions_read": False,
            "selection_uses_only_case_and_product_identity": True,
            "selection_uses_condition_text_or_availability": False,
            "selection_uses_v7_status_or_predictions": False, "selection_excludes_missing_or_wrong_options": False,
            "model_inference_run": False, "gate_output_created": False,
            "selection_membership_binds_exact_source_case_line": True,
            "source_case_input_sha256_by_version": {"v2": "a" * 64, "v3": "a" * 64}}
    plan_path = directory / "selection-plan.json"
    write_json(plan_path, plan)
    return (*paths, plan_path), mem_bytes


def fake_label_archive(path: Path, *, version: str, records: list[dict], answer_rows: list[dict],
                       wrong_answer_pin: bool = False) -> str:
    prefix = f".lab-output/sku-independent-real-20261011/eval-{version}/labels-token/"
    answer_file = "answers-001.jsonl"
    answer_bytes = b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in answer_rows)
    resolved_bytes = b"".join((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in records)
    answer_hash = digest(answer_bytes)
    if wrong_answer_pin:
        answer_hash = "f" * 64
    manifest = {"answers_sha256": {answer_file: answer_hash}, "resolved_sha256": digest(resolved_bytes)}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(prefix + "private/resolved.jsonl", resolved_bytes)
        archive.writestr(prefix + "private/label-manifest.json", json.dumps(manifest).encode())
        archive.writestr(prefix + answer_file, answer_bytes)
    return digest(path.read_bytes())


class FreshDevMembershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.membership, self.cases, self.rows = fixture_membership()
        self.paths, mem_bytes = write_membership_files(self.directory, self.membership, self.cases, self.rows)
        self.original_sha = grader.MEMBERSHIP_SHA256
        grader.MEMBERSHIP_SHA256 = digest(mem_bytes)

    def tearDown(self):
        grader.MEMBERSHIP_SHA256 = self.original_sha
        self.temp.cleanup()

    def test_exact_forty_dev_cases_and_twenty_products_pass(self):
        membership, _, row_keys = grader.validate_dev_membership(*self.paths)
        self.assertEqual(len(membership), 40)
        self.assertEqual(len(row_keys), 40)

    def test_non_dev_case_plan_is_rejected(self):
        self.cases[0]["split"] = "test"
        paths, _ = write_membership_files(self.directory, self.membership, self.cases, self.rows)
        with self.assertRaisesRegex(ValueError, "source split"):
            grader.validate_dev_membership(*paths)

    def test_duplicate_case_id_is_rejected(self):
        self.membership[-1] = dict(self.membership[0])
        paths, _ = write_membership_files(self.directory, self.membership, self.cases, self.rows)
        grader.MEMBERSHIP_SHA256 = digest(paths[0].read_bytes())
        with self.assertRaisesRegex(ValueError, "duplicate case_id"):
            grader.validate_dev_membership(*paths)

    def test_missing_row_key_is_rejected(self):
        self.rows.pop()
        paths, _ = write_membership_files(self.directory, self.membership, self.cases, self.rows)
        with self.assertRaisesRegex(ValueError, "complete row-key set"):
            grader.validate_dev_membership(*paths)

    def test_wrong_membership_sha_is_rejected(self):
        self.paths[0].write_bytes(self.paths[0].read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "membership SHA"):
            grader.validate_dev_membership(*self.paths)


class FreshDevFreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original_root, self.original_file = grader.ROOT, grader.__file__
        grader.ROOT = self.root
        grader.__file__ = str(self.root / "experiments/sku-matching/grade_fresh_dev_sku_gate_v1.py")
        self.original_membership_sha = grader.MEMBERSHIP_SHA256
        grader.MEMBERSHIP_SHA256 = "a" * 64
        self.paths = {}
        for rel, content in {
            "inputs/membership.jsonl": b"membership\n",
            "inputs/case-plans.jsonl": b"case-plan\n",
            "inputs/row-plans.jsonl": b"row-plan\n",
            "inputs/selection-plan.json": b"{}\n",
            "inputs/proof-manifest.json": b"proofs\n",
            "run/predictions.jsonl": b"predictions\n",
            "run/gates/decisions.jsonl": b"decisions\n",
            "run/gates/row_results.jsonl": b"row results\n",
            "experiments/sku-matching/grade_fresh_dev_sku_gate_v1.py": b"grader\n",
            "experiments/sku-matching/route_mixed_sku_references_v1.py": b"parser\n",
            "experiments/sku-matching/grade_final_test_subset_v1.py": b"score helper\n",
            "tests/test_lab_fresh_dev_sku_gate.py": b"synthetic tests\n",
        }.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            self.paths[rel] = {"bytes": len(content), "sha256": digest(content)}
        self.receipt_path = self.root / "run/freeze.json"
        self.receipt = {
            "schema_version": "sku-next-dev-grading-freeze-v1",
            "freeze_status": "all_dev_predictions_and_gates_frozen",
            "source_split": "dev", "independent_test": False,
            "labels_read": False, "test_split_read": False,
            "all_predictions_frozen": True, "all_gates_frozen": True,
            "policy_or_threshold_changed": False, "classifier_outputs_changed_after_freeze": False,
            "membership_sha256": "a" * 64,
            "variants": {"baseline": {"predictions": "run/predictions.jsonl", "gate_files": ["run/gates/decisions.jsonl", "run/gates/row_results.jsonl"]}},
            "gate_files": ["run/gates/decisions.jsonl", "run/gates/row_results.jsonl"],
            "input_files": {"membership": "inputs/membership.jsonl", "case_plans": "inputs/case-plans.jsonl",
                             "row_plans": "inputs/row-plans.jsonl", "selection_plan": "inputs/selection-plan.json"},
            "required_artifact_paths": ["inputs/proof-manifest.json"],
            "frozen_files": self.paths.copy(),
            "grader_code_sha256": self.paths["experiments/sku-matching/grade_fresh_dev_sku_gate_v1.py"]["sha256"],
            "reference_parser_helper_sha256": self.paths["experiments/sku-matching/route_mixed_sku_references_v1.py"]["sha256"],
            "score_helper_sha256": self.paths["experiments/sku-matching/grade_final_test_subset_v1.py"]["sha256"],
            "grader_tests_sha256": self.paths["tests/test_lab_fresh_dev_sku_gate.py"]["sha256"],
        }
        write_json(self.receipt_path, self.receipt)

    def tearDown(self):
        grader.ROOT, grader.__file__ = self.original_root, self.original_file
        grader.MEMBERSHIP_SHA256 = self.original_membership_sha
        self.temp.cleanup()

    def validate(self):
        return grader.validate_dev_freeze_receipt(
            self.receipt_path,
            membership_path=self.root / "inputs/membership.jsonl",
            case_plans_path=self.root / "inputs/case-plans.jsonl",
            row_plans_path=self.root / "inputs/row-plans.jsonl",
            selection_plan_path=self.root / "inputs/selection-plan.json",
        )

    def test_complete_dev_freeze_receipt_passes(self):
        self.assertEqual(self.validate()["variant_names"], ["baseline"])

    def test_independent_test_true_is_rejected(self):
        self.receipt["independent_test"] = True
        write_json(self.receipt_path, self.receipt)
        with self.assertRaisesRegex(ValueError, "DEV"):
            self.validate()

    def test_unread_label_assertion_false_is_rejected(self):
        self.receipt["labels_read"] = True
        write_json(self.receipt_path, self.receipt)
        with self.assertRaisesRegex(ValueError, "unread"):
            self.validate()

    def test_missing_prediction_pin_is_rejected(self):
        self.receipt["frozen_files"].pop("run/predictions.jsonl")
        write_json(self.receipt_path, self.receipt)
        with self.assertRaisesRegex(ValueError, "lacks an input/prediction/gate pin"):
            self.validate()

    def test_changed_proof_hash_is_rejected(self):
        (self.root / "inputs/proof-manifest.json").write_bytes(b"edited")
        with self.assertRaisesRegex(ValueError, "pin mismatch"):
            self.validate()

    def test_unlisted_model_or_proof_artifact_is_rejected(self):
        self.receipt["required_artifact_paths"] = ["inputs/unpinned-model-manifest.json"]
        write_json(self.receipt_path, self.receipt)
        with self.assertRaisesRegex(ValueError, "lacks an input/prediction/gate pin"):
            self.validate()


class FreshDevScoreAndReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_root_go_guard_precedes_any_input_or_archive_read(self):
        with self.assertRaisesRegex(PermissionError, "explicit --root-go"):
            grader.grade_from_files(
                root_go=False,
                membership_path=self.directory / "missing-membership.jsonl",
                case_plans_path=self.directory / "missing-case-plans.jsonl",
                row_plans_path=self.directory / "missing-row-plans.jsonl",
                selection_plan_path=self.directory / "missing-selection.json",
                receipt_path=self.directory / "missing-freeze.json",
                archive_v2=self.directory / "missing-v2.zip",
                archive_v3=self.directory / "missing-v3.zip",
            )

    def test_unknown_reference_is_retained_as_unknown_not_binary(self):
        membership = [{"case_id": "a", "source_version": "v2"},
                      {"case_id": "b", "source_version": "v2"},
                      {"case_id": "c", "source_version": "v2"}]
        predictions = [{"case_id": "a", "decision": "matched", "top_row_key": "r1"},
                       {"case_id": "b", "decision": "unmatched"},
                       {"case_id": "c", "decision": "matched", "top_row_key": "r3"}]
        refs = {"a": {"case_id": "a", "label": "matched", "au_row_keys": ["r1"]},
                "b": {"case_id": "b", "label": "unmatched"},
                "c": {"case_id": "c", "label": "review"}}
        result = existing_grader.score_variant(membership, predictions, refs)
        self.assertEqual(result["metrics"]["tp"], 1)
        self.assertEqual(result["metrics"]["tn"], 1)
        self.assertEqual(result["unknown_reference_count"], 1)
        self.assertEqual(result["unknown_reference_case_ids"], ["c"])

    def test_wrong_accepted_row_is_explicit(self):
        membership = [{"case_id": "a", "source_version": "v2"}]
        predictions = [{"case_id": "a", "decision": "matched", "top_row_key": "wrong"}]
        refs = {"a": {"case_id": "a", "label": "matched", "au_row_keys": ["right"]}}
        result = existing_grader.score_variant(membership, predictions, refs)
        self.assertEqual(result["row_metrics"], {"wrong_accepted_row": 1})
        self.assertEqual(result["differences"][0]["difference"], "wrong_accepted_row")

    def test_exact_dev_resolved_rows_preserve_source_and_unknown_label(self):
        v2_archive = self.directory / "v2.zip"
        v3_archive = self.directory / "v3.zip"
        answer_rows = [
            {"s": "source string", "expected": "accept", "au_rows": ["row-v2"],
             "data_quality_flag": "ok", "rationale": ["exact source"]},
        ]
        v2_records = [{"case_id": "dev-v2", "answer_file": "answers-001.jsonl", "answer_line": 1,
                       "expected": "accept", "data_quality_flag": "ok", "au_row_keys": ["row-v2"]}]
        v3_records = [{"case_id": "dev-v3", "answer_file": "answers-001.jsonl", "answer_line": 1,
                       "expected": None, "data_quality_flag": "ok", "au_row_keys": ["row-v3"]}]
        h2 = fake_label_archive(v2_archive, version="v2", records=v2_records, answer_rows=answer_rows)
        v3_answer = [{"s": "unknown source", "expected": None, "au_rows": ["row-v3"],
                      "data_quality_flag": "ok", "rationale": []}]
        h3 = fake_label_archive(v3_archive, version="v3", records=v3_records, answer_rows=v3_answer)
        with patch.object(existing_grader, "V2_LABEL_ARCHIVE_SHA256", h2), patch.object(existing_grader, "V3_LABEL_ARCHIVE_SHA256", h3):
            result = existing_grader.load_token_reference_shards(
                v2_archive, v3_archive, {"dev-v2", "dev-v3"},
                {"dev-v2": "v2", "dev-v3": "v3"},
                {"dev-v2": {"row-v2"}, "dev-v3": {"row-v3"}},
            )
        self.assertEqual(result["dev-v2"]["label"], "matched")
        self.assertEqual(result["dev-v2"]["source_answer_record"]["s"], "source string")
        self.assertEqual(result["dev-v3"]["label"], "review")
        self.assertEqual(result["dev-v3"]["au_row_keys"], ["row-v3"])

    def test_duplicate_answer_pointer_is_rejected(self):
        archive = self.directory / "v2.zip"
        records = [
            {"case_id": "one", "answer_file": "answers-001.jsonl", "answer_line": 1,
             "expected": "accept", "data_quality_flag": "ok", "au_row_keys": ["r1"]},
            {"case_id": "two", "answer_file": "answers-001.jsonl", "answer_line": 1,
             "expected": "accept", "data_quality_flag": "ok", "au_row_keys": ["r2"]},
        ]
        h = fake_label_archive(archive, version="v2", records=records, answer_rows=[
            {"s": "one", "expected": "accept", "au_rows": ["r1"], "data_quality_flag": "ok", "rationale": []}])
        with patch.object(existing_grader, "V2_LABEL_ARCHIVE_SHA256", h):
            with self.assertRaisesRegex(ValueError, "duplicate selected source answer pointer"):
                existing_grader.load_token_reference_shards(
                    archive, archive, {"one", "two"}, {"one": "v2", "two": "v2"},
                    {"one": {"r1"}, "two": {"r2"}},
                )

    def test_answer_row_key_absent_from_frozen_plan_is_rejected(self):
        archive = self.directory / "v2.zip"
        records = [{"case_id": "one", "answer_file": "answers-001.jsonl", "answer_line": 1,
                    "expected": "accept", "data_quality_flag": "ok", "au_row_keys": ["invented-row"]}]
        h = fake_label_archive(archive, version="v2", records=records, answer_rows=[
            {"s": "one", "expected": "accept", "au_rows": ["invented-row"], "data_quality_flag": "ok", "rationale": []}])
        with patch.object(existing_grader, "V2_LABEL_ARCHIVE_SHA256", h):
            with self.assertRaisesRegex(ValueError, "absent from frozen case inputs"):
                existing_grader.load_token_reference_shards(
                    archive, archive, {"one"}, {"one": "v2"}, {"one": {"actual-row"}},
                )

    def test_outer_archive_hash_mismatch_is_rejected(self):
        archive = self.directory / "v2.zip"
        records = [{"case_id": "one", "answer_file": "answers-001.jsonl", "answer_line": 1,
                    "expected": "accept", "data_quality_flag": "ok", "au_row_keys": ["r1"]}]
        fake_label_archive(archive, version="v2", records=records, answer_rows=[
            {"s": "one", "expected": "accept", "au_rows": ["r1"], "data_quality_flag": "ok", "rationale": []}])
        with patch.object(existing_grader, "V2_LABEL_ARCHIVE_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "answer archive SHA"):
                existing_grader.load_token_reference_shards(
                    archive, archive, {"one"}, {"one": "v2"}, {"one": {"r1"}},
                )

    def test_missing_selected_reference_is_rejected(self):
        archive = self.directory / "v2.zip"
        records = [{"case_id": "one", "answer_file": "answers-001.jsonl", "answer_line": 1,
                    "expected": "accept", "data_quality_flag": "ok", "au_row_keys": ["r1"]}]
        h = fake_label_archive(archive, version="v2", records=records, answer_rows=[
            {"s": "one", "expected": "accept", "au_rows": ["r1"], "data_quality_flag": "ok", "rationale": []}])
        with patch.object(existing_grader, "V2_LABEL_ARCHIVE_SHA256", h):
            with self.assertRaisesRegex(ValueError, "exactly cover"):
                existing_grader.load_token_reference_shards(
                    archive, archive, {"one", "missing"}, {"one": "v2", "missing": "v2"},
                    {"one": {"r1"}, "missing": {"r2"}},
                )

    def test_answer_member_hash_mismatch_is_rejected(self):
        archive = self.directory / "v2.zip"
        records = [{"case_id": "one", "answer_file": "answers-001.jsonl", "answer_line": 1,
                    "expected": "accept", "data_quality_flag": "ok", "au_row_keys": ["r1"]}]
        h = fake_label_archive(archive, version="v2", records=records, answer_rows=[
            {"s": "one", "expected": "accept", "au_rows": ["r1"], "data_quality_flag": "ok", "rationale": []}],
            wrong_answer_pin=True)
        with patch.object(existing_grader, "V2_LABEL_ARCHIVE_SHA256", h):
            with self.assertRaisesRegex(ValueError, "answer member SHA"):
                existing_grader.load_token_reference_shards(
                    archive, archive, {"one"}, {"one": "v2"}, {"one": {"r1"}},
                )

    def test_answer_envelope_schema_mismatch_is_rejected(self):
        archive = self.directory / "v2.zip"
        records = [{"case_id": "one", "answer_file": "answers-001.jsonl", "answer_line": 1,
                    "expected": "accept", "data_quality_flag": "ok", "au_row_keys": ["r1"]}]
        h = fake_label_archive(archive, version="v2", records=records, answer_rows=[
            {"s": "one", "expected": "accept", "au_rows": ["r1"], "data_quality_flag": "ok", "rationale": [], "extra": "bad"}])
        with patch.object(existing_grader, "V2_LABEL_ARCHIVE_SHA256", h):
            with self.assertRaisesRegex(ValueError, "unexpected frozen answer envelope"):
                existing_grader.load_token_reference_shards(
                    archive, archive, {"one"}, {"one": "v2"}, {"one": {"r1"}},
                )


if __name__ == "__main__":
    unittest.main()
