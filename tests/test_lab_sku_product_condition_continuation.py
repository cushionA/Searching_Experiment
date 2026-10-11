"""Integrity/binary queue tests; synthetic fixtures stay separate from SKU data."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_condition_continuation_v1 as C
import product_condition_mask_v1 as M
import product_condition_sharing_v1 as S
from test_lab_sku_product_condition_mask import prepared, run_mask


def rehash_mask(mask):
    for row in mask["condition_rows"]:
        row["condition_sha256"] = S.digest({k: v for k, v in row.items() if k != "condition_sha256"})
    for row in mask["case_rows"]:
        row["case_sha256"] = S.digest({k: v for k, v in row.items() if k != "case_sha256"})
    mask["condition_rows_sha256"] = S.digest(mask["condition_rows"])
    mask["case_rows_sha256"] = S.digest(mask["case_rows"])
    mask["mask_sha256"] = S.digest({k: v for k, v in mask.items() if k != "mask_sha256"})
    return mask


def source_files(directory, mask, cases, rows):
    directory.mkdir()
    mask_file = directory / "mask.json"
    mask_file.write_text(json.dumps(mask, ensure_ascii=False), encoding="utf-8")
    case_file, row_file = directory / "case_plans.jsonl", directory / "row_plans.jsonl"
    for path, values in [(case_file, cases), (row_file, rows)]:
        path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8")
    return mask_file, case_file, row_file


class ProductConditionContinuationContracts(unittest.TestCase):
    def test_binary_drop_filters_original_case_and_row_records_without_final_acceptance(self):
        mask, _, _, data = run_mask()
        queue = C.build_continuation(mask, data[5], data[6])
        self.assertEqual(queue["case_plans"], data[5][2:])
        self.assertEqual(queue["row_plans"], data[6][2:])
        self.assertEqual(queue["removed_case_ids"], mask["dropped_case_ids"])
        self.assertEqual(queue["continued_case_ids"], mask["continued_case_ids"])
        self.assertEqual(queue["next_stage"], "remaining_sku_comparison")
        self.assertFalse(queue["final_accept_from_queue_allowed"])
        self.assertFalse(queue["production_adoption_allowed"])
        self.assertTrue(all("final_accept" not in original for original in queue["case_plans"]))

    def test_positive_mask_still_only_continues_remaining_comparison(self):
        mask, _, _, data = run_mask(winner="あり")
        queue = C.build_continuation(mask, data[5], data[6])
        self.assertEqual(queue["case_plans"], data[5][:2] + data[5][4:])
        self.assertFalse(queue["final_accept_from_queue_allowed"])
        self.assertEqual(queue["counts"]["measured_additional_avoided_inference_count"], 0)

    def test_no_eligible_conditions_retains_all_cases_and_rows(self):
        data = prepared()
        data[3]["eligible_forward_request_ids"] = []
        mask, _, _, _ = run_mask(data)
        queue = C.build_continuation(mask, data[5], data[6])
        self.assertEqual(queue["case_plans"], data[5])
        self.assertEqual(queue["row_plans"], data[6])
        self.assertEqual(queue["removed_case_ids"], [])

    def test_all_drop_can_produce_empty_valid_continuation_queue(self):
        # Select only cases covered by an eligible condition, then unknown drops all.
        mask, plan, predictions, data = run_mask(winner=S.UNKNOWN)
        cases, rows = data[5][:4], data[6][:4]
        selected = M.build_mask(plan, predictions, cases, rows)
        queue = C.build_continuation(selected, cases, rows)
        self.assertEqual(queue["case_plans"], [])
        self.assertEqual(queue["row_plans"], [])
        self.assertEqual(queue["counts"]["removed_case_count"], 4)

    def test_interleaved_row_order_and_multiple_candidate_rows_are_preserved(self):
        data = prepared()
        # An original case can include an AU row with no model binding.
        extra = {"case_id": data[5][2]["case_id"], "au_row_key": "synthetic-extra-au-row",
                 "all_original_axes_covered": True, "condition_bindings": []}
        data[5][2]["candidate_row_keys"].append(extra["au_row_key"])
        data[6].insert(1, extra)
        mask, _, _, _ = run_mask(data)
        queue = C.build_continuation(mask, data[5], data[6])
        self.assertEqual(queue["row_plans"], [extra] + data[6][3:])
        self.assertEqual(queue["case_plans"][0]["candidate_row_keys"], data[5][2]["candidate_row_keys"])

    def test_mask_tampering_and_record_collection_hash_tampering_are_rejected(self):
        mask, _, _, data = run_mask()
        changed = copy.deepcopy(mask)
        changed["continued_case_ids"].reverse()
        with self.assertRaisesRegex(ValueError, "mask_sha256"):
            C.build_continuation(changed, data[5], data[6])
        changed = copy.deepcopy(mask)
        changed["case_rows"][0]["reason"] = "changed-record"
        changed["mask_sha256"] = S.digest({k: v for k, v in changed.items() if k != "mask_sha256"})
        with self.assertRaisesRegex(ValueError, "row collection"):
            C.build_continuation(changed, data[5], data[6])

    def test_modified_originals_missing_records_and_reordered_inputs_rejected(self):
        mask, _, _, data = run_mask()
        changed = copy.deepcopy(data[5]); changed[0]["source_sku_key"] = "different-original-sku"
        for cases, rows in [(changed, data[6]), (data[5][1:], data[6]),
                            (data[5], data[6][::-1]), (data[5], data[6][:-1])]:
            with self.assertRaisesRegex(ValueError, "semantic SHA"):
                C.build_continuation(mask, cases, rows)
        with self.assertRaisesRegex(ValueError, "order exactly"):
            C.build_continuation(mask, data[5][::-1], data[6])

    def test_rehashed_inconsistent_early_drop_or_final_acceptance_rejected(self):
        mask, _, _, data = run_mask()
        for field, value in [("early_drop", False), ("case_decision", "continue_to_remaining_sku_comparison"),
                             ("final_accept", True)]:
            changed = copy.deepcopy(mask)
            changed["case_rows"][0][field] = value
            rehash_mask(changed)
            with self.assertRaisesRegex(ValueError, "continuation semantics"):
                C.build_continuation(changed, data[5], data[6])

    def test_rehashed_case_condition_proofs_must_cover_and_match_original_conditions(self):
        mask, _, _, data = run_mask()
        changed = copy.deepcopy(mask)
        changed["case_rows"][0]["condition_refs"][0]["group_sha256"] = "stale-group-version"
        rehash_mask(changed)
        with self.assertRaisesRegex(ValueError, "proof reference"):
            C.build_continuation(changed, data[5], data[6])
        changed = copy.deepcopy(mask)
        changed["case_rows"][0]["eligible_request_ids"] = []
        rehash_mask(changed)
        with self.assertRaisesRegex(ValueError, "lost or added"):
            C.build_continuation(changed, data[5], data[6])

    def test_changed_source_originals_inside_rehashed_mask_rejected(self):
        mask, _, _, data = run_mask()
        changed = copy.deepcopy(mask)
        changed["case_rows"][2]["source_case_plan"]["source_sku_key"] = "wrong-original"
        rehash_mask(changed)
        with self.assertRaisesRegex(ValueError, "source originals"):
            C.build_continuation(changed, data[5], data[6])

    def test_potential_future_savings_never_claim_historical_avoided_inference(self):
        mask, _, _, data = run_mask()
        queue = C.build_continuation(mask, data[5], data[6])
        self.assertFalse(queue["runtime_savings_measured"])
        self.assertEqual(queue["counts"]["measured_additional_avoided_inference_count"], 0)
        self.assertTrue(all(k.startswith("potential_") for k in queue["potential_future_check_savings"]))
        changed = copy.deepcopy(mask)
        changed["counts"]["measured_additional_avoided_inference_count"] = 10
        rehash_mask(changed)
        with self.assertRaisesRegex(ValueError, "zero newly avoided"):
            C.build_continuation(changed, data[5], data[6])

    def test_queue_hashes_and_original_input_no_aliasing(self):
        mask, _, _, data = run_mask()
        before = copy.deepcopy((mask, data[5], data[6]))
        queue = C.build_continuation(mask, data[5], data[6])
        self.assertEqual(queue["queue_sha256"], S.digest({k: v for k, v in queue.items() if k != "queue_sha256"}))
        self.assertEqual(queue["case_plans_sha256"], S.digest(queue["case_plans"]))
        self.assertEqual(queue["row_plans_sha256"], S.digest(queue["row_plans"]))
        queue["case_plans"][0]["source_sku_key"] = "output-only-mutation"
        queue["row_plans"][0]["condition_bindings"].clear()
        self.assertEqual((mask, data[5], data[6]), before)

    def test_export_freezes_source_file_provenance_output_shas_and_removed_ids(self):
        mask, _, _, data = run_mask()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            inputs = source_files(root / "inputs", mask, data[5], data[6])
            before = [p.read_bytes() for p in inputs]
            manifest = C.export_continuation(*inputs, root / "output")
            self.assertEqual(before, [p.read_bytes() for p in inputs])
            self.assertTrue(manifest["input_files_sha256_unchanged"])
            self.assertEqual(manifest["manifest_sha256"], S.digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}))
            self.assertEqual(json.loads((root / "output/removed-case-ids.json").read_text()), mask["dropped_case_ids"])
            for filename, wanted in manifest["output_sha256"].items():
                self.assertEqual(hashlib.sha256((root / "output" / filename).read_bytes()).hexdigest(), wanted)
            cases = [json.loads(v) for v in (root / "output/case_plans.jsonl").read_text().splitlines()]
            self.assertEqual(cases, data[5][2:])
            self.assertEqual(manifest["counts"]["continued_case_count"], 4)

    def test_export_refuses_existing_directory_and_does_not_create_output_for_invalid_input(self):
        mask, _, _, data = run_mask()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            inputs = source_files(root / "inputs", mask, data[5], data[6])
            (root / "existing").mkdir()
            with self.assertRaises(FileExistsError):
                C.export_continuation(*inputs, root / "existing")
            mask["continued_case_ids"].reverse()
            inputs[0].write_text(json.dumps(mask), encoding="utf-8")
            with self.assertRaises(ValueError):
                C.export_continuation(*inputs, root / "invalid")
            self.assertFalse((root / "invalid").exists())

    def test_cli_exports_without_model_or_label_inputs(self):
        mask, _, _, data = run_mask()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            inputs = source_files(root / "inputs with spaces", mask, data[5], data[6])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = C.main(["--mask", str(inputs[0]), "--case-plans", str(inputs[1]),
                               "--row-plans", str(inputs[2]), "--output-dir", str(root / "queue")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["counts"]["removed_case_count"], 2)
            manifest = json.loads((root / "queue/manifest.json").read_text())
            self.assertFalse(manifest["labels_read"])
            self.assertFalse(manifest["model_inference_run"])


if __name__ == "__main__":
    unittest.main()
