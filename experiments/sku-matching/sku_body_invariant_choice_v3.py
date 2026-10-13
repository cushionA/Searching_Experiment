#!/usr/bin/env python3
"""Source-only DEV prototype for body-invariant whole-option NLI v3.

This module prepares an experimental model payload and validates/render refs.
It does not run inference, inspect labels or predictions, grade responses, or
make semantic truth/production claims. Model-authored meaning is never inferred
by the renderer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUT = ROOT / ".lab-output/sku-next-body-invariant-dev-20261013-v1"
DEFAULT_SOURCE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-next-body-invariant-v3-source-only-20261013-v1"
PROTOCOL = "sku-body-invariant-choice-v3"
STATE = {"support", "explicit_conflict", "unknown"}
SCOPE = {"same_condition", "different_condition", "uncertain"}
RELATION = {"support", "explicit_conflict", "uncertain"}
EXCLUSIVITY = {"source_family", "source_body", "model_assessed", "uncertain"}
BODY_ROLE = {"product_attribute", "restriction", "comparison", "recommendation", "other", "uncertain"}


def _anchor_excluded(block: dict[str, Any]) -> bool:
    """Fail closed on blocks rooted in an anchor or containing anchor material."""
    ref = block.get("source_ref") or {}
    locator = ref.get("locator") or {}
    dom_path = str(ref.get("dom_path", ""))
    if "/a[" in dom_path or locator.get("tag") == "a":
        return True
    if block.get("links"):
        return True
    return any("/a[" in str((run.get("source_ref") or {}).get("dom_path", ""))
               for run in block.get("support_text_runs", []) if isinstance(run, dict))

import sku_directional_choice_nli_v1 as NLI  # noqa: E402
import sku_directional_choice_nli_au_coordinate_v1 as COORD  # noqa: E402


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with Path(path).open("xb") as stream:
        for row in rows:
            stream.write(canon(row) + b"\n")


def _manifest(input_dir: Path) -> dict[str, Any]:
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    required = {"schema_version": "sku-next-body-invariant-dev-source-v1",
                "dataset": "sku-next-body-invariant-dev-20261013-v1", "split": "dev",
                "independent_test": False, "selected_product_count": 5,
                "selected_case_count": 10, "row_count": 110, "task_count": 452,
                "derived_request_count": 270, "document_count": 10,
                "labels_read": False, "predictions_read": False,
                "model_inference_run": False, "scores_created": False,
                "test_split_read": False, "production_adoption_allowed": False}
    if any(manifest.get(key) != value for key, value in required.items()):
        raise ValueError("input is not the frozen label-blind DEV10 source bundle")
    if not isinstance(manifest.get("outputs_sha256"), dict):
        raise ValueError("source bundle lacks its output hash inventory")
    for name, expected in manifest["outputs_sha256"].items():
        path = input_dir / name
        if not path.is_file() or sha_file(path) != expected:
            raise ValueError("frozen source input hash mismatch: " + name)
    for name, key in (("selection-policy.json", "selection_policy_sha256"),
                      ("selection-plan.json", "selection_plan_sha256")):
        if sha_file(input_dir / name) != manifest.get(key):
            raise ValueError("frozen selection provenance hash mismatch: " + name)
    return manifest


def _validate_input_records(input_dir: Path, manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    names = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl",
             "case-plans.jsonl", "task-request-family-audit.jsonl", "membership.jsonl")
    rows = {name: read_rows(input_dir / name) for name in names}
    expected_counts = {"documents.jsonl": manifest["document_count"],
        "requests.jsonl": manifest["derived_request_count"], "tasks.jsonl": manifest["task_count"],
        "row-plans.jsonl": manifest["row_count"], "case-plans.jsonl": manifest["selected_case_count"],
        "task-request-family-audit.jsonl": manifest["task_count"],
        "membership.jsonl": manifest["selected_case_count"]}
    if any(len(rows[name]) != count for name, count in expected_counts.items()):
        raise ValueError("frozen DEV10 source artifact count mismatch")
    tasks = rows["tasks.jsonl"]
    task_ids = [t.get("task_id") for t in tasks]
    if len(set(task_ids)) != len(tasks):
        raise ValueError("duplicate original source task identity")
    requests = {r.get("request_id"): r for r in rows["requests.jsonl"]}
    if any(t.get("request_id") not in requests for t in tasks):
        raise ValueError("original task references absent source-family request")
    audits = rows["task-request-family-audit.jsonl"]
    if len({a.get("task_id") for a in audits}) != len(audits) or {a.get("task_id") for a in audits} != set(task_ids):
        raise ValueError("source-family receipts do not cover every original task exactly once")
    for receipt in audits:
        family = receipt.get("raw_family")
        members = receipt.get("member_spans")
        values = family.get("ordered_native_family") if isinstance(family, dict) else None
        if (receipt.get("status") != "verified_complete_ordered_native_family"
                or not isinstance(values, list) or not values or not isinstance(members, list)
                or len(members) != len(values)
                or any(not isinstance(m, dict) or m.get("index") != i or m.get("value") != values[i]
                       or not isinstance(m.get("span"), dict) for i, m in enumerate(members))):
            raise ValueError("one or more original task source-family receipts is incomplete")
    return rows


def _field_block(store: Any, doc: dict[str, Any], raw_file: str, expected_sha: str,
                 path: str, field_name: str, value: Any, index: int | None = None) -> dict[str, Any] | None:
    """Build a literal native-field block from a verified JSON string leaf."""
    if not isinstance(value, str) or not value.strip():
        return None
    json_path = path if index is None else f"{path}[{index}]"
    source_ref = {"raw_file": raw_file, "sha256": expected_sha, "json_path": json_path}
    extracted = NLI.extract_blocks(value, source_ref)
    NLI.verify_blocks(value, extracted)
    found = [b for b in extracted if b.get("support_text") and not _anchor_excluded(b)]
    if found:
        result = []
        for block in found:
            raw_ref = dict(block["source_ref"])
            block_id = "native-field:" + NLI.digest({"document_sha256": doc["document_sha256"],
                "raw_file": raw_file, "sha256": expected_sha, "json_path": json_path,
                "block_id": block["block_id"], "support_text": block["support_text"]})
            result.append({"block_id": block_id, "field": field_name,
                "passage": block.get("passage", ""), "support_text": block["support_text"],
                "source_ref": raw_ref, "support_text_runs": block.get("support_text_runs", []),
                "document_sha256": doc["document_sha256"],
                "links_present": bool(block.get("links"))})
        return result  # type: ignore[return-value]
    # Literal text leaves (for example a single spec string) still receive an
    # exact raw JSON leaf span. HTML with no eligible visible text is excluded.
    if "<" in value and ">" in value:
        return None
    span = NLI.SOURCES.json_leaf_span(store, raw_file, json_path, value)
    NLI._verify_span(store, span, value, "native product field")
    block_id = "native-field:" + NLI.digest({"document_sha256": doc["document_sha256"],
        "raw_file": raw_file, "sha256": expected_sha, "json_path": json_path,
        "start": span["start"], "end": span["end"], "quote": value})
    return {"block_id": block_id, "field": field_name, "passage": value,
            "support_text": value, "source_ref": span,
            "support_text_runs": [{"text": value, "kind": "text", "source_ref": span}],
            "document_sha256": doc["document_sha256"]}


def _native_au_blocks(store: Any, doc: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [r for s in doc.get("description", {}).get("segments", [])
            for r in s.get("source_refs", [])]
    refs += list(doc.get("title", {}).get("source_refs", []))
    routes = {}
    for ref in refs:
        path = ref.get("locator") or ref.get("json_path")
        raw_file, digest = ref.get("raw_file"), ref.get("sha256")
        if isinstance(path, str) and path.startswith("$") and isinstance(raw_file, str) and isinstance(digest, str):
            routes[(raw_file.replace("\\", "/"), digest)] = True
    blocks: dict[str, dict[str, Any]] = {}
    for raw_file, expected in sorted(routes):
        store.raw(raw_file, expected)
        obj = store.json(raw_file)
        item = obj.get("itemInfo") or {}
        paths = [("$.itemInfo.itemName", "native_product_name"),
                 ("$.itemInfo.itemComment", "native_description"),
                 ("$.itemInfo.detailComment", "native_description"),
                 ("$.itemInfo.extraItemComment", "native_description"),
                 ("$.itemInfo.extraItemCommentWithVideo", "native_description")]
        for path, field in paths:
            try:
                value = NLI.SOURCES.resolve_json_path(obj, path)
            except (KeyError, IndexError, ValueError, TypeError):
                continue
            field_blocks = _field_block(store, doc, raw_file, expected, path, field, value)
            if field_blocks:
                for block in (field_blocks if isinstance(field_blocks, list) else [field_blocks]):
                    blocks[block["block_id"]] = block
        sku = item.get("skuInfo") or {}
        for field_name in ("header", "footer"):
            path = f"$.itemInfo.skuInfo.{field_name}"
            value = sku.get(field_name)
            field_blocks = _field_block(store, doc, raw_file, expected, path,
                                        "native_sku_" + field_name, value)
            if field_blocks:
                for block in (field_blocks if isinstance(field_blocks, list) else [field_blocks]):
                    blocks[block["block_id"]] = block
        for key in ("specNames", "specValues"):
            values = item.get(key)
            if isinstance(values, list):
                for index, value in enumerate(values):
                    field_blocks = _field_block(store, doc, raw_file, expected,
                        f"$.itemInfo.{key}", "native_spec_name" if key == "specNames" else "native_spec_value",
                        value, index)
                    if field_blocks:
                        for block in (field_blocks if isinstance(field_blocks, list) else [field_blocks]):
                            blocks[block["block_id"]] = block
            elif isinstance(values, str):
                field_blocks = _field_block(store, doc, raw_file, expected, f"$.itemInfo.{key}",
                    "native_spec_name" if key == "specNames" else "native_spec_value", values)
                if field_blocks:
                    for block in (field_blocks if isinstance(field_blocks, list) else [field_blocks]):
                        blocks[block["block_id"]] = block
    return list(blocks.values())


def _native_rakuten_blocks(store: Any, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """No eligible Rakuten body field is declared by this source bundle.

    The captured route is whole-page visible text, which can mix product
    description with price, inventory, and related/comparison content. Without
    a native field ref this prototype abstains from using it as body evidence.
    Source title and structured SKU rows remain separately available.
    """
    return []


def _source_body(store: Any, doc: dict[str, Any]) -> dict[str, Any]:
    blocks = _native_au_blocks(store, doc) if doc.get("side") == "au" else _native_rakuten_blocks(store, doc)
    citations = []
    for i, block in enumerate(blocks):
        ref = block["source_ref"]
        citations.append({"evidence_id": "body:" + block["block_id"], "block_id": block["block_id"],
            "document_sha256": doc["document_sha256"], "source_raw_file": ref["raw_file"],
            "source_raw_sha256": ref["sha256"], "source_locator": ref["locator"],
            "source_start": ref["start"], "source_end": ref["end"], "source_quote": ref["quote"],
            "field": block["field"], "block_index": i})
    description_refs = [r for s in doc.get("description", {}).get("segments", [])
                        for r in s.get("source_refs", [])]
    title_refs = list(doc.get("title", {}).get("source_refs", []))
    return {"document_id": doc["document_id"], "document_sha256": doc["document_sha256"],
            "side": doc["side"], "title": doc.get("title"), "blocks": blocks,
            "citations": citations, "status": "eligible_blocks" if blocks else "no_eligible_native_blocks",
            "source_document_binding": {"title_refs": title_refs, "description_refs": description_refs},
            "native_field_policy": "AU native title/spec/body fields; Rakuten captured page visible blocks with anchor-filtered support text; no inferred titles"}


def _target_sku_inventory(store: Any, doc: dict[str, Any]) -> dict[str, Any]:
    """Expose source-bound native option axes and values, excluding price/stock."""
    title_refs = doc.get("title", {}).get("source_refs", [])
    all_identity_refs = list(title_refs) + [r for s in doc.get("description", {}).get("segments", [])
                                           for r in s.get("source_refs", [])]
    sources = []
    for ref in all_identity_refs:
        raw_file, digest = ref.get("raw_file"), ref.get("sha256")
        if isinstance(raw_file, str) and isinstance(digest, str):
            raw_file = raw_file.replace("\\", "/")
            if (raw_file, digest) not in sources:
                sources.append((raw_file, digest))
    title_sources = []
    for ref in title_refs:
        raw_file, digest = ref.get("raw_file"), ref.get("sha256")
        locator = ref.get("locator") or {}
        path = locator.get("json_path") if isinstance(locator, dict) else None
        if (isinstance(raw_file, str) and isinstance(digest, str)
                and path in {"$.itemInfo.itemTitle", "$.itemInfo.itemName"}):
            pair = (raw_file.replace("\\", "/"), digest)
            if pair not in title_sources:
                title_sources.append(pair)
    au_match = title_sources[0] if len(title_sources) == 1 and title_sources[0][0].endswith(".json") else None
    if doc.get("side") == "au" and au_match:
        raw_file, digest = au_match
        store.raw(raw_file, digest)
        sku = (store.json(raw_file).get("itemInfo") or {}).get("skuInfo") or {}
        axes = []
        for kind in ("row", "column"):
            name = (sku.get("optionName") or {}).get(kind)
            values = sku.get(kind + "Names")
            if not isinstance(name, str) or not name.strip() or not isinstance(values, list) or not values:
                continue
            name_span = NLI.SOURCES.json_leaf_span(store, raw_file,
                f"$.itemInfo.skuInfo.optionName.{kind}", name)
            members = []
            for i, value in enumerate(values):
                if not isinstance(value, str) or not value:
                    raise ValueError("malformed AU native target option family")
                span = NLI.SOURCES.json_leaf_span(store, raw_file,
                    f"$.itemInfo.skuInfo.{kind}Names[{i}]", value)
                members.append({"index": i, "value": value, "evidence_id": NLI._span_id(span), "span": span})
            axes.append({"axis_name": name, "axis_name_span": name_span,
                         "axis_kind": kind, "members": members})
        if not axes:
            return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
                    "axes": [], "all_option_states": [], "row_completeness_basis": "no complete native option axis"}
        # Validate native row/column and matrix dimensions without reading or
        # exposing per-SKU stock, price, or availability values.
        row_values, col_values = sku.get("rowNames"), sku.get("columnNames")
        expected_rows = len(row_values) if isinstance(row_values, list) and any(row_values) else 1
        expected_cols = len(col_values) if isinstance(col_values, list) and any(col_values) else 1
        matrix = sku.get("stockList")
        if (not isinstance(matrix, list) or len(matrix) != expected_rows
                or any(not isinstance(row, list) or len(row) != expected_cols for row in matrix)):
            return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
                    "axes": axes, "all_option_states": [], "row_completeness_basis": "native row/column matrix shape incomplete"}
        # Every state is tied to one exact native stockList matrix coordinate;
        # the cell's price/stock/availability content is never read or exposed.
        states, row_refs = [], []
        row_axis = next((a for a in axes if a["axis_kind"] == "row"), None)
        col_axis = next((a for a in axes if a["axis_kind"] == "column"), None)
        for ri in range(expected_rows):
            for ci in range(expected_cols):
                row_path = f"$.itemInfo.skuInfo.stockList[{ri}][{ci}]"
                cell = NLI.SOURCES.resolve_json_path(store.json(raw_file), row_path)
                if not isinstance(cell, dict):
                    return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
                            "axes": axes, "all_option_states": [], "row_completeness_basis": "native row matrix cell is not a SKU record"}
                values_map, member_ids = [], []
                if row_axis:
                    member = row_axis["members"][ri]
                    values_map.append({"axis_index": next(i for i, a in enumerate(axes) if a is row_axis), "axis_kind": "row",
                        "axis_name": row_axis["axis_name"], "value": member["value"],
                        "member_evidence_id": member["evidence_id"]})
                    member_ids.append(member["evidence_id"])
                if col_axis:
                    member = col_axis["members"][ci]
                    values_map.append({"axis_index": next(i for i, a in enumerate(axes) if a is col_axis), "axis_kind": "column",
                        "axis_name": col_axis["axis_name"], "value": member["value"],
                        "member_evidence_id": member["evidence_id"]})
                    member_ids.append(member["evidence_id"])
                row_ref_id = f"target-native-row:{doc['document_sha256'][:16]}:{ri}:{ci}"
                row_refs.append({"ref_id": row_ref_id, "raw_file": raw_file, "sha256": digest,
                                 "json_path": row_path, "row_index": ri, "column_index": ci})
                states.append({"state_index": len(states), "values": values_map,
                               "member_evidence_ids": member_ids, "row_ref_id": row_ref_id})
        for axis in axes:
            observed = {v["value"] for s in states for v in s["values"]
                        if v["axis_kind"] == axis["axis_kind"]}
            if observed != {m["value"] for m in axis["members"]}:
                return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
                        "axes": axes, "all_option_states": [], "row_completeness_basis": "native SKU row coverage misses an axis family member"}
        return {"status": "verified_native_axes", "document_sha256": doc["document_sha256"],
                "raw_file": raw_file, "source_sha256": digest, "axes": axes,
                "all_option_states": states,
                "native_row_refs": row_refs,
                "row_completeness_basis": "each row/column state binds to an existing native stockList matrix coordinate; cell contents are excluded"}
    if doc.get("side") == "rakuten":
        # Source SKU axis/value records are exposed where a captured raw SKU
        # locator is present; absence stays explicit instead of guessing.
        html_match = next(((f, h) for f, h in sources if not f.endswith(".json")), None)
        if html_match:
            raw_file, digest = html_match
            store.raw(raw_file, digest)
            locators = [r.get("locator") for r in all_identity_refs if r.get("raw_file") == raw_file]
            encoding = next((x.get("encoding") for x in locators if isinstance(x, dict) and x.get("encoding")), "EUC-JP")
            selectors = NLI.SOURCES.rakuten_variant_selectors(store, raw_file, encoding)
            axes = []
            for index, selector in enumerate(selectors):
                label, values = selector.get("label"), selector.get("values")
                label_span = selector.get("label_span")
                if not isinstance(label, str) or not label or not isinstance(values, list) or not values:
                    raise ValueError("malformed Rakuten native target selector family")
                members = []
                for value in values:
                    span = NLI.SOURCES.rakuten_selector_value_span(store, raw_file, encoding,
                        index, selector["key"], value)
                    if not span:
                        raise ValueError("missing exact Rakuten option value source span")
                    members.append({"index": len(members), "value": value,
                                    "evidence_id": NLI._span_id(span), "span": span})
                axes.append({"axis_name": label, "axis_name_span": label_span,
                    "axis_key": selector["key"], "members": members})
            if not axes:
                return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
                        "axes": [], "all_option_states": [], "row_completeness_basis": "no complete native selector axis"}
            # Each native SKU object is an actual target row. Do not assume all
            # cross-axis combinations exist. Duplicate capture copies must agree.
            raw_text = store.text(raw_file, encoding)
            decoder = json.JSONDecoder()
            records: dict[str, list[str]] = {}
            markers = list(re.finditer(r'\{"variantId":', raw_text))
            if not markers:
                return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
                        "axes": axes, "all_option_states": [], "row_completeness_basis": "native SKU row records unavailable"}
            for marker in markers:
                try:
                    record, _end = decoder.raw_decode(raw_text, marker.start())
                except json.JSONDecodeError as exc:
                    raise ValueError("unparsed captured native SKU row marker") from exc
                if not isinstance(record, dict):
                    raise ValueError("captured native SKU marker is not an object")
                variant_id, values = record.get("variantId"), record.get("selectorValues")
                if not isinstance(variant_id, str) or not variant_id:
                    raise ValueError("captured native SKU row lacks identity")
                if values is None:
                    # Rakuten captures serialize a second same-ID stock/status
                    # record adjacent to each selector row. Count it, require
                    # its native shape, and bind it by variant ID; it never
                    # contributes an option state.
                    if not isinstance(record.get("newPurchaseSku"), dict):
                        raise ValueError("unrecognized captured native SKU record shape")
                    records.setdefault(variant_id, [])
                    continue
                if not isinstance(values, list) or len(values) != len(axes):
                    raise ValueError("captured native SKU row lacks complete selector mapping")
                if any(not isinstance(v, str) or not v for v in values):
                    raise ValueError("captured native SKU row has malformed selector values")
                prior = records.setdefault(variant_id, [])
                if prior and prior != values:
                    raise ValueError("conflicting native SKU row copies")
                records[variant_id] = values
            if any(not isinstance(values, list) or not values for values in records.values()):
                raise ValueError("captured SKU status record has no matching selector row")
            states = []
            for variant_id, values in records.items():
                spans = NLI.SOURCES.rakuten_selected_values(store, raw_file, encoding, variant_id)
                if len(spans) != len(axes):
                    raise ValueError("native Rakuten SKU row does not cover every selector axis")
                member_ids = []
                state_values = []
                for i, (axis, value, span) in enumerate(zip(axes, values, spans)):
                    if value not in [m["value"] for m in axis["members"]]:
                        raise ValueError("native Rakuten SKU row value outside complete selector family")
                    NLI._verify_span(store, span, value, "native Rakuten SKU row option")
                    member_ids.append(NLI._span_id(span))
                    state_values.append({"axis_index": i, "axis_key": axis["axis_key"],
                        "axis_name": axis["axis_name"], "value": value,
                        "member_evidence_id": NLI._span_id(span)})
                states.append({"values": state_values, "member_evidence_ids": member_ids,
                    "row_ref_id": f"target-native-row:{doc['document_sha256'][:16]}:{variant_id}",
                    "variant_id": variant_id})
            if not states:
                return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
                        "axes": axes, "all_option_states": [], "row_completeness_basis": "native SKU row records unavailable"}
            for index, state in enumerate(states):
                state["state_index"] = index
            for axis_index, axis in enumerate(axes):
                observed = {v["value"] for state in states for v in state["values"]
                            if v["axis_index"] == axis_index and v["axis_key"] == axis["axis_key"]}
                declared = {member["value"] for member in axis["members"]}
                if observed != declared:
                    return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
                            "axes": axes, "all_option_states": [], "row_completeness_basis": "native SKU rows do not cover every selector family member"}
            return {"status": "verified_native_axes", "document_sha256": doc["document_sha256"],
                    "raw_file": raw_file, "source_sha256": digest, "axes": axes,
                    "all_option_states": states,
                    "row_completeness_basis": "complete captured native per-SKU selectorValues rows; price and inventory excluded"}
    return {"status": "unknown_native_target_sku_inventory", "document_sha256": doc["document_sha256"],
            "axes": [], "all_option_states": [], "row_completeness_basis": "native family source locator unavailable"}


def _public_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    axes = [{"axis_index": i, "axis_name": axis.get("axis_name"),
        "axis_kind": axis.get("axis_kind"), "axis_key": axis.get("axis_key"),
        "members": [{"index": member["index"], "value": member["value"],
                     "evidence_id": member["evidence_id"]} for member in axis.get("members", [])]}
        for i, axis in enumerate(inventory.get("axes", []))]
    return {"status": inventory.get("status"),
        "document_sha256": inventory.get("document_sha256"), "axes": axes,
        "all_option_states": inventory.get("all_option_states", []),
        "row_completeness_basis": inventory.get("row_completeness_basis")}


def build_model_payload(pair: dict[str, Any], context: dict[str, Any],
                        source_docs: dict[str, dict[str, Any]],
                        target_sku_inventory: dict[str, Any]) -> dict[str, Any]:
    """Construct visible fields from frozen source only; hide task binding details."""
    base = NLI._build_model_payload(pair, context, source_docs)
    target_sha = base["context"]["target_document_sha256"]
    body = source_docs.get(target_sha)
    if body is None:
        raise ValueError("target source document missing")
    base["protocol"] = PROTOCOL
    base["evaluation_instruction"] = (
        "For each complete option independently return support, explicit_conflict, or unknown. "
        "Use selected_fact only for supplied exact target facts. Use selected_sku_body only when "
        "the body explicitly maps a condition to a supplied current target-row fact, citing both. "
        "You may use body_invariant only when "
        "eligible text provides positive evidence of a product attribute and value, binds the statement to this target "
        "product, and supports coverage of every complete native target SKU state below. This may be an explicit "
        "universal statement or a product-scope-wide specification; the model must explain that positive scope "
        "basis from quoted text, never from a missing axis. Audit every state "
        "once by option index and exact whole value. Selector-axis absence is not invariant evidence. "
        "Linked/related/comparison/accessory products, defaults, prices, stock, relative winners, or ambiguous "
        "multi-SKU alternatives do not establish target scope. The separately displayed canonical product title "
        "is omitted; use the source-bound native product-name block when one is present, otherwise target identity "
        "remains uncertain. Cite exact body quote refs. Source citations prove origin only. Explicit conflict "
        "needs direct condition contradiction and explained exclusivity. "
        "The model authors all semantic fields; uncertainty or incomplete scope is unknown.")
    base["context"]["target_native_sku_inventory"] = _public_inventory(target_sku_inventory)
    base["context"]["eligible_target_body"] = {
        "document_sha256": body["document_sha256"], "status": body["status"],
        "blocks": [
            {"body_index": i, "block_id": b["block_id"], "field": b["field"],
             "passage": b["passage"], "support_text": b["support_text"],
             "source_sha256": b["source_ref"]["sha256"],
             "source_alias": f"body-source:{target_sha[:16]}:{i}",
             "links_present": b.get("links_present", False)}
            for i, b in enumerate(body["blocks"]) ]}
    # Private source-selected value, task/case/row IDs, and validator bindings
    # never enter the request payload.
    base["sourceproof_certified"] = False
    base["semantic_truth_verified"] = False
    base["production_adoption_allowed"] = False
    return base


def _check_quote_ref(ref: Any, payload: dict[str, Any]) -> bool:
    if not isinstance(ref, dict):
        return False
    if set(ref) != {"body_index", "block_id", "document_sha256", "source_sha256",
                    "source_alias", "quote", "start", "end"}:
        return False
    blocks = payload.get("context", {}).get("eligible_target_body", {}).get("blocks", [])
    i = ref.get("body_index")
    if type(i) is not int or not 0 <= i < len(blocks):
        return False
    block = blocks[i]
    start, end, quote = ref.get("start"), ref.get("end"), ref.get("quote")
    return (ref.get("block_id") == block.get("block_id")
            and ref.get("document_sha256") == payload["context"]["target_document_sha256"]
            and ref.get("source_sha256") == block.get("source_sha256")
            and ref.get("source_alias") == block.get("source_alias")
            and type(start) is int and type(end) is int and 0 <= start < end <= len(block.get("support_text", ""))
            and isinstance(quote, str) and block["support_text"][start:end] == quote)


def _exact_keys(value: Any, required: set[str], allowed: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(label + " must be an object")
    missing, extra = required - set(value), set(value) - allowed
    if missing:
        raise ValueError(label + " missing required properties: " + ",".join(sorted(missing)))
    if extra:
        raise ValueError(label + " has unknown properties: " + ",".join(sorted(extra)))


def validate_literal_response(response: Any, pair: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Schema/transport validation only; semantic conclusions are not made here."""
    import jsonschema
    schema = json.loads((Path(__file__).with_suffix(".schema.json")).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(response),
                    key=lambda error: list(error.absolute_path))
    if errors:
        raise ValueError("literal response schema violation: " + errors[0].message)
    if response.get("protocol") != PROTOCOL:
        raise ValueError("response protocol mismatch")
    if response.get("pair_id") != pair.get("pair_id") or response.get("cache_key") != pair.get("cache_key"):
        raise ValueError("response identity mismatch")
    decisions = response.get("decisions")
    options = pair.get("option_values")
    if not isinstance(decisions, list) or len(decisions) != len(options):
        raise ValueError("response must cover every complete original option")
    for i, (d, value) in enumerate(zip(decisions, options)):
        candidate = pair["candidate_vector"][i]
        required = {"option_index", "whole_option", "option_evidence_id", "whole_value_assessed",
            "state", "scope_assessment", "semantic_relation", "scope_and_coordinate_explanation",
            "option_relation_explanation", "exclusivity_assessment", "exclusivity_explanation",
            "grounding_route", "target_fact_indices", "body_invariant", "body_quote_refs",
            "body_condition_bindings", "direct_contradiction_explanation"}
        _exact_keys(d, required, required, "decision")
        if (d["option_index"] != i or d["whole_option"] != value
                or d["option_evidence_id"] != candidate["option_evidence_id"]
                or d["whole_value_assessed"] is not True):
            raise ValueError("decision does not bind exact original whole option")
        if d["state"] not in STATE or d["scope_assessment"] not in SCOPE or d["semantic_relation"] not in RELATION:
            raise ValueError("unsupported model-authored relation fields")
        if d["exclusivity_assessment"] not in EXCLUSIVITY:
            raise ValueError("unsupported exclusivity field")
        if any(not isinstance(d[k], str) or not d[k].strip() for k in
               ("scope_and_coordinate_explanation", "option_relation_explanation", "exclusivity_explanation")):
            raise ValueError("required literal model explanations missing")
        route = d["grounding_route"]
        facts = d["target_fact_indices"]
        if route not in {"selected_fact", "selected_sku_body", "body_invariant", "none"} or not isinstance(facts, list):
            raise ValueError("grounding route fields malformed")
        if any(type(x) is not int or not 0 <= x < len(payload["context"]["target_selected_facts"]) for x in facts):
            raise ValueError("target fact reference outside supplied context")
        if len(facts) != len(set(facts)):
            raise ValueError("duplicate target fact reference")
        for fact_index in facts:
            fact = payload["context"]["target_selected_facts"][fact_index]
            for span_key, quote in (("axis_name_span", fact.get("axis_name")),
                                    ("value_span", fact.get("value"))):
                span = fact.get(span_key)
                if (not isinstance(span, dict) or span.get("quote") != quote
                        or not isinstance(span.get("raw_file"), str)
                        or not isinstance(span.get("sha256"), str)
                        or type(span.get("start")) is not int or type(span.get("end")) is not int
                        or span["start"] < 0 or span["end"] <= span["start"]):
                    raise ValueError("selected target fact lacks an exact source span")
        invariant = d["body_invariant"]
        refs = d["body_quote_refs"]
        condition_bindings = d["body_condition_bindings"]
        if not isinstance(refs, list) or any(not _check_quote_ref(x, payload) for x in refs):
            raise ValueError("body quote reference malformed")
        if route == "body_invariant":
            if facts or condition_bindings or not isinstance(invariant, dict) or not refs:
                raise ValueError("body invariant route lacks literal invariant fields or exact quote")
            required_inv = {"attribute", "attribute_quote", "value", "value_quote", "role",
                "role_explanation", "subject_resolution", "subject_scope", "target_identity_explanation", "quantifier_scope",
                "invariance_basis", "target_document_sha256",
                "family_audit", "semantic_explanation", "scope_evidence_quote",
                "positive_scope_explanation", "product_scope_assessment", "whole_attribute_assessed"}
            _exact_keys(invariant, required_inv, required_inv, "body invariant")
            if (not required_inv.issubset(invariant) or invariant["role"] not in BODY_ROLE
                    or invariant["subject_resolution"] != "target_product"
                    or invariant["invariance_basis"] not in {"explicit_universal_statement", "positive_scope_wide_product_specification"}
                    or invariant["product_scope_assessment"] != "target_product_level"
                    or invariant["whole_attribute_assessed"] is not True):
                raise ValueError("body invariant semantic fields incomplete")
            if d["state"] in {"support", "explicit_conflict"} and invariant["role"] != "product_attribute":
                raise ValueError("decisive body invariant route requires a product-attribute role")
            if invariant["target_document_sha256"] != payload["context"]["target_document_sha256"]:
                raise ValueError("body invariant target document mismatch")
            quoted_text = "\n".join(ref["quote"] for ref in refs)
            if (invariant["attribute_quote"] not in quoted_text or invariant["value_quote"] not in quoted_text
                    or invariant["scope_evidence_quote"] not in quoted_text):
                raise ValueError("model-authored attribute/value wording is absent from its exact body quote")
            if any(not isinstance(invariant[k], str) or not invariant[k].strip() for k in
                    ("positive_scope_explanation", "quantifier_scope", "target_identity_explanation")):
                raise ValueError("positive target-product scope explanation is missing")
            inventory = payload["context"]["target_native_sku_inventory"]
            expected_states = inventory.get("all_option_states", [])
            audit = invariant["family_audit"]
            if inventory.get("status") != "verified_native_axes" or not isinstance(audit, list) or len(audit) != len(expected_states):
                raise ValueError("complete target native SKU family audit unavailable or incomplete")
            for j, (entry, target_state) in enumerate(zip(audit, expected_states)):
                _exact_keys(entry, {"state_index", "whole_state", "member_evidence_ids", "applicability", "explanation"},
                    {"state_index", "whole_state", "member_evidence_ids", "applicability", "explanation"}, "family audit entry")
                if (not isinstance(entry, dict) or entry.get("state_index") != j
                    or entry.get("whole_state") != target_state or entry.get("applicability") != "covered"
                    or entry.get("member_evidence_ids") != target_state.get("member_evidence_ids")
                    or not isinstance(entry.get("explanation"), str) or not entry["explanation"].strip()):
                    raise ValueError("target SKU audit is incomplete or not in exact source order")
        elif route == "selected_fact":
            if not facts or invariant is not None or refs or condition_bindings:
                raise ValueError("selected-fact route must cite supplied target facts only")
        elif route == "selected_sku_body":
            if not facts or invariant is not None or not refs or not isinstance(condition_bindings, list):
                raise ValueError("selected-SKU body route lacks fact and body bindings")
            if (len(condition_bindings) != len(facts)
                    or {b.get("target_fact_index") for b in condition_bindings if isinstance(b, dict)} != set(facts)):
                raise ValueError("selected-SKU body route must bind each selected fact exactly once")
            quoted = "\n".join(ref["quote"] for ref in refs)
            by_fact = [b for b in condition_bindings if isinstance(b, dict)]
            if len(by_fact) != len(condition_bindings):
                raise ValueError("selected-SKU body condition binding malformed")
            for binding in by_fact:
                _exact_keys(binding, {"target_fact_index", "body_axis_quote", "body_value_quote", "role", "binding_explanation"},
                    {"target_fact_index", "body_axis_quote", "body_value_quote", "role", "binding_explanation"}, "selected-SKU body binding")
                fact = payload["context"]["target_selected_facts"][binding["target_fact_index"]]
                if (not isinstance(binding.get("body_axis_quote"), str)
                        or not isinstance(binding.get("body_value_quote"), str)
                        or binding.get("role") not in BODY_ROLE
                        or not binding["body_axis_quote"] or not binding["body_value_quote"]
                        or binding["body_axis_quote"] not in quoted or binding["body_value_quote"] not in quoted
                        or not isinstance(binding.get("binding_explanation"), str)
                        or not binding["binding_explanation"].strip()):
                    raise ValueError("selected-SKU body binding is not grounded in exact body quotes")
                axis_quote, value_quote = binding["body_axis_quote"], binding["body_value_quote"]
                fact_axis, fact_value = fact["axis_name_span"]["quote"], fact["value_span"]["quote"]
                if axis_quote not in {fact_axis, fact["axis_name"]} or value_quote not in {fact_value, fact["value"]}:
                    raise ValueError("selected-SKU body quote does not bind the exact selected fact axis and value")
                if d["state"] in {"support", "explicit_conflict"} and binding["role"] != "product_attribute":
                    raise ValueError("decisive selected-SKU body binding must assess a product attribute")
        else:
            if d["state"] != "unknown" or facts or invariant is not None or refs or condition_bindings:
                raise ValueError("ungrounded route must remain unknown")
        if d["state"] in {"support", "explicit_conflict"}:
            if d["scope_assessment"] != "same_condition" or d["semantic_relation"] != d["state"]:
                raise ValueError("decisive state lacks same-condition semantic assessment")
            if route not in {"selected_fact", "selected_sku_body", "body_invariant"}:
                raise ValueError("decisive state lacks explicit target grounding route")
        if d["state"] == "explicit_conflict" and d["exclusivity_assessment"] == "uncertain":
            raise ValueError("explicit conflict requires explained non-uncertain exclusivity assessment")
        if d["state"] == "explicit_conflict" and (not isinstance(d.get("direct_contradiction_explanation"), str)
                or not d["direct_contradiction_explanation"].strip()):
            raise ValueError("explicit conflict requires a direct condition contradiction explanation")
    return {"pair_id": pair["pair_id"], "cache_key": pair["cache_key"],
            "decision_count": len(decisions), "transport_validated": True,
            "source_citations_verified": False, "semantic_truth_verified": False,
            "sourceproof_certified": False, "production_adoption_allowed": False}


def render_response(response: dict[str, Any], pair: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Deterministic pass-through after structural checks; never fills semantics."""
    validate_literal_response(response, pair, payload)
    return {"protocol": PROTOCOL, "pair_id": response["pair_id"], "cache_key": response["cache_key"],
        "scope_status": "model_assessed_unproved", "judgments": response["decisions"],
        "source_citations_verified": False, "semantic_truth_verified": False,
        "sourceproof_certified": False, "production_adoption_allowed": False}


def validate_source_response(response: dict[str, Any], pair: dict[str, Any], payload: dict[str, Any],
        source_body: dict[str, Any], source_root: Path) -> dict[str, Any]:
    """Re-derive cited body blocks from pinned native source and verify raw spans.

    This validates citation provenance only. It does not establish what the
    quoted text means or whether the model's target/attribute assessment is true.
    """
    structural = validate_literal_response(response, pair, payload)
    if source_body.get("document_sha256") != payload["context"]["target_document_sha256"]:
        raise ValueError("source-body document binding mismatch")
    eligible = payload.get("context", {}).get("eligible_target_body")
    if not isinstance(eligible, dict) or set(eligible) != {"document_sha256", "status", "blocks"}:
        raise ValueError("payload contains unverified title metadata or malformed target-body envelope")
    binding = source_body.get("source_document_binding") or {}
    title = {"source_refs": binding.get("title_refs", [])}
    desc_refs = binding.get("description_refs", [])
    source_doc = {"document_id": source_body.get("document_id"),
        "document_sha256": source_body.get("document_sha256"), "side": source_body.get("side"),
        "title": title, "description": {"segments": [{"source_refs": [r]} for r in desc_refs]}}
    store = NLI.SOURCES.RawStore(Path(source_root))
    rebuilt_inventory = _target_sku_inventory(store, source_doc)
    supplied_inventory = payload.get("context", {}).get("target_native_sku_inventory")
    if supplied_inventory != _public_inventory(rebuilt_inventory):
        raise ValueError("payload native target SKU inventory differs from complete source rederivation")
    raw_file = rebuilt_inventory.get("raw_file")
    raw_sha = rebuilt_inventory.get("source_sha256")
    if isinstance(raw_file, str) and isinstance(raw_sha, str):
        store.raw(raw_file, raw_sha)
    for row_ref in rebuilt_inventory.get("native_row_refs", []):
        store.raw(row_ref["raw_file"], row_ref["sha256"])
        raw_obj = store.json(row_ref["raw_file"])
        row_obj = NLI.SOURCES.resolve_json_path(raw_obj, row_ref["json_path"])
        if not isinstance(row_obj, dict):
            raise ValueError("AU native target row ref is not an object")
    hypothesis_members = payload.get("context", {}).get("hypothesis", {}).get("family_members", [])
    if len(hypothesis_members) != len(pair.get("option_values", [])):
        raise ValueError("complete source family spans are missing")
    for i, member in enumerate(hypothesis_members):
        if (member.get("whole_option") != pair["option_values"][i]
                or member.get("evidence_id") != pair["candidate_vector"][i].get("option_evidence_id")
                or not NLI.SOURCES.verify_span(store, member.get("span", {}))):
            raise ValueError("candidate whole-option raw source span verification failed")
    for decision in response["decisions"]:
        for fact_index in decision["target_fact_indices"]:
            fact = payload["context"]["target_selected_facts"][fact_index]
            for key in ("axis_name_span", "value_span"):
                if not NLI.SOURCES.verify_span(store, fact[key]):
                    raise ValueError("selected target raw source fact verification failed")
    fresh = _source_body(store, source_doc)
    if eligible.get("status") != fresh.get("status") or eligible.get("document_sha256") != fresh.get("document_sha256"):
        raise ValueError("payload target-body status or document binding differs from source rederivation")
    submitted_blocks = payload.get("context", {}).get("eligible_target_body", {}).get("blocks", [])
    expected_public = [{"body_index": i, "block_id": b["block_id"], "field": b["field"],
        "passage": b["passage"], "support_text": b["support_text"],
        "source_sha256": b["source_ref"]["sha256"],
        "source_alias": f"body-source:{source_body['document_sha256'][:16]}:{i}",
        "links_present": b.get("links_present", False)} for i, b in enumerate(fresh["blocks"])]
    if submitted_blocks != expected_public:
        raise ValueError("payload eligible body block inventory differs from full source rederivation")
    old_by_id = {b.get("block_id"): b for b in source_body.get("blocks", [])}
    new_by_id = {b.get("block_id"): b for b in fresh.get("blocks", [])}
    if set(old_by_id) != set(new_by_id):
        raise ValueError("eligible source block inventory does not rederive")
    for block_id, original in old_by_id.items():
        rebuilt = new_by_id[block_id]
        if (original.get("support_text") != rebuilt.get("support_text")
                or original.get("source_ref") != rebuilt.get("source_ref")):
            raise ValueError("source block text/ref differs from native source rederivation")
        if not NLI.SOURCES.verify_span(store, original["source_ref"]):
            raise ValueError("source block raw SHA/offset/quote verification failed")
    cited = []
    for decision in response["decisions"]:
        for ref in decision["body_quote_refs"]:
            block = payload["context"]["eligible_target_body"]["blocks"][ref["body_index"]]
            source = old_by_id[block["block_id"]]
            cited.append({"body_index": ref["body_index"], "block_id": block["block_id"],
                "document_sha256": source_body["document_sha256"],
                "source_raw_file": source["source_ref"]["raw_file"],
                "source_sha256": source["source_ref"]["sha256"],
                "source_locator": source["source_ref"]["locator"],
                "source_start": source["source_ref"]["start"],
                "source_end": source["source_ref"]["end"],
                "source_quote": source["source_ref"]["quote"],
                "support_text_quote": ref["quote"], "support_text_start": ref["start"],
                "support_text_end": ref["end"]})
    return {**structural, "source_citations_verified": True,
            "source_truth_verified": False, "citations": cited}


def persist_literal_responses_atomically(output_path: Path, literal_json: list[str],
        expected_pair_ids: list[str], validator: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Validate the entire model response set in memory before creating a file."""
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    if len(literal_json) != len(expected_pair_ids) or len(set(expected_pair_ids)) != len(expected_pair_ids):
        raise ValueError("literal model response set does not match unique expected pair IDs")
    parsed = []
    for raw, expected_id in zip(literal_json, expected_pair_ids):
        if not isinstance(raw, str):
            raise TypeError("model response must remain literal JSON text")
        item = json.loads(raw)
        if not isinstance(item, dict) or item.get("pair_id") != expected_id:
            raise ValueError("literal response pair order/identity mismatch")
        validator(item)
        parsed.append(item)
    # Keep the original literal JSON bytes; no reconstruction or semantic patching.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="." + output_path.name + ".", suffix=".tmp",
                                     dir=output_path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            for raw in literal_json:
                stream.write(raw.encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link creation is atomic and fails if another process created the
        # destination, so final output can never be silently replaced.
        os.link(temp_path, output_path)
        temp_path.unlink()
        dir_fd = os.open(output_path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return {"path": str(output_path), "sha256": sha_file(output_path),
                "response_count": len(parsed), "literal_model_output_preserved": True}
    finally:
        if temp_path.exists():
            temp_path.unlink()


def prepare(input_dir: Path = DEFAULT_INPUT, source_root: Path = DEFAULT_SOURCE,
            output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Prepare source-only v3 payloads through the frozen NLI coordinate adapter."""
    input_dir, source_root, output_dir = (Path(x).resolve() for x in (input_dir, source_root, output_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest = _manifest(input_dir)
    frozen_inputs = _validate_input_records(input_dir, manifest)
    inventory_path = source_root.parent / "inventory.json"
    if sha_file(inventory_path) != manifest.get("source_bundle_inventory_sha256"):
        raise ValueError("raw source inventory SHA differs from frozen DEV10 manifest")
    # Use NLI's frozen source-coordinate and fanout preparation, swapping only
    # the DEV identity validator and source-body extraction callback in-memory.
    original_loader, original_fact, original_body = NLI._load_manifest, NLI._fact_record, NLI._document_blocks
    def loader(path: Path) -> dict[str, Any]:
        m = _manifest(path)
        return {**m, "selected_row_count": m["row_count"], "selected_task_count": m["task_count"]}
    def body(store: Any, doc: dict[str, Any]) -> dict[str, Any]:
        record = _source_body(store, doc)
        record["citations"] = [{"evidence_id": "body-block:" + b["block_id"],
            "block_id": b["block_id"], "document_sha256": doc["document_sha256"],
            "source_raw_file": b["source_ref"]["raw_file"],
            "source_raw_sha256": b["source_ref"]["sha256"],
            "source_json_path": (b["source_ref"].get("locator") or {}).get("json_path")}
            for b in record["blocks"]]
        return record
    NLI._load_manifest, NLI._fact_record, NLI._document_blocks = loader, COORD._fact_record, body
    temp_root = output_dir.parent / ("." + output_dir.name + ".source-prep")
    try:
        NLI.prepare(input_dir, source_root, temp_root)
    finally:
        NLI._load_manifest, NLI._fact_record, NLI._document_blocks = original_loader, original_fact, original_body
    try:
        freeze = json.loads((temp_root / "freeze.json").read_text(encoding="utf-8"))
        pairs = read_rows(temp_root / "model-pairs.jsonl")
        contexts = {x["context_id"]: x for x in read_rows(temp_root / "paired-contexts.jsonl")}
        docs_list = read_rows(temp_root / "source-documents.jsonl")
        # Rebuild complete body documents with verified typed target SKU axes.
        store = NLI.SOURCES.RawStore(source_root)
        original_docs = {d["document_sha256"]: d for d in read_rows(input_dir / "documents.jsonl")}
        docs = {}
        sku_inventories = {}
        for doc in docs_list:
            original = original_docs[doc["document_sha256"]]
            docs[doc["document_sha256"]] = _source_body(store, original)
            sku_inventories[doc["document_sha256"]] = _target_sku_inventory(store, original)
        payloads = []
        for pair in pairs:
            context = contexts[pair["context_id"]]
            payloads.append(build_model_payload(pair, context, docs,
                sku_inventories[context["target_document_sha256"]]))
        output_dir.mkdir(parents=True, exist_ok=False)
        copied_source_names = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl",
            "case-plans.jsonl", "task-request-family-audit.jsonl", "membership.jsonl")
        for name in copied_source_names:
            with (output_dir / ("source-" + name)).open("xb") as stream:
                stream.write((input_dir / name).read_bytes())
        # Preserve NLI's exact private worker-binding and preparation receipts.
        # These are validation-only files and are never included in model payloads.
        private_receipts = ("validation-bindings.jsonl", "freeze.json", "summary.json")
        for name in private_receipts:
            with (output_dir / name).open("xb") as stream:
                stream.write((temp_root / name).read_bytes())
        write_rows(output_dir / "model-pairs-v3.jsonl", pairs)
        write_rows(output_dir / "paired-contexts.jsonl", list(contexts.values()))
        write_rows(output_dir / "source-bodies.jsonl", list(docs.values()))
        write_rows(output_dir / "target-native-sku-inventory.jsonl", list(sku_inventories.values()))
        write_rows(output_dir / "model-payloads-v3.jsonl", payloads)
        errors = read_rows(temp_root / "preparation-errors.jsonl")
        write_rows(output_dir / "preparation-errors.jsonl", errors)
        report = {"schema_version": PROTOCOL, "dataset": manifest["dataset"], "split": "dev",
            "source_input_manifest_sha256": sha_file(input_dir / "manifest.json"),
            "source_nli_freeze_sha256": sha_file(temp_root / "freeze.json"),
            "task_count": manifest["task_count"], "row_count": manifest["row_count"],
            "derived_request_count": manifest["derived_request_count"],
            "source_task_receipts_retained": len(frozen_inputs["task-request-family-audit.jsonl"]),
            "private_validation_binding_count": len(read_rows(temp_root / "validation-bindings.jsonl")),
            "private_validation_bindings_preserved_byte_exact": True,
            "worker_payload_contains_private_bindings": False,
            "nli_freeze_and_summary_preserved_byte_exact": True,
            "ordered_complete_source_families_verified": True,
            "document_count": len(docs), "prepared_pair_count": len(pairs),
            "payload_count": len(payloads), "preparation_error_count": len(errors),
            "target_sku_inventory_status_counts": {
                status: sum(x.get("status") == status for x in sku_inventories.values())
                for status in sorted({x.get("status") for x in sku_inventories.values()})},
            "native_body_block_count": sum(len(x["blocks"]) for x in docs.values()),
            "labels_read": False, "predictions_read": False, "test_split_read": False,
            "model_inference_run": False, "scores_created": False,
            "semantic_truth_verified": False, "sourceproof_certified": False,
            "production_adoption_allowed": False,
            "artifact_sha256": {p.name: sha_file(p) for p in output_dir.iterdir() if p.is_file()}}
        with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return report
    finally:
        import shutil
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.source_root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
