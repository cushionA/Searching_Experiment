#!/usr/bin/env python3
"""Freeze proof-based composition dispatch selection before any responses.

The 40-case baseline still runs freshly for every original request. This
selection only avoids hosted composition inference when every request sharing
an exact paired model input already has a complete, verified literal-family
proof. No global sharing or source-block truncation is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import composition_language_model_v1 as LM
import composition_context_v1 as CONTEXT

BASE = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1"
GATE_PREP = BASE / "gate-prepared-v2"
SOURCE = BASE / "composition-source-only"
PROOF = ROOT / ".lab-output/sku-final-literal-family-proofs-40-20261013-v1"
OUT = BASE / "inference-selection-v2"


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


def write_rows(path: Path, rows):
    with path.open("xb") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode() + b"\n")


def aggregate_verified_task_proofs(tasks, requests, proof_rows):
    task_by_id = {t["task_id"]: t for t in tasks}
    if len(task_by_id) != len(tasks):
        raise ValueError("duplicate frozen tasks")
    request_by_id = {r["request_id"]: r for r in requests}
    if len(request_by_id) != len(requests):
        raise ValueError("duplicate hydrated requests")
    task_status_evidence = defaultdict(list)
    pair_status_counts = Counter()
    for proof in proof_rows:
        pair_status_counts[proof["status"]] += 1
        forward_id, reverse_id = proof.get("forward_task_id"), proof.get("reverse_task_id")
        forward, reverse = task_by_id.get(forward_id), task_by_id.get(reverse_id)
        if forward is None or reverse is None:
            raise ValueError("literal proof references a task outside the frozen subset")
        if (forward["direction"] != "rakuten_to_au" or reverse["direction"] != "au_to_rakuten"
                or forward["case_id"] != reverse["case_id"]
                or forward["au_row_key"] != reverse["au_row_key"]
                or proof.get("case_id") != forward["case_id"]
                or proof.get("au_row_key") != forward["au_row_key"]):
            raise ValueError("literal proof task pair identity differs")
        status = proof["status"]
        if status not in {"support", "conflict", "unknown"}:
            raise ValueError("unsupported literal proof status")
        for task in (forward, reverse):
            task_status_evidence[task["task_id"]].append(status)

    # A task can participate in more than one paired proof if the opposite
    # source has repeated axis occurrences. Preserve only unanimous, verified
    # support/conflict; every mixed or unknown set remains unknown.
    task_status = {}
    for tid, statuses in task_status_evidence.items():
        task_status[tid] = (statuses[0] if statuses
                            and all(s in {"support", "conflict"} for s in statuses)
                            and len(set(statuses)) == 1 else "unknown")

    task_ids_by_request = defaultdict(list)
    for task in tasks:
        if task["request_id"] not in request_by_id:
            raise ValueError("task has no hydrated request")
        task_ids_by_request[task["request_id"]].append(task["task_id"])
    request_proofs = {}
    request_status_counts = Counter()
    for rid, request in request_by_id.items():
        tids = task_ids_by_request.get(rid, [])
        statuses = [task_status.get(tid, "unknown") for tid in tids]
        # A proof applies only when every original task bound to this request
        # has a verified and identical support/conflict status.
        eligible = bool(statuses) and all(s in {"support", "conflict"} for s in statuses) and len(set(statuses)) == 1
        state = statuses[0] if eligible else "unknown"
        if eligible and state == "support" and any(
                task_by_id[tid]["raw_condition"].get("value") != request["selected_value"] for tid in tids):
            raise ValueError("verified support proof differs from the request's exact selected value")
        request_proofs[rid] = {"request_id": rid, "status": state,
                               "eligible": eligible, "bound_task_ids": sorted(tids),
                               "bound_task_count": len(tids),
                               "covered_task_count": sum(tid in task_status_evidence for tid in tids),
                               "proof_status_counts": dict(Counter(statuses)),
                               "proof_ids": sorted(tid for tid in tids if tid in task_status_evidence)}
        request_status_counts[state] += 1
    return request_proofs, task_status, pair_status_counts, request_status_counts


def exact_context_selection(packets, payloads, fanout, requests, tasks, documents, request_proofs):
    packet_by_id = {p["unit_id"]: p for p in packets}
    payload_by_id = {p["unit_id"]: p for p in payloads}
    if len(packet_by_id) != len(packets) or set(packet_by_id) != set(payload_by_id):
        raise ValueError("packet/model-payload unit IDs differ")
    request_by_id = {r["request_id"]: r for r in requests}
    task_by_id = {t["task_id"]: t for t in tasks}
    document_by_id = {d["document_id"]: d for d in documents}
    mapped_requests, mapped_tasks, selected = set(), set(), []
    for group in fanout:
        uid = group["context_unit_id"]
        packet, payload = packet_by_id.get(uid), payload_by_id.get(uid)
        if packet is None:
            raise ValueError("fanout references an unknown source packet")
        if group.get("target_document_id") != packet["target_document_id"]:
            raise ValueError("fanout target document differs from exact packet")
        req_ids = group.get("request_ids")
        tids = group.get("selected_task_ids")
        model_ids = group.get("model_input_ids")
        if (not isinstance(req_ids, list) or not req_ids or not isinstance(tids, list)
                or not isinstance(model_ids, list)):
            raise ValueError("exact context fanout is incomplete")
        if mapped_requests & set(req_ids) or mapped_tasks & set(tids):
            raise ValueError("request/task is fanned out by multiple context units")
        mapped_requests.update(req_ids)
        mapped_tasks.update(tids)
        bound_tasks = {t["task_id"] for t in tasks if t["request_id"] in set(req_ids)}
        if set(tids) != bound_tasks:
            raise ValueError("fanout task IDs do not exactly cover its requests")
        reqs = [request_by_id[rid] for rid in req_ids]
        if any((r["axis_name"] != packet["axis_name"]
                or r["option_values"] != packet["option_values"]
                or r["target_document_id"] != packet["target_document_id"]
                or r["target_document_sha256"] != document_by_id[packet["target_document_id"]]["document_sha256"]
                or r["target_selected_axes_text"] != packet["target_selected_axes_text"]
                or r["target_selected_axes"] != reqs[0]["target_selected_axes"]
                or r["direction"] != reqs[0]["direction"])
               for r in reqs):
            raise ValueError("paired packet is not exact for every original request")
        if (packet["kind"] != "paired" or payload["kind"] != "paired"
                or packet.get("target_selected_axes_text") in (None, "")
                or group.get("context_cache_kind") != "paired_exact_context_cache"):
            raise ValueError("full-context dispatch requires a complete paired selected-row context")
        key_object = {"direction": reqs[0]["direction"], "axis_name": reqs[0]["axis_name"],
                      "option_values": reqs[0]["option_values"],
                      "target_document_sha256": reqs[0]["target_document_sha256"],
                      "target_selected_axes": reqs[0]["target_selected_axes"]}
        key_sha = hashlib.sha256(json.dumps(key_object, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
        if (group.get("cache_key_sha256") != key_sha
                or uid != "final-exact-context:" + key_sha):
            raise ValueError("context unit id/cache key is not the exact paired model-input key")
        if "selected_value" in payload or "selected_value" in packet:
            raise ValueError("desired value leaked into a composition model input")
        # Recompute the exact payload from the complete source packet. This
        # validates every literal citation and confirms the compact payload is
        # byte-identical without dropping/truncating any source block.
        expected_payload = CONTEXT.model_payload(packet)
        if expected_payload != payload:
            raise ValueError("source packet and dispatched payload differ")
        proof_complete = all(request_proofs[rid]["eligible"] for rid in req_ids)
        selected.append({"context_unit_id": uid, "cache_key_sha256": group["cache_key_sha256"],
                         "request_ids": sorted(req_ids), "selected_task_ids": sorted(tids),
                         "model_input_ids": sorted(model_ids),
                         "literal_proof_complete_for_all_requests": proof_complete,
                         "dispatch": not proof_complete,
                         "dispatch_reason": "unproved_or_inconsistent_request_binding" if not proof_complete else "all_requests_proof_covered",
                         "packet_sha256": packet["packet_sha256"],
                         "model_input_sha256": payload["model_input_sha256"],
                         "source_block_count": len(packet["blocks"]),
                         "source_block_ids": [b["block_id"] for b in packet["blocks"]]})
    if mapped_requests != set(request_by_id):
        raise ValueError("source packet fanout misses original requests")
    if mapped_tasks != set(task_by_id):
        raise ValueError("source packet fanout misses original task bindings")
    if len(selected) != len(packets):
        raise ValueError("each exact context packet must have one fanout group")
    return selected


def freeze_selection(gate_dir: Path, source_dir: Path, proof_dir: Path, output_dir: Path):
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite " + str(output_dir))
    gate_manifest = read_json(gate_dir / "manifest.json")
    proof_manifest = read_json(proof_dir / "manifest.json")
    if (gate_manifest["labels_read"] is not False or gate_manifest["model_inference_run"] is not False
            or proof_manifest["labels_read"] is not False or proof_manifest["predictions_read"] is not False
            or proof_manifest.get("membership_sha256") != gate_manifest["membership_sha256"]
            or proof_manifest.get("proof_pair_coverage_exact") is not True
            or proof_manifest.get("unknown_is_not_conflict") is not True
            or any(not r.get("verified") for r in proof_manifest["used_raw_source_receipts"].values())):
        raise ValueError("literal proof/source scope is not frozen and verified")
    proof_path = proof_dir / "proofs.jsonl"
    if sha_file(proof_path) != proof_manifest["proofs_sha256"]:
        raise ValueError("literal proof file SHA differs")
    names = ("packets.jsonl", "model-payloads.jsonl", "request-fanout.jsonl", "source-blocks.jsonl",
             "protocol.json", "raw-pins.jsonl", "span-receipts.jsonl", "source-review.json")
    source_hashes = {name: sha_file(source_dir / name) for name in names}
    source_manifest = read_json(source_dir / "manifest.json")
    if (source_manifest.get("independent_test_labels_accessed") is not False
            or source_manifest.get("full_test_prior_predictions_accessed") is not False
            or source_manifest.get("inference_started") is not False):
        raise ValueError("source packet producer reports test labels/predictions/inference")
    for name, digest in source_manifest["output_sha256"].items():
        if sha_file(source_dir / name) != digest:
            raise ValueError("frozen source packet output hash differs: " + name)
    requests, tasks = read_rows(gate_dir / "requests.jsonl"), read_rows(gate_dir / "tasks.jsonl")
    request_proofs, task_status, pair_counts, request_counts = aggregate_verified_task_proofs(
        tasks, requests, read_rows(proof_path))
    packets, payloads = read_rows(source_dir / "packets.jsonl"), read_rows(source_dir / "model-payloads.jsonl")
    documents = read_rows(gate_dir / "documents.jsonl")
    fanout = read_rows(source_dir / "request-fanout.jsonl")
    selection = exact_context_selection(packets, payloads, fanout, requests, tasks, documents, request_proofs)
    dispatch_ids = {x["context_unit_id"] for x in selection if x["dispatch"]}
    dispatch_payloads = [p for p in payloads if p["unit_id"] in dispatch_ids]
    if {p["unit_id"] for p in dispatch_payloads} != dispatch_ids:
        raise ValueError("dispatch payload selection differs from frozen context selection")

    output_dir.mkdir(parents=True)
    # The selection decision is frozen before writing any dispatch payload or
    # accepting any hosted response.
    plan = {
        "schema_version": "final-sku-composition-inference-selection-v1",
        "status": "predeclared_before_any_composition_response",
        "evaluation_membership_sha256": gate_manifest["membership_sha256"],
        "gate_prepared_manifest_sha256": sha_file(gate_dir / "manifest.json"),
        "literal_proof_manifest_sha256": sha_file(proof_dir / "manifest.json"),
        "literal_proof_sha256": proof_manifest["proofs_sha256"],
        "source_packet_hashes": source_hashes,
        "source_packet_manifest_sha256": sha_file(source_dir / "manifest.json"),
        "task_literal_status_counts": dict(Counter(task_status.values())),
        "literal_proof_pair_status_counts": dict(pair_counts),
        "request_literal_proof_status_counts": dict(request_counts),
        "request_count": len(requests), "task_count": len(tasks),
        "exact_paired_context_count": len(selection),
        "proof_complete_context_count": sum(x["literal_proof_complete_for_all_requests"] for x in selection),
        "composition_dispatch_context_count": len(dispatch_ids),
        "composition_model_input_byte_count": sum(len(json.dumps(x, ensure_ascii=False, sort_keys=True,
                                                                      separators=(",", ":")).encode()) + 1
                                                    for x in dispatch_payloads),
        "dispatch_rule": "dispatch a complete exact paired context iff at least one fanned-out request lacks a consistent complete verified task-level literal proof; retain every source block, no global sharing",
        "baseline_fresh_request_count": len(requests),
        "proof_ids_protected_from_composition_policy": sum(x["eligible"] for x in request_proofs.values()),
        "selected_value_in_composition_model_payload": False,
        "labels_read": False, "predictions_read": False, "model_inference_run": False,
        "source_blocks_truncated": False,
        "selection_rows_sha256": sha_file(source_dir / "request-fanout.jsonl"),
    }
    (output_dir / "inference-selection-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    selection_path = output_dir / "context-dispatch-selection.jsonl"
    write_rows(selection_path, selection)
    proof_status_path = output_dir / "request-literal-proof-status.jsonl"
    write_rows(proof_status_path, [request_proofs[k] for k in sorted(request_proofs)])
    payload_path = output_dir / "dispatch-model-payloads.jsonl"
    write_rows(payload_path, dispatch_payloads)
    manifest = {"schema_version": "final-sku-composition-inference-selection-v1",
                "phase": "frozen_before_composition_inference",
                "membership_sha256": gate_manifest["membership_sha256"],
                "selection_plan_sha256": sha_file(output_dir / "inference-selection-plan.json"),
                "context_dispatch_count": len(dispatch_ids), "request_count": len(requests),
                "task_count": len(tasks), "model_inference_run": False,
                "labels_read": False, "predictions_read": False,
                "output_sha256": {p.name: sha_file(p) for p in (selection_path, proof_status_path, payload_path)}}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return plan


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gate-dir", type=Path, default=GATE_PREP)
    ap.add_argument("--source-dir", type=Path, default=SOURCE)
    ap.add_argument("--proof-dir", type=Path, default=PROOF)
    ap.add_argument("--output-dir", type=Path, default=OUT)
    args = ap.parse_args(argv)
    plan = freeze_selection(args.gate_dir, args.source_dir, args.proof_dir, args.output_dir)
    print(json.dumps({k: plan[k] for k in ("request_count", "task_count", "exact_paired_context_count",
                                           "proof_complete_context_count", "composition_dispatch_context_count",
                                           "task_literal_status_counts", "request_literal_proof_status_counts",
                                           "composition_model_input_byte_count", "labels_read", "predictions_read",
                                           "model_inference_run")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
