#!/usr/bin/env python3
"""Build Rakuten HTML evidence blocks bound to frozen visible-text documents."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from build_scoped_html_blocks_v1 import extract_blocks, verify_blocks
from prepare_scoped_html_trial_v1 import literal_refs, render_passage


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _aliases(value: str) -> set[str]:
    """Normalize path separators only; do not alter source path components."""
    return {value.replace("\\", "/"), value.replace("/", "\\")}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _registry(prepared_dir: Path) -> dict[str, dict[str, Any]]:
    records = _read_jsonl(prepared_dir / "raw-source-registry.jsonl")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        for alias in [record["raw_file"], *record.get("original_aliases", [])]:
            for key in _aliases(alias):
                old = result.get(key)
                if old and (old["sha256"], old["bundle_file"]) != (record["sha256"], record["bundle_file"]):
                    raise ValueError("ambiguous source alias: " + key)
                result[key] = record
    return result


def build_rakuten_blocks(prepared_dir: Path, documents: list[dict[str, Any]]):
    """Return (blocks, exclusions, raw_files) for required frozen Rakuten docs.

    Only raw captures named by the PREP registry are opened. Each complete
    capture is byte-hash checked before decoding with the title locator's
    original encoding. Visible-text binding requires a literal exact match in
    a segment tied to the same raw SHA and whole-document locator.
    """
    prepared_dir = Path(prepared_dir)
    registry = _registry(prepared_dir)
    required: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for document in documents:
        if document.get("side") != "rakuten":
            continue
        refs = []
        for segment in document.get("description", {}).get("segments", []):
            for ref in segment.get("source_refs", []):
                locator = ref.get("locator")
                if locator == "whole_document_visible_text":
                    refs.append((ref.get("raw_file"), ref.get("sha256")))
        pairs = set(refs)
        if not pairs:
            continue
        if len(pairs) != 1:
            raise ValueError("Rakuten document has multiple whole-document sources: " + document["document_id"])
        raw_file, digest = next(iter(pairs))
        if not raw_file or not digest:
            raise ValueError("incomplete Rakuten document source reference")
        required.setdefault((raw_file, digest), []).append(document)

    blocks, exclusions, raw_files = [], [], {}
    for document in documents:
        if document.get("side") == "rakuten" and not any(document in ds for ds in required.values()):
            exclusions.append({"target_document_id": document["document_id"],
                               "exclusion_reason": "no_bound_whole_document_description_source"})
    for (raw_file, expected), source_docs in sorted(required.items()):
        rec = registry.get(raw_file) or registry.get(raw_file.replace("\\", "/"))
        if rec is None:
            raise FileNotFoundError("source alias absent from frozen registry: " + raw_file)
        if rec["sha256"] != expected:
            raise ValueError("document hash differs from registry: " + raw_file)
        capture_relative = Path(rec["bundle_file"])
        if capture_relative.is_absolute() or ".." in capture_relative.parts:
            raise ValueError("unsafe frozen capture path")
        capture = prepared_dir / capture_relative
        data = capture.read_bytes()
        if len(data) != rec["bytes"] or _sha(data) != expected:
            raise ValueError("raw capture size/SHA mismatch: " + raw_file)
        encodings = set()
        for document in source_docs:
            for ref in document.get("title", {}).get("source_refs", []):
                if (ref.get("raw_file") in _aliases(raw_file) and ref.get("sha256") == expected
                        and isinstance(ref.get("locator"), dict)):
                    encoding = ref["locator"].get("encoding")
                    if encoding:
                        encodings.add(encoding)
        if len(encodings) != 1:
            raise ValueError("Rakuten title source must identify one encoding: " + raw_file)
        encoding = next(iter(encodings))
        html = data.decode(encoding)
        raw_files[str(capture.resolve())] = expected
        source_ref = {"raw_file": raw_file, "sha256": expected,
                      "json_path": "whole_document_visible_text"}
        extracted = extract_blocks(html, source_ref)
        verify_blocks(html, extracted)
        # The extractor's leaf identity is rewritten to the flattened visible
        # document's exact source field before literal_refs binds quote spans.
        for block in extracted:
            block["source_ref"]["locator"] = {
                "kind": "html_text", "json_path": "whole_document_visible_text",
                "encoding": encoding,
            }
            for document in source_docs:
                refs, failures = literal_refs(block, document)
                identity = json.dumps([block["block_id"], document["document_id"], document["document_sha256"]], separators=(",", ":"))
                new = {**block, "source_html_block_id": block["block_id"],
                       "block_id": "composition-html-block:" + _sha(identity.encode()),
                       "target_document_id": document["document_id"],
                       "target_document_sha256": document["document_sha256"],
                       "quote_refs": refs, "model_passage": render_passage(block),
                       "scope_status": "unproved"}
                if failures or not refs:
                    exclusions.append({**new,
                        "exclusion_reason": "not_all_visible_parts_bind_same_source_document",
                        "unmatched_parts": failures})
                else:
                    blocks.append(new)
    return blocks, exclusions, raw_files
