#!/usr/bin/env python3
"""Indexed transport adapter for body-invariant choice v3.

The model still authors every semantic field. This module only expands exact
indices into source metadata before invoking the frozen v3 literal validator.
Raw indexed responses and rendered v3 responses are separate artifacts.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import sku_body_invariant_choice_v3 as BODY

PROTOCOL = "sku-body-invariant-indexed-choice-v1"
_DECISION_SEMANTIC_KEYS = {
    "whole_value_assessed", "state", "scope_assessment", "semantic_relation",
    "scope_and_coordinate_explanation", "option_relation_explanation",
    "exclusivity_assessment", "exclusivity_explanation", "grounding_route",
    "target_fact_indices", "body_invariant", "body_quote_refs",
    "body_condition_bindings", "direct_contradiction_explanation",
}
_INVARIANT_SEMANTIC_KEYS = {
    "attribute", "attribute_quote", "value", "value_quote", "role", "role_explanation",
    "subject_resolution", "subject_scope", "target_identity_explanation", "quantifier_scope",
    "invariance_basis", "family_audit", "semantic_explanation", "scope_evidence_quote",
    "positive_scope_explanation", "product_scope_assessment", "whole_attribute_assessed",
}


def build_indexed_payload(base_payload: dict[str, Any], pair_ref: int) -> dict[str, Any]:
    """Add public index hints while retaining the complete v3 context."""
    if type(pair_ref) is not int or pair_ref < 0:
        raise ValueError("pair_ref must be a nonnegative integer")
    out = deepcopy(base_payload)
    out["indexed_transport"] = {
        "protocol": PROTOCOL,
        "pair_ref": pair_ref,
        "decision_indices": "option_index is each original option index exactly once in order",
        "body_quote_refs": "provide body_container_index=0, block_index, exact quote, start, end; block_index is also canonical body_index",
        "family_audit": "provide state_index, applicability, explanation; source identities are inserted",
        "authorship": "model must infer and author every v3 semantic field; no omitted fields or renderer defaults",
    }
    body_blocks = out.get("context", {}).get("eligible_target_body", {}).get("blocks", [])
    source_states = out.get("context", {}).get("target_native_sku_inventory", {}).get("all_option_states", [])
    out["indexed_transport"]["body_blocks"] = [
        {"body_container_index": 0, "block_index": i,
         "canonical_body_index": i, "block_id": b.get("block_id")}
        for i, b in enumerate(body_blocks)]
    out["indexed_transport"]["family_states"] = [
        {"state_index": i, "whole_state": deepcopy(s)} for i, s in enumerate(source_states)]
    return out


def _keys(obj: Any, required: set[str], allowed: set[str], label: str) -> None:
    if not isinstance(obj, dict):
        raise ValueError(label + " must be an object")
    missing, extra = required - set(obj), set(obj) - allowed
    if missing:
        raise ValueError(label + " missing required properties: " + ",".join(sorted(missing)))
    if extra:
        raise ValueError(label + " has unknown properties: " + ",".join(sorted(extra)))


def render_indexed_response(raw: Any, pair: dict[str, Any], payload: dict[str, Any],
                            expected_pair_ref: int) -> dict[str, Any]:
    """Expand source indices; preserve every semantic value verbatim."""
    _keys(raw, {"protocol", "pair_ref", "decisions"}, {"protocol", "pair_ref", "decisions"}, "indexed response")
    if raw["protocol"] != PROTOCOL or type(raw["pair_ref"]) is not int or raw["pair_ref"] != expected_pair_ref:
        raise ValueError("indexed response protocol or pair_ref mismatch")
    options = pair.get("option_values")
    candidate = pair.get("candidate_vector")
    if (not isinstance(pair.get("pair_id"), str) or not pair["pair_id"]
            or not isinstance(pair.get("cache_key"), str) or not pair["cache_key"]):
        raise ValueError("pair identity is incomplete")
    if payload.get("pair_id") != pair["pair_id"] or payload.get("cache_key") != pair["cache_key"]:
        raise ValueError("pair identity differs from public payload")
    payload_candidate = payload.get("candidate_vector")
    if payload_candidate != candidate:
        raise ValueError("candidate vector differs from public payload")
    decisions = raw["decisions"]
    if not isinstance(options, list) or not options or not isinstance(candidate, list) or len(candidate) != len(options):
        raise ValueError("pair lacks complete original option vector")
    if not isinstance(decisions, list) or len(decisions) != len(options):
        raise ValueError("decisions must cover every complete original option")
    indices = []
    for d in decisions:
        if not isinstance(d, dict) or type(d.get("option_index")) is not int:
            raise ValueError("option_index must be an integer, not a boolean")
        indices.append(d["option_index"])
    if indices != list(range(len(options))):
        raise ValueError("decisions must use each original option index once in order")

    blocks = payload["context"]["eligible_target_body"]["blocks"]
    states = payload["context"]["target_native_sku_inventory"]["all_option_states"]
    rendered = []
    for i, source in enumerate(decisions):
        required = {"option_index"} | _DECISION_SEMANTIC_KEYS
        _keys(source, required, required, "decision")
        d = {k: deepcopy(source[k]) for k in _DECISION_SEMANTIC_KEYS}
        d["option_index"] = i
        d["whole_option"] = options[i]
        d["option_evidence_id"] = candidate[i]["option_evidence_id"]

        # Model references are indices; inserted fields are source metadata only.
        raw_refs = source["body_quote_refs"]
        if not isinstance(raw_refs, list):
            raise ValueError("body_quote_refs must be a list")
        refs = []
        for ref in raw_refs:
            _keys(ref, {"body_container_index", "block_index", "quote", "start", "end"},
                  {"body_container_index", "block_index", "quote", "start", "end"}, "body quote ref")
            bi, ki = ref["body_container_index"], ref["block_index"]
            if type(bi) is not int or bi != 0 or type(ki) is not int or not 0 <= ki < len(blocks):
                raise ValueError("body index reference outside exact public target body")
            block = blocks[ki]
            refs.append({"body_index": ki, "block_id": block["block_id"],
                "document_sha256": payload["context"]["target_document_sha256"],
                "source_sha256": block["source_sha256"], "source_alias": block["source_alias"],
                "quote": ref["quote"], "start": ref["start"], "end": ref["end"]})
        d["body_quote_refs"] = refs

        invariant = d["body_invariant"]
        if invariant is not None:
            _keys(invariant, _INVARIANT_SEMANTIC_KEYS, _INVARIANT_SEMANTIC_KEYS, "body invariant")
            if not isinstance(invariant.get("family_audit"), list):
                raise ValueError("body invariant family audit must be explicitly supplied")
            audits = invariant["family_audit"]
            if len(audits) != len(states):
                raise ValueError("family audit must cover every exact public source state")
            expanded = []
            for j, audit in enumerate(audits):
                _keys(audit, {"state_index", "applicability", "explanation"},
                      {"state_index", "applicability", "explanation"}, "family audit entry")
                if type(audit["state_index"]) is not int or audit["state_index"] != j:
                    raise ValueError("family audit indices must follow exact source order")
                state = states[j]
                expanded.append({**deepcopy(audit), "whole_state": deepcopy(state),
                                 "member_evidence_ids": deepcopy(state["member_evidence_ids"])})
            invariant["family_audit"] = expanded
            invariant["target_document_sha256"] = payload["context"]["target_document_sha256"]
        rendered.append(d)

    response = {"protocol": BODY.PROTOCOL, "pair_id": pair["pair_id"],
                "cache_key": pair["cache_key"], "decisions": rendered}
    BODY.validate_literal_response(response, pair, payload)
    return response
