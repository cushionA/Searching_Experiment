"""Generic fail-closed composition policy for complete row predictions.

No labels or semantic inference are performed here. Callers provide validated
baseline/model predictions, requests, original row plans, and frozen baseline
gate decisions. The helper only applies the acceptance policy to those inputs.

``complete_predictions(baseline, derived, requests, row_plans,
baseline_gates, proof_ids)`` returns ``(predictions, audit)``. The audit has
``reverted_request_ids``, ``full_model_supported_rows``, and
``alternative_request_ids``.
"""

from copy import deepcopy


_RAW_BINDING_FIELDS = (
    "request_id", "request_sha256", "selected_value", "direction",
    "source_request", "source_task_bindings",
)


def _assert_coverage(baseline, derived, requests):
    keys = set(baseline)
    if not keys or set(derived) != keys or set(requests) != keys:
        raise ValueError("baseline, derived predictions, and requests need identical coverage")
    for rid in keys:
        req = requests[rid]
        if req.get("request_id", rid) != rid:
            raise ValueError("request key and request_id differ")
        selected = req.get("selected_value")
        if selected is None:
            raise ValueError("request selected_value is required")
        for source_name, prediction in (("baseline", baseline[rid]), ("derived", derived[rid])):
            if prediction.get("selected_value") != selected:
                raise ValueError(f"{source_name} selected_value differs from request")
        for field in _RAW_BINDING_FIELDS:
            if field in baseline[rid]:
                if field not in derived[rid]:
                    raise ValueError(f"derived prediction omitted original {field}")
                if baseline[rid][field] != derived[rid][field]:
                    raise ValueError(f"derived prediction changed original {field}")
    return keys


def _validate_rows(row_plans, baseline_gates, request_ids):
    if not row_plans:
        raise ValueError("at least one original row plan is required")
    seen = set()
    normalized = []
    for row in row_plans:
        case_id = row.get("case_id")
        au_row_key = row.get("au_row_key")
        bindings = row.get("condition_bindings")
        if not case_id or not au_row_key or not isinstance(bindings, list):
            raise ValueError("row plan must include case_id, au_row_key, and condition_bindings")
        row_key = (case_id, au_row_key)
        if row_key in seen:
            raise ValueError("duplicate case/row plan")
        seen.add(row_key)
        if case_id not in baseline_gates:
            raise ValueError("row plan case is missing from baseline gates")
        if "decision" not in baseline_gates[case_id]:
            raise ValueError("baseline gate decision is missing for row case")
        for binding in bindings:
            kind = binding.get("kind")
            if kind == "upstream_identical":
                continue
            rid = binding.get("request_id")
            if rid is None or rid not in request_ids:
                raise ValueError("condition binding is missing a covered request_id")
        normalized.append(row)
    return normalized


def complete_predictions(baseline, derived, requests, row_plans, baseline_gates, proof_ids):
    """Apply fallback-all while requiring whole same-row model support.

    Any non-proof alternative winner explicitly turns that prediction unknown.
    Existing accepts survive unless such an alternative is explicit. A new
    support is retained only when every original condition for each relevant
    non-accepted row is covered and supported by the derived model. If one
    reused request appears on an incomplete row, its newly added support is
    retracted globally.
    """
    request_ids = _assert_coverage(baseline, derived, requests)
    proofs = set(proof_ids)
    if not proofs <= request_ids:
        raise ValueError("proof IDs must be covered requests")
    rows = _validate_rows(row_plans, baseline_gates, request_ids)

    predictions = deepcopy(baseline)
    alternatives = set()
    reverted = set()
    accepted_case_bound_requests = {
        binding["request_id"]
        for row in rows
        if baseline_gates[row["case_id"]].get("decision") == "accept"
        for binding in row["condition_bindings"]
        if binding.get("request_id") is not None
    }
    for rid, candidate in derived.items():
        if rid in proofs:
            continue
        winner = candidate.get("winning_value")
        selected = requests[rid]["selected_value"]
        if winner is not None and winner != selected:
            replacement = deepcopy(candidate)
            replacement["state"] = "unknown"
            replacement["reason"] = "coherent_model_alternative_abstention"
            predictions[rid] = replacement
            alternatives.add(rid)
        elif (baseline[rid].get("state") != "support"
              and candidate.get("state") == "support"
              and rid not in accepted_case_bound_requests):
            predictions[rid] = deepcopy(candidate)
        elif (baseline[rid].get("state") != "support"
              and candidate.get("state") == "support"
              and rid in accepted_case_bound_requests):
            reverted.add(rid)

    full_model_supported = {}
    row_bound_requests = set()
    for row in rows:
        bindings_supported = all(
            binding.get("kind") == "upstream_identical"
            or derived[binding["request_id"]].get("state") == "support"
            for binding in row["condition_bindings"]
        )
        row_key = (row["case_id"], row["au_row_key"])
        full_model_supported[row_key] = (bool(row.get("all_original_axes_covered"))
                                         and bool(row["condition_bindings"])
                                         and bindings_supported)
        row_bound_requests.update(binding["request_id"] for binding in row["condition_bindings"]
                                  if binding.get("request_id") is not None)

    # An already accepted case can contain competing rows. Adding support to
    # any request in that case could create a second supported row and lower
    # the case decision, so keep all such newly unsupported requests at their
    # baseline state. Explicit alternative winners above still abstain.
    for rid in accepted_case_bound_requests - proofs:
        if (baseline[rid].get("state") != "support"
                and predictions[rid].get("state") == "support"):
            predictions[rid] = deepcopy(baseline[rid])
            reverted.add(rid)

    for rid in request_ids - proofs - row_bound_requests:
        if (predictions[rid].get("state") == "support"
                and baseline[rid].get("state") != "support"):
            predictions[rid] = deepcopy(baseline[rid])
            reverted.add(rid)

    for row in rows:
        case_id = row["case_id"]
        if (baseline_gates[case_id].get("decision") == "accept"
                or full_model_supported[(case_id, row["au_row_key"])]):
            continue
        for binding in row["condition_bindings"]:
            rid = binding.get("request_id")
            if (rid is not None and rid not in proofs
                    and predictions[rid].get("state") == "support"
                    and baseline[rid].get("state") != "support"):
                predictions[rid] = deepcopy(baseline[rid])
                reverted.add(rid)

    for rid in proofs:
        predictions[rid] = deepcopy(baseline[rid])
    return predictions, {
        "reverted_request_ids": sorted(reverted),
        "full_model_supported_rows": sorted(key for key, supported in full_model_supported.items()
                                            if supported),
        "alternative_request_ids": sorted(alternatives),
    }
