from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("mixed_real_inputs", HERE / "prepare_mixed_gpu_sku_inputs_v1.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class MixedRealInputTests(unittest.TestCase):
    """Small synthetic fixtures are unit tests, never performance inputs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = {"itemInfo": {"shopUserId": "shop", "itemTitle": "fixture product",
            "hasFreeOptions": True, "hasPaidOptions": False,
            "itemComment": "<table><tr><td>Size</td><td>A (extra)</td></tr></table>",
            "extraItemComment": "Full description remains here.",
            "skuInfo": {"skuId": 22, "rowCount": 2, "columnCount": 1,
                "optionName": {"row": "Size", "column": "Color"},
                "rowNames": ["A (extra)", "B"], "columnNames": ["Blue"],
                "stockList": [[{"isSoldOut": True}], [{"isSoldOut": False}]]}}}
        (self.root / "item.json").write_text(json.dumps(self.raw), encoding="utf-8")
        self.store = M.CapturedStore(self.root)
        self.au, self.audit, self.flags = M.reconstruct_au(self.store, "11", "item.json")
        self.rak_axis = {"axis_index": 0, "axis_key": "size", "axis_label": "Size", "value": "A",
            "family_values": ["A", "B"], "value_span": {"raw_file": "fixture.html", "sha256": "f" * 64,
                "locator": {"kind": "html_text", "encoding": "utf-8"}, "start": 0, "end": 1, "quote": "A"}}
        self.case = {"case_id": "fixture-case", "dossier_id": "fixture-pair", "cohort": "fixture-dev",
            "au_product_id": "11", "rakuten": {"axes": [self.rak_axis], "url": "https://example.test/fixed/",
                "variant_id": "v1", "source_sku_key": "fixed#v1", "sku_record_key": "record-1"}}
        self.product = {"dossier_id": "fixture-pair", "au": self.au, "rakuten": {
            "url": self.case["rakuten"]["url"], "title": "fixture target", "descriptions": [{"text": "Full target text."}]}}

    def tearDown(self):
        self.tmp.cleanup()

    def coverage_fixture(self):
        membership = [
            {"case_id": "primary-a", "memberships": [{"stratum": "primary", "primary_score_eligible": True}]},
            {"case_id": "primary-b", "memberships": [{"stratum": "primary", "primary_score_eligible": True}]},
            {"case_id": "diagnostic", "memberships": [{"stratum": "diagnostic", "primary_score_eligible": False}]},
        ]
        cases = [{"case_id": row["case_id"], "memberships": deepcopy(row["memberships"]),
                  "selection_primary_score_eligible": row["memberships"][0]["primary_score_eligible"]}
                 for row in membership]
        plans = [{"case_id": "primary-a", "candidate_row_keys": ["r-a"]},
                 {"case_id": "primary-b", "candidate_row_keys": []},
                 {"case_id": "diagnostic", "candidate_row_keys": ["r-d"]}]
        rows = [{"case_id": "primary-a", "au_row_key": "r-a",
                 "condition_bindings": [{"kind": "model", "task_id": "t-a"}]},
                {"case_id": "diagnostic", "au_row_key": "r-d",
                 "condition_bindings": [{"kind": "upstream_identical"}]}]
        tasks = [{"task_id": "t-a", "case_id": "primary-a", "au_row_key": "r-a", "request_id": "q-a"}]
        requests = [{"request_id": "q-a"}]
        return membership, cases, plans, rows, tasks, requests

    def test_population_coverage_is_dynamic_and_accepts_complete_mixed_fixture(self):
        values = self.coverage_fixture()
        coverage = M.require_complete_population(self.root / "complete", *values, [], {"input": "sha"})
        self.assertEqual(coverage["requested_case_count"], 3)
        self.assertEqual(coverage["primary_membership_case_count"], 2)
        self.assertEqual(coverage["diagnostic_membership_case_count"], 1)
        self.assertEqual(coverage["covered_row_plan_count"], 2)
        self.assertEqual(coverage["covered_task_count"], 1)

    def test_population_coverage_rejects_missing_case_row_and_orphan_task(self):
        values = self.coverage_fixture()
        with self.assertRaisesRegex(ValueError, "prepared case coverage"):
            M.validate_population_coverage(values[0], values[1][:-1], *values[2:])
        values = self.coverage_fixture()
        values[3][0]["au_row_key"] = "wrong-row"
        with self.assertRaisesRegex(ValueError, "row plan coverage"):
            M.validate_population_coverage(*values)
        values = self.coverage_fixture()
        values[4][0]["request_id"] = "orphan-request"
        with self.assertRaisesRegex(ValueError, "task is not bound"):
            M.validate_population_coverage(*values)

    def test_non_ready_manifest_preserves_diagnostics(self):
        membership = [{"case_id": "missing-source"}]
        failure = {"case_id": "missing-source", "source_incomplete": True, "error_type": "ValueError"}
        out = self.root / "non-ready"
        with self.assertRaisesRegex(RuntimeError, "1 source reconstruction failure"):
            M.require_complete_population(out, membership, [], [], [], [], [], [failure], {"input": "sha"})
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertFalse(manifest["input_ready"])
        self.assertEqual(manifest["failure_count"], 1)
        self.assertEqual(M.read_rows(out / "failures.jsonl"), [failure])
        self.assertEqual(manifest["readiness_failure"], "source_reconstruction_failures")

    def test_population_mismatch_writes_non_ready_diagnostic_before_raising(self):
        values = self.coverage_fixture()
        values[1].pop()
        out = self.root / "bad-coverage"
        with self.assertRaisesRegex(RuntimeError, "prepared case coverage"):
            M.require_complete_population(out, *values, [], {"input": "sha"})
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertFalse(manifest["input_ready"])
        self.assertEqual(manifest["readiness_failure"], "population_coverage_mismatch")
        self.assertEqual(manifest["coverage_error_count"], 1)
        self.assertIn("prepared case coverage", M.read_rows(out / "coverage-errors.jsonl")[0]["error"])

    def plans(self, decision, handoffs=()):
        docs, requests, tasks, rows = {}, {}, [], []
        plan = M.add_new_plans(self.case, self.product, decision, list(handoffs), {"fixture": True}, docs, requests, tasks, rows)
        return plan, docs, requests, tasks, rows

    def test_parser_keeps_every_observed_matrix_cell_and_literal_spans(self):
        self.assertEqual([row["row_key"] for row in self.au["rows"]], ["au:11:22:0:0", "au:11:22:1:0"])
        self.assertEqual(len(self.audit["all_stock_cells"]), 2)
        self.assertTrue(self.audit["all_stock_cells"][0]["source_record_raw"]["stock"]["isSoldOut"])
        self.assertEqual(self.audit["raw_excluded_rows"], [])
        self.assertEqual(self.au["rows"][0]["axes"][0]["value"], "A (extra)")
        M.verify_record_spans(self.store, self.au)
        self.assertIn("options_capture_missing", self.flags)
        self.assertIsNone(self.audit["item_options_raw"])

    def test_frozen_products_preserve_rows_options_and_source_spans(self):
        case = {"case_id": "frozen-case", "dossier_id": "frozen-pair", "au_product_id": "11",
                "rakuten_selected": {"url": "https://example.test/item", "axes": []}}
        row = {"row_key": "au:11:22:0:0", "row_index": 0, "column_index": 0, "sku_id": 22,
               "axes": [{"axis_name": "Size", "value": "M", "value_span": {"raw_file": "item.json",
                   "sha256": "fixture", "quote": "M"}}]}
        purchase_ref = {"raw_file": "sku-arrays.jsonl", "sha256": "fixture", "quote": "Option"}
        frozen = {"dossier_id": "frozen-pair", "au_product": {"product_id": "11", "title": "AU",
                    "title_span": {"raw_file": "item.json", "sha256": "fixture", "quote": "AU"},
                    "axis_names": {"row": "Size"}}, "rakuten_product": {"url": case["rakuten_selected"]["url"],
                    "title": "Rakuten", "title_span": {"raw_file": "rakuten.html", "sha256": "fixture", "quote": "Rakuten"}},
                 "au_rows": [row], "au_description_lines": [], "rakuten_description_lines": [],
                 "au_purchase_option_lines": [{"text": "Option", "span": purchase_ref}]}
        model = M.frozen_product_to_model(frozen, case)
        self.assertEqual(model["au"]["rows"], [row])
        self.assertEqual(model["au"]["purchase_options"][0]["source_ref"], purchase_ref)
        self.assertEqual(model["au"]["title_source"], frozen["au_product"]["title_span"])
        sku_record = {"au_sku_cells_count": 1, "au_sku_cells_raw": [{"source_grain": {"sku_id": 22,
            "row_index": 0, "column_index": 0}, "axes_raw": [{"axis_name_raw": "Size", "value_raw": "M"}],
            "source_record_raw": {"stock": {"isSoldOut": True}}}], "au_product_options_raw": {"free_options": []}}
        self.assertEqual(M.validate_frozen_sku_rows(frozen, sku_record, case), [])
        self.assertTrue(sku_record["au_sku_cells_raw"][0]["source_record_raw"]["stock"]["isSoldOut"])
        self.assertEqual(M.validate_frozen_sku_rows(frozen, None, case), ["options_capture_missing"])

    def test_frozen_v7_archive_members_use_eval_cohort_directory(self):
        prefix = M.frozen_v7_align_prefix("v2")
        self.assertEqual(prefix + "decisions.jsonl",
            ".lab-output/sku-independent-real-20261011/eval-v2/align-v7-token/align/decisions.jsonl")
        self.assertEqual(M.frozen_v7_align_prefix("v3") + "handoff.jsonl",
            ".lab-output/sku-independent-real-20261011/eval-v3/align-v7-token/align/handoff.jsonl")
        with self.assertRaisesRegex(ValueError, "unsupported frozen v7"):
            M.frozen_v7_align_prefix("v4")

    def test_frozen_products_reject_row_key_or_complete_array_mismatch(self):
        case = {"au_product_id": "11"}
        frozen = {"au_rows": [{"row_key": "au:11:22:0:0", "axes": []}]}
        sku_record = {"au_sku_cells_count": 1, "au_sku_cells_raw": [{"source_grain": {"sku_id": 99,
            "row_index": 0, "column_index": 0}, "axes_raw": [], "source_record_raw": {}}],
            "au_product_options_raw": {}}
        with self.assertRaisesRegex(ValueError, "row key differs"):
            M.validate_frozen_sku_rows(frozen, sku_record, case)

    def test_matrix_missing_source_value_is_rejected_without_fabrication(self):
        raw = deepcopy(self.raw)
        raw["itemInfo"]["skuInfo"]["rowNames"] = ["A"]
        (self.root / "incomplete.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lacks its source axis value"):
            M.reconstruct_au(self.store, "11", "incomplete.json")

    def test_direct_nonidentical_core_requires_forward_and_all_reverse_axes(self):
        decision = {"case_id": "fixture-case", "decision": "matched", "top_row_key": "au:11:22:0:0",
            "reason": "all_conditions_aligned_on_one_row", "row_states": {"aligned": 1, "contradiction": 1}}
        plan, _, requests, tasks, rows = self.plans(decision)
        self.assertEqual(plan["total_fixed_au_row_count"], 2)
        self.assertEqual(plan["upstream_contradiction_row_count"], 1)
        self.assertEqual(len(tasks), 3)
        self.assertEqual(CounterDirection(tasks), {"rakuten_to_au": 1, "au_to_rakuten": 2})
        self.assertTrue(rows[0]["all_original_axes_covered"])
        self.assertTrue(all(binding["kind"] == "model" for binding in rows[0]["condition_bindings"]))
        forward = next(request for request in requests.values() if request["direction"] == "rakuten_to_au")
        self.assertEqual(forward["option_values"], ["A", "B"])
        self.assertEqual(forward["selected_value"], "A")
        self.assertFalse(forward["upstream_core_is_proof"])

    def test_pending_handoff_joins_original_keys_and_keeps_reverse_au_only(self):
        row = self.au["rows"][0]
        handoff = {"case_id": self.case["case_id"], "au_product_id": "11", "au_row_key": row["row_key"],
            "rakuten_sku": {key: self.case["rakuten"][key] for key in ("url", "variant_id", "source_sku_key", "sku_record_key")},
            "au_row_axes": [{key: axis[key] for key in ("axis_name", "value", "value_span")} for axis in row["axes"]],
            "au_axis_options": {"Size": ["A (extra)", "B"], "Color": ["Blue"]},
            "conditions": [{"kind": "rakuten", "axis_key": "size", "value": "A", "au_axis": "Size",
                "au_value": "A (extra)", "status": "residual", "core": {"identical": True},
                "residual": [{"side": "au", "text": "extra", "source_value": "A (extra)", "verify_with": "rakuten"}]},
                {"kind": "au_only", "au_axis": "Color", "au_value": "Blue", "status": "one_sided"}]}
        decision = {"case_id": self.case["case_id"], "decision": "unmatched", "top_row_key": None,
            "reason": "pending_description_check", "row_states": {"pending_description_check": 1, "contradiction": 1}}
        _, _, _, tasks, rows = self.plans(decision, [handoff])
        self.assertEqual(len(tasks), 3)
        self.assertEqual(next(task for task in tasks if task["axis_key"] == "Color")["origin"], "v7_au_only")
        self.assertTrue(all(task["raw_condition"]["value"] in task["option_values"] for task in tasks))
        self.assertTrue(rows[0]["all_original_axes_covered"])
        bad = deepcopy(handoff)
        bad["au_row_axes"][0]["value"] = "A"
        with self.assertRaisesRegex(ValueError, "cannot join producer"):
            self.plans(decision, [bad])

    def test_aggregate_competitor_mismatch_cannot_be_adopted(self):
        bad = {"decision": "matched", "top_row_key": "au:11:22:0:0", "reason": "all_conditions_aligned_on_one_row",
               "row_states": {"aligned": 1}}
        with self.assertRaisesRegex(ValueError, "aggregate row counts differ"):
            self.plans(bad)

    def test_nfkc_whole_value_only_is_host_proof(self):
        self.case["rakuten"]["axes"][0]["value"] = "Ａ (extra)"
        self.case["rakuten"]["axes"][0]["family_values"] = ["Ａ (extra)", "B"]
        decision = {"decision": "matched", "top_row_key": "au:11:22:0:0", "reason": "all_conditions_aligned_on_one_row",
               "row_states": {"aligned": 1, "contradiction": 1}}
        _, _, _, tasks, rows = self.plans(decision)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(rows[0]["condition_bindings"][0]["kind"], "upstream_identical")
        self.assertEqual(tasks[0]["axis_key"], "Color")

    def test_raw_archive_collision_and_sha_changes_are_rejected(self):
        first, second = self.root / "first.zip", self.root / "second.zip"
        for path, body in ((first, b"first"), (second, b"second")):
            with ZipFile(path, "w") as zipped:
                zipped.writestr("capture/item.json", body)
        store = M.CapturedStore(self.root, [first, second])
        with self.assertRaisesRegex(ValueError, "conflicting original raw bytes"):
            store.raw("capture\\item.json")
        with self.assertRaisesRegex(ValueError, "SHA256 differs"):
            self.store.raw("item.json", "0" * 64)


def CounterDirection(tasks):
    return {direction: sum(task["direction"] == direction for task in tasks) for direction in {task["direction"] for task in tasks}}


if __name__ == "__main__":
    unittest.main()
