#!/usr/bin/env python3
"""Validate a source-only fresh-v7 whole-SKU gate preparation artifact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from prepare_fresh_dev_v7_gate_v1 import (
    DEFAULT_BASE,
    DEFAULT_SOURCE,
    DEFAULT_SOURCE_BUNDLE,
    canonical,
    filter_source,
    load_selection,
    load_source,
    read_json,
    read_rows,
    sha_file,
)

DEFAULT_ARTIFACT = DEFAULT_BASE / "full-gate-inputs-v8"


def index_rows(rows, key, label):
    out = {}
    for row in rows:
        identity = row.get(key)
        if identity in out:
            raise ValueError("duplicate " + label + " identity")
        out[identity] = row
    return out


def compare_mappings(actual, expected, label):
    if set(actual) != set(expected):
        raise ValueError(label + " population differs from original source inputs")
    for key in expected:
        if canonical(actual[key]) != canonical(expected[key]):
            raise ValueError(label + " source record changed: " + str(key))


def validate_trace_coverage(rows, expected_pairs):
    observed = [(row.get("case_id"), row.get("au_row_key")) for row in rows]
    if len(observed) != len(set(observed)) or set(observed) != set(expected_pairs):
        raise ValueError("fresh row trace does not cover the exact expected case×AU-row population")
    return len(observed)


def validate_prepared_artifact(artifact_dir: Path, source_dir: Path,
                               source_bundle_dir: Path, selection_dir: Path) -> dict:
    manifest = read_json(artifact_dir / "manifest.json")
    if (manifest.get("schema_version") != "fresh-dev-v7-whole-sku-gate-prepare-v1"
            or manifest.get("phase") != "source_only_prepare_with_v7_hints"
            or manifest.get("labels_read") is not False or manifest.get("test_split_read") is not False
            or manifest.get("adapter_model_inference_run") is not False
            or manifest.get("whole_gate_evaluation_run") is not False
            or manifest.get("whole_gate_scores_created") is not False
            or manifest.get("fresh_v7_status_is_untrusted_metadata_only") is not True
            or manifest.get("whole_value_equality_shortcut_used") is not False
            or manifest.get("normalization_core_residual_used_as_proof") is not False):
        raise ValueError("artifact manifest does not satisfy the source-only, label-blind contract")
    outputs = manifest.get("outputs_sha256")
    if not isinstance(outputs, dict) or not outputs:
        raise ValueError("artifact output hash manifest is missing")
    for name, digest in outputs.items():
        if sha_file(artifact_dir / name) != digest:
            raise ValueError("artifact output SHA differs: " + name)

    selection_plan, members = load_selection(selection_dir)
    source_manifest, source_hashes, cases, source_rows, source_tasks, source_requests, source_documents, original_cases, source_case_line_sha, source_case_line_number, source_case_file_sha, root_source_manifest = load_source(source_dir, source_bundle_dir)
    for member in members:
        cid = member["case_id"]
        case = cases[cid]
        version = case["source_version"]
        source_path = f".lab-output/sku-independent-real-20261011/eval-{version}/run-token/inputs/cases.jsonl"
        if (member.get("source_case_line_sha256") != source_case_line_sha[cid]
                or member.get("source_case_input_sha256") != source_manifest["source_inputs"].get(source_path)
                or member.get("selected_rakuten_source_sha256") != case.get("raw_selected_sku_source_sha256")):
            raise ValueError("membership source case/input/SKU hash binding differs")
    selected_cases, selected_rows, selected_tasks, selected_requests, selected_documents = filter_source(
        members, cases, source_rows, source_tasks, source_requests, source_documents)

    if manifest.get("full_dev_source_manifest_sha256") != sha_file(source_dir / "manifest.json"):
        raise ValueError("source DEV manifest binding differs")
    if manifest.get("full_condition_root_source_manifest_sha256") != sha_file(source_dir.parent / "manifest.json"):
        raise ValueError("full-condition root source manifest binding differs")
    if manifest.get("source_span_count_verified_in_full_prep") != root_source_manifest.get("source_span_count_verified"):
        raise ValueError("full-condition source span verification receipt differs")
    expected_counts = {"selected_case_count": len(selected_cases), "selected_row_count": len(selected_rows),
                       "selected_task_count": len(selected_tasks), "selected_request_count": len(selected_requests),
                       "selected_document_count": len(selected_documents)}
    for field, count in expected_counts.items():
        if manifest.get(field) != count:
            raise ValueError("artifact " + field + " differs from original source coverage")

    compare_mappings(index_rows(read_rows(artifact_dir / "case-plans.jsonl"), "case_id", "artifact case"),
                     selected_cases, "case-plan")
    artifact_row_map = {}
    for row in read_rows(artifact_dir / "row-plans.jsonl"):
        key = (row.get("case_id"), row.get("au_row_key"))
        if key in artifact_row_map:
            raise ValueError("duplicate artifact case×AU-row identity")
        artifact_row_map[key] = row
    expected_row_map = {(row["case_id"], row["au_row_key"]): row for row in selected_rows}
    compare_mappings(artifact_row_map, expected_row_map, "row-plan")
    compare_mappings(index_rows(read_rows(artifact_dir / "tasks.jsonl"), "task_id", "artifact task"),
                     selected_tasks, "whole-condition task")
    compare_mappings(index_rows(read_rows(artifact_dir / "requests.jsonl"), "request_id", "artifact request"),
                     selected_requests, "whole-condition request")
    compare_mappings(index_rows(read_rows(artifact_dir / "documents.jsonl"), "document_id", "artifact document"),
                     selected_documents, "target document")

    trace_rows = read_rows(artifact_dir / "fresh-v7-row-trace.jsonl")
    selected_pairs = {(row["case_id"], row["au_row_key"]) for row in selected_rows}
    full_dev_pairs = {(row["case_id"], row["au_row_key"]) for row in source_rows}
    if validate_trace_coverage(trace_rows, full_dev_pairs) != len(source_rows):
        raise ValueError("fresh row trace does not cover every original full-DEV case×AU-row identity")
    if not selected_pairs.issubset({(row["case_id"], row["au_row_key"]) for row in trace_rows}):
        raise ValueError("fresh row trace omits a selected original AU row")
    if any(row.get("producer_row_state_is_untrusted") is not True
           or row.get("heuristic_status_is_untrusted_hint") is not True
           or not isinstance(row.get("pending_description_check_candidate"), bool)
           for row in trace_rows):
        raise ValueError("fresh per-row states or candidate flags are not marked untrusted")
    decisions = index_rows(read_rows(artifact_dir / "fresh-v7-decisions.jsonl"), "case_id", "fresh decision hint")
    if set(decisions) != set(selected_cases) or any(row.get("producer_decision_is_untrusted") is not True or row.get("whole_gate_decision") is not None for row in decisions.values()):
        raise ValueError("fresh decisions are not exact selected untrusted hints")
    hints = read_rows(artifact_dir / "fresh-v7-task-hints.jsonl")
    hints_by_task = index_rows(hints, "task_id", "fresh task hint")
    if set(hints_by_task) != set(selected_tasks):
        raise ValueError("fresh hint joins do not cover each original task exactly once")
    for task_id, hint in hints_by_task.items():
        source = selected_tasks[task_id]
        if ((hint.get("case_id"), hint.get("au_row_key")) != (source["case_id"], source["au_row_key"])
                or hint.get("producer_status_is_proof") is not False
                or hint.get("automatic_conflict_allowed") is not False
                or hint.get("automatic_support_allowed") is not False
                or hint.get("core_residual_are_untrusted_hints") is not True):
            raise ValueError("fresh hint join changes source identity or weakens untrusted status")
    return {"selected_case_count": len(selected_cases), "selected_product_count": len({str(x["au_product_id"]) for x in selected_cases.values()}),
            "selected_au_row_count": len(selected_rows), "selected_bidirectional_task_count": len(selected_tasks),
            "selected_request_count": len(selected_requests), "selected_document_count": len(selected_documents),
            "full_dev_trace_row_count": len(trace_rows), "selected_trace_row_count": len(selected_pairs), "decision_hint_count": len(decisions),
            "task_hint_join_count": len(hints), "labels_read": False, "whole_gate_evaluation_run": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT)
    ap.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--source-bundle-dir", type=Path, default=DEFAULT_SOURCE_BUNDLE)
    ap.add_argument("--selection-dir", type=Path, default=DEFAULT_BASE)
    args = ap.parse_args(argv)
    result = validate_prepared_artifact(args.artifact_dir.resolve(), args.source_dir.resolve(),
                                        args.source_bundle_dir.resolve(), args.selection_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
