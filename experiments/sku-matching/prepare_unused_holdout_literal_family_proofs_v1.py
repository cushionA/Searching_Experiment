#!/usr/bin/env python3
"""Prepare strict raw-family literal proofs for the frozen unused TEST holdout.

This source-only wrapper adapts split metadata and cohort counts around the
unchanged literal proof builder. It reads only frozen source inputs and raw
captures; it never reads labels, gold, worker responses, predictions, or scores.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / ".lab-output/sku-unused-holdout-choice-20261013-v1"
SOURCE_INPUTS = BASE / "source-inputs"
SOURCE_ROOT = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/test"
SOURCE_BUNDLE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
NLI_INPUTS = BASE / "prepared-choice-nli"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-unused-holdout-literal-family-proofs-20261013-v1"
BUILDER = HERE / "build_sku_literal_family_proofs_v1.py"
HELPERS = HERE / "prepare_dev_literal_family_proofs_v1.py"


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_rows(path: Path, rows) -> None:
    with path.open("xb") as stream:
        for row in rows:
            stream.write(canonical(row) + b"\n")


def indexed(rows: list[dict], key: str, label: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        identity = row.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError(f"missing or duplicate {label} identity")
        result[identity] = row
    return result


def validate_source_manifest(source_inputs: Path, source_root: Path, nli_dir: Path) -> tuple[dict, dict]:
    """Bind the frozen 20x2 TEST source packet to its parent TEST inputs."""
    source_manifest = read_json(source_inputs / "manifest.json")
    if (source_manifest.get("schema_version") != "full-condition-inputs-v2"
            or source_manifest.get("split") != "test"
            or source_manifest.get("independent_test") is not True
            or source_manifest.get("selected_product_count") != 20
            or source_manifest.get("selected_case_count") != 40
            or source_manifest.get("selected_row_count") != 394
            or source_manifest.get("selected_task_count") != 1598
            or source_manifest.get("labels_read") is not False
            or source_manifest.get("model_inference_run") is not False
            or source_manifest.get("production_adoption_allowed") is not False
            or source_manifest.get("all_rows_for_selected_cases_retained") is not True
            or source_manifest.get("all_original_task_bindings_retained") is not True):
        raise ValueError("source inputs are not the frozen 20-product x 2-case TEST subset")
    root_manifest = read_json(source_root.parent / "manifest.json")
    if (root_manifest.get("schema_version") != "full-condition-adapter-v2"
            or root_manifest.get("missing_referenced_sources") != []
            or root_manifest.get("labels_read") is not False
            or root_manifest.get("model_inference_run") is not False):
        raise ValueError("full-condition TEST source tree is not raw-source ready")
    for name, digest in source_manifest.get("outputs_sha256", {}).items():
        if sha_file(source_inputs / name) != digest:
            raise ValueError("selected source-input hash differs: " + name)
    holdout_root = source_inputs.parent
    holdout_manifest = read_json(holdout_root / "manifest.json")
    policy_path = holdout_root / "selection-policy.json"
    membership_path = holdout_root / "selection-membership.jsonl"
    policy = read_json(policy_path)
    policy_core = {key: value for key, value in policy.items()
                   if key not in {"policy_sha256", "dev_test_product_separation_audit", "status"}}
    membership = read_rows(membership_path)
    cases = indexed(read_rows(source_inputs / "case-plans.jsonl"), "case_id", "selected case")
    members = indexed(membership, "case_id", "selected membership case")
    if (holdout_manifest.get("schema_version") != "unused-holdout-source-choice-nli-v1"
            or holdout_manifest.get("selected_case_count") != 40
            or holdout_manifest.get("selected_product_count") != 20
            or holdout_manifest.get("selected_row_count") != 394
            or holdout_manifest.get("selected_task_count") != 1598
            or holdout_manifest.get("selected_request_count") != 1042
            or holdout_manifest.get("selected_old_product_overlap") != 0
            or holdout_manifest.get("selected_dev_product_overlap") != 0
            or source_manifest.get("selection_policy_sha256") != policy.get("policy_sha256")
            or source_manifest.get("selected_membership_sha256") != sha_file(membership_path)
            or holdout_manifest.get("selection_policy_sha256") != policy.get("policy_sha256")
            or policy.get("selection_count") != {"products": 20, "cases_per_product": 2, "cases": 40}
            or hashlib.sha256(canonical(policy_core)).hexdigest() != policy.get("policy_sha256")
            or set(members) != set(cases)):
        raise ValueError("selection policy/membership does not bind the exact frozen 20x2 holdout")
    product_counts = Counter(str(row.get("au_product_id")) for row in membership)
    if len(product_counts) != 20 or set(product_counts.values()) != {2}:
        raise ValueError("selection membership is not exactly two TEST cases per product")
    for case_id, case in cases.items():
        member = members[case_id]
        if any(member.get(key) != case.get(key) for key in
               ("case_id", "au_product_id", "dossier_id", "source_version")):
            raise ValueError("selected membership and source case identity differ")
    for name, digest in source_manifest.get("source_original_output_sha256", {}).items():
        if sha_file(source_root / name) != digest:
            raise ValueError("full TEST source hash differs: " + name)
    nli_summary = read_json(nli_dir / "summary.json")
    if (nli_summary.get("selected_task_count") != source_manifest.get("selected_task_count")
            or nli_summary.get("input_manifest_sha256") != sha_file(source_inputs / "manifest.json")
            or nli_summary.get("inference_run") is not False
            or nli_summary.get("scores_or_gate_outputs_created") is not False
            or nli_summary.get("labels_read") is not False
            or nli_summary.get("gold_or_worker_responses_read") is not False):
        raise ValueError("choice-NLI preparation does not bind the same blind source cohort")
    return source_manifest, root_manifest


def validate_full_coverage(source_inputs: Path) -> dict:
    cases = indexed(read_rows(source_inputs / "case-plans.jsonl"), "case_id", "case")
    tasks = indexed(read_rows(source_inputs / "tasks.jsonl"), "task_id", "task")
    requests = indexed(read_rows(source_inputs / "requests.jsonl"), "request_id", "request")
    rows = read_rows(source_inputs / "row-plans.jsonl")
    task_ids_from_rows = []
    row_keys = set()
    task_ids_by_row = defaultdict(set)
    for row in rows:
        case_id, row_key = row.get("case_id"), row.get("au_row_key")
        if case_id not in cases or not isinstance(row_key, str) or (case_id, row_key) in row_keys:
            raise ValueError("duplicate or unbound fixed TEST row plan")
        row_keys.add((case_id, row_key))
        bindings = row.get("bindings")
        if not isinstance(bindings, list):
            raise ValueError("row plan lacks complete task bindings")
        bound_ids = set()
        for binding in bindings:
            task_id = binding.get("task_id")
            task = tasks.get(task_id)
            if (task is None or task.get("case_id") != case_id or task.get("au_row_key") != row_key
                    or task.get("request_id") not in requests or task_id in bound_ids):
                raise ValueError("fixed row/task/request source binding mismatch")
            bound_ids.add(task_id)
            task_ids_from_rows.append(task_id)
            task_ids_by_row[(case_id, row_key)].add(task_id)
        for direction, count_key in (("rakuten_to_au", "rakuten_axis_count"),
                                     ("au_to_rakuten", "au_axis_count")):
            if sum(tasks[tid].get("direction") == direction for tid in bound_ids) != row.get(count_key):
                raise ValueError("fixed row is missing a direction's full source task bindings")
    if len(cases) != 40 or len(rows) != 394 or len(tasks) != 1598:
        raise ValueError("selected TEST source counts differ from the frozen cohort")
    if len(task_ids_from_rows) != len(set(task_ids_from_rows)) or set(task_ids_from_rows) != set(tasks):
        raise ValueError("selected tasks do not exactly equal all bidirectional row bindings")
    task_requests = {task["request_id"] for task in tasks.values()}
    if task_requests != set(requests):
        raise ValueError("selected requests do not exactly equal the complete source-task request set")
    products = {str(case.get("au_product_id")) for case in cases.values()}
    if len(products) != 20 or any(case.get("split") != "test" for case in cases.values()):
        raise ValueError("selected case plans do not retain the TEST 20-product cohort")
    for case_id, case in cases.items():
        actual_rows = {row_key for cid, row_key in row_keys if cid == case_id}
        if actual_rows != set(case.get("row_keys", [])) or len(actual_rows) != case.get("total_au_rows"):
            raise ValueError("selected case omits a captured AU row")
    return {"cases": cases, "rows": rows, "tasks": tasks, "requests": requests,
            "selected_product_count": len(products), "selected_row_count": len(rows),
            "selected_task_count": len(tasks), "selected_request_count": len(requests)}


def _load_helpers():
    spec = importlib.util.spec_from_file_location("dev_literal_proof_helpers", HELPERS)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load reusable source-only literal proof helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepare_inputs(source_inputs: Path, source_root: Path, source_bundle: Path,
                   output_root: Path) -> tuple[dict, dict]:
    if output_root.exists():
        raise FileExistsError("refusing to overwrite " + str(output_root))
    source_manifest = read_json(source_inputs / "manifest.json")
    coverage = validate_full_coverage(source_inputs)
    source_case_plans = coverage["cases"]
    helper = _load_helpers()
    source_split_manifest = read_json(source_root / "manifest.json")
    source_cases, source_products, source_pins, selected_sources = helper.load_original_records(
        source_bundle, source_split_manifest, source_case_plans)

    output_root.mkdir(parents=True)
    inputs = output_root / "inputs"
    inputs.mkdir()
    tasks = coverage["tasks"]
    cases = coverage["cases"]
    requests = coverage["requests"]
    rows = coverage["rows"]
    write_rows(inputs / "tasks.jsonl", (tasks[key] for key in sorted(tasks)))
    write_rows(inputs / "source-cases.jsonl", (source_cases[key] for key in sorted(source_cases)))
    write_rows(inputs / "source-products.jsonl", (source_products[key] for key in sorted(source_products)))
    write_rows(inputs / "row-plans.jsonl", sorted(rows, key=lambda row: (row["case_id"], row["au_row_key"])))
    write_rows(inputs / "selected-source-manifest.jsonl", sorted(selected_sources, key=lambda x: x["case_id"]))
    write_json(inputs / "source-input-manifests.json", source_pins)
    data_names = ("tasks.jsonl", "source-cases.jsonl", "source-products.jsonl", "row-plans.jsonl",
                  "selected-source-manifest.jsonl", "source-input-manifests.json")
    output_hashes = {name: sha_file(inputs / name) for name in data_names}
    input_manifest = {"schema_version": "sku-literal-family-unused-holdout-input-v1",
        "source_gate_artifact": str(source_inputs.relative_to(ROOT)),
        "source_gate_manifest_sha256": sha_file(source_inputs / "manifest.json"),
        "source_full_condition_manifest_sha256": sha_file(source_root / "manifest.json"),
        "source_bundle_inventory_sha256": sha_file(source_bundle.parent / "inventory.json"),
        "source_bundle_index_sha256": sha_file(source_bundle.parent / "source-index.json"),
        "selection_policy_sha256": source_manifest.get("selection_policy_sha256"),
        "selected_membership_sha256": source_manifest.get("selected_membership_sha256"),
        "selected_case_count": len(cases), "selected_product_count": coverage["selected_product_count"],
        "row_plan_count": len(rows), "task_count": len(tasks),
        "request_count": len(requests), "proof_pair_coverage_required": True,
        "source_input_pins": source_pins, "output_sha256": output_hashes,
        "labels_read": False, "gold_or_references_read": False, "worker_responses_read": False,
        "test_split_read": True, "predictions_read": False, "scores_read": False,
        "model_inference_run": False, "classification_or_scoring_run": False,
        "whole_gate_evaluation_run": False, "v7_hints_read": False,
        "all_source_rows_retained": True, "all_original_task_bindings_retained": True,
        "production_adoption_allowed": False}
    write_json(inputs / "manifest.json", input_manifest)
    return {"input_dir": inputs, "selected_cases": cases, "selected_rows": rows,
            "selected_tasks": tasks, "selected_requests": requests,
            "source_cases": source_cases, "source_products": source_products,
            "source_pins": source_pins}, coverage


def finalize_manifest(output_root: Path, manifest: dict) -> dict:
    canonical_names = ("inputs/manifest.json", "proof-bundle/manifest.json", "proof-bundle/proofs.jsonl",
        "task-source-verification.jsonl", "request-proof-statuses.jsonl", "protected-requests.jsonl",
        "task-proof-states.jsonl", "protected-nli-contexts.jsonl", "nli-v2-task-protection.jsonl",
        "nli-v2-context-protection.jsonl")
    hashes = {name: {"sha256": sha_file(output_root / name),
                     "bytes": (output_root / name).stat().st_size} for name in canonical_names}
    manifest.update({"canonical_output_hashes": hashes, "canonical_output_hashes_finalized": True,
        "source_code_pins": {
            "experiments/sku-matching/prepare_unused_holdout_literal_family_proofs_v1.py": sha_file(Path(__file__)),
            "experiments/sku-matching/prepare_dev_literal_family_proofs_v1.py": sha_file(HELPERS),
            "experiments/sku-matching/build_sku_literal_family_proofs_v1.py": sha_file(BUILDER),
            "experiments/sku-matching/sku_literal_family_proof_v1.py": sha_file(HERE / "sku_literal_family_proof_v1.py")},
        "output_manifest_rule": "canonical_output_hashes is the authoritative path/bytes/SHA map"})
    path = output_root / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def attach_protection_with_contextless_abstentions(output_root: Path, nli_dir: Path,
                                                   helper) -> dict:
    """Use the frozen join for prepared pairs and retain source abstentions.

    The shared helper requires one pair for every task. TEST preparation has
    18 explicit contextless source abstentions; they must remain in task maps
    without inventing a pair or cache context.
    """
    bindings = read_rows(nli_dir / "validation-bindings.jsonl")
    model_pairs = indexed(read_rows(nli_dir / "model-pairs.jsonl"), "pair_id", "NLI pair")
    bound = [row for row in bindings if row.get("pair_id") in model_pairs]
    contextless = [row for row in bindings if row.get("pair_id") not in model_pairs]
    if (len(contextless) != 18 or any(row.get("pair_id") is not None
                                       or row.get("preparation_status") != "unknown_invalid_source_context"
                                       for row in contextless)):
        raise ValueError("NLI contextless task set differs from frozen source-preparation abstentions")
    states_path = output_root / "task-proof-states.jsonl"
    all_states = read_rows(states_path)
    state_by_id = indexed(all_states, "task_id", "literal task state")
    contextless_ids = {row["task_binding"]["task_id"] for row in contextless}
    if not contextless_ids.issubset(state_by_id) or len(state_by_id) != 1598:
        raise ValueError("contextless NLI abstentions do not map to every frozen literal task state")
    contextless_states = [state_by_id[task_id] for task_id in sorted(contextless_ids)]
    # The reusable join stays byte-for-byte unchanged. Give it a temporary
    # source-preparation view containing only genuine prepared semantic pairs.
    with tempfile.TemporaryDirectory(prefix="sku-holdout-literal-nli-map-") as temp:
        adapted = Path(temp) / "prepared-choice-nli"
        shutil.copytree(nli_dir, adapted)
        (adapted / "validation-bindings.jsonl").unlink()
        write_rows(adapted / "validation-bindings.jsonl", bound)
        summary_path = adapted / "summary.json"
        summary = read_json(summary_path)
        output_sha = dict(summary.get("output_sha256") or {})
        output_sha["validation-bindings.jsonl"] = sha_file(adapted / "validation-bindings.jsonl")
        summary["output_sha256"] = output_sha
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                                encoding="utf-8")
        try:
            write_rows(states_path.with_name(".task-proof-states.prepared-only.jsonl"),
                       (state_by_id[task_id] for task_id in sorted(set(state_by_id) - contextless_ids)))
            os.replace(states_path.with_name(".task-proof-states.prepared-only.jsonl"), states_path)
            joined = helper.attach_nli_v2_protection_map(output_root, adapted)
        finally:
            # Restore all 1,598 states for downstream canonical outputs.
            pending = states_path.with_name(".task-proof-states.restore.jsonl")
            write_rows(pending, all_states)
            os.replace(pending, states_path)
    task_map_path = output_root / "nli-v2-task-protection.jsonl"
    task_rows = read_rows(task_map_path)
    for state in contextless_states:
        task_rows.append({"pair_id": None, "context_id": None, "cache_key": None,
            "task_id": state["task_id"], "request_id": state["request_id"],
            "case_id": state["case_id"], "au_row_key": state["au_row_key"],
            "direction": state["direction"], "axis_name": state["axis_name"],
            "selected_value": state["selected_value"], "option_values": state["ordered_family"],
            "literal_status": state["status"],
            "literal_proof_verified": state["literal_proof_verified"],
            "skip_task_classification": state["status"] in {"support", "conflict"}
                and state["literal_proof_verified"] is True,
            "nli_preparation_status": "unknown_invalid_source_context",
            "no_semantic_pair_or_context_created": True,
            "production_adoption_allowed": False})
    pending = task_map_path.with_name(".nli-v2-task-protection.pending.jsonl")
    write_rows(pending, sorted(task_rows, key=lambda row: row["task_id"]))
    os.replace(pending, task_map_path)
    # Preserve immutable pins to the original NLI preparation, not the narrow
    # temporary view used to make the helper's join fail closed.
    pin_names = ("freeze.json", "summary.json", "model-pairs.jsonl", "paired-contexts.jsonl",
                 "validation-bindings.jsonl", "source-documents.jsonl", "preparation-errors.jsonl")
    manifest_path = output_root / "manifest.json"
    manifest = read_json(manifest_path)
    manifest.update({"nli_v2_preparation_pins": {name: sha_file(nli_dir / name) for name in pin_names},
        "nli_v2_source_task_binding_count": len(bindings),
        "nli_v2_contextless_abstention_task_count": len(contextless),
        "nli_v2_contextless_abstention_task_ids": sorted(contextless_ids),
        "nli_v2_task_protection_sha256": sha_file(task_map_path),
        "nli_v2_inference_run": False, "nli_v2_responses_read": False})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                             encoding="utf-8")
    return {**joined, "nli_v2_source_task_binding_count": len(bindings),
            "nli_v2_contextless_abstention_task_count": len(contextless)}


def run(source_inputs: Path = SOURCE_INPUTS, source_root: Path = SOURCE_ROOT,
        source_bundle: Path = SOURCE_BUNDLE, nli_dir: Path = NLI_INPUTS,
        output_root: Path = DEFAULT_OUTPUT) -> dict:
    source_manifest, root_manifest = validate_source_manifest(source_inputs, source_root, nli_dir)
    prepared, coverage = prepare_inputs(source_inputs, source_root, source_bundle, output_root)
    builder_spec = importlib.util.spec_from_file_location("sku_literal_family_builder_unused_holdout", BUILDER)
    if builder_spec is None or builder_spec.loader is None:
        raise RuntimeError("cannot load unchanged literal family proof builder")
    builder = importlib.util.module_from_spec(builder_spec)
    sys.modules[builder_spec.name] = builder
    builder_spec.loader.exec_module(builder)
    bundle_dir = output_root / "proof-bundle"
    proof_manifest = builder.build(output_root, source_bundle, bundle_dir)
    helper = _load_helpers()
    # The reusable receipt writer updates this manifest as it writes its
    # canonical task-source receipt. Seed only the selected source metadata.
    write_json(output_root / "manifest.json", {
        "schema_version": "sku-unused-holdout-literal-family-proof-v1",
        "phase": "source_only_test_holdout_literal_proofs_in_progress",
        "input_task_count": proof_manifest["task_count"],
        "labels_read": False, "gold_or_references_read": False,
        "worker_responses_read": False, "test_split_read": True,
        "predictions_read": False, "scores_read": False,
        "model_inference_run": False, "classification_or_scoring_run": False,
        "whole_gate_evaluation_run": False, "production_adoption_allowed": False})
    task_verification = helper.verify_all_task_source_families(output_root, source_bundle)
    prepared["task_source_receipts"] = read_rows(output_root / "task-source-verification.jsonl")
    statuses = helper.aggregate_request_statuses(output_root, prepared, bundle_dir / "proofs.jsonl")
    manifest = {"schema_version": "sku-unused-holdout-literal-family-proof-v1",
        "phase": "source_only_test_holdout_literal_proofs_complete",
        "source_inputs_manifest_sha256": sha_file(source_inputs / "manifest.json"),
        "source_manifest_sha256": sha_file(source_root / "manifest.json"),
        "source_root_manifest_sha256": sha_file(source_root.parent / "manifest.json"),
        "selection_policy_sha256": source_manifest["selection_policy_sha256"],
        "selected_membership_sha256": source_manifest["selected_membership_sha256"],
        "nli_preparation_manifest_sha256": sha_file(nli_dir / "freeze.json"),
        "proof_bundle_manifest_sha256": sha_file(bundle_dir / "manifest.json"),
        "proofs_sha256": sha_file(bundle_dir / "proofs.jsonl"),
        "input_case_count": proof_manifest["input_subset_case_count"],
        "input_product_count": coverage["selected_product_count"],
        "input_row_count": proof_manifest["row_plan_count"],
        "input_task_count": proof_manifest["task_count"],
        "input_request_count": len(prepared["selected_requests"]),
        "literal_proof_status_counts": proof_manifest["proof_status_counts"],
        **task_verification, **statuses,
        "labels_read": False, "gold_or_references_read": False, "worker_responses_read": False,
        "test_split_read": True, "predictions_read": False, "scores_read": False,
        "model_inference_run": False, "classification_or_scoring_run": False,
        "whole_gate_evaluation_run": False, "production_adoption_allowed": False,
        "all_source_rows_retained": True, "all_original_task_bindings_retained": True,
        "nli_v2_protection_attached": False}
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    protection = attach_protection_with_contextless_abstentions(output_root, nli_dir, helper)
    manifest = read_json(output_root / "manifest.json")
    manifest.update(protection)
    manifest["nli_v2_protection_attached"] = True
    manifest["nli_v2_inference_run"] = False
    manifest["nli_v2_responses_read"] = False
    return finalize_manifest(output_root, manifest)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-inputs", type=Path, default=SOURCE_INPUTS)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--source-bundle", type=Path, default=SOURCE_BUNDLE)
    parser.add_argument("--nli-inputs", type=Path, default=NLI_INPUTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.source_inputs, args.source_root, args.source_bundle,
                         args.nli_inputs, args.output), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
