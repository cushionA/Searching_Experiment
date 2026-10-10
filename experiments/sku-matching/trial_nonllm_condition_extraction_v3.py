#!/usr/bin/env python3
"""Bounded GLiNER trial with source-joined, typed SKU axis views.

Creates immutable pre-inference freeze metadata, then extracts span proposals.
All outputs remain research proposals; no gold labels or adoption decisions are read.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUT = ROOT / ".lab-output/sku-novel-real-inputs-20261010-v2/inputs.jsonl"
DEFAULT_PACKETS = ROOT / ".lab-output/sku-gpu-condition-tasks-20261010-v2/model/packets.jsonl"
DEFAULT_MODEL = ROOT / ".deps/sku-gliner-extract-model"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-nonllm-model-trial-20261010-v4"
MODEL_ID = "fastino/gliner2.5-multi-v1"
REVISION = "cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3"
MAX_LEN = 4096
THRESHOLDS = (0.5, 0.7)

sys.path.insert(0, str(HERE))
from trial_nonllm_condition_extraction_v1 import (  # noqa: E402
    ENTITY_DESCRIPTIONS,
    normalize_raw,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)


def _resolve_json_path(document: Any, path: str) -> Any:
    if not path.startswith("$"):
        raise ValueError(f"Expected JSONPath rooted at $: {path}")
    tokens = re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", path[1:])
    rebuilt = "".join((f".{name}" if name else f"[{index}]") for name, index in tokens)
    if rebuilt != path[1:]:
        raise ValueError(f"Unsupported JSONPath syntax: {path}")
    value = document
    for name, index in tokens:
        value = value[name] if name else value[int(index)]
    return value


def _read_source_ref(source_ref: dict[str, Any], cache: dict[str, Any]) -> tuple[Path, Any]:
    rel = source_ref.get("raw_file")
    digest = source_ref.get("raw_sha256")
    if not isinstance(rel, str) or not isinstance(digest, str):
        raise ValueError("source reference missing raw_file/raw_sha256")
    path = (ROOT / rel).resolve()
    if path not in cache:
        if sha256_file(path) != digest:
            raise ValueError(f"raw source hash mismatch: {path}")
        cache[path] = json.loads(path.read_text(encoding="utf-8"))
    return path, cache[path]


def _title_binding(row: dict[str, Any], side: str, text: str) -> dict[str, Any]:
    registry = row.get("evidence_registry") or []
    matches = [x for x in registry if isinstance(x, dict) and x.get("field") == "title"
               and x.get("side") == side and x.get("quote") == text]
    return {
        "source_kind": "title",
        "case_id": row.get("case_id"),
        "side": side,
        "source_field": f"{side}.title_raw",
        "evidence_registry_bindings": matches,
    }


def _axis_name_value(axis: Any) -> tuple[str | None, str | None]:
    if isinstance(axis, dict):
        name = axis.get("axis_name_raw", axis.get("axis_key", axis.get("axis_name")))
        value = axis.get("selected_value_raw", axis.get("value"))
    elif isinstance(axis, (tuple, list)) and len(axis) >= 2:
        name, value = axis[0], axis[1]
    else:
        return None, None
    return (name.strip() if isinstance(name, str) and name.strip() else None,
            value.strip() if isinstance(value, str) and value.strip() else None)


def _rakuten_selector_axis_label(option: dict[str, Any], option_index: int,
                                  selected_sku: dict[str, Any], text_cache: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Join a normalized Rakuten selector value to its raw variantSelectors label span."""
    key, value = option.get("axis_key"), option.get("value")
    if not isinstance(key, str) or not isinstance(value, str):
        raise ValueError("Rakuten selected option missing axis_key/value")
    spans = [x for x in selected_sku.get("selected_sku_exact_property_spans", [])
             if isinstance(x, dict) and x.get("scope") == "selected_sku_original_selector_values"]
    if not spans:
        raise ValueError("Rakuten selected SKU lacks exact selectorValues source span")
    selector_binding = spans[0]
    selector_values_raw = selected_sku.get("selector_values_raw")
    if not isinstance(selector_values_raw, list) or option_index >= len(selector_values_raw):
        raise ValueError("Rakuten selected axis index is absent from selector_values_raw")
    if selector_values_raw[option_index] != value:
        raise ValueError("Rakuten selected axis value does not match selector_values_raw at option index")
    try:
        selector_quote_values = json.loads(selector_binding.get("quote", ""))
    except json.JSONDecodeError as exc:
        raise ValueError("Rakuten exact selectorValues evidence is not a JSON array") from exc
    if not isinstance(selector_quote_values, list) or option_index >= len(selector_quote_values) or selector_quote_values[option_index] != value:
        raise ValueError("Rakuten selected axis value does not match exact raw selectorValues evidence")
    ref = selector_binding.get("source_ref") or {}
    raw_path = ref.get("raw_file")
    raw_sha = ref.get("raw_sha256")
    if not isinstance(raw_path, str) or not isinstance(raw_sha, str):
        raise ValueError("Rakuten selectorValues ref missing raw file/hash")
    path = (ROOT / raw_path).resolve()
    cache_key = str(path)
    if cache_key not in text_cache:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != raw_sha:
            raise ValueError(f"Rakuten raw source hash mismatch: {path}")
        encoding = ref.get("encoding") or "utf-8"
        text_cache[cache_key] = data.decode(encoding)
    raw = text_cache[cache_key]
    selector_start = int(ref.get("html_char_start", 0))
    decoder = json.JSONDecoder()
    all_matches = []
    marker = '"variantSelectors":'
    pos = 0
    while True:
        marker_pos = raw.find(marker, pos)
        if marker_pos < 0:
            break
        array_start = marker_pos + len(marker)
        try:
            selectors, array_end = decoder.raw_decode(raw, array_start)
        except json.JSONDecodeError:
            pos = marker_pos + len(marker)
            continue
        if isinstance(selectors, list):
            for selector_index, selector in enumerate(selectors):
                if not isinstance(selector, dict) or selector.get("key") != key:
                    continue
                values = selector.get("values") or []
                if not any(isinstance(item, dict) and item.get("value") == value for item in values):
                    continue
                all_matches.append((abs(selector_start - marker_pos), marker_pos, array_start,
                                    array_end, selector_index, selectors, selector))
        pos = max(marker_pos + len(marker), array_end)
    if not all_matches:
        raise ValueError(f"Could not source-join Rakuten selector axis key/value: {key}={value}")
    _, marker_pos, array_start, array_end, selector_index, selectors, selector = min(all_matches)
    label = selector.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"Raw Rakuten selector has no label for {key}={value}")
    selector_array = raw[array_start:array_end]
    selector_quote = json.dumps(selector, ensure_ascii=False, separators=(",", ":"))
    # Build an exact label span from the original compact JSON object.
    raw_selector_obj = raw[marker_pos:array_end]
    property_text = '"label":' + json.dumps(label, ensure_ascii=False, separators=(",", ":"))
    local_label = raw_selector_obj.find(property_text)
    if local_label < 0:
        raise ValueError(f"Cannot locate raw selector label span for {label!r}")
    label_start = marker_pos + local_label
    label_end = label_start + len(property_text)
    label_ref = {
        "raw_file": raw_path, "raw_sha256": raw_sha,
        "encoding": ref.get("encoding"), "offset_unit": "unicode_codepoint_after_decode",
        "html_char_start": label_start, "html_char_end": label_end,
        "quote": raw[label_start:label_end],
        "source_container": "variantSelectors", "selector_index": selector_index,
        "axis_key": key,
    }
    return label.strip(), {
        "axis_label_source_ref": label_ref,
        "selector_array_source_ref": {**ref, "selector_index": option_index,
                                       "selector_values_raw": selected_sku.get("selector_values_raw"),
                                       "selector_array_quote": selector_binding.get("quote"),
                                       "selector_object_quote": selector_quote,
                                       "variant_selectors_array_index": selector_index},
    }


def _is_status_axis(axis_name: str, axis_value: str) -> bool:
    low = axis_name.casefold()
    return axis_value in {"あり", "なし"} and ("天板" in axis_name or "レース" in axis_name or "lace" in low)


def _group_axis_unit(groups: dict[tuple[str, str], dict[str, Any]], *, axis_name: str,
                     axis_value: str, binding: dict[str, Any], preserve_duplicate: bool = False) -> None:
    text = f"{axis_name}：{axis_value}"
    key = ("axis_observation" if preserve_duplicate else "axis_view", text)
    if preserve_duplicate:
        # Unique source occurrence, even when the same positive/negative value recurs.
        key = ("axis_observation", f"{text}\0{len(groups)}")
    unit = groups.setdefault(key, {
        "unit_kind": key[0], "text": text,
        "segments": [
            {"segment_type": "axis_label", "start": 0, "end": len(axis_name), "text": axis_name},
            {"segment_type": "axis_value", "start": len(axis_name) + 1,
             "end": len(text), "text": axis_value},
        ],
        "bindings": [],
    })
    unit["bindings"].append(binding)


def _add_raw_status_unit(groups: dict[tuple[str, str], dict[str, Any]], *, axis_value: str,
                         binding: dict[str, Any], occurrence_id: str) -> None:
    groups[("raw_status_value", occurrence_id)] = {
        "unit_kind": "raw_status_value", "text": axis_value,
        "segments": [{"segment_type": "axis_value", "start": 0,
                      "end": len(axis_value), "text": axis_value}],
        "bindings": [binding],
    }


def _native_au_axes(row: dict[str, Any], raw_cache: dict[str, Any]) -> list[dict[str, Any]]:
    au = row.get("au") or {}
    full = au.get("original_full_sku_array")
    ref = au.get("original_full_sku_array_source")
    sku_rows = au.get("sku_rows") or []
    if not isinstance(full, dict) or not isinstance(ref, dict):
        raise ValueError(f"{row.get('case_id')}: missing AU full SKU array/source reference")
    _, raw_document = _read_source_ref(ref, raw_cache)
    source_path = ref.get("json_path")
    source_full = _resolve_json_path(raw_document, source_path)
    if source_full != full:
        raise ValueError(f"{row.get('case_id')}: normalized AU SKU array differs from referenced source JSONPath")
    names = full.get("optionName") or {}
    result = []
    for row_pos, sku_row in enumerate(sku_rows):
        row_key = sku_row.get("row_key")
        try:
            row_ix, col_ix = map(int, str(row_key).rsplit(":", 2)[1:])
        except (ValueError, IndexError):
            raise ValueError(f"{row.get('case_id')}: malformed AU row_key {row_key!r}")
        for axis_key, value_key, name_key, index in (
            ("rowNames", "row", "row", row_ix), ("columnNames", "column", "column", col_ix),
        ):
            values = full.get(axis_key) or []
            axis_name = names.get(name_key)
            if not isinstance(axis_name, str) or not axis_name.strip() or index >= len(values):
                continue
            axis_value = values[index]
            if not isinstance(axis_value, str) or not axis_value.strip():
                continue
            json_path = f"{source_path}.{axis_key}[{index}]"
            native_value = _resolve_json_path(raw_document, json_path)
            native_name = _resolve_json_path(raw_document, f"{source_path}.optionName.{name_key}")
            if native_value != axis_value or native_name != axis_name:
                raise ValueError(f"{row.get('case_id')}: AU axis label/value does not match source JSONPath {json_path}")
            axis_ref = {**ref, "json_path": json_path}
            result.append({
                "axis_name_raw": axis_name.strip(), "axis_value_raw": axis_value.strip(),
                "binding": {
                    "source_kind": "au_native_full_row_axis",
                    "case_id": row.get("case_id"), "au_row_key": row_key,
                    "sku_row_index": row_pos, "axis_index": index,
                    "axis_name_raw": axis_name.strip(), "axis_value_raw": axis_value.strip(),
                    "axis_name_source_ref": {**ref, "json_path": f"{source_path}.optionName.{name_key}"},
                    "axis_value_source_ref": axis_ref,
                    "source_join_verified": True,
                },
            })
    return result


def collect_units(rows: list[dict[str, Any]], packets: list[dict[str, Any]],
                  raw_cache: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Collect literal and typed axis views with complete deduplicated bindings."""
    raw_cache = raw_cache if raw_cache is not None else {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    rakuten_text_cache: dict[str, str] = {}

    def add_literal(kind: str, text: Any, binding: dict[str, Any]) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        key = (kind, text)
        unit = groups.setdefault(key, {
            "unit_kind": kind, "text": text,
            "segments": [{"segment_type": "source_literal", "start": 0, "end": len(text), "text": text}],
            "bindings": [],
        })
        unit["bindings"].append(binding)

    for row in rows:
        for side in ("au", "rakuten"):
            text = (row.get(side) or {}).get("title_raw")
            if isinstance(text, str):
                add_literal("title", text, _title_binding(row, side, text))
        rk = (row.get("rakuten") or {}).get("selected_sku") or {}
        selector_spans = [x for x in rk.get("selected_sku_exact_property_spans", [])
                          if isinstance(x, dict) and x.get("scope") == "selected_sku_original_selector_values"]
        selector_ref = selector_spans[0].get("source_ref") if selector_spans else rk.get("source_ref")
        for option_index, option in enumerate(rk.get("option_values") or []):
            if not isinstance(option, dict):
                continue
            label, source_join = _rakuten_selector_axis_label(option, option_index, rk, rakuten_text_cache)
            value = option.get("value")
            value = value.strip() if isinstance(value, str) else None
            if not label or not value:
                continue
            binding = {
                "source_kind": "rakuten_selected_axis", "case_id": row.get("case_id"),
                "axis_index": option_index, "axis_name_raw": label, "axis_value_raw": value,
                **source_join,
                "axis_evidence_binding": selector_spans[0],
                "selector_values_raw": rk.get("selector_values_raw"),
            }
            _group_axis_unit(groups, axis_name=label, axis_value=value, binding=binding,
                             preserve_duplicate=_is_status_axis(label, value))
            if _is_status_axis(label, value):
                _add_raw_status_unit(groups, axis_value=value, binding=binding,
                                     occurrence_id=f"case:{row.get('case_id')}:{option_index}")
        for native in _native_au_axes(row, raw_cache):
            label, value = native["axis_name_raw"], native["axis_value_raw"]
            _group_axis_unit(groups, axis_name=label, axis_value=value,
                             binding=native["binding"], preserve_duplicate=_is_status_axis(label, value))

    for packet in packets:
        evidence = packet.get("evidence") or []
        for i, item in enumerate(evidence):
            if isinstance(item, dict):
                add_literal("packet_quote", item.get("quote"), {
                    "source_kind": "packet_quote", "packet_id": packet.get("packet_id"),
                    "case_id": packet.get("case_id"), "au_row_key": packet.get("au_row_key"),
                    "evidence_index": i, "evidence_binding": item,
                    "source_ref": item.get("source_ref"),
                })
        context = packet.get("context") or {}
        for side, field in (("au", "au_selected_axes"), ("rakuten", "rakuten_selected_axes")):
            for axis_index, axis in enumerate(context.get(field) or []):
                label, value = _axis_name_value(axis)
                if not label or not value:
                    continue
                binding = {
                    "source_kind": "packet_selected_axis", "packet_id": packet.get("packet_id"),
                    "case_id": packet.get("case_id"), "au_row_key": packet.get("au_row_key"),
                    "side": side, "source_field": f"context.{field}[{axis_index}]",
                    "axis_index": axis_index, "axis_name_raw": label, "axis_value_raw": value,
                    "condition": packet.get("condition"),
                    "evidence_refs": [e.get("source_ref") for e in evidence if isinstance(e, dict)],
                }
                special = _is_status_axis(label, value)
                _group_axis_unit(groups, axis_name=label, axis_value=value, binding=binding,
                                 preserve_duplicate=special)
                if special:
                    # Raw, axis-scoped selected status observations are deliberately not deduplicated.
                    _add_raw_status_unit(groups, axis_value=value, binding=binding,
                                         occurrence_id=f"packet:{packet.get('packet_id')}:{side}:{axis_index}")

    units = list(groups.values())
    for unit in units:
        unit["input_text_sha256"] = sha256_bytes(unit["text"].encode("utf-8"))
        for segment in unit["segments"]:
            if unit["text"][segment["start"]:segment["end"]] != segment["text"]:
                raise ValueError("typed segment boundaries do not reconstruct source-derived text")
    return units


def validate_unit_source_joins(units: list[dict[str, Any]]) -> dict[str, int]:
    """Fail closed unless all native axis bindings and the MBC005 ablation are sourced."""
    au_axis_bindings = 0
    rakuten_axis_bindings = 0
    mbc_board_status: list[tuple[str, str]] = []
    mbc_raw_status = 0
    for unit in units:
        for binding in unit["bindings"]:
            if binding.get("source_kind") == "au_native_full_row_axis":
                if not binding.get("source_join_verified") or not binding.get("axis_name_source_ref", {}).get("json_path") or not binding.get("axis_value_source_ref", {}).get("json_path"):
                    raise ValueError("AU native axis binding lacks verified raw JSONPath references")
                au_axis_bindings += 1
            if binding.get("source_kind") == "rakuten_selected_axis":
                label_ref = binding.get("axis_label_source_ref") or {}
                source_span = binding.get("axis_evidence_binding") or {}
                if not label_ref.get("raw_file") or not label_ref.get("raw_sha256") or not label_ref.get("quote"):
                    raise ValueError("Rakuten selected axis label lacks an exact raw HTML span")
                if not source_span.get("source_ref", {}).get("embedded_json_path"):
                    raise ValueError("Rakuten selected axis value lacks an embedded source path")
                if binding.get("selector_values_raw", [None] * (binding.get("axis_index", 0) + 1))[binding.get("axis_index", 0)] != binding.get("axis_value_raw"):
                    raise ValueError("Rakuten selected axis value does not join to selector values")
                rakuten_axis_bindings += 1
                if unit["unit_kind"] == "axis_observation" and "mbc005" in str(binding.get("case_id", "")).lower() and binding.get("axis_name_raw") == "天板":
                    mbc_board_status.append((binding["case_id"], binding.get("axis_value_raw")))
            if unit["unit_kind"] == "raw_status_value" and "mbc005" in str(binding.get("case_id", "")).lower() and binding.get("axis_name_raw") == "天板":
                mbc_raw_status += 1
    if len(mbc_board_status) != 6 or sorted(value for _, value in mbc_board_status) != ["あり"] * 3 + ["なし"] * 3:
        raise ValueError(f"Expected six MBC005 天板 selected axes (3 あり, 3 なし), found {mbc_board_status}")
    if mbc_raw_status != 6:
        raise ValueError(f"Expected six nondeduplicated MBC005 raw 天板 status units, found {mbc_raw_status}")
    return {"au_native_axis_bindings": au_axis_bindings,
            "rakuten_selected_axis_bindings": rakuten_axis_bindings,
            "mbc005_topboard_axis_observations": len(mbc_board_status),
            "mbc005_topboard_raw_status_units": mbc_raw_status}


def map_spans_to_segments(text: str, spans: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapped = []
    for span in spans:
        start, end = span.get("start"), span.get("end")
        updated = dict(span)
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end > len(text) or start >= end:
            updated["segment_mapping"] = "unresolved_invalid_offsets"
            updated["segment_type"] = None
        else:
            hits = [segment for segment in segments if start >= segment["start"] and end <= segment["end"]]
            if len(hits) == 1:
                updated["segment_mapping"] = "mapped"
                updated["segment_type"] = hits[0]["segment_type"]
            elif any(start < segment["end"] and end > segment["start"] for segment in segments):
                updated["segment_mapping"] = "unresolved_cross_segment"
                updated["segment_type"] = None
            else:
                updated["segment_mapping"] = "unresolved_outside_segments"
                updated["segment_type"] = None
        mapped.append(updated)
    return mapped


def _write_exclusive(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--packets", type=Path, default=DEFAULT_PACKETS)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {args.output}")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    sys.path.insert(0, str(HERE))
    from backend_gliner_extract import GLiNERAttributeExtractor, verify_snapshot

    snapshot = verify_snapshot(args.model_dir)
    manifest_path = args.model_dir / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("repo") != MODEL_ID or manifest.get("revision") != REVISION:
        raise RuntimeError("Local extraction model is not the pinned model/revision")
    rows, packets = read_jsonl(args.input), read_jsonl(args.packets)
    if len(rows) != 56 or len(packets) != 27 or sum(len(x.get("evidence") or []) for x in packets) != 97:
        raise RuntimeError("Frozen inputs changed: expected 56 case rows, 27 packets, 97 quotes")
    raw_cache: dict[str, Any] = {}
    units = collect_units(rows, packets, raw_cache)
    if len(units) > 200:
        raise RuntimeError(f"Bounded v3 unit count exceeds 200: {len(units)}")
    source_join_summary = validate_unit_source_joins(units)
    unit_hash = sha256_bytes(json.dumps(units, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.mkdir()
    freeze = {
        "freeze_created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runner_path": str(Path(__file__).resolve()),
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "inputs": {
            "case_input_path": str(args.input.resolve()), "case_input_sha256": sha256_file(args.input),
            "packet_input_path": str(args.packets.resolve()), "packet_input_sha256": sha256_file(args.packets),
            "case_count": len(rows), "packet_count": len(packets), "packet_quote_count": 97,
            "validated_au_native_source_files": [
                {"path": str(path), "sha256": sha256_file(path)} for path in sorted(raw_cache)
            ],
            "validated_rakuten_selector_source_files": [
                {"path": path, "sha256": digest} for path, digest in sorted({
                    ((binding.get("axis_label_source_ref") or {}).get("raw_file"),
                     (binding.get("axis_label_source_ref") or {}).get("raw_sha256"))
                    for unit in units for binding in unit["bindings"]
                    if binding.get("axis_label_source_ref")
                })
            ],
            "source_join_summary": source_join_summary,
            "unit_count": len(units), "units_sha256": unit_hash,
        },
        "model": {"id": MODEL_ID, "revision": REVISION, "files": snapshot,
                  "source_manifest_sha256": sha256_file(manifest_path)},
        "schema": ENTITY_DESCRIPTIONS,
        "runtime_fixed": {"device": "cpu", "python": sys.version, "platform": platform.platform(),
                          "torch": importlib.metadata.version("torch"),
                          "gliner2": importlib.metadata.version("gliner2"),
                          "batch_size": args.batch_size, "max_len": MAX_LEN,
                          "encoder_max_position_embeddings": 512},
        "fixed_thresholds": {"model_entity_threshold": 0.5, "diagnostic_views": list(THRESHOLDS)},
        "unit_manifest": units,
        "label_access": "none",
    }
    freeze_path = args.output / "freeze.json"
    _write_exclusive(freeze_path, freeze)

    load_start = time.perf_counter()
    extractor = GLiNERAttributeExtractor(args.model_dir, label_style="ja")
    schema = extractor.model.create_schema().entities(ENTITY_DESCRIPTIONS)
    load_seconds = time.perf_counter() - load_start
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True)

    proposals: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    eligible = []
    for unit in units:
        n_tokens = len(tokenizer(unit["text"], add_special_tokens=True, truncation=False)["input_ids"])
        if n_tokens > MAX_LEN:
            errors.append({"unit_kind": unit["unit_kind"], "input_text_sha256": unit["input_text_sha256"],
                           "token_count_untruncated": n_tokens, "error_type": "InputTooLong",
                           "error": "Skipped without truncation", "text": unit["text"]})
        else:
            eligible.append((unit, n_tokens))
    inference_total = 0.0
    for offset in range(0, len(eligible), args.batch_size):
        batch = eligible[offset:offset + args.batch_size]
        inference_elapsed_counted = False
        try:
            started = time.perf_counter()
            raw_results = extractor.model.batch_extract(
                [item[0]["text"] for item in batch], schema, batch_size=args.batch_size,
                threshold=0.5, include_spans=True, include_confidence=True,
            )
            elapsed = time.perf_counter() - started
            inference_total += elapsed
            inference_elapsed_counted = True
            if len(raw_results) != len(batch):
                raise RuntimeError("model output count differs from input batch")
            for (unit, n_tokens), raw in zip(batch, raw_results, strict=True):
                attributes = normalize_raw(unit["text"], raw)
                proposed = {}
                for field, spans in attributes.items():
                    mapped_spans = map_spans_to_segments(unit["text"], spans, unit["segments"])
                    proposed[field] = {
                        "all_spans": mapped_spans,
                        "threshold_views": {
                            str(threshold): [s for s in mapped_spans
                                             if isinstance(s.get("confidence"), (int, float))
                                             and s["confidence"] >= threshold]
                            for threshold in THRESHOLDS
                        },
                    }
                proposals.append({
                    "unit_kind": unit["unit_kind"], "text": unit["text"],
                    "segments": unit["segments"], "bindings": unit["bindings"],
                    "input_text_sha256": unit["input_text_sha256"],
                    "token_count_untruncated": n_tokens, "model_max_len_config": MAX_LEN,
                    "was_truncated": False, "batch_elapsed_seconds": elapsed,
                    "proposals": proposed, "raw_model_output": raw,
                })
        except Exception as exc:
            if not inference_elapsed_counted:
                inference_total += time.perf_counter() - started
            for unit, n_tokens in batch:
                errors.append({"unit_kind": unit["unit_kind"], "input_text_sha256": unit["input_text_sha256"],
                               "token_count_untruncated": n_tokens, "text": unit["text"],
                               "error_type": type(exc).__name__, "error": str(exc)})

    proposals_path = args.output / "proposals.jsonl"
    with proposals_path.open("x", encoding="utf-8") as stream:
        for record in proposals:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    errors_path = args.output / "errors.jsonl"
    with errors_path.open("x", encoding="utf-8") as stream:
        for record in errors:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    metadata = {
        "freeze_sha256": sha256_file(freeze_path), "runner_sha256": freeze["runner_sha256"],
        "unit_count": len(units), "success_count": len(proposals), "error_count": len(errors),
        "model_load_seconds": load_seconds, "inference_total_seconds": inference_total,
        "proposals_sha256": sha256_file(proposals_path), "errors_sha256": sha256_file(errors_path),
        "fixed_thresholds": freeze["fixed_thresholds"],
        "interpretation": "Raw non-generative model span proposals only; no accuracy or final SKU adoption evaluated.",
    }
    _write_exclusive(args.output / "metadata.json", metadata)
    print(json.dumps({"output": str(args.output), "units": len(units), "success": len(proposals),
                      "errors": len(errors), "load_seconds": load_seconds,
                      "inference_total_seconds": inference_total}, ensure_ascii=False))


if __name__ == "__main__":
    main()
