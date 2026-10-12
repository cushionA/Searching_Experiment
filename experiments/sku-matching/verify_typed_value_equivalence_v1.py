"""Fail-closed verifier for typed SKU value equivalence sidecars.

Only ``own_axis_name_affix`` is eligible. The proof is recomputed from frozen
raw option families; sidecar verdicts and normalization summaries are hints.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "claude_v9_gate"))
import sku_gate_sources as sources  # noqa: E402


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _span(store, span: Any, expected: str, label: str) -> None:
    _need(isinstance(span, dict), f"{label}: span missing")
    _need(span.get("quote") == expected, f"{label}: quote differs")
    _need(type(span.get("start")) is int and type(span.get("end")) is int,
          f"{label}: offsets malformed")
    _need(span["end"] - span["start"] == len(expected), f"{label}: span length differs")
    _need(sources.verify_span(store, span), f"{label}: raw source span failed verification")


def _exact_span(store, supplied: Any, fresh: dict, label: str) -> None:
    _span(store, supplied, fresh["quote"], label)
    for key in ("raw_file", "sha256", "locator", "start", "end", "quote"):
        left, right = supplied.get(key), fresh.get(key)
        if key == "raw_file" and isinstance(left, str) and isinstance(right, str):
            left, right = left.replace("\\", "/"), right.replace("\\", "/")
        _need(left == right, f"{label}: span does not match the frozen source locator")


def _verify_embedded_spans(store, value: Any, label: str = "sidecar") -> None:
    span_keys = {"raw_file", "sha256", "locator", "start", "end", "quote"}
    if isinstance(value, dict):
        if span_keys.intersection(value):
            _need(span_keys.issubset(value), f"{label}: partial source span")
            _span(store, value, value["quote"], label)
        for key, child in value.items():
            _verify_embedded_spans(store, child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _verify_embedded_spans(store, child, f"{label}[{index}]")


def _one(items: list, predicate, label: str):
    found = [item for item in items if predicate(item)]
    _need(len(found) == 1, f"{label}: expected exactly one match, got {len(found)}")
    return found[0]


def _fresh_au_axis(store, source_product: dict, axis_name: str):
    product = source_product.get("au_product") or {}
    raw_file, digest = product.get("raw_file"), product.get("sha256")
    _need(isinstance(raw_file, str) and isinstance(digest, str), "AU raw source reference missing")
    store.raw(raw_file, digest)
    obj = store.json(raw_file)
    sku = obj["itemInfo"]["skuInfo"]
    option_name = sku.get("optionName") or {}
    locations = [(kind, kind + "Names") for kind, name in option_name.items() if name == axis_name]
    _need(len(locations) == 1, "AU axis name is missing or ambiguous in raw SKU axes")
    kind, values_key = locations[0]
    values = sku.get(values_key)
    _need(isinstance(values, list) and values and all(isinstance(v, str) and v for v in values),
          "AU raw option family is missing or malformed")
    spans = [sources.json_leaf_span(store, raw_file, f"$.itemInfo.skuInfo.{values_key}[{i}]")
             for i in range(len(values))]
    axis_span = sources.json_leaf_span(store, raw_file, f"$.itemInfo.skuInfo.optionName.{kind}")
    _span(store, axis_span, axis_name, "AU axis name")
    for i, (value, span) in enumerate(zip(values, spans)):
        _span(store, span, value, f"AU raw option {i}")
    return kind, values, spans, axis_span, sku


def _exact_family(values: Any, expected: list[str], label: str) -> None:
    _need(isinstance(values, list) and values == expected, f"{label}: full family differs from frozen raw options")
    _need(bool(values) and all(isinstance(v, str) and v for v in values), f"{label}: empty family/value")
    _need(len(values) == len(set(values)), f"{label}: duplicate family values")


def _affix_proof(r_value: str, a_value: str, axis: str,
                 r_family: list[str], a_family: list[str]) -> tuple[str, str, list[str]]:
    _need(axis != "", "axis name is empty")
    matches = []
    if a_value == r_value + axis:
        matches.append(("au", "suffix"))
    if a_value == axis + r_value:
        matches.append(("au", "prefix"))
    if r_value == a_value + axis:
        matches.append(("rakuten", "suffix"))
    if r_value == axis + a_value:
        matches.append(("rakuten", "prefix"))
    _need(len(matches) == 1, "selected raw values are not one exact axis-name affix apart")
    side, position = matches[0]
    own, other = (a_family, r_family) if side == "au" else (r_family, a_family)

    def carries(value: str) -> bool:
        return value.endswith(axis) if position == "suffix" else value.startswith(axis)

    _need(all(carries(value) for value in own), "axis name is not affixed consistently to every option on one side")
    _need(not any(carries(value) for value in other), "other-side option family also carries the axis affix")
    stripped = [value[:-len(axis)] if position == "suffix" else value[len(axis):] for value in own]
    _need(all(stripped), "axis removal makes an empty option")
    _need(len(stripped) == len(set(stripped)), "axis removal merges same-side options")
    _need(len(r_family) == len(set(r_family)) and len(a_family) == len(set(a_family)),
          "duplicate raw family options")
    _need(sorted(stripped) == sorted(other), "complete option families do not form a bijection")
    _need(stripped.count(r_value if side == "au" else a_value) == 1,
          "selected option is not unique after axis removal")
    counterpart = r_value if side == "au" else a_value
    target = counterpart
    _need(sum(1 for value in other if value == counterpart) == 1,
          "selected counterpart is not unique in the other family")
    own_selected = [value for value in own if carries(value) and
                    (value[:-len(axis)] if position == "suffix" else value[len(axis):]) == target]
    _need(len(own_selected) == 1, "selected value is not unique in its own family")
    return side, position, stripped


def _validate_sidecar(sidecar: dict, task: dict, request: dict, source_case: dict,
                      source_product: dict, store) -> dict:
    """Internal verifier; public wrapper turns malformed shapes into ValueError."""
    _need(isinstance(sidecar, dict), "sidecar must be an object")
    _verify_embedded_spans(store, sidecar)
    for key in ("case_id", "dossier_id", "au_row_key", "axis_key"):
        _need(sidecar.get(key) == task.get(key), f"sidecar {key} is not bound to task")
    _need(task.get("case_id") == source_case.get("case_id"), "task/source case ID differs")
    _need(task.get("dossier_id") == source_case.get("dossier_id") == source_product.get("dossier_id"),
          "task/case/product dossier differs")
    _need(task.get("au_product_id") == source_case.get("au_product_id") ==
          str((source_product.get("au_product") or {}).get("product_id")), "AU product binding differs")
    _need(task.get("request_id") == request.get("request_id"), "task/request ID differs")
    _need(task.get("direction") in {"rakuten_to_au", "au_to_rakuten"} and
          request.get("direction") == task.get("direction"), "unsupported or mismatched direction")
    _need(sidecar.get("verdict") == "proven_equivalent" and
          sidecar.get("proof_type") == "own_axis_name_affix", "sidecar is not the supported proof type")
    _need(sidecar.get("residual_unconfirmed") is False, "residual must be absent for automatic proof")

    align = sidecar.get("align_v7") or {}
    _need(align.get("status") == "model_candidate", "sidecar does not describe a model_candidate")
    for residual in (align.get("residual"), sidecar.get("preserved", {}).get("residual")):
        _need(residual in (None, []), "residual evidence is present")

    selected = source_case.get("rakuten_selected") or {}
    axes = selected.get("axes")
    _need(isinstance(axes, list), "frozen selected Rakuten axes missing")
    r_axis = _one(axes, lambda a: a.get("axis_key") == task.get("axis_key"), "selected Rakuten axis")
    axis_index = r_axis.get("axis_index")
    _need(type(axis_index) is int and axis_index >= 0, "Rakuten axis index missing")
    r_label, r_value = r_axis.get("axis_label"), r_axis.get("value")
    r_family_declared = r_axis.get("family_values")
    _need(isinstance(r_label, str) and r_label and isinstance(r_value, str) and r_value,
          "frozen Rakuten selected axis/value malformed")
    r_raw, r_sha = selected.get("raw_file"), selected.get("sha256")
    _need(isinstance(r_raw, str) and isinstance(r_sha, str), "frozen Rakuten raw reference missing")
    store.raw(r_raw, r_sha)
    encoding = r_axis.get("value_span", {}).get("locator", {}).get("encoding") or "EUC-JP"
    raw_selectors = sources.rakuten_variant_selectors(store, r_raw, encoding)
    selector = _one(raw_selectors, lambda x: x.get("key") == r_axis.get("axis_key"), "raw Rakuten selector")
    _need(selector.get("label") == r_label, "raw Rakuten axis label differs")
    r_family = selector.get("values")
    _exact_family(r_family, r_family_declared, "Rakuten family")
    _span(store, r_axis.get("axis_label_span"), r_label, "frozen Rakuten axis label")
    _span(store, r_axis.get("value_span"), r_value, "frozen Rakuten selected value")
    _exact_span(store, r_axis.get("axis_label_span"), selector.get("label_span"), "frozen Rakuten axis label locator")
    selected_variant_id = selected.get("variant_id")
    _need(isinstance(selected_variant_id, (str, int)), "selected Rakuten variant ID missing")
    _need(task.get("source_sku_key") == selected.get("source_sku_key"), "task selected Rakuten SKU key differs")
    selected_values = sources.rakuten_selected_values(store, r_raw, encoding, str(selected_variant_id))
    _need(axis_index < len(selected_values), "selected variant has fewer values than selected axes")
    _need(selected_values[axis_index].get("quote") == r_value, "selected SKU selector value differs from frozen axis")
    _span(store, selected_values[axis_index], r_value, "raw selected Rakuten SKU option")
    _exact_span(store, r_axis.get("value_span"), selected_values[axis_index], "frozen Rakuten selected value locator")

    producer = task.get("producer_condition") or {}
    _need(producer.get("status") == "model_candidate" and producer.get("kind") == "rakuten" and
          producer.get("axis_key") == task.get("axis_key") and producer.get("axis_label") == r_label and
          producer.get("value") == r_value and producer.get("au_axis") == request.get("axis_name"),
          "task producer condition differs from frozen selected Rakuten axis")
    _need(producer.get("residual") in (None, []), "task producer condition contains residual")

    # Source product is an independent frozen projection and must agree with the raw selector.
    rp = source_product.get("rakuten_product") or {}
    _need(rp.get("raw_file", "").replace("\\", "/") == r_raw.replace("\\", "/") and
          rp.get("sha256") == r_sha, "source product Rakuten source differs")
    product_families = rp.get("selector_families")
    _need(isinstance(product_families, list), "source product selector families missing")
    pf = _one(product_families, lambda x: x.get("key") == r_axis.get("axis_key"), "product Rakuten family")
    _need(pf.get("values") == r_family and pf.get("label") == r_label,
          "source product Rakuten family differs from captured page")

    au_axis = request.get("axis_name")
    _need(isinstance(au_axis, str) and au_axis, "request AU axis name missing")
    _need(r_label == au_axis, "raw Rakuten and AU axis names differ")
    au_kind, au_values, au_spans, au_axis_span, au_sku = _fresh_au_axis(store, source_product, au_axis)
    _need(au_values == task.get("source_handoff", {}).get("au_axis_options", {}).get(au_axis),
          "task AU family differs from raw AU options")
    _need(au_values == sidecar.get("au", {}).get("all_options", [o.get("value") for o in sidecar.get("au", {}).get("options", [])]),
          "sidecar AU full family differs from raw AU options")

    au_rows = source_product.get("au_rows")
    _need(isinstance(au_rows, list), "frozen AU rows missing")
    product_family, product_option_spans = [], []
    for source_row in au_rows:
        for source_axis in source_row.get("axes", []):
            if source_axis.get("axis_name") == au_axis:
                value = source_axis.get("value")
                _need(isinstance(value, str) and value, "frozen AU row contains empty option")
                _span(store, source_axis.get("axis_name_span"), au_axis, "frozen AU row axis span")
                _span(store, source_axis.get("value_span"), value, "frozen AU row value span")
                if value not in product_family:
                    product_family.append(value)
                    product_option_spans.append(source_axis["value_span"])
    _need(product_family == au_values, "frozen AU row family is incomplete or differs from raw SKU options")
    matches = [row for row in au_rows if row.get("row_key") == task.get("au_row_key")]
    _need(len(matches) == 1, "AU row key missing or duplicated")
    au_row = matches[0]
    row_axes = au_row.get("axes") or []
    row_axis = _one(row_axes, lambda a: a.get("axis_name") == au_axis, "selected AU row axis")
    au_value = row_axis.get("value")
    _need(isinstance(au_value, str) and au_value and au_value in au_values,
          "selected AU value is not an option in raw family")
    _need(producer.get("au_value") == au_value, "task producer condition AU value differs from raw selected row")
    _need(producer.get("status") == align.get("status") and producer.get("core") == align.get("core") and
          producer.get("residual") == align.get("residual"),
          "task producer condition differs from sidecar alignment binding")
    _need(producer.get("residual") in (None, []) and align.get("residual") in (None, []),
          "task or sidecar alignment contains residual")
    _span(store, row_axis.get("axis_name_span"), au_axis, "frozen AU axis span")
    _span(store, row_axis.get("value_span"), au_value, "frozen AU selected span")
    _need(au_row.get("sku_id") == au_sku.get("skuId"), "AU row SKU ID differs from raw JSON")
    row_index, column_index = au_row.get("row_index"), au_row.get("column_index")
    _need(type(row_index) is int and type(column_index) is int and row_index >= 0 and column_index >= 0,
          "AU row/column index malformed or negative")
    axis_names = (source_product.get("au_product", {}).get("axis_names") or {})
    _need(axis_names == au_sku.get("optionName"), "frozen AU axis names differ from raw JSON")
    raw_row_values, raw_column_values = au_sku.get("rowNames") or [], au_sku.get("columnNames") or []
    _need(row_index < len(raw_row_values) and column_index < len(raw_column_values), "AU row index out of range")
    _need(len(au_values) == len(set(au_values)), "duplicate raw AU option values")
    au_option_index = au_values.index(au_value)
    axis_coordinate = row_index if au_kind == "row" else column_index
    _need(au_option_index == axis_coordinate,
          "selected AU row coordinate differs from raw option value")
    expected_row_key = f"au:{task['au_product_id']}:{au_sku['skuId']}:{row_index}:{column_index}"
    _need(au_row.get("row_key") == expected_row_key, "AU row key does not bind to raw SKU matrix coordinates")
    expected_row_axes = []
    for kind, index, values in (("row", row_index, raw_row_values), ("column", column_index, raw_column_values)):
        name = axis_names.get(kind)
        if name and index < len(values) and values[index]:
            expected_row_axes.append((name, values[index]))
    _need([(x.get("axis_name"), x.get("value")) for x in row_axes] == expected_row_axes,
          "frozen AU selected row axes differ from raw matrix coordinates")
    selected_row_axis = _one(row_axes, lambda x: x.get("axis_name") == au_axis, "selected AU row axis")
    _exact_span(store, selected_row_axis.get("axis_name_span"), au_axis_span, "frozen AU axis locator")
    _exact_span(store, selected_row_axis.get("value_span"), au_spans[au_option_index], "frozen AU row value locator")
    _need(task.get("source_handoff", {}).get("au_row_key") == task.get("au_row_key"), "task handoff AU row differs")
    handoff = task.get("source_handoff") or {}
    _need(handoff.get("case_id") == task.get("case_id") and handoff.get("dossier_id") == task.get("dossier_id"),
          "task handoff case/dossier differs")
    handoff_rakuten_axes = handoff.get("rakuten_axes") or {}
    handoff_rakuten = handoff_rakuten_axes.get(task.get("axis_key"))
    _need(isinstance(handoff_rakuten, dict) and handoff_rakuten.get("axis_label") == r_label and
          handoff_rakuten.get("value") == r_value and handoff_rakuten.get("family_values") == r_family,
          "task handoff Rakuten selected axis/family differs")
    handoff_conditions = handoff.get("conditions")
    _need(isinstance(handoff_conditions, list), "task handoff conditions missing")
    handoff_condition = _one(handoff_conditions,
        lambda c: c.get("kind") == "rakuten" and c.get("axis_key") == task.get("axis_key"),
        "task handoff matching condition")
    _need(handoff_condition == producer, "task handoff condition differs from producer condition")
    handoff_axes = task["source_handoff"].get("au_row_axes")
    _need(isinstance(handoff_axes, list), "task handoff AU selected axes missing")
    handoff_axis = _one(handoff_axes, lambda x: x.get("axis_name") == au_axis and x.get("value") == au_value,
                        "task handoff AU selected axis")
    _exact_span(store, handoff_axis.get("value_span"), au_spans[au_option_index], "task handoff AU selected value span")

    # Directional request binding: source selected value and target selected axes must agree.
    target_axes = request.get("target_selected_axes")
    _need(isinstance(target_axes, list), "request target selected axes missing")
    if task["direction"] == "rakuten_to_au":
        raw_condition = task.get("raw_condition") or {}
        _need(raw_condition.get("axis_key") == task.get("axis_key") and raw_condition.get("axis_label") == r_label and
              raw_condition.get("value") == r_value and raw_condition.get("family_values") == r_family,
              "task raw Rakuten condition differs from captured axis")
        _exact_span(store, raw_condition.get("axis_label_span"), selector.get("label_span"), "task Rakuten axis label span")
        _exact_span(store, raw_condition.get("value_span"), selected_values[axis_index], "task Rakuten selected span")
        _need(request.get("selected_value") == r_value, "request source value differs from Rakuten selection")
        _need(request.get("option_values") == r_family, "request source family differs from raw Rakuten options")
        expected_targets = [{"axis_name": x["axis_name"], "value": x["value"]} for x in row_axes]
        _need(target_axes == expected_targets, "request target AU axes are incomplete or differ from selected row")
        _need(task.get("option_values") == r_family, "task source option family differs")
    else:
        raw_condition = task.get("raw_condition") or {}
        _need(raw_condition.get("axis_name") == au_axis and raw_condition.get("value") == au_value,
              "task raw AU condition differs from selected source row")
        _exact_span(store, raw_condition.get("axis_name_span"), au_axis_span, "task AU axis name span")
        _exact_span(store, raw_condition.get("value_span"), au_spans[au_option_index], "task AU selected span")
        _need(request.get("selected_value") == au_value, "request source value differs from AU selection")
        _need(request.get("option_values") == au_values, "request source family differs from raw AU options")
        expected_targets = [{"axis_name": x["axis_label"], "value": x["value"]}
                            for x in selected.get("axes", [])]
        _need(target_axes == expected_targets, "request target Rakuten axes are incomplete or differ from selection")
        _need(task.get("option_values") == au_values, "task source option family differs")
    _need(request.get("selected_value_is_binding_only") is True, "request selected value is not marked binding-only")

    # Check every provenance value supplied by the sidecar; generate Rakuten option spans afresh.
    rside, aside = sidecar.get("rakuten") or {}, sidecar.get("au") or {}
    _need(rside.get("axis_label") == r_label and rside.get("value") == r_value and
          rside.get("family_values") == r_family, "sidecar Rakuten values differ from frozen input")
    _exact_span(store, rside.get("axis_label_span"), r_axis["axis_label_span"], "sidecar Rakuten axis span")
    _exact_span(store, rside.get("value_span"), selected_values[axis_index], "sidecar Rakuten selected span")
    side_au_name = aside.get("axis_name")
    _need(side_au_name == au_axis and aside.get("value") == au_value,
          "sidecar AU selection differs from frozen input")
    _exact_span(store, aside.get("axis_name_span"), au_axis_span, "sidecar AU axis span")
    # AU selected span is the raw matrix axis value span; require matching exact leaf locator.
    selected_au_index = au_option_index
    _exact_span(store, aside.get("value_span"), au_spans[selected_au_index], "sidecar AU selected span")
    supplied_options = aside.get("options")
    _need(isinstance(supplied_options, list) and [x.get("value") for x in supplied_options] == au_values,
          "sidecar AU options are incomplete or reordered")
    for i, option in enumerate(supplied_options):
        _exact_span(store, option.get("value_span"), au_spans[i], f"sidecar AU option span {i}")
    r_family_spans = []
    for value in r_family:
        fresh = sources.rakuten_selector_value_span(store, r_raw, encoding, axis_index,
                                                     r_axis["axis_key"], value)
        _need(fresh is not None, f"Rakuten family option lacks raw span: {value}")
        _span(store, fresh, value, f"Rakuten family option {value}")
        r_family_spans.append(fresh)

    # Sidecar must describe this exact raw proof, with no hidden NFKC/core shortcut.
    _need(align.get("core") == {"rakuten": [r_value], "au": [au_value], "identical": False},
          "align core is not the one-element raw whole-value pair")
    expected_normalization = [
        {"step": "nfkc_remove_whitespace", "rakuten": r_value, "au": au_value},
        {"step": "set_aside_bracketed_and_slash_parts", "rakuten_core": r_value,
         "au_core": au_value, "residual_kept": []},
    ]
    _need(sidecar.get("normalization_before_types") == expected_normalization,
          "normalization/core metadata differs from literal raw pair or implies a transformation")
    _need(sidecar.get("full_option_list_bijection") is True or
          aside.get("full_option_list_bijection") is True or
          (sidecar.get("attempts") and any(a.get("type") == "own_axis_name_affix" and
                                           a.get("full_option_list_bijection") is True for a in sidecar["attempts"])),
          "sidecar does not claim complete option-family bijection")
    attempts = sidecar.get("attempts")
    _need(isinstance(attempts, list), "sidecar proof attempts missing")
    attempt = _one(attempts, lambda a: a.get("type") == "own_axis_name_affix", "own_axis_name_affix attempt")
    _need(attempt.get("proven") is True, "own-axis proof attempt is not marked proven")
    for other in attempts:
        if other is not attempt:
            _need(other.get("proven") is not True, "another proof type is marked proven")
    checks = attempt.get("checks")
    expected_checks = ["same_axis_name", "one_core_is_the_other_plus_the_axis_name",
                       "axis_name_on_every_au_option" if au_value == r_value + au_axis or au_value == au_axis + r_value
                       else "axis_name_on_every_rakuten_option",
                       "axis_name_on_no_option_of_the_other_side", "removal_keeps_options_distinct_and_non_empty",
                       "unique_counterpart"]
    _need(isinstance(checks, list) and [x.get("name") for x in checks] == expected_checks and
          all(x.get("passed") is True for x in checks), "attempt checks missing, reordered, or failed")
    side, position, stripped = _affix_proof(r_value, au_value, r_label, r_family, au_values)
    norm = attempt.get("normalization")
    expected_result = au_value[:-len(au_axis)] if side == "au" and position == "suffix" else (
        au_value[len(au_axis):] if side == "au" else
        r_value[:-len(au_axis)] if position == "suffix" else r_value[len(au_axis):])
    _need(norm == [{"step": "remove_axis_name_affix", "side": side, "position": position,
                    "removed_text": au_axis, "result": expected_result}],
          "proof normalization metadata differs from exact raw affix operation")
    _need(attempt.get("removed_text") == au_axis, "proof removed text differs from raw axis name")
    _need(attempt.get("full_option_list_bijection") is True, "attempt lacks complete family bijection")
    _need(sidecar.get("preserved", {}).get("only_change") == norm,
          "preserved only_change metadata differs from recomputed operation")

    return {
        "verified": True,
        "proof_type": "own_axis_name_affix",
        "case_id": task["case_id"], "dossier_id": task["dossier_id"],
        "au_row_key": task["au_row_key"], "axis_key": task["axis_key"],
        "direction": task["direction"],
        "raw_axis_name": au_axis,
        "rakuten": {"axis_label": r_label, "selected_value": r_value,
                     "family": list(r_family), "selected_span": selected_values[axis_index],
                     "family_spans": r_family_spans},
        "au": {"axis_name": au_axis, "selected_value": au_value,
               "family": list(au_values), "selected_span": au_spans[selected_au_index],
               "family_spans": au_spans},
        "affix": {"side": side, "position": position, "removed_text": au_axis,
                  "stripped_family": stripped, "complete_bijection": True},
    }


def validate_sidecar(sidecar: dict, task: dict, request: dict, source_case: dict,
                     source_product: dict, store) -> dict:
    """Return a verified proof receipt or fail closed with ``ValueError``."""
    try:
        return _validate_sidecar(sidecar, task, request, source_case, source_product, store)
    except ValueError:
        raise
    except (KeyError, TypeError, IndexError, AttributeError, OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed or unavailable proof input: {exc}") from exc

