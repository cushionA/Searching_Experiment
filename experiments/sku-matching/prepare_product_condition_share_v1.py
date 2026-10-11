#!/usr/bin/env python3
"""Prepare a user-asserted product-level condition sharing contract.

This tool performs provenance checks and structural counts only. It never reads
labels, calls a model, or changes the prepared request/task records.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-product-condition-share-20261011-v1"
PREP_FILES = ("requests.jsonl", "documents.jsonl", "tasks.jsonl")
NOVEL_FILE = "upstream_conditions.jsonl"
STATEMENT = "AU product non-asymmetric conditions are fixed across size/color; sold-out SKU rows excluded upstream."


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"expected object at {path}:{number}")
            rows.append(row)
    return rows


def verify_manifest_file(directory: Path, manifest: dict[str, Any], name: str) -> str:
    expected = manifest.get("output_sha256", {}).get(name)
    actual = sha_file(directory / name)
    if not isinstance(expected, str) or expected != actual:
        raise ValueError(f"{name} SHA differs from prepared manifest in {directory}")
    return actual


def verify_prepared(directory: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, str]]:
    directory = directory.resolve()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = read_json(manifest_path)
    if not isinstance(manifest.get("schema_version"), str) or not manifest["schema_version"]:
        raise ValueError(f"prepared manifest has no schema_version: {directory}")
    rows: dict[str, list[dict[str, Any]]] = {}
    hashes = {"manifest.json": sha_file(manifest_path)}
    for name in PREP_FILES:
        hashes[name] = verify_manifest_file(directory, manifest, name)
        rows[name] = read_jsonl(directory / name)
    return manifest, rows, hashes


def index_unique(rows: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"missing {key} in {label}")
        if value in result:
            raise ValueError(f"duplicate {key} in {label}: {value}")
        result[value] = row
    return result


def novel_condition_index(path: Path, manifest: dict[str, Any]) -> tuple[dict[tuple[Any, ...], list[tuple[int, dict[str, Any], str]]], str]:
    path = path.resolve()
    actual = sha_file(path)
    expected = manifest.get("output_sha256", {}).get(NOVEL_FILE)
    if not isinstance(expected, str) or expected != actual:
        raise ValueError("upstream_conditions.jsonl SHA differs from novel prepared manifest")
    index: dict[tuple[Any, ...], list[tuple[int, dict[str, Any], str]]] = defaultdict(list)
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            condition = json.loads(line)
            if not isinstance(condition, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            key = (condition.get("case_id"), condition.get("row_key"), condition.get("axis_key"), condition.get("value"))
            index[key].append((line_number, condition, sha_bytes(line.rstrip("\n").encode("utf-8"))))
    return index, actual


def _condition_key(task: dict[str, Any]) -> tuple[Any, ...]:
    raw = task.get("raw_condition")
    if not isinstance(raw, dict) or not isinstance(raw.get("value"), str):
        raise ValueError(f"upstream_residual task lacks raw condition value: {task.get('task_id')}")
    return (task.get("case_id"), task.get("au_row_key"), task.get("axis_key"), raw["value"])


def _eligible_task_proof(task: dict[str, Any], novel_index, novel_path: Path | None) -> dict[str, Any] | None:
    origin = task.get("origin")
    if origin == "one_sided":
        return {"task_id": task["task_id"], "origin": origin,
                "source_ref": task.get("source_ref"), "proof": "prepared task origin one_sided"}
    if origin != "upstream_residual" or novel_index is None or novel_path is None:
        return None
    matches = novel_index.get(_condition_key(task), [])
    valid = [(line, row, digest) for line, row, digest in matches
             if row.get("kind") == "rakuten" and row.get("status") == "one_sided" and row.get("au_axis") is None]
    if len(matches) != 1 or len(valid) != 1:
        return None
    line, condition, digest = valid[0]
    return {"task_id": task["task_id"], "origin": origin, "source_ref": task.get("source_ref"),
            "proof": "exact upstream condition: rakuten / one_sided / au_axis null",
            "condition_ref": {"path": str(novel_path.resolve()), "line": line},
            "condition_sha256": digest}


def prepare(prepared_dir: Path, output_dir: Path, novel_prepared_dir: Path | None = None) -> dict[str, Any]:
    manifest, files, source_hashes = verify_prepared(prepared_dir)
    requests = index_unique(files["requests.jsonl"], "request_id", "requests")
    documents = index_unique(files["documents.jsonl"], "document_id", "documents")
    tasks = index_unique(files["tasks.jsonl"], "task_id", "tasks")
    novel_index = None
    novel_manifest = None
    novel_hashes = {}
    novel_path = None
    if novel_prepared_dir is not None:
        novel_manifest_path = novel_prepared_dir.resolve() / "manifest.json"
        novel_manifest = read_json(novel_manifest_path)
        if not isinstance(novel_manifest.get("schema_version"), str) or not novel_manifest["schema_version"]:
            raise ValueError("novel prepared manifest has no schema_version")
        novel_path = novel_prepared_dir.resolve() / NOVEL_FILE
        novel_index, novel_hash = novel_condition_index(novel_path, novel_manifest)
        novel_hashes = {"manifest.json": sha_file(novel_manifest_path), NOVEL_FILE: novel_hash}

    forward_requests = {rid: row for rid, row in requests.items() if row.get("direction") == "rakuten_to_au"}
    task_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    forward_task_ids_all: set[str] = set()
    for task in tasks.values():
        rid = task.get("request_id")
        if rid not in requests:
            raise ValueError(f"task references unknown request_id: {rid}")
        task_by_request[rid].append(task)
        if task.get("direction") == "rakuten_to_au":
            forward_task_ids_all.add(task["task_id"])
    eligible: dict[str, list[dict[str, Any]]] = {}
    for rid, request in forward_requests.items():
        bound = task_by_request.get(rid, [])
        if not bound:
            continue
        proofs = [_eligible_task_proof(task, novel_index, novel_path) for task in bound]
        # Every bound task must independently prove the same allowed source class.
        if all(proof is not None for proof in proofs):
            origins = {task.get("origin") for task in bound}
            if len(origins) == 1:
                eligible[rid] = [proof for proof in proofs if proof is not None]

    old_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    new_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    eligible_product_ids: set[str] = set()
    for rid in eligible:
        request = requests[rid]
        document_id = request.get("target_document_id")
        document = documents.get(document_id)
        if document is None:
            raise ValueError(f"request references unknown document_id: {document_id}")
        if request.get("target_document_sha256") != document.get("document_sha256"):
            raise ValueError(f"request/document SHA mismatch: {rid}")
        options = request.get("option_values")
        if not isinstance(options, list) or not all(isinstance(value, str) for value in options):
            raise ValueError(f"request option_values must be ordered strings: {rid}")
        axis = request.get("axis_name")
        if not isinstance(axis, str):
            raise ValueError(f"request axis_name missing: {rid}")
        doc_sha = request["target_document_sha256"]
        old_key = (document_id, doc_sha, axis, tuple(options), request.get("target_selected_axes_text"))
        new_key = (document_id, doc_sha, axis, tuple(options))
        old_groups[old_key].append(rid)
        new_groups[new_key].append(rid)
        product_id = document.get("product_id")
        if product_id is not None:
            eligible_product_ids.add(str(product_id))

    option_pairs_old = sum(len(key[3]) for key in old_groups)
    option_pairs_new = sum(len(key[3]) for key in new_groups)
    eligible_task_ids = {proof["task_id"] for group in eligible.values() for proof in group
                         if tasks[proof["task_id"]].get("direction") == "rakuten_to_au"}
    forward_task_ids = forward_task_ids_all
    reverse_request_count = sum(row.get("direction") != "rakuten_to_au" for row in requests.values())
    summary = {
        "eligible_forward_request_records": len(eligible),
        "eligible_forward_task_fanout": len(eligible_task_ids),
        "noneligible_forward_request_records": len(forward_requests) - len(eligible),
        "noneligible_forward_task_fanout": len(forward_task_ids - eligible_task_ids),
        "reverse_request_records_passthrough": reverse_request_count,
        "old_exact_input_groups": len(old_groups),
        "product_level_groups": len(new_groups),
        "distinct_product_ids": len(eligible_product_ids),
        "option_label_pairs_old": option_pairs_old,
        "option_label_pairs_product_level": option_pairs_new,
        "jev_labels_with_one_unknown_old": option_pairs_old + len(old_groups),
        "jev_labels_with_one_unknown_product_level": option_pairs_new + len(new_groups),
    }
    scope = {"prepared_dir": str(prepared_dir.resolve()), "schema_version": manifest["schema_version"],
             "novel_prepared_dir": str(novel_prepared_dir.resolve()) if novel_prepared_dir else None,
             "novel_schema_version": novel_manifest.get("schema_version") if novel_manifest else None}
    contract = {
        "basis": "user_asserted",
        "statement": STATEMENT,
        "eligible_forward_request_ids": sorted(eligible),
        "sold_out_rows_excluded_upstream": True,
        "source_proof": [{"request_id": rid, "task_proofs": eligible[rid]} for rid in sorted(eligible)],
        "source_sha256": {"prepared": source_hashes, "novel_prepared": novel_hashes},
        "scope": scope,
        "structural_savings": summary,
        "pass_through": {"reverse_request_ids": sorted(rid for rid, row in requests.items() if row.get("direction") != "rakuten_to_au"),
                         "noneligible_forward_request_ids": sorted(set(forward_requests) - set(eligible))},
    }
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "contract.json").open("x", encoding="utf-8") as stream:
        json.dump(contract, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--novel-prepared-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = prepare(args.prepared_dir, args.output_dir, args.novel_prepared_dir)
    print(json.dumps({"output": str(args.output_dir.resolve()), **result["structural_savings"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
