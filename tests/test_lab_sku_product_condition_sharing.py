"""Structural tests only; these synthetic fixtures never become SKU data."""
import copy
import sys
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
import product_condition_sharing_v1 as S
from integrate_claude_description_v1 import resolve_prediction


PIN = {"model_id": "synthetic-ensemble", "revision": "fixed-policy-sha",
       "precision": "fp32", "components": [{"model_id": "synthetic", "revision": "fixed"}]}


def document(identity="fixed-au:synthetic", product_id="999000001", side="au", title=None):
    value = {"document_id": identity, "product_id": product_id, "side": side, "fixed_page": True,
             "url": None if side == "au" else "https://example.test/synthetic",
             "title": {"text": title or "合成単体カーテン🙂", "source_refs": []},
             "description": {"text": "現在の商品にはレースが付いていません。幅ごとの情報も含みます。",
                             "complete": True, "segments": []},
             "input_ref": {"path": "synthetic-source-only", "version": "v1"},
             "scope_applicability_proved": False}
    value["document_sha256"] = S.digest(value)
    return value


def rehash_doc(value):
    value["document_sha256"] = S.digest({k: v for k, v in value.items() if k != "document_sha256"})
    return value


def request(doc, wanted, color="合成赤", size="100×80", axis="レース", options=None, direction="rakuten_to_au"):
    axes = [{"axis_name": "カラー", "value": color}, {"axis_name": "サイズ", "value": size}]
    semantic = {"direction": direction, "axis_name": axis, "selected_value": wanted,
                "option_values": options or ["あり", "なし"], "target_document_id": doc["document_id"],
                "target_document_sha256": doc["document_sha256"], "target_selected_axes": axes,
                "target_selected_axes_text": "\n".join(a["axis_name"] + "：" + a["value"] for a in axes)}
    checksum = S.digest(semantic)
    return {**semantic, "request_sha256": checksum, "request_id": "generic-description:" + checksum,
            "applicability_required": True, "selected_value_is_binding_only": True,
            "production_adoption_allowed": False}


def binding(req, number, product_id="999000001", outside_subset=False):
    return {"task_id": "synthetic-task-" + str(number), "request_id": req["request_id"],
            "direction": req["direction"], "raw_condition": {
                "axis_label" if req["direction"] == "rakuten_to_au" else "axis_name": req["axis_name"],
                "value": req["selected_value"], "raw_span": {"quote": req["selected_value"]}},
            "option_values": copy.deepcopy(req["option_values"]), "au_product_id": product_id,
            "au_row_key": "synthetic-au-row-" + str(number),
            "case_id": "synthetic-case-outside-subset" if outside_subset else "synthetic-case-" + str(number),
            "source_sku_key": "synthetic-rakuten-sku-" + str(number)}


def fixture():
    au = document()
    rak = document("fixed-rakuten:synthetic", None, "rakuten")
    values = [request(au, "あり", "合成赤", "100×80"), request(au, "あり", "合成青", "150×100"),
              request(au, "なし", "合成赤", "100×80"), request(au, "なし", "合成緑", "150×120")]
    values += [request(au, "100×80", axis="サイズ", options=["100×80", "150×100"]),
               request(rak, "なし", direction="au_to_rakuten")]
    documents = {v["document_id"]: v for v in [au, rak]}
    bindings = {v["request_id"]: [binding(v, i)] for i, v in enumerate(values)}
    # Exact-input dedup also needs to preserve a shared task outside the subset.
    bindings[values[0]["request_id"]].append(binding(values[0], 90, outside_subset=True))
    contract = {"basis": "user_asserted", "statement": "この未対応条件はAUページの全サイズ・色で固定。",
                "sold_out_rows_excluded_upstream": True,
                "eligible_forward_request_ids": [v["request_id"] for v in values[:4]]}
    baseline = {v["request_id"]: {"request_id": v["request_id"], "request_sha256": v["request_sha256"],
                "selected_value": v["selected_value"], "source_request": copy.deepcopy(v),
                "source_task_bindings": copy.deepcopy(bindings[v["request_id"]]), "state": "unknown",
                "winning_value": None, "quote_refs": [], "scope_status": "unproved",
                "production_adoption_allowed": False, "diagnostic": {"keep": ["all", "fields"]}}
                for v in values}
    return values, documents, bindings, contract, baseline


def build(values=None, documents=None, bindings=None, contract=None, pin=None, version="synthetic-render-v1"):
    defaults = fixture()
    return S.build_groups(values if values is not None else defaults[0],
                          documents if documents is not None else defaults[1],
                          bindings if bindings is not None else defaults[2],
                          model_identity=pin or PIN, content_version=version,
                          invariance_contract=contract if contract is not None else defaults[3])


def result(plan, winner="なし", quotes=True, status="ok"):
    results = {}
    for group in plan["groups"]:
        doc = plan["documents"][group["target_document_id"]]
        refs = [{"document_id": doc["document_id"], "document_sha256": doc["document_sha256"],
                 "field": "description", "start": 0, "end": len(doc["description"]["text"]),
                 "quote": doc["description"]["text"], "source_html_block_id": "synthetic-block-only"}] if quotes else []
        results[group["group_id"]] = {"group_sha256": group["group_sha256"], "status": status,
                                      "winning_value": winner, "quote_refs": refs}
    return results


class ProductConditionSharingContracts(unittest.TestCase):
    def test_wanted_values_and_different_au_rows_share_one_product_input(self):
        plan = build()
        self.assertEqual(len(plan["groups"]), 1)
        self.assertEqual(len(plan["groups"][0]["request_ids"]), 4)
        self.assertEqual(len(plan["passthrough_request_ids"]), 2)
        model = plan["model_requests"][0]
        self.assertEqual([v["label"] for v in model["choices"]], ["あり", "なし", S.UNKNOWN])
        self.assertNotIn("selected_value", model)
        for text in ["合成赤", "合成青", "合成緑", "100×80", "150×100", "150×120"]:
            self.assertNotIn(text, model["state"])
        self.assertNotIn("選択行", model["state"])
        self.assertNotIn("選択行", model["choices"][-1]["description"])

    def test_savings_account_for_existing_exact_input_dedup(self):
        savings = build()["savings"]
        self.assertEqual(savings["eligible_original_request_count"], 4)
        self.assertEqual(savings["eligible_old_exact_input_count"], 3)
        self.assertEqual(savings["shared_group_count"], 1)
        self.assertEqual(savings["old_exact_dedup_saved_request_count"], 1)
        self.assertEqual(savings["additional_saved_model_input_count"], 2)
        self.assertEqual(savings["eligible_original_candidate_pair_count"], 12)
        self.assertEqual(savings["eligible_old_exact_candidate_pair_count"], 9)
        self.assertEqual(savings["shared_candidate_pair_count"], 3)
        self.assertEqual(savings["additional_saved_candidate_pair_count"], 6)

    def test_url_null_is_bound_to_product_id_and_url_mismatch_rejected(self):
        self.assertEqual(build()["groups"][0]["canonical_au_url"], "https://wowma.jp/item/999000001")
        doc = document()
        doc["url"] = "https://wowma.jp/item/999000002"
        with self.assertRaisesRegex(ValueError, "URL and product_id"):
            S.canonical_au_url(doc)
        doc["url"] = "https://elsewhere.test/item/999000001"
        with self.assertRaises(ValueError):
            S.canonical_au_url(doc)
        doc["url"] = "https://wowma.jp/item/999000001/?tracking=1#sku"
        self.assertEqual(S.canonical_au_url(doc), "https://wowma.jp/item/999000001")

    def test_eligibility_cannot_be_inferred_from_forward_or_axis_names(self):
        values, docs, bindings, contract, _ = fixture()
        contract["eligible_forward_request_ids"] = []
        plan = build(values, docs, bindings, contract)
        self.assertEqual(plan["groups"], [])
        self.assertEqual(plan["passthrough_request_ids"], [v["request_id"] for v in values])

    def test_unknown_duplicate_and_reverse_eligibility_rejected(self):
        values, docs, bindings, contract, _ = fixture()
        for identifiers in [["missing-id"], [values[0]["request_id"]] * 2, [values[-1]["request_id"]]]:
            modified = {**contract, "eligible_forward_request_ids": identifiers}
            with self.assertRaises(ValueError):
                build(values, docs, bindings, modified)

    def test_invariance_requires_explicit_user_statement_and_upstream_stock_contract(self):
        values, docs, bindings, contract, _ = fixture()
        for field, changed in [("basis", "inferred"), ("statement", ""), ("sold_out_rows_excluded_upstream", False)]:
            with self.assertRaises(ValueError):
                build(values, docs, bindings, {**contract, field: changed})

    def test_mixed_stock_retention_requires_exact_row_policy_audit_and_keeps_onesided_scope(self):
        values, docs, bindings, contract, _ = fixture()
        mixed = {**contract,
            "statement": ("AU one_sided source proof; only Rakuten one_sided with null AU axis is eligible "
                          "to share; all other and reverse requirements remain row-specific."),
            "sold_out_rows_excluded_upstream": False,
            "row_policy_audit": {"schema_version": "mixed-real-gpu-sku-inputs-v1",
                "row_policy": "all captured stockList cells, as in the pinned producer; stock is audit-only",
                "all_captured_rows_retained": True, "stock_is_identity_evidence": False}}
        lookup = {value["request_id"]: value for value in values}
        self.assertEqual(S._validate_contract(mixed, lookup), set(mixed["eligible_forward_request_ids"]))
        plan = build(values, docs, bindings, mixed)
        self.assertEqual(len(plan["groups"]), 1)
        self.assertEqual(set(plan["groups"][0]["request_ids"]), set(mixed["eligible_forward_request_ids"]))
        self.assertEqual(len(plan["passthrough_request_ids"]), len(values) - len(mixed["eligible_forward_request_ids"]))

    def test_mixed_stock_retention_rejects_missing_or_inaccurate_audit(self):
        values, docs, bindings, contract, _ = fixture()
        statement = "only Rakuten one_sided with null AU axis is eligible to share; all other and reverse requirements remain row-specific"
        good = {"schema_version": "mixed-real-gpu-sku-inputs-v1",
            "row_policy": "all captured stockList cells, as in the pinned producer; stock is audit-only",
            "all_captured_rows_retained": True, "stock_is_identity_evidence": False}
        for audit in (None, {**good, "all_captured_rows_retained": False},
                      {**good, "stock_is_identity_evidence": True}, {**good, "row_policy": "row filtering occurred"}):
            modified = {**contract, "statement": statement, "sold_out_rows_excluded_upstream": False}
            if audit is not None:
                modified["row_policy_audit"] = audit
            with self.assertRaisesRegex(ValueError, "row-policy audit"):
                build(values, docs, bindings, modified)
        with self.assertRaisesRegex(ValueError, "one_sided source scope"):
            build(values, docs, bindings, {**contract, "statement": "share all options",
                "sold_out_rows_excluded_upstream": False, "row_policy_audit": good})

    def test_product_identity_axis_ordered_options_and_document_identity_isolate_groups(self):
        doc = document()
        other = document("fixed-au:other", "999000002")
        alias = document("fixed-au:alias")
        values = [request(doc, "あり"), request(other, "あり"), request(alias, "あり"),
                  request(doc, "あり", axis=" レース"), request(doc, "あり", options=["なし", "あり"])]
        docs = {d["document_id"]: d for d in [doc, other, alias]}
        binds = {r["request_id"]: [binding(r, i, docs[r["target_document_id"]]["product_id"])] for i, r in enumerate(values)}
        contract = {**fixture()[3], "eligible_forward_request_ids": [r["request_id"] for r in values]}
        self.assertEqual(len(build(values, docs, binds, contract)["groups"]), 5)

    def test_source_and_model_and_content_versions_invalidate_shared_cache(self):
        initial = build()["groups"][0]
        self.assertNotEqual(initial["model_cache_key"], build(pin={**PIN, "revision": "next-policy"})["groups"][0]["model_cache_key"])
        self.assertNotEqual(initial["model_cache_key"], build(version="synthetic-render-v2")["groups"][0]["model_cache_key"])
        values, docs, binds, contract, _ = fixture()
        doc = docs[values[0]["target_document_id"]]
        doc["input_ref"]["version"] = "v2"
        rehash_doc(doc)
        new_values, new_binds = [], {}
        eligible = []
        for index, old in enumerate(values):
            changed = request(docs[old["target_document_id"]], old["selected_value"], old["target_selected_axes"][0]["value"],
                              old["target_selected_axes"][1]["value"], old["axis_name"], old["option_values"], old["direction"])
            new_values.append(changed)
            new_binds[changed["request_id"]] = [binding(changed, index)]
            if old["request_id"] in contract["eligible_forward_request_ids"]:
                eligible.append(changed["request_id"])
        modified = build(new_values, docs, new_binds, {**contract, "eligible_forward_request_ids": eligible})
        self.assertNotEqual(initial["model_cache_key"], modified["groups"][0]["model_cache_key"])

    def test_document_or_request_tampering_and_wrong_title_collisions_fail_fast(self):
        values, docs, binds, contract, _ = fixture()
        docs[values[0]["target_document_id"]]["title"]["text"] = "変えた原文"
        with self.assertRaisesRegex(ValueError, "document semantic SHA"):
            build(values, docs, binds, contract)
        values, docs, binds, contract, _ = fixture()
        values[0]["selected_value"] = "なし"
        with self.assertRaisesRegex(ValueError, "request identity"):
            build(values, docs, binds, contract)
        doc, alias = document(), document("fixed-au:alias", title="同じURLに別の商品名")
        values = [request(doc, "あり"), request(alias, "あり")]
        binds = {r["request_id"]: [binding(r, i)] for i, r in enumerate(values)}
        contract = {**fixture()[3], "eligible_forward_request_ids": [r["request_id"] for r in values]}
        with self.assertRaisesRegex(ValueError, "conflicting titles"):
            build(values, {d["document_id"]: d for d in [doc, alias]}, binds, contract)

    def test_original_binding_conditions_and_fixed_product_cannot_change(self):
        values, docs, binds, contract, _ = fixture()
        binds[values[0]["request_id"]][0]["au_product_id"] = "999000099"
        with self.assertRaisesRegex(ValueError, "fixed AU product"):
            build(values, docs, binds, contract)
        binds[values[0]["request_id"]][0]["au_product_id"] = "999000001"
        binds[values[0]["request_id"]][0]["raw_condition"]["value"] = "なし"
        with self.assertRaisesRegex(ValueError, "raw condition"):
            build(values, docs, binds, contract)

    def test_explicit_negative_option_is_reused_across_wanted_values_and_rows(self):
        values, docs, binds, contract, baseline = fixture()
        plan = build(values, docs, binds, contract)
        output = S.fan_out(plan, result(plan, "なし"), baseline)
        self.assertEqual(len(output), len(values))
        for expected, predicted in zip(values[:4], output[:4], strict=True):
            should_support = expected["selected_value"] == "なし"
            self.assertEqual(predicted["state"], "support" if should_support else "unknown")
            self.assertEqual(predicted["winning_value"], "なし")
            self.assertEqual(predicted["source_request"], expected)
            self.assertEqual(predicted["source_task_bindings"], binds[expected["request_id"]])
            resolved, reason, refs = resolve_prediction(predicted, expected, docs)
            self.assertEqual(resolved, "support" if should_support else "unknown")
            if not should_support:
                self.assertEqual(reason, "alternative_winner_is_not_conflict_proof")
                self.assertEqual(predicted["quote_refs"], [])
                self.assertEqual(refs, [])
        self.assertEqual(output[0]["source_task_bindings"][1]["case_id"], "synthetic-case-outside-subset")

    def test_positive_option_also_fans_out_and_preserves_literal_description_refs(self):
        values, docs, binds, contract, baseline = fixture()
        plan = build(values, docs, binds, contract)
        decision = result(plan, "あり")
        predicted = S.fan_out(plan, decision, baseline)
        self.assertEqual([p["state"] for p in predicted[:4]], ["support", "support", "unknown", "unknown"])
        self.assertEqual(predicted[0]["quote_refs"], decision[plan["groups"][0]["group_id"]]["quote_refs"])
        self.assertTrue(all(ref["field"] != "selected_axes" for ref in predicted[0]["quote_refs"]))
        self.assertEqual(predicted[0]["scope_status"], "unproved")
        self.assertFalse(predicted[0]["production_adoption_allowed"])

    def test_reverse_and_ineligible_forward_predictions_are_complete_baseline_copies(self):
        values, docs, binds, contract, baseline = fixture()
        plan = build(values, docs, binds, contract)
        output = S.fan_out(plan, result(plan), baseline)
        for index in [4, 5]:
            self.assertEqual(output[index], baseline[values[index]["request_id"]])
            self.assertIsNot(output[index], baseline[values[index]["request_id"]])

    def test_unknown_errors_and_ties_drop_without_invalid_gate_winner(self):
        values, docs, binds, contract, baseline = fixture()
        plan = build(values, docs, binds, contract)
        decisions = [result(plan, S.UNKNOWN), result(plan, None), result(plan, "あり", status="error")]
        tied = result(plan, None)
        tied[plan["groups"][0]["group_id"]]["scores"] = {"あり": 1, "なし": 1, S.UNKNOWN: 0}
        decisions.append(tied)
        for decision in decisions:
            output = S.fan_out(plan, decision, baseline)
            self.assertEqual([p["state"] for p in output[:4]], ["unknown"] * 4)
            self.assertTrue(all(p["winning_value"] is None and p["quote_refs"] == [] for p in output[:4]))
            for req, pred in zip(values[:4], output[:4], strict=True):
                self.assertEqual(resolve_prediction(pred, req, docs)[0], "unknown")

    def test_scores_classify_complete_options_once_and_reject_partial_nonfinite_scores(self):
        plan = build()
        baseline = fixture()[4]
        decision = result(plan, None)
        identity = plan["groups"][0]["group_id"]
        decision[identity]["scores"] = {"あり": -.4, "なし": .6, S.UNKNOWN: .1}
        output = S.fan_out(plan, decision, baseline)
        self.assertEqual([v["state"] for v in output[:4]], ["unknown", "unknown", "support", "support"])
        for scores in [{"あり": 1, "なし": 0}, {"あり": float("nan"), "なし": 0, S.UNKNOWN: 0},
                       {"あり": True, "なし": 0, S.UNKNOWN: 0}]:
            invalid = copy.deepcopy(decision)
            invalid[identity]["scores"] = scores
            with self.assertRaisesRegex(ValueError, "finite group scores"):
                S.fan_out(plan, invalid, baseline)
        conflict = copy.deepcopy(decision)
        conflict[identity]["winning_value"] = "あり"
        with self.assertRaisesRegex(ValueError, "score argmax"):
            S.fan_out(plan, conflict, baseline)

    def test_support_without_a_product_quote_is_dropped(self):
        plan = build()
        output = S.fan_out(plan, result(plan, "なし", quotes=False), fixture()[4])
        self.assertEqual([v["state"] for v in output[:4]], ["unknown"] * 4)
        self.assertTrue(all(v["reason"] == "group_missing_product_quote" for v in output[:4]))

    def test_invented_row_quotes_wrong_document_version_and_nonliteral_spans_rejected(self):
        plan = build()
        good = result(plan)
        identity = plan["groups"][0]["group_id"]
        for field, value in [("field", "selected_axes"), ("document_id", "other-document"),
                             ("document_sha256", "wrong-version"), ("quote", "原文に存在しない"), ("start", True)]:
            bad = copy.deepcopy(good)
            bad[identity]["quote_refs"][0][field] = value
            with self.assertRaises(ValueError):
                S.fan_out(plan, bad, fixture()[4])

    def test_coverage_wrong_group_winner_and_group_version_are_rejected(self):
        plan = build()
        identity = plan["groups"][0]["group_id"]
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            S.fan_out(plan, {}, fixture()[4])
        for field, value in [("group_sha256", "changed-version"), ("winning_value", "ない新しい値"), ("status", "support")]:
            invalid = result(plan)
            invalid[identity][field] = value
            with self.assertRaises(ValueError):
                S.fan_out(plan, invalid, fixture()[4])

    def test_plan_is_frozen_and_outputs_do_not_alias_original_inputs(self):
        values, docs, binds, contract, baseline = fixture()
        original = copy.deepcopy((values, docs, binds, contract, baseline))
        plan = build(values, docs, binds, contract)
        output = S.fan_out(plan, result(plan), baseline)
        output[0]["source_task_bindings"][0]["au_row_key"] = "mutated-output-only"
        self.assertEqual((values, docs, binds, contract, baseline), original)
        plan["groups"][0]["request_ids"].pop()
        with self.assertRaisesRegex(ValueError, "plan was modified"):
            S.fan_out(plan, {}, baseline)

    def test_passthrough_requires_complete_original_binding_and_coverage(self):
        values, docs, binds, contract, baseline = fixture()
        plan = build(values, docs, binds, contract)
        omitted = copy.deepcopy(baseline)
        del omitted[values[-1]["request_id"]]
        with self.assertRaisesRegex(ValueError, "cover every passthrough"):
            S.fan_out(plan, result(plan), omitted)
        changed = copy.deepcopy(baseline)
        changed[values[-1]["request_id"]]["source_task_bindings"] = []
        with self.assertRaisesRegex(ValueError, "passthrough original"):
            S.fan_out(plan, result(plan), changed)

    def test_empty_eligible_contract_needs_no_new_results_and_keeps_every_prediction(self):
        values, docs, binds, contract, baseline = fixture()
        plan = build(values, docs, binds, {**contract, "eligible_forward_request_ids": []})
        self.assertEqual(S.fan_out(plan, {}, baseline), list(baseline.values()))


if __name__ == "__main__":
    unittest.main()
