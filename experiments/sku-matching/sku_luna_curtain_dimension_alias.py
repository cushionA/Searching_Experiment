"""Narrow curtain-specific alias for explicit AU width-by-length dimensions."""
from __future__ import annotations

import copy
import re

import sku_luna_dimension_guard as dimension_guard

NUMBER = r"\d+(?:\.\d+)?"
CURTAIN_WIDTH_BY_LENGTH = re.compile(
    rf"幅\s*{NUMBER}\s*[×xX*]\s*丈\s*{NUMBER}\s*(?:mm|cm|m)(?![a-z])"
)
AU_CONDITION_ID = re.compile(r"A(\d+)")


def _source_records(case):
    sources = case.get("sources", [])
    if isinstance(sources, dict):
        return [(str(sid), item) for sid, item in sources.items() if isinstance(item, dict)]
    if isinstance(sources, list):
        return [(str(item.get("source_id", "")), item) for item in sources if isinstance(item, dict)]
    return []


def _is_fixed_au_curtain(case, source_map):
    for sid, record in _source_records(case):
        mapped = source_map.get(sid, {}) if isinstance(source_map, dict) else {}
        if not isinstance(mapped, dict):
            continue
        kinds = {value for value in (record.get("kind"), mapped.get("kind")) if value is not None}
        scopes = {value for value in (record.get("scope"), mapped.get("scope")) if value is not None}
        if kinds != {"title"} or scopes != {"fixed_product"}:
            continue
        text = record.get("text", record.get("quote", ""))
        quote = mapped.get("quote", "")
        if "カーテン" in str(text) or "カーテン" in str(quote):
            return True
    return False


def _part_labeled(quote, axis):
    normalized_quote = dimension_guard.normalize(quote)
    normalized_axis = dimension_guard.normalize(axis)
    return any(part in normalized_quote or part in normalized_axis for part in dimension_guard.PARTS)


def _temporary_aliases(case, answer, source_map, row_conditions):
    """Copy the source map and normalize only cited current-row width×丈 quotes."""
    temporary = copy.deepcopy(source_map)
    audit = []
    if not _is_fixed_au_curtain(case, source_map):
        return temporary, audit
    checks = answer.get("checks", []) if isinstance(answer, dict) else []
    for check_index, check in enumerate(checks):
        if not isinstance(check, dict) or check.get("status") != "support":
            continue
        for sid in check.get("source_ids", []):
            match_id = AU_CONDITION_ID.fullmatch(str(sid))
            if not match_id:
                continue
            condition_index = int(match_id.group(1))
            if condition_index >= len(row_conditions) or sid not in temporary:
                continue
            condition = row_conditions[condition_index]
            source = temporary[sid]
            if not isinstance(source, dict) or not isinstance(source.get("quote"), str):
                continue
            quote = source["quote"]
            if _part_labeled(quote, condition.get("axis", "")):
                continue
            normalized = dimension_guard.normalize(quote)
            pair = CURTAIN_WIDTH_BY_LENGTH.search(normalized)
            if not pair:
                continue
            length_label = normalized.find("丈", pair.start(), pair.end())
            if length_label < 0:
                continue
            source["quote"] = normalized[:length_label] + "高さ" + normalized[length_label + 1:]
            audit.append({"condition_index": check_index, "source_id": sid,
                          "row_condition_index": condition_index,
                          "alias": "丈→高さ", "reason": "fixed_au_curtain_explicit_width_by_length"})
    return temporary, audit


def curtain_dimension_alias_audit(case, answer, source_map, row_conditions):
    """Describe eligible aliases without modifying any inputs."""
    _, audit = _temporary_aliases(case, answer, source_map, row_conditions)
    return audit


def guard_curtain_dimensions(case, targets, answer, source_map, row_conditions):
    """Apply the existing dimension guard with a local, curtain-only alias.

    Original case data, targets, answers, source quotes, and provenance remain
    untouched. Returned issues retain the existing dimension guard format.
    """
    temporary_map, _ = _temporary_aliases(case, answer, source_map, row_conditions)
    return dimension_guard.guard_dimensions(targets, answer, temporary_map, row_conditions)
