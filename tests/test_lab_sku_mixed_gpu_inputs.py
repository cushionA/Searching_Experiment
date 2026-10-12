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
