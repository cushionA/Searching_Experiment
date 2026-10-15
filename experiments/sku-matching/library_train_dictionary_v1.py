#!/usr/bin/env python3
"""Train-only whole-string alias dictionary for the library SKU pipeline.

The builder accepts only the caller's TRAIN products, cases, requests,
documents, references, and dossier-to-independent-source-group map. It learns
axis aliases from product-local raw mutual-unique catalog overlap and value
aliases from explicitly positive TRAIN references. It defines no SKU grammar.

References are the canonical mapping ``case_id -> {label: matched|unmatched|
review, au_row_keys, source_answer_sha256}``. Values are mined only from
``matched`` TRAIN cases. Axis and value mappings are whole-string
equivalence groups over the fixed price-free raw view.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from typing import Any, Iterable, Mapping

SCHEMA = "library-train-dictionary-v1"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _valid_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _has_numeric(value: str) -> bool:
    return any(char.isnumeric() for char in value)


def _side_for_input(direction: str) -> str | None:
    return {"au_to_rakuten": "au", "rakuten_to_au": "rakuten"}.get(direction)


def _native_domains(product: Mapping[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Return only complete raw string domains seen in source product catalogs."""
    domains: dict[str, dict[str, list[str]]] = {"au": {}, "rakuten": {}}
    for row in product.get("au_rows", []) or []:
        for axis in row.get("axes", []) or []:
            name, value = axis.get("axis_name"), axis.get("value")
            if _valid_string(name) and _valid_string(value):
                bucket = domains["au"].setdefault(name, [])
                if value not in bucket:
                    bucket.append(value)
    for family in (product.get("rakuten_product", {}) or {}).get("selector_families", []) or []:
        name, values = family.get("label"), family.get("values")
        if _valid_string(name) and isinstance(values, list) and all(_valid_string(v) for v in values):
            bucket = domains["rakuten"].setdefault(name, [])
            bucket.extend(v for v in values if v not in bucket)
    return domains


def _request_domains(requests: list[dict[str, Any]], documents: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Reconstruct request-side native domains, rejecting inconsistent vectors."""
    docs = {d.get("document_id"): d for d in documents}
    vectors: dict[tuple[str, str, str], set[tuple[str, ...]]] = defaultdict(set)
    for req in requests:
        doc = docs.get(req.get("target_document_id"))
        side = _side_for_input(req.get("direction"))
        vals, axis = req.get("option_values"), req.get("axis_name")
        if doc and side and _valid_string(axis) and isinstance(vals, list) and all(_valid_string(v) for v in vals):
            vectors[(str(doc.get("dossier_id")), side, axis)].add(tuple(vals))
    result: dict[str, dict[str, dict[str, list[str]]]] = defaultdict(lambda: {"au": {}, "rakuten": {}})
    for (dossier, side, axis), found in vectors.items():
        if len(found) == 1:
            result[dossier][side][axis] = list(next(iter(found)))
    return result


def _merge_domains(products: list[dict[str, Any]], requests: list[dict[str, Any]],
                    documents: list[dict[str, Any]], raw_view: Mapping[str, str]
                    ) -> dict[str, dict[str, dict[str, list[str]]]]:
    from_requests = _request_domains(requests, documents)
    result: dict[str, dict[str, dict[str, list[str]]]] = {}
    for product in products:
        dossier = product.get("dossier_id")
        if not isinstance(dossier, str):
            continue
        native = _native_domains(product)
        requested = from_requests.get(dossier, {"au": {}, "rakuten": {}})
        result[dossier] = {"au": {}, "rakuten": {}}
        for side in ("au", "rakuten"):
            native_axes = set(native[side])
            for axis, values in native[side].items():
                # Use the same frozen price-free raw view as evaluation.
                mapped = [raw_view[value] for value in values if value in raw_view]
                if (len(mapped) == len(values) and all(_valid_string(v) for v in mapped)
                        and len(mapped) == len(set(mapped))):
                    result[dossier][side][axis] = mapped
            for axis, values in requested[side].items():
                if axis not in native_axes:
                    mapped = [raw_view[value] for value in values if value in raw_view]
                    if (len(mapped) == len(values) and all(_valid_string(v) for v in mapped)
                            and len(mapped) == len(set(mapped))):
                        result[dossier][side][axis] = mapped
    return result


def _axis_evidence(domains: Mapping[str, Mapping[str, Mapping[str, list[str]]]],
                   group_by_dossier: Mapping[str, str]) -> dict[tuple[str, str], dict[str, set[str]]]:
    """Collect cross-side axis edges from raw shared values and mutual uniqueness."""
    import networkx as nx
    evidence: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
        lambda: {"dossiers": set(), "groups": set()})
    for dossier, sides in domains.items():
        group = group_by_dossier.get(dossier)
        if not isinstance(group, str) or not group:
            continue
        graph = nx.Graph()
        au = sides.get("au", {})
        rk = sides.get("rakuten", {})
        edges: list[tuple[str, str]] = []
        for au_name, au_values in au.items():
            for rk_name, rk_values in rk.items():
                if len(set(au_values) & set(rk_values)) >= 2:
                    edges.append((au_name, rk_name))
        graph.add_nodes_from([("au", name) for name in au])
        graph.add_nodes_from([("rakuten", name) for name in rk])
        graph.add_edges_from((('au', a), ('rakuten', b)) for a, b in edges)
        for au_name, rk_name in edges:
            # Retain all raw overlap candidates so ambiguous components appear
            # in blocked_candidates; acceptance below requires a collision-free
            # one-to-one component, equivalent to mutual uniqueness here.
            pair = tuple(sorted((au_name, rk_name)))
            evidence[pair]["dossiers"].add(dossier)
            evidence[pair]["groups"].add(group)
    return evidence


def _matched_references(train_refs: Any, cases_by_id: Mapping[str, dict[str, Any]],
                       products_by_id: Mapping[str, dict[str, Any]],
                       axis_roles: Mapping[str, str], group_by_dossier: Mapping[str, str],
                       raw_view: Mapping[str, str]
                       ) -> dict[tuple[str, str, str], dict[str, set[str]]]:
    """Derive whole-value equivalence candidates from matched TRAIN cases.

    Every allowed AU row must expose exactly one axis for the source axis role,
    and all allowed rows must agree on the exact whole-string AU value.
    """
    if not isinstance(train_refs, Mapping):
        raise ValueError("train_refs must be the canonical case_id reference mapping")
    if set(train_refs) != set(cases_by_id):
        raise ValueError("reference case IDs must exactly equal the TRAIN case IDs")
    support: dict[tuple[str, str, str], dict[str, set[str]]] = defaultdict(
        lambda: {"dossiers": set(), "groups": set(), "cases": set(), "answers": set()})
    for case_id, reference in train_refs.items():
        if not isinstance(reference, Mapping) or reference.get("label") != "matched":
            continue
        case = cases_by_id[case_id]
        dossier = case.get("dossier_id")
        product = products_by_id.get(dossier)
        row_keys = reference.get("au_row_keys")
        if product is None or not isinstance(row_keys, list) or not row_keys:
            continue
        rows_by_key = {row.get("row_key"): row for row in product.get("au_rows", [])}
        rows = [rows_by_key.get(key) for key in row_keys]
        if any(row is None for row in rows):
            continue
        source_axes = case.get("rakuten_selected", {}).get("axes", [])
        if not isinstance(source_axes, list):
            continue
        answer_sha = reference.get("source_answer_sha256")
        for source_axis in source_axes:
            source_name, source_value = source_axis.get("axis_label"), source_axis.get("value")
            if not _valid_string(source_name) or not _valid_string(source_value):
                continue
            role = axis_roles.get(source_name, source_name)
            aligned_names = {name for name, candidate_role in axis_roles.items() if candidate_role == role}
            aligned_names.add(role)
            row_values: list[str] = []
            valid = True
            for row in rows:
                found = [axis.get("value") for axis in row.get("axes", [])
                         if axis.get("axis_name") in aligned_names]
                if len(found) != 1 or not _valid_string(found[0]):
                    valid = False
                    break
                row_values.append(found[0])
            if not valid or len(set(row_values)) != 1:
                continue
            target_value = row_values[0]
            if source_value not in raw_view or target_value not in raw_view:
                continue
            source_free, target_free = raw_view[source_value], raw_view[target_value]
            if (not _valid_string(source_free) or not _valid_string(target_free)
                    or source_free == target_free
                    or _has_numeric(source_value) or _has_numeric(target_value)
                    or _has_numeric(source_free) or _has_numeric(target_free)):
                continue
            members = sorted((source_free, target_free))
            key = (role, members[0], members[1])
            support[key]["dossiers"].add(dossier)
            support[key]["groups"].add(group_by_dossier[dossier])
            support[key]["cases"].add(case_id)
            if isinstance(answer_sha, str) and answer_sha:
                support[key]["answers"].add(answer_sha)
    return support


def _components(edges: Iterable[tuple[str, str]]) -> list[list[str]]:
    import networkx as nx
    graph = nx.Graph()
    graph.add_edges_from(edges)
    return [sorted(component) for component in nx.connected_components(graph) if len(component) > 1]


def _axis_blockers(members: list[str], domains: Mapping[str, Mapping[str, Mapping[str, list[str]]]]) -> list[str]:
    member_set = set(members)
    for sides in domains.values():
        for side in ("au", "rakuten"):
            present = member_set.intersection(sides.get(side, {}))
            if len(present) > 1:
                return ["same_side_axis_collision"]
    return []


def _value_blockers(members: list[str], axis_role: str,
                    domains: Mapping[str, Mapping[str, Mapping[str, list[str]]]],
                    axis_roles: Mapping[str, str]) -> list[str]:
    member_set = set(members)
    for sides in domains.values():
        for side in ("au", "rakuten"):
            for raw_axis, values in sides.get(side, {}).items():
                if axis_roles.get(raw_axis, raw_axis) == axis_role and len(member_set.intersection(values)) > 1:
                    return ["same_native_axis_value_collision"]
    return []


def build_dictionary(products: list[dict[str, Any]], cases: list[dict[str, Any]],
                     requests: list[dict[str, Any]], documents: list[dict[str, Any]],
                     raw_view: Mapping[str, str], train_refs: Mapping[str, dict[str, Any]],
                     group_by_dossier: Mapping[str, str], min_support: int) -> dict[str, Any]:
    """Build a frozen dictionary from a caller-enforced TRAIN-only population.

    ``min_support`` counts independent source groups, never rows or cases.
    Accepted values are 1 and 2, both predeclared experiment arms.
    """
    if min_support not in (1, 2):
        raise ValueError("min_support must be one of the two predeclared values: 1 or 2")
    product_ids = [p.get("dossier_id") for p in products]
    if any(not isinstance(i, str) or not i for i in product_ids) or len(product_ids) != len(set(product_ids)):
        raise ValueError("TRAIN products require unique dossier_id values")
    train_ids = set(product_ids)
    if any(dossier not in group_by_dossier for dossier in train_ids):
        raise ValueError("every TRAIN dossier must map to a split source_group_id")
    if any(not isinstance(group_by_dossier[dossier], str) or not group_by_dossier[dossier]
           for dossier in train_ids):
        raise ValueError("every TRAIN dossier must map to a nonempty source_group_id")
    case_ids = {c.get("case_id") for c in cases}
    if None in case_ids or len(case_ids) != len(cases):
        raise ValueError("TRAIN cases require unique case_id values")
    if any(c.get("dossier_id") not in train_ids for c in cases):
        raise ValueError("non-TRAIN case passed to dictionary builder")
    if not isinstance(train_refs, Mapping) or set(train_refs) != case_ids:
        raise ValueError("reference case IDs must exactly equal the TRAIN case IDs")
    if any(d.get("dossier_id") not in train_ids for d in documents):
        raise ValueError("non-TRAIN document passed to dictionary builder")
    docs_by_id = {d.get("document_id"): d for d in documents}
    if len(docs_by_id) != len(documents) or any(not isinstance(key, str) or not key for key in docs_by_id):
        raise ValueError("TRAIN documents require unique document_id values")
    if any(r.get("target_document_id") not in docs_by_id
           or docs_by_id[r.get("target_document_id")].get("dossier_id") not in train_ids
           for r in requests):
        raise ValueError("non-TRAIN request passed to dictionary builder")

    # Require complete frozen price-free view coverage; never substitute raw
    # text for a missing key.
    raw_values = set()
    for product in products:
        for row in product.get("au_rows", []):
            raw_values.update(axis.get("value") for axis in row.get("axes", [])
                              if _valid_string(axis.get("value")))
        for family in product.get("rakuten_product", {}).get("selector_families", []):
            raw_values.update(v for v in family.get("values", []) if _valid_string(v))
    for case in cases:
        raw_values.update(axis.get("value") for axis in case.get("rakuten_selected", {}).get("axes", [])
                          if _valid_string(axis.get("value")))
    for request in requests:
        raw_values.update(v for v in request.get("option_values", []) if _valid_string(v))
        raw_values.update(axis.get("value") for axis in request.get("target_selected_axes", [])
                          if _valid_string(axis.get("value")))
    if any(value not in raw_view or not isinstance(raw_view[value], str) for value in raw_values):
        raise ValueError("TRAIN raw values are not fully covered by the frozen price-free raw_view")

    domains = _merge_domains(products, requests, documents, raw_view)
    axis_evidence = _axis_evidence(domains, group_by_dossier)
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    eligible_axis_edges = []
    for pair, support in sorted(axis_evidence.items()):
        if len(support["groups"]) < min_support:
            blocked.append({"kind": "axis", "members": list(pair), "canonical": min(pair),
                            "supporting_product_ids": sorted(support["dossiers"]),
                            "supporting_group_ids": sorted(support["groups"]),
                            "support_case_ids": [], "evidence_kind": "raw_shared_catalog_values",
                            "blocked_reason": "insufficient_independent_source_groups"})
        else:
            eligible_axis_edges.append(pair)
    for members in _components(eligible_axis_edges):
        support = {"dossiers": set(), "groups": set()}
        for key in (tuple(sorted((a, b))) for a in members for b in members if a < b):
            row = axis_evidence.get(key)
            if row:
                support["dossiers"].update(row["dossiers"])
                support["groups"].update(row["groups"])
        canonical = min(members)
        reason = _axis_blockers(members, domains)
        entry = {"kind": "axis", "members": members, "canonical": canonical,
                 "supporting_product_ids": sorted(support["dossiers"]),
                 "supporting_group_ids": sorted(support["groups"]),
                 "support_case_ids": [], "evidence_kind": "raw_mutual_unique_shared_values",
                 "blocked_reason": reason[0] if reason else None}
        if reason:
            blocked.append(entry)
        elif len(support["groups"]) < min_support:
            blocked.append({**entry, "blocked_reason": "insufficient_independent_source_groups"})
        else:
            accepted.append(entry)

    axis_groups = [e for e in accepted if e["kind"] == "axis"]
    axis_canon: dict[str, str] = {}
    for entry in axis_groups:
        for member in entry["members"]:
            axis_canon[member] = entry["canonical"]
    products_by_id = {p["dossier_id"]: p for p in products}
    cases_by_id = {c["case_id"]: c for c in cases}
    value_support = _matched_references(train_refs, cases_by_id, products_by_id,
                                        axis_canon, group_by_dossier, raw_view)

    # Only join evidence-backed edges. Connected components that induce a
    # collision are retained in blocked_candidates for audit and never applied.
    by_role: dict[str, list[tuple[str, str]]] = defaultdict(list)
    eligible_value_edges: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for role, left, right in value_support:
        row = value_support[(role, left, right)]
        if len(row["groups"]) < min_support:
            blocked.append({"kind": "value", "axis_role": role,
                            "members": [left, right], "canonical": left,
                            "supporting_product_ids": sorted(row["dossiers"]),
                            "supporting_group_ids": sorted(row["groups"]),
                            "support_case_ids": sorted(row["cases"]),
                            "source_answer_sha256": sorted(row["answers"]),
                            "evidence_kind": "train_reference_positive_whole_value",
                            "blocked_reason": "insufficient_independent_source_groups"})
        else:
            eligible_value_edges[role].append((left, right))
    for role, edges in eligible_value_edges.items():
        for members in _components(edges):
            support = {"dossiers": set(), "groups": set(), "cases": set(), "answers": set()}
            for left in members:
                for right in members:
                    if left >= right:
                        continue
                    row = value_support.get((role, left, right))
                    if row:
                        for key in support:
                            support[key].update(row[key])
            reason = _value_blockers(members, role, domains, axis_canon)
            entry = {"kind": "value", "axis_role": role, "members": members,
                     "canonical": min(members),
                     "supporting_product_ids": sorted(support["dossiers"]),
                     "supporting_group_ids": sorted(support["groups"]),
                     "support_case_ids": sorted(support["cases"]),
                     "source_answer_sha256": sorted(support["answers"]),
                     "evidence_kind": "train_reference_positive_whole_value",
                     "blocked_reason": reason[0] if reason else None}
            if reason:
                blocked.append(entry)
            elif len(support["groups"]) < min_support:
                blocked.append({**entry, "blocked_reason": "insufficient_independent_source_groups"})
            else:
                accepted.append(entry)

    accepted.sort(key=lambda e: (e["kind"], e.get("axis_role", ""), e["members"]))
    blocked.sort(key=lambda e: (e["kind"], e.get("axis_role", ""), e["members"], e["blocked_reason"] or ""))
    evidence = {"train_case_ids": sorted(case_ids),
                "train_product_ids": sorted(train_ids),
                "group_by_dossier": {d: group_by_dossier[d] for d in sorted(train_ids)},
                "reference_count": len(train_refs),
                "positive_reference_count": sum(
                    isinstance(r, Mapping) and r.get("label") == "matched"
                    for r in train_refs.values()),
                "raw_request_ids": sorted(str(r.get("request_id")) for r in requests),
                "raw_view_sha256": _sha(dict(sorted(raw_view.items())))}
    return {"schema": SCHEMA, "min_independent_source_groups": min_support,
            "support_thresholds_predeclared": [1, 2], "accepted_alias_groups": accepted,
            "blocked_candidates": blocked, "evidence": evidence,
            "training_only": True, "uses_numeric_aliases": False,
            "whole_value_replacements_only": True}


def _group_maps(dictionary: Mapping[str, Any]) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    axis: dict[str, str] = {}
    values: dict[tuple[str, str], str] = {}
    for entry in dictionary.get("accepted_alias_groups", []):
        members, canonical = entry.get("members", []), entry.get("canonical")
        if not _valid_string(canonical) or not isinstance(members, list):
            continue
        if entry.get("kind") == "axis":
            axis.update({member: canonical for member in members if _valid_string(member)})
        elif entry.get("kind") == "value" and _valid_string(entry.get("axis_role")):
            values.update({(entry["axis_role"], member): canonical for member in members
                           if _valid_string(member) and not _has_numeric(member)})
    return axis, values


def _mapped_value(role: str, raw_value: str, value_map: Mapping[tuple[str, str], str],
                  raw_view: Mapping[str, str]) -> str | None:
    """Apply frozen price removal, then a nonnumeric whole-value alias."""
    if raw_value not in raw_view:
        return None
    view_value = raw_view[raw_value]
    if not isinstance(view_value, str) or not view_value:
        return None
    # A price-free transform must never launder a numeric SKU into a learned
    # alias candidate; keep the price-free string literal for numeric inputs.
    if _has_numeric(raw_value) or _has_numeric(view_value):
        return view_value
    return value_map.get((role, view_value), view_value)


def dictionary_request_state(request: dict[str, Any], docs: Mapping[str, dict[str, Any]],
                             catalogs: Mapping[tuple[str, str, str], set[tuple[str, ...]]],
                             dictionary: Mapping[str, Any], raw_view: Mapping[str, str]
                             ) -> tuple[str, str, dict[str, Any]]:
    """Apply accepted whole-string groups to a copied request/catalog context.

    Returns the existing selected-state triple ``(state, reason, trace)``.
    Any ambiguity, collision, missing counterpart, or incomplete family is
    held as unknown. Original request strings and catalogs are never mutated.
    """
    from run_installed_library_selection_v1 import selected_state

    axis_map, value_map = _group_maps(dictionary)
    original_axis = request.get("axis_name")
    canonical_axis = axis_map.get(original_axis, original_axis)
    doc = docs.get(request.get("target_document_id"), {})
    dossier, target_side = doc.get("dossier_id"), doc.get("side")
    source_side = _side_for_input(request.get("direction"))
    if source_side not in {"au", "rakuten"} or target_side not in {"au", "rakuten"}:
        return "unknown", "invalid_source_or_target_side", {"axis_name_raw": original_axis}

    # Axis group may not collapse two native axes on either side.
    for side in (source_side, target_side):
        names = [name for d, s, name in catalogs if d == dossier and s == side]
        normalized = [axis_map.get(name, name) for name in names]
        if len(normalized) != len(set(normalized)):
            return "unknown", "dictionary_axis_catalog_collision", {
                "axis_name_raw": original_axis, "axis_name_canonical": canonical_axis,
                "colliding_side": side}

    source_keys = [key for key in catalogs if key[0] == dossier and key[1] == source_side
                   and axis_map.get(key[2], key[2]) == canonical_axis]
    target_keys = [key for key in catalogs if key[0] == dossier and key[1] == target_side
                   and axis_map.get(key[2], key[2]) == canonical_axis]
    if len(source_keys) != 1 or len(target_keys) != 1:
        return "unknown", "dictionary_axis_counterpart_missing_or_ambiguous", {
            "source_axis_keys": [k[2] for k in source_keys],
            "target_axis_keys": [k[2] for k in target_keys],
            "axis_name_raw": original_axis, "axis_name_canonical": canonical_axis}

    source_axis_name, target_axis_name = source_keys[0][2], target_keys[0][2]
    source_values_set, target_values_set = catalogs[source_keys[0]], catalogs[target_keys[0]]
    if len(source_values_set) != 1 or len(target_values_set) != 1:
        return "unknown", "dictionary_catalog_missing_or_inconsistent", {}
    source_values, target_values = list(next(iter(source_values_set))), list(next(iter(target_values_set)))
    if not source_values or not target_values or any(not _valid_string(v) for v in source_values + target_values):
        return "unknown", "incomplete_native_catalog", {}
    role = canonical_axis
    if any(value not in raw_view for value in source_values + target_values):
        return "unknown", "price_free_view_missing_catalog_value", {}
    source_mapped = [_mapped_value(role, value, value_map, raw_view) for value in source_values]
    target_mapped = [_mapped_value(role, value, value_map, raw_view) for value in target_values]
    if any(value is None for value in source_mapped + target_mapped):
        return "unknown", "price_free_view_missing_catalog_value", {}
    if len(set(source_mapped)) != len(source_mapped) or len(set(target_mapped)) != len(target_mapped):
        return "unknown", "dictionary_catalog_value_collision", {
            "source_axis": source_axis_name, "target_axis": target_axis_name}

    selected = request.get("selected_value")
    selected_axes = request.get("target_selected_axes", [])
    target_matches = [a for a in selected_axes if axis_map.get(a.get("axis_name"), a.get("axis_name")) == canonical_axis]
    if not _valid_string(selected) or selected not in source_values:
        return "unknown", "selected_value_not_in_source_catalog", {}
    if len(target_matches) != 1 or not _valid_string(target_matches[0].get("value")):
        return "unknown", "target_axis_missing_or_ambiguous", {}
    target_selected = target_matches[0]["value"]
    if target_selected not in target_values:
        return "unknown", "selected_value_not_in_target_catalog", {}
    if selected not in raw_view or target_selected not in raw_view:
        return "unknown", "price_free_view_missing_selected_value", {}
    selected_key = _mapped_value(role, selected, value_map, raw_view)
    target_key = _mapped_value(role, target_selected, value_map, raw_view)
    if selected_key is None or target_key is None:
        return "unknown", "price_free_view_missing_selected_value", {}
    if selected_key not in target_mapped:
        return "unknown", "selected_value_has_no_catalog_counterpart", {}
    # Reuse the existing selected-state gate on copied, transformed structures.
    # Dictionary resolution is context-specific to this axis role.
    transformed_catalogs: dict[tuple[str, str, str], set[tuple[str, ...]]] = {}
    for (dossier_id, side, axis_name), vectors in catalogs.items():
        if dossier_id != dossier:
            continue
        canonical_name = axis_map.get(axis_name, axis_name)
        transformed_vectors = set()
        for vector in vectors:
            mapped_vector = [_mapped_value(canonical_name, value, value_map, raw_view) for value in vector]
            if any(value is None for value in mapped_vector):
                continue
            transformed_vectors.add(tuple(mapped_vector))
        transformed_catalogs[(dossier_id, side, canonical_name)] = transformed_vectors
    transformed_request = dict(request)
    transformed_request["axis_name"] = canonical_axis
    if any(value not in raw_view for value in request.get("option_values", [])):
        return "unknown", "price_free_view_missing_source_value", {}
    transformed_option_values = [_mapped_value(role, value, value_map, raw_view)
                                  for value in request.get("option_values", [])]
    if any(value is None for value in transformed_option_values):
        return "unknown", "price_free_view_missing_source_value", {}
    transformed_request["option_values"] = transformed_option_values
    transformed_request["selected_value"] = selected_key
    transformed_request["target_selected_axes"] = []
    for axis in request.get("target_selected_axes", []):
        axis_role = axis_map.get(axis.get("axis_name"), axis.get("axis_name"))
        value = axis.get("value")
        mapped_axis_value = _mapped_value(axis_role, value, value_map, raw_view) if _valid_string(value) else None
        if axis_role == canonical_axis and mapped_axis_value is None:
            return "unknown", "price_free_view_missing_selected_value", {}
        transformed_request["target_selected_axes"].append({
            **axis, "axis_name": axis_role,
            "value": mapped_axis_value if mapped_axis_value is not None else value})
    transformed_docs = dict(docs)
    state, reason = selected_state(transformed_request, transformed_docs,
                                   transformed_catalogs, {v: v for vectors in transformed_catalogs.values()
                                                          for vector in vectors for v in vector})
    trace = {"axis_name_raw": original_axis, "axis_name_canonical": canonical_axis,
             "source_axis_raw": source_axis_name, "target_axis_raw": target_axis_name,
             "selected_value_raw": selected, "target_selected_value_raw": target_selected,
             "selected_value_canonical": selected_key, "target_value_canonical": target_key,
             "source_catalog_values_raw": source_values, "target_catalog_values_raw": target_values,
             "source_catalog_values_canonical": source_mapped,
             "target_catalog_values_canonical": target_mapped,
             "dictionary_applied": True, "selected_state_reason": reason}
    return state, reason, trace


__all__ = ["SCHEMA", "build_dictionary", "dictionary_request_state"]
