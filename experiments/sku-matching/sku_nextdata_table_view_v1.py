#!/usr/bin/env python3
"""Create a source-verified AU extraItemComment table-only payload view.

The view reuses BODY v3 blocks and their visible text. It filters by the exact
JSON leaf plus parsed DOM ancestry; it does not re-extract or rewrite evidence.
The root caller can use the returned index map to expand model-local indices to
original BODY v3 indices before existing response validation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_SOURCE_ROOT = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
EXTRA_PATH = "$.itemInfo.extraItemComment"

# Reuse the repository's tolerant HTML parser so source_ref DOM paths and spans
# are checked against the same structural path convention used by BODY v3.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import build_scoped_html_blocks_v1 as HTML_BLOCKS  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_refs(source_body: Mapping[str, Any]) -> list[dict[str, Any]]:
    binding = source_body.get("source_document_binding") or {}
    refs = binding.get("description_refs") or []
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in refs:
        if isinstance(ref, dict) and ref.get("raw_file") and ref.get("sha256"):
            unique[(str(ref["raw_file"]), str(ref["sha256"]))] = ref
    if len(unique) > 1:
        raise ValueError("source body binds descriptions to multiple raw captures")
    return list(unique.values())


def _raw_capture(source_body: Mapping[str, Any], source_root: Path) -> tuple[dict[str, Any] | None, str | None, Path | None]:
    refs = _source_refs(source_body)
    if not refs:
        return None, None, None
    ref = refs[0]
    rel = Path(str(ref["raw_file"]))
    root = Path(source_root).resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError("raw source path escapes source_root")
    raw = path.read_bytes()
    actual_sha = _sha(raw)
    if actual_sha != ref["sha256"]:
        raise ValueError("raw source capture SHA-256 mismatch")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("raw source capture is not a JSON object")
    return obj, actual_sha, path


def _field_and_dom(source_body: Mapping[str, Any], source_root: Path):
    obj, capture_sha, raw_path = _raw_capture(source_body, source_root)
    if obj is None:
        return None, capture_sha, raw_path, None, None
    item = obj.get("itemInfo")
    if not isinstance(item, dict):
        raise ValueError("raw AU capture lacks itemInfo object")
    value = item.get("extraItemComment")
    if value is None:
        return obj, capture_sha, raw_path, None, None
    if not isinstance(value, str):
        raise ValueError("extraItemComment leaf is not a string")
    dom = HTML_BLOCKS._DOM(value).finish()
    return obj, capture_sha, raw_path, value, dom


def _table_ancestor_path(dom_path: Any) -> bool:
    if not isinstance(dom_path, str):
        return False
    return bool(re.search(r"(?:^|/)table\[\d+\](?:/|$)", dom_path))


def _verify_block_source(block: Mapping[str, Any], html: str, dom: Any,
                         capture_sha: str, raw_file: str) -> None:
    ref = block.get("source_ref")
    if not isinstance(ref, dict):
        raise ValueError("table block lacks source_ref")
    locator = ref.get("locator") or {}
    if (ref.get("raw_file") != raw_file or ref.get("sha256") != capture_sha
            or locator.get("json_path") != EXTRA_PATH):
        raise ValueError("table block source identity differs from requested field")
    start, end = ref.get("start"), ref.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(html):
        raise ValueError("table block source span is invalid")
    if (ref.get("quote") != html[start:end]
            or ref.get("html_sha256") != _sha(html.encode("utf-8"))):
        raise ValueError("table block quote or field hash mismatch")
    path = ref.get("dom_path")
    nodes = {node.path: node for node in dom.nodes}
    node = nodes.get(path)
    if node is None or node.start != start or node.end != end:
        raise ValueError("table block DOM path does not resolve to its exact span")
    if not any(parent.tag == "table" for parent in HTML_BLOCKS._ancestors(node)):
        raise ValueError("table block DOM path is not beneath a table")


def table_block_ids(source_body: Mapping[str, Any],
                    source_root: Path = DEFAULT_SOURCE_ROOT) -> list[str]:
    """Return verified existing BODY v3 block IDs inside extraItemComment tables.

    Only native_description blocks whose locator is exactly
    ``$.itemInfo.extraItemComment`` and whose DOM node has a table ancestor are
    eligible. Missing/empty fields yield an empty list. No fallback field is
    used. The raw JSON hash, exact leaf span/hash, and parsed ancestry are
    verified before a block ID is returned.
    """
    _, capture_sha, raw_path, html, dom = _field_and_dom(source_body, source_root)
    if html is None:
        return []
    raw_file = next(iter(_source_refs(source_body)))["raw_file"]
    selected: list[str] = []
    seen: set[str] = set()
    for block in source_body.get("blocks", []):
        if block.get("field") != "native_description":
            continue
        ref = block.get("source_ref") or {}
        locator = ref.get("locator") or {}
        if locator.get("json_path") != EXTRA_PATH or not _table_ancestor_path(ref.get("dom_path")):
            continue
        _verify_block_source(block, html, dom, capture_sha, str(raw_file))
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            raise ValueError("table block lacks block_id")
        if block_id in seen:
            raise ValueError("duplicate table block_id")
        seen.add(block_id)
        selected.append(block_id)
    return selected


def table_html_audit(source_body: Mapping[str, Any],
                     source_root: Path = DEFAULT_SOURCE_ROOT,
                     selected_block_ids: list[str] | None = None) -> dict[str, Any]:
    """Return compact, exact-span audit for every table node in the raw leaf."""
    _, capture_sha, raw_path, html, dom = _field_and_dom(source_body, source_root)
    doc_sha = source_body.get("document_sha256")
    base = {"document_sha256": doc_sha, "side": source_body.get("side"),
            "source_capture_sha256": capture_sha,
            "source_raw_file": str(next(iter(_source_refs(source_body)))["raw_file"]) if _source_refs(source_body) else None,
            "field_json_path": EXTRA_PATH, "field_present": html is not None,
            "field_sha256": _sha(html.encode("utf-8")) if html is not None else None,
            "field_chars": len(html) if html is not None else 0,
            "selected_table_block_ids": list(selected_block_ids or []),
            "original_status": source_body.get("status")}
    if html is None or dom is None:
        base.update({"table_count": 0, "tables": []})
        return base
    tables = [node for node in dom.nodes if node.tag == "table"]
    ordinal = {node.path: i + 1 for i, node in enumerate(tables)}
    rows = []
    for node in tables:
        start, end = node.start, node.end
        if not (0 <= start <= end <= len(html)):
            raise ValueError("parsed table span is invalid")
        table_raw = html[start:end]
        ancestors = [parent for parent in reversed(list(HTML_BLOCKS._ancestors(node)))
                     if parent.tag == "table"]
        rows.append({"table_ordinal": ordinal[node.path], "dom_path": node.path,
                     "ancestor_table_ordinals_outer_to_self": [ordinal[p.path] for p in ancestors] + [ordinal[node.path]],
                     "parent_table_ordinal": ordinal[ancestors[-1].path] if ancestors else None,
                     "start": start, "end": end, "offset_unit": "unicode_code_points",
                     "html_sha256": _sha(table_raw.encode("utf-8")), "html_chars": len(table_raw),
                     "closure": node.closure})
    base.update({"table_count": len(rows), "tables": rows})
    return base


def _source_body_for(doc_sha: str, source_bodies_by_sha: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    source = source_bodies_by_sha.get(doc_sha)
    if not isinstance(source, Mapping):
        raise ValueError("missing full source body for payload document " + doc_sha)
    if source.get("document_sha256") != doc_sha:
        raise ValueError("source body map key/document SHA mismatch")
    return source


def _filter_public_body(public_body: dict[str, Any], source_body: Mapping[str, Any],
                        source_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    doc_sha = str(public_body.get("document_sha256") or "")
    if doc_sha != source_body.get("document_sha256"):
        raise ValueError("public and full source body document hashes differ")
    if source_body.get("side") != "au":
        return public_body, [], {"document_sha256": doc_sha, "side": source_body.get("side"), "filtered": False}
    selected_ids = set(table_block_ids(source_body, source_root))
    source_blocks = {str(b.get("block_id")): b for b in source_body.get("blocks", [])}
    old_blocks = public_body.get("blocks") or []
    new_blocks = []
    index_map = []
    for original_index, block in enumerate(old_blocks):
        block_id = block.get("block_id")
        full = source_blocks.get(str(block_id))
        if full is None:
            raise ValueError("payload block missing from full source body")
        if block.get("support_text") != full.get("support_text"):
            raise ValueError("payload support_text differs from source body")
        if block_id not in selected_ids:
            continue
        new_index = len(new_blocks)
        copied = deepcopy(block)
        copied["body_index"] = new_index
        new_blocks.append(copied)
        index_map.append({"local_index": new_index,
                          "original_body_index": block.get("body_index", original_index),
                          "block_id": block_id,
                          "source_alias": block.get("source_alias")})
    out = deepcopy(public_body)
    out["blocks"] = new_blocks
    out["status"] = "eligible_blocks" if new_blocks else "no_eligible_native_blocks"
    audit = table_html_audit(source_body, source_root, [x["block_id"] for x in index_map])
    audit["filtered"] = True
    audit["filtered_block_count"] = len(new_blocks)
    audit["original_public_block_count"] = len(old_blocks)
    audit["filtered_status"] = out["status"]
    return out, index_map, audit


def apply_table_view(payload: Mapping[str, Any],
                     source_bodies_by_sha: Mapping[str, Mapping[str, Any]],
                     source_root: Path = DEFAULT_SOURCE_ROOT
                     ) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Filter AU source and target bodies, returning payload, index map, audit.

    The input payload and full source bodies are not mutated. Rakuten bodies are
    copied unchanged. Each filtered AU body's body_index is local to the view,
    while source_alias is preserved byte-for-byte. The index map is keyed by
    document_sha256 and expands local indices to original BODY v3 indices.
    """
    out = deepcopy(dict(payload))
    context = out.get("context")
    if not isinstance(context, dict):
        raise ValueError("payload context is missing")
    index_map: dict[str, list[dict[str, Any]]] = {}
    au_audit: dict[str, dict[str, Any]] = {}

    def filter_one(public_body: dict[str, Any]) -> dict[str, Any]:
        doc_sha = str(public_body.get("document_sha256") or "")
        source = _source_body_for(doc_sha, source_bodies_by_sha)
        if source.get("side") != "au":
            return deepcopy(public_body)
        filtered, mapping, audit = _filter_public_body(public_body, source, source_root)
        index_map[doc_sha] = mapping
        au_audit[doc_sha] = audit
        return filtered

    source_rows = context.get("eligible_source_bodies")
    if not isinstance(source_rows, list):
        raise ValueError("eligible_source_bodies must be a list")
    context["eligible_source_bodies"] = [filter_one(body) for body in source_rows]
    target = context.get("eligible_target_body")
    if isinstance(target, dict):
        context["eligible_target_body"] = filter_one(target)
    table_doc_count = sum(1 for row in au_audit.values() if row.get("table_count", 0) > 0)
    empty_doc_count = sum(1 for row in au_audit.values() if row.get("table_count", 0) == 0)
    audit = {"schema_version": "sku-nextdata-table-view-audit-v1",
             "au_document_count": len(au_audit), "au_documents_with_tables": table_doc_count,
             "au_documents_without_tables": empty_doc_count,
             "table_count": sum(row.get("table_count", 0) for row in au_audit.values()),
             "selected_table_block_count": sum(row.get("filtered_block_count", 0) for row in au_audit.values()),
             "documents": au_audit}
    return out, index_map, audit
