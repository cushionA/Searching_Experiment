#!/usr/bin/env python3
"""Restore exact source-bound composition blocks from frozen PREP routes.

The builder reads only source documents, their source_refs, the explicit raw
source registry, and prior block identities used for duplicate suppression.
It never reads labels or model responses. HTML visibility, anchor exclusion,
and table/heading construction are delegated to the existing extractor.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "claude_v9_gate"))

import sku_gate_sources as SOURCES
from build_scoped_html_blocks_v1 import extract_blocks, verify_blocks
from prepare_scoped_html_trial_v1 import context_snippets, render_passage

DEFAULT_PREP = ROOT / ".lab-output/sku-mixed-gpu-prep-20261012-v5"
DEFAULT_MAIN = ROOT / ".lab-output/sku-composition-evidence-20261013-v1"
DEFAULT_V2 = ROOT / ".lab-output/sku-composition-source-revalidation-20261013-v2"
DEFAULT_OUT = ROOT / ".lab-output/sku-composition-source-restoration-20261013-v3"
_ITEM_DESC = re.compile(r"^item_desc\[line=([1-9][0-9]*)\]$")
_SOURCE_KEYS = {"raw_file", "sha256", "locator"}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def rows(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_rows(path: Path, values: list[dict[str, Any]]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False) + "\n")


def aliases(raw_file: str) -> set[str]:
    # Registry alias resolution permits separator variants only.
    return {raw_file, raw_file.replace("\\", "/"), raw_file.replace("/", "\\")}


class BundleStore(SOURCES.RawStore):
    """RawStore restricted to PREP's explicit, hash-pinned registry."""

    def __init__(self, prepared_dir: Path):
        super().__init__(prepared_dir)
        self.entries: dict[str, dict[str, Any]] = {}
        for entry in rows(Path(prepared_dir) / "raw-source-registry.jsonl"):
            for raw_file in [entry["raw_file"], *entry.get("original_aliases", [])]:
                for alias in aliases(raw_file):
                    prior = self.entries.setdefault(alias, entry)
                    if prior != entry:
                        raise ValueError("conflicting frozen raw-source alias: " + alias)

    def raw(self, rel: str, expected_sha256: str | None = None) -> bytes:
        if rel not in self.entries:
            raise FileNotFoundError("raw source absent from PREP registry: " + str(rel))
        entry = self.entries[rel]
        relative = Path(entry["bundle_file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe frozen raw capture path")
        body = (self.root / relative).read_bytes()
        digest = sha_bytes(body)
        if len(body) != entry["bytes"] or digest != entry["sha256"]:
            raise ValueError("frozen raw capture size/SHA mismatch: " + str(rel))
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("source reference SHA differs from frozen capture: " + str(rel))
        key = rel
        self._bytes[key], self._sha[key] = body, digest
        return body

    def sha(self, rel: str) -> str:
        self.raw(rel)
        return self._sha[rel]

    def pin(self, rel: str) -> dict[str, Any]:
        self.raw(rel)
        entry = self.entries[rel]
        return {"raw_file": rel, "bundle_file": entry["bundle_file"],
                "bytes": entry["bytes"], "sha256": entry["sha256"]}


def _locator(ref: dict[str, Any]) -> dict[str, Any] | str | None:
    value = ref.get("locator")
    if isinstance(value, dict):
        return value
    return value if isinstance(value, str) else None


def _route_key(ref: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    raw_file, digest = ref.get("raw_file"), ref.get("sha256")
    loc = _locator(ref)
    if not isinstance(raw_file, str) or not isinstance(digest, str) or len(digest) != 64:
        return None
    if isinstance(loc, dict) and loc.get("kind") == "html_text":
        encoding, path = loc.get("encoding"), loc.get("path")
        if isinstance(encoding, str) and _ITEM_DESC.fullmatch(str(path or "")):
            # Group line fragments from this same documented item_desc field
            # so table rows/headings can bind across their explicit line refs.
            return raw_file, digest, "rakuten_html", encoding, "item_desc"
    if isinstance(loc, dict) and loc.get("kind") == "json_leaf":
        path = loc.get("json_path")
        if isinstance(path, str) and path.startswith("$"):
            return raw_file, digest, "api_html", "", path
    # Older frozen PREP docs use a bare JSONPath string. The path itself is
    # an explicit typed locator; it is not inferred from a title or URL.
    if isinstance(loc, str) and loc.startswith("$"):
        return raw_file, digest, "api_html", "", loc
    return None


def _span(ref: dict[str, Any]) -> bool:
    return (all(key in ref for key in ("raw_file", "sha256", "locator", "start", "end", "quote"))
            and isinstance(ref.get("locator"), dict))


def _source_refs(document: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    result = []
    for segment in document.get("description", {}).get("segments", []):
        for ref in segment.get("source_refs", []):
            result.append((segment, ref))
    return result


def _route_matches(route: tuple[str, str, str, str, str], ref: dict[str, Any]) -> bool:
    key = _route_key(ref)
    if key is None:
        return False
    if route[2] == "rakuten_html":
        return key[:4] == route[:4]
    return key == route


def _bind_run(block: dict[str, Any], run: dict[str, Any], route,
              source_refs, document: dict[str, Any], store: BundleStore) -> tuple[list[dict[str, Any]], list[str]]:
    raw_ref = run.get("source_ref")
    if not isinstance(raw_ref, dict) or not _span(raw_ref):
        return [], ["visible_run_missing_exact_raw_span"]
    # The extractor emits exact raw HTML span offsets. Verify the raw file,
    # JSON leaf/decoded page locator, offset unit, and quote against PREP bytes.
    if not SOURCES.verify_span(store, raw_ref):
        return [], ["visible_run_raw_span_unverified"]
    expected_encoding = route[3] if route[2] == "rakuten_html" else None
    raw_loc = raw_ref.get("locator")
    if route[2] == "rakuten_html":
        if not (isinstance(raw_loc, dict) and raw_loc.get("kind") == "html_text"
                and raw_loc.get("encoding") == expected_encoding
                and raw_ref.get("raw_file") == route[0] and raw_ref.get("sha256") == route[1]):
            return [], ["visible_run_route_identity_mismatch"]
    elif not (isinstance(raw_loc, dict) and raw_loc.get("kind") == "json_leaf"
              and raw_loc.get("json_path") == route[4]
              and raw_ref.get("raw_file") == route[0] and raw_ref.get("sha256") == route[1]):
        return [], ["visible_run_route_identity_mismatch"]

    text = document.get("description", {}).get("text", "")
    run_text = run.get("text")
    if not isinstance(run_text, str) or not run_text.strip():
        return [], []
    visible_parts = [part.strip() for part in run_text.splitlines() if part.strip()]
    if not visible_parts:
        return [], []
    raw_start, raw_end = raw_ref["start"], raw_ref["end"]
    matched_segments = []
    for segment, doc_ref in source_refs:
        if not _route_matches(route, doc_ref):
            continue
        # AU source maps contain a field locator and can bind exact generated
        # leaf spans. Rakuten source maps additionally require containment in
        # one of the explicitly frozen item_desc[line=N] raw spans.
        if route[2] == "rakuten_html":
            if not _span(doc_ref) or not SOURCES.verify_span(store, doc_ref):
                continue
            if doc_ref["start"] > raw_start or raw_end > doc_ref["end"]:
                continue
        else:
            if _span(doc_ref):
                if not SOURCES.verify_span(store, doc_ref):
                    continue
                if doc_ref["start"] > raw_start or raw_end > doc_ref["end"]:
                    continue
            # Some frozen PREP API description refs identify the exact JSON
            # leaf but omit a substring span. The run itself is still verified
            # against that same raw leaf and its original SHA above.
        lo, hi = segment.get("start"), segment.get("end")
        if not isinstance(lo, int) or not isinstance(hi, int) or not 0 <= lo <= hi <= len(text):
            continue
        segment_text = text[lo:hi]
        if any(part in segment_text for part in visible_parts):
            matched_segments.append((lo, hi, segment_text, doc_ref))
    if not matched_segments:
        return [], ["visible_run_not_literal_in_same_bound_document_route"]

    quote_refs = []
    # The existing composition literal binder treats each rendered visible
    # line as its own quote. Apply that same rule while retaining the enclosing
    # exact raw run span and requiring a unique canonical document occurrence.
    for part in visible_parts:
        positions = {}
        for lo, hi, segment_text, doc_ref in matched_segments:
            cursor = 0
            while True:
                found = segment_text.find(part, cursor)
                if found < 0:
                    break
                start = lo + found
                positions[start] = (hi, doc_ref)
                cursor = found + 1
        if len(positions) != 1:
            continue
        start, (segment_end, doc_ref) = next(iter(positions.items()))
        quote_refs.append({"document_id": document["document_id"],
                           "document_sha256": document["document_sha256"],
                           "field": "description", "start": start,
                           "end": start + len(part), "quote": part,
                           "source_html_block_id": block["block_id"],
                           "source_raw_file": raw_ref["raw_file"],
                           "source_sha256": raw_ref["sha256"],
                           "source_locator": raw_ref["locator"],
                           "source_start": raw_start, "source_end": raw_end,
                           "source_quote": raw_ref["quote"],
                           "document_source_locator": doc_ref.get("locator"),
                           "document_source_start": doc_ref.get("start"),
                           "document_source_end": doc_ref.get("end"),
                           "document_source_quote": doc_ref.get("quote")})
    if len(quote_refs) != len(visible_parts):
        return [], ["canonical_document_literal_offset_ambiguous"]
    return quote_refs, []


def _visible_runs(block: dict[str, Any]):
    output = [("block", run) for run in block.get("support_text_runs", [])]
    snippets = context_snippets(block)
    for index, snippet in enumerate(snippets):
        for run in snippet.get("support_text_runs", []):
            output.append((f"context[{index}]", run))
    return output


def _span_key(span: dict[str, Any]) -> tuple[Any, ...]:
    locator = span.get("locator")
    return (span.get("raw_file"), span.get("sha256"),
            json.dumps(locator, ensure_ascii=False, sort_keys=True),
            span.get("start"), span.get("end"), span.get("quote"))


def _physical_span_key(span: dict[str, Any]) -> tuple[Any, ...]:
    return (span.get("raw_file"), span.get("sha256"),
            span.get("start"), span.get("end"), span.get("quote"))


def _fallback_structure_exclusion(route, block, store):
    """Conservative guard for legacy per-run restoration.

    A detached text run cannot prove its table/ancestor scope. Therefore only
    allow fallback on an anchor-free raw HTML leaf and outside extracted table
    rows. The whole-leaf anchor test is deliberately conservative: it prevents
    a malformed/implicit ancestor from hiding linked context.
    """
    if block.get("kind") == "table_row" or block.get("context", {}).get("table"):
        return "fallback_table_context_unbound"
    if block.get("links"):
        return "fallback_linked_block_context_excluded"
    raw_html, _ = _raw_route_html(store, route)
    if re.search(r"<\s*a(?:\s|>)", raw_html, flags=re.IGNORECASE):
        return "fallback_anchor_in_raw_leaf_context_excluded"
    return None


def _restore_exact_item_desc_spans(route, bound_refs, extracted, document, store,
                                   existing_ids, additions, exclusions, receipts,
                                   covered_spans):
    """Recover only source refs that are exact anchor-excluded text runs.

    Some frozen Rakuten pages have malformed outer table markup, so the HTML
    extractor's structural row spans unrelated page text. In that case this
    fallback emits the exact frozen item_desc line as one plain text block,
    and only if extract_blocks independently surfaced the same raw span as a
    support_text_run (which excludes anchor text). No row/table scope is
    inferred or carried across the split.
    """
    before = len(additions)
    seen_runs = {}
    for block in extracted:
        for run in block.get("support_text_runs", []):
            ref = run.get("source_ref", {})
            if _span(ref):
                seen_runs.setdefault(_physical_span_key(ref), []).append((run, block))
    for segment, source_ref in bound_refs:
        if not (_span(source_ref) and SOURCES.verify_span(store, source_ref)):
            continue
        key = _physical_span_key(source_ref)
        matches = seen_runs.get(key, [])
        visible_texts = {run.get("text") for run, _ in matches
                         if isinstance(run.get("text"), str) and run.get("text").strip()}
        blocked = sorted({_fallback_structure_exclusion(route, block, store)
                          for _, block in matches} - {None})
        if blocked:
            exclusions.append({"target_document_id": document["document_id"],
                               "source_route": {"raw_file": route[0], "sha256": route[1],
                                                "locator": source_ref["locator"]},
                               "source_html_block_ids": sorted({block.get("block_id") for _, block in matches}),
                               "exclusion_reason": "fallback_preserved_original_structure_exclusion",
                               "structure_exclusions": blocked})
            continue
        if len(visible_texts) != 1:
            exclusions.append({"target_document_id": document["document_id"],
                               "source_route": {"raw_file": route[0], "sha256": route[1],
                                                "locator": source_ref["locator"]},
                               "exclusion_reason": "item_desc_span_not_one_exact_anchor_excluded_visible_run"})
            continue
        visible_text = next(iter(visible_texts))
        doc_text = document.get("description", {}).get("text", "")
        start, end = segment.get("start"), segment.get("end")
        if not (isinstance(start, int) and isinstance(end, int)
                and 0 <= start <= end <= len(doc_text)
                and doc_text[start:end] == source_ref["quote"]
                and visible_text == source_ref["quote"]):
            exclusions.append({"target_document_id": document["document_id"],
                               "source_route": {"raw_file": route[0], "sha256": route[1],
                                                "locator": source_ref["locator"]},
                               "exclusion_reason": "item_desc_source_quote_not_exact_canonical_segment"})
            continue
        original_locator = source_ref["locator"]
        locator = {**original_locator, "json_path": original_locator["path"]}
        exact_ref = {**source_ref, "locator": locator,
                     "offset_unit": "unicode_code_points"}
        raw_run = {"text": visible_text, "kind": "text", "source_ref": exact_ref}
        synthetic = {"schema_version": "scoped-html-blocks-v2-anchor-excluded",
                     "block_id": "html-source-span:" + sha_bytes(json.dumps(
                         [document["document_id"], source_ref], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode()),
                     "kind": "description_text_span", "tag": "text",
                     "text": visible_text, "support_text": visible_text,
                     "support_visible_parts": [part.strip() for part in visible_text.splitlines()
                                                if part.strip()],
                     "support_text_runs": [raw_run], "context": {},
                     "source_ref": {"raw_file": route[0], "sha256": route[1],
                                    "locator": {"kind": "html_text", "encoding": route[3],
                                                "path": original_locator["path"],
                                                "json_path": original_locator["path"]},
                                    "start": source_ref["start"], "end": source_ref["end"],
                                    "quote": source_ref["quote"]}}
        quote_refs, failures, exact_sources = literal_refs(
            synthetic, route, bound_refs, document, store)
        if failures or not quote_refs:
            exclusions.append({"target_document_id": document["document_id"],
                               "source_html_block_id": synthetic["block_id"],
                               "exclusion_reason": "item_desc_span_failed_literal_document_binding",
                               "binding_failures": failures})
            continue
        synthetic["source_html_block_id"] = synthetic["block_id"]
        synthetic["block_id"] = _block_identity(synthetic, document)
        synthetic["target_document_id"] = document["document_id"]
        synthetic["target_document_sha256"] = document["document_sha256"]
        synthetic["route_kind"] = route[2]
        synthetic["description_source_locator"] = original_locator
        synthetic["description_source_refs"] = exact_sources
        synthetic["quote_refs"] = quote_refs
        synthetic["model_passage"] = render_passage(synthetic)
        synthetic["scope_status"] = "unproved"
        if synthetic["block_id"] in existing_ids:
            continue
        # If a full accepted block already carries this same exact line, the
        # smaller restoration block adds no new source coverage.
        if key in covered_spans:
            continue
        additions.append(synthetic)
        existing_ids.add(synthetic["block_id"])
        covered_spans.add(key)
        for span in _iter_spans(synthetic):
            if not SOURCES.verify_span(store, span["span"]):
                raise ValueError("restored item_desc span failed final verification")
            receipts.append({"block_id": synthetic["block_id"],
                             "target_document_id": document["document_id"],
                             "span_path": span["span_path"],
                             "raw_file": span["span"]["raw_file"],
                             "sha256": span["span"]["sha256"],
                             "locator": span["span"]["locator"],
                             "start": span["span"]["start"],
                             "end": span["span"]["end"],
                             "quote": span["span"]["quote"], "verified": True})
    return len(additions) - before


def _restore_exact_api_source_runs(route, bound_refs, extracted, document, store,
                                   existing_ids, additions, exclusions, receipts,
                                   covered_spans):
    """Add exact, anchor-excluded API field text runs when a full block cannot bind."""
    before = len(additions)
    runs = {}
    for block in extracted:
        for run in block.get("support_text_runs", []):
            ref = run.get("source_ref", {})
            if _span(ref):
                runs.setdefault(_physical_span_key(ref), (run, block))
    for physical_key, (run, source_block) in runs.items():
        if physical_key in covered_spans:
            continue
        raw_ref = run["source_ref"]
        if not SOURCES.verify_span(store, raw_ref):
            continue
        text = run.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        locator = raw_ref["locator"]
        path = locator.get("json_path") if isinstance(locator, dict) else None
        if path != route[4]:
            continue
        structural_exclusion = _fallback_structure_exclusion(route, source_block, store)
        if structural_exclusion:
            exclusions.append({"target_document_id": document["document_id"],
                               "source_html_block_id": source_block.get("block_id"),
                               "source_route": {"raw_file": route[0], "sha256": route[1],
                                                "locator": _route_locator(route)},
                               "exclusion_reason": "fallback_preserved_original_structure_exclusion",
                               "structure_exclusions": [structural_exclusion]})
            continue
        source_html_id = "html-source-run:" + sha_bytes(json.dumps(
            [document["document_id"], raw_ref], ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode())
        synthetic = {"schema_version": "scoped-html-blocks-v2-anchor-excluded",
                     "block_id": source_html_id, "kind": "description_text_span",
                     "tag": "text", "text": text, "support_text": text,
                     "support_visible_parts": [part.strip() for part in text.splitlines()
                                                if part.strip()],
                     "support_text_runs": [run], "context": {},
                     "source_ref": raw_ref}
        quote_refs, failures, exact_sources = literal_refs(
            synthetic, route, bound_refs, document, store)
        if failures or not quote_refs:
            exclusions.append({"target_document_id": document["document_id"],
                               "source_html_block_id": source_html_id,
                               "source_route": {"raw_file": route[0], "sha256": route[1],
                                                "locator": _route_locator(route)},
                               "exclusion_reason": "api_visible_run_failed_literal_document_binding",
                               "binding_failures": failures})
            continue
        synthetic["source_html_block_id"] = source_html_id
        synthetic["block_id"] = _block_identity(synthetic, document)
        synthetic["target_document_id"] = document["document_id"]
        synthetic["target_document_sha256"] = document["document_sha256"]
        synthetic["route_kind"] = route[2]
        synthetic["description_source_locator"] = _route_locator(route)
        synthetic["description_source_refs"] = exact_sources
        synthetic["quote_refs"] = quote_refs
        synthetic["model_passage"] = render_passage(synthetic)
        synthetic["scope_status"] = "unproved"
        if synthetic["block_id"] in existing_ids:
            continue
        additions.append(synthetic)
        existing_ids.add(synthetic["block_id"])
        covered_spans.add(physical_key)
        for span in _iter_spans(synthetic):
            if not SOURCES.verify_span(store, span["span"]):
                raise ValueError("restored API text run failed final verification")
            receipts.append({"block_id": synthetic["block_id"],
                             "target_document_id": document["document_id"],
                             "span_path": span["span_path"],
                             "raw_file": span["span"]["raw_file"],
                             "sha256": span["span"]["sha256"],
                             "locator": span["span"]["locator"],
                             "start": span["span"]["start"],
                             "end": span["span"]["end"],
                             "quote": span["span"]["quote"], "verified": True})
    return len(additions) - before


def literal_refs(block: dict[str, Any], route, source_refs,
                 document: dict[str, Any], store: BundleStore):
    """Bind every rendered visible run to a canonical doc and exact raw span."""
    if "support_visible_parts" not in block:
        return [], ["legacy_block_requires_rebuild"], []
    snippets = context_snippets(block)
    if any("support_visible_parts" not in snippet for snippet in snippets):
        return [], ["legacy_context_requires_rebuild"], []
    runs = _visible_runs(block)
    all_parts = list(block.get("support_visible_parts", []))
    for snippet in snippets:
        all_parts.extend(part for part in snippet.get("support_visible_parts", []))
    if not all_parts:
        return [], ["no_support_text"], []
    if not runs:
        return [], ["visible_parts_without_source_runs"], []

    quote_refs, failures, exact_sources = [], [], []
    for location, run in runs:
        refs, problems = _bind_run(block, run, route, source_refs, document, store)
        if problems:
            failures.extend(f"{location}:{problem}" for problem in problems)
            continue
        quote_refs.extend(refs)
        raw = run["source_ref"]
        exact_sources.append({k: raw[k] for k in
                              ("raw_file", "sha256", "locator", "start", "end", "quote")})
    if failures:
        return [], sorted(set(failures)), []
    # Confirm that all rendered support parts are represented by extracted
    # visible runs. This guards against accidental text added by a renderer.
    run_parts = []
    for _, run in runs:
        run_parts.extend(part.strip() for part in run.get("text", "").splitlines() if part.strip())
    if sorted(run_parts) != sorted(part.strip() for part in all_parts if part.strip()):
        return [], ["rendered_visible_parts_do_not_match_source_runs"], []
    return quote_refs, [], exact_sources


def _as_html_source_refs(value: Any, encoding: str) -> Any:
    if isinstance(value, list):
        return [_as_html_source_refs(child, encoding) for child in value]
    if not isinstance(value, dict):
        return value
    result = {key: _as_html_source_refs(child, encoding) for key, child in value.items()}
    if _SOURCE_KEYS.issubset(value) and isinstance(value.get("locator"), dict):
        result["locator"] = {"kind": "html_text", "encoding": encoding,
                             "path": "whole_document_visible_text",
                             "json_path": "whole_document_visible_text"}
    return result


def _raw_route_html(store: BundleStore, route):
    raw_file, expected, kind, encoding, path = route
    data = store.raw(raw_file, expected)
    if kind == "rakuten_html":
        return data.decode(encoding), {"raw_file": raw_file, "sha256": expected,
                                       "json_path": "whole_document_visible_text"}
    source = json.loads(data)
    leaf = SOURCES.resolve_json_path(source, path)
    if not isinstance(leaf, str):
        raise ValueError("API description route does not resolve to an HTML string: " + path)
    return leaf, {"raw_file": raw_file, "sha256": expected, "json_path": path}


def _route_locator(route):
    if route[2] == "rakuten_html":
        return {"kind": "html_text", "encoding": route[3],
                "path_family": "item_desc[line=N]"}
    return {"kind": "json_leaf", "json_path": route[4]}


def _document_routes(document):
    result = defaultdict(list)
    unsupported = []
    already_bound_whole_document_refs = 0
    for segment, ref in _source_refs(document):
        if _locator(ref) == "whole_document_visible_text":
            # This is the exact route already handled by the frozen v1/v2
            # whole-document builder. Keep its blocks and model inputs intact.
            already_bound_whole_document_refs += 1
            continue
        route = _route_key(ref)
        if route is None:
            unsupported.append({"source_ref": ref, "reason": "unrecognized_or_incomplete_typed_source_route"})
        else:
            result[route].append((segment, ref))
    return result, unsupported, already_bound_whole_document_refs


def _block_identity(block: dict[str, Any], document: dict[str, Any]) -> str:
    encoded = json.dumps([block["block_id"], document["document_id"],
                          document["document_sha256"]], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode()
    return "composition-restore-html-block:" + sha_bytes(encoded)


def _existing_identities(main_dir: Path, v2_dir: Path) -> set[str]:
    found = set()
    for path in (main_dir / "source-blocks.jsonl", v2_dir / "source-blocks.jsonl"):
        if path.exists():
            for row in rows(path):
                for key in ("block_id", "source_html_block_id"):
                    if row.get(key):
                        found.add(row[key])
    return found


def _existing_source_spans(main_dir: Path, v2_dir: Path):
    found = defaultdict(set)
    for path in (main_dir / "source-blocks.jsonl", v2_dir / "source-blocks.jsonl"):
        if not path.exists():
            continue
        for row in rows(path):
            document_id = row.get("target_document_id")
            if not document_id:
                continue
            for span in _iter_spans(row):
                found[document_id].add(_physical_span_key(span["span"]))
    return found


def restore(prepared_dir: Path, documents: list[dict[str, Any]], target_document_ids: set[str],
            existing_ids: set[str], existing_source_spans=None):
    store = BundleStore(prepared_dir)
    existing_source_spans = existing_source_spans or {}
    additions, exclusions, receipts, raw_pins = [], [], [], {}
    document_coverage = {}
    docs_by_id = {doc["document_id"]: doc for doc in documents}
    missing_docs = sorted(target_document_ids - docs_by_id.keys())
    if missing_docs:
        raise ValueError("classification targets missing from frozen PREP documents: " + ",".join(missing_docs))
    for document_id in sorted(target_document_ids):
        document = docs_by_id[document_id]
        routes, unsupported, existing_route_refs = _document_routes(document)
        doc_added, doc_excluded = 0, 0
        covered_spans = set(existing_source_spans.get(document_id, set()))
        for bad in unsupported:
            exclusions.append({"target_document_id": document_id,
                               "exclusion_reason": bad["reason"], "source_ref": bad["source_ref"]})
            doc_excluded += 1
        if not routes and not existing_route_refs and not unsupported:
            exclusions.append({"target_document_id": document_id,
                               "exclusion_reason": "no_typed_description_source_route_in_frozen_PREP"})
            doc_excluded += 1
        for route, bound_refs in sorted(routes.items()):
            for segment, ref in bound_refs:
                if _span(ref):
                    if not SOURCES.verify_span(store, ref):
                        exclusions.append({"target_document_id": document_id,
                                           "exclusion_reason": "frozen_document_source_span_failed_verification",
                                           "source_ref": ref})
                        doc_excluded += 1
                else:
                    # The frozen source route can be field-scoped without a
                    # substring anchor. Confirm raw SHA and typed string leaf.
                    try:
                        store.raw(ref["raw_file"], ref["sha256"])
                        if route[2] == "api_html":
                            raw_json = json.loads(store.raw(route[0], route[1]))
                            if not isinstance(SOURCES.resolve_json_path(raw_json, route[4]), str):
                                raise ValueError("source leaf is not a string")
                        else:
                            raise ValueError("HTML route requires a frozen source span")
                    except (KeyError, OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
                        exclusions.append({"target_document_id": document_id,
                                           "exclusion_reason": "frozen_source_route_failed_verification",
                                           "source_ref": ref, "detail": type(exc).__name__})
                        doc_excluded += 1
            try:
                html, source_identity = _raw_route_html(store, route)
                extracted = extract_blocks(html, source_identity)
                verify_blocks(html, extracted)
            except (KeyError, OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
                exclusions.append({"target_document_id": document_id,
                                   "exclusion_reason": "raw_description_route_not_extractable",
                                   "source_route": {"raw_file": route[0], "sha256": route[1],
                                                    "locator": _route_locator(route)},
                                   "detail": type(exc).__name__})
                doc_excluded += 1
                continue
            raw_pins[route[0]] = store.pin(route[0])
            route_added = 0
            for original in extracted:
                candidate = original
                if route[2] == "rakuten_html":
                    candidate = _as_html_source_refs(original, route[3])
                quote_refs, failures, exact_sources = literal_refs(
                    candidate, route, bound_refs, document, store)
                if failures or not quote_refs:
                    exclusions.append({"target_document_id": document_id,
                                       "source_route": {"raw_file": route[0], "sha256": route[1],
                                                        "locator": _route_locator(route)},
                                       "source_html_block_id": original["block_id"],
                                       "exclusion_reason": "visible_source_parts_not_fully_bound_to_same_document_route",
                                       "binding_failures": failures or ["no_literal_document_quotes"]})
                    doc_excluded += 1
                    continue
                restored = {**candidate,
                            "block_id": _block_identity(original, document),
                            "source_html_block_id": original["block_id"],
                            "target_document_id": document_id,
                            "target_document_sha256": document["document_sha256"],
                            "route_kind": route[2],
                            "description_source_locator": _route_locator(route),
                            "description_source_refs": exact_sources,
                            "quote_refs": quote_refs,
                            "model_passage": render_passage(candidate),
                            "scope_status": "unproved"}
                if route[2] == "rakuten_html":
                    field_paths = sorted({
                        ref.get("document_source_locator", {}).get("path")
                        for ref in quote_refs
                        if isinstance(ref.get("document_source_locator"), dict)
                        and ref["document_source_locator"].get("path")
                    })
                    if not field_paths:
                        exclusions.append({"target_document_id": document_id,
                                           "source_html_block_id": original["block_id"],
                                           "exclusion_reason": "original_item_desc_locator_not_retained"})
                        doc_excluded += 1
                        continue
                    restored["model_passage"] = restored["model_passage"].replace(
                        "説明フィールド：whole_document_visible_text",
                        "説明フィールド：" + ", ".join(field_paths))
                if original["block_id"] in existing_ids:
                    continue
                additions.append(restored)
                existing_ids.add(original["block_id"])
                route_added += 1
                doc_added += 1
                for span in _iter_spans(restored):
                    if not SOURCES.verify_span(store, span["span"]):
                        raise ValueError("restored source span failed final raw verification: " + span["span_path"])
                    covered_spans.add(_physical_span_key(span["span"]))
                    receipts.append({"block_id": restored["block_id"],
                                     "target_document_id": document_id,
                                     "span_path": span["span_path"],
                                     "raw_file": span["span"]["raw_file"],
                                     "sha256": span["span"]["sha256"],
                                     "locator": span["span"]["locator"],
                                     "start": span["span"]["start"],
                                     "end": span["span"]["end"],
                                     "quote": span["span"]["quote"],
                                     "verified": True})
            if route[2] == "rakuten_html":
                span_added = _restore_exact_item_desc_spans(
                    route, bound_refs, extracted, document, store, existing_ids,
                    additions, exclusions, receipts, covered_spans)
                route_added += span_added
                doc_added += span_added
            elif route[2] == "api_html":
                span_added = _restore_exact_api_source_runs(
                    route, bound_refs, extracted, document, store, existing_ids,
                    additions, exclusions, receipts, covered_spans)
                route_added += span_added
                doc_added += span_added
            if route_added == 0:
                exclusions.append({"target_document_id": document_id,
                                   "source_route": {"raw_file": route[0], "sha256": route[1],
                                                    "locator": _route_locator(route)},
                                   "exclusion_reason": "no_new_fully_bound_blocks_from_route"})
                doc_excluded += 1
        document_coverage[document_id] = {"description_source_ref_count": sum(len(v) for v in routes.values()),
                                          "total_description_source_ref_count": sum(len(v) for v in routes.values()) + existing_route_refs,
                                          "typed_route_count": len(routes),
                                          "preexisting_whole_document_source_ref_count": existing_route_refs,
                                          "new_block_count": doc_added,
                                          "exclusion_count": doc_excluded}
    return additions, exclusions, receipts, raw_pins, document_coverage


def _iter_spans(value: Any, path: str = ""):
    if isinstance(value, dict):
        required = {"raw_file", "sha256", "locator", "start", "end", "quote"}
        if required.issubset(value):
            yield {"span_path": path, "span": value}
        for key, child in value.items():
            yield from _iter_spans(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_spans(child, f"{path}[{index}]")


def build_artifact(prepared_dir: Path = DEFAULT_PREP, main_dir: Path = DEFAULT_MAIN,
                   v2_dir: Path = DEFAULT_V2, output_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    prepared_dir, main_dir, v2_dir, output_dir = map(Path, (prepared_dir, main_dir, v2_dir, output_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    sys.path.insert(0, str(HERE))
    import evaluate_mixed_gpu_sku_v1 as E
    import trial_composition_evidence_v1 as T
    manifest, _, documents, _, _, _, _ = E.load_mixed_prepared(prepared_dir)
    target_units = rows(main_dir / "classification-units.jsonl")
    if len(target_units) != 82:
        raise ValueError("expected frozen 82-unit classification input")
    targets = {unit["target_document_id"] for unit in target_units}
    if len(targets) != 37:
        raise ValueError("expected 37 unique frozen target documents")
    if prepared_dir.resolve() != T.PREP.resolve():
        # This script is pinned to the same prepared inputs as the composition
        # trial; do not silently redirect it to another source population.
        raise ValueError("prepared directory differs from frozen composition trial PREP")
    existing = _existing_identities(main_dir, v2_dir)
    blocks, exclusions, receipts, raw_pins, coverage = restore(
        prepared_dir, list(documents.values()), targets, existing,
        _existing_source_spans(main_dir, v2_dir))
    existing_counts = defaultdict(set)
    for directory in (main_dir, v2_dir):
        path = directory / "source-blocks.jsonl"
        if path.exists():
            for row in rows(path):
                if row.get("target_document_id") and row.get("block_id"):
                    existing_counts[row["target_document_id"]].add(row["block_id"])
                if row.get("target_document_id") and row.get("source_html_block_id"):
                    existing_counts[row["target_document_id"]].add(row["source_html_block_id"])
    for document_id, record in coverage.items():
        record["preexisting_composition_block_count"] = len(existing_counts[document_id])
        if record["new_block_count"]:
            record["coverage_status"] = "new_verified_source_blocks_added"
        elif record["preexisting_composition_block_count"]:
            record["coverage_status"] = "existing_blocks_preserved_no_duplicate_added"
        elif record["preexisting_whole_document_source_ref_count"]:
            record["coverage_status"] = "preexisting_whole_document_route_preserved"
        else:
            record["coverage_status"] = "no_new_or_preexisting_composition_blocks"
    unit_coverage = [{"group_id": unit["group_id"],
                      "target_document_id": unit["target_document_id"],
                      "classification_unit_kind": unit.get("kind"),
                      "document_coverage_status": coverage[unit["target_document_id"]]["coverage_status"],
                      "document_new_block_count": coverage[unit["target_document_id"]]["new_block_count"],
                      "document_preexisting_block_count": coverage[unit["target_document_id"]]["preexisting_composition_block_count"]}
                     for unit in target_units]
    output_dir.mkdir(parents=True, exist_ok=False)
    write_rows(output_dir / "source-block-additions.jsonl", blocks)
    write_rows(output_dir / "excluded-source-blocks.jsonl", exclusions)
    write_rows(output_dir / "span-receipts.jsonl", receipts)
    write_rows(output_dir / "raw-pins.jsonl", list(raw_pins.values()))
    write_rows(output_dir / "document-coverage.jsonl",
               [{"document_id": k, **v} for k, v in sorted(coverage.items())])
    write_rows(output_dir / "classification-unit-coverage.jsonl", unit_coverage)
    dependencies = [Path(__file__), HERE / "build_scoped_html_blocks_v1.py",
                    HERE / "prepare_scoped_html_trial_v1.py",
                    HERE / "claude_v9_gate/sku_gate_sources.py",
                    HERE / "evaluate_mixed_gpu_sku_v1.py",
                    HERE / "trial_composition_evidence_v1.py"]
    code_dir = output_dir / "code"
    code_dir.mkdir()
    code_hashes = {}
    for source in dependencies:
        snapshot = code_dir / source.name
        snapshot.write_bytes(source.read_bytes())
        code_hashes[str(source.resolve())] = sha_file(snapshot)
    output_names = ("source-block-additions.jsonl", "excluded-source-blocks.jsonl",
                    "span-receipts.jsonl", "raw-pins.jsonl", "document-coverage.jsonl",
                    "classification-unit-coverage.jsonl")
    artifact = {"schema_version": "sku-composition-source-restoration-v3-v2",
                "prepared_dir": str(prepared_dir.resolve()),
                "prepared_manifest_sha256": sha_file(prepared_dir / "manifest.json"),
                "documents_sha256": sha_file(prepared_dir / "documents.jsonl"),
                "raw_registry_sha256": sha_file(prepared_dir / "raw-source-registry.jsonl"),
                "classification_units_sha256": sha_file(main_dir / "classification-units.jsonl"),
                "existing_main_blocks_sha256": sha_file(main_dir / "source-blocks.jsonl"),
                "existing_v2_blocks_sha256": sha_file(v2_dir / "source-blocks.jsonl"),
                "code_sha256": code_hashes,
                "output_sha256": {name: sha_file(output_dir / name) for name in output_names},
                "classification_unit_count": len(target_units),
                "classification_unit_coverage_count": len(unit_coverage),
                "target_document_count": len(targets),
                "raw_capture_pin_count": len(raw_pins),
                "addition_block_count": len(blocks),
                "excluded_block_or_route_count": len(exclusions),
                "verified_source_span_receipt_count": len(receipts),
        "target_document_count_with_new_additions": sum(v["new_block_count"] > 0 for v in coverage.values()),
        "target_document_count_with_preexisting_whole_document_routes": sum(
            v["preexisting_whole_document_source_ref_count"] > 0 for v in coverage.values()),
                "target_document_count_with_preexisting_blocks": sum(
                    v["preexisting_composition_block_count"] > 0 for v in coverage.values()),
                "target_document_count_without_new_or_preexisting_blocks": sum(
                    v["new_block_count"] == 0 and v["preexisting_composition_block_count"] == 0
                    for v in coverage.values()),
                "scope_status": "unproved_for_all_blocks",
                "labels_read": False, "model_responses_read": False,
                "new_web_or_capture_used": False,
                "policy": {"HTML_parser": "existing extract_blocks/verify_blocks with anchor-excluded support text",
                           "route_source": "only typed routes explicitly present in frozen PREP description source_refs",
                           "raw_source": "only PREP raw-source-registry aliases; complete capture size and SHA checked",
                           "literal_binding": "every visible run must have a verified raw span, exact same typed field/source route, and a literal unique quote in a canonical document description segment",
                           "link_table_keyword_policy": "inherited unchanged; per-run fallback is excluded for table rows, linked blocks, or any raw leaf containing an anchor",
                           "applicability": "all source blocks remain unproved"},
                "prior_artifact_lifecycle": {
                    "path": str((ROOT / ".lab-output/sku-composition-source-restoration-20261013-v3").resolve()),
                    "first_observed_run_removed_by_agent": True,
                    "removed_run_recoverable_counts": {"addition_block_count": 234,
                                                       "verified_source_span_receipt_count": 4891,
                                                       "excluded_block_or_route_count": 857},
                    "removed_run_code_sha256_recovered_from_stdout": "ad611347fe181f5ba5af975ff52ee19bbb2e681d6553f31d006dfdcf900f06a4",
                    "currently_preserved_v3_counts": {"addition_block_count": 403,
                                                      "verified_source_span_receipt_count": 5398,
                                                      "excluded_block_or_route_count": 1026},
                    "note": "The first v3 artifact was deleted/replaced during an earlier run; it is not represented as an extant artifact. This final run is separate and preserves both lifecycle facts."},
                "document_coverage": coverage}
    (output_dir / "manifest.json").write_text(json.dumps(artifact, ensure_ascii=False,
                                                          indent=2, sort_keys=True) + "\n",
                                                encoding="utf-8")
    return artifact


if __name__ == "__main__":
    print(json.dumps(build_artifact(), ensure_ascii=False, sort_keys=True))
