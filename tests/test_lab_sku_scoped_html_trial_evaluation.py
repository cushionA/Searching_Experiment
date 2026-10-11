"""CPU-free evaluation contracts; synthetic fixtures stay outside real data."""
from copy import deepcopy
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
SPEC = importlib.util.spec_from_file_location("scoped_html_evaluator", HERE / "evaluate_scoped_html_trial_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def fixture():
    requests, documents, tasks, row_plans, case_plans, predictions = [], [], [], [], [], []
    for cell in ("fp", "fn"):
        identity, request_id = "synthetic-" + cell, "synthetic-request-" + cell
        doc = {"document_id": "synthetic-document-" + cell,
               "title": {"text": "原商品🙂 " + cell}, "description": {"text": "完全な原文商品仕様"}}
        request = {"request_id": request_id, "request_sha256": cell[0] * 64,
                   "selected_value": "完全な原文選択値", "option_values": ["完全な原文選択値", "別の原文値"],
                   "direction": "rakuten_to_au", "target_document_id": doc["document_id"],
                   "target_selected_axes_text": "色：青🙂"}
        task = {"task_id": "synthetic-task-" + cell, "case_id": identity, "request_id": request_id,
                "raw_condition": {"value": request["selected_value"]}, "au_row_key": "row-" + cell}
        prediction = {"request_id": request_id, "request_sha256": request["request_sha256"],
                      "selected_value": request["selected_value"], "source_request": request,
                      "source_task_bindings": [task], "state": "support" if cell == "fp" else "unknown",
                      "winning_value": request["selected_value"] if cell == "fp" else None,
                      "quote_refs": [{"document_id": doc["document_id"], "field": "title", "start": 0,
                                      "end": len(doc["title"]["text"]), "quote": doc["title"]["text"]}] if cell == "fp" else [],
                      "scope_status": "unproved", "production_adoption_allowed": False}
        row_plans.append({"case_id": identity, "au_row_key": "row-" + cell,
                          "condition_bindings": [{"kind": "model_request", "request_id": request_id}],
                          "all_original_axes_covered": True, "upstream_state": "unknown"})
        case_plans.append({"case_id": identity, "dossier_id": "synthetic-pair-" + cell,
                           "cohort": "legacy" if cell == "fp" else "family", "au_product_id": "product-" + cell,
                           "source_sku_key": "synthetic-sku-" + cell, "candidate_row_keys": ["row-" + cell],
                           "all_pending_rows_received": True, "unresolved_omitted_row_count": 0,
                           "upstream_contradiction_row_count": 0, "total_fixed_au_row_count": 1,
                           "upstream_decision": {"decision": "drop", "top_row_key": None}})
        requests.append(request)
        documents.append(doc)
        tasks.append(task)
        predictions.append(prediction)
    return dict(zip(R.SOURCE_NAMES, [requests, documents, tasks, row_plans, case_plans], strict=True)), predictions


def prepare(root):
    prepared, run_dir = root / "prepared", root / "run"
    prepared.mkdir()
    run_dir.mkdir()
    sources, baseline = fixture()
    for name, values in sources.items():
        R.write_rows(prepared / name, values)
    R.write_json(prepared / "manifest.json", {
        # Use the actual producer's schema/fields. The record contents remain
        # synthetic isolated unit fixtures, not performance evaluation data.
        "schema_version": R.PREPARED_SCHEMA, "phase": "prepare", "upstream_head": "frozen-synthetic-head",
        "baseline_matched_count": 0, "labels_read": False, "errors_only": True,
        "independent_test": False, "synthetic_data": False, "production_adoption_allowed": False,
        "case_count": 2, "request_count": 2, "task_count": 2, "document_count": 2, "row_count": 2,
        "selected_error_cells": {"synthetic-fp": "fp", "synthetic-fn": "fn"},
        "selected_error_counts": {"fp": 1, "fn": 1},
        "selection_policy": {"group_fields": ["au_product_id", "cell"], "max_per_group": 3,
                             "population_error_count": 302, "population_error_counts": {"fp": 78, "fn": 224}},
        "output_sha256": {n: R.sha(prepared / n) for n in R.SOURCE_NAMES}})
    for variant in R.VARIANTS:
        values = deepcopy(baseline)
        if variant != "baseline":
            values[0].update(state="unknown", winning_value=None, quote_refs=[])
            doc = sources["documents.jsonl"][1]
            values[1].update(state="support", winning_value=values[1]["selected_value"],
                             quote_refs=[{"document_id": doc["document_id"], "field": "description", "start": 0,
                                          "end": len(doc["description"]["text"]), "quote": doc["description"]["text"]}])
        R.write_rows(run_dir / (variant + "-predictions.jsonl"), values)
    legacy_labels, novel_labels = root / "legacy-labels.jsonl", root / "novel-labels.jsonl"
    R.write_rows(legacy_labels, [{"case_id": "synthetic-fp", "label": "unmatched", "matched_au_row_keys": []}])
    R.write_rows(novel_labels, [{"case_id": "synthetic-fn", "label": "matched", "matched_au_row_keys": ["row-fn"]}])
    return prepared, run_dir, legacy_labels, novel_labels


def decisions():
    baseline = {
        "synthetic-fp": {"decision": "accept", "top_row_key": "row-fp", "au_product_id": "product-fp", "source_sku_key": "sku-fp", "reason": "synthetic"},
        "synthetic-fn": {"decision": "drop", "top_row_key": None, "au_product_id": "product-fn", "source_sku_key": "sku-fn", "reason": "synthetic"},
    }
    current = deepcopy(baseline)
    current["synthetic-fp"].update(decision="drop", top_row_key=None)
    current["synthetic-fn"].update(decision="accept", top_row_key="row-fn")
    refs = {"synthetic-fp": {"label": "unmatched"}, "synthetic-fn": {"label": "matched", "matched_au_row_keys": ["row-fn"]}}
    cells = {"synthetic-fp": "fp", "synthetic-fn": "fn"}
    return baseline, current, refs, cells


class ScopedHtmlTrialEvaluationContracts(unittest.TestCase):
    def test_actual_producer_contract_does_not_require_an_invented_coverage_flag(self):
        with tempfile.TemporaryDirectory() as td:
            args = prepare(Path(td))
            manifest = json.loads((args[0] / "manifest.json").read_text())
            self.assertNotIn("predictions_all_original_request_ids", manifest)
            actual, requests, _, tasks, cases = R.load_prepared(args[0])
            self.assertEqual(actual["schema_version"], "scoped-html-error-preparation-v1")
            self.assertEqual((len(requests), len(tasks), len(cases)), (2, 2, 2))

    def test_unknown_preparation_schema_is_rejected_without_reference_reads(self):
        with tempfile.TemporaryDirectory() as td:
            args = prepare(Path(td))
            path = args[0] / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["schema_version"] = "unrelated-schema"
            path.write_text(json.dumps(manifest))
            with patch.object(R, "route_references") as route, self.assertRaisesRegex(ValueError, "preparation contract"):
                R.load_prepared(args[0])
            route.assert_not_called()

    def test_actual_declared_row_count_is_checked_instead_of_a_boolean_claim(self):
        with tempfile.TemporaryDirectory() as td:
            args = prepare(Path(td))
            path = args[0] / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["row_count"] = 3
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "row_count"):
                R.load_prepared(args[0])

    def test_error_correction_counts_use_selected_denominators_not_population(self):
        result = R.compare_variant(*decisions())
        self.assertEqual(result["fp_corrected"], {"count": 1, "denominator": 1})
        self.assertEqual(result["fn_binary_recovered"], {"count": 1, "denominator": 1})
        self.assertEqual(result["fn_correct_row_recovered"], 1)
        self.assertEqual(result["fn_wrong_row_accepted"], 0)

    def test_wrong_au_row_is_explicit_even_when_binary_sku_label_is_matched(self):
        baseline, current, refs, cells = decisions()
        current["synthetic-fn"]["top_row_key"] = "different-au-row"
        result = R.compare_variant(baseline, current, refs, cells)
        self.assertEqual(result["fn_binary_recovered"]["count"], 1)
        self.assertEqual(result["fn_correct_row_recovered"], 0)
        self.assertEqual(result["fn_wrong_row_accepted"], 1)
        self.assertEqual(result["case_results"][1]["accepted_row_status"], "wrong_accepted_row")

    def test_missing_row_evidence_is_unscored_not_automatically_correct(self):
        baseline, current, refs, cells = decisions()
        del refs["synthetic-fn"]["matched_au_row_keys"]
        result = R.compare_variant(baseline, current, refs, cells)
        self.assertEqual(result["fn_accepted_row_unscored"], 1)
        self.assertEqual(result["fn_correct_row_recovered"], 0)

    def test_original_baseline_must_match_frozen_fp_fn_selection(self):
        baseline, current, refs, cells = decisions()
        cells["synthetic-fp"] = "fn"
        with self.assertRaisesRegex(ValueError, "producer-declared error"):
            R.compare_variant(baseline, current, refs, cells)

    def test_unresolved_reference_cannot_be_scored_as_a_confirmed_error(self):
        baseline, current, refs, cells = decisions()
        refs["synthetic-fn"]["label"] = "needs_review"
        with self.assertRaisesRegex(ValueError, "resolved"):
            R.compare_variant(baseline, current, refs, cells)

    def test_missing_prediction_identity_is_rejected_before_gating(self):
        sources, baseline_values = fixture()
        baseline = {p["request_id"]: p for p in baseline_values}
        with self.assertRaisesRegex(ValueError, "exactly every"):
            R.validate_predictions({r["request_id"]: r for r in sources["requests.jsonl"]}, {}, {}, {}, baseline, "structured-jev")

    def test_reverse_prediction_must_remain_the_complete_baseline_copy(self):
        sources, values = fixture()
        for value in values:
            value["source_request"]["direction"] = "au_to_rakuten"
        baseline = {p["request_id"]: p for p in values}
        current = deepcopy(baseline)
        current["synthetic-request-fp"]["state"] = "unknown"
        with self.assertRaisesRegex(ValueError, "reverse prediction"):
            R.validate_predictions({r["request_id"]: r for r in sources["requests.jsonl"]},
                                   {d["document_id"]: d for d in sources["documents.jsonl"]},
                                   {t["task_id"]: t for t in sources["tasks.jsonl"]}, current, baseline, "structured-jev")

    def test_shared_original_bindings_outside_the_capped_subset_are_preserved(self):
        sources, values = fixture()
        extra = {"task_id": "outside-capped-subset", "case_id": "other-case", "request_id": values[0]["request_id"]}
        values[0]["source_task_bindings"].append(extra)
        baseline = {p["request_id"]: p for p in values}
        result = R.validate_predictions({r["request_id"]: r for r in sources["requests.jsonl"]},
                                       {d["document_id"]: d for d in sources["documents.jsonl"]},
                                       {t["task_id"]: t for t in sources["tasks.jsonl"]}, baseline, baseline, "baseline")
        self.assertEqual(result["literal_verified_support"], 1)

    def test_three_gates_are_frozen_before_first_reference_routing_and_scoring(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = prepare(root)
            output = root / "evaluation"
            original_route = R.route_references
            def checked_route(*route_args, **kwargs):
                self.assertTrue((output / "all-gates-frozen.json").is_file())
                self.assertTrue(all((output / v / "manifest.json").is_file() for v in R.VARIANTS))
                return original_route(*route_args, **kwargs)
            before = {p: R.sha(p) for p in [*args[0].iterdir(), *args[1].iterdir(), args[2], args[3]]}
            with patch.object(R, "route_references", side_effect=checked_route) as route:
                summary = R.run(*args, output)
            route.assert_called_once()
            self.assertEqual(summary["selected_case_count"], 2)
            self.assertEqual(summary["reference_route_counts"], {"legacy-v3": 1, "novel-v1": 1})
            self.assertFalse(summary["full_dataset_accuracy_measured"])
            self.assertFalse(summary["independent_test"])
            self.assertEqual(summary["variants"]["baseline"]["fp_corrected"]["count"], 0)
            self.assertEqual(summary["variants"]["structured-jev"]["fp_corrected"]["count"], 1)
            self.assertEqual(summary["variants"]["structured-verified"]["fn_correct_row_recovered"], 1)
            self.assertTrue(all(R.sha(path) == checksum for path, checksum in before.items()))
            self.assertTrue(all(R.sha(output / path) == checksum for path, checksum in summary["output_sha256"].items()))

    def test_unverified_support_quote_is_dropped_by_unchanged_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = prepare(root)
            path = args[1] / "structured-jev-predictions.jsonl"
            values = list(R.rows(path))
            values[1]["quote_refs"][0]["quote"] = "原文に存在しない引用"
            path.unlink()
            R.write_rows(path, values)
            summary = R.run(*args, root / "evaluation")
            self.assertEqual(summary["variants"]["structured-jev"]["fn_binary_recovered"]["count"], 0)
            self.assertEqual(summary["variants"]["structured-verified"]["fn_binary_recovered"]["count"], 1)

    def test_prepared_corruption_is_rejected_without_opening_reference_labels(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = prepare(root)
            with (args[0] / "tasks.jsonl").open("a") as handle:
                handle.write("{}\n")
            with patch.object(R, "route_references") as route, self.assertRaisesRegex(ValueError, "SHA differs"):
                R.run(*args, root / "evaluation")
            route.assert_not_called()

    def test_ambiguous_selected_reference_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy, novel = root / "legacy", root / "novel"
            R.write_rows(legacy, [{"case_id": "same", "label": "matched"}])
            R.write_rows(novel, [{"case_id": "same", "label": "matched"}])
            with self.assertRaisesRegex(ValueError, "ambiguously"):
                R.route_references({"same"}, legacy, novel, root)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError):
                R.run(Path(td), Path(td), Path(td), Path(td), Path(td))


if __name__ == "__main__":
    unittest.main()
