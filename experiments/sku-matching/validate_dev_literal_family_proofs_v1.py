#!/usr/bin/env python3
"""Fail-closed validator for the source-only selected DEV literal proof bundle."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-next-dev-literal-family-proofs-20261013-v1"
DEFAULT_GATE_INPUTS = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8"
DEFAULT_SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
DEFAULT_SOURCE_BUNDLE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
sys.path.insert(0, str(ROOT / "experiments/sku-matching/claude_v9_gate"))
import sku_gate_sources as sources  # noqa: E402


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


def index(rows, key, label):
    result = {}
    for row in rows:
        identity = row.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError("missing or duplicate " + label + " identity")
        result[identity] = row
    return result


def assert_no_untrusted_producer_fields(row):
    forbidden = {"producer_condition", "producer_row_state", "case_decision", "core", "residual",
                 "pending_description_check_candidate", "heuristic_status_is_untrusted_hint"}
    if forbidden.intersection(row):
        raise ValueError("untrusted v7 heuristic fields leaked into literal proof status")


def verify_raw_span(store, span):
    span = dict(span)
    if isinstance(span.get("raw_file"), str):
        span["raw_file"] = span["raw_file"].replace("\\", "/")
    if not sources.verify_span(store, span):
        raise ValueError("raw source span receipt fails source SHA/offset/quote validation")


def validate(output: Path = DEFAULT_OUTPUT, gate_inputs: Path = DEFAULT_GATE_INPUTS,
             source_dir: Path = DEFAULT_SOURCE, source_bundle: Path = DEFAULT_SOURCE_BUNDLE) -> dict:
    manifest = read_json(output / "manifest.json")
    if (manifest.get("phase") != "source_only_raw_span_proofs_complete"
            or manifest.get("labels_read") is not False or manifest.get("test_split_read") is not False
            or manifest.get("predictions_read") is not False or manifest.get("model_inference_run") is not False
            or manifest.get("whole_gate_evaluation_run") is not False
            or manifest.get("v7_hints_used_as_proof") is not False):
        raise ValueError("literal proof bundle does not satisfy the label-blind source-only phase")
    canonical_hashes = manifest.get("canonical_output_hashes")
    if manifest.get("canonical_output_hashes_finalized") is not True or not isinstance(canonical_hashes, dict):
        raise ValueError("literal proof manifest lacks its canonical output path/bytes/SHA map")
    for name, pin in canonical_hashes.items():
        path = output / name
        if (not path.is_file() or path.stat().st_size != pin.get("bytes")
                or sha_file(path) != pin.get("sha256")):
            raise ValueError("canonical literal proof output hash differs: " + name)
    for alias, lifecycle in manifest.get("historical_hash_alias_lifecycle", {}).items():
        if lifecycle.get("state") != "superseded_by_canonical_output_file" or not lifecycle.get("canonical_path"):
            raise ValueError("stale historical hash alias lacks an explicit lifecycle note: " + alias)
    for rel, digest in manifest.get("source_code_pins", {}).items():
        if sha_file(ROOT / rel) != digest:
            raise ValueError("literal proof/evaluator source code pin differs: " + rel)
    policy = read_json(output / "gate-policy.json")
    if (policy.get("review_state") != "root_review_required_before_any_model_or_baseline_evaluation"
            or policy.get("labels_read") is not False or policy.get("test_split_read") is not False
            or policy.get("model_inference_run") is not False or policy.get("whole_gate_evaluation_run") is not False
            or policy.get("variants", {}).get("choice_nli_full", {}).get("axis_name_rule")
               != "Do not require equal cross-store axis-name strings for semantic support/conflict. Exact-name matching is required only inside strict literal-family proof generation."):
        raise ValueError("gate policy draft violates reviewed source-only evaluation constraints")
    input_manifest_path = output / "inputs/manifest.json"
    input_manifest = read_json(input_manifest_path)
    if (manifest.get("inputs_manifest_sha256") != sha_file(input_manifest_path)
            or input_manifest.get("labels_read") is not False
            or input_manifest.get("test_split_read") is not False
            or input_manifest.get("predictions_read") is not False
            or input_manifest.get("v7_hints_read") is not False
            or input_manifest.get("model_inference_run") is not False):
        raise ValueError("literal proof inputs manifest is unbound or declares forbidden inputs")
    gate_manifest = read_json(gate_inputs / "manifest.json")
    if (input_manifest.get("source_gate_manifest_sha256") != sha_file(gate_inputs / "manifest.json")
            or input_manifest.get("adapter_code_sha256") != gate_manifest.get("adapter_code_sha256")
            or input_manifest.get("source_full_condition_manifest_sha256") != sha_file(source_dir / "manifest.json")):
        raise ValueError("literal proof inputs differ from frozen selected gate or full-condition source")
    for name, digest in input_manifest.get("output_sha256", {}).items():
        if sha_file(output / "inputs" / name) != digest:
            raise ValueError("literal proof source-input SHA differs: " + name)

    proof_dir = output / "proof-bundle"
    proof_manifest = read_json(proof_dir / "manifest.json")
    if (proof_manifest.get("proof_version") != "literal_complete_ordered_family_v1"
            or proof_manifest.get("labels_read") is not False
            or proof_manifest.get("predictions_read") is not False
            or proof_manifest.get("model_inference_run") is not False
            or proof_manifest.get("unknown_is_not_conflict") is not True
            or proof_manifest.get("task_count") != 1924
            or proof_manifest.get("row_plan_count") != 504
            or sha_file(proof_dir / "proofs.jsonl") != proof_manifest.get("proofs_sha256")):
        raise ValueError("unchanged exact-family proof bundle manifest or proof hash is invalid")
    for name, pin in proof_manifest.get("input_pins", {}).items():
        path = Path(name) if Path(name).is_absolute() else output / "inputs" / name
        if sha_file(path) != pin.get("sha256"):
            raise ValueError("literal proof input/code/source pin differs: " + name)

    tasks = index(read_rows(output / "inputs/tasks.jsonl"), "task_id", "source task")
    requests = index(read_rows(gate_inputs / "requests.jsonl"), "request_id", "source request")
    if set(tasks) != {row["task_id"] for row in read_rows(gate_inputs / "tasks.jsonl")}:
        raise ValueError("literal proof input task IDs differ from original full-condition gate tasks")
    proofs = read_rows(proof_dir / "proofs.jsonl")
    by_task = {}
    for proof in proofs:
        if proof.get("status") not in {"support", "conflict", "unknown"}:
            raise ValueError("literal proof row has unsupported relation state")
        for task_id in (proof.get("forward_task_id"), proof.get("reverse_task_id")):
            if task_id:
                if task_id not in tasks:
                    raise ValueError("literal proof references a foreign source task")
                by_task.setdefault(task_id, []).append(proof)

    source_receipts = index(read_rows(output / "task-source-verification.jsonl"), "task_id", "task source receipt")
    if set(source_receipts) != set(tasks):
        raise ValueError("per-task raw-source receipts do not cover every original task exactly once")
    raw_store = sources.RawStore(source_bundle)
    for task_id, task in tasks.items():
        receipt = source_receipts[task_id]
        if (receipt.get("status") != "verified_complete_ordered_raw_family"
                or receipt.get("case_id") != task.get("case_id")
                or receipt.get("au_row_key") != task.get("au_row_key")
                or receipt.get("direction") != task.get("direction")
                or receipt.get("selected_value") != task.get("raw_condition_ref", {}).get("value")
                or receipt.get("axis_name") != task.get("raw_condition_ref", {}).get("axis_name")
                or not isinstance(receipt.get("ordered_family"), list)
                or receipt["ordered_family"].count(receipt.get("selected_value")) != 1):
            raise ValueError("per-task source raw-family receipt differs from exact source task")
        if (receipt.get("family_value_span_count") != len(receipt["ordered_family"])
                or len(receipt.get("raw_span_receipts", [])) != len(receipt["ordered_family"])):
            raise ValueError("per-task source receipt omits one or more complete family members")
        verify_raw_span(raw_store, receipt["axis_label_span"])
        verify_raw_span(raw_store, receipt["selected_value_span"])
        for span in receipt["raw_span_receipts"]:
            verify_raw_span(raw_store, span)
        for proof_row in by_task.get(task_id, []):
            if proof_row.get("case_id") != task.get("case_id") or proof_row.get("au_row_key") != task.get("au_row_key"):
                raise ValueError("literal proof pair changed fixed source case/row identity")

    task_by_request = {}
    for task in tasks.values():
        task_by_request.setdefault(task["request_id"], []).append(task)
    status_rows = read_rows(output / "request-proof-statuses.jsonl")
    status_map = index(status_rows, "request_id", "request proof status")
    if set(status_map) != set(requests) or set(task_by_request) != set(requests):
        raise ValueError("request proof statuses do not cover every selected original request")
    for request_id, status in status_map.items():
        request = requests[request_id]
        bound = task_by_request[request_id]
        if (status.get("source_task_count") != len(bound)
                or set(status.get("source_task_ids", [])) != {task["task_id"] for task in bound}
                or status.get("selected_value") != request.get("selected_value")
                or status.get("option_values") != request.get("option_values")
                or status.get("direction") != request.get("direction")):
            raise ValueError("request proof status task fanout or complete source claim differs")
        if status.get("eligible") is True:
            if status.get("status") not in {"support", "conflict"} or status.get("proof_pair_count") != len(bound):
                raise ValueError("eligible literal proof is incomplete or unknown")
            if status.get("status") == "support" and status.get("winning_value") != request.get("selected_value"):
                raise ValueError("literal support winner differs from original selected value")
            if status.get("status") == "conflict" and status.get("winning_value") is not None:
                raise ValueError("literal conflict must not manufacture a competing winning value")
        elif status.get("status") != "unknown" or status.get("winning_value") is not None:
            raise ValueError("incomplete or nonunanimous proof was promoted beyond unknown")
        assert_no_untrusted_producer_fields(status)

    task_proof_states = index(read_rows(output / "task-proof-states.jsonl"), "task_id", "task proof state")
    if set(task_proof_states) != set(tasks):
        raise ValueError("task-level literal states do not cover every original task")
    task_state_counts = {}
    for task_id, state in task_proof_states.items():
        if state.get("status") not in {"support", "conflict", "unknown"}:
            raise ValueError("task proof state is outside support/conflict/unknown")
        if state.get("literal_proof_verified") != (state.get("status") in {"support", "conflict"}):
            raise ValueError("task proof state proof flag disagrees with its relation")
        if (state.get("raw_source_family_verified") is not True
                or state.get("ordered_family") != source_receipts[task_id].get("ordered_family")):
            raise ValueError("task proof state lacks its exact independently verified source family")
        task_state_counts[state["status"]] = task_state_counts.get(state["status"], 0) + 1
        assert_no_untrusted_producer_fields(state)

    protected = read_rows(output / "protected-nli-contexts.jsonl")
    for context in protected:
        claims = [status_map[rid] for rid in context.get("request_ids", [])]
        if (not claims or any(not claim.get("eligible") for claim in claims)
                or len({(claim["direction"], claim["axis_name"], claim["target_document_id"],
                         claim["target_document_sha256"], tuple(claim["option_values"]),
                         json.dumps(claim["target_selected_axes"], ensure_ascii=False, sort_keys=True))
                        for claim in claims}) != 1
                or context.get("statuses_may_differ_across_desired_bindings") is not True
                or context.get("skip_nli_context_if_frozen_inference_map_matches") is not True):
            raise ValueError("protected model context combines conflicting or incomplete source requests")
        assert_no_untrusted_producer_fields(context)
    nli_task_map = index(read_rows(output / "nli-v2-task-protection.jsonl"), "task_id", "NLI v2 task protection")
    nli_contexts = index(read_rows(output / "nli-v2-context-protection.jsonl"), "pair_id", "NLI v2 context protection")
    if set(nli_task_map) != set(tasks) or len(nli_contexts) != 543:
        raise ValueError("NLI v2 source mapping does not cover the frozen pair/task population")
    for task_id, mapped in nli_task_map.items():
        state = task_proof_states[task_id]
        if (mapped.get("literal_status") != state["status"]
                or mapped.get("skip_task_classification") != state["literal_proof_verified"]
                or mapped.get("option_values") != source_receipts[task_id]["ordered_family"]):
            raise ValueError("NLI v2 task map disagrees with the source-bound task proof state")
    for pair_id, mapped in nli_contexts.items():
        if (mapped.get("skip_classification")
                != mapped.get("all_bound_tasks_have_verified_literal_state")
                or mapped.get("statuses_may_differ_across_desired_bindings") is not True):
            raise ValueError("NLI context protection rule is incomplete or requires uniform desired-state")
        expected_skip = all(nli_task_map[tid]["skip_task_classification"] for tid in mapped["task_ids"])
        if expected_skip != mapped["skip_classification"]:
            raise ValueError("NLI context skip status differs from every bound task proof")
    nli_pins = manifest.get("nli_v2_preparation_pins", {})
    nli_dir = ROOT / ".lab-output/sku-next-dev-choice-nli-20261013-v2"
    for name, digest in nli_pins.items():
        if sha_file(nli_dir / name) != digest:
            raise ValueError("NLI v2 frozen source preparation pin differs: " + name)
    return {"case_count": input_manifest.get("selected_case_count"),
            "row_count": len({(x["case_id"], x["au_row_key"]) for x in tasks.values()}),
            "task_count": len(tasks), "proof_pair_count": len(proofs),
            "task_source_verification_count": len(source_receipts),
            "task_source_family_status_counts": {"verified_complete_ordered_raw_family": len(source_receipts)},
            "task_literal_status_counts": task_state_counts,
            "request_count": len(requests),
            "eligible_request_status_counts": manifest.get("eligible_request_status_counts"),
            "unknown_request_status_count": manifest.get("unknown_request_status_count"),
            "protected_nli_context_count": len(protected), "labels_read": False,
            "whole_gate_evaluation_run": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gate-inputs", type=Path, default=DEFAULT_GATE_INPUTS)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--source-bundle", type=Path, default=DEFAULT_SOURCE_BUNDLE)
    args = parser.parse_args()
    print(json.dumps(validate(args.output.resolve(), args.gate_inputs.resolve(), args.source_dir.resolve(),
                             args.source_bundle.resolve()),
                     ensure_ascii=False, sort_keys=True))
