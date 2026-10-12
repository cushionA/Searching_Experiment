#!/usr/bin/env python3
"""Freeze full source-context packets and evaluate source-bound model responses.

This runner makes no model calls. Inference is dispatched explicitly to the
declared hosted alias, with response and orchestration traces retained.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import composition_context_v1 as CONTEXT
import composition_context_policy_v1 as POLICY
import composition_language_model_v1 as LM
import composition_source_blocks_v3 as RESTORATION
import trial_composition_context_v1 as PARENT
import evaluate_keyword_filtered_product_v1 as REFERENCES
import evaluate_mixed_gpu_sku_v1 as E
import product_condition_sharing_v1 as SHARING
import trial_composition_evidence_v1 as T
import trial_keyword_filtered_product_v1 as VERIFY

ROOT = T.ROOT
MAIN = ROOT / ".lab-output/sku-composition-evidence-20261013-v1"
REVALID = ROOT / ".lab-output/sku-composition-source-revalidation-20261013-v2"
PREVIOUS = ROOT / ".lab-output/sku-composition-context-20261013-v1"
BASE = PREVIOUS / "evaluation/complete-context-fallback"
RESTORED = ROOT / ".lab-output/sku-composition-source-restoration-20261013-v3-v2"
CONTENT_VERSION = CONTEXT.CONTENT_VERSION + "/restored-source-v3-v2"
DEFAULT_OUT = ROOT / ".lab-output/sku-restored-composition-context-20261013-v1"
POLICIES = ("complete-restored-context-fallback", "restored-context-replace")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def verify_summary(directory):
    summary = read(directory / "summary.json")
    for name, expected in summary["output_sha256"].items():
        if E.sha(directory / name) != expected:
            raise ValueError(f"frozen output changed: {directory / name}")
    return summary


def ordered_source_block(block, store):
    """Use the verified source leaf span as ordering metadata for text spans."""
    if type(block.get("html_start")) is int and type(block.get("html_end")) is int:
        return block
    span = block.get("source_ref")
    if (block.get("kind") != "description_text_span" or not isinstance(span, dict)
            or not RESTORATION.SOURCES.verify_span(store, span)):
        raise ValueError("restored source lacks verified ordering coordinates")
    result = deepcopy(block)
    result["html_start"], result["html_end"] = span["start"], span["end"]
    return result


def prepare(output_dir):
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    verify_summary(MAIN)
    verify_summary(PREVIOUS / "evaluation")
    PARENT.verified_assembly_snapshot(PREVIOUS, read(PREVIOUS / "freeze.json"))
    validation = read(REVALID / "validation.json")
    if not validation["v1_model_inputs_unchanged"] or not validation["all_accepted_spans_verified"]:
        raise ValueError("source span revalidation is incomplete")
    _, requests, documents, tasks, _, _, _ = E.load_mixed_prepared(T.PREP)
    baseline = E.GATE.indexed(BASE / "predictions.jsonl", "request_id")
    E.GATE.validate_predictions(requests, documents, tasks, baseline, baseline, "baseline")
    units = E.GATE.indexed(MAIN / "classification-units.jsonl", "group_id")
    blocks = E.GATE.indexed(MAIN / "source-blocks.jsonl", "block_id")
    for block in E.rows(REVALID / "source-blocks.jsonl"):
        old = blocks[block["block_id"]]
        if old["model_passage"] != block["model_passage"] or old["quote_refs"] != block["quote_refs"]:
            raise ValueError("revalidated source contents changed")
        blocks[block["block_id"]] = block
    restored = read(RESTORED / "manifest.json")
    if restored["schema_version"] != "sku-composition-source-restoration-v3-v2":
        raise ValueError("restoration schema differs")
    for name, expected in restored["output_sha256"].items():
        if E.sha(RESTORED / name) != expected:
            raise ValueError("restored source output changed: " + name)
    for name, expected in restored["code_sha256"].items():
        if E.sha(Path(name)) != expected:
            raise ValueError("restoration source code changed: " + name)
    raw_paths = []
    store = RESTORATION.BundleStore(T.PREP)
    for pin in E.rows(RESTORED / "raw-pins.jsonl"):
        body = store.raw(pin["raw_file"], pin["sha256"])
        if len(body) != pin["bytes"]:
            raise ValueError("restoration raw size differs")
        raw_paths.append(T.PREP / pin["bundle_file"])
    for block in E.rows(RESTORED / "source-block-additions.jsonl"):
        if block["block_id"] in blocks:
            raise ValueError("restored block duplicates original identity")
        blocks[block["block_id"]] = ordered_source_block(block, store)
    by_document = defaultdict(list)
    for block in blocks.values():
        by_document[block["target_document_id"]].append(block)
    packets, empty = [], []
    for uid, unit in sorted(units.items()):
        packet = CONTEXT.build_packet(unit, by_document[unit["target_document_id"]])
        if packet is None:
            empty.append({"unit_id": uid, "winning_value": None, "evidence_block_ids": [],
                          "reason": "出典を検証できる本文ブロックがないため不明。"})
            continue
        for block in packet["blocks"]:
            if T.literal_block_refs(block, unit["target_document_id"], documents) is None:
                raise ValueError("full context contains an invalid document quote")
        packets.append(packet)
    output_dir.mkdir()
    (output_dir / "worker-responses").mkdir()
    E.write_rows(output_dir / "packets.jsonl", packets)
    E.write_rows(output_dir / "empty-unit-responses.jsonl", empty)
    protocol = CONTEXT.PROTOCOL
    E.write_json(output_dir / "protocol.json", {
        "protocol": protocol, "protocol_sha256": hashlib.sha256(protocol.encode()).hexdigest(),
        "policies": list(POLICIES), "requested_model": "gpt-6-luna",
        "hosted_weights_revision_verified": False, "probabilities_available": False,
        "labels_or_prior_predictions_in_model_inputs": False,
        "independent_test": False, "context": "all eligible source blocks, no ranking or truncation",
        "quote_refs": "retained in full packets; model sees exact passages and block IDs without duplicate locator text"})
    payloads = [CONTEXT.model_payload(p) for p in packets]
    E.write_rows(output_dir / "model-payloads.jsonl", payloads)
    for i in range(4):
        E.write_json(output_dir / f"worker-{i}.json", {"protocol": protocol, "packets": payloads[i::4]})
    paths = [Path(__file__), Path(CONTEXT.__file__), Path(POLICY.__file__), Path(LM.__file__), Path(PARENT.__file__), Path(RESTORATION.__file__),
             RESTORED / "manifest.json", RESTORED / "source-block-additions.jsonl",
             RESTORED / "span-receipts.jsonl", RESTORED / "raw-pins.jsonl", *raw_paths,
             MAIN / "classification-units.jsonl", MAIN / "source-blocks.jsonl",
             REVALID / "source-blocks.jsonl", REVALID / "validation.json", REVALID / "span-receipts.jsonl",
             PREVIOUS / "evaluation/summary.json", BASE / "predictions.jsonl",
             BASE / "gates/decisions.jsonl", output_dir / "packets.jsonl",
             output_dir / "model-payloads.jsonl", output_dir / "empty-unit-responses.jsonl",
             output_dir / "protocol.json", *(output_dir / f"worker-{i}.json" for i in range(4))]
    E.write_json(output_dir / "freeze.json", {
        "schema_version": "restored-composition-context-inputs-v1", "experiment_status": "exploratory_dev_informed",
        "labels_read": False, "independent_test": False, "production_adoption_allowed": False,
        "unit_count": len(units), "classification_unit_count": len(packets), "no_evidence_unit_count": len(empty),
        "worker_count": 4, "policies": list(POLICIES), "requested_model": "gpt-6-luna",
        "hosted_weights_revision_verified": False,
        "raw_options_unmodified": True, "shared_selected_axes_in_inputs": False,
        "title_field_in_inputs": False, "selected_value_in_inputs": False,
        "source_snapshot": VERIFY.snapshot_paths({str(p): p for p in paths}),
        "new_acceptance_contract": "same row, all original conditions must have new model support or upstream identity; retain previous accepted cases unless explicit different whole option"})
    return {"units": len(units), "packets": len(packets), "empty": len(empty),
            "passage_characters": sum(len(b["model_passage"]) for p in packets for b in p["blocks"]),
            "model_input_bytes": (output_dir / "model-payloads.jsonl").stat().st_size}


def evaluate(output_dir):
    output_dir = Path(output_dir).resolve()
    out = output_dir / "evaluation"
    if out.exists():
        raise FileExistsError(out)
    freeze = read(output_dir / "freeze.json")
    if freeze["policies"] != list(POLICIES):
        raise ValueError("undeclared evaluation policy")
    assembly_snapshot = freeze["source_snapshot"]
    VERIFY.verify_snapshot(assembly_snapshot, phase="before restored context assembly")
    verify_summary(PREVIOUS / "evaluation")
    VERIFY.verify_source(T.CPU / "product-model")
    _, requests, documents, tasks, cases, membership, hosts = E.load_mixed_prepared(T.PREP)
    baseline = E.GATE.indexed(BASE / "predictions.jsonl", "request_id")
    E.GATE.validate_predictions(requests, documents, tasks, baseline, baseline, "baseline")
    units = E.GATE.indexed(MAIN / "classification-units.jsonl", "group_id")
    packets = E.GATE.indexed(output_dir / "packets.jsonl", "unit_id")
    responses = {}
    response_paths = []
    for i in range(freeze["worker_count"]):
        assigned = {p["unit_id"] for p in read(output_dir / f"worker-{i}.json")["packets"]}
        path = output_dir / f"worker-responses/worker-{i}.json"
        result = read(path)
        if set(result) != {"responses"}:
            raise ValueError("unexpected worker response fields")
        received = result["responses"]
        if len(received) != len(assigned) or {r["unit_id"] for r in received} != assigned:
            raise ValueError(f"worker {i} response coverage differs")
        for response in received:
            if response["unit_id"] in responses:
                raise ValueError("duplicate worker response")
            responses[response["unit_id"]] = response
        response_paths.append(path)
    if set(responses) != set(packets):
        raise ValueError("response and frozen packet coverage differ")
    results, invalid = {}, []
    for uid, packet in packets.items():
        try:
            results[uid] = LM.validate_response(packet, responses[uid],
                                               requests[uid] if packet["kind"] == "paired" else None,
                                               documents)
        except ValueError as error:
            invalid.append({"unit_id": uid, "reason": str(error), "raw_response": responses[uid]})
            results[uid] = {"unit_id": uid, "status": "unknown", "winning_value": None,
                            "quote_refs": [], "reason": "invalid_source_bound_model_response",
                            "source_model_response": responses[uid], "scope_status": "unproved",
                            "production_adoption_allowed": False}
    for response in E.rows(output_dir / "empty-unit-responses.jsonl"):
        results[response["unit_id"]] = {"unit_id": response["unit_id"], "status": "unknown",
                                       "winning_value": None, "quote_refs": [], "reason": response["reason"],
                                       "source_model_response": response, "scope_status": "unproved",
                                       "production_adoption_allowed": False}
    if set(results) != set(units):
        raise ValueError("validated unit coverage differs")
    trace = read(output_dir / "dispatch-trace.json")
    if (trace["dispatcher"] != "collaboration.spawn_agent" or trace["model_override"] != "gpt-6-luna"
            or trace["hosted_weights_revision_verified"] is not False or len(trace["agents"]) != freeze["worker_count"]):
        raise ValueError("missing explicit model dispatch provenance")
    protocol = read(output_dir / "protocol.json")
    identity = {"model_id": "gpt-6-luna", "revision": "hosted-alias-revision-unresolved/20261013",
                "hosted_weights_revision_verified": False, "identity_basis": "explicit orchestrator model override",
                "probabilities_available": False, "protocol_sha256": protocol["protocol_sha256"],
                "validator_sha256": E.sha(Path(LM.__file__)), "context_version": CONTENT_VERSION,
                "input_scope": "all eligible global passages; exact full selected axes only for paired units"}
    contract = read(MAIN / "sharing-contract.json")
    plan = SHARING.build_groups(requests, documents, {rid: p["source_task_bindings"] for rid, p in baseline.items()},
                               model_identity=identity, content_version=CONTENT_VERSION,
                               invariance_contract=contract)
    eligible = set(plan["request_to_group"])
    old_plan = read(MAIN / "sharing-plan.json")
    old_ids = {tuple(sorted(group["request_ids"])): group["group_id"] for group in old_plan["groups"]}
    shared = {}
    for group in plan["groups"]:
        uid = old_ids[tuple(sorted(group["request_ids"]))]
        unit = units[uid]
        if unit["option_values"] != group["option_values"] or unit["target_document_id"] != group["target_document_id"]:
            raise ValueError("sharing scope or raw option set changed")
        result = results[uid]
        shared[group["group_id"]] = {
            "group_sha256": group["group_sha256"], "status": "ok" if result["winning_value"] else "unknown",
            "winning_value": result["winning_value"], "quote_refs": result["quote_refs"],
            "reason": "source_bound_full_context_choice", "source_unit_id": uid,
            "source_model_response": result["source_model_response"], "variant": CONTENT_VERSION}
    derived = {p["request_id"]: p for p in SHARING.fan_out(plan, shared, baseline)}
    E.SHARED.validate_shared_predictions(requests, documents, tasks, derived, baseline, eligible, "product-top3-jev")
    for uid, unit in units.items():
        if unit["kind"] == "paired":
            derived[uid] = T.paired_prediction(requests[uid], baseline[uid], results[uid])
            derived[uid]["variant"] = CONTENT_VERSION
    proof_ids = {rid for rid, pred in baseline.items() if pred.get("verified_value_equivalence")}
    if len(proof_ids) != 1 or len(eligible) != 80:
        raise ValueError("proof or shared request coverage differs")
    E.GATE.validate_predictions(requests, documents, tasks, derived, baseline, "baseline")
    out.mkdir()
    E.write_rows(out / "validated-unit-results.jsonl", results.values())
    E.write_rows(out / "invalid-model-responses.jsonl", invalid)
    E.write_rows(out / "derived-predictions.jsonl", derived.values())
    E.write_json(out / "sharing-plan.json", plan)
    source_paths = [*response_paths, output_dir / "dispatch-trace.json", output_dir / "freeze.json"]
    if (output_dir / "assembly-freeze.json").exists():
        source_paths.append(output_dir / "assembly-freeze.json")
    snapshot = VERIFY.snapshot_paths({str(p): p for p in source_paths})
    E.write_json(out / "execution-freeze.json", {
        "experiment_status": "exploratory_dev_informed", "labels_read": False, "independent_test": False,
        "production_adoption_allowed": False, "policies": list(POLICIES), "model_identity": identity,
        "source_snapshot": snapshot, "new_model_inference": True, "baseline": str(BASE),
        "all_unit_responses_frozen": True, "invalid_response_count": len(invalid)})
    baseline_gates = E.GATE.indexed(BASE / "gates/decisions.jsonl", "case_id")
    for policy in POLICIES:
        destination = out / policy
        destination.mkdir()
        if policy == "complete-restored-context-fallback":
            predicted, audit = POLICY.complete_predictions(baseline, derived, requests,
                                                           list(E.rows(T.PREP / "row_plans.jsonl")),
                                                           baseline_gates, proof_ids)
            E.write_json(destination / "complete-row-contract.json", audit)
        else:
            predicted = deepcopy(derived)
            for rid in proof_ids:
                predicted[rid] = deepcopy(baseline[rid])
        E.GATE.validate_predictions(requests, documents, tasks, predicted, baseline, "baseline")
        if any(predicted[rid] != baseline[rid] for rid in proof_ids):
            raise ValueError("typed raw value proof changed")
        if any(ref.get("field") == "selected_axes" for rid in eligible for ref in predicted[rid].get("quote_refs", [])):
            raise ValueError("shared unit leaked selected-axis context")
        E.write_rows(destination / "predictions.jsonl", predicted.values())
        E.GATE.finish(T.PREP, destination / "predictions.jsonl", destination / "gates")
    gate_snapshot = VERIFY.snapshot_paths({str(p): p for p in out.rglob("*") if p.is_file()})
    E.write_json(out / "all-gates-frozen.json", {
        "labels_read": False, "all_predictions_and_gates_frozen": True,
        "policies": list(POLICIES), "source_snapshot": gate_snapshot})
    _, _, _, references, _ = REFERENCES.verify_original_sources(T.PREP, T.OLD_EVAL, T.ROUTES)
    ids = {r["case_id"] for r in E.eligibility_rows(membership, hosts)[0] if r["primary_score_eligible"]}
    scored = {k: v for k, v in references.items() if k in ids and not v.get("reference_uncertain", False)}
    if len(scored) != 73:
        raise ValueError("development reference cohort changed")
    before = {r["case_id"]: r for r in E.metrics_for(baseline_gates, scored)["case_results"]}
    metrics = {}
    for policy in POLICIES:
        destination = out / policy
        gates = E.GATE.indexed(destination / "gates/decisions.jsonl", "case_id")
        if set(gates) != set(cases):
            raise ValueError("case coverage differs")
        metrics[policy], detail = {}, []
        for stratum in E.STRATA:
            value = E.metrics_for(gates, {k: v for k, v in scored.items()
                                         if membership[k]["membership_strata"] == [stratum]})
            metrics[policy][stratum] = {k: v for k, v in value.items() if k != "case_results"}
            detail.extend({"stratum": stratum, **r} for r in value["case_results"])
        value = E.metrics_for(gates, scored)
        metrics[policy]["scored_primary_reference_union"] = {k: v for k, v in value.items() if k != "case_results"}
        E.write_rows(destination / "case-results.jsonl", detail)
        E.write_rows(destination / "case-changes-vs-previous.jsonl", [
            {"case_id": r["case_id"], "before": before[r["case_id"]], "after": r}
            for r in value["case_results"] if r != before[r["case_id"]]])
    for value in (assembly_snapshot, snapshot, gate_snapshot):
        VERIFY.verify_snapshot(value, phase="after full context scoring")
    VERIFY.verify_source(T.CPU / "product-model")
    E.write_json(out / "summary.json", {
        "schema_version": "restored-composition-context-evaluation-v1", "model_identity": identity,
        "experiment_status": "exploratory_dev_informed", "independent_test": False,
        "production_adoption_allowed": False, "model_input_unit_count": len(packets),
        "no_evidence_unit_count": len(results) - len(packets), "scored_case_count": len(scored),
        "all_case_count": len(cases), "invalid_model_response_count": len(invalid),
        "reference_kind": "existing machine development labels", "policies": metrics,
        "labels_read_after_all_predictions_and_gates_frozen": True,
        "sources_and_outputs_verified_after_scoring": True,
        "output_sha256": {str(p.relative_to(out)): E.sha(p) for p in out.rglob("*") if p.is_file()}})
    return {k: v["scored_primary_reference_union"] for k, v in metrics.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "evaluate"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    actions = {"prepare": prepare, "evaluate": evaluate}
    result = actions[args.action](args.output_dir)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
