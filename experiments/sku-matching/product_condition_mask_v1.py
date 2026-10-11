"""Explicit early keep/drop mask over frozen AU-product condition decisions.

Keep only means continue the remaining SKU comparison. Alternative model
winners are rejection evidence for this automation policy, never asserted
semantic contradiction. This pure adapter runs no model and reads no labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy

from product_condition_sharing_v1 import VERSION as SHARING_VERSION
from product_condition_sharing_v1 import canonical_au_url, digest, fan_out

VERSION = "product-condition-mask-v1"


def _index(values, field):
    if isinstance(values, dict):
        result = dict(values)
        if any(key != value.get(field) for key, value in result.items()):
            raise ValueError("mapping identity differs: " + field)
        return result
    result = {}
    for value in values:
        identity = value.get(field)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError("missing or duplicate " + field)
        result[identity] = value
    return result


def _validate_frozen_predictions(plan, predictions):
    if (plan.get("schema_version") != SHARING_VERSION or
            plan.get("plan_sha256") != digest({k: v for k, v in plan.items() if k != "plan_sha256"})):
        raise ValueError("immutable source sharing plan was modified")
    if set(predictions) != set(plan["original_requests"]):
        raise ValueError("mask needs every original frozen fan-out request")
    results = {}
    for group in plan["groups"]:
        for identity in group["request_ids"]:
            prediction = predictions[identity]
            result = prediction.get("shared_group_decision")
            if not isinstance(result, dict):
                raise ValueError("eligible request is missing its frozen shared group decision")
            previous = results.setdefault(group["group_id"], result)
            if previous != result:
                raise ValueError("same product group has inconsistent frozen decisions")
    # Reuse the frozen helper to verify every winner, quote, request and binding
    # without changing its ranking-to-unknown semantics or executing inference.
    expected = {r["request_id"]: r for r in fan_out(plan, results, predictions)}
    fields = (
        "request_id", "request_sha256", "selected_value", "direction", "source_request",
        "source_task_bindings", "state", "winning_value", "quote_refs", "group_id",
        "group_sha256", "group_model_cache_key", "shared_group_decision", "scope_status",
        "production_adoption_allowed", "tie",
    )
    for identity in plan["request_to_group"]:
        if any(predictions[identity].get(field) != expected[identity].get(field) for field in fields):
            raise ValueError("frozen eligible fan-out metadata/state differs: " + identity)
    return expected


def _validate_case_rows(plan, cases, row_plans):
    rows, bound_tasks = {}, {}
    task_index = {t["task_id"]: t for values in plan["bindings"].values() for t in values}
    for row in row_plans:
        key = row.get("case_id"), row.get("au_row_key")
        if key[0] not in cases or not isinstance(key[1], str) or not key[1] or key in rows:
            raise ValueError("original row must uniquely bind an output case")
        rows[key] = row
        if type(row.get("all_original_axes_covered")) is not bool:
            raise ValueError("original row coverage metadata required")
        bindings = row.get("condition_bindings")
        if not isinstance(bindings, list):
            raise ValueError("complete original row condition bindings required")
        for binding in bindings:
            if binding.get("kind") == "upstream_identical":
                continue
            if binding.get("kind") != "model":
                raise ValueError("unsupported original row condition binding kind")
            identity = binding.get("request_id")
            task = task_index.get(binding.get("task_id"))
            if (identity not in plan["original_requests"] or task is None or
                    task.get("request_id") != identity or task.get("case_id") != key[0] or
                    task.get("au_row_key") != key[1] or task.get("direction") != binding.get("direction") or
                    task.get("axis_key") != binding.get("axis_key")):
                raise ValueError("original row/task/request binding differs")
            if binding["task_id"] in bound_tasks:
                raise ValueError("original task appears in multiple row bindings")
            bound_tasks[binding["task_id"]] = task
    for identity, case in cases.items():
        product_id, sku_key = case.get("au_product_id"), case.get("source_sku_key")
        if not isinstance(sku_key, str) or not sku_key:
            raise ValueError("original case source SKU identity required")
        canonical_au_url({"side": "au", "fixed_page": True, "product_id": product_id, "url": None})
        actual = {key[1] for key in rows if key[0] == identity}
        candidate = case.get("candidate_row_keys")
        if (not isinstance(candidate, list) or len(set(candidate)) != len(candidate) or set(candidate) != actual):
            raise ValueError("original candidate AU row identities differ from row plans")
    for values in plan["bindings"].values():
        for task in values:
            if task.get("case_id") not in cases:
                continue
            if task["task_id"] not in bound_tasks:
                raise ValueError("selected original case lost a source task/row binding")
            case = cases[task["case_id"]]
            if (task.get("au_product_id") != case["au_product_id"] or
                    task.get("source_sku_key") != case["source_sku_key"]):
                raise ValueError("original case fixed AU URL or Rakuten SKU differs")
    return rows


def _ambiguities(plan, predictions):
    """No axis-specific interpretation: conflicting raw families cannot keep."""
    by_scope = defaultdict(list)
    for group in plan["groups"]:
        by_scope[(group["canonical_au_url"], group["axis_name"])].append(group)
    ambiguous, details = {}, []
    for (url, axis), groups in by_scope.items():
        families = {tuple(g["option_values"]) for g in groups}
        winners = {predictions[g["request_ids"][0]]["winning_value"] for g in groups}
        concrete_winners = winners - {None}
        reasons = []
        if len(families) > 1:
            reasons.append("multiple_unchanged_ordered_option_families")
        if len(concrete_winners) > 1:
            reasons.append("inconsistent_product_global_shared_winners")
        if not reasons:
            continue
        detail = {"canonical_au_url": url, "axis_name": axis, "reasons": reasons,
                  "group_ids": [g["group_id"] for g in groups],
                  "ordered_option_families": [list(v) for v in sorted(families)],
                  "observed_whole_original_winners": sorted(concrete_winners),
                  "policy": "early_drop_ambiguity_without_semantic_conflict_claim"}
        details.append(detail)
        for group in groups:
            ambiguous[group["group_id"]] = detail
    return ambiguous, details


def build_mask(plan, predictions, case_plans, row_plans):
    """Return a frozen per-request mask and bounded original-case early exit.

    All original source bindings, including outside the selected cases, stay
    in each condition proof. Only case_plans IDs receive case outputs. Any
    eligible product-global condition dropped for a case makes early_drop.
    Keep or no eligible condition always continues the remaining SKU matching;
    neither is a final product/SKU acceptance.

    Remaining model bindings skipped by early exit are *potential future*
    savings. Already checked upstream-identical bindings and historically
    executed baseline predictions are never claimed as avoided inference.
    """
    predicted = _index(predictions, "request_id")
    cases = _index(case_plans, "case_id")
    source_rows = list(row_plans)
    original_hashes = {
        "sharing_plan": digest(plan), "fanout_predictions": digest(predicted),
        "case_plans": digest(cases), "row_plans": digest(source_rows),
    }
    expected = _validate_frozen_predictions(plan, predicted)
    rows = _validate_case_rows(plan, cases, source_rows)
    ambiguities, ambiguity_details = _ambiguities(plan, expected)
    groups = {g["group_id"]: g for g in plan["groups"]}
    conditions, by_case, counts = [], defaultdict(list), Counter()
    case_axis_values = defaultdict(set)
    for identity, group_id in plan["request_to_group"].items():
        group, prediction = groups[group_id], expected[identity]
        request = plan["original_requests"][identity]
        ambiguity = ambiguities.get(group_id)
        keep = prediction["state"] == "support" and prediction["winning_value"] == request["selected_value"] and ambiguity is None
        decision = "keep" if keep else "drop"
        reason = ("ambiguous_product_global_option_family_or_winner" if ambiguity else
                  "shared_winner_supports_original_condition" if keep else
                  "shared_alternative_winner" if prediction["winning_value"] is not None else
                  "shared_condition_not_supported")
        proof = {
            "request_id": identity, "request_sha256": request["request_sha256"],
            "selected_value": request["selected_value"], "axis_name": request["axis_name"],
            "option_values": copy.deepcopy(request["option_values"]),
            "canonical_au_url": group["canonical_au_url"], "target_document_id": group["target_document_id"],
            "target_document_sha256": group["target_document_sha256"], "group_id": group_id,
            "group_sha256": group["group_sha256"], "group_model_cache_key": group["model_cache_key"],
            "shared_winning_value": prediction["winning_value"], "condition_decision": decision,
            "reason": reason, "original_model_state": prediction["state"],
            "quote_refs": copy.deepcopy(prediction["quote_refs"]),
            "source_request": copy.deepcopy(request),
            "source_task_bindings": copy.deepcopy(plan["bindings"][identity]),
            "source_prediction_sha256": digest(predicted[identity]),
            "ambiguity": copy.deepcopy(ambiguity), "semantic_conflict_proved": False,
            "scope_status": "unproved", "product_global_invariance_basis": "user_asserted",
        }
        proof["condition_sha256"] = digest(proof)
        conditions.append(proof)
        counts["eligible_condition_" + decision + "_count"] += 1
        counts["eligible_source_task_binding_count"] += len(proof["source_task_bindings"])
        for task in proof["source_task_bindings"]:
            if task["case_id"] not in cases:
                counts["outside_output_case_source_binding_count"] += 1
                continue
            case = cases[task["case_id"]]
            wanted_url = "https://wowma.jp/item/" + case["au_product_id"]
            if group["canonical_au_url"] != wanted_url:
                raise ValueError("product-global condition cannot spill into another AU URL")
            by_case[task["case_id"]].append(proof)
            case_axis_values[(task["case_id"], wanted_url, request["axis_name"])].add(request["selected_value"])
    if any(len(values) > 1 for values in case_axis_values.values()):
        raise ValueError("one original Rakuten SKU has inconsistent same-axis wanted values across AU rows")
    case_results, dropped, continued = [], [], []
    noneligible_bindings_dropped = []
    completed_identical_all = completed_identical_dropped = 0
    for identity, case in cases.items():
        bound = {c["request_id"]: c for c in by_case[identity]}
        rejected = [c for c in bound.values() if c["condition_decision"] == "drop"]
        early_drop = bool(rejected)
        decision = "early_drop" if early_drop else "continue_to_remaining_sku_comparison"
        (dropped if early_drop else continued).append(identity)
        counts["early_dropped_case_count" if early_drop else "continued_case_count"] += 1
        if not bound:
            counts["cases_without_eligible_product_global_condition"] += 1
        source_case_rows = [r for key, r in rows.items() if key[0] == identity]
        model_bindings = [b for r in source_case_rows for b in r["condition_bindings"]
                          if b["kind"] == "model" and b["request_id"] not in plan["request_to_group"]]
        identical = sum(b["kind"] == "upstream_identical" for r in source_case_rows for b in r["condition_bindings"])
        completed_identical_all += identical
        if early_drop:
            noneligible_bindings_dropped.extend(model_bindings)
            completed_identical_dropped += identical
        result = {
            "case_id": identity, "source_sku_key": case["source_sku_key"],
            "au_product_id": case["au_product_id"], "canonical_au_url": "https://wowma.jp/item/" + case["au_product_id"],
            "case_decision": decision, "early_drop": early_drop, "final_accept": False,
            "reason": "any_eligible_product_global_condition_dropped" if early_drop else
                      "all_eligible_conditions_kept_continue_remaining_comparison" if bound else
                      "no_eligible_condition_continue_remaining_comparison",
            "eligible_request_ids": list(bound), "dropped_request_ids": [c["request_id"] for c in rejected],
            "condition_refs": [{"request_id": c["request_id"], "request_sha256": c["request_sha256"],
                                "group_id": c["group_id"], "group_sha256": c["group_sha256"],
                                "condition_sha256": c["condition_sha256"]} for c in bound.values()],
            "potential_skipped_future_model_binding_count": len(model_bindings) if early_drop else 0,
            "already_completed_upstream_identical_count": identical,
            "source_case_plan": copy.deepcopy(case), "source_row_plans": copy.deepcopy(source_case_rows),
            "production_adoption_allowed": False,
        }
        result["case_sha256"] = digest(result)
        case_results.append(result)
    potentially_skipped_ids = {b["request_id"] for b in noneligible_bindings_dropped}
    wholly_skippable_ids = {identity for identity in potentially_skipped_ids
                           if all(task.get("case_id") in dropped for task in plan["bindings"][identity])}
    counts.update({
        "total_original_request_count": len(predicted), "eligible_condition_count": len(conditions),
        "noneligible_passthrough_request_count": len(plan["passthrough_request_ids"]),
        "selected_original_case_count": len(cases), "shared_group_count": len(groups),
        "ambiguous_product_global_scope_count": len(ambiguity_details),
        "potential_skipped_future_model_binding_count": len(noneligible_bindings_dropped),
        "potential_skipped_future_unique_model_request_count_within_selected_cases": len(potentially_skipped_ids),
        "potential_wholly_skippable_future_model_request_count": len(wholly_skippable_ids),
        "already_completed_upstream_identical_count": completed_identical_all,
        "already_completed_upstream_identical_count_in_dropped_cases": completed_identical_dropped,
        "measured_additional_avoided_inference_count": 0,
    })
    if original_hashes != {"sharing_plan": digest(plan), "fanout_predictions": digest(predicted),
                           "case_plans": digest(cases), "row_plans": digest(source_rows)}:
        raise ValueError("source inputs changed during mask construction")
    mask = {
        "schema_version": VERSION, "labels_read": False, "model_inference_run": False,
        "production_adoption_allowed": False, "final_accept_from_mask_allowed": False,
        "input_sha256": original_hashes, "input_sha256_unchanged": True,
        "source_plan_sha256": plan["plan_sha256"], "source_model_identity": copy.deepcopy(plan["model_identity"]),
        "source_content_version": plan["content_version"],
        "invariance_contract": copy.deepcopy(plan["invariance_contract"]),
        "dropped_case_ids": dropped, "continued_case_ids": continued,
        "condition_rows": conditions, "case_rows": case_results,
        "ambiguities": ambiguity_details, "counts": dict(counts),
        "saved_remaining_symmetric_check_count": len(noneligible_bindings_dropped),
        "saved_remaining_symmetric_check_count_is_potential": True,
        "runtime_savings_measured": False,
        "savings_note": "Potential future noneligible model bindings only; upstream-identical checks and historical baseline inference were already executed. Actual group-model dedup/runtime savings belong to the separate model trial.",
    }
    mask["condition_rows_sha256"] = digest(conditions)
    mask["case_rows_sha256"] = digest(case_results)
    mask["mask_sha256"] = digest(mask)
    return mask
