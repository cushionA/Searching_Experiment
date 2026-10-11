"""Synthetic structural fixtures only; no real SKU labels or model execution."""
import copy
import sys
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_condition_mask_v1 as M
import product_condition_sharing_v1 as S
from test_lab_sku_product_condition_sharing import PIN, binding, document, fixture, request, result


def prepared():
    values, docs, bindings, contract, baseline = fixture()
    cases, rows = [], []
    for index, req in enumerate(values):
        for task in bindings[req["request_id"]]:
            task["axis_key"] = req["axis_name"]
        task = bindings[req["request_id"]][0]
        cases.append({"case_id": task["case_id"], "source_sku_key": task["source_sku_key"],
                      "au_product_id": task["au_product_id"], "candidate_row_keys": [task["au_row_key"]],
                      "production_adoption_allowed": False})
        rows.append({"case_id": task["case_id"], "au_row_key": task["au_row_key"],
                     "all_original_axes_covered": True, "production_adoption_allowed": False,
                     "condition_bindings": [
                         {"kind": "model", "task_id": task["task_id"], "request_id": req["request_id"],
                          "direction": req["direction"], "axis_key": task["axis_key"]},
                         {"kind": "upstream_identical", "raw_condition": {"value": "既に確認済みの合成条件"}},
                     ]})
        baseline[req["request_id"]]["source_task_bindings"] = copy.deepcopy(bindings[req["request_id"]])
    return values, docs, bindings, contract, baseline, cases, rows


def run_mask(data=None, winner="なし", group_results=None):
    data = data or prepared()
    values, docs, bindings, contract, baseline, cases, rows = data
    plan = S.build_groups(values, docs, bindings, model_identity=PIN, content_version="mask-fixture-v1",
                          invariance_contract=contract)
    predicted = S.fan_out(plan, group_results if group_results is not None else result(plan, winner), baseline)
    return M.build_mask(plan, predicted, cases, rows), plan, predicted, data


def append_condition(data, *, axis="タッセル", wanted="あり", options=None, product=None,
                     case_index=0, color="合成別色", new_case=False):
    values, docs, bindings, contract, baseline, cases, rows = data
    doc = product or docs[values[0]["target_document_id"]]
    docs[doc["document_id"]] = doc
    added = request(doc, wanted, color=color, axis=axis, options=options)
    task = binding(added, 500 + len(values), doc["product_id"])
    task["axis_key"] = axis
    if not new_case:
        task.update({"case_id": cases[case_index]["case_id"], "source_sku_key": cases[case_index]["source_sku_key"],
                     "au_row_key": rows[case_index]["au_row_key"]})
    else:
        cases.append({"case_id": task["case_id"], "source_sku_key": task["source_sku_key"],
                      "au_product_id": task["au_product_id"], "candidate_row_keys": [task["au_row_key"]]})
        rows.append({"case_id": task["case_id"], "au_row_key": task["au_row_key"],
                     "all_original_axes_covered": True, "condition_bindings": []})
    target_row = rows[-1] if new_case else rows[case_index]
    target_row["condition_bindings"].append({"kind": "model", "request_id": added["request_id"],
                                           "task_id": task["task_id"], "direction": added["direction"], "axis_key": axis})
    values.append(added)
    bindings[added["request_id"]] = [task]
    contract["eligible_forward_request_ids"].append(added["request_id"])
    baseline[added["request_id"]] = {"request_id": added["request_id"], "request_sha256": added["request_sha256"],
        "selected_value": added["selected_value"], "source_request": copy.deepcopy(added),
        "source_task_bindings": copy.deepcopy([task]), "state": "unknown", "winning_value": None,
        "quote_refs": [], "scope_status": "unproved", "production_adoption_allowed": False}
    return added


class ProductConditionMaskContracts(unittest.TestCase):
    def test_explicit_negative_rejects_all_wanted_positive_cases_across_colors_and_sizes(self):
        mask, _, _, data = run_mask(winner="なし")
        case_ids = [v["case_id"] for v in data[5]]
        self.assertEqual(mask["dropped_case_ids"], case_ids[:2])
        self.assertEqual(mask["continued_case_ids"], case_ids[2:])
        self.assertEqual([r["condition_decision"] for r in mask["condition_rows"]], ["drop", "drop", "keep", "keep"])
        self.assertTrue(all(not r["semantic_conflict_proved"] for r in mask["condition_rows"]))
        self.assertEqual(mask["condition_rows"][0]["reason"], "shared_alternative_winner")

    def test_positive_keeps_only_matching_conditions_and_never_final_accepts(self):
        mask, _, _, data = run_mask(winner="あり")
        self.assertEqual(mask["dropped_case_ids"], [v["case_id"] for v in data[5][2:4]])
        self.assertTrue(all(r["final_accept"] is False for r in mask["case_rows"]))
        self.assertFalse(mask["final_accept_from_mask_allowed"])
        self.assertTrue(all(r["case_decision"] == "continue_to_remaining_sku_comparison"
                            for r in mask["case_rows"] if not r["early_drop"]))

    def test_zero_eligible_does_not_touch_noneligible_or_reverse_requests(self):
        data = prepared()
        data[3]["eligible_forward_request_ids"] = []
        before = copy.deepcopy(data)
        mask, _, _, _ = run_mask(data)
        self.assertEqual(mask["condition_rows"], [])
        self.assertEqual(mask["dropped_case_ids"], [])
        self.assertEqual(len(mask["continued_case_ids"]), 6)
        self.assertEqual(mask["counts"]["cases_without_eligible_product_global_condition"], 6)
        self.assertEqual(mask["saved_remaining_symmetric_check_count"], 0)
        self.assertEqual(data, before)

    def test_two_unmatched_conditions_use_conjunction_and_one_drop_is_sufficient(self):
        data = prepared()
        append_condition(data)
        values, docs, binds, contract, baseline, cases, rows = data
        plan = S.build_groups(values, docs, binds, model_identity=PIN, content_version="v1", invariance_contract=contract)
        decisions = result(plan, "あり")
        for group in plan["groups"]:
            if group["axis_name"] == "タッセル":
                decisions[group["group_id"]]["winning_value"] = "なし"
        predictions = S.fan_out(plan, decisions, baseline)
        mask = M.build_mask(plan, predictions, cases, rows)
        case = mask["case_rows"][0]
        self.assertTrue(case["early_drop"])
        self.assertEqual(len(case["eligible_request_ids"]), 2)
        self.assertEqual(len(case["dropped_request_ids"]), 1)

    def test_same_raw_axis_in_another_au_url_never_receives_rejection_spill(self):
        data = prepared()
        other_doc = document("fixed-au:other-mask-product", "999000002")
        append_condition(data, product=other_doc, axis="レース", new_case=True)
        values, docs, binds, contract, baseline, cases, rows = data
        plan = S.build_groups(values, docs, binds, model_identity=PIN, content_version="v1", invariance_contract=contract)
        decisions = result(plan, "なし")
        for group in plan["groups"]:
            if group["canonical_au_url"].endswith("999000002"):
                decisions[group["group_id"]]["winning_value"] = "あり"
        mask = M.build_mask(plan, S.fan_out(plan, decisions, baseline), cases, rows)
        self.assertIn(cases[-1]["case_id"], mask["continued_case_ids"])
        self.assertNotIn(cases[-1]["case_id"], mask["dropped_case_ids"])
        self.assertEqual(mask["ambiguities"], [])

    def test_all_full_original_bindings_remain_but_output_cases_are_bounded(self):
        mask, plan, _, data = run_mask()
        proof = mask["condition_rows"][0]
        self.assertEqual(proof["source_task_bindings"], plan["bindings"][proof["request_id"]])
        self.assertEqual(len(proof["source_task_bindings"]), 2)
        self.assertEqual(mask["counts"]["outside_output_case_source_binding_count"], 1)
        self.assertNotIn("synthetic-case-outside-subset", mask["dropped_case_ids"] + mask["continued_case_ids"])
        self.assertEqual(set(mask["dropped_case_ids"] + mask["continued_case_ids"]), {c["case_id"] for c in data[5]})

    def test_family_disagreement_on_same_url_axis_drops_instead_of_false_keep(self):
        data = prepared()
        append_condition(data, axis="レース", wanted="あり", options=["なし", "あり"], new_case=True)
        mask, _, _, data = run_mask(data, winner="あり")
        self.assertEqual(len(mask["ambiguities"]), 1)
        self.assertIn("multiple_unchanged_ordered_option_families", mask["ambiguities"][0]["reasons"])
        self.assertTrue(all(r["condition_decision"] == "drop" for r in mask["condition_rows"]))
        self.assertEqual(len(mask["dropped_case_ids"]), 5)
        self.assertTrue(all(not r["semantic_conflict_proved"] for r in mask["condition_rows"]))

    def test_more_than_one_au_row_keeps_same_shared_condition_outcome(self):
        data = prepared()
        values, docs, binds, contract, baseline, cases, rows = data
        doc = docs[values[0]["target_document_id"]]
        added = request(doc, "あり", color="新しい合成色", size="150×200")
        task = binding(added, 700)
        task.update({"case_id": cases[0]["case_id"], "source_sku_key": cases[0]["source_sku_key"], "axis_key": "レース"})
        values.append(added); binds[added["request_id"]] = [task]
        contract["eligible_forward_request_ids"].append(added["request_id"])
        baseline[added["request_id"]] = {"request_id": added["request_id"], "request_sha256": added["request_sha256"],
            "selected_value": "あり", "source_request": added, "source_task_bindings": [task],
            "state": "unknown", "winning_value": None, "quote_refs": [], "scope_status": "unproved", "production_adoption_allowed": False}
        cases[0]["candidate_row_keys"].append(task["au_row_key"])
        rows.append({"case_id": task["case_id"], "au_row_key": task["au_row_key"], "all_original_axes_covered": True,
                     "condition_bindings": [{"kind": "model", "task_id": task["task_id"], "request_id": added["request_id"],
                                              "direction": "rakuten_to_au", "axis_key": "レース"}]})
        mask, _, _, _ = run_mask(data, winner="なし")
        first = mask["case_rows"][0]
        self.assertTrue(first["early_drop"])
        self.assertEqual(len(first["dropped_request_ids"]), 2)

    def test_remaining_compute_counts_are_potential_and_identical_already_completed(self):
        data = prepared()
        # Move the existing noneligible model condition into a dropped case.
        values, docs, binds, contract, baseline, cases, rows = data
        symmetric = values[4]
        task = binds[symmetric["request_id"]][0]
        old_case = task["case_id"]
        task.update({"case_id": cases[0]["case_id"], "source_sku_key": cases[0]["source_sku_key"], "au_row_key": rows[0]["au_row_key"]})
        baseline[symmetric["request_id"]]["source_task_bindings"] = copy.deepcopy([task])
        row = next(r for r in rows if r["case_id"] == old_case)
        model = row["condition_bindings"].pop(0)
        rows[0]["condition_bindings"].append(model)
        mask, _, _, _ = run_mask(data)
        self.assertEqual(mask["counts"]["potential_skipped_future_model_binding_count"], 1)
        self.assertEqual(mask["saved_remaining_symmetric_check_count"], 1)
        self.assertTrue(mask["saved_remaining_symmetric_check_count_is_potential"])
        self.assertEqual(mask["counts"]["already_completed_upstream_identical_count"], 6)
        self.assertEqual(mask["counts"]["already_completed_upstream_identical_count_in_dropped_cases"], 2)
        self.assertEqual(mask["counts"]["measured_additional_avoided_inference_count"], 0)
        self.assertFalse(mask["runtime_savings_measured"])

    def test_stale_plan_changed_fanout_group_and_missing_request_fail_fast(self):
        _, plan, predictions, data = run_mask()
        altered = copy.deepcopy(plan)
        altered["groups"][0]["option_values"].reverse()
        with self.assertRaisesRegex(ValueError, "plan was modified"):
            M.build_mask(altered, predictions, data[5], data[6])
        for field, changed in [("request_sha256", "stale"), ("source_task_bindings", []),
                               ("group_sha256", "stale-group"), ("winning_value", "あり")]:
            altered = copy.deepcopy(predictions)
            altered[0][field] = changed
            with self.assertRaises(ValueError):
                M.build_mask(plan, altered, data[5], data[6])
        with self.assertRaisesRegex(ValueError, "every original"):
            M.build_mask(plan, predictions[:-1], data[5], data[6])

    def test_unsupported_unknown_group_is_explicit_drop_without_conflict_proof(self):
        mask, _, _, _ = run_mask(winner=S.UNKNOWN)
        self.assertEqual(len(mask["dropped_case_ids"]), 4)
        self.assertTrue(all(r["condition_decision"] == "drop" and r["shared_winning_value"] is None
                            and not r["semantic_conflict_proved"] for r in mask["condition_rows"]))

    def test_source_case_sku_au_url_and_row_bindings_cannot_change(self):
        _, plan, predictions, data = run_mask()
        for field, value in [("source_sku_key", "wrong-sku"), ("au_product_id", "999000009"), ("candidate_row_keys", [])]:
            cases = copy.deepcopy(data[5]); cases[0][field] = value
            with self.assertRaises(ValueError):
                M.build_mask(plan, predictions, cases, data[6])
        rows = copy.deepcopy(data[6]); rows[0]["condition_bindings"][0]["axis_key"] = "different-axis"
        with self.assertRaisesRegex(ValueError, "binding differs"):
            M.build_mask(plan, predictions, data[5], rows)

    def test_condition_case_and_complete_mask_hashes_are_verifiable_and_no_inputs_mutate(self):
        data = prepared()
        before = copy.deepcopy(data)
        mask, plan, predictions, _ = run_mask(data)
        self.assertEqual(data, before)
        self.assertEqual(mask["mask_sha256"], S.digest({k: v for k, v in mask.items() if k != "mask_sha256"}))
        self.assertEqual(mask["condition_rows_sha256"], S.digest(mask["condition_rows"]))
        self.assertEqual(mask["case_rows_sha256"], S.digest(mask["case_rows"]))
        for row in mask["condition_rows"]:
            self.assertEqual(row["condition_sha256"], S.digest({k: v for k, v in row.items() if k != "condition_sha256"}))
        for row in mask["case_rows"]:
            self.assertEqual(row["case_sha256"], S.digest({k: v for k, v in row.items() if k != "case_sha256"}))
        self.assertEqual(mask["input_sha256"]["sharing_plan"], S.digest(plan))
        self.assertEqual(mask["input_sha256"]["fanout_predictions"], S.digest({p["request_id"]: p for p in predictions}))


if __name__ == "__main__":
    unittest.main()
