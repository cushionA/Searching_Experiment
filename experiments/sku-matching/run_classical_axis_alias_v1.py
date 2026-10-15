#!/usr/bin/env python3
"""Run a separate DEV diagnostic with three predeclared generic axis aliases."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import run_classical_sku_v1 as R
import sku_classical_matcher_v1 as Base

VARIANTS = ("literal", "typed", "axis_alias")
_GROUPS = (
    ("color", frozenset({"色", "カラー", "color", "colour"}), "カラー"),
    ("size", frozenset({"サイズ", "size"}), "サイズ"),
    ("material", frozenset({"素材", "材質", "material"}), "素材"),
)


def _axis(label: Any) -> tuple[str, str | None]:
    if not isinstance(label, str):
        return label, None
    folded = label.casefold()
    for group_id, members, canonical in _GROUPS:
        if folded in members:
            return canonical, group_id
    return label, None


def _fp(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _alias_families(native: dict[str, dict[str, dict[str, Any]]]):
    result, mapping = {}, {}
    for document_id, axes in native.items():
        groups: dict[str, list[tuple[str, dict[str, Any], str | None]]] = {}
        for raw_name, family in axes.items():
            canonical, group_id = _axis(raw_name)
            groups.setdefault(canonical, []).append((raw_name, family, group_id))
        result[document_id], mapping[document_id] = {}, {}
        for canonical, members in groups.items():
            raw_names = sorted({raw for raw, _, _ in members})
            group_ids = {group for _, _, group in members}
            if len(raw_names) != 1:
                result[document_id][canonical] = {
                    "document_id": document_id,
                    "dossier_id": members[0][1].get("dossier_id"),
                    "side": members[0][1].get("side"),
                    "axis_name": canonical, "family_values": [], "valid": False,
                    "reasons": ["canonical_axis_label_collision"],
                    "original_axis_names": raw_names,
                    "original_families": [family for _, family, _ in members],
                }
            else:
                raw_name, family, _ = members[0]
                result[document_id][canonical] = {**family, "axis_name": canonical,
                    "raw_axis_name": raw_name}
            mapping[document_id][canonical] = {
                "canonical_axis_name": canonical, "original_axis_names": raw_names,
                "alias_group": next(iter(group_ids)) if len(group_ids) == 1 else None,
                "collision": len(raw_names) != 1,
            }
    return result, mapping


def build_families(requests, documents):
    base = Base.build_families(requests, documents)
    aliases, mappings = _alias_families(base["source_families"])
    pairs = {}
    for request in requests:
        request_id = request.get("request_id")
        raw_pair = base["request_pairs"].get(request_id)
        if not isinstance(raw_pair, dict):
            continue
        raw_axis = request.get("axis_name")
        canonical, group_id = _axis(raw_axis)
        source_id, target_id = raw_pair.get("source_document_id"), raw_pair.get("target_document_id")
        source_family = aliases.get(source_id, {}).get(canonical)
        target_family = aliases.get(target_id, {}).get(canonical)
        raw_target_axes = request.get("target_selected_axes")
        target_axes = []
        target_labels = []
        if isinstance(raw_target_axes, list):
            for axis in raw_target_axes:
                if not isinstance(axis, dict):
                    continue
                normalized_axis, _ = _axis(axis.get("axis_name"))
                copy_axis = dict(axis)
                copy_axis["axis_name"] = normalized_axis
                target_axes.append(copy_axis)
                if normalized_axis == canonical:
                    target_labels.append(axis.get("axis_name"))
        errors = list(raw_pair.get("errors") or [])
        source_map = mappings.get(source_id, {}).get(canonical, {})
        target_map = mappings.get(target_id, {}).get(canonical, {})
        if source_map.get("collision"):
            errors.append("source_canonical_axis_label_collision")
        if target_map.get("collision"):
            errors.append("target_canonical_axis_label_collision")
        if len({label for label in target_labels if isinstance(label, str)}) > 1:
            errors.append("target_selected_axis_alias_collision")
        alias_pair = dict(raw_pair)
        alias_pair.update({
            "axis_name": canonical,
            "source_family": source_family,
            "target_family": target_family,
            "target_selected_values_for_axis": [axis["value"] for axis in target_axes
                if axis.get("axis_name") == canonical and isinstance(axis.get("value"), str)],
            "errors": errors,
            "request_option_values_fingerprint": _fp(request.get("option_values")),
            "request_selected_value_fingerprint": _fp(request.get("selected_value")),
            "request_target_selected_axes_fingerprint": _fp(target_axes),
        })
        alias_pair["target_selected_value"] = (
            alias_pair["target_selected_values_for_axis"][0]
            if len(alias_pair["target_selected_values_for_axis"]) == 1 else None)
        pairs[request_id] = {
            "pair": alias_pair,
            "normalized_target_selected_axes": target_axes,
            "axis_mapping": {
                "rule": "predeclared_generic_axis_alias_v1",
                "alias_group": group_id,
                "source": source_map,
                "target": target_map,
                "target_selected_axis_labels": target_labels,
                "source_axis_name_raw": raw_axis,
                "source_axis_name_normalized": canonical,
                "target_axis_collision": len({x for x in target_labels if isinstance(x, str)}) > 1,
                "semantic_or_product_applicability_proof": False,
            },
        }
    return {**base, "axis_alias": {"source_families": aliases,
                                     "axis_mapping": mappings, "request_pairs": pairs}}


def evaluate_request(request, families, mode="typed"):
    if mode != "axis_alias":
        return Base.evaluate_request(request, families, mode=mode)
    pair_record = (families.get("axis_alias", {}).get("request_pairs", {}).get(request.get("request_id")))
    if not isinstance(pair_record, dict):
        return {"schema": Base.SCHEMA, "request_id": request.get("request_id"),
                "mode": "axis_alias", "state": "unknown",
                "reasons": ["axis_alias_family_binding_missing"], "family_state": "unknown",
                "family_mapping": [], "suggestions_affect_state": False}
    normalized, record = dict(request), pair_record
    canonical, _ = _axis(request.get("axis_name"))
    normalized["axis_name"] = canonical
    normalized["target_selected_axes"] = record["normalized_target_selected_axes"]
    result = Base.evaluate_request(normalized,
        {"request_pairs": {request["request_id"]: record["pair"]}}, mode="typed")
    candidates = dict(result.get("candidate_suggestions") or {})
    candidates.pop("axis_alias_candidates", None)
    return {**result, "mode": "axis_alias", "base_mode": "typed",
            "axis_name_raw": request.get("axis_name"), "axis_name_normalized": canonical,
            "target_selected_axes_raw": request.get("target_selected_axes"),
            "target_selected_axes_normalized": record["normalized_target_selected_axes"],
            "axis_alias_mapping": record["axis_mapping"],
            "candidate_suggestions": {**candidates,
                "normalized_axis_names": {"source": canonical,
                    "target": [axis.get("axis_name") for axis in record["normalized_target_selected_axes"]]},
                "axis_mapping_advisory_only": True},
            "suggestions_affect_state": False}


def _activate():
    R.M = SimpleNamespace(SCHEMA=Base.SCHEMA, build_families=build_families,
                          evaluate_request=evaluate_request)
    R.VARIANTS = VARIANTS
    R.CODE = tuple(n for n in R.CODE if n != "run_classical_axis_alias_v1.py") + (
        "run_classical_axis_alias_v1.py",)


def run(source: Path, output: Path):
    _activate()
    R.run(source, output)


def grade(source: Path, output: Path):
    _activate()
    R.grade(source, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "grade"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    globals()[args.action](args.source, args.output)
