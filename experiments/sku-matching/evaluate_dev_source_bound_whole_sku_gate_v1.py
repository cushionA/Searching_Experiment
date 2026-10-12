#!/usr/bin/env python3
"""Source-bound whole-SKU evaluation primitives for the frozen DEV40 protocol.

This module defines assembly only. It does not read labels, run inference, or
consume producer hints. NLI judgments must first pass the unchanged v2 source
response validator; strict literal proofs are separately source-verified.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json


RELATIONS = {"support", "conflict", "unknown"}
VARIANTS = {"choice_nli_full", "baseline_literal_overlay"}


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def index(rows, key, label):
    result = {}
    for row in rows:
        identity = row.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError("missing or duplicate " + label + " identity")
        result[identity] = row
    return result


def source_literal_state(task: dict, proof_state: dict, source_receipt: dict) -> dict | None:
    """Return an independently verified exact-family decision, else None."""
    state = proof_state.get("status")
    if state not in {"support", "conflict"} or proof_state.get("literal_proof_verified") is not True:
        return None
    selected = task.get("raw_condition_ref", {}).get("value")
    family = source_receipt.get("ordered_family")
    if (source_receipt.get("status") != "verified_complete_ordered_raw_family"
            or proof_state.get("raw_source_family_verified") is not True
            or proof_state.get("selected_value") != selected
            or proof_state.get("ordered_family") != family
            or not isinstance(family, list) or family.count(selected) != 1):
        raise ValueError("literal task state does not bind the verified complete source family")
    return {"task_id": task["task_id"], "request_id": task["request_id"],
            "case_id": task["case_id"], "au_row_key": task["au_row_key"],
            "direction": task["direction"], "axis_name": task["raw_condition_ref"]["axis_name"],
            "selected_value": selected, "state": state, "source": "strict_literal_family",
            "proof_pair_id": proof_state.get("proof_pair_id"),
            "source_family_sha256": sha(family), "source_citations_verified": True,
            "semantic_truth_verified": False, "production_adoption_allowed": False}


def nli_task_state(task: dict, binding: dict, validated_pair: dict) -> dict:
    """Project only the exact original source-selected member from validated NLI.

    ``validated_pair`` must be the result of the unchanged source-bound v2
    response validator, not raw model JSON.
    """
    task_binding = binding.get("task_binding") or {}
    request_binding = binding.get("request_binding") or {}
    if (binding.get("preparation_status") != "ready"
            or binding.get("pair_id") != validated_pair.get("pair_id")
            or task_binding.get("task_id") != task.get("task_id")
            or task_binding.get("case_id") != task.get("case_id")
            or task_binding.get("au_row_key") != task.get("au_row_key")
            or task_binding.get("request_id") != task.get("request_id")
            or task_binding.get("direction") != task.get("direction")
            or request_binding.get("request_id") != task.get("request_id")):
        raise ValueError("validated NLI output has a foreign or incomplete original task binding")
    if (validated_pair.get("source_citations_verified") is not True
            or validated_pair.get("semantic_truth_verified") is not False
            or validated_pair.get("sourceproof_certified") is not False
            or validated_pair.get("production_adoption_allowed") is not False):
        raise ValueError("NLI pair result is not a source-validated non-adoptable model judgment")
    desired = (binding.get("source_selected_fact") or {}).get("value")
    if desired != task.get("raw_condition_ref", {}).get("value"):
        raise ValueError("hidden source-selected NLI fact differs from original task selected value")
    judgments = [item for item in validated_pair.get("judgments", [])
                 if item.get("whole_option") == desired]
    if len(judgments) != 1:
        return {"task_id": task["task_id"], "request_id": task["request_id"],
                "case_id": task["case_id"], "au_row_key": task["au_row_key"],
                "direction": task["direction"], "axis_name": task["raw_condition_ref"]["axis_name"],
                "selected_value": desired, "state": "unknown", "source": "choice_nli",
                "pair_id": binding["pair_id"], "reason": "exact_source_selected_member_missing_or_duplicated",
                "source_citations_verified": True, "semantic_truth_verified": False,
                "production_adoption_allowed": False}
    judgment = judgments[0]
    state = judgment.get("state")
    if (state not in {"support", "explicit_conflict", "unknown"}
            or (state in {"support", "explicit_conflict"}
                and (judgment.get("scope_assessment") != "same_condition"
                     or judgment.get("semantic_relation") != state
                     or type(judgment.get("citations_verified")) is not int
                     or judgment["citations_verified"] < 1))):
        state = "unknown"
    relation = "conflict" if state == "explicit_conflict" else state
    return {"task_id": task["task_id"], "request_id": task["request_id"],
            "case_id": task["case_id"], "au_row_key": task["au_row_key"],
            "direction": task["direction"], "axis_name": task["raw_condition_ref"]["axis_name"],
            "selected_value": desired, "state": relation, "source": "choice_nli",
            "pair_id": binding["pair_id"],
            "citations_verified_count": judgment.get("citations_verified", 0),
            "source_citations_verified": True, "semantic_truth_verified": False,
            "production_adoption_allowed": False}


def build_choice_nli_task_resolutions(tasks: list[dict], bindings: list[dict],
                                      validated_pairs: list[dict], task_proofs: list[dict],
                                      task_source_receipts: list[dict]) -> list[dict]:
    task_by_id = index(tasks, "task_id", "original task")
    proof_by_id = index(task_proofs, "task_id", "literal task proof state")
    receipt_by_id = index(task_source_receipts, "task_id", "task source receipt")
    pair_results = index(validated_pairs, "pair_id", "validated NLI pair")
    bindings_by_task = index(bindings, "task_binding", "NLI task binding") if False else None
    by_task = {}
    for binding in bindings:
        task_id = (binding.get("task_binding") or {}).get("task_id")
        if task_id in by_task:
            raise ValueError("NLI source bindings duplicate an original task")
        by_task[task_id] = binding
    if set(by_task) != set(task_by_id) or set(proof_by_id) != set(task_by_id) or set(receipt_by_id) != set(task_by_id):
        raise ValueError("NLI, literal proof, or source receipt population differs from complete original tasks")
    results = []
    for task_id, task in task_by_id.items():
        literal = source_literal_state(task, proof_by_id[task_id], receipt_by_id[task_id])
        if literal is not None:
            results.append(literal)
            continue
        binding = by_task[task_id]
        pair_result = pair_results.get(binding.get("pair_id"))
        if pair_result is None:
            results.append({"task_id": task_id, "request_id": task["request_id"],
                "case_id": task["case_id"], "au_row_key": task["au_row_key"],
                "direction": task["direction"], "axis_name": task["raw_condition_ref"]["axis_name"],
                "selected_value": task["raw_condition_ref"]["value"], "state": "unknown",
                "source": "choice_nli", "reason": "no_validated_response_for_unprotected_context",
                "source_citations_verified": False, "semantic_truth_verified": False,
                "production_adoption_allowed": False})
        else:
            results.append(nli_task_state(task, binding, pair_result))
    if len(results) != len(task_by_id) or {row["task_id"] for row in results} != set(task_by_id):
        raise ValueError("choice NLI task resolutions omit or add original tasks")
    return results


def project_baseline_literal_overlay(requests: list[dict], request_proofs: list[dict],
                                     baseline_states: dict[str, dict]) -> dict[str, dict]:
    """Overlay unanimous verified literal proof; preserve baseline otherwise."""
    request_by_id = index(requests, "request_id", "original request")
    proof_by_id = index(request_proofs, "request_id", "literal request proof")
    if set(proof_by_id) != set(request_by_id):
        raise ValueError("literal request proof population differs from original request set")
    output = {}
    for request_id, request in request_by_id.items():
        proof_row = proof_by_id[request_id]
        if proof_row.get("eligible"):
            state = proof_row.get("status")
            if (state not in {"support", "conflict"}
                    or proof_row.get("selected_value") != request.get("selected_value")
                    or proof_row.get("option_values") != request.get("option_values")):
                raise ValueError("literal request override differs from complete original request")
            output[request_id] = {"request_id": request_id, "state": state,
                "winning_value": request["selected_value"] if state == "support" else None,
                "source": "strict_literal_family", "proof_pair_ids": proof_row.get("proof_pair_ids", []),
                "production_adoption_allowed": False}
            continue
        baseline = baseline_states.get(request_id)
        if baseline is None:
            state, winner, reason = "unknown", None, "missing_baseline_request"
        else:
            state = baseline.get("state")
            winner = baseline.get("winning_value")
            reason = "baseline_prediction"
            if state not in RELATIONS or (winner is not None and winner not in request.get("option_values", [])):
                raise ValueError("baseline result escaped the original request whole-option family")
            if state in {"support", "conflict"} and baseline.get("source_citations_verified") is not True:
                state, winner, reason = "unknown", None, "baseline_decisive_state_lacks_verified_literal_citation"
            elif winner is not None and winner != request.get("selected_value"):
                state, winner, reason = "unknown", None, "alternate_option_is_not_conflict_proof"
            elif state == "support" and winner != request.get("selected_value"):
                state, winner, reason = "unknown", None, "support_does_not_select_original_whole_value"
            elif state == "conflict":
                winner = None
        output[request_id] = {"request_id": request_id, "state": state,
            "winning_value": winner, "source": "baseline_model", "reason": reason,
            "production_adoption_allowed": False}
    return output


def baseline_task_resolutions(tasks: list[dict], request_states: dict[str, dict]) -> list[dict]:
    task_by_id = index(tasks, "task_id", "original task")
    if any(task.get("request_id") not in request_states for task in task_by_id.values()):
        raise ValueError("baseline request projection omits an original task request")
    results = []
    for task_id, task in task_by_id.items():
        request_state = request_states[task["request_id"]]
        results.append({"task_id": task_id, "request_id": task["request_id"],
            "case_id": task["case_id"], "au_row_key": task["au_row_key"],
            "direction": task["direction"], "axis_name": task["raw_condition_ref"]["axis_name"],
            "selected_value": task["raw_condition_ref"]["value"], "state": request_state["state"],
            "source": request_state["source"], "reason": request_state.get("reason"),
            "production_adoption_allowed": False})
    return results


def finish_whole_sku_gate(variant: str, case_plans: list[dict], row_plans: list[dict],
                          tasks: list[dict], task_resolutions: list[dict]) -> dict:
    """Assemble complete rows/cases; unknown never excludes a competing row."""
    if variant not in VARIANTS:
        raise ValueError("unsupported frozen DEV gate variant")
    task_by_id = index(tasks, "task_id", "original task")
    resolution_by_id = index(task_resolutions, "task_id", "task resolution")
    if set(task_by_id) != set(resolution_by_id):
        raise ValueError("task resolution population differs from full original bidirectional task set")
    if any(row.get("state") not in RELATIONS for row in task_resolutions):
        raise ValueError("task resolution state is outside support/conflict/unknown")
    bound_seen = []
    row_results = []
    row_keys_by_case = defaultdict(set)
    for plan in row_plans:
        task_ids = [binding.get("task_id") for binding in plan.get("bindings", [])]
        if not task_ids or any(task_id not in task_by_id for task_id in task_ids):
            raise ValueError("source AU row has missing or foreign bidirectional task bindings")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("source AU row duplicates an original task binding")
        selected_tasks = [task_by_id[task_id] for task_id in task_ids]
        directions = Counter(task["direction"] for task in selected_tasks)
        if (directions.get("rakuten_to_au", 0) != plan.get("rakuten_axis_count")
                or directions.get("au_to_rakuten", 0) != plan.get("au_axis_count")
                or any((task["case_id"], task["au_row_key"]) != (plan["case_id"], plan["au_row_key"])
                       for task in selected_tasks)):
            raise ValueError("source AU row does not bind every original axis in both directions")
        bound_seen.extend(task_ids)
        states = [resolution_by_id[task_id]["state"] for task_id in task_ids]
        row_state = "conflict" if "conflict" in states else "unknown" if "unknown" in states else "support"
        row_results.append({"case_id": plan["case_id"], "au_row_key": plan["au_row_key"],
            "state": row_state, "task_count": len(task_ids), "condition_state_counts": dict(Counter(states)),
            "all_original_axes_covered": True, "production_adoption_allowed": False})
        row_keys_by_case[plan["case_id"]].add(plan["au_row_key"])
    if len(bound_seen) != len(set(bound_seen)) or set(bound_seen) != set(task_by_id):
        raise ValueError("whole-SKU assembly must use every original task exactly once")

    rows_by_case = defaultdict(list)
    for row in row_results:
        rows_by_case[row["case_id"]].append(row)
    decisions = []
    seen_cases = set()
    for case in case_plans:
        cid = case.get("case_id")
        if cid in seen_cases:
            raise ValueError("duplicate fixed source case")
        seen_cases.add(cid)
        expected = set(case.get("row_keys", []))
        actual = row_keys_by_case.get(cid, set())
        rows = rows_by_case.get(cid, [])
        if (not expected or actual != expected or len(rows) != case.get("total_au_rows")
                or len(actual) != len(rows)):
            raise ValueError("case does not include every captured fixed AU row")
        supported = [row["au_row_key"] for row in rows if row["state"] == "support"]
        accepted = len(supported) == 1 and all(row["state"] == "conflict" or row["au_row_key"] in supported
                                               for row in rows)
        reason = ("unique_supported_row_all_other_rows_conflict" if accepted else
                  "multiple_supported_rows" if len(supported) > 1 else
                  "unknown_competing_row" if supported and any(row["state"] == "unknown" for row in rows) else
                  "no_fully_supported_row")
        decisions.append({"case_id": cid, "dossier_id": case["dossier_id"],
            "au_product_id": case["au_product_id"], "decision": "accept" if accepted else "drop",
            "top_row_key": supported[0] if accepted else None, "reason": reason,
            "row_state_counts": dict(Counter(row["state"] for row in rows)),
            "row_count": len(rows), "required_row_count": case["total_au_rows"],
            "production_adoption_allowed": False})
    if seen_cases != set(rows_by_case):
        raise ValueError("whole-SKU assembly found rows outside the frozen selected case population")
    return {"variant": variant, "rows": row_results, "decisions": decisions,
        "task_state_counts": dict(Counter(row["state"] for row in task_resolutions)),
        "row_state_counts": dict(Counter(row["state"] for row in row_results)),
        "decision_counts": dict(Counter(row["decision"] for row in decisions)),
        "production_adoption_allowed": False, "labels_read": False}


def eval_manifest(variant: str, input_hashes: dict, resolution_hash: str) -> dict:
    if variant not in VARIANTS or not input_hashes or not resolution_hash:
        raise ValueError("evaluation freeze is missing variant or source hashes")
    return {"schema_version": "dev-source-bound-whole-sku-gate-v1",
        "variant": variant, "phase": "evaluation_ready_after_root_freeze",
        "input_sha256": input_hashes, "task_resolution_sha256": resolution_hash,
        "uses_v7_upstream_decisions": False, "uses_v7_core_or_residual": False,
        "labels_read": False, "test_split_read": False, "production_adoption_allowed": False}
