#!/usr/bin/env python3
"""Prepare a blind, full-condition SKU gate for the frozen 40-case test subset.

This module only hydrates original source bindings and assembles gate inputs.
It never loads labels, predictions, model weights, or runs inference. Fresh
baseline and composition artifacts are consumed by separate, later phases.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/inputs"
DEFAULT_BINDINGS = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/binding-only"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/gate-prepared-v2"
V2_TEST = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/test"


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path: Path, values):
    with path.open("xb") as f:
        for value in values:
            f.write(canonical(value) + b"\n")


def build_gate_rows(case_plans, row_plans, tasks, requests, bindings):
    """Hydrate source task bindings and enforce complete original row scope."""
    task_by_id = {row["task_id"]: row for row in tasks}
    if len(task_by_id) != len(tasks):
        raise ValueError("duplicate task IDs")
    binding_by_task = {row["task_id"]: row for row in bindings}
    if len(binding_by_task) != len(bindings) or set(binding_by_task) != set(task_by_id):
        raise ValueError("missing or extra task source bindings")
    request_by_id = {row["request_id"]: row for row in requests}
    if len(request_by_id) != len(requests):
        raise ValueError("duplicate hydrated request IDs")
    if {task["request_id"] for task in tasks} - set(request_by_id):
        raise ValueError("task has no hydrated request")

    full_tasks = []
    by_request = defaultdict(list)
    for task in tasks:
        binding = binding_by_task[task["task_id"]]
        if (binding["request_id"] != task["request_id"]
                or binding["raw_condition_ref"] != task["raw_condition_ref"]
                or binding["direction"] != task["direction"]):
            raise ValueError("task binding identity/provenance mismatch")
        req = request_by_id[task["request_id"]]
        if (binding["raw_condition"].get("value") != req["selected_value"]
                or binding["option_values"] != req["option_values"]
                or binding["raw_condition"].get("axis_name") != req["axis_name"]):
            raise ValueError("task condition and full request disagree")
        hydrated = {**task, "raw_condition": binding["raw_condition"],
                    "option_values": binding["option_values"],
                    "source_condition_provenance": binding["source_provenance"],
                    "source_binding_role": binding["binding_role"]}
        full_tasks.append(hydrated)
        by_request[task["request_id"]].append(hydrated)

    full_requests = []
    for rid, original in request_by_id.items():
        bound = by_request.get(rid, [])
        if not bound:
            raise ValueError("hydrated request has no source task binding")
        values = {(t["raw_condition"]["value"], tuple(t["option_values"]), t["direction"])
                  for t in bound}
        if len(values) != 1:
            raise ValueError("one request maps to inconsistent raw condition bindings")
        full_requests.append({**original,
                              "source_task_bindings": [
                                  {"task_id": t["task_id"], "case_id": t["case_id"],
                                   "au_row_key": t["au_row_key"], "direction": t["direction"],
                                   "axis_key": t["axis_key"], "raw_condition_ref": t["raw_condition_ref"],
                                   "source_condition_provenance": t["source_condition_provenance"]}
                                  for t in bound],
                              "source_binding_only": True,
                              "excluded_from_model_payload": True})

    full_rows = []
    row_keys_by_case = defaultdict(list)
    for row in row_plans:
        source_bindings_for_row = row.get("bindings")
        if not source_bindings_for_row:
            raise ValueError("original AU row has no task bindings")
        all_bindings, forward_axes, reverse_axes = [], set(), set()
        for binding in source_bindings_for_row:
            tid, rid = binding.get("task_id"), binding.get("request_id")
            if tid not in task_by_id or rid not in request_by_id:
                raise ValueError("row binding references a missing task/request")
            task = task_by_id[tid]
            if task["request_id"] != rid or (task["case_id"], task["au_row_key"]) != (row["case_id"], row["au_row_key"]):
                raise ValueError("row binding does not match original task case/row")
            if task["direction"] == "rakuten_to_au":
                forward_axes.add(task["axis_key"])
            elif task["direction"] == "au_to_rakuten":
                reverse_axes.add(task["axis_key"])
            else:
                raise ValueError("unsupported condition direction")
            all_bindings.append({**binding, "kind": "model",
                                 "source_binding_provenance": binding_by_task[tid]["source_provenance"]})
        if len(forward_axes) != row["rakuten_axis_count"] or len(reverse_axes) != row["au_axis_count"]:
            raise ValueError("forward Rakuten/reverse AU axis counts do not match the complete original axes")
        full_rows.append({**row, "condition_bindings": all_bindings,
                          "upstream_state": "unmatched_fresh_baseline_pending",
                          "all_original_axes_covered": True,
                          "rakuten_forward_axis_count_verified": len(forward_axes),
                          "au_reverse_axis_count_verified": len(reverse_axes),
                          "production_adoption_allowed": False})
        row_keys_by_case[row["case_id"]].append(row["au_row_key"])

    full_cases = []
    case_ids = {row["case_id"] for row in case_plans}
    if case_ids != set(row_keys_by_case):
        raise ValueError("gate cases and complete source row plans differ")
    for case in case_plans:
        case_rows = row_keys_by_case[case["case_id"]]
        if (len(case_rows) != case["total_au_rows"]
                or len(set(case_rows)) != len(case_rows)
                or set(case_rows) != set(case["row_keys"])):
            raise ValueError("gate candidate rows do not cover every original AU row")
        # This is a declared unmatched baseline placeholder, never a replay of
        # the old 83-case predictions or a statement about this test case.
        full_cases.append({
            "case_id": case["case_id"], "dossier_id": case["dossier_id"],
            "cohort": "final_independent_test_subset",
            "au_product_id": case["au_product_id"], "source_sku_key": case["source_sku_key"],
            "candidate_row_keys": list(case_rows), "total_fixed_au_row_count": case["total_au_rows"],
            "upstream_contradiction_row_count": 0, "unresolved_omitted_row_count": 0,
            "all_pending_rows_received": True,
            "upstream_decision": {"decision": "unmatched", "reason": "fresh_baseline_not_yet_run",
                                  "top_row_key": None, "not_reused_old_predictions": True},
            "production_adoption_allowed": False})
    return full_requests, full_tasks, full_rows, full_cases


def prepare(input_dir: Path, binding_dir: Path, output_dir: Path):
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite " + str(output_dir))
    in_manifest = read_json(input_dir / "manifest.json")
    binding_manifest = read_json(binding_dir / "manifest.json")
    membership_sha = in_manifest["membership_sha256"]
    if (in_manifest.get("independent_test") is not True or in_manifest.get("labels_read") is not False
            or in_manifest.get("predictions_read") is not False or in_manifest.get("model_inference_run") is not False
            or binding_manifest.get("membership_sha256") != membership_sha
            or binding_manifest.get("model_payload_included") is not False):
        raise ValueError("test preparation/binding scope is not label-blind or membership-bound")
    for name, expected in in_manifest["output_sha256"].items():
        if sha_file(input_dir / name) != expected:
            raise ValueError("subset input hash differs: " + name)
    tasks = read_rows(input_dir / "tasks.jsonl")
    source_bindings = read_rows(binding_dir / "task-condition-bindings.jsonl")
    if sha_file(binding_dir / "task-condition-bindings.jsonl") != binding_manifest["task_bindings_sha256"]:
        raise ValueError("binding sidecar SHA differs")
    source_req = {x["request_id"]: x for x in read_rows(V2_TEST / "requests.jsonl")}
    request_map = read_rows(input_dir / "request-model-input-map.jsonl")
    selected_request_ids = {x["request_id"] for x in request_map}
    if not selected_request_ids <= set(source_req):
        raise ValueError("frozen subset request map includes a foreign request")
    requests = [source_req[rid] for rid in sorted(selected_request_ids)]
    case_plans = read_rows(input_dir / "case-plans.jsonl")
    row_plans = read_rows(input_dir / "row-plans.jsonl")
    full_requests, full_tasks, full_rows, full_cases = build_gate_rows(
        case_plans, row_plans, tasks, requests, source_bindings)
    documents = read_rows(input_dir / "documents.jsonl")
    document_ids = {x["document_id"] for x in documents}
    if any(req["target_document_id"] not in document_ids for req in full_requests):
        raise ValueError("hydrated request document is missing")
    if len(full_requests) != len(selected_request_ids) or len(full_tasks) != len(tasks):
        raise ValueError("assembled gate request/task count mismatch")

    output_dir.mkdir(parents=True)
    files = {
        "documents.jsonl": documents, "requests.jsonl": full_requests,
        "tasks.jsonl": full_tasks, "row_plans.jsonl": full_rows,
        "case_plans.jsonl": full_cases,
        "model-request-map.jsonl": request_map,
        "baseline-model-inputs.jsonl": read_rows(input_dir / "baseline-model-inputs.jsonl"),
    }
    for name, values in files.items():
        write_rows(output_dir / name, values)
    out_hashes = {name: sha_file(output_dir / name) for name in files}
    src_manifest = read_json(V2_TEST / "manifest.json")
    manifest = {
        "schema_version": "final-full-sku-gate-preparation-v1", "phase": "prepared_before_inference",
        "upstream_head": "frozen-independent-test-membership:" + membership_sha,
        "evaluation_scope": "primary_cpu_40case_full_fresh_baseline_and_composition",
        "membership_sha256": membership_sha, "input_manifest_sha256": sha_file(input_dir / "manifest.json"),
        "binding_manifest_sha256": sha_file(binding_dir / "manifest.json"),
        "v2_test_manifest_sha256": sha_file(V2_TEST / "manifest.json"),
        "all_v2_source_spans_verified_count": src_manifest.get("source_span_count_verified"),
        "case_count": len(full_cases), "product_count": in_manifest["product_count"],
        "row_count": len(full_rows), "task_count": len(full_tasks), "request_count": len(full_requests),
        "document_count": len(documents),
        "condition_binding_kind_counts": dict(Counter(b["kind"] for row in full_rows for b in row["condition_bindings"])),
        "forward_rakuten_axis_count": sum(row["rakuten_forward_axis_count_verified"] for row in full_rows),
        "reverse_au_axis_count": sum(row["au_reverse_axis_count_verified"] for row in full_rows),
        "all_original_rows_retained": True, "all_original_axes_covered": True,
        "candidate_rows_equal_all_original_au_rows": True,
        "upstream_decision_contract": "unmatched placeholder only; no old 83-case baseline or prior predictions reused",
        "upstream_contradictions": 0, "unresolved_omitted_rows": 0,
        "all_pending_rows_received": True, "unverified_shortcuts": "disabled",
        "baseline_predictions_expected": "fresh inference for every selected request; not yet present",
        "composition_expected": "source-only exact per-request context with paired cache fanout; no inference yet",
        "labels_read": False, "predictions_read": False, "model_inference_run": False,
        "gates_frozen": False, "production_adoption_allowed": False,
        "output_sha256": out_hashes,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--binding-dir", type=Path, default=DEFAULT_BINDINGS)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)
    m = prepare(args.input_dir, args.binding_dir, args.output_dir)
    print(json.dumps({k: m[k] for k in ("schema_version", "evaluation_scope", "case_count", "product_count",
                                       "row_count", "task_count", "request_count", "forward_rakuten_axis_count",
                                       "reverse_au_axis_count", "all_original_rows_retained", "labels_read",
                                       "predictions_read", "model_inference_run", "gates_frozen")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
