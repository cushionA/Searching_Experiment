"""Conservative local checks for cited physical dimensions; no product rules."""
from __future__ import annotations

import copy
import re
import unicodedata
from decimal import Decimal, InvalidOperation

SCALE = {"mm": Decimal("0.1"), "cm": Decimal(1), "m": Decimal(100)}
LABELS = {"横幅": "width", "幅": "width", "横": "width", "奥行き": "depth", "奥行": "depth",
          "高さ": "height", "縦幅": "length", "長さ": "length", "縦": "vertical",
          "厚み": "thickness", "厚さ": "thickness", "直径": "diameter"}
NUMBER = r"\d+(?:\.\d+)?"
LABEL = "|".join(sorted(LABELS, key=len, reverse=True))
MEASURE = re.compile(rf"({LABEL})\s*[:=：]?\s*({NUMBER})\s*(mm|cm|m)?")
TUPLE = re.compile(rf"({NUMBER}(?:\s*(?:[xX×*]|\s)\s*(?:(?:{LABEL})\s*)?{NUMBER})+)\s*(mm|cm|m)")
PARTS = ("座面", "収納", "折りたたみ", "折り畳み", "クッション", "バスケット", "梱包")


def normalize(text):
    return unicodedata.normalize("NFKC", str(text)).replace("㎝", "cm").replace("㎜", "mm")


def axis_kind(axis):
    text = normalize(axis)
    for label in sorted(LABELS, key=len, reverse=True):
        if label in text:
            return LABELS[label]
    return None


def target_value(target):
    try:
        value = normalize(target["value"]).strip()
        if not re.fullmatch(NUMBER, value):
            return None
        return Decimal(value) * SCALE[target["unit"]]
    except (KeyError, InvalidOperation, TypeError):
        return None


def observations(quote, axis=None):
    text = normalize(quote).split("※", 1)[0]
    units = set(re.findall(r"mm|cm|(?<![a-z])m(?![a-z])", text))
    fallback = next(iter(units)) if len(units) == 1 else None
    labeled = {}
    for label, value, unit in MEASURE.findall(text):
        unit = unit or fallback
        if unit:
            labeled.setdefault(LABELS[label], []).append(Decimal(value) * SCALE[unit])
    if axis is not None and axis_kind(axis) and re.fullmatch(rf"\s*({NUMBER})\s*(mm|cm|m)\s*", text):
        value, unit = re.fullmatch(rf"\s*({NUMBER})\s*(mm|cm|m)\s*", text).groups()
        labeled.setdefault(axis_kind(axis), []).append(Decimal(value) * SCALE[unit])
    tuples = []
    for sequence, unit in TUPLE.findall(text):
        tuples.append(sorted(Decimal(value) * SCALE[unit] for value in re.findall(NUMBER, sequence)))
    return labeled, tuples


def guard_dimensions(targets, answer, source_map, row_conditions):
    """Downgrade unsupported dimension assertions without rewriting raw answers.

An explicit axis mismatch takes precedence over an unlabeled tuple's multiset.
This is a measurement guard, not a guarantee of overall product equivalence.
"""
    guarded = copy.deepcopy(answer)
    issues = []
    all_values = [target_value(t) for t in targets]
    complete_values = sorted(all_values) if all(v is not None for v in all_values) else None
    for i, (target, check) in enumerate(zip(targets, guarded["checks"], strict=True)):
        if check["status"] != "support":
            continue
        expected, kind = target_value(target), axis_kind(target["axis"])
        proven, contradictions = False, []
        for sid in check["source_ids"]:
            quote = source_map[sid]["quote"]
            target_part = next((part for part in PARTS if part in normalize(target["axis"])), None)
            source_parts = [part for part in PARTS if part in normalize(quote)]
            if source_parts and target_part not in source_parts:
                continue
            cited_axis = row_conditions[int(sid[1:])]["axis"] if sid.startswith("A") else None
            labeled, tuples = observations(quote, cited_axis)
            direct = labeled.get(kind, [])
            # A declared diameter supplies both horizontal extents mathematically.
            if not direct and kind in {"width", "depth"}:
                direct = labeled.get("diameter", [])
            if expected is not None and direct:
                if all(v == expected for v in direct):
                    proven = True
                else:
                    contradictions.append(sid)
            elif complete_values is not None and complete_values in tuples:
                proven = True
        if contradictions:
            check.update(status="contradiction", source_ids=list(dict.fromkeys(contradictions)))
            issues.append({"condition_index": i, "error": "explicit_dimension_axis_mismatch", "source_ids": check["source_ids"]})
        elif not proven:
            check.update(status="unknown", source_ids=[])
            issues.append({"condition_index": i, "error": "dimension_not_proven_by_cited_quote"})
    return guarded, issues
