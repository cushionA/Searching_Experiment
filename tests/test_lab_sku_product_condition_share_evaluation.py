"""Isolated synthetic fixtures verify provenance and scoring order only."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("product_share_eval", HERE / "evaluate_product_condition_share_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
FIX_SPEC = importlib.util.spec_from_file_location("share_eval_fixtures", ROOT / "tests/test_lab_sku_scoped_html_trial_evaluation.py")
F = importlib.util.module_from_spec(FIX_SPEC)
FIX_SPEC.loader.exec_module(F)


def prepared_fixture(root):
    args = F.prepare(root)
    run_dir = args[1]
    for variant in R.VARIANTS[1:]:
        shutil.copyfile(run_dir / "structured-jev-predictions.jsonl", run_dir / (variant + "-predictions.jsonl"))
    R.E.write_json(run_dir / "sharing-contract.json", {"basis": "user_asserted",
        "eligible_forward_request_ids": ["synthetic-request-fp", "synthetic-request-fn"],
        "sold_out_rows_excluded_upstream": True})
    R.E.write_json(run_dir / "freeze.json", {"labels_read": False})
    R.E.write_json(run_dir / "summary.json", {"labels_read": False})
    return args


class ProductShareEvaluationTests(unittest.TestCase):
    def test_all_shared_variants_gated_before_reference_access(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = prepared_fixture(root)
            output = root / "score"
            original = R.E.route_references
            def checked(*a, **kw):
                self.assertTrue((output / "all-gates-frozen.json").is_file())
                self.assertTrue(all((output / v / "manifest.json").is_file() for v in R.VARIANTS))
                return original(*a, **kw)
            with patch.object(R.E, "route_references", side_effect=checked):
                result = R.run(*args, output)
            self.assertFalse(result["full_dataset_accuracy_measured"])
            self.assertEqual(result["variants"]["product-top1-jev"]["fn_correct_row_recovered"], 1)
            self.assertEqual(result["variants"]["product-top3-verified"]["remaining_fp"], 0)

    def test_ineligible_forward_changed_prediction_is_rejected(self):
        sources, values = F.fixture()
        baseline = {p["request_id"]: p for p in values}
        current = deepcopy(baseline)
        current["synthetic-request-fn"]["reason"] = "unapproved replacement"
        with self.assertRaisesRegex(ValueError, "exact baseline"):
            R.validate_shared_predictions(
                {r["request_id"]: r for r in sources["requests.jsonl"]},
                {d["document_id"]: d for d in sources["documents.jsonl"]},
                {t["task_id"]: t for t in sources["tasks.jsonl"]},
                current, baseline, {"synthetic-request-fp"}, "product-top1-jev")

    def test_shared_selected_row_citation_is_rejected(self):
        sources, values = F.fixture()
        baseline = {p["request_id"]: p for p in values}
        current = deepcopy(baseline)
        r = sources["requests.jsonl"][0]
        text = r["target_selected_axes_text"]
        current[r["request_id"]]["quote_refs"] = [{"document_id": r["target_document_id"],
            "field": "selected_axes", "start": 0, "end": len(text), "quote": text}]
        with self.assertRaisesRegex(ValueError, "varying AU selected row"):
            R.validate_shared_predictions(
                {r["request_id"]: r for r in sources["requests.jsonl"]},
                {d["document_id"]: d for d in sources["documents.jsonl"]},
                {t["task_id"]: t for t in sources["tasks.jsonl"]},
                current, baseline, set(baseline), "product-top3-jev")

    def test_reverse_eligibility_fails_before_reference_reads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = prepared_fixture(root)
            contract_path = args[1] / "sharing-contract.json"
            contract = json.loads(contract_path.read_text())
            contract["eligible_forward_request_ids"].append("not-an-original-forward-request")
            contract_path.write_text(json.dumps(contract))
            with patch.object(R.E, "route_references") as route, self.assertRaisesRegex(ValueError, "original forward"):
                R.run(*args, root / "score")
            route.assert_not_called()


if __name__ == "__main__":
    unittest.main()
