#!/usr/bin/env python3
"""Revalidate v1 composition blocks with RawStore-compatible HTML locators.

Only span locator metadata changes. Raw quote, offsets, hashes, model passage,
document citations and block identities remain frozen from v1.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "claude_v9_gate"))

import composition_source_blocks_v1 as V1
import sku_gate_sources as SOURCES
from prepare_scoped_html_trial_v1 import literal_refs


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _alias(value: str) -> str:
    return value.replace("\\", "/")


class BundleStore(SOURCES.RawStore):
    """RawStore backed only by PREP's explicit registry entries."""

    def __init__(self, prepared_dir: Path):
        super().__init__(prepared_dir)
        self.entries: dict[str, dict[str, Any]] = {}
        for entry in _rows(prepared_dir / "raw-source-registry.jsonl"):
            for alias in [entry["raw_file"], *entry.get("original_aliases", [])]:
                key = _alias(alias)
                prior = self.entries.setdefault(key, entry)
                if prior != entry:
                    raise ValueError("conflicting raw capture alias: " + key)

    def raw(self, rel: str, expected_sha256: str | None = None) -> bytes:
        key = _alias(str(rel))
        if key not in self.entries:
            raise FileNotFoundError("raw source is absent from PREP registry: " + key)
        entry = self.entries[key]
        relative = Path(entry["bundle_file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe frozen raw capture path")
        body = (self.root / relative).read_bytes()
        digest = _sha(body)
        if len(body) != entry["bytes"] or digest != entry["sha256"]:
            raise ValueError("frozen raw capture size/SHA mismatch: " + key)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("raw source SHA differs from span: " + key)
        self._bytes[key], self._sha[key] = body, digest
        return body

    def sha(self, rel: str) -> str:
        key = _alias(str(rel))
        self.raw(key)
        return self._sha[key]


def _transform_spans(value: Any, encoding: str) -> Any:
    if isinstance(value, list):
        return [_transform_spans(child, encoding) for child in value]
    if not isinstance(value, dict):
        return value
    result = {key: _transform_spans(child, encoding) for key, child in value.items()}
    required = {"raw_file", "sha256", "locator", "start", "end", "quote"}
    if required.issubset(value):
        old_locator = value["locator"]
        dom_path = value.get("dom_path")
        if not dom_path and isinstance(old_locator, dict):
            dom_path = old_locator.get("path")
        path_hint = dom_path or "whole_document_visible_text"
        result["locator"] = {
            "kind": "html_text", "encoding": encoding,
            "path": path_hint, "json_path": "whole_document_visible_text",
        }
    return result


def _iter_spans(value: Any, path: str = ""):
    if isinstance(value, dict):
        keys = {"raw_file", "sha256", "locator", "start", "end", "quote"}
        if keys.issubset(value):
            yield path, value
        for key, child in value.items():
            yield from _iter_spans(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_spans(child, f"{path}[{index}]")


def _encoding(block: dict[str, Any]) -> str:
    locator = block.get("source_ref", {}).get("locator", {})
    encoding = locator.get("encoding") if isinstance(locator, dict) else None
    if not encoding:
        raise ValueError("v1 top-level block has no encoding")
    return encoding


def build_rakuten_blocks(prepared_dir: Path, documents: list[dict[str, Any]]):
    """v1-compatible tuple API, adding locator conversion and raw revalidation."""
    prepared_dir = Path(prepared_dir)
    source_blocks, source_exclusions, raw_files = V1.build_rakuten_blocks(prepared_dir, documents)
    store = BundleStore(prepared_dir)
    documents_by_id = {doc["document_id"]: doc for doc in documents}
    blocks, exclusions = [], []

    def revalidate(original):
        expected_model = {key: deepcopy(original.get(key)) for key in
                          ("model_passage", "quote_refs", "block_id", "source_html_block_id")}
        if not isinstance(original.get("source_ref"), dict):
            return deepcopy(original), [{"path": "source_ref", "reason": "no_raw_html_block_to_revalidate"}]
        candidate = _transform_spans(deepcopy(original), _encoding(original))
        failed = []
        for span_path, span in _iter_spans(candidate):
            try:
                if not SOURCES.verify_span(store, span):
                    failed.append({"path": span_path, "reason": "raw_span_verification_failed"})
            except (KeyError, IndexError, OSError, UnicodeDecodeError, ValueError) as exc:
                failed.append({"path": span_path, "reason": type(exc).__name__})
        document = documents_by_id.get(candidate.get("target_document_id"))
        if document is None:
            failed.append({"path": "target_document_id", "reason": "target_document_missing"})
        else:
            # v1 generated quote refs before assigning its document-bound block
            # id; preserve that original source block identity for exact replay.
            literal_candidate = deepcopy(candidate)
            literal_candidate["block_id"] = candidate.get("source_html_block_id", candidate["block_id"])
            fresh_refs, literal_failures = literal_refs(literal_candidate, document)
            if literal_failures or fresh_refs != candidate.get("quote_refs"):
                failed.append({"path": "quote_refs", "reason": "document_literal_binding_changed",
                               "unmatched_parts": literal_failures})
        if any(candidate.get(key) != value for key, value in expected_model.items()):
            failed.append({"path": "model_input", "reason": "v1_model_input_changed"})
        return candidate, failed

    for original in source_blocks:
        candidate, failed = revalidate(original)
        if failed:
            exclusions.append({**candidate, "exclusion_reason": "v2_raw_revalidation_failed",
                               "revalidation_failures": failed})
        else:
            blocks.append(candidate)
    for original in source_exclusions:
        candidate, failed = revalidate(original)
        exclusions.append({**candidate,
                           "revalidation_failures": failed,
                           "revalidation_status": "failed" if failed else "verified_but_excluded_by_v1"})
    # raw_files from v1 are absolute capture paths and remain the immutable pins.
    return blocks, exclusions, raw_files


def build_artifact(prepared_dir: Path, output_dir: Path) -> dict[str, Any]:
    prepared_dir, output_dir = Path(prepared_dir).resolve(), Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    docs = _rows(prepared_dir / "documents.jsonl")
    v1_blocks, v1_exclusions, v1_pins = V1.build_rakuten_blocks(prepared_dir, docs)
    blocks, exclusions, raw_files = build_rakuten_blocks(prepared_dir, docs)
    v1_model_records = [row for row in [*v1_blocks, *v1_exclusions]
                        if row.get("source_html_block_id")]
    v2_by_identity = {(row.get("source_html_block_id"), row.get("target_document_id")): row
                      for row in [*blocks, *exclusions] if row.get("source_html_block_id")}
    model_fields = ("model_passage", "quote_refs", "block_id", "source_html_block_id")
    model_inputs_unchanged = len(v2_by_identity) == len(v1_model_records)
    for old in v1_model_records:
        current = v2_by_identity.get((old.get("source_html_block_id"), old.get("target_document_id")))
        model_inputs_unchanged &= current is not None and all(current.get(key) == old.get(key) for key in model_fields)
    store = BundleStore(prepared_dir)
    receipts = []
    for block in blocks:
        for span_path, span in _iter_spans(block):
            if not SOURCES.verify_span(store, span):
                raise ValueError("accepted span failed final receipt verification")
            receipts.append({"block_id": block["block_id"], "target_document_id": block["target_document_id"],
                             "span_path": span_path, "raw_file": span["raw_file"], "sha256": span["sha256"],
                             "locator": span["locator"], "start": span["start"], "end": span["end"],
                             "quote": span["quote"], "verified": True})
    pins = []
    registry = BundleStore(prepared_dir).entries
    for path, digest in sorted(raw_files.items()):
        body = Path(path).read_bytes()
        if _sha(body) != digest:
            raise ValueError("raw pin changed before artifact write")
        pins.append({"absolute_path": str(Path(path).resolve()), "sha256": digest, "bytes": len(body)})
    if {str(Path(path).resolve()): digest for path, digest in v1_pins.items()} != raw_files:
        raise ValueError("v1 and v2 raw pin sets differ")

    output_dir.mkdir(parents=True, exist_ok=False)
    _write_jsonl(output_dir / "source-blocks.jsonl", blocks)
    _write_jsonl(output_dir / "span-receipts.jsonl", receipts)
    _write_jsonl(output_dir / "raw-pins.jsonl", pins)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for source in (Path(__file__), HERE / "composition_source_blocks_v1.py"):
        shutil.copyfile(source, code_dir / source.name)
    manifest = {
        "schema_version": "sku-composition-source-revalidation-v2",
        "prepared_dir": str(prepared_dir),
        "source_sha256": {str((prepared_dir / "documents.jsonl").resolve()): _sha((prepared_dir / "documents.jsonl").read_bytes()),
                          str((prepared_dir / "raw-source-registry.jsonl").resolve()): _sha((prepared_dir / "raw-source-registry.jsonl").read_bytes())},
        "source_code_sha256": {path.name: _sha(path.read_bytes()) for path in code_dir.iterdir()},
        "input_document_count": sum(doc.get("side") == "rakuten" for doc in docs),
        "raw_file_count": len(pins), "v1_block_count": len(v1_blocks), "v1_exclusion_count": len(v1_exclusions),
        "block_count": len(blocks), "excluded_count": len(exclusions), "verified_span_count": len(receipts),
        "all_scopes_unproved": all(block.get("scope_status") == "unproved" for block in blocks),
        "v1_model_inputs_unchanged": model_inputs_unchanged,
        "all_accepted_spans_verified": all(item["verified"] for item in receipts),
        "all_document_literal_refs_rechecked": True,
        "raw_pins": pins,
    }
    (output_dir / "validation.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepared_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_artifact(args.prepared_dir, args.output), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
