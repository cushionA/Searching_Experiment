#!/usr/bin/env python3
"""Fail-closed source preparation retaining explicitly unknown volume tasks.

The frozen source/body/proof semantics remain in their existing modules. This
wrapper relaxes only the all-receipts-ready input precondition for audit rows
whose request already has an empty option vector and explicit unknown status.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BRIDGE_PATH = HERE / "prepare_indexed_volume_source_v1.py"


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_bridge():
    spec = importlib.util.spec_from_file_location("volume_source_bridge_for_partial", BRIDGE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load volume source bridge")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_source_records(rows: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]) -> dict[str, Any]:
    """Preserve frozen record guards, accepting only explicit empty-family unknowns."""
    names = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl",
             "case-plans.jsonl", "task-request-family-audit.jsonl", "membership.jsonl")
    for name in names:
        if name not in rows:
            raise ValueError("source readiness input missing: " + name)
    expected_counts = {"documents.jsonl": manifest["document_count"],
        "requests.jsonl": manifest["derived_request_count"], "tasks.jsonl": manifest["task_count"],
        "row-plans.jsonl": manifest["row_count"], "case-plans.jsonl": manifest["selected_case_count"],
        "task-request-family-audit.jsonl": manifest["task_count"],
        "membership.jsonl": manifest["selected_case_count"]}
    if any(len(rows[name]) != count for name, count in expected_counts.items()):
        raise ValueError("frozen TEST source artifact count mismatch")
    tasks = rows["tasks.jsonl"]
    task_ids = [task.get("task_id") for task in tasks]
    if len(set(task_ids)) != len(tasks) or any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
        raise ValueError("duplicate or missing original source task identity")
    request_list = rows["requests.jsonl"]
    request_ids = [request.get("request_id") for request in request_list]
    if len(set(request_ids)) != len(request_list) or any(not isinstance(rid, str) or not rid for rid in request_ids):
        raise ValueError("duplicate or missing source request identity")
    request_by_id = {request["request_id"]: request for request in request_list}
    if any(task.get("request_id") not in request_by_id for task in tasks):
        raise ValueError("original task references absent source-family request")
    audit_rows = rows["task-request-family-audit.jsonl"]
    audit_ids = [audit.get("task_id") for audit in audit_rows]
    if len(set(audit_ids)) != len(audit_rows) or set(audit_ids) != set(task_ids):
        raise ValueError("source-family receipts do not cover every original task exactly once")
    task_by_id = {task["task_id"]: task for task in tasks}
    unknown_ids: list[str] = []
    verified_count = 0
    for receipt in audit_rows:
        task_id = receipt["task_id"]
        task = task_by_id[task_id]
        request = request_by_id[task["request_id"]]
        if receipt.get("derived_request_id") != task.get("request_id"):
            raise ValueError("source-family receipt does not bind the task request")
        if receipt.get("status") == "verified_complete_ordered_native_family":
            family = receipt.get("raw_family")
            members = receipt.get("member_spans")
            values = family.get("ordered_native_family") if isinstance(family, dict) else None
            if (not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values)
                    or len(values) != len(set(values)) or not isinstance(members, list) or len(members) != len(values)
                    or any(not isinstance(member, dict) or member.get("index") != i
                           or member.get("value") != values[i] or not isinstance(member.get("span"), dict)
                           for i, member in enumerate(members))
                    or request.get("option_values") != values
                    or request.get("source_family_receipt") != family
                    or request.get("source_family_status") == "unknown"):
                raise ValueError("verified source-family receipt is incomplete or differs from its request")
            verified_count += 1
            continue
        if receipt.get("status") != "unknown":
            raise ValueError("unsupported source-family receipt status")
        # Unknown is admissible only when the cohort builder already emitted an
        # explicit empty-family request. No vector or receipt is manufactured.
        if (not isinstance(receipt.get("reason"), str) or not receipt["reason"]
                or receipt.get("raw_family") is not None or receipt.get("member_spans") is not None
                or request.get("source_family_status") != "unknown"
                or request.get("option_values") != []
                or request.get("source_family_receipt") is not None
                or request.get("model_input_id") is not None
                or request.get("production_adoption_allowed") is not False):
            raise ValueError("unknown receipt lacks an explicitly empty, unbound unknown request")
        unknown_ids.append(task_id)
    # Membership and case identity remain exact; no case/task is removed to make
    # preparation succeed.
    cases = rows["case-plans.jsonl"]
    membership = rows["membership.jsonl"]
    case_pairs = [(str(case.get("case_id")), str(case.get("au_product_id"))) for case in cases]
    member_pairs = [(str(member.get("case_id")), str(member.get("au_product_id"))) for member in membership]
    if (len(set(case_pairs)) != len(case_pairs) or len(set(member_pairs)) != len(member_pairs)
            or set(case_pairs) != set(member_pairs)
            or len({x[0] for x in case_pairs}) != manifest["selected_case_count"]
            or len({x[1] for x in case_pairs}) != manifest["selected_product_count"]):
        raise ValueError("source case plans differ from complete frozen identity membership")
    # Ensure an empty-family request has no runnable request/model-input map.
    model_map = rows.get("request-model-input-map.jsonl", [])
    mapped_requests = {item.get("request_id") for item in model_map}
    baseline_inputs = rows.get("baseline-model-inputs.jsonl", [])
    input_ids = {item.get("model_input_id") for item in baseline_inputs}
    if any(request_by_id[task_by_id[task_id]["request_id"]].get("request_id") in mapped_requests
           or request_by_id[task_by_id[task_id]["request_id"]].get("model_input_id") in input_ids
           for task_id in unknown_ids):
        raise ValueError("unknown source task is still bound to a model input")
    return {"verified_task_ids": sorted(task_id for task_id in task_ids if task_id not in set(unknown_ids)),
            "unknown_task_ids": sorted(unknown_ids), "verified_count": verified_count,
            "unknown_count": len(unknown_ids), "task_count": len(tasks),
            "unknown_task_ids_sha256": digest(sorted(unknown_ids)),
            "unknown_receipt_ledger_sha256": digest(sorted(
                (receipt for receipt in audit_rows if receipt.get("status") == "unknown"),
                key=lambda receipt: receipt["task_id"]))}


def validate_contextless_unknowns(unknown_ids: list[str], validation_bindings: list[dict[str, Any]],
                                  pairs: list[dict[str, Any]]) -> None:
    bindings_by_id: dict[str, list[dict[str, Any]]] = {}
    for binding in validation_bindings:
        task_id = (binding.get("task_binding") or {}).get("task_id")
        bindings_by_id.setdefault(task_id, []).append(binding)
    pair_ids = {pair.get("pair_id") for pair in pairs}
    for task_id in unknown_ids:
        matches = bindings_by_id.get(task_id, [])
        if (len(matches) != 1 or matches[0].get("pair_id") is not None
                or matches[0].get("preparation_status") != "unknown_invalid_source_context"
                or not isinstance(matches[0].get("preparation_errors"), list)
                or not matches[0]["preparation_errors"]):
            raise ValueError("unknown source task was not retained as one contextless binding")
        if matches[0].get("pair_id") in pair_ids:
            raise ValueError("unknown source task unexpectedly has a model pair")


def classify_expected_contextless_errors(errors: list[dict[str, Any]], readiness: dict[str, Any],
        tasks: list[dict[str, Any]], requests: list[dict[str, Any]], documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Allow only source-unknown tasks and exact same-row target-fact dependents."""
    task_by = {item["task_id"]: item for item in tasks}
    request_by = {item["request_id"]: item for item in requests}
    document_by = {item["document_id"]: item for item in documents}
    if len(task_by) != len(tasks) or len(request_by) != len(requests) or len(document_by) != len(documents):
        raise ValueError("source error dependency inventory has duplicate IDs")
    by_task: dict[str, dict[str, Any]] = {}
    for error in errors:
        task_id = error.get("task_id")
        if task_id not in task_by or task_id in by_task:
            raise ValueError("source preparation error has missing, foreign, or duplicate task ID")
        by_task[task_id] = error
    unknown_ids = set(readiness["unknown_task_ids"])
    if set(by_task) < unknown_ids:
        raise ValueError("one or more explicit unknown source receipts lacks a preparation error")
    for task_id in unknown_ids:
        if by_task[task_id].get("error") != "full original option family missing or malformed":
            raise ValueError("unknown receipt did not fail closed at the complete option-family guard")
    dependent_ids: list[str] = []
    unknown_tasks = [task_by[task_id] for task_id in unknown_ids]
    for task_id, error in by_task.items():
        if task_id in unknown_ids:
            continue
        task = task_by[task_id]
        if error.get("error") != "exact target selected axis fact: expected one source coordinate, found 0":
            raise ValueError("unexpected source preparation error outside explicit unknown dependency")
        if task.get("direction") != "au_to_rakuten":
            raise ValueError("target-fact dependency error has unexpected task direction")
        request = request_by.get(task.get("request_id"))
        if not request:
            raise ValueError("target-fact dependency request is missing")
        target_doc = document_by.get(request.get("target_document_id"))
        if not target_doc or target_doc.get("side") != "rakuten":
            raise ValueError("target-fact dependency does not target the Rakuten source side")
        target_axes = request.get("target_selected_axes")
        if not isinstance(target_axes, list) or not target_axes:
            raise ValueError("target-fact dependency has no exact target axes")
        candidates = []
        for unknown_task in unknown_tasks:
            if (unknown_task.get("direction") != "rakuten_to_au"
                    or unknown_task.get("case_id") != task.get("case_id")
                    or unknown_task.get("au_row_key") != task.get("au_row_key")):
                continue
            raw_ref = unknown_task.get("raw_condition_ref") or {}
            if any(axis.get("axis_name") == raw_ref.get("axis_name")
                   and axis.get("value") == raw_ref.get("value") for axis in target_axes):
                candidates.append(unknown_task)
        if len(candidates) != 1:
            raise ValueError("target-fact dependency is not uniquely linked to one same-row unknown Rakuten fact")
        dependent_ids.append(task_id)
    expected_ids = unknown_ids | set(dependent_ids)
    if set(by_task) != expected_ids:
        raise ValueError("source preparation errors do not match the complete fail-closed dependency ledger")
    return {"contextless_task_ids": sorted(expected_ids),
            "dependent_contextless_task_ids": sorted(dependent_ids),
            "source_unknown_count": len(unknown_ids), "dependent_count": len(dependent_ids),
            "contextless_count": len(expected_ids),
            "dependent_task_ids_sha256": digest(sorted(dependent_ids)),
            "contextless_task_ids_sha256": digest(sorted(expected_ids)),
            "contextless_error_ledger_sha256": digest(sorted(errors, key=lambda error: error["task_id"]))}


def _validate_input_records(input_dir: Path, manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    names = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl",
             "case-plans.jsonl", "task-request-family-audit.jsonl", "membership.jsonl",
             "request-model-input-map.jsonl", "baseline-model-inputs.jsonl")
    rows = {name: read_rows(input_dir / name) for name in names}
    validate_source_records(rows, manifest)
    return rows


def prepare(config_path: Path, finalize_existing: bool = False) -> dict[str, Any]:
    bridge = _load_bridge()
    config = bridge.load_config(config_path)
    input_dir = Path(config["output"])
    output_dir = Path(config["native_output"])
    source_scope = bridge.scope_source_manifest(input_dir, config)
    if output_dir.exists() and not finalize_existing:
        raise FileExistsError("refusing to overwrite partial source output: " + str(output_dir))
    if output_dir.exists() and finalize_existing:
        existing = read_json(output_dir / "manifest.json")
        if (existing.get("source_input_manifest_sha256") != sha(input_dir / "manifest.json")
                or existing.get("dataset") != config["dataset"]
                or existing.get("task_count") != source_scope["task_count"]
                or existing.get("row_count") != source_scope["row_count"]):
            raise ValueError("existing partial source output does not match the frozen input cohort")
        if existing.get("partial_source_bridge_sha256"):
            raise FileExistsError("partial source output already has a final metadata receipt")
    if not output_dir.exists():
        scoped = import_module("sku_body_invariant_scoped_rakuten_inventory_test_v1")
        base = scoped.NATIVE.BASE
        original_manifest = scoped.INDEXED.manifest
        original_validator = base._validate_input_records
        scoped.INDEXED.manifest = lambda path: bridge.scope_source_manifest(path, config)
        base._validate_input_records = _validate_input_records
        try:
            report = scoped.prepare(input_dir, Path(config["source_root"]), output_dir)
        finally:
            scoped.INDEXED.manifest = original_manifest
            base._validate_input_records = original_validator
    else:
        report = read_json(output_dir / "manifest.json")
    readiness = validate_source_records({name: read_rows(input_dir / name) for name in (
        "documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl",
        "case-plans.jsonl", "task-request-family-audit.jsonl", "membership.jsonl",
        "request-model-input-map.jsonl", "baseline-model-inputs.jsonl")}, source_scope)
    errors = read_rows(output_dir / "preparation-errors.jsonl")
    bindings = read_rows(output_dir / "validation-bindings.jsonl")
    pairs = read_rows(output_dir / "model-pairs-v3.jsonl")
    classified = classify_expected_contextless_errors(errors, readiness,
        read_rows(input_dir / "tasks.jsonl"), read_rows(input_dir / "requests.jsonl"),
        read_rows(input_dir / "documents.jsonl"))
    validate_contextless_unknowns(classified["contextless_task_ids"], bindings, pairs)
    binding_ids = [(item.get("task_binding") or {}).get("task_id") for item in bindings]
    expected_ids = {item["task_id"] for item in read_rows(input_dir / "tasks.jsonl")}
    if len(binding_ids) != len(expected_ids) or set(binding_ids) != expected_ids:
        raise ValueError("partial native output does not preserve exactly one private binding per task")
    error_ids = [item.get("task_id") for item in errors]
    if len(error_ids) != len(set(error_ids)) or set(error_ids) != set(classified["contextless_task_ids"]):
        raise ValueError("native source preparation errors differ from the explicit contextless dependency ledger")
    freeze = read_json(output_dir / "freeze.json")
    if freeze.get("source_fact_errors") != readiness["unknown_count"]:
        raise ValueError("native freeze source-fact error count differs from explicit unknown receipt count")
    # Preserve NLI's freeze.json and expected error rows byte-for-byte. Scope
    # metadata states that the family evidence is partial and pins the failure ledger.
    report.update({"dataset": config["dataset"], "selected_product_count": config["selected_product_count"],
        "selected_case_count": config["selected_case_count"],
        "identity_plan_manifest_sha256": config["identity_plan_manifest_sha256"],
        "selected_membership_sha256": config["selected_membership_sha256"],
        "identity_bridge_sha256": config["identity_bridge_sha256"],
        "raw_source_capture_inventory_sha256": source_scope["raw_source_capture_inventory_sha256"],
        "source_family_completeness": "partial_unknown_retained",
        "source_family_complete_all_tasks": False,
        "source_family_verified_task_count": readiness["verified_count"],
        "source_family_unknown_task_count": readiness["unknown_count"],
        "source_family_unknown_task_ids_sha256": readiness["unknown_task_ids_sha256"],
        "source_family_unknown_receipt_ledger_sha256": readiness["unknown_receipt_ledger_sha256"],
        "source_family_unknown_task_ids": readiness["unknown_task_ids"],
        "source_fact_errors_preserved": readiness["unknown_count"],
        "preparation_error_count": len(errors),
        "private_validation_binding_count": len(bindings),
        "private_validation_bindings_preserved_byte_exact": True,
        "contextless_unknown_binding_count": classified["contextless_count"],
        "target_fact_context_missing_task_count": classified["dependent_count"],
        "target_fact_context_missing_task_ids": classified["dependent_contextless_task_ids"],
        "target_fact_context_missing_task_ids_sha256": classified["dependent_task_ids_sha256"],
        "contextless_task_ids": classified["contextless_task_ids"],
        "contextless_task_ids_sha256": classified["contextless_task_ids_sha256"],
        "contextless_error_ledger_sha256": classified["contextless_error_ledger_sha256"],
        "ordered_complete_source_families_verified": False,
        "unknown_tasks_have_no_model_pair": True,
        "source_input_manifest_sha256": sha(input_dir / "manifest.json"),
        "partial_source_bridge_sha256": sha(Path(__file__)),
        "test_split_read": True, "independent_test": True, "labels_read": False,
        "predictions_read": False, "model_inference_run": False, "scores_created": False,
        "production_adoption_allowed": False})
    (output_dir / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def import_module(name: str):
    sys.path.insert(0, str(HERE))
    return __import__(name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--finalize-existing", action="store_true",
        help="validate and stamp an existing source-only v2 output after a prior postcondition failure")
    args = parser.parse_args()
    report = prepare(args.config, finalize_existing=args.finalize_existing)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
