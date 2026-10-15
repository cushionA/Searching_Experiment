#!/usr/bin/env python3
"""Conservative, label-free matching of complete cross-store SKU families.

This module compares native option families recorded by the original
bidirectional requests. Axis labels must match literally. Complete family
members must admit a unique one-to-one mapping before an individual selected
condition can produce support or conflict. Similarity and aliases are emitted
only as review suggestions and never affect a state.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import difflib
from functools import lru_cache
import json
from typing import Any, Iterable, Mapping

import sku_classical_quantity_v1 as QUANTITY


SCHEMA = "sku-classical-matcher-v1"
_SIDES = {"au", "rakuten"}
_DIRECTIONS = {"rakuten_to_au": ("rakuten", "au"),
               "au_to_rakuten": ("au", "rakuten")}

# These are deliberately hints only. They never normalize an axis or value
# used to support, conflict, or validate a family bijection.
_AXIS_ALIAS_GROUPS = (
    frozenset({"色", "カラー", "color", "colour"}),
    frozenset({"サイズ", "size"}),
    frozenset({"幅", "横幅", "width"}),
    frozenset({"高さ", "height"}),
    frozenset({"長さ", "丈", "length"}),
    frozenset({"奥行", "depth"}),
    frozenset({"素材", "材質", "material"}),
)
_COLOR_ALIAS_GROUPS = (
    frozenset({"black", "黒", "ブラック", "クロ"}),
    frozenset({"white", "白", "ホワイト", "シロ"}),
    frozenset({"gray", "grey", "灰", "グレー"}),
    frozenset({"red", "赤", "レッド"}),
    frozenset({"blue", "青", "ブルー"}),
    frozenset({"green", "緑", "グリーン"}),
    frozenset({"pink", "ピンク"}),
    frozenset({"beige", "ベージュ"}),
    frozenset({"brown", "茶", "ブラウン"}),
    frozenset({"navy", "紺", "ネイビー"}),
)


@lru_cache(maxsize=131_072)
def _signature_key(value: str, mode: str) -> str:
    if mode == "literal":
        signature: Any = {"raw": value}
    else:
        parsed = QUANTITY.parse_value(value)
        if parsed.get("parse_status") == "empty":
            # Empty values are never positive evidence, even in literal mode.
            signature = {"empty": True, "raw": value}
        else:
            signature = QUANTITY.canonical_signature(parsed)
    return json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique(values: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _field_fingerprint(value: Any) -> str:
    """Stable exact snapshot for binding request fields without deep copies."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_score(query: str, candidates: list[str]) -> list[dict[str, Any]]:
    """Return advisory text similarities; callers must not use them as proof."""
    if not query or not candidates:
        return []
    try:
        from rapidfuzz import fuzz, process  # type: ignore[import-not-found]

        matches = process.extract(query, candidates, scorer=fuzz.ratio, limit=5)
        return [{"value": value, "score": round(float(score), 3), "method": "rapidfuzz_ratio"}
                for value, score, _ in matches if score > 0]
    except ImportError:
        matches = [(candidate, difflib.SequenceMatcher(None, query, candidate).ratio() * 100)
                   for candidate in candidates]
        matches.sort(key=lambda item: (-item[1], candidates.index(item[0])))
        return [{"value": value, "score": round(score, 3), "method": "difflib_ratio_fallback"}
                for value, score in matches[:5] if score > 0]


def _alias_groups_for(value: str, groups: tuple[frozenset[str], ...]) -> list[str]:
    folded = value.casefold()
    for group in groups:
        if folded in {member.casefold() for member in group}:
            return sorted(group)
    return []


def _suggestions(source_name: str, target_names: list[str],
                 source_values: list[str] | None = None,
                 target_values: list[str] | None = None,
                 target_axis_families: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    aliases = _alias_groups_for(source_name, _AXIS_ALIAS_GROUPS)
    alias_hits = [name for name in target_names
                  if name.casefold() in {member.casefold() for member in aliases}]
    axis_fuzzy = _candidate_score(source_name, target_names)
    fuzzy_by_name = {row["value"]: row["score"] for row in axis_fuzzy}
    axis_family_differences = []
    for target_name in target_names:
        family = (target_axis_families or {}).get(target_name)
        if not isinstance(family, Mapping):
            continue
        alias_match = target_name in alias_hits
        score = fuzzy_by_name.get(target_name, 0.0)
        if target_name != source_name and not alias_match and score < 20:
            continue
        target_family_values = family.get("family_values")
        if not isinstance(target_family_values, list):
            continue
        differences = _array_difference(source_values or [], target_family_values, "typed")
        axis_family_differences.append({
            "target_axis_name": target_name,
            "axis_alias_match": alias_match,
            "axis_fuzzy_score": score,
            "family_state": "candidate_only",
            "array_difference": differences,
        })
    value_suggestions = []
    for source_value in source_values or []:
        alias_values = _alias_groups_for(source_value, _COLOR_ALIAS_GROUPS)
        alias_set = {member.casefold() for member in alias_values}
        candidates = target_values or []
        alias_matches = [value for value in candidates if value.casefold() in alias_set]
        fuzzy = _candidate_score(source_value, candidates)
        if alias_matches or fuzzy:
            value_suggestions.append({"source_value": source_value,
                                      "color_alias_candidates": alias_matches,
                                      "fuzzy_candidates": fuzzy})
    return {"axis_alias_candidates": _unique(alias_hits),
            "axis_fuzzy_candidates": axis_fuzzy,
            "axis_family_candidates": axis_family_differences,
            "value_suggestions": value_suggestions,
            "advisory_only": True}


def _array_difference(source_values: list[str], target_values: list[str], mode: str) -> dict[str, Any]:
    """Report ordered raw members left unmatched by advisory signatures only."""
    remaining: dict[str, list[int]] = defaultdict(list)
    for index, value in enumerate(target_values):
        try:
            key = _signature_key(value, mode)
        except Exception:
            key = "invalid:" + value
        remaining[key].append(index)
    source_only = []
    matched = []
    for source_index, value in enumerate(source_values):
        try:
            key = _signature_key(value, mode)
        except Exception:
            key = "invalid:" + value
        indexes = remaining.get(key) or []
        if indexes:
            target_index = indexes.pop(0)
            matched.append({"source_index": source_index, "source_value": value,
                            "target_index": target_index, "target_value": target_values[target_index]})
        else:
            source_only.append({"source_index": source_index, "value": value})
    target_only = [{"target_index": index, "value": value}
                   for index, value in enumerate(target_values)
                   if any(index in indexes for indexes in remaining.values())]
    return {"mode": mode, "matched_candidates": matched,
            "source_only": source_only, "target_only": target_only,
            "advisory_only": True}


def _family_entry(document_id: str, dossier_id: str, side: str, axis_name: str,
                  vectors: list[tuple[list[str] | None, str, str | None]]) -> dict[str, Any]:
    valid_vectors = [tuple(vector) for vector, _, _ in vectors if vector is not None]
    distinct = _unique(valid_vectors)
    malformed_vector = any(vector is None for vector, _, _ in vectors)
    conflicting_vectors = len(distinct) > 1
    duplicate_values = any(len(vector) != len(set(vector)) for vector in distinct)
    consistent = not malformed_vector and len(distinct) == 1
    stored_vectors = [list(vector) for vector in distinct]
    family_values = stored_vectors[0] if consistent else []
    empty_member = any(vector is not None and any(not value for value in vector)
                       for vector, _, _ in vectors)
    vector_index = {vector: index for index, vector in enumerate(distinct)}
    return {
        "document_id": document_id,
        "dossier_id": dossier_id,
        "side": side,
        "axis_name": axis_name,
        "family_values": family_values,
        "request_ids": [request_id for _, request_id, _ in vectors],
        # Store each distinct vector once; retain every request binding without
        # copying its usually identical option array into this audit list.
        "source_vectors": stored_vectors,
        "vector_request_ids": [
            {"source_vector_index": vector_index.get(tuple(vector)) if vector is not None else None,
             "request_id": request_id, "request_sha256": request_sha}
            for vector, request_id, request_sha in vectors],
        "consistent": consistent,
        "duplicate_values": duplicate_values,
        "valid": consistent and bool(family_values) and not duplicate_values and not empty_member,
        "reasons": (["conflicting_option_values_vectors"] if conflicting_vectors else []) +
                   (["malformed_option_values_vector"] if malformed_vector else []) +
                   (["duplicate_family_value"] if duplicate_values else []) +
                   (["empty_family_member"] if empty_member else []) +
                   (["empty_family"] if consistent and not family_values else []),
    }


def build_families(requests: list[dict[str, Any]],
                   documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconstruct native whole families from all original directional requests.

    A request's ``option_values`` belongs to the document opposite its target.
    The target family is reconstructed from requests in the reverse direction.
    Documents are paired only by a unique same-dossier, opposite-side identity.
    """
    docs_by_id: dict[str, dict[str, Any]] = {}
    duplicate_doc_ids: set[str] = set()
    for doc in documents:
        doc_id = doc.get("document_id")
        if not isinstance(doc_id, str) or not doc_id:
            continue
        if doc_id in docs_by_id:
            duplicate_doc_ids.add(doc_id)
        else:
            docs_by_id[doc_id] = doc

    docs_by_dossier_side: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for doc in documents:
        dossier = doc.get("dossier_id")
        side = doc.get("side")
        if dossier is not None and side in _SIDES:
            docs_by_dossier_side[(dossier, side)].append(doc)

    family_vectors: dict[tuple[str, str], list[tuple[list[str] | None, str, str | None]]] = defaultdict(list)
    request_bindings: dict[str, dict[str, Any]] = {}
    request_errors: dict[str, list[str]] = {}

    for ordinal, request in enumerate(requests):
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            request_id = f"__missing_request_id_{ordinal}"
        errors: list[str] = []
        direction = request.get("direction")
        source_side, target_side = _DIRECTIONS.get(direction, (None, None))
        target_id = request.get("target_document_id")
        target = docs_by_id.get(target_id) if isinstance(target_id, str) else None
        if request_id in request_bindings:
            errors.append("duplicate_request_id")
        if request_id.startswith("__missing_request_id_"):
            errors.append("missing_request_id")
        if target_id in duplicate_doc_ids:
            errors.append("duplicate_target_document_id")
        if not target:
            errors.append("missing_target_document")
        if source_side is None:
            errors.append("invalid_direction")
        if target and target.get("side") != target_side:
            errors.append("direction_target_side_mismatch")

        source_doc = None
        if target and source_side:
            dossier = target.get("dossier_id")
            candidates = docs_by_dossier_side.get((dossier, source_side), [])
            candidates = [doc for doc in candidates if doc.get("document_id") != target_id]
            if len(candidates) == 1:
                source_doc = candidates[0]
            elif not candidates:
                errors.append("missing_opposite_source_document")
            else:
                errors.append("ambiguous_opposite_source_document")

        axis_name = request.get("axis_name")
        values = request.get("option_values")
        if not isinstance(axis_name, str) or not axis_name:
            errors.append("missing_axis_name")
        if (not isinstance(values, list) or not values or
                any(not isinstance(value, str) or not value for value in values)):
            errors.append("malformed_option_values")
        if isinstance(values, list) and any(not isinstance(value, str) for value in values):
            errors.append("malformed_option_values")
        if source_doc and isinstance(axis_name, str) and axis_name:
            if (isinstance(values, list) and values and
                    all(isinstance(value, str) and value for value in values)):
                family_vector: list[str] | None = list(values)
            else:
                family_vector = None
            request_sha = request.get("request_sha256")
            family_vectors[(source_doc["document_id"], axis_name)].append(
                (family_vector, request_id, request_sha if isinstance(request_sha, str) else None))
        request_bindings[request_id] = {
            "request_id": request_id,
            "target_document_id": target_id,
            "source_document_id": source_doc.get("document_id") if source_doc else None,
            "dossier_id": target.get("dossier_id") if target else None,
            "direction": direction,
            "request_option_values_fingerprint": _field_fingerprint(values),
            "request_selected_value_fingerprint": _field_fingerprint(request.get("selected_value")),
            "request_target_selected_axes_fingerprint": _field_fingerprint(request.get("target_selected_axes")),
            "source_side": source_side,
            "target_side": target_side,
            "axis_name": axis_name,
            "errors": errors,
        }
        if errors:
            request_errors[request_id] = errors

    source_families: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (document_id, axis_name), vectors in family_vectors.items():
        doc = docs_by_id.get(document_id) or {}
        source_families[document_id][axis_name] = _family_entry(
            document_id, str(doc.get("dossier_id")), str(doc.get("side")), axis_name, vectors)

    request_pairs: dict[str, dict[str, Any]] = {}
    for request in requests:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            continue
        binding = request_bindings.get(request_id, {})
        source_id, target_id = binding.get("source_document_id"), binding.get("target_document_id")
        axis_name = binding.get("axis_name")
        source_family = source_families.get(source_id, {}).get(axis_name) if source_id and axis_name else None
        # Reverse-direction requests make the current target document the
        # source of its own native families. Never copy the current source
        # family's vector onto the target side.
        target_family = source_families.get(target_id, {}).get(axis_name) if target_id and axis_name else None
        target_doc = docs_by_id.get(target_id) if isinstance(target_id, str) else None
        target_names = sorted(source_families.get(target_id, {}).keys()) if target_id else []
        suggestions = _suggestions(axis_name, target_names,
                                   source_family.get("family_values", []) if source_family else None,
                                   target_family.get("family_values", []) if target_family else None,
                                   source_families.get(target_id, {}) if target_id else None) \
            if isinstance(axis_name, str) else {"advisory_only": True}
        target_selected_axes = request.get("target_selected_axes")
        selected_matches = []
        if isinstance(target_selected_axes, list) and isinstance(axis_name, str):
            selected_matches = [axis.get("value") for axis in target_selected_axes
                                if isinstance(axis, dict) and axis.get("axis_name") == axis_name]
        target_selected_value = selected_matches[0] if len(selected_matches) == 1 else None
        request_pairs[request_id] = {
            **deepcopy(binding),
            # Shared immutable-by-convention family records avoid copying each
            # complete option vector into every row/request pair.
            "source_family": source_family,
            "target_family": target_family,
            "target_document_dossier_id": target_doc.get("dossier_id") if target_doc else None,
            "target_selected_values_for_axis": selected_matches,
            "target_selected_value": target_selected_value,
            "target_axis_names": target_names,
            "target_axis_families": source_families.get(target_id, {}) if target_id else {},
            "suggestions": suggestions,
        }

    return {"schema": SCHEMA,
            "source_families": {doc: dict(axes) for doc, axes in source_families.items()},
            "request_pairs": request_pairs,
            "request_errors": request_errors,
            "duplicate_document_ids": sorted(duplicate_doc_ids),
            "document_count": len(documents), "request_count": len(requests)}


@lru_cache(maxsize=65_536)
def _family_bijection_cached(source_key: tuple[Any, ...],
                             target_key: tuple[Any, ...],
                             mode: str) -> tuple[Any, ...]:
    """Cache an immutable complete-family comparison by raw ordered inputs."""
    (source_doc, source_dossier, source_side, source_axis, source_valid,
     source_reasons, left) = source_key
    (target_doc, target_dossier, target_side, target_axis, target_valid,
     target_reasons, right) = target_key
    reasons = []
    if source_axis != target_axis:
        reasons.append("axis_name_not_identical")
    if source_doc == target_doc:
        reasons.append("same_document_on_both_family_sides")
    if source_dossier != target_dossier:
        reasons.append("dossier_mismatch")
    if source_side == target_side or source_side not in _SIDES or target_side not in _SIDES:
        reasons.append("family_sides_not_opposite")
    if source_valid is not True:
        reasons.extend(source_reasons or ("invalid_source_family",))
    if target_valid is not True:
        reasons.extend(target_reasons or ("invalid_target_family",))
    if not left or not right:
        reasons.append("empty_or_malformed_family")
    if reasons:
        return ("unknown", tuple(_unique(reasons)), (), (), ())
    if len(left) != len(right):
        return ("unknown", ("family_lengths_differ",), (), (), ())

    try:
        left_keys = [_signature_key(value, mode) for value in left]
        right_keys = [_signature_key(value, mode) for value in right]
    except Exception:
        return ("unknown", ("value_parse_failed",), (), (), ())
    if len(left_keys) != len(set(left_keys)) or len(right_keys) != len(set(right_keys)):
        return ("unknown", ("canonical_family_collision",), (), (), ())

    support_edges: list[tuple[int, int]] = []
    for source_index, source_value in enumerate(left):
        for target_index, target_value in enumerate(right):
            if mode == "literal":
                comparison = {"state": "support" if source_value == target_value else "unknown",
                              "reasons": ["exact_raw_value_equal" if source_value == target_value else "literal_values_differ"]}
            else:
                try:
                    comparison = QUANTITY.compare_values(source_value, target_value)
                except Exception:
                    comparison = {"state": "unknown", "reasons": ["value_parse_failed"]}
            if comparison.get("state") == "support":
                support_edges.append((source_index, target_index))
    source_degrees = [0] * len(left)
    target_degrees = [0] * len(right)
    for source_index, target_index in support_edges:
        source_degrees[source_index] += 1
        target_degrees[target_index] += 1
    if (any(degree != 1 for degree in source_degrees) or any(degree != 1 for degree in target_degrees)
            or len(support_edges) != len(left)):
        return ("unknown", ("family_not_unique_one_to_one",), (),
                tuple(source_degrees), tuple(target_degrees))
    mapping = tuple(sorted(support_edges))
    return ("support", ("complete_unique_family_bijection",), mapping,
            tuple(source_degrees), tuple(target_degrees))


def _family_cache_key(family: Mapping[str, Any] | None) -> tuple[Any, ...] | None:
    if not isinstance(family, Mapping):
        return None
    values = family.get("family_values")
    if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
        return None
    reasons = family.get("reasons") or ()
    return (family.get("document_id"), family.get("dossier_id"), family.get("side"),
            family.get("axis_name"), family.get("valid") is True, tuple(reasons), tuple(values))


def _family_bijection(source: Mapping[str, Any] | None,
                      target: Mapping[str, Any] | None,
                      mode: str) -> dict[str, Any]:
    source_key, target_key = _family_cache_key(source), _family_cache_key(target)
    if source_key is None or target_key is None:
        return {"state": "unknown", "reasons": ["missing_or_malformed_axis_family"], "mapping": []}
    state, reasons, indexed_mapping, source_degrees, target_degrees = _family_bijection_cached(
        source_key, target_key, mode)
    left, right = source_key[-1], target_key[-1]
    mapping = [{"source_index": source_index, "source_value": left[source_index],
                "target_index": target_index, "target_value": right[target_index]}
               for source_index, target_index in indexed_mapping]
    result = {"state": state, "reasons": list(reasons), "mapping": mapping}
    if source_degrees:
        result["source_degrees"] = list(source_degrees)
    if target_degrees:
        result["target_degrees"] = list(target_degrees)
    return result


def evaluate_request(request: dict[str, Any], families: dict[str, Any],
                     mode: str = "typed") -> dict[str, Any]:
    """Conservatively compare one selected source value with the target row.

    ``mode='literal'`` is the exact-string baseline. ``mode='typed'`` uses the
    existing quantity parser, but still requires a unique complete-family
    bijection and exact axis-name identity. Candidate suggestions are never
    consulted for state assignment.
    """
    if mode not in {"literal", "typed"}:
        raise ValueError("mode must be 'literal' or 'typed'")
    request_id = request.get("request_id")
    pair_map = families.get("request_pairs", families)
    pair = pair_map.get(request_id) if isinstance(pair_map, Mapping) else None
    if not isinstance(pair, Mapping):
        return {"schema": SCHEMA, "request_id": request_id, "mode": mode,
                "state": "unknown", "reasons": ["request_family_binding_missing"],
                "family_state": "unknown", "family_mapping": [],
                "suggestions": {"advisory_only": True}}
    reasons = list(pair.get("errors") or [])
    source_family, target_family = pair.get("source_family"), pair.get("target_family")
    source_axis = request.get("axis_name")
    target_axes = request.get("target_selected_axes")
    if not isinstance(target_axes, list):
        reasons.append("target_selected_axes_missing")
    if source_axis != pair.get("axis_name"):
        reasons.append("request_axis_binding_mismatch")
    for field, pair_field in (("option_values", "request_option_values_fingerprint"),
                              ("selected_value", "request_selected_value_fingerprint"),
                              ("target_selected_axes", "request_target_selected_axes_fingerprint")):
        if pair_field in pair and _field_fingerprint(request.get(field)) != pair.get(pair_field):
            reasons.append("request_" + field + "_binding_mismatch")
    for field in ("target_document_id", "direction"):
        if field in pair and request.get(field) != pair.get(field):
            reasons.append("request_" + field + "_binding_mismatch")
    selected_source = request.get("selected_value")
    selected_matches = []
    if isinstance(source_family, Mapping):
        values = source_family.get("family_values")
        if isinstance(values, list):
            selected_matches = [i for i, value in enumerate(values) if value == selected_source]
    if len(selected_matches) != 1:
        reasons.append("selected_source_value_not_unique_family_member")
    target_values = pair.get("target_selected_values_for_axis")
    target_value = pair.get("target_selected_value")
    if not isinstance(target_values, list) or len(target_values) != 1 or not isinstance(target_value, str) or not target_value:
        reasons.append("target_selected_axis_missing_or_ambiguous")
    target_family_values = (target_family.get("family_values")
                            if isinstance(target_family, Mapping) else None)
    if (isinstance(target_family_values, list) and isinstance(target_value, str)
            and target_family_values.count(target_value) != 1):
        reasons.append("target_selected_value_not_unique_family_member")

    family_result = _family_bijection(source_family, target_family, mode)
    if family_result["state"] != "support":
        reasons.extend(family_result.get("reasons", []))
    if reasons:
        state = "unknown"
        condition_reasons = _unique(reasons)
    else:
        source_index = selected_matches[0]
        mapping = family_result["mapping"]
        mapped = next((entry for entry in mapping if entry["source_index"] == source_index), None)
        if mapped is None:
            state, condition_reasons = "unknown", ["selected_value_mapping_missing"]
        elif mapped["target_value"] == target_value:
            state, condition_reasons = "support", ["selected_target_value_matches_family_mapping"]
        else:
            state, condition_reasons = "conflict", ["selected_target_value_differs_under_complete_family_mapping"]
    return {"schema": SCHEMA, "request_id": request_id, "mode": mode,
            "state": state, "reasons": condition_reasons,
            "family_state": family_result["state"],
            "family_reasons": family_result.get("reasons", []),
            "family_mapping": family_result.get("mapping", []),
            "selected_source_value": selected_source,
            "target_selected_value": target_value,
            "axis_name": source_axis,
            "candidate_suggestions": deepcopy(pair.get("suggestions") or {"advisory_only": True}),
            "suggestions_affect_state": False}


__all__ = ["SCHEMA", "build_families", "evaluate_request"]
