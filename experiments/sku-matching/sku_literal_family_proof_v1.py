"""Fail-closed source proof for literal identity/conflict on complete SKU families.

This is a separate evidence type. It does not call or modify the classifier or
the existing SKU gate. Only exact raw strings and complete ordered families
are eligible; every source value and axis label is re-derived from raw captures.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "claude_v9_gate"))
import sku_gate_sources as sources  # noqa: E402

PROOF_VERSION = "literal_complete_ordered_family_v1"


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def _verified_span(store, span: Any, expected: str, label: str) -> None:
    _need(isinstance(span, dict), f"{label}: source span missing")
    _need(span.get("quote") == expected, f"{label}: quoted text differs")
    _need(type(span.get("start")) is int and type(span.get("end")) is int,
          f"{label}: malformed offsets")
    normalized = dict(span)
    if isinstance(normalized.get("raw_file"), str):
        normalized["raw_file"] = _raw_rel(normalized["raw_file"])
    _need(sources.verify_span(store, normalized), f"{label}: raw source span failed verification")


def _raw_rel(value: str) -> str:
    return value.replace("\\", "/")


def _normalized_span(span: Any) -> Any:
    if not isinstance(span, dict):
        return span
    out = dict(span)
    if isinstance(out.get("raw_file"), str):
        out["raw_file"] = _raw_rel(out["raw_file"])
    return out


def _one(items: list, predicate, label: str):
    matches = [item for item in items if predicate(item)]
    _need(len(matches) == 1, f"{label}: expected exactly one match, got {len(matches)}")
    return matches[0]


def _rakuten_family(store, selected: dict, axis: dict) -> tuple[list[str], list[dict]]:
    raw_file, digest = _raw_rel(selected.get("raw_file", "")), selected.get("sha256")
    _need(isinstance(raw_file, str) and isinstance(digest, str), "Rakuten selected raw reference missing")
    store.raw(raw_file, digest)
    axes = selected.get("axes")
    _need(isinstance(axes, list), "Rakuten selected axes missing")
    label = axis.get("axis_label")
    _need(isinstance(label, str) and label, "Rakuten exact axis label missing")
    _one(axes, lambda a: a.get("axis_label") == label, "unique Rakuten selected axis name")
    _verified_span(store, axis.get("axis_label_span"), label, "Rakuten axis label")
    selected_value = axis.get("value")
    _need(isinstance(selected_value, str) and selected_value, "Rakuten selected value missing")
    _verified_span(store, axis.get("value_span"), selected_value, "Rakuten selected value")
    encoding = (axis.get("value_span", {}).get("locator") or {}).get("encoding") or "EUC-JP"
    selectors = sources.rakuten_variant_selectors(store, raw_file, encoding)
    selector = _one(selectors, lambda x: x.get("label") == label, "unique raw Rakuten selector")
    _verified_span(store, selector.get("label_span"), label, "raw Rakuten selector label")
    family = selector.get("values")
    _need(isinstance(family, list) and family and all(isinstance(v, str) and v for v in family),
          "raw Rakuten option family missing or malformed")
    _need(len(family) == len(set(family)), "duplicate raw Rakuten option values")
    _need(axis.get("family_values") == family,
          "complete raw Rakuten family differs from frozen selected-axis context")
    spans = []
    selector_index = selectors.index(selector)
    for value in family:
        span = sources.rakuten_selector_value_span(store, raw_file, encoding, selector_index,
                                                    selector["key"], value)
        _verified_span(store, span, value, "raw Rakuten family value")
        spans.append(span)
    _need(selected_value in family and family.count(selected_value) == 1,
          "Rakuten selected value is not uniquely in its complete family")
    return family, spans, selector.get("label_span"), axis.get("value_span")


def _au_family(store, product: dict, axis_name: str, selected_value: str,
               selected_span: dict) -> tuple[list[str], list[dict]]:
    au = product.get("au_product") or {}
    raw_file, digest = _raw_rel(au.get("raw_file", "")), au.get("sha256")
    _need(isinstance(raw_file, str) and isinstance(digest, str), "AU raw product reference missing")
    store.raw(raw_file, digest)
    sku = store.json(raw_file).get("itemInfo", {}).get("skuInfo") or {}
    names = sku.get("optionName") or {}
    matches = [(kind, kind + "Names") for kind, name in names.items() if name == axis_name]
    _need(len(matches) == 1, "AU raw axis name is missing or ambiguous")
    kind, values_key = matches[0]
    label_span = sources.json_leaf_span(store, raw_file,
                                        f"$.itemInfo.skuInfo.optionName.{kind}", axis_name)
    _verified_span(store, label_span, axis_name, "AU raw axis label")
    family = sku.get(values_key)
    _need(isinstance(family, list) and family and all(isinstance(v, str) and v for v in family),
          "AU raw option family missing or malformed")
    _need(len(family) == len(set(family)), "duplicate AU raw option values")
    spans = []
    for index, value in enumerate(family):
        span = sources.json_leaf_span(store, raw_file,
                                      f"$.itemInfo.skuInfo.{values_key}[{index}]", value)
        _verified_span(store, span, value, "AU raw family value")
        spans.append(span)
    _verified_span(store, selected_span, selected_value, "AU selected value")
    _need(selected_value in family and family.count(selected_value) == 1,
          "AU selected value is not uniquely in its complete family")
    return family, spans, label_span, selected_span


def prove_task_pair(forward_task: dict, reverse_task: dict, source_case: dict,
                    source_product: dict, store, source_pins: dict | None = None) -> dict:
    """Prove exact selected-axis support/conflict for one original task pair.

    ``forward_task`` is Rakuten->AU; ``reverse_task`` is AU->Rakuten. The pair
    must bind the same case and AU row. Missing/ambiguous/malformed evidence is
    returned as unknown, never repaired by normalization or inferred semantics.
    """
    base = {"proof_version": PROOF_VERSION,
            "forward_task_id": forward_task.get("task_id"),
            "reverse_task_id": reverse_task.get("task_id"),
            "case_id": forward_task.get("case_id"),
            "au_row_key": forward_task.get("au_row_key"),
            "status": "unknown"}
    try:
        _need(forward_task.get("direction") == "rakuten_to_au", "forward task direction mismatch")
        _need(reverse_task.get("direction") == "au_to_rakuten", "reverse task direction mismatch")
        for key in ("case_id", "dossier_id", "au_product_id", "au_row_key", "source_sku_key"):
            _need(forward_task.get(key) == reverse_task.get(key), f"task pair {key} binding differs")
        f_prov, r_prov = forward_task.get("provenance") or {}, reverse_task.get("provenance") or {}
        if source_pins is not None:
            for key in ("case_source_sha256", "product_source_sha256", "source_version", "row_array_source", "row_key"):
                _need(f_prov.get(key) == r_prov.get(key), f"task pair provenance {key} differs")
            version = f_prov.get("source_version")
            _need(version in source_pins, "task source version is not pinned")
            _need(f_prov.get("case_source_sha256") == source_pins[version].get("cases_sha256"),
                  "task case-source SHA differs from frozen source manifest")
            _need(f_prov.get("product_source_sha256") == source_pins[version].get("products_sha256"),
                  "task product-source SHA differs from frozen source manifest")
        case_record = source_case.get("record") or source_case
        product = source_product.get("record") or source_product
        _need(case_record.get("case_id") == forward_task.get("case_id"), "case/source binding differs")
        _need(case_record.get("dossier_id") == forward_task.get("dossier_id") == product.get("dossier_id"),
              "dossier binding differs")
        _need(str(case_record.get("au_product_id")) == str(forward_task.get("au_product_id")) ==
              str((product.get("au_product") or {}).get("product_id")), "AU product binding differs")
        _need((selected_sku_key := ((case_record.get("rakuten_selected") or {}).get("source_sku_key"))) ==
              forward_task.get("source_sku_key"), "Rakuten selected SKU binding differs")
        selected = case_record.get("rakuten_selected") or {}
        rak_axes = selected.get("axes") or []
        forward_ref = forward_task.get("raw_condition_ref") or {}
        reverse_ref = reverse_task.get("raw_condition_ref") or {}
        axis_name = forward_ref.get("axis_name")
        _need(isinstance(axis_name, str) and axis_name, "forward exact axis name missing")
        _need(reverse_ref.get("axis_name") == axis_name, "opposite selected context axis name differs")
        r_axis = _one(rak_axes, lambda a: a.get("axis_label") == axis_name,
                      "unique selected Rakuten axis")
        _need(r_axis.get("value") == forward_ref.get("value"),
              "forward selected value does not mirror frozen Rakuten context")
        _verified_span(store, forward_ref.get("value_span"), r_axis["value"],
                       "forward task selected Rakuten value")
        r_family, r_family_spans, r_axis_span, r_selected_span = _rakuten_family(store, selected, r_axis)
        a_value = reverse_ref.get("value")
        _need(isinstance(a_value, str) and a_value, "reverse exact selected value missing")
        a_span = reverse_ref.get("value_span")
        au_rows = product.get("au_rows")
        _need(isinstance(au_rows, list), "frozen AU row array missing")
        au_row = _one(au_rows, lambda row: row.get("row_key") == forward_task.get("au_row_key"),
                      "exact frozen AU row")
        row_axis = _one(au_row.get("axes") or [],
                        lambda a: a.get("axis_name") == axis_name,
                        "unique axis in frozen AU row")
        _need(row_axis.get("value") == a_value, "reverse task selected value differs from frozen AU row")
        _verified_span(store, row_axis.get("axis_name_span"), axis_name, "frozen AU row axis name")
        _verified_span(store, row_axis.get("value_span"), a_value, "frozen AU row selected value")
        _need(_normalized_span(a_span) == _normalized_span(row_axis.get("value_span")),
              "reverse task value span differs from frozen AU row span")
        a_family, a_family_spans, a_axis_span, a_selected_span = _au_family(store, product, axis_name, a_value, a_span)
        _need(r_family == a_family, "complete ordered raw option families differ")
        base.update({"axis_name": axis_name, "rakuten_selected_value": r_axis["value"],
                     "au_selected_value": a_value, "ordered_family": r_family,
            "rakuten_raw_file": _raw_rel(selected["raw_file"]),
                     "au_raw_file": _raw_rel(product["au_product"]["raw_file"]),
                     "raw_span_receipts": {"rakuten_axis_name": _normalized_span(r_axis_span),
                                            "rakuten_selected_value": _normalized_span(r_selected_span),
                                            "rakuten_ordered_family": [_normalized_span(x) for x in r_family_spans],
                                            "au_axis_name": _normalized_span(a_axis_span),
                                            "au_selected_value": _normalized_span(a_selected_span),
                                            "au_ordered_family": [_normalized_span(x) for x in a_family_spans]},
                     "status": "support" if r_axis["value"] == a_value else "conflict",
                     "reason": "exact_literal_value_equal" if r_axis["value"] == a_value else
                               "distinct_exact_values_in_identical_complete_ordered_family"})
    except (KeyError, TypeError, ValueError, OSError, UnicodeDecodeError) as exc:
        base["reason"] = str(exc)
    return base


def build_proofs(tasks: list[dict], source_cases: list[dict], source_products: list[dict],
                 store, source_pins: dict | None = None) -> list[dict]:
    """Build proofs only for uniquely paired original forward/reverse tasks."""
    cases = {(item.get("record") or item).get("case_id"): item for item in source_cases}
    products = {(item.get("record") or item).get("dossier_id"): item for item in source_products}
    grouped: dict[tuple, dict[str, list[dict]]] = {}
    for task in tasks:
        key = (task.get("case_id"), task.get("au_row_key"))
        direction = task.get("direction")
        if direction in {"rakuten_to_au", "au_to_rakuten"}:
            grouped.setdefault(key, {}).setdefault(direction, []).append(task)
    proofs = []
    for (case_id, _row_key), directions in sorted(grouped.items()):
        forwards, reverses = directions.get("rakuten_to_au", []), directions.get("au_to_rakuten", [])
        for forward in forwards:
            for reverse in reverses:
                f_name = (forward.get("raw_condition_ref") or {}).get("axis_name")
                r_name = (reverse.get("raw_condition_ref") or {}).get("axis_name")
                if f_name != r_name:
                    continue
                proofs.append(prove_task_pair(forward, reverse, cases.get(case_id) or {},
                                              products.get(forward.get("dossier_id")) or {}, store, source_pins))
    return proofs
