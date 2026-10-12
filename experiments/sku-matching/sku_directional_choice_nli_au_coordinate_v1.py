#!/usr/bin/env python3
"""DEV-only coordinate-aware AU family adapter for directional choice NLI."""
from __future__ import annotations

import re
import hashlib
import json
from urllib.parse import unquote, urlsplit
from pathlib import Path
from typing import Any

import sku_directional_choice_nli_v1 as base

_AXIS_PATH = re.compile(r"^\$\.itemInfo\.skuInfo\.(row|column)Names\[(0|[1-9][0-9]*)\]$")
_RAK_LABEL_PATH = re.compile(r"^variantSelectors\[(0|[1-9][0-9]*)\]\.label$")
_RAK_VALUE_PATH = re.compile(r"^sku\[variantId=([^]]+)\]\.selectorValues\[(0|[1-9][0-9]*)\]$")


def _au_family(store: Any, fact: dict[str, Any]) -> dict[str, Any]:
    """Resolve the source coordinate from the selected raw value's exact JSON path."""
    span = fact.get("value_span")
    if not isinstance(span, dict):
        raise ValueError("AU selected row fact lacks a source span")
    raw_file = str(span.get("raw_file", "")).replace("\\", "/")
    locator = span.get("locator")
    path = locator.get("json_path") if isinstance(locator, dict) else None
    match = _AXIS_PATH.fullmatch(path or "")
    if not raw_file or not match:
        raise ValueError("AU selected value has no exact row/column source coordinate")
    kind, index_text = match.groups()
    expected_sha = span.get("sha256")
    store.raw(raw_file, expected_sha)
    source = store.json(raw_file)
    sku = (source.get("itemInfo") or {}).get("skuInfo") or {}
    names = sku.get("optionName") or {}
    family_key = kind + "Names"
    family = sku.get(family_key)
    if not isinstance(family, list) or not family or any(not isinstance(v, str) or not v for v in family):
        raise ValueError("AU source option family is malformed")
    if len(family) != len(set(family)):
        raise ValueError("duplicate AU source option values")
    index = int(index_text)
    if index >= len(family) or family[index] != fact.get("value"):
        raise ValueError("AU selected value differs from exact source coordinate")
    axis_name = names.get(kind)
    if not isinstance(axis_name, str) or not axis_name or axis_name != fact.get("axis_name"):
        raise ValueError("AU axis caption differs from exact source coordinate")
    name_path = f"$.itemInfo.skuInfo.optionName.{kind}"
    name_span = fact.get("axis_name_span")
    if not isinstance(name_span, dict):
        raise ValueError("AU axis name has no existing source-coordinate span")
    name_locator = name_span.get("locator") or {}
    if (name_locator.get("json_path") != name_path
            or name_span.get("sha256") != expected_sha
            or str(name_span.get("raw_file", "")).replace("\\", "/") != raw_file):
        raise ValueError("AU axis name span does not match selected value coordinate")
    base._verify_span(store, name_span, axis_name, "AU raw axis name")
    if name_span.get("sha256") != expected_sha or name_span.get("raw_file", "").replace("\\", "/") != raw_file:
        raise ValueError("AU axis name/source identity differs from selected value")
    members = []
    for member_index, value in enumerate(family):
        member_span = base.SOURCES.json_leaf_span(store, raw_file,
                         f"$.itemInfo.skuInfo.{family_key}[{member_index}]", value)
        base._verify_span(store, member_span, value, "AU whole option member")
        if member_span.get("sha256") != expected_sha:
            raise ValueError("AU family member/source SHA differs")
        members.append({"index": member_index, "value": value,
                        "evidence_id": base._span_id(member_span), "span": member_span,
                        "role": "selected" if member_index == index else "unselected"})
    return {"axis_name": axis_name, "axis_name_span": name_span,
            "family_values": family, "members": members,
            "family_source": {"raw_file": raw_file, "sha256": expected_sha,
                              "locator": name_span["locator"]}, "axis_kind": kind}


def _rakuten_family(store: Any, fact: dict[str, Any]) -> dict[str, Any]:
    label_span, selected_span = fact.get("axis_name_span"), fact.get("value_span")
    if not isinstance(label_span, dict) or not isinstance(selected_span, dict):
        raise ValueError("Rakuten source coordinate spans are incomplete")
    label_locator, value_locator = label_span.get("locator") or {}, selected_span.get("locator") or {}
    label_match = _RAK_LABEL_PATH.fullmatch(label_locator.get("path", ""))
    value_match = _RAK_VALUE_PATH.fullmatch(value_locator.get("path", ""))
    if not label_match or not value_match:
        raise ValueError("Rakuten source spans lack exact selector coordinates")
    selector_index = int(label_match.group(1))
    variant_id, value_index_text = value_match.groups()
    if int(value_index_text) != selector_index:
        raise ValueError("Rakuten label/value selector coordinates differ")
    raw_file = str(selected_span.get("raw_file", "")).replace("\\", "/")
    if (not raw_file or str(label_span.get("raw_file", "")).replace("\\", "/") != raw_file
            or label_span.get("sha256") != selected_span.get("sha256")):
        raise ValueError("Rakuten source-coordinate spans have different raw identity")
    store.raw(raw_file, selected_span.get("sha256"))
    selectors = base.SOURCES.rakuten_variant_selectors(
        store, raw_file, value_locator.get("encoding") or "EUC-JP")
    if selector_index >= len(selectors):
        raise ValueError("Rakuten selector coordinate is outside source family")
    selector = selectors[selector_index]
    if (selector.get("key") != fact.get("axis_key")
            or selector.get("label") != fact.get("axis_name")):
        raise ValueError("Rakuten selector key/caption differs from exact source coordinate")
    source_sku_key = fact.get("source_sku_key") or fact.get("identity", {}).get("source_sku_key")
    parsed_key = urlsplit(source_sku_key) if isinstance(source_sku_key, str) else None
    native_variant_id = unquote(parsed_key.fragment.split(":", 1)[0]) if parsed_key and parsed_key.fragment else None
    if not native_variant_id or variant_id != native_variant_id:
        raise ValueError("Rakuten source variant ID differs from selected source SKU key")
    if base._span_id(label_span) != base._span_id(selector.get("label_span")):
        raise ValueError("Rakuten existing label span differs from exact selector locator")
    base._verify_span(store, label_span, fact["axis_name"], "Rakuten exact selector label")
    selected_values = base.SOURCES.rakuten_selected_values(
        store, raw_file, value_locator.get("encoding") or "EUC-JP", variant_id)
    index = int(value_index_text)
    if index >= len(selected_values) or base._span_id(selected_span) != base._span_id(selected_values[index]):
        raise ValueError("Rakuten selected value span differs from exact variant/index locator")
    family = selector.get("values")
    if not isinstance(family, list) or not family or any(not isinstance(v, str) or not v for v in family):
        raise ValueError("Rakuten source option family is malformed")
    if len(family) != len(set(family)):
        raise ValueError("duplicate Rakuten source option values")
    if fact.get("value") not in family or family.count(fact["value"]) != 1:
        raise ValueError("Rakuten selected value is not a unique complete-family member")
    base._verify_span(store, selected_span, fact["value"], "Rakuten selected source value")
    members = []
    for index, value in enumerate(family):
        member_span = base.SOURCES.rakuten_selector_value_span(
            store, raw_file, value_locator.get("encoding") or "EUC-JP",
            selector_index, selector["key"], value)
        base._verify_span(store, member_span, value, "Rakuten whole option member")
        if member_span.get("sha256") != selected_span.get("sha256"):
            raise ValueError("Rakuten family member/source SHA differs")
        members.append({"index": index, "value": value,
                        "evidence_id": base._span_id(member_span), "span": member_span,
                        "role": "selected" if value == fact["value"] else "unselected"})
    return {"axis_name": fact["axis_name"], "axis_name_span": label_span,
            "family_values": family, "members": members,
            "family_source": {"raw_file": raw_file, "sha256": selected_span["sha256"],
                              "locator": label_span["locator"]}, "axis_key": selector["key"]}

def _fact_record(store: Any, task: dict[str, Any]) -> dict[str, Any]:
    fact = base._fact_from_task(store, task)
    fact["axis_key"] = task.get("axis_key")
    fact["source_sku_key"] = task.get("source_sku_key")
    if fact["side"] == "au":
        span = fact.get("value_span") or {}
        locator = span.get("locator") or {}
        match = _AXIS_PATH.fullmatch(locator.get("json_path", ""))
        if not match:
            raise ValueError("AU selected value has no exact row/column source coordinate")
        kind = match.group(1)
        # Derive the caption span from the exact API coordinate; never search by display text.
        supplied_label_span = fact.get("axis_name_span")
        if supplied_label_span is None:
            fact["axis_name_span"] = base.SOURCES.json_leaf_span(
                store, str(span["raw_file"]).replace("\\", "/"),
                f"$.itemInfo.skuInfo.optionName.{kind}", fact["axis_name"])
        else:
            fact["axis_name_span"] = supplied_label_span
        base._verify_span(store, fact["axis_name_span"], fact["axis_name"], "AU exact-coordinate axis name")
    family = _au_family(store, fact) if fact["side"] == "au" else _rakuten_family(store, fact)
    return {**fact, "family": family}


def prepare(input_dir=base.DEFAULT_INPUT, source_root=base.DEFAULT_SOURCE_ROOT,
            output_dir=base.DEFAULT_OUTPUT):
    original_au_family = base._au_family
    original_rakuten_family = base._rakuten_family
    original_fact_record = base._fact_record
    original_loader = base._load_manifest
    def _load_next_dev_manifest(path):
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("dataset") != "sku-next-dev-coordinate-refids-20261013-v1"
                or manifest.get("independent_test") is not False
                or manifest.get("labels_read") is not False
                or manifest.get("test_split_read") is not False
                or manifest.get("production_adoption_allowed") is not False):
            raise ValueError("input bundle is not the frozen label-blind next-DEV cohort")
        return manifest
    base._au_family = _au_family
    base._rakuten_family = _rakuten_family
    base._fact_record = _fact_record
    base._load_manifest = _load_next_dev_manifest
    try:
        result = base.prepare(input_dir, source_root, output_dir)
    finally:
        base._au_family = original_au_family
        base._rakuten_family = original_rakuten_family
        base._fact_record = original_fact_record
        base._load_manifest = original_loader
    result["dataset"] = "sku-next-dev-coordinate-refids-20261013-v1"
    result["builder_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["selection_policy"] = "frozen label-blind product/case ID SHA ranking"
    result["selected_split"] = "dev"
    result["independent_test_set"] = False
    freeze_path = Path(output_dir) / "freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    freeze.update({k: result[k] for k in ("dataset", "builder_sha256", "selection_policy", "selected_split", "independent_test_set")})
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path = Path(output_dir) / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update({k: result[k] for k in ("dataset", "builder_sha256", "selection_policy", "selected_split", "independent_test_set")})
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
