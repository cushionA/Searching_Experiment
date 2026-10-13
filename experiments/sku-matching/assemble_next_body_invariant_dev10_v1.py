#!/usr/bin/env python3
"""Assemble a frozen source-only DEV10 body-invariant run; never score or infer."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

ROOT = Path(__file__).resolve().parents[2]
COHORT = ROOT / ".lab-output/sku-next-body-invariant-dev-20261013-v1"
PREPARED = ROOT / ".lab-output/sku-next-body-invariant-v3-source-only-20261013-r8"
PROOF = ROOT / ".lab-output/sku-next-body-invariant-literal-proofs-20261013-v1"
PRIOR_DEV30 = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/membership.jsonl"
PRIOR_COORDINATE_DEV20 = ROOT / ".lab-output/sku-next-dev-coordinate-refids-20261013-v1/membership.jsonl"
EXPECTED_CASES, EXPECTED_PRODUCTS, EXPECTED_ROWS, EXPECTED_TASKS = 10, 5, 110, 452
TASK_COUNT = EXPECTED_TASKS
VARIANTS = {"body-invariant-v3", "literal-only"}

sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import evaluate_dev_source_bound_whole_sku_gate_v1 as EVAL  # noqa: E402
import sku_body_invariant_choice_v3 as BODY  # noqa: E402
import sku_directional_choice_nli_v1 as NLI  # noqa: E402


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pin(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": sha_file(path)}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_rows(path: Path) -> list[dict[str, Any]]:
    return NLI.read_rows(Path(path))


def write_json(path: Path, value: dict[str, Any]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(BODY.canon(row).decode("utf-8") + "\n")


def relpath(path: Path) -> str:
    return Path(path).resolve().relative_to(ROOT).as_posix()


def verify_dev10_cohort(prepared_dir: Path) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    manifest = read_json(COHORT / "manifest.json")
    policy = read_json(COHORT / "selection-policy.json")
    plan = read_json(COHORT / "selection-plan.json")
    expected_hashes = manifest.get("outputs_sha256", {})
    for name, digest in expected_hashes.items():
        path = COHORT / name
        if not path.is_file() or sha_file(path) != digest:
            raise ValueError(f"blind DEV10 source input SHA differs: {name}")
    if (manifest.get("schema_version") != "sku-next-body-invariant-dev-source-v1"
            or manifest.get("split") != "dev" or manifest.get("independent_test") is not False
            or manifest.get("selected_case_count") != EXPECTED_CASES
            or manifest.get("selected_product_count") != EXPECTED_PRODUCTS
            or manifest.get("row_count") != EXPECTED_ROWS
            or manifest.get("task_count") != EXPECTED_TASKS
            or manifest.get("labels_read") is not False or manifest.get("predictions_read") is not False
            or manifest.get("test_split_read") is not False or manifest.get("scores_created") is not False):
        raise ValueError("source DEV10 manifest does not match its frozen blind cohort")
    if (policy.get("schema_version") != "sku-next-body-invariant-dev-policy-v1"
            or policy.get("dataset") != manifest.get("dataset")
            or policy.get("labels_read") is not False or policy.get("predictions_read") is not False
            or policy.get("task_details_read") is not False or policy.get("test_split_read") is not False
            or sha_file(COHORT / "selection-policy.json") != manifest.get("selection_policy_sha256")
            or sha_file(COHORT / "selection-plan.json") != manifest.get("selection_plan_sha256")
            or plan.get("policy_sha256") != manifest.get("selection_policy_sha256")):
        raise ValueError("DEV10 identity-only selection policy/plan pins differ")
    members = read_rows(COHORT / "membership.jsonl")
    cases = read_rows(COHORT / "case-plans.jsonl")
    rows = read_rows(COHORT / "row-plans.jsonl")
    tasks = read_rows(COHORT / "tasks.jsonl")
    requests = read_rows(COHORT / "requests.jsonl")
    if (len(members) != EXPECTED_CASES or len(cases) != EXPECTED_CASES
            or len(rows) != EXPECTED_ROWS or len(tasks) != EXPECTED_TASKS
            or len(requests) != 270
            or len({r["case_id"] for r in members}) != EXPECTED_CASES
            or len({r["au_product_id"] for r in members}) != EXPECTED_PRODUCTS):
        raise ValueError("DEV10 exact membership/case/product/row/task/request population differs")
    member_cases = {r["case_id"] for r in members}
    member_products = {r["au_product_id"] for r in members}
    if (set(plan.get("selected_case_ids", [])) != member_cases
            or set(plan.get("selected_products", [])) != member_products
            or plan.get("selected_case_count") != EXPECTED_CASES
            or plan.get("selected_product_count") != EXPECTED_PRODUCTS
            or any(sum(r["au_product_id"] == product for r in members) != 2 for product in member_products)):
        raise ValueError("selected case/product identities differ from their frozen source selection plan")
    if sha_file(COHORT / "membership.jsonl") != "b0bd4693ac02e1041ab52246fdcb2f2a4fbe9533022aaeb6bc7915d0652d7cea":
        raise ValueError("frozen DEV10 membership SHA differs")
    # Disjointness uses identity-only provenance and source membership bytes.
    excluded_hashes = {sha_file(PRIOR_DEV30), sha_file(PRIOR_COORDINATE_DEV20)}
    if excluded_hashes != set(plan.get("excluded_membership_sha256", [])):
        raise ValueError("DEV10 exclusion membership hashes differ from the policy plan")
    excluded_products = {r.get("au_product_id") for path in (PRIOR_DEV30, PRIOR_COORDINATE_DEV20)
                         for r in read_rows(path)}
    if {r.get("au_product_id") for r in members} & excluded_products:
        raise ValueError("DEV10 product membership overlaps prior DEV cohorts")
    return members, cases, rows, tasks, requests


def load_complete_bindings(tasks: list[dict], prepared_dir: Path) -> list[dict]:
    bindings = read_rows(prepared_dir / "validation-bindings.jsonl")
    task_ids = [row["task_id"] for row in tasks]
    binding_ids = [(row.get("task_binding") or {}).get("task_id") for row in bindings]
    if (len(bindings) != EXPECTED_TASKS or len(set(binding_ids)) != EXPECTED_TASKS
            or set(binding_ids) != set(task_ids)):
        raise ValueError("prepared body source bindings must cover every DEV10 task exactly once")
    return bindings


def referenced_source_artifacts(prepared_dir: Path, source_root: Path) -> list[Path]:
    """Return the exact pinned raw files and source inventory used by validation."""
    raw_names: set[str] = set()
    expected_shas: dict[str, set[str]] = {}
    input_files = [prepared_dir / "model-payloads-v3.jsonl", prepared_dir / "source-bodies.jsonl",
                   PROOF / "task-source-verification.jsonl", PROOF / "literal-pair-proofs.jsonl"]
    for input_file in input_files:
        for row in read_rows(input_file):
            def visit(value: Any) -> None:
                if isinstance(value, dict):
                    raw_name, digest = value.get("raw_file"), value.get("sha256")
                    if isinstance(raw_name, str):
                        raw_name = raw_name.replace("\\", "/")
                        raw_names.add(raw_name)
                        if isinstance(digest, str):
                            expected_shas.setdefault(raw_name, set()).add(digest)
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)
            visit(row)
    if not raw_names:
        raise ValueError("body source payloads contain no native raw-source references")
    output = [source_root.parent / "inventory.json"]
    for raw_name in sorted(raw_names):
        if len(expected_shas.get(raw_name, set())) > 1:
            raise ValueError(f"one body raw source path has conflicting SHA pins: {raw_name}")
        path = source_root / raw_name
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"pinned body raw source is missing or unsafe: {raw_name}")
        expected = next(iter(expected_shas.get(raw_name, set())), None)
        if expected is not None and sha_file(path) != expected:
            raise ValueError(f"pinned body raw source SHA differs: {raw_name}")
        output.append(path)
    if not output[0].is_file() or output[0].is_symlink():
        raise FileNotFoundError("source raw inventory is missing or unsafe")
    return output


def adapt_literal_proofs_for_eval(tasks: list[dict], proof_states: list[dict],
                                  source_receipts: list[dict], pair_proofs: list[dict]) -> list[dict]:
    """Attach validator metadata to the proof agent's unchanged source-only states."""
    task_by_id = EVAL.index(tasks, "task_id", "body DEV10 task")
    state_by_id = EVAL.index(proof_states, "task_id", "body DEV10 literal proof state")
    receipt_by_id = EVAL.index(source_receipts, "task_id", "body DEV10 source receipt")
    if set(task_by_id) != set(state_by_id) or set(task_by_id) != set(receipt_by_id):
        raise ValueError("literal source proof state/receipt population differs from all 452 tasks")
    pair_by_task: dict[str, dict] = {}
    for pair in pair_proofs:
        task_ids = [pair.get("forward_task_id"), pair.get("reverse_task_id")]
        if (not all(isinstance(x, str) and x for x in task_ids)
                or task_ids[0] == task_ids[1] or any(tid in pair_by_task for tid in task_ids)):
            raise ValueError("literal pair proof has missing or duplicate original task IDs")
        for tid in task_ids:
            pair_by_task[tid] = pair
    adapted = []
    for task_id, state in state_by_id.items():
        receipt = receipt_by_id[task_id]
        if receipt.get("status") != "verified_complete_ordered_raw_family":
            raise ValueError("literal proof source receipt does not verify the full ordered raw family")
        family = receipt.get("ordered_family")
        selected = task_by_id[task_id].get("raw_condition_ref", {}).get("value")
        if not isinstance(family, list) or family.count(selected) != 1 or receipt.get("selected_value") != selected:
            raise ValueError("literal source receipt family does not bind the exact task-selected whole value")
        row = dict(state)
        pair = pair_by_task.get(task_id)
        decisive = state.get("status") in {"support", "conflict"}
        if decisive:
            if (pair is None or pair.get("status") != state.get("status")
                    or pair.get("ordered_family") != family
                    or state.get("proof_pair_id") != (
                        pair.get("forward_task_id") + "|" + pair.get("reverse_task_id"))):
                raise ValueError("decisive task literal proof lacks its exact matching verified pair proof")
            row.update({"literal_proof_verified": True, "raw_source_family_verified": True,
                "selected_value": selected, "ordered_family": family,
                "proof_pair_id": state["proof_pair_id"]})
        adapted.append(row)
    if len(adapted) != TASK_COUNT or {r["task_id"] for r in adapted} != set(task_by_id):
        raise ValueError("adapted literal states do not cover all original DEV10 tasks")
    return adapted


def derive_pair_partition(tasks: list[dict], bindings: list[dict], prepared_pairs: list[dict],
                          proof_states: list[dict], source_receipts: list[dict]) -> dict[str, Any]:
    """Protect a pair only when every original task fanout has a source proof."""
    task_by_id = EVAL.index(tasks, "task_id", "partition task")
    proof_by_id = EVAL.index(proof_states, "task_id", "partition literal state")
    receipt_by_id = EVAL.index(source_receipts, "task_id", "partition source receipt")
    pair_by_id = EVAL.index(prepared_pairs, "pair_id", "partition prepared pair")
    if set(task_by_id) != set(proof_by_id) or set(task_by_id) != set(receipt_by_id):
        raise ValueError("partition requires exact source proofs/receipts for every original task")
    groups: dict[str, list[str]] = {}
    seen_tasks: set[str] = set()
    for binding in bindings:
        tb = binding.get("task_binding") or {}
        task_id, pair_id = tb.get("task_id"), binding.get("pair_id")
        if task_id not in task_by_id or task_id in seen_tasks:
            raise ValueError("partition bindings have a missing, foreign, or duplicate original task")
        seen_tasks.add(task_id)
        if binding.get("preparation_status") == "ready":
            if pair_id not in pair_by_id:
                raise ValueError("ready original task binding has a foreign prepared pair")
            groups.setdefault(pair_id, []).append(task_id)
        elif pair_id is not None:
            raise ValueError("contextless original task unexpectedly names a model pair")
    if seen_tasks != set(task_by_id) or set(groups) != set(pair_by_id):
        raise ValueError("partition task bindings do not cover exact prepared pair/task populations")
    protected, dispatched, protected_details = [], [], []
    for pair_id in sorted(pair_by_id):
        task_ids = groups[pair_id]
        protected_states = [EVAL.source_literal_state(task_by_id[tid], proof_by_id[tid], receipt_by_id[tid])
                            for tid in task_ids]
        if all(state is not None for state in protected_states):
            protected.append(pair_id)
            protected_details.append({"pair_id": pair_id,
                "status": "literal_source_protected_not_dispatched",
                "task_ids": sorted(task_ids), "protected_task_count": len(task_ids),
                "all_bound_tasks_have_source_literal_state": True})
        else:
            dispatched.append(pair_id)
    if (set(protected) & set(dispatched) or set(protected) | set(dispatched) != set(pair_by_id)
            or len(protected) + len(dispatched) != len(pair_by_id)):
        raise ValueError("literal protection and model dispatch do not partition all prepared pair IDs")
    return {"protected_pair_ids": protected, "dispatch_pair_ids": dispatched,
            "protected_details": protected_details,
            "protected_task_count": sum(row["protected_task_count"] for row in protected_details)}


def _partition_inputs(prepared_dir: Path, proof_dir: Path):
    _members, _cases, _rows, tasks, _requests = verify_dev10_cohort(prepared_dir)
    bindings = load_complete_bindings(tasks, prepared_dir)
    prepared_pairs = read_rows(prepared_dir / "model-pairs-v3.jsonl")
    proof_manifest = read_json(proof_dir / "manifest.json")
    proof_states = read_rows(proof_dir / "task-literal-proof-states.jsonl")
    proof_receipts = read_rows(proof_dir / "task-source-verification.jsonl")
    pair_proofs = read_rows(proof_dir / "literal-pair-proofs.jsonl")
    proof_states = adapt_literal_proofs_for_eval(tasks, proof_states, proof_receipts, pair_proofs)
    if (proof_manifest.get("task_count") != EXPECTED_TASKS
            or proof_manifest.get("selected_case_count") != EXPECTED_CASES
            or proof_manifest.get("selected_product_count") != EXPECTED_PRODUCTS
            or proof_manifest.get("cohort_manifest_sha256") != sha_file(COHORT / "manifest.json")
            or proof_manifest.get("source_bundle_inventory_sha256")
                != read_json(COHORT / "manifest.json").get("source_bundle_inventory_sha256")
            or proof_manifest.get("all_452_tasks_have_exactly_one_source_receipt") is not True
            or proof_manifest.get("source_task_receipt_count") != EXPECTED_TASKS
            or proof_manifest.get("task_literal_state_count") != EXPECTED_TASKS
            or len(prepared_pairs) != 141
            or proof_manifest.get("labels_read") is not False
            or proof_manifest.get("predictions_read") is not False
            or proof_manifest.get("model_inference_run") is not False
            or proof_manifest.get("scores_created") is not False
            or proof_manifest.get("test_split_read") is not False):
        raise ValueError("literal source partition inputs are not the exact blind DEV10 artifacts")
    for name, digest in proof_manifest.get("outputs_sha256", {}).items():
        if sha_file(proof_dir / name) != digest:
            raise ValueError(f"partition literal proof source SHA differs: {name}")
    partition = derive_pair_partition(tasks, bindings, prepared_pairs, proof_states, proof_receipts)
    return tasks, bindings, prepared_pairs, proof_states, proof_receipts, proof_manifest, partition


def write_partition(prepared_dir: Path, proof_dir: Path, output_dir: Path) -> dict[str, Any]:
    if Path(prepared_dir).resolve() != PREPARED or Path(proof_dir).resolve() != PROOF:
        raise ValueError("pre-model partition must use the canonical final R7 source and literal proof bundles")
    output_dir = Path(output_dir).resolve()
    if not relpath(output_dir).startswith(".lab-output/") or output_dir.exists():
        raise FileExistsError("source pair partition output must be a new .lab-output directory")
    _tasks, _bindings, prepared_pairs, _states, _receipts, proof_manifest, partition = _partition_inputs(prepared_dir, proof_dir)
    output_dir.mkdir(parents=True)
    try:
        write_rows(output_dir / "dispatch-pair-ids.jsonl", [{"pair_id": x} for x in partition["dispatch_pair_ids"]])
        write_rows(output_dir / "literal-protected-pair-ids.jsonl", [{"pair_id": x} for x in partition["protected_pair_ids"]])
        write_rows(output_dir / "literal-protected-no-dispatch.jsonl", partition["protected_details"])
        receipt = {"schema_version": "sku-next-body-invariant-dev10-pair-partition-v1",
            "status": "complete_source_derived_pre_model_partition",
            "prepared_pair_count": len(prepared_pairs),
            "dispatch_pair_count": len(partition["dispatch_pair_ids"]),
            "dispatch_pair_ids_sha256": BODY.sha_bytes(BODY.canon(partition["dispatch_pair_ids"])),
            "literal_protected_pair_count": len(partition["protected_pair_ids"]),
            "literal_protected_pair_ids_sha256": BODY.sha_bytes(BODY.canon(partition["protected_pair_ids"])),
            "literal_protected_task_count": partition["protected_task_count"],
            "protected_pairs_are_not_missing_model_responses": True,
            "source_inputs": {"cohort_manifest_sha256": sha_file(COHORT / "manifest.json"),
                "membership_sha256": sha_file(COHORT / "membership.jsonl"),
                "prepared_manifest_sha256": sha_file(prepared_dir / "manifest.json"),
                "prepared_freeze_sha256": sha_file(prepared_dir / "freeze.json"),
                "validation_bindings_sha256": sha_file(prepared_dir / "validation-bindings.jsonl"),
                "literal_proof_manifest_sha256": sha_file(proof_dir / "manifest.json"),
                "literal_proof_outputs_sha256": proof_manifest["outputs_sha256"],
                "eval_helper_sha256": sha_file(Path(EVAL.__file__)),
                "body_source_validator_sha256": sha_file(Path(BODY.__file__)),
                "partition_builder_sha256": sha_file(Path(__file__))},
            "labels_read": False, "predictions_read": False, "inference_run": False,
            "scores_created": False, "test_split_read": False, "production_adoption_allowed": False}
        write_json(output_dir / "pair-partition-receipt.json", receipt)
        outputs = [p for p in output_dir.iterdir() if p.is_file()]
        write_json(output_dir / "partition-output-inventory.json", {"schema_version": "body-dev10-partition-inventory-v1",
            "files": {p.name: pin(p) for p in sorted(outputs)}, "labels_read": False,
            "inference_run": False, "scores_created": False})
        return receipt
    except Exception as exc:
        write_json(output_dir / "partition-failure.json", {"error_type": type(exc).__name__,
            "error": str(exc), "traceback": traceback.format_exc(), "labels_read": False,
            "inference_run": False, "scores_created": False})
        raise


def validate_partition(partition_dir: Path, prepared_dir: Path, proof_dir: Path) -> tuple[dict, dict[str, Any]]:
    partition_dir = Path(partition_dir)
    resolved_partition_dir = partition_dir.resolve()
    if (partition_dir.is_symlink() or not resolved_partition_dir.is_relative_to(ROOT / ".lab-output")
            or not resolved_partition_dir.is_dir()):
        raise ValueError("frozen source partition must be a regular .lab-output directory")
    partition_dir = resolved_partition_dir
    receipt_path = partition_dir / "pair-partition-receipt.json"
    inventory_path = partition_dir / "partition-output-inventory.json"
    receipt, inventory = read_json(receipt_path), read_json(inventory_path)
    if (receipt.get("schema_version") != "sku-next-body-invariant-dev10-pair-partition-v1"
            or receipt.get("status") != "complete_source_derived_pre_model_partition"
            or receipt.get("labels_read") is not False or receipt.get("inference_run") is not False
            or receipt.get("scores_created") is not False or receipt.get("test_split_read") is not False
            or receipt.get("source_inputs", {}).get("partition_builder_sha256") != sha_file(Path(__file__))
            or inventory.get("schema_version") != "body-dev10-partition-inventory-v1"
            or inventory.get("labels_read") is not False or inventory.get("inference_run") is not False
            or inventory.get("scores_created") is not False):
        raise ValueError("pre-model literal protection partition receipt is incomplete or tainted")
    pins = inventory.get("files")
    if not isinstance(pins, dict) or set(pins) != {"dispatch-pair-ids.jsonl",
            "literal-protected-pair-ids.jsonl", "literal-protected-no-dispatch.jsonl",
            "pair-partition-receipt.json"}:
        raise ValueError("partition inventory does not enumerate the exact pre-model artifacts")
    current_files = {p.name for p in partition_dir.iterdir() if p.is_file() and p != inventory_path}
    if current_files != set(pins):
        raise ValueError("source pair partition directory has unlisted or missing artifacts")
    for name, expected in pins.items():
        if pin(partition_dir / name) != expected:
            raise ValueError(f"pre-model pair partition artifact SHA differs: {name}")
    _tasks, _bindings, prepared_pairs, _states, _receipts, _manifest, derived = _partition_inputs(prepared_dir, proof_dir)
    dispatch = [row["pair_id"] for row in read_rows(partition_dir / "dispatch-pair-ids.jsonl")]
    protected = [row["pair_id"] for row in read_rows(partition_dir / "literal-protected-pair-ids.jsonl")]
    details = read_rows(partition_dir / "literal-protected-no-dispatch.jsonl")
    if (dispatch != derived["dispatch_pair_ids"] or protected != derived["protected_pair_ids"]
            or details != derived["protected_details"]
            or receipt.get("prepared_pair_count") != len(prepared_pairs)
            or receipt.get("dispatch_pair_count") != len(dispatch)
            or receipt.get("dispatch_pair_ids_sha256") != BODY.sha_bytes(BODY.canon(dispatch))
            or receipt.get("literal_protected_pair_count") != len(protected)
            or receipt.get("literal_protected_pair_ids_sha256") != BODY.sha_bytes(BODY.canon(protected))
            or receipt.get("literal_protected_task_count") != derived["protected_task_count"]
            or receipt.get("protected_pairs_are_not_missing_model_responses") is not True):
        raise ValueError("frozen pre-model partition differs from exact source-derived pair membership")
    return receipt, {**derived, "partition_dir": partition_dir}


def validate_snapshot(raw_path: Path, receipt_path: Path, dispatch_ids: set[str],
                      protected_ids: set[str], partition_receipt_sha256: str) -> tuple[list[dict], list[dict], dict]:
    raw_path, receipt_path = Path(raw_path), Path(receipt_path)
    for original in (raw_path, receipt_path):
        path = original.resolve()
        if (original.is_symlink() or not path.is_relative_to(ROOT / ".lab-output")
                or not path.is_file()):
            raise ValueError("frozen first-pass snapshot and receipt must be regular .lab-output files")
    raw_path, receipt_path = raw_path.resolve(), receipt_path.resolve()
    receipt = read_json(receipt_path)
    if (receipt.get("schema_version") != "sku-next-body-invariant-dev10-literal-snapshot-v1"
            or receipt.get("status") != "complete_firstpass_snapshot_frozen"
            or receipt.get("all_predictions_frozen") is not True
            or receipt.get("labels_read") is not False or receipt.get("test_labels_read") is not False
            or receipt.get("scores_created") is not False or receipt.get("production_adoption_allowed") is not False
            or receipt.get("prepared_pair_count") != len(dispatch_ids) + len(protected_ids)
            or receipt.get("expected_dispatched_pair_count") != len(dispatch_ids)
            or receipt.get("expected_dispatched_pair_ids_sha256") != BODY.sha_bytes(BODY.canon(sorted(dispatch_ids)))
            or receipt.get("literal_protected_pair_count") != len(protected_ids)
            or receipt.get("literal_protected_pair_ids_sha256") != BODY.sha_bytes(BODY.canon(sorted(protected_ids)))
            or receipt.get("pair_partition_receipt_sha256") != partition_receipt_sha256
            or receipt.get("snapshot_path") != relpath(raw_path)):
        raise ValueError("body DEV10 literal snapshot receipt is incomplete or not bound to this prepared source")
    if pin(raw_path) != {"bytes": receipt.get("snapshot_bytes"), "sha256": receipt.get("snapshot_sha256")}:
        raise ValueError("body DEV10 immutable raw snapshot bytes/SHA differ from receipt")
    raw = read_rows(raw_path)
    response_ids = [r.get("pair_id") for r in raw]
    if (len(response_ids) != len(set(response_ids)) or not set(response_ids) <= dispatch_ids
            or receipt.get("response_count") != len(raw)
            or receipt.get("response_pair_ids_sha256") != BODY.sha_bytes(BODY.canon(sorted(response_ids)))):
        raise ValueError("literal snapshot has duplicate, protected, foreign, or unreceipted pair outputs")
    missing_ids = dispatch_ids - set(response_ids)
    failure_rel = Path(receipt.get("failure_ledger_path", ""))
    if failure_rel.is_absolute() or ".." in failure_rel.parts or not failure_rel.as_posix().startswith(".lab-output/"):
        raise ValueError("body DEV10 failure ledger path must be repo-relative under .lab-output")
    failures_path = ROOT / failure_rel
    if (not failures_path.is_file() or failures_path.is_symlink()
            or not failures_path.resolve().is_relative_to(ROOT / ".lab-output")):
        raise FileNotFoundError("body DEV10 failure ledger is required, including an empty one")
    if pin(failures_path) != {"bytes": receipt.get("failure_ledger_bytes"),
                              "sha256": receipt.get("failure_ledger_sha256")}:
        raise ValueError("body DEV10 failure ledger bytes/SHA differ from snapshot receipt")
    failures = read_rows(failures_path)
    fail_ids = [r.get("pair_id") for r in failures]
    if (len(fail_ids) != len(set(fail_ids)) or set(fail_ids) != missing_ids
            or any(r.get("status") != "missing_firstpass_response_unknown"
                   or r.get("model_judgment_recorded") is not False
                   or r.get("semantic_retry_performed") is not False for r in failures)):
        raise ValueError("missing literal model responses must be explicitly unknown without fabricated retry")
    if receipt.get("failure_count") != len(failures):
        raise ValueError("body DEV10 failure receipt count differs from dispatched first-pass responses")
    return raw, failures, receipt


def adapt_source_validated_pair(response: dict, pair: dict, payload: dict,
                                source_body: dict, source_root: Path) -> tuple[dict, list[dict]]:
    validation = BODY.validate_source_response(response, pair, payload, source_body, source_root)
    # Keep every model-authored semantic field byte-value equivalent; append
    # only a count of source references that the unchanged validator checked.
    judgments = []
    for decision in response["decisions"]:
        copied = dict(decision)
        copied["citations_verified"] = (2 * len(decision.get("target_fact_indices", []))
                                        + len(decision.get("body_quote_refs", [])))
        judgments.append(copied)
    validated = {"pair_id": pair["pair_id"], "cache_key": pair["cache_key"],
        "judgments": judgments, "source_citations_verified": validation.get("source_citations_verified") is True,
        "semantic_truth_verified": False, "sourceproof_certified": False,
        "production_adoption_allowed": False,
        "validated_source_citation_count": len(validation.get("citations", []))}
    if len(judgments) != len(pair["option_values"]):
        raise ValueError("body source validator returned an incomplete whole-option vector")
    return validated, validation.get("citations", [])


def assemble(prepared_dir: Path, raw_path: Path, snapshot_receipt_path: Path,
             proof_dir: Path, partition_dir: Path, source_root: Path, output_dir: Path) -> dict[str, Any]:
    if Path(prepared_dir).resolve() != PREPARED or Path(proof_dir).resolve() != PROOF:
        raise ValueError("assembly must use the canonical final R7 source and literal proof bundles")
    members, cases, rows, tasks, _requests = verify_dev10_cohort(prepared_dir)
    prepared = read_json(prepared_dir / "manifest.json")
    for name, digest in prepared.get("artifact_sha256", {}).items():
        path = prepared_dir / name
        if not path.is_file() or sha_file(path) != digest:
            raise ValueError(f"body source preparation artifact SHA differs: {name}")
    prepared_freeze = read_json(prepared_dir / "freeze.json")
    if (prepared.get("schema_version") != BODY.PROTOCOL
            or prepared.get("task_count") != EXPECTED_TASKS
            or prepared.get("row_count") != EXPECTED_ROWS
            or prepared.get("prepared_pair_count") != 141
            or prepared.get("preparation_error_count") != 0
            or prepared.get("labels_read") is not False
            or prepared.get("predictions_read") is not False
            or prepared.get("semantic_truth_verified") is not False
            or prepared.get("sourceproof_certified") is not False
            or prepared.get("scores_created") is not False):
        raise ValueError("final body-invariant source-only preparation is not a blind 141-pair DEV10 freeze")
    if (prepared_freeze.get("schema_version") != "sku-directional-choice-nli-v2"
            or prepared_freeze.get("selected_case_count") != EXPECTED_CASES
            or prepared_freeze.get("selected_row_count") != EXPECTED_ROWS
            or prepared_freeze.get("selected_task_count") != EXPECTED_TASKS
            or prepared_freeze.get("prepared_pair_count") != 141
            or prepared_freeze.get("ready_pair_count") != 141
            or prepared_freeze.get("source_fact_errors") != 0
            or prepared_freeze.get("labels_read") is not False
            or prepared_freeze.get("test_split_read") is not False
            or prepared_freeze.get("inference_run") is not False
            or prepared_freeze.get("scores_or_gate_outputs_created") is not False
            or prepared_freeze.get("production_adoption_allowed") is not False):
        raise ValueError("preserved NLI source freeze is not the blind complete 141-pair snapshot")
    if Path(prepared_freeze.get("source_root", "")).resolve() != source_root.resolve():
        raise ValueError("source validator root differs from the frozen R7 preparation")
    source_inventory = source_root.parent / "inventory.json"
    if sha_file(source_inventory) != read_json(COHORT / "manifest.json").get("source_bundle_inventory_sha256"):
        raise ValueError("validator raw-source inventory differs from the frozen DEV10 source manifest")
    prepared_pairs = read_rows(prepared_dir / "model-pairs-v3.jsonl")
    payloads = read_rows(prepared_dir / "model-payloads-v3.jsonl")
    source_bodies = read_rows(prepared_dir / "source-bodies.jsonl")
    pair_by_id = {r["pair_id"]: r for r in prepared_pairs}
    payload_by_id = {r["pair_id"]: r for r in payloads}
    body_by_sha = {r["document_sha256"]: r for r in source_bodies}
    if (len(pair_by_id) != 141 or len(payload_by_id) != 141 or len(body_by_sha) != 10
            or set(pair_by_id) != set(payload_by_id)):
        raise ValueError("body-invariant prepared pair/payload/document populations differ")
    partition_receipt, partition = validate_partition(partition_dir, prepared_dir, proof_dir)
    partition_ids = set(partition["protected_pair_ids"]) | set(partition["dispatch_pair_ids"])
    if partition_ids != set(pair_by_id):
        raise ValueError("pre-model source partition differs from exact prepared pair set")
    raw, failures, snapshot_receipt = validate_snapshot(raw_path, snapshot_receipt_path,
        set(partition["dispatch_pair_ids"]), set(partition["protected_pair_ids"]),
        sha_file(partition_dir / "pair-partition-receipt.json"))
    bindings = load_complete_bindings(tasks, prepared_dir)
    proof_manifest = read_json(proof_dir / "manifest.json")
    proof_states = read_rows(proof_dir / "task-literal-proof-states.jsonl")
    proof_receipts = read_rows(proof_dir / "task-source-verification.jsonl")
    literal_pair_proofs = read_rows(proof_dir / "literal-pair-proofs.jsonl")
    if (proof_manifest.get("schema_version") != "sku-next-body-invariant-literal-proofs-v1"
            or proof_manifest.get("selected_case_count") != EXPECTED_CASES
            or proof_manifest.get("row_count") != EXPECTED_ROWS
            or proof_manifest.get("task_count") != EXPECTED_TASKS
            or proof_manifest.get("selected_product_count") != EXPECTED_PRODUCTS
            or len(proof_states) != EXPECTED_TASKS or len(proof_receipts) != EXPECTED_TASKS
            or len(literal_pair_proofs) != proof_manifest.get("literal_pair_proof_count")
            or proof_manifest.get("all_452_tasks_have_exactly_one_source_receipt") is not True
            or proof_manifest.get("labels_read") is not False
            or proof_manifest.get("predictions_read") is not False
            or proof_manifest.get("model_inference_run") is not False
            or proof_manifest.get("scores_created") is not False
            or proof_manifest.get("test_split_read") is not False
            or proof_manifest.get("cohort_manifest_sha256") != sha_file(COHORT / "manifest.json")
            or proof_manifest.get("source_bundle_inventory_sha256") != read_json(COHORT / "manifest.json").get("source_bundle_inventory_sha256")):
        raise ValueError("frozen body DEV10 independent literal proof population is incomplete or scored")
    for name, digest in proof_manifest.get("outputs_sha256", {}).items():
        path = proof_dir / name
        if not path.is_file() or sha_file(path) != digest:
            raise ValueError(f"literal proof artifact SHA differs: {name}")
    proof_tasks = adapt_literal_proofs_for_eval(tasks, proof_states, proof_receipts, literal_pair_proofs)
    output_dir = output_dir.resolve()
    out_rel = relpath(output_dir)
    if not out_rel.startswith(".lab-output/") or output_dir.exists():
        raise FileExistsError("assembly output must be a new .lab-output directory; no overwrite")
    output_dir.mkdir(parents=True)
    try:
        shutil.copyfile(raw_path, output_dir / "raw-literal-responses.jsonl")
        write_json(output_dir / "literal-snapshot-receipt.json", snapshot_receipt)
        write_rows(output_dir / "missing-response-failures.jsonl", failures)
        shutil.copyfile(partition_dir / "dispatch-pair-ids.jsonl", output_dir / "dispatch-pair-ids.jsonl")
        shutil.copyfile(partition_dir / "literal-protected-pair-ids.jsonl", output_dir / "literal-protected-pair-ids.jsonl")
        shutil.copyfile(partition_dir / "literal-protected-no-dispatch.jsonl", output_dir / "literal-protected-no-dispatch.jsonl")
        shutil.copyfile(partition_dir / "pair-partition-receipt.json", output_dir / "pair-partition-receipt.json")
        shutil.copyfile(partition_dir / "partition-output-inventory.json", output_dir / "partition-output-inventory.json")
        write_rows(output_dir / "canonical-validation-bindings.jsonl", bindings)
        validated_pairs, validation_report, citations = [], [], []
        for response in raw:
            pid = response.get("pair_id")
            pair, payload = pair_by_id[pid], payload_by_id[pid]
            target_sha = payload["context"]["target_document_sha256"]
            source_body = body_by_sha[target_sha]
            try:
                validated, verified_citations = adapt_source_validated_pair(
                    response, pair, payload, source_body, source_root)
                validated_pairs.append(validated)
                citations.extend(verified_citations)
                validation_report.append({"pair_id": pid, "status": "source_citations_verified",
                    "decision_count": len(validated["judgments"]),
                    "validated_source_citation_count": validated["validated_source_citation_count"]})
            except (KeyError, TypeError, ValueError, OSError, JsonSchemaValidationError) as exc:
                validation_report.append({"pair_id": pid, "status": "invalid_unknown", "error": str(exc)})
        write_rows(output_dir / "source-validation-report.jsonl", validation_report)
        write_rows(output_dir / "validated-pairs-for-eval.jsonl", validated_pairs)
        write_rows(output_dir / "verified-citation-metadata.jsonl", citations)
        gates = {}
        for variant in ("body-invariant-v3", "literal-only"):
            variant_pairs = validated_pairs if variant == "body-invariant-v3" else []
            resolutions = EVAL.build_choice_nli_task_resolutions(
                tasks, bindings, variant_pairs, proof_tasks, proof_receipts)
            internal = "choice_nli_full" if variant == "body-invariant-v3" else "baseline_literal_overlay"
            gate = EVAL.finish_whole_sku_gate(internal, cases, rows, tasks, resolutions)
            gate["variant"] = variant
            gate["assembly_method"] = ("source_validated_body_invariant_model_plus_literal_proofs"
                if variant == "body-invariant-v3" else "independent_literal_proofs_only_unknown_elsewhere")
            gate["production_adoption_allowed"] = False
            if (len(resolutions) != EXPECTED_TASKS or len(gate["rows"]) != EXPECTED_ROWS
                    or len(gate["decisions"]) != EXPECTED_CASES):
                raise ValueError(f"frozen whole-SKU EVAL output coverage differs: {variant}")
            variant_dir = output_dir / variant
            variant_dir.mkdir()
            write_rows(variant_dir / "task-resolutions.jsonl", resolutions)
            write_rows(variant_dir / "all-row-results.jsonl", gate["rows"])
            write_rows(variant_dir / "case-decisions.jsonl", gate["decisions"])
            source_hashes = {"raw_literal_responses": sha_file(raw_path),
                "prepared_manifest": sha_file(prepared_dir / "manifest.json"),
                "validation_bindings": sha_file(prepared_dir / "validation-bindings.jsonl"),
                "literal_proof_manifest": sha_file(proof_dir / "manifest.json"),
                "tasks": sha_file(COHORT / "tasks.jsonl"), "case_plans": sha_file(COHORT / "case-plans.jsonl"),
                "row_plans": sha_file(COHORT / "row-plans.jsonl")}
            gm = EVAL.eval_manifest(internal, source_hashes,
                                    sha_file(variant_dir / "task-resolutions.jsonl"))
            gm.update({"variant": variant, "assembly_method": gate["assembly_method"],
                "source_split": "dev", "independent_test": False,
                "case_count": EXPECTED_CASES, "product_count": EXPECTED_PRODUCTS,
                "row_count": EXPECTED_ROWS, "task_count": EXPECTED_TASKS,
                "labels_read": False, "test_labels_read": False,
                "semantic_truth_verified": False, "sourceproof_certified": False,
                "production_adoption_allowed": False})
            write_json(variant_dir / "gate-manifest.json", gm)
            gates[variant] = {"task_state_counts": gate["task_state_counts"],
                "row_state_counts": gate["row_state_counts"], "decision_counts": gate["decision_counts"]}
        write_json(output_dir / "assembly-receipt.json", {
            "schema_version": "sku-next-body-invariant-dev10-assembly-v1",
            "status": "complete_both_whole_gate_variants_frozen", "variants": gates,
            "case_count": EXPECTED_CASES, "product_count": EXPECTED_PRODUCTS,
            "row_count": EXPECTED_ROWS, "task_count": EXPECTED_TASKS,
            "prepared_pair_count": len(pair_by_id), "raw_response_count": len(raw),
            "expected_dispatched_pair_count": len(partition["dispatch_pair_ids"]),
            "expected_dispatched_pair_ids_sha256": BODY.sha_bytes(BODY.canon(partition["dispatch_pair_ids"])),
            "literal_protected_pair_count": len(partition["protected_pair_ids"]),
            "literal_protected_pair_ids_sha256": BODY.sha_bytes(BODY.canon(partition["protected_pair_ids"])),
            "literal_protected_task_count": partition_receipt["literal_protected_task_count"],
            "pair_partition_receipt_sha256": sha_file(partition_dir / "pair-partition-receipt.json"),
            "missing_response_count": len(failures), "source_validated_pair_count": len(validated_pairs),
            "invalid_or_missing_response_handling": "unknown_no_synthetic_semantics_or_model_fallback",
            "semantic_decisions_edited": False, "source_citations_verified_only": True,
            "semantic_truth_verified": False, "sourceproof_certified": False,
            "labels_read": False, "test_labels_read": False, "scores_created": False,
            "production_adoption_allowed": False})
        files = [p for p in output_dir.rglob("*") if p.is_file() and p.name != "assembly-output-inventory.json"]
        write_json(output_dir / "assembly-output-inventory.json", {"schema_version": "body-dev10-output-inventory-v1",
            "files": {p.relative_to(output_dir).as_posix(): pin(p) for p in sorted(files)},
            "labels_read": False, "scores_created": False})
        return read_json(output_dir / "assembly-receipt.json")
    except Exception as exc:
        write_json(output_dir / "assembly-failure.json", {"schema_version": "body-dev10-assembly-failure-v1",
            "error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(),
            "partial_outputs_preserved": True, "labels_read": False, "scores_created": False})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    partition_cmd = sub.add_parser("partition", help="derive source-protected and model-dispatched pair IDs before inference")
    partition_cmd.add_argument("--prepared-dir", type=Path, required=True)
    partition_cmd.add_argument("--proof-dir", type=Path, required=True)
    partition_cmd.add_argument("--output-dir", type=Path, required=True)
    assemble_cmd = sub.add_parser("assemble", help="assemble frozen first-pass model responses into both whole gates")
    assemble_cmd.add_argument("--prepared-dir", type=Path, required=True)
    assemble_cmd.add_argument("--raw-responses", type=Path, required=True)
    assemble_cmd.add_argument("--snapshot-receipt", type=Path, required=True)
    assemble_cmd.add_argument("--proof-dir", type=Path, required=True)
    assemble_cmd.add_argument("--partition-dir", type=Path, required=True)
    assemble_cmd.add_argument("--source-root", type=Path, default=BODY.DEFAULT_SOURCE)
    assemble_cmd.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "partition":
        result = write_partition(args.prepared_dir, args.proof_dir, args.output_dir)
        print(json.dumps({"status": result["status"], "dispatch_pair_count": result["dispatch_pair_count"],
                          "literal_protected_pair_count": result["literal_protected_pair_count"],
                          "labels_read": False, "inference_run": False}, ensure_ascii=False))
    else:
        result = assemble(args.prepared_dir, args.raw_responses, args.snapshot_receipt,
                          args.proof_dir, args.partition_dir, args.source_root, args.output_dir)
        print(json.dumps({"status": result["status"], "variants": sorted(result["variants"]),
                          "labels_read": False, "scores_created": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
