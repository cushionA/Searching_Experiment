"""Aggregate source-bound condition proposals against an unchanged full AU pool.

This evaluates an experiment, not confirmed gold. Model confidence is never
used as a substitute for a complete condition inventory or source provenance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


MODEL_REVISION = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
RELATIONS = ("support", "conflict", "unknown")


def canonical_sha(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def proposal_relation(packet: dict, prediction: dict | None, input_sha: str) -> tuple[str, list[str]]:
    """Fail closed on transport errors, stale bindings, or answer-symbol bias."""
    if prediction is None:
        return "unknown", ["missing_prediction"]
    errors = []
    for key in ("packet_id", "case_id", "au_row_key"):
        if prediction.get(key) != packet[key]:
            errors.append("binding_mismatch:" + key)
    if prediction.get("input_sha256") != input_sha:
        errors.append("input_sha_mismatch")
    if prediction.get("packet_sha256") != canonical_sha(packet):
        errors.append("packet_sha_mismatch")
    if prediction.get("model_revision") != MODEL_REVISION:
        errors.append("model_revision_mismatch")
    if prediction.get("evidence_refs") != packet["evidence"]:
        errors.append("evidence_binding_mismatch")
    if prediction.get("record_sha256") != canonical_sha({k: v for k, v in prediction.items() if k != "record_sha256"}):
        errors.append("record_sha_mismatch")
    if prediction.get("status") != "ok":
        errors.append("prediction_not_ok")
    permutations = prediction.get("permutations", [])
    if len(permutations) != 3:
        errors.append("incomplete_permutations")
    outcomes = []
    for index, result in enumerate(permutations):
        expected = {r: str((i + index) % 3) for i, r in enumerate(RELATIONS)}
        if result.get("relation_to_digit") != expected or result.get("status") != "ok":
            errors.append("invalid_permutation:" + str(index))
        digit = result.get("restricted_argmax_digit")
        relation = next((r for r, d in expected.items() if d == digit), "unknown")
        if relation != result.get("permutation_relation"):
            errors.append("invalid_relation_mapping:" + str(index))
        logits = result.get("raw_digit_logits", {})
        if set(logits) != {"0", "1", "2"} or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in logits.values()):
            errors.append("invalid_digit_logits:" + str(index))
        elif digit != max(logits, key=logits.get):
            errors.append("invalid_restricted_argmax:" + str(index))
        # No arbitrary probability threshold. The unrestricted next token must
        # itself be one of the legal answers, in every symbol permutation.
        if result.get("full_vocab_argmax_is_legal_digit") is not True:
            errors.append("unrestricted_answer_not_digit:" + str(index))
        outcomes.append(relation)
    if len(outcomes) != 3 or len(set(outcomes)) != 1:
        errors.append("permutation_disagreement")
    relation = outcomes[0] if len(outcomes) == 3 and len(set(outcomes)) == 1 else "unknown"
    if prediction.get("aggregate_relation") != relation:
        errors.append("aggregate_relation_mismatch")
    if not packet["evidence"] and relation != "unknown":
        errors.append("no_evidence_for_decisive_relation")
    return ("unknown" if errors else relation), errors


def aggregate_case(host: dict, packets: list[dict], relations: dict[str, str]) -> dict:
    """Every condition must hold on one row; unknown alternative rows remain."""
    result = {"case_id": host["case_id"], "decision": "review", "au_row_key": None,
              "reason": "unresolved", "row_count": len(host["au_rows"]), "model_used": False}
    row_keys = [r["row_key"] for r in host["au_rows"]]
    rows = host["row_condition_statuses"]
    if len(set(row_keys)) != len(row_keys) or [r["row_key"] for r in rows] != row_keys:
        raise ValueError("Host rows and full pool differ")
    required = host["required_conditions"]
    for row in rows:
        if [x["condition_index"] for x in row["condition_status"]] != list(range(len(required))):
            raise ValueError("Incomplete row condition inventory")
    conflicts = {row["row_key"] for row in rows
                 if any(x["status"] == "typed_conflict" for x in row["condition_status"])}
    if not required or host.get("blocking_issues"):
        result["reason"] = "incomplete_conditions_or_evidence"
        return result
    if len(conflicts) == len(row_keys):
        result.update(decision="unmatched", reason="every_full_pool_row_has_explicit_typed_conflict")
        return result
    focus = host.get("focus_row_key")
    if focus is None or focus not in row_keys:
        result["reason"] = "no_unique_source_bound_focus"
        return result
    if focus in conflicts:
        result["reason"] = "focus_conflicts_but_other_rows_unresolved"
        return result
    if any(key != focus and key not in conflicts for key in row_keys):
        result["reason"] = "other_full_pool_rows_unresolved"
        return result
    if any(p["case_id"] != host["case_id"] or p["au_row_key"] != focus for p in packets):
        raise ValueError("Packet escaped its fixed AU case/row")
    r_packets = [p for p in packets if p["condition"]["side"] == "rakuten"]
    a_packets = [p for p in packets if p["condition"]["side"] == "au"]
    if len(r_packets) != len(required):
        result["reason"] = "missing_mandatory_rakuten_packets"
        return result
    for req, packet in zip(required, r_packets):
        if any(packet["condition"].get(k) != req[k] for k in ("axis_name_raw", "selected_value_raw", "atom_kind", "atom_value")):
            raise ValueError("Rakuten packet requirement inventory differs")
    focus_row = next(row for row in rows if row["row_key"] == focus)
    extras = focus_row["au_only_or_unmatched_atoms"]
    if len(a_packets) != len(extras):
        result["reason"] = "missing_mandatory_au_only_packets"
        return result
    for extra, packet in zip(extras, a_packets):
        if any(packet["condition"].get(k) != extra[k] for k in ("axis_name_raw", "atom_kind", "atom_value")):
            raise ValueError("AU-only packet requirement inventory differs")
    conditions = []
    for state, packet in zip(focus_row["condition_status"], r_packets):
        if state["status"] == "supported_by_AU_row":
            conditions.append("support")
        else:
            conditions.append(relations.get(packet["packet_id"], "unknown"))
            result["model_used"] = True
    for packet in a_packets:
        conditions.append(relations.get(packet["packet_id"], "unknown"))
        result["model_used"] = True
    result["condition_relations"] = conditions
    if "conflict" in conditions:
        result.update(decision="unmatched", reason="focus_model_conflict_and_other_rows_typed_excluded")
    elif conditions and set(conditions) == {"support"}:
        result.update(decision="matched", au_row_key=focus, reason="all_required_conditions_support_one_row")
    else:
        result["reason"] = "mandatory_condition_unknown"
    return result


def evaluate(prepared: Path, predictions: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    packet_file = prepared / "model/packets.jsonl"
    input_sha = hashlib.sha256(packet_file.read_bytes()).hexdigest()
    packets = read_jsonl(packet_file)
    pools = read_jsonl(prepared / "host/fullpools.jsonl")
    records = read_jsonl(predictions)
    if len({r["packet_id"] for r in records}) != len(records):
        raise ValueError("Duplicate prediction packet ID")
    packet_ids = {p["packet_id"] for p in packets}
    if any(r["packet_id"] not in packet_ids for r in records):
        raise ValueError("Unexpected prediction packet ID")
    lookup = {r["packet_id"]: r for r in records}
    evaluated, relations = [], {}
    for packet in packets:
        relation, errors = proposal_relation(packet, lookup.get(packet["packet_id"]), input_sha)
        relations[packet["packet_id"]] = relation
        evaluated.append({"packet_id": packet["packet_id"], "case_id": packet["case_id"],
                          "relation": relation, "validation_errors": errors})
    decisions = [aggregate_case(host, [p for p in packets if p["case_id"] == host["case_id"]], relations)
                 for host in pools]
    output.mkdir(parents=True)
    for name, values in (("packet-validation.jsonl", evaluated), ("decisions.jsonl", decisions)):
        (output / name).write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8")
    summary = {"schema": "condition-host-evaluation-v1", "case_count": len(pools),
               "full_pool_row_references": sum(len(h["au_rows"]) for h in pools),
               "packet_count": len(packets), "prediction_count": len(records),
               "raw_model_aggregate_counts": dict(Counter(r.get("aggregate_relation", "unknown") for r in records)),
               "validated_relation_counts": dict(Counter(relations.values())),
               "packet_validation_failure_count": sum(bool(v["validation_errors"]) for v in evaluated),
               "decisions": dict(Counter(d["decision"] for d in decisions)),
               "reasons": dict(Counter(d["reason"] for d in decisions)), "labels_read": False,
               "model_result_is_proposal": True, "input_sha256": input_sha,
               "prediction_sha256": hashlib.sha256(predictions.read_bytes()).hexdigest(),
               "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prepared", type=Path, required=True)
    ap.add_argument("--predictions", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(evaluate(args.prepared, args.predictions, args.output), ensure_ascii=False, indent=2))
