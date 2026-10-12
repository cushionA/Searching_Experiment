"""Synthetic contract tests for the mixed GPU evaluator; no labels or GPU data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tests"))
SPEC = importlib.util.spec_from_file_location("mixed_gpu_evaluation", HERE / "evaluate_mixed_gpu_sku_v1.py")
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
import test_lab_sku_scoped_html_trial_evaluation as FIXTURE


class MixedGpuEvaluationTests(unittest.TestCase):
    def test_canonical_resolved_row_keys_and_false_adoptions_are_counted(self):
        gates = {
            "canonical": {"decision": "accept", "top_row_key": "au-row"},
            "false": {"decision": "accept", "top_row_key": "other-row"},
            "unscored": {"decision": "accept", "top_row_key": "row-without-reference"},
        }
        route = {"source_path": "references.jsonl"}
        references = {
            "canonical": {"decision": "matched", "au_row_keys": ["au-row"], "scoring_reference_source": route},
            "false": {"decision": "unmatched", "scoring_reference_source": route},
            "unscored": {"decision": "matched", "scoring_reference_source": route},
        }
        result = E.metrics_for(gates, references)
        self.assertEqual(result["row_outcomes"], {
            "correct_accepted_row": 1, "wrong_accepted_row": 0,
            "accepted_matched_row_unscored": 1, "false_positive_sku": 1,
        })

    def test_metrics_are_stratum_local_and_report_row_errors(self):
        gates = {
            "a": {"decision": "accept", "top_row_key": "row-good"},
            "b": {"decision": "accept", "top_row_key": "row-wrong"},
            "c": {"decision": "drop", "top_row_key": None},
        }
        refs = {
            "a": {"label": "matched", "matched_au_row_keys": ["row-good"], "scoring_reference_source": {"source_path": "old.jsonl"}},
            "b": {"label": "matched", "matched_au_row_keys": ["row-good"], "scoring_reference_source": {"source_path": "old.jsonl"}},
            "c": {"label": "unmatched", "scoring_reference_source": {"source_path": "old.jsonl"}},
        }
        result = E.metrics_for(gates, refs)
        self.assertEqual((result["tp"], result["fp"], result["fn"], result["tn"]), (2, 0, 0, 1))
        self.assertEqual(result["row_outcomes"], {"correct_accepted_row": 1, "wrong_accepted_row": 1,
                         "accepted_matched_row_unscored": 0, "false_positive_sku": 0})
        self.assertEqual(result["case_results"][1]["row_status"], "wrong_accepted_row")

    def test_old_reference_reader_preserves_route_and_rejects_other_strata(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "old-labels.jsonl"
            E.write_rows(path, [{"case_id": "old-case", "decision": "matched",
                                 "scoring_reference_source": {"source_path": "original-labels.jsonl",
                                     "source_sha256": "a" * 64, "jsonl_row": 4}}])
            refs, digest = E.read_reference_file(path, {"old-case"}, "prior_errors")
            self.assertEqual(refs["old-case"]["scoring_reference_source"]["jsonl_row"], 4)
            self.assertEqual(digest, E.sha(path))
            with self.assertRaisesRegex(ValueError, "out-of-scope"):
                E.read_reference_file(path, {"new-dev-case"}, "prior_errors")

    def test_new_dev_route_requires_exact_selected_ids_and_keeps_uncertainty_unscored(self):
        selected = {"dev-a", "dev-b", "dev-c"}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "selected-dev-references.jsonl"
            route = {"source_path": "original-dev-labels.jsonl", "source_sha256": "c" * 64,
                     "jsonl_row": 1, "reference_kind": "independent_dev"}
            E.write_rows(path, [
                {"case_id": "dev-a", "decision": "matched", "matching_au_row_keys": ["row-a"],
                 "scoring_reference_source": route},
                {"case_id": "dev-b", "decision": "unmatched", "scoring_reference_source": route},
                {"case_id": "dev-c", "decision": "review", "scoring_reference_source": route},
            ])
            refs, _ = E.read_reference_file(path, selected, "independent_dev", require_all=True)
            self.assertEqual(refs["dev-c"]["label"], "review")
            self.assertTrue(refs["dev-c"]["reference_uncertain"])
            scored = E.metrics_for({
                "dev-a": {"decision": "accept", "top_row_key": "row-a"},
                "dev-b": {"decision": "drop", "top_row_key": None},
                "dev-c": {"decision": "accept", "top_row_key": "row-c"},
            }, refs)
            self.assertEqual(scored["scored_case_count"], 2)
            self.assertEqual((scored["tp"], scored["tn"]), (1, 1))
            path.unlink()
            E.write_rows(path, [{"case_id": "dev-a", "decision": "matched"}])
            with self.assertRaisesRegex(ValueError, "original source route"):
                E.read_reference_file(path, {"dev-a"}, "independent_dev", require_original_route=True)
            path.unlink()
            E.write_rows(path, [
                {"case_id": "dev-a", "decision": "matched", "matching_au_row_keys": ["row-a"],
                 "scoring_reference_source": route},
                {"case_id": "dev-b", "decision": "unmatched", "scoring_reference_source": route},
                {"case_id": "dev-c", "decision": "review", "scoring_reference_source": route},
            ])
            with self.assertRaisesRegex(ValueError, "incomplete"):
                E.read_reference_file(path, selected | {"dev-missing"}, "independent_dev", require_all=True)

            path.unlink()
            E.write_rows(path, [{"case_id": "independent-test-case", "decision": "matched",
                                 "scoring_reference_source": route}])
            with self.assertRaisesRegex(ValueError, "out-of-scope"):
                E.read_reference_file(path, selected, "independent_dev", require_all=True)
            path.unlink()
            E.write_rows(path, [{"case_id": "dev-a", "decision": "matched", "scoring_reference_source": route},
                                {"case_id": "dev-a", "decision": "unmatched", "scoring_reference_source": route}])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                E.read_reference_file(path, selected, "independent_dev")
            path.unlink()
            E.write_rows(path, [{"case_id": "dev-a", "decision": "unsupported", "scoring_reference_source": route}])
            with self.assertRaisesRegex(ValueError, "unsupported label"):
                E.read_reference_file(path, selected, "independent_dev")

    def test_mixed_loader_rejects_error_subset_contract_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text('{"schema_version":"mixed-real-gpu-sku-inputs-v1",'
                            '"phase":"prepare","labels_read":false,"errors_only":true, '
                            '"independent_test":false,"synthetic":false,'
                            '"production_adoption_allowed":false}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mixed development"):
                E.load_mixed_prepared(Path(td), expected_counts=None)

    def test_diagnostic_and_source_ineligible_cases_are_separate_from_scoring(self):
        membership = {
            "eligible": {"membership_strata": ["old_structural_control"]},
            "ineligible": {"membership_strata": ["prior_errors"]},
            "diagnostic": {"membership_strata": ["product_pair_diagnostic"]},
        }
        hosts = {
            "eligible": {"primary_score_eligible": True, "source_flags": ["options_capture_missing"],
                         "optional_source_capture_incomplete": True},
            "ineligible": {"primary_score_eligible": False, "source_flags": ["source_schema_missing"]},
            "diagnostic": {"primary_score_eligible": False, "source_flags": []},
        }
        rows, flag_counts = E.eligibility_rows(membership, hosts)
        by_id = {row["case_id"]: row for row in rows}
        self.assertTrue(by_id["eligible"]["primary_score_eligible"])
        self.assertTrue(by_id["eligible"]["optional_source_capture_incomplete"])
        self.assertFalse(by_id["ineligible"]["primary_score_eligible"])
        self.assertIn("diagnostic_case_separate_from_condition_score", by_id["diagnostic"]["scoring_exclusion_reasons"])
        self.assertEqual(flag_counts, {"options_capture_missing": 1, "source_schema_missing": 1})

    def test_run_freezes_all_gates_before_reading_old_references(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, run_dir, _, _ = FIXTURE.prepare(root)
            output, labels = root / "evaluation", root / "old-references.jsonl"
            dev_labels = root / "new-dev-references.jsonl"
            source_manifest_path = prepared / "manifest.json"
            source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
            case_ids = list(E.GATE.indexed(prepared / "case_plans.jsonl", "case_id"))
            membership_rows = [{"case_id": identity, "membership_strata": ["prior_errors"],
                                "memberships": [{"stratum": "prior_errors", "primary_score_eligible": True}],
                                "source_cohorts": ["old"], "membership_count": 1} for identity in case_ids]
            gpu_cases = [{"case_id": identity, "source_strata": ["prior_errors"],
                          "memberships": membership_rows[index]["memberships"], "source_flags": [],
                          "primary_score_eligible": True, "optional_source_capture_incomplete": False}
                         for index, identity in enumerate(case_ids)]
            E.write_rows(prepared / "membership.jsonl", membership_rows)
            E.write_rows(prepared / "gpu-cases.jsonl", gpu_cases)
            E.write_rows(prepared / "failures.jsonl", [])
            manifest_path = prepared / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.update(schema_version=E.PREP_SCHEMA, phase="prepare", errors_only=False,
                            synthetic=False, synthetic_data=False, labels_in_model_inputs=False,
                            model_inference_run=False, development_only=True, independent_test=False,
                            production_adoption_allowed=False, upstream_head="test-head",
                            case_count=len(case_ids), membership_case_count=len(case_ids),
                            request_count=source_manifest["request_count"], document_count=source_manifest["document_count"],
                            task_count=source_manifest["task_count"], row_count=source_manifest["row_count"],
                            baseline_matched_count=0, stratum_case_counts={"prior_errors": len(case_ids)})
            manifest["output_sha256"].update({name: E.sha(prepared / name) for name in
                                               ("membership.jsonl", "gpu-cases.jsonl", "failures.jsonl")})
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            product_dir = root / "gpu" / "product-model"
            (root / "gpu").mkdir()
            product_dir.mkdir()
            for name, value in (("summary.json", {"request_count": source_manifest["request_count"]}),
                                ("gpu-source-freeze.json", {}), ("execution-freeze.json", {})):
                (root / "gpu" / name).write_text(json.dumps(value), encoding="utf-8")
            run_dir = root / "gpu"
            for name, value in (("freeze.json", {}), ("summary.json", {}),
                                ("sharing-contract.json", {"basis": "user_asserted"})):
                (product_dir / name).write_text(json.dumps(value), encoding="utf-8")
            _, base_predictions = FIXTURE.fixture()
            E.write_rows(product_dir / "baseline-predictions.jsonl", base_predictions)
            for variant in E.PREDICTION_VARIANTS:
                E.write_rows(product_dir / (variant + "-predictions.jsonl"), base_predictions)
            E.write_rows(labels, [{"case_id": identity, "decision": "matched",
                                   "scoring_reference_source": {"source_path": "old-label-file.jsonl",
                                       "source_sha256": "b" * 64, "jsonl_row": index + 1}}
                                  for index, identity in enumerate(case_ids)])
            E.write_rows(dev_labels, [])

            read_calls = []
            def frozen_reference_reader(*args, **kwargs):
                self.assertTrue((output / "all-gates-frozen.json").is_file())
                self.assertTrue((output / "freeze.json").is_file())
                read_calls.append(args[2])
                return original_reader(*args, **kwargs)

            original_reader = E.read_reference_file
            with patch.object(E, "verify_gpu_run", return_value=({"request_count": source_manifest["request_count"]}, {})), \
                 patch.object(E, "check_prediction_coverage", return_value=(
                     {row["request_id"]: row for row in base_predictions},
                     {name: {row["request_id"]: row for row in base_predictions} for name in E.PREDICTION_VARIANTS}, set())), \
                 patch.object(E, "read_reference_file", side_effect=frozen_reference_reader):
                result = E.run(prepared, run_dir, labels, output, new_dev_references=dev_labels,
                               expected_counts={"prior_errors": len(case_ids)})
            self.assertTrue(result["labels_read_after_all_gates_frozen"])
            self.assertEqual(read_calls, ["prior_errors", "independent_dev"])
            self.assertEqual(result["reference_case_counts"], {"prior_errors": len(case_ids)})
            self.assertFalse(result["independent_dev_references_loaded"])

            freeze_output = root / "evaluation-freeze-only"
            with patch.object(E, "verify_gpu_run", return_value=({"request_count": source_manifest["request_count"]}, {})), \
                 patch.object(E, "check_prediction_coverage", return_value=(
                     {row["request_id"]: row for row in base_predictions},
                     {name: {row["request_id"]: row for row in base_predictions} for name in E.PREDICTION_VARIANTS}, set())), \
                 patch.object(E, "read_reference_file", side_effect=AssertionError("freeze-only accessed references")):
                frozen = E.run(prepared, run_dir, None, freeze_output,
                               expected_counts={"prior_errors": len(case_ids)}, freeze_only=True)
            self.assertTrue(frozen["all_gates_frozen"])
            self.assertFalse((freeze_output / "summary.json").exists())
            self.assertTrue((freeze_output / "freeze.json").exists())


if __name__ == "__main__":
    unittest.main()
