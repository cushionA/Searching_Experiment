"""Synthetic integrity and scoring tests for keyword-filter evaluation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[1]
HERE = REPO / "experiments/sku-matching"
sys.path.insert(0, str(HERE))

import evaluate_keyword_filtered_product_v1 as EV


def write_rows(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(v, sort_keys=True) + "\n" for v in values), encoding="utf-8")


class KeywordFilteredEvaluationTests(unittest.TestCase):
    def test_candidate_predictions_require_full_coverage_and_exact_passthrough(self):
        requests = {"forward": {"direction": "rakuten_to_au"},
                    "reverse": {"direction": "au_to_rakuten"}}
        baseline = {key: {"request_id": key, "source": "fresh-baseline"} for key in requests}
        docs, tasks, eligible = {}, {}, {"forward"}
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td)
            write_rows(candidate / "product-top3-jev-predictions.jsonl", baseline.values())
            with patch.object(EV.E.SHARED, "validate_shared_predictions") as validator:
                predictions, passthrough = EV.validate_candidate_predictions(
                    candidate, requests, docs, tasks, baseline, eligible)
            validator.assert_called_once_with(requests, docs, tasks, predictions,
                                              baseline, eligible, EV.VARIANT)
            self.assertEqual(passthrough, {"reverse"})

            write_rows(candidate / "product-top3-jev-predictions.jsonl", [baseline["forward"]])
            with self.assertRaisesRegex(ValueError, "cover every original request"):
                EV.validate_candidate_predictions(candidate, requests, docs, tasks, baseline, eligible)

            write_rows(candidate / "product-top3-jev-predictions.jsonl", [
                baseline["forward"], {"request_id": "reverse", "source": "changed"}])
            with patch.object(EV.E.SHARED, "validate_shared_predictions"):
                with self.assertRaisesRegex(ValueError, "nonshared/reverse baseline"):
                    EV.validate_candidate_predictions(candidate, requests, docs, tasks, baseline, eligible)

    def test_selected_archive_record_checks_archive_member_offset_and_record_hash(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "labels.zip"
            member_name = "labels/resolved.jsonl"
            lines = [b'{"case_id":"unselected","expected":"drop"}\n',
                     b'{"case_id":"dev-a","expected":"accept","au_row_keys":["row-a"]}\n']
            payload = b"".join(lines)
            with ZipFile(archive, "w") as zipped:
                zipped.writestr(member_name, payload)
            offset = len(lines[0])
            source = {"source_archive_sha256": EV.sha(archive), "member": member_name,
                      "member_sha256": hashlib.sha256(payload).hexdigest(),
                      "byte_offset": offset, "record_sha256": hashlib.sha256(lines[1].rstrip()).hexdigest()}
            record = EV.verify_archive_reference(archive, source, "dev-a")
            self.assertEqual(record["au_row_keys"], ["row-a"])
            with self.assertRaisesRegex(ValueError, "original reference bytes"):
                EV.verify_archive_reference(archive, {**source, "record_sha256": "0" * 64}, "dev-a")

    def test_score_changes_counts_recovery_regression_and_canonical_row_errors(self):
        gates = {
            "recover": {"decision": "accept", "top_row_key": "gold-row"},
            "regress": {"decision": "drop", "top_row_key": None},
            "wrong-row": {"decision": "accept", "top_row_key": "other-row"},
            "stable": {"decision": "drop", "top_row_key": None},
        }
        route = {"source_path": "fixture.jsonl", "source_sha256": "a" * 64}
        refs = {
            "recover": {"label": "matched", "au_row_keys": ["gold-row"], "scoring_reference_source": route},
            "regress": {"label": "matched", "au_row_keys": ["gold-row"], "scoring_reference_source": route},
            "wrong-row": {"label": "matched", "au_row_keys": ["gold-row"], "scoring_reference_source": route},
            "stable": {"label": "unmatched", "scoring_reference_source": route},
        }
        old = {
            "recover": {"binary_cell": "fn", "decision": "drop", "row_status": "not_accepted"},
            "regress": {"binary_cell": "tp", "decision": "accept", "row_status": "correct_accepted_row"},
            "wrong-row": {"binary_cell": "tp", "decision": "accept", "row_status": "correct_accepted_row"},
            "stable": {"binary_cell": "tn", "decision": "drop", "row_status": "not_accepted"},
        }
        metrics, changes = EV.score_changes(gates, old, refs)
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"]), (2, 0, 1, 1))
        self.assertEqual(metrics["row_outcomes"]["wrong_accepted_row"], 1)
        self.assertEqual(changes["fn_to_tp_recovered_case_ids"], ["recover"])
        self.assertEqual(changes["tp_to_fn_regressed_case_ids"], ["regress"])
        self.assertEqual(changes["binary_cell_transitions"], {
            "fn->tp": 1, "tp->fn": 1, "tp->tp": 1, "tn->tn": 1})

    def test_evaluation_does_not_open_original_references_before_candidate_gate_freeze(self):
        identities = ["prior", "dev", "control", "diagnostic"]
        strata = ["prior_errors", "independent_dev", "old_structural_control", "product_pair_diagnostic"]
        membership = {identity: {"membership_strata": [stratum]} for identity, stratum in zip(identities, strata)}
        hosts = {identity: {"primary_score_eligible": identity != "diagnostic", "source_flags": []}
                 for identity in identities}
        cases = dict.fromkeys(identities, {})
        refs = {
            "prior": {"label": "matched", "au_row_keys": ["row-prior"],
                      "scoring_reference_source": {"reference_kind": "prior_errors"}},
            "dev": {"label": "matched", "au_row_keys": ["row-dev"],
                    "scoring_reference_source": {"reference_kind": "independent_dev"}},
            "control": {"label": "unmatched",
                        "scoring_reference_source": {"reference_kind": "old_structural_control"}},
        }
        old = {
            "prior": {"binary_cell": "fn", "decision": "drop", "row_status": "not_accepted"},
            "dev": {"binary_cell": "fn", "decision": "drop", "row_status": "not_accepted"},
            "control": {"binary_cell": "tn", "decision": "drop", "row_status": "not_accepted"},
        }
        baseline = {"r1": {"request_id": "r1"}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prepared, cpu, candidate = root / "prepared", root / "cpu", root / "candidate"
            original, routes = root / "original-eval", root / "routes"
            output = Path(os.path.relpath(root / "evaluation"))
            for directory in (prepared, cpu / "product-model", candidate, original, routes):
                directory.mkdir(parents=True)
            for path in (prepared / "manifest.json", prepared / "requests.jsonl",
                         prepared / "documents.jsonl", prepared / "tasks.jsonl",
                         prepared / "case_plans.jsonl", prepared / "row_plans.jsonl",
                         prepared / "membership.jsonl", prepared / "gpu-cases.jsonl",
                         prepared / "failures.jsonl", cpu / "summary.json", cpu / "cpu-source-freeze.json",
                         cpu / "execution-freeze.json", cpu / "product-model/freeze.json",
                         cpu / "product-model/summary.json", candidate / "freeze.json",
                         candidate / "summary.json", candidate / "product-top3-jev-predictions.jsonl",
                         original / "references-for-scoring.jsonl", routes / "manifest.json"):
                path.write_text("{}\n", encoding="utf-8")
            fake_predictions = {"r1": {"request_id": "r1"}}

            def fake_finish(prepared_dir, prediction_path, gate_dir):
                gate_dir.mkdir(parents=True)
                decisions = [{"case_id": "prior", "decision": "accept", "top_row_key": "row-prior"},
                             {"case_id": "dev", "decision": "accept", "top_row_key": "row-dev"},
                             {"case_id": "control", "decision": "drop", "top_row_key": None},
                             {"case_id": "diagnostic", "decision": "drop", "top_row_key": None}]
                write_rows(gate_dir / "decisions.jsonl", decisions)
                (gate_dir / "manifest.json").write_text(json.dumps({"output_sha256": {}}), encoding="utf-8")
                return {}

            def references_after_freeze(*args):
                self.assertTrue((output / "all-gates-frozen.json").is_file())
                self.assertTrue((output / "freeze.json").is_file())
                return ({"variants": {EV.VARIANT: {}}}, {}, {}, refs, old)

            with patch.object(EV.E, "load_mixed_prepared", return_value=({}, {}, {}, {}, cases, membership, hosts)), \
                 patch.object(EV.E, "verify_cpu_run", return_value=({}, {})), \
                 patch.object(EV.E, "check_prediction_coverage", return_value=(baseline, {}, {"r1"})), \
                 patch.object(EV, "verify_candidate", return_value=({}, {}, fake_predictions, set())), \
                 patch.object(EV, "verify_original_sources", side_effect=references_after_freeze), \
                 patch.object(EV.E.GATE, "finish", side_effect=fake_finish):
                result = EV.run(prepared, cpu, candidate, original, routes, output,
                                expected_scored_counts={"prior_errors": 1,
                                                        "independent_dev": 1,
                                                        "old_structural_control": 1})
            self.assertTrue(result["labels_read_after_candidate_gates_frozen"])
            self.assertEqual(result["scored_case_count"], 3)
            self.assertEqual(result["fn_to_tp_recovered_case_ids"], ["prior", "dev"])
            self.assertEqual(result["unscored_case_counts"]["diagnostic"], 1)

    def test_frozen_output_hash_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            item = root / "prediction.jsonl"
            item.write_text('{"request_id":"r1"}\n', encoding="utf-8")
            expected = EV.sha(item)
            EV.verify_output_hashes(root, {"prediction.jsonl": expected})
            item.write_text('{"request_id":"r2"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "output SHA mismatch"):
                EV.verify_output_hashes(root, {"prediction.jsonl": expected})


if __name__ == "__main__":
    unittest.main()
