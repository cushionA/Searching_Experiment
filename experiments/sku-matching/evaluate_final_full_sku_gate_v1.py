#!/usr/bin/env python3
"""Blindly assemble fresh-baseline, literal-proof and full-context test gates.

This runner is intentionally not invoked by its authoring task. It requires a
separate root-controlled freeze/go step and the frozen CPU baseline plus exact
216-unit Luna dispatch receipt. It never opens test labels or scores results.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import composition_context_policy_v1 as POLICY
import composition_context_v1 as CONTEXT
import composition_language_model_v1 as LM
import integrate_claude_description_v1 as GATE
import trial_composition_evidence_v1 as T

BASE = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1"
PREP = BASE / "gate-prepared-v2"
SELECTION = BASE / "inference-selection-v2"
SOURCE = BASE / "composition-source-only"
PROOFS = ROOT / ".lab-output/sku-final-literal-family-proofs-40-20261013-v1"
BASELINE = ROOT / ".lab-output/sku-final-cpu-baseline-20261013-v1"
OUT = BASE / "evaluation-v1"


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


def write_json(path: Path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def write_rows(path: Path, values):
    with path.open("x", encoding="utf-8") as f:
        for value in values:
            f.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False) + "\n")


def indexed(rows, key, label):
    result = {}
    for row in rows:
        if row.get(key) in result:
            raise ValueError("duplicate " + label + " identity")
        result[row[key]] = row
    return result


def enrich_baseline(predictions, requests):
    """Bind fresh model rows to exact original requests and source tasks."""
    predicted = indexed(predictions, "request_id", "baseline request")
    if set(predicted) != set(requests):
        raise ValueError("fresh baseline request coverage differs from full subset")
    enriched = {}
    for rid, req in requests.items():
        row = deepcopy(predicted[rid])
        if (row.get("request_sha256") != req["request_sha256"]
                or row.get("selected_value") != req["selected_value"]):
            raise ValueError("fresh baseline does not bind the original condition")
        if row.get("state") not in {"support", "conflict", "unknown"}:
            raise ValueError("fresh baseline state is outside the final gate contract")
        winner = row.get("winning_value")
        if winner is not None and winner not in req["option_values"]:
            raise ValueError("fresh baseline winner is not a whole original option")
        raw_bindings = req["source_task_bindings"]
        if not raw_bindings:
            raise ValueError("final request has no original source task binding")
        for key, value in (("request_id", rid), ("request_sha256", req["request_sha256"]),
                           ("selected_value", req["selected_value"]), ("direction", req["direction"])):
            if key in row and row[key] != value:
                raise ValueError("baseline rewrote original " + key)
        row.update({"request_id": rid, "request_sha256": req["request_sha256"],
                    "selected_value": req["selected_value"], "direction": req["direction"],
                    "source_request": deepcopy(req),
                    "source_task_bindings": deepcopy(raw_bindings),
                    "scope_status": row.get("scope_status", "unproved"),
                    "production_adoption_allowed": False})
        enriched[rid] = row
    return enriched


def apply_literal_proofs(baseline, requests, documents, proof_status_rows, proof_rows,
                         proof_manifest_sha, proof_file_sha):
    """Overlay unanimous verified raw proofs without weakening source binding."""
    status_by_id = indexed(proof_status_rows, "request_id", "literal proof request")
    pairs_by_task = {}
    for proof in proof_rows:
        if proof["status"] not in {"support", "conflict"}:
            continue
        for task_id in (proof.get("forward_task_id"), proof.get("reverse_task_id")):
            if task_id:
                pairs_by_task.setdefault(task_id, []).append(proof)
    overlay = deepcopy(baseline)
    proof_ids = set()
    for rid, status_row in status_by_id.items():
        if not status_row.get("eligible"):
            continue
        req = requests[rid]
        state = status_row["status"]
        if state not in {"support", "conflict"}:
            raise ValueError("eligible raw proof has an unknown state")
        if state == "support":
            winner = req["selected_value"]
        else:
            winner = None
        axes = req.get("target_selected_axes_text")
        if not isinstance(axes, str) or not axes:
            raise ValueError("proof-protected request lacks exact target selected axes")
        ref = {"document_id": req["target_document_id"], "field": "selected_axes",
               "start": 0, "end": len(axes), "quote": axes}
        if not GATE.verify_quote(ref, req, documents):
            raise ValueError("literal proof target-axis quote does not verify")
        proof_records = []
        seen_pair_hashes = set()
        for task_id in status_row["proof_ids"]:
            for proof in pairs_by_task.get(task_id, []):
                if proof["status"] == state:
                    pair_sha = hashlib.sha256(json.dumps(
                        proof, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    if pair_sha in seen_pair_hashes:
                        continue
                    seen_pair_hashes.add(pair_sha)
                    proof_records.append({"proof_pair_sha256": pair_sha,
                        "forward_task_id": proof["forward_task_id"],
                        "reverse_task_id": proof["reverse_task_id"], "proof_status": proof["status"],
                        "raw_span_receipt_count": sum(len(v) if isinstance(v, list) else int(bool(v))
                                                       for v in proof.get("raw_span_receipts", {}).values())})
        if not proof_records:
            raise ValueError("eligible request has no verified literal proof records")
        row = deepcopy(baseline[rid])
        row.update({"state": state, "winning_value": winner,
                    "reason": "verified_exact_literal_family_" + state,
                    "quote_refs": [ref], "scope_status": "unproved",
                    "literal_family_proof": {"proof_version": "literal_complete_ordered_family_v1",
                        "proof_manifest_sha256": proof_manifest_sha,
                        "proof_file_sha256": proof_file_sha,
                        "request_status": state, "task_proof_records": proof_records,
                        "verified_complete_ordered_raw_family": True,
                        "production_adoption_allowed": False}})
        overlay[rid] = row
        proof_ids.add(rid)
    return overlay, proof_ids


def validate_dispatch(selection_dir: Path, source_dir: Path):
    """Check all four Luna worker assignments and response-file receipts."""
    plan = read_json(selection_dir / "inference-selection-plan.json")
    selection_manifest = read_json(selection_dir / "manifest.json")
    if selection_manifest["selection_plan_sha256"] != sha_file(selection_dir / "inference-selection-plan.json"):
        raise ValueError("composition selection plan SHA differs")
    if selection_manifest.get("model_inference_run") is not False:
        raise ValueError("predeclared selection was modified after inference")
    dispatch_rows = [x for x in read_rows(selection_dir / "context-dispatch-selection.jsonl") if x["dispatch"]]
    expected_units = {x["context_unit_id"] for x in dispatch_rows}
    trace = read_json(selection_dir / "dispatch-trace.json")
    protocol = read_json(source_dir / "protocol.json")
    if (trace.get("dispatcher") != "collaboration.spawn_agent"
            or trace.get("model_override") != "gpt-6-luna"
            or trace.get("fork_turns") != "none"
            or trace.get("hosted_weights_revision_verified") is not False
            or trace.get("protocol_sha256") != protocol.get("protocol_sha256")
            or trace.get("selection_manifest_sha256") != sha_file(selection_dir / "manifest.json")):
        raise ValueError("composition dispatch trace differs from frozen protocol/selection")
    agents = trace.get("agents")
    if not isinstance(agents, list) or len(agents) != 4:
        raise ValueError("exactly four explicit Luna worker traces are required")
    assigned = set()
    response_map = {}
    for agent in agents:
        if (agent.get("model_override") != "gpt-6-luna" or agent.get("fork_turns") != "none"
                or not agent.get("task_name")):
            raise ValueError("worker model identity/assignment provenance is missing")
        units = agent.get("expected_unit_ids")
        if not isinstance(units, list) or assigned.intersection(units):
            raise ValueError("worker unit assignment is invalid or overlapping")
        assignment = Path(agent["assignment_file"])
        if not assignment.is_absolute():
            assignment = selection_dir / assignment
        if sha_file(assignment) != agent.get("assignment_sha256"):
            raise ValueError("worker assignment file SHA differs")
        assignment_obj = read_json(assignment)
        assignment_units = assignment_obj.get("unit_ids", assignment_obj.get("expected_unit_ids"))
        if assignment_units != units:
            raise ValueError("assignment file and dispatch trace units differ")
        assigned.update(units)
        response = Path(agent["response_file"])
        if not response.is_absolute():
            response = selection_dir / response
        response_map[agent["task_name"]] = (response, agent)
    if assigned != expected_units:
        raise ValueError("workers do not cover exactly the frozen 216-unit dispatch selection")
    completion = read_json(selection_dir / "dispatch-completion-receipt.json")
    if (completion.get("selection_manifest_sha256") != sha_file(selection_dir / "manifest.json")
            or completion.get("protocol_sha256") != protocol.get("protocol_sha256")
            or completion.get("model_override") != "gpt-6-luna"
            or completion.get("hosted_weights_revision_verified") is not False
            or completion.get("classifier_identity_matches") is not True):
        raise ValueError("dispatch completion receipt does not bind model identity")
    response_checks = completion.get("response_files", {})
    responses = {}
    response_paths = []
    for task_name, (path, agent) in response_map.items():
        digest = sha_file(path)
        expected_digest = agent.get("response_sha256") or response_checks.get(agent["response_file"], {}).get("sha256")
        if digest != expected_digest:
            raise ValueError("worker response-file SHA differs from dispatch receipt")
        rows = read_rows(path)
        expected_count = response_checks.get(agent["response_file"], {}).get("response_count", len(rows))
        if len(rows) != expected_count or set(row.get("unit_id") for row in rows) != set(agent["expected_unit_ids"]):
            raise ValueError("worker response count/identity coverage differs")
        for row in rows:
            if row["unit_id"] in responses:
                raise ValueError("duplicate raw response unit ID")
            responses[row["unit_id"]] = row
        response_paths.append(path)
    if set(responses) != expected_units:
        raise ValueError("raw model responses do not exactly cover selected dispatch units")
    return plan, dispatch_rows, responses, trace, completion, response_paths


def validate_source_fanout(packets, payloads, fanouts, requests):
    """Prove cache units retain exact scoped context and one-to-many bindings."""
    packet_by_unit = indexed(packets, "unit_id", "source packet")
    payload_by_unit = indexed(payloads, "unit_id", "model payload")
    fanout_by_unit = indexed(fanouts, "context_unit_id", "source fanout")
    if not (set(packet_by_unit) == set(payload_by_unit) == set(fanout_by_unit)):
        raise ValueError("source packet/payload/fanout unit coverage differs")
    seen_requests, seen_tasks = set(), set()
    for unit_id, packet in packet_by_unit.items():
        payload, fan = payload_by_unit[unit_id], fanout_by_unit[unit_id]
        rids, tids = fan.get("request_ids", []), fan.get("selected_task_ids", [])
        if len(rids) != len(set(rids)) or len(tids) != len(set(tids)):
            raise ValueError("source fanout repeats request/task binding")
        if seen_requests.intersection(rids) or seen_tasks.intersection(tids):
            raise ValueError("exact-context groups overlap original request/task bindings")
        if not set(rids).issubset(requests):
            raise ValueError("source fanout references a missing original request")
        if not rids:
            raise ValueError("source context has no original request bindings")
        members = [requests[rid] for rid in rids]
        representative = members[0]
        key = {"direction": representative["direction"],
               "axis_name": representative["axis_name"],
               "option_values": representative["option_values"],
               "target_document_sha256": representative["target_document_sha256"],
               "target_selected_axes": representative.get("target_selected_axes") or []}
        key_sha = hashlib.sha256(json.dumps(key, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        axes_text = representative.get("target_selected_axes_text")
        doc_id = representative["target_document_id"]
        for member in members:
            member_key = {"direction": member["direction"], "axis_name": member["axis_name"],
                          "option_values": member["option_values"],
                          "target_document_sha256": member["target_document_sha256"],
                          "target_selected_axes": member.get("target_selected_axes") or []}
            if (member_key != key
                    or (member.get("target_selected_axes_text") or None) != (axes_text or None)
                    or member.get("target_document_id") != doc_id):
                raise ValueError("fanout requests differ in exact context or axes text")
        if (fan.get("cache_key_sha256") != key_sha
                or unit_id != "final-exact-context:" + key_sha):
            raise ValueError("recomputed exact cache key/unit differs from fanout")
        expected_kind = "paired" if key["target_selected_axes"] else "shared"
        packet_axes = packet.get("target_selected_axes_text")
        if (packet.get("unit_id") != unit_id or packet.get("kind") != expected_kind
                or packet.get("axis_name") != key["axis_name"]
                or packet.get("option_values") != key["option_values"]
                or packet.get("target_document_id") != doc_id
                or (expected_kind == "paired" and packet_axes != axes_text)
                or (expected_kind == "shared" and packet_axes is not None)):
            raise ValueError("frozen packet does not match its exact request context")
        if packet.get("packet_sha256") is None:
            raise ValueError("frozen source packet lacks its packet SHA")
        expected_payload = CONTEXT.model_payload(packet)
        if payload != expected_payload:
            raise ValueError("compact model payload differs from composition_context.model_payload(packet)")
        seen_requests.update(rids)
        seen_tasks.update(tids)
    return packet_by_unit, payload_by_unit, fanout_by_unit


def run(prep_dir: Path, selection_dir: Path, source_dir: Path, proof_dir: Path,
        baseline_dir: Path, output_dir: Path):
    """Create frozen baseline-literal and complete-scoped-fallback gates.

    Call only after the root has frozen code/protocol and explicitly started the
    blind evaluation. This function contains no reference/label path.
    """
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite " + str(output_dir))
    prep_manifest = read_json(prep_dir / "manifest.json")
    selection_plan = read_json(selection_dir / "inference-selection-plan.json")
    selection_manifest = read_json(selection_dir / "manifest.json")
    proof_manifest = read_json(proof_dir / "manifest.json")
    source_manifest = read_json(source_dir / "manifest.json")
    baseline_path = baseline_dir / "fusion-predictions.jsonl"
    if not baseline_path.is_file() and (baseline_dir / "model-run/fusion-predictions.jsonl").is_file():
        baseline_path = baseline_dir / "model-run/fusion-predictions.jsonl"
    baseline_meta_dir = baseline_dir
    if not (baseline_meta_dir / "summary.json").is_file() and (baseline_dir.parent / "summary.json").is_file():
        # The canonical baseline runner places its outer receipt beside model-run/.
        baseline_meta_dir = baseline_dir.parent
    baseline_summary = read_json(baseline_meta_dir / "summary.json")
    if (prep_manifest.get("labels_read") is not False or prep_manifest.get("model_inference_run") is not False
            or selection_plan.get("membership_sha256") != prep_manifest.get("membership_sha256")
            or proof_manifest.get("membership_sha256") != prep_manifest.get("membership_sha256")
            or source_manifest.get("independent_test_labels_accessed") is not False
            or source_manifest.get("full_test_prior_predictions_accessed") is not False):
        raise ValueError("frozen blind evaluation inputs do not share one exact membership")
    if (baseline_summary.get("labels_read") is not False
            or baseline_summary.get("fresh_inference") is not True
            or baseline_summary.get("request_count") != prep_manifest["request_count"]
            or baseline_summary.get("independent_test") is not True):
        raise ValueError("baseline run does not certify fresh full-subset inference")
    baseline_pins = {"jev": baseline_summary.get("verified_jev_pin"),
                      "nli": baseline_summary.get("verified_nli_pin")}
    if any(not isinstance(pin, dict) or not pin for pin in baseline_pins.values()):
        raise ValueError("fresh baseline must verify both fixed JEV and NLI model pins")
    baseline_freeze = baseline_meta_dir / "runtime-source-freeze.json"
    if not baseline_freeze.is_file():
        raise ValueError("fresh baseline runtime source freeze is missing")
    for name, digest in prep_manifest["output_sha256"].items():
        if sha_file(prep_dir / name) != digest:
            raise ValueError("gate preparation changed: " + name)
    if (sha_file(selection_dir / "inference-selection-plan.json") != selection_manifest["selection_plan_sha256"]
            or proof_manifest.get("proofs_sha256") != sha_file(proof_dir / "proofs.jsonl")):
        raise ValueError("pre-inference proof/selection input SHA differs")

    requests = indexed(read_rows(prep_dir / "requests.jsonl"), "request_id", "request")
    documents = indexed(read_rows(prep_dir / "documents.jsonl"), "document_id", "document")
    tasks = indexed(read_rows(prep_dir / "tasks.jsonl"), "task_id", "task")
    row_plans = read_rows(prep_dir / "row_plans.jsonl")
    baseline = enrich_baseline(read_rows(baseline_path), requests)
    status_rows = read_rows(selection_dir / "request-literal-proof-status.jsonl")
    proof_rows = read_rows(proof_dir / "proofs.jsonl")
    proof_file_sha, proof_manifest_sha = sha_file(proof_dir / "proofs.jsonl"), sha_file(proof_dir / "manifest.json")
    overlay, proof_ids = apply_literal_proofs(baseline, requests, documents, status_rows, proof_rows,
                                               proof_manifest_sha, proof_file_sha)
    plan, dispatch_rows, responses, trace, completion, response_paths = validate_dispatch(selection_dir, source_dir)
    packets, payloads, fanouts = validate_source_fanout(
        read_rows(source_dir / "packets.jsonl"), read_rows(source_dir / "model-payloads.jsonl"),
        read_rows(source_dir / "request-fanout.jsonl"), requests)
    payload_to_dispatch = {row["context_unit_id"]: row for row in dispatch_rows}
    results = {}
    invalid = []
    for uid, response in responses.items():
        packet = packets[uid]
        fanout = fanouts[uid]
        representative = requests[fanout["request_ids"][0]]
        try:
            results[uid] = LM.validate_response(packet, response, representative, documents)
        except ValueError as exc:
            invalid.append({"unit_id": uid, "reason": str(exc)})
            results[uid] = {"unit_id": uid, "status": "unknown", "winning_value": None,
                            "quote_refs": [], "reason": "invalid_source_bound_model_response",
                            "scope_status": "unproved", "production_adoption_allowed": False}
    # Exact cache fan-out remains scoped to each source request's complete
    # document/axis/ordered options/full selected axes. No global singleton.
    derived = {}
    selected_requests = set()
    for context in read_rows(selection_dir / "context-dispatch-selection.jsonl"):
        uid = context["context_unit_id"]
        fan = fanouts[uid]
        if sorted(fan["request_ids"]) != context["request_ids"] or sorted(fan["selected_task_ids"]) != context["selected_task_ids"]:
            raise ValueError("frozen composition fanout differs from selection")
        result = results.get(uid)
        if context["dispatch"] and result is None:
            raise ValueError("selected source context has no validated response result")
        if not context["dispatch"]:
            if not context["literal_proof_complete_for_all_requests"]:
                raise ValueError("unproved context was incorrectly skipped")
        for rid in fan["request_ids"]:
            if rid in selected_requests:
                raise ValueError("request appears in multiple exact context groups")
            selected_requests.add(rid)
            if rid in proof_ids or result is None:
                derived[rid] = deepcopy(overlay[rid])
            else:
                derived[rid] = T.paired_prediction(requests[rid], overlay[rid], result)
    if selected_requests != set(requests) or set(derived) != set(overlay):
        raise ValueError("composition predictions do not exactly cover the full request set")

    output_dir.mkdir(parents=True)
    (output_dir / "baseline-literal-overlay").mkdir()
    (output_dir / "complete-scoped-fallback").mkdir()
    baseline_pred_path = output_dir / "baseline-literal-overlay/predictions.jsonl"
    write_rows(baseline_pred_path, overlay.values())
    baseline_gates_dir = output_dir / "baseline-literal-overlay/gates"
    base_gate = GATE.finish(prep_dir, baseline_pred_path, baseline_gates_dir)
    baseline_gates = indexed(read_rows(baseline_gates_dir / "decisions.jsonl"), "case_id", "baseline gate")
    completed, policy_audit = POLICY.complete_predictions(overlay, derived, requests,
                                                          row_plans, baseline_gates, proof_ids)
    final_pred_path = output_dir / "complete-scoped-fallback/predictions.jsonl"
    write_rows(final_pred_path, completed.values())
    final_gate_dir = output_dir / "complete-scoped-fallback/gates"
    final_gate = GATE.finish(prep_dir, final_pred_path, final_gate_dir)
    write_json(output_dir / "complete-scoped-fallback/policy-audit.json", policy_audit)
    write_rows(output_dir / "composition-unit-results.jsonl", results.values())
    write_rows(output_dir / "invalid-responses.jsonl", invalid)

    source_paths = [prep_dir / "manifest.json", selection_dir / "inference-selection-plan.json",
                    selection_dir / "manifest.json", selection_dir / "dispatch-trace.json",
                    selection_dir / "dispatch-completion-receipt.json", source_dir / "manifest.json",
                    source_dir / "protocol.json", proof_dir / "manifest.json", proof_dir / "proofs.jsonl",
                    baseline_meta_dir / "summary.json", baseline_freeze, baseline_path, *response_paths,
                    Path(__file__), Path(POLICY.__file__), Path(LM.__file__), Path(T.__file__),
                    Path(GATE.__file__)]
    snapshot = {str(path.resolve()): {"sha256": sha_file(path), "bytes": path.stat().st_size}
                for path in source_paths}
    all_gate_files = [p for p in output_dir.rglob("*") if p.is_file()]
    frozen = {"schema_version": "final-full-sku-gates-frozen-v1",
              "evaluation_scope": "primary_cpu_40case_full_fresh_baseline_and_composition",
              "membership_sha256": prep_manifest["membership_sha256"],
              "case_count": prep_manifest["case_count"], "product_count": prep_manifest["product_count"],
              "task_count": prep_manifest["task_count"], "request_count": prep_manifest["request_count"],
              "source_spans_verified_count": prep_manifest["all_v2_source_spans_verified_count"],
              "literal_proof_ids": len(proof_ids), "composition_context_count": plan["exact_paired_context_count"],
              "composition_dispatch_count": len(dispatch_rows), "composition_response_count": len(responses),
              "invalid_response_count": len(invalid), "baseline_prediction_count": len(overlay),
              "final_prediction_count": len(completed), "baseline_gate_case_count": base_gate["case_count"],
              "final_gate_case_count": final_gate["case_count"],
              "variants": ["baseline-literal-overlay", "complete-scoped-fallback"],
              "selected_policy": "complete-scoped-fallback", "all_predictions_frozen": True,
              "all_gates_frozen": True, "labels_read": False, "test_gold_opened": False,
              "predictions_read_before_inference": False, "production_adoption_allowed": False,
              "source_snapshot": snapshot,
              # Freeze receipts are intentionally outside this inventory to
              # avoid self-referential hashes; their hashes are recorded below.
              "output_sha256": {str(p.relative_to(output_dir)): sha_file(p) for p in all_gate_files}}
    write_json(output_dir / "all-gates-frozen.json", frozen)
    frozen["all_gates_frozen_manifest_sha256"] = sha_file(output_dir / "all-gates-frozen.json")
    (output_dir / "execution-freeze.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n")
    return frozen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prep-dir", type=Path, default=PREP)
    ap.add_argument("--selection-dir", type=Path, default=SELECTION)
    ap.add_argument("--source-dir", type=Path, default=SOURCE)
    ap.add_argument("--proof-dir", type=Path, default=PROOFS)
    ap.add_argument("--baseline-dir", type=Path, default=BASELINE)
    ap.add_argument("--output-dir", type=Path, default=OUT)
    args = ap.parse_args(argv)
    result = run(args.prep_dir, args.selection_dir, args.source_dir, args.proof_dir,
                 args.baseline_dir, args.output_dir)
    print(json.dumps({k: result[k] for k in ("case_count", "task_count", "request_count",
                                            "baseline_prediction_count", "final_prediction_count",
                                            "baseline_gate_case_count", "final_gate_case_count",
                                            "all_predictions_frozen", "all_gates_frozen", "labels_read")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
