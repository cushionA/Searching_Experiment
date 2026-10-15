#!/usr/bin/env python3
"""Source-bound, table-first description extractor.

This module selects explicit two-column product specification tables from
captured AU description fields and Rakuten ``span.item_desc`` HTML. It never
classifies products or interprets a value as applicable to a particular SKU.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from build_scoped_html_blocks_v1 import _DOM, _Node, _Text, _ancestors, _descendants, _nearest, _text

VERSION = "sku-spec-table-first-v1"
TABLE_LABELS = ("仕様", "商品仕様", "商品詳細", "商品情報")
POLICY = {
    "version": VERSION,
    "selection_basis": "generic table structure and explicit specification caption only",
    "no_category_or_product_rules": True,
    "semantic_or_sku_scope_proof": False,
    "full_body_fallback_on_complex_spans": True,
    "table_only_when_eligible_tables_exist": True,
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_ref(raw_file: str, digest: str, locator: Any, html: str,
                start: int, end: int, **extra: Any) -> dict[str, Any]:
    if not (0 <= start < end <= len(html)):
        raise ValueError("HTML source span is out of bounds")
    result = {"raw_file": raw_file, "sha256": digest, "locator": locator,
            "start": start, "end": end, "quote": html[start:end],
            "offset_unit": "unicode_code_points",
            "html_sha256": _sha(html.encode("utf-8")), **extra}
    if extra.get("json_leaf_start") is not None:
        result["json_leaf_source_ref"] = {
            "start": extra["json_leaf_start"], "end": extra["json_leaf_end"],
            "quote": extra["json_lexeme"], "offset_unit": "unicode_code_points",
            "json_leaf": extra.get("html_leaf"),
        }
    return result


def _table_owner_rows(table: _Node) -> list[_Node]:
    return [n for n in _descendants(table, {"tr"}) if _nearest(n, {"table"}) is table]


def _direct_cells(row: _Node) -> list[_Node]:
    return [n for n in _descendants(row, {"td", "th"}) if _nearest(n, {"tr"}) is row]


def _item_desc(node: _Node) -> bool:
    return any(a.tag == "span" and "item_desc" in a.attrs.get("class", "").split()
               for a in _ancestors(node))


def _render_cell(cell: _Node, html: str, metadata: dict[str, Any]) -> dict[str, Any]:
    links = []
    for link in _descendants(cell, {"a"}):
        href = link.attrs.get("href", "")
        links.append({"href": href, "text": _text(link),
                      "source_ref": _source_ref(metadata["raw_file"], metadata["sha256"],
                metadata["locator"], html, link.start, link.end,
                          html_leaf=metadata.get("html_leaf"),
                          encoding=metadata.get("encoding"),
                          json_leaf_start=metadata.get("json_leaf_start"),
                          json_leaf_end=metadata.get("json_leaf_end"),
                          json_lexeme=metadata.get("json_lexeme"))})
    return {"tag": cell.tag, "text": _text(cell), "attrs": dict(cell.attrs),
            "links": links,
            "source_ref": _source_ref(metadata["raw_file"], metadata["sha256"],
                metadata["locator"], html, cell.start, cell.end,
                html_leaf=metadata.get("html_leaf"),
                encoding=metadata.get("encoding"),
                json_leaf_start=metadata.get("json_leaf_start"),
                json_leaf_end=metadata.get("json_leaf_end"),
                json_lexeme=metadata.get("json_lexeme"))}


def _candidate(table: _Node, html: str, metadata: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if any(n.tag == "table" for n in _descendants(table, {"table"})):
        # A table nesting another table is treated as layout; do not let nested
        # cell text distort the outer table's key/value rows.
        return None, None
    caption_node = next((n for n in _descendants(table, {"caption"})
                         if _nearest(n, {"table"}) is table), None)
    caption = _text(caption_node) if caption_node else ""
    rows = _table_owner_rows(table)
    parsed: list[tuple[_Node, list[_Node]]] = [(row, _direct_cells(row)) for row in rows]
    data_rows = [(r, c) for r, c in parsed if len(c) == 2 and
                 (c[0].tag == "th" or c[0].attrs.get("scope", "").lower() == "row")]
    two_td = [(r, c) for r, c in parsed if len(c) == 2 and c[0].tag == "td" and c[1].tag == "td"]
    spanning_headings = [(r, c) for r, c in parsed if len(c) == 1 and
                         c[0].attrs.get("colspan", "1") not in ("", "1") and
                         any(word in _text(c[0]) for word in TABLE_LABELS)]
    allowed_td = any(word in caption for word in TABLE_LABELS) or len(spanning_headings) == 1
    if len(data_rows) < 2 and not (allowed_td and len(two_td) >= 2):
        return None, None

    # Any nontrivial layout span outside the narrowly defined single heading
    # row makes row-to-column correspondence ambiguous: retain the full body.
    complex_span = False
    for row, cells in parsed:
        for cell in cells:
            rowspan = cell.attrs.get("rowspan", "1")
            colspan = cell.attrs.get("colspan", "1")
            if rowspan not in ("", "1"):
                complex_span = True
            if colspan not in ("", "1"):
                is_allowed_heading = (len(cells) == 1 and row in [r for r, _ in spanning_headings]
                                      and colspan.isdecimal() and int(colspan) >= 2)
                if not is_allowed_heading:
                    complex_span = True
    if complex_span:
        return None, "complex_rowspan_or_colspan"

    row_data = []
    rendered = []
    for row, cells in parsed:
        rendered_cells = [_render_cell(c, html, metadata) for c in cells]
        row_data.append({"cells": rendered_cells,
                         "source_ref": _source_ref(metadata["raw_file"], metadata["sha256"],
                             metadata["locator"], html, row.start, row.end,
                             html_leaf=metadata.get("html_leaf"),
                             encoding=metadata.get("encoding"),
                             json_leaf_start=metadata.get("json_leaf_start"),
                             json_leaf_end=metadata.get("json_leaf_end"),
                             json_lexeme=metadata.get("json_lexeme"))})
        texts = [c["text"] for c in rendered_cells]
        if not any(texts):
            continue
        if len(texts) >= 2:
            rendered.append(f"{texts[0]}: {' | '.join(texts[1:])}")
        else:
            rendered.append(texts[0])
    if caption:
        rendered.insert(0, caption)
    table_source = _source_ref(metadata["raw_file"], metadata["sha256"], metadata["locator"],
                               html, table.start, table.end,
                               html_leaf=metadata.get("html_leaf"),
                               encoding=metadata.get("encoding"),
                               json_leaf_start=metadata.get("json_leaf_start"),
                               json_leaf_end=metadata.get("json_leaf_end"),
                               json_lexeme=metadata.get("json_lexeme"))
    item = {"caption": caption, "rows": row_data, "text": "\n".join(rendered),
            "source_ref": table_source, "content_sha256": _sha(html[table.start:table.end].encode("utf-8")),
            "provenance": [table_source]}
    return item, None


def _select_html_internal(html: str, source_metadata: dict[str, Any],
                          require_item_desc: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(html, str) or not isinstance(source_metadata, dict):
        raise TypeError("html and source_metadata have invalid types")
    if (not isinstance(source_metadata.get("raw_file"), str) or not source_metadata["raw_file"] or
            not isinstance(source_metadata.get("sha256"), str) or not source_metadata["sha256"] or
            not isinstance(source_metadata.get("locator"), (str, dict)) or not source_metadata["locator"]):
        raise ValueError("source_metadata requires raw_file, sha256 and locator")
    if _sha(html.encode("utf-8")) != source_metadata.get("html_sha256", _sha(html.encode("utf-8"))):
        raise ValueError("decoded HTML hash mismatch")
    if "json_leaf_start" in source_metadata:
        s, e = source_metadata.get("json_leaf_start"), source_metadata.get("json_leaf_end")
        if type(s) is not int or type(e) is not int or not (0 <= s < e):
            raise ValueError("invalid AU JSON leaf offsets")
        if source_metadata.get("json_lexeme") is None:
            raise ValueError("AU exact JSON lexeme is required")
        if (type(source_metadata.get("json_lexeme")) is not str or
                e - s != len(source_metadata["json_lexeme"]) or
                json.loads(source_metadata["json_lexeme"]) != html):
            raise ValueError("AU exact JSON lexeme does not bind the decoded HTML leaf")
    dom = _DOM(html).finish()
    selected, issues = [], []
    for table in (n for n in dom.nodes if n.tag == "table"):
        if require_item_desc and not _item_desc(table):
            continue
        item, issue = _candidate(table, html, source_metadata)
        if issue:
            issues.append(issue)
        if item:
            selected.append(item)
    return selected, issues


def select_html(html: str, source_metadata: dict[str, Any],
                require_item_desc: bool = False) -> list[dict[str, Any]]:
    """Select eligible tables and bind their exact spans to source metadata."""
    return _select_html_internal(html, source_metadata, require_item_desc)[0]


def _json_lexemes(text: str) -> tuple[Any, dict[tuple[Any, ...], tuple[int, int, Any]]]:
    """Parse JSON while recording exact string-value lexemes and Unicode offsets."""
    decoder = json.JSONDecoder()
    strings: dict[tuple[Any, ...], tuple[int, int, Any]] = {}

    def ws(pos: int) -> int:
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        return pos

    def parse(pos: int, path: tuple[Any, ...]) -> tuple[Any, int]:
        pos = ws(pos)
        if pos >= len(text):
            raise ValueError("truncated JSON")
        if text[pos] == "{":
            out = {}; pos = ws(pos + 1)
            if pos < len(text) and text[pos] == "}": return out, pos + 1
            while True:
                key, end = decoder.raw_decode(text, ws(pos))
                if not isinstance(key, str) or key in out: raise ValueError("invalid/duplicate JSON key")
                pos = ws(end)
                if pos >= len(text) or text[pos] != ":": raise ValueError("missing JSON colon")
                out[key], pos = parse(pos + 1, path + (key,)); pos = ws(pos)
                if pos < len(text) and text[pos] == "}": return out, pos + 1
                if pos >= len(text) or text[pos] != ",": raise ValueError("invalid JSON object")
                pos += 1
        if text[pos] == "[":
            out = []; pos = ws(pos + 1)
            if pos < len(text) and text[pos] == "]": return out, pos + 1
            while True:
                value, pos = parse(pos, path + (len(out),)); out.append(value); pos = ws(pos)
                if pos < len(text) and text[pos] == "]": return out, pos + 1
                if pos >= len(text) or text[pos] != ",": raise ValueError("invalid JSON array")
                pos += 1
        value, end = decoder.raw_decode(text, pos)
        if isinstance(value, str): strings[path] = (pos, end, value)
        return value, end
    result, end = parse(0, ())
    if ws(end) != len(text): raise ValueError("trailing JSON data")
    return result, strings


def _path(locator: Any) -> tuple[str, ...]:
    if not isinstance(locator, str) or not locator.startswith("$."):
        raise ValueError("AU locator must be a JSON path beginning '$.'")
    return tuple(locator[2:].split("."))


def _resolve_source(source_root: Path, raw_file: str) -> Path:
    path = (source_root / raw_file).resolve()
    root = source_root.resolve()
    if path != root and root not in path.parents:
        raise ValueError("raw source path escapes source_root")
    return path


def _decode_rakuten_html(raw: bytes, locator: Any) -> tuple[str, str]:
    """Decode strictly, preferring UTF-8 and auditing any declared fallback."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="strict"), "utf-8-sig"
    try:
        return raw.decode("utf-8", errors="strict"), "utf-8"
    except UnicodeDecodeError:
        pass
    declared = locator.get("encoding") if isinstance(locator, dict) else None
    if not declared:
        probe = raw[:8192].decode("ascii", errors="ignore")
        match = re.search(r"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.-]+)", probe, re.I)
        declared = match.group(1) if match else None
    if declared:
        normalized = declared.strip().lower()
        # Common UTF-8 metadata can state a generic alias; retain the decode name.
        try:
            return raw.decode(declared, errors="strict"), declared
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("euc_jp", errors="strict"), "euc_jp"
    except UnicodeDecodeError as exc:
        raise ValueError("Rakuten HTML is not valid UTF-8 or declared/EUC-JP encoding") from exc


def select_document(document: dict[str, Any], source_root: str | Path) -> dict[str, Any]:
    """Select source-bound tables, or return the untouched full description."""
    if not isinstance(document, dict): raise TypeError("document must be a mapping")
    description = document.get("description")
    fallback = description.get("text", "") if isinstance(description, dict) else ""
    if not isinstance(fallback, str): raise ValueError("description.text must be a string")
    has_original_text = isinstance(description, dict) and isinstance(description.get("text"), str)
    segments = description.get("segments", []) if isinstance(description, dict) else []
    refs = [ref for seg in segments if isinstance(seg, dict)
            for ref in seg.get("source_refs", []) if isinstance(ref, dict)]
    result_base = {"side": document.get("side"),
                   "document_id": document.get("document_id"),
                   "document_sha256": document.get("document_sha256"),
                   "tables": [], "raw_sources": [],
                   "policy": dict(POLICY)}
    if not refs:
        return {**result_base, "mode": "full_body_fallback", "text": fallback,
                "policy": {**result_base["policy"], "reason": "no_source_refs"}}
    root = Path(source_root)
    all_tables, raw_sources, complex_issues, fallback_sources = [], [], [], []
    # Segments often repeat the same source leaf. Read each unique binding once.
    unique_refs = {}
    for ref in refs:
        unique_refs.setdefault((ref.get("raw_file"), ref.get("sha256"),
                                json.dumps(ref.get("locator"), sort_keys=True)), ref)
    for ref in unique_refs.values():
        raw_file, digest = ref.get("raw_file"), ref.get("sha256")
        locator = ref.get("locator")
        if not isinstance(raw_file, str) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("source ref requires raw_file and SHA256")
        path = _resolve_source(root, raw_file)
        raw = path.read_bytes()
        if _sha(raw) != digest: raise ValueError(f"raw source SHA256 mismatch: {raw_file}")
        raw_sources.append({"raw_file": raw_file, "sha256": digest, "locator": locator,
                            "byte_count": len(raw)})
        if document.get("side") == "au" or raw_file.lower().endswith(".json"):
            text = raw.decode("utf-8", errors="strict")
            parsed, lexemes = _json_lexemes(text)
            parts = _path(locator)
            value: Any = parsed
            for part in parts: value = value[part]
            if not isinstance(value, str): raise ValueError("AU source locator does not resolve to a string")
            start, end, exact_value = lexemes[parts]
            if exact_value != value or json.loads(text[start:end]) != value:
                raise ValueError("AU exact source lexeme does not match parsed field")
            html = value
            fallback_sources.append(html)
            metadata = {"raw_file": raw_file, "sha256": digest, "locator": locator,
                        "json_leaf_start": start, "json_leaf_end": end,
                        "json_lexeme": text[start:end], "html_leaf": list(parts),
                        "encoding": "utf-8",
                        "html_sha256": _sha(html.encode("utf-8"))}
            found, issues = _select_html_internal(html, metadata, False)
        else:
            html, encoding = _decode_rakuten_html(raw, locator)
            dom_for_fallback = _DOM(html).finish()
            fallback_sources.extend(_text(n) for n in dom_for_fallback.nodes
                                    if n.tag == "span" and "item_desc" in n.attrs.get("class", "").split())
            metadata = {"raw_file": raw_file, "sha256": digest, "locator": locator,
                        "encoding": encoding, "html_sha256": _sha(html.encode("utf-8"))}
            found, issues = _select_html_internal(html, metadata, True)
        raw_sources[-1].update({"encoding": metadata["encoding"],
                                "html_sha256": metadata["html_sha256"]})
        for table in found:
            table["provenance"] = [{"raw_file": ref["raw_file"], "sha256": ref["sha256"],
                                    "locator": ref.get("locator"),
                                    "description_source_ref": ref,
                                    "table_source_ref": table["source_ref"]}]
            table["source_ref"]["description_source_ref"] = ref
            all_tables.append(table)
        complex_issues.extend(issues)
    if not has_original_text and fallback_sources:
        # Documents with source-segment-only schemas lack the materialized
        # description.text. Preserve each source field intact for the fallback.
        fallback = "\n\n".join(part for part in fallback_sources if part)
    if complex_issues:
        return {**result_base, "raw_sources": raw_sources, "mode": "full_body_fallback",
                "text": fallback, "tables": [],
                "policy": {**result_base["policy"], "reason": "complex_table_span",
                           "issues": complex_issues}}
    dedup: dict[str, dict[str, Any]] = {}
    for table in all_tables:
        key = table["content_sha256"]
        if key in dedup:
            dedup[key]["provenance"].extend(table["provenance"])
            dedup[key]["source_ref"].setdefault("duplicate_source_refs", []).append(table["source_ref"])
        else:
            dedup[key] = table
    tables = list(dedup.values())
    if tables:
        return {**result_base, "mode": "spec_table", "text": "\n\n".join(t["text"] for t in tables),
                "tables": tables, "raw_sources": raw_sources}
    return {**result_base, "mode": "full_body_fallback", "text": fallback,
            "raw_sources": raw_sources,
            "policy": {**result_base["policy"], "reason": "no_eligible_tables"}}

