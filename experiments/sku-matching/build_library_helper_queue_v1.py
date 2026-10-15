#!/usr/bin/env python3
"""Freeze an advisory, price-free helper queue from existing gate/ranking files.

This is a file integration step only. Raw conflicts come from the existing
price-free selection output; priorities come from frozen RapidFuzz rankings.
No match decision is made here and every source candidate remains in the queue.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / ".lab-output/sku-installed-library-matching-20261015-v1"
PRICE_FREE = LAB / "price-free"
SELECTION = PRICE_FREE / "selection-v1"
RANKING = PRICE_FREE / "ranking-v1"
SOURCE = ROOT / ".lab-output/sku-classical-parser-20261015-v1/population-inputs"
EXPECTED = {"cases": 1370, "row_plans": 32325, "raw_review_cases": 982,
            "remaining_candidates": 10947, "residual_contexts": 2088}
RANKING_VIEW = "spacy_norm"
RANKING_SCORER = "token_set_ratio"
CONTEXT_KEYS = ("direction", "axis_name", "option_values",
                "target_document_id", "target_selected_axes")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("xb") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8") + b"\n")


def _request_context_key(request: dict[str, Any]) -> str:
    try:
        context = {key: request[key] for key in CONTEXT_KEYS}
    except KeyError as exc:
        raise ValueError(f"request missing context field {exc.args[0]}") from exc
    return json.dumps(context, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _verify_pin(path: Path, expected_sha256: str, name: str) -> None:
    observed = _sha(path)
    if observed != expected_sha256:
        raise ValueError(f"pinned input changed ({name}): {observed}")


def _verify_frozen_inputs() -> dict[str, Any]:
    source_manifest = _read_json(SOURCE / "manifest.json")
    selection_freeze = _read_json(SELECTION / "OUTPUT_FREEZE.json")
    ranking_freeze = _read_json(RANKING / "OUTPUT_FREEZE.json")

    for name in ("requests.jsonl", "row-plans.jsonl", "case-plans.jsonl"):
        _verify_pin(SOURCE / name, source_manifest["output_sha256"][name], name)
        _verify_pin(SOURCE / name, selection_freeze["input_pins"][name],
                    f"selection input {name}")
    for name in ("raw-cases.jsonl", "raw-rows.jsonl", "raw-requests.jsonl"):
        _verify_pin(SELECTION / name, selection_freeze["output_pins"][name],
                    f"selected raw {name}")
    _verify_pin(RANKING / "rankings.jsonl",
                ranking_freeze["output_files"]["rankings.jsonl"]["sha256"],
                "price-free ranking")
    view_path = Path(selection_freeze["view_path"])
    if not view_path.is_absolute():
        view_path = ROOT / view_path
    _verify_pin(view_path, selection_freeze["view_sha256"], "price-free views")
    return {"source_manifest": source_manifest,
            "selection_freeze": selection_freeze,
            "ranking_freeze": ranking_freeze,
            "view_path": view_path}


def _index_unique(rows: list[dict[str, Any]], key_fields: tuple[str, ...],
                  name: str) -> dict[tuple[Any, ...], dict[str, Any]]:
    result = {}
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        if key in result:
            raise ValueError(f"duplicate {name} key: {key}")
        result[key] = row
    return result


def build_queue(output: Path) -> dict[str, Any]:
    frozen = _verify_frozen_inputs()
    source_manifest = frozen["source_manifest"]
    selection_freeze = frozen["selection_freeze"]
    ranking_freeze = frozen["ranking_freeze"]

    original_requests = _read_jsonl(SOURCE / "requests.jsonl")
    row_plans = _read_jsonl(SOURCE / "row-plans.jsonl")
    case_plans = _read_jsonl(SOURCE / "case-plans.jsonl")
    raw_cases = _read_jsonl(SELECTION / "raw-cases.jsonl")
    raw_rows = _read_jsonl(SELECTION / "raw-rows.jsonl")
    raw_requests = _read_jsonl(SELECTION / "raw-requests.jsonl")

    if len(case_plans) != EXPECTED["cases"] or len(row_plans) != EXPECTED["row_plans"]:
        raise ValueError("unexpected frozen population size")
    case_plan_by_id = {row["case_id"]: row for row in case_plans}
    if len(case_plan_by_id) != len(case_plans):
        raise ValueError("duplicate case plan id")
    case_gate = {row["case_id"]: row for row in raw_cases}
    if len(case_gate) != len(raw_cases) or set(case_gate) != set(case_plan_by_id):
        raise ValueError("selected raw cases do not cover the full case population")
    request_by_id = {row["request_id"]: row for row in original_requests}
    request_state = {row["request_id"]: row["state"] for row in raw_requests}
    if len(request_by_id) != len(original_requests) or len(request_state) != len(raw_requests):
        raise ValueError("duplicate request id")
    row_gate = _index_unique(raw_rows, ("case_id", "au_row_key"), "selected raw row")
    row_plan_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in row_plans:
        row_plan_by_case[row["case_id"]].append(row)
    if set(row_plan_by_case) != set(case_plan_by_id):
        raise ValueError("row plans do not cover every case")

    ranked: dict[str, dict[str, Any]] = {}
    with (RANKING / "rankings.jsonl").open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("view") != RANKING_VIEW or record.get("scorer") != RANKING_SCORER:
                continue
            case_id = record.get("case_id")
            if not isinstance(case_id, str) or case_id in ranked:
                raise ValueError(f"duplicate or malformed frozen ranking case: {case_id!r}")
            ranked[case_id] = record
    if set(ranked) != set(case_plan_by_id):
        raise ValueError("selected ranking does not cover every case")

    # Build unknown context groups only from review cases and rows not already
    # excluded by the frozen raw selection gate.
    context_groups: dict[str, dict[str, Any]] = {}
    request_context_key: dict[str, str] = {}
    case_context_ids: dict[str, set[str]] = defaultdict(set)
    case_residual_request_ids: dict[str, set[str]] = defaultdict(set)
    residual_request_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)

    row_plan_maps: dict[tuple[str, str], dict[str, Any]] = {}
    for case_id, plans in row_plan_by_case.items():
        for plan in plans:
            key = (case_id, plan["au_row_key"])
            if key in row_plan_maps:
                raise ValueError(f"duplicate row plan binding: {key}")
            row_plan_maps[key] = plan
    if set(row_plan_maps) != set(row_gate):
        raise ValueError("raw selected rowset and source row plans differ")

    for case_id, gate_case in case_gate.items():
        if gate_case.get("disposition") != "review":
            continue
        for plan in row_plan_by_case[case_id]:
            gate_row = row_gate[(case_id, plan["au_row_key"])]
            if gate_row.get("state") == "conflict":
                continue
            for binding in plan.get("bindings", []):
                request_id = binding.get("request_id")
                if request_state.get(request_id) != "unknown":
                    continue
                request = request_by_id.get(request_id)
                if request is None:
                    raise ValueError(f"missing original request {request_id!r}")
                key_text = _request_context_key(request)
                context_id = sha256(key_text.encode("utf-8")).hexdigest()
                request_context_key[request_id] = context_id
                group = context_groups.setdefault(context_id, {
                    "schema": "sku-installed-library-residual-context-v1",
                    "context_id": context_id,
                    "context_key": json.loads(key_text),
                    "request_ids": set(),
                    "source_context_refs": {},
                    "candidate_row_refs": set(),
                })
                group["request_ids"].add(request_id)
                group["source_context_refs"][request_id] = request
                group["candidate_row_refs"].add((case_id, plan["au_row_key"],
                                                  binding.get("task_id"), request_id))
                case_context_ids[case_id].add(context_id)
                case_residual_request_ids[case_id].add(request_id)
                residual_request_refs[request_id].append({
                    "case_id": case_id,
                    "au_row_key": plan["au_row_key"],
                    "task_id": binding.get("task_id"),
                })

    row_priority: dict[tuple[str, str], dict[str, Any]] = {}
    remaining_count = 0
    for case_id, record in ranked.items():
        planned_keys = [plan["au_row_key"] for plan in row_plan_by_case[case_id]]
        ranked_candidates = record.get("candidates")
        if not isinstance(ranked_candidates, list):
            raise ValueError(f"malformed ranking candidates for {case_id}")
        rank_keys = [item.get("row_key") for item in ranked_candidates]
        if len(rank_keys) != len(set(rank_keys)) or set(rank_keys) != set(planned_keys):
            raise ValueError(f"ranking/source candidate rows differ for {case_id}")
        for item in ranked_candidates:
            row_priority[(case_id, item["row_key"])] = item
        remaining_count += sum(row_gate[(case_id, key)]["state"] != "conflict"
                               for key in planned_keys)

    queue_records = []
    review_case_count = 0
    for case_plan in case_plans:
        case_id = case_plan["case_id"]
        gate_case = case_gate[case_id]
        disposition = gate_case.get("disposition")
        if disposition == "review":
            review_case_count += 1
        ordered_plans = row_plan_by_case[case_id]
        candidates = []
        for source_order, plan in enumerate(ordered_plans):
            row_key = plan["au_row_key"]
            gate_row = row_gate[(case_id, row_key)]
            priority = row_priority[(case_id, row_key)]
            excluded = gate_row.get("state") == "conflict"
            candidates.append({
                "source_order": source_order,
                "row_key": row_key,
                "raw_gate_state": gate_row.get("state"),
                "excluded_by_existing_gate_conflict": excluded,
                "exclusion_reason": "existing_raw_catalogue_conflict" if excluded else None,
                "condition_count": gate_row.get("condition_count"),
                "rapidfuzz_priority_rank": priority["rank"],
                "rapidfuzz_tie_size": priority["tie_size"],
                "rapidfuzz_score": priority["score"],
                "raw_axis_values": priority["raw_axis_values"],
                "source_row_refs": {
                    "au_row_index": plan.get("au_row_index"),
                    "au_row_sku_id": plan.get("au_row_sku_id"),
                    "au_row_axis_source_spans": plan.get("au_row_axis_source_spans"),
                    "bindings": plan.get("bindings", []),
                },
            })
        remaining = [candidate for candidate in candidates
                     if not candidate["excluded_by_existing_gate_conflict"]]
        remaining.sort(key=lambda item: (item["rapidfuzz_priority_rank"],
                                         item["source_order"]))
        for index, candidate in enumerate(remaining):
            candidate["priority_order"] = index + 1
            candidate["priority_role"] = (
                "initial_read_priority" if disposition == "review" and index < 3
                else "backfill"
            )
        initial_keys = ([candidate["row_key"] for candidate in remaining[:3]]
                        if disposition == "review" else [])
        backfill_keys = ([candidate["row_key"] for candidate in remaining[3:]]
                         if disposition == "review"
                         else [candidate["row_key"] for candidate in remaining])
        queue_records.append({
            "schema": "sku-installed-library-helper-queue-case-v1",
            "case_id": case_id,
            "dossier_id": case_plan.get("dossier_id"),
            "source_version": case_plan.get("source_version"),
            "source_split": case_plan.get("split"),
            "advisory_gate": {
                "disposition": disposition,
                "decision": gate_case.get("decision"),
                "production_adoption_allowed": False,
                "helper_work_required": disposition == "review",
            },
            "priority_policy": {
                "ranking_view": RANKING_VIEW,
                "ranking_scorer": RANKING_SCORER,
                "initial_read_priority_row_keys": initial_keys,
                "backfill_row_keys": backfill_keys,
                "all_remaining_candidates_retained": True,
                "first_three_are_reading_order_only": True,
                "winner_acceptance_allowed_before_all_unknown_competitors_checked": False,
                "all_unknown_competitors_must_be_checked_before_exclusive_acceptance": True,
            },
            "candidate_counts": {
                "all_original_rows": len(candidates),
                "excluded_by_existing_gate_conflict": sum(
                    item["excluded_by_existing_gate_conflict"] for item in candidates),
                "remaining_nonconflict_rows": len(remaining),
            },
            "all_candidate_rows_in_source_order": candidates,
            "remaining_candidate_row_keys_in_priority_order": [item["row_key"] for item in remaining],
            "residual_unknown_request_ids": sorted(case_residual_request_ids[case_id]),
            "residual_context_ids": sorted(case_context_ids[case_id]),
        })

    residual_context_records = []
    for context_id, group in sorted(context_groups.items()):
        residual_context_records.append({
            "schema": group["schema"],
            "context_id": context_id,
            "deduplication_key_fields": list(CONTEXT_KEYS),
            "context_key": group["context_key"],
            "request_ids": sorted(group["request_ids"]),
            "source_context_refs": [group["source_context_refs"][request_id]
                                    for request_id in sorted(group["request_ids"])],
            "candidate_row_refs": [
                {"case_id": case_id, "au_row_key": row_key,
                 "task_id": task_id, "request_id": request_id}
                for case_id, row_key, task_id, request_id
                in sorted(group["candidate_row_refs"])
            ],
        })
    residual_request_records = []
    for request_id in sorted(request_context_key):
        residual_request_records.append({
            "schema": "sku-installed-library-residual-request-v1",
            "request_id": request_id,
            "raw_gate_state": request_state[request_id],
            "context_id": request_context_key[request_id],
            "source_request": request_by_id[request_id],
            "candidate_row_refs": sorted(residual_request_refs[request_id],
                                          key=lambda ref: (ref["case_id"], ref["au_row_key"],
                                                           ref["task_id"] or "")),
        })

    actual_counts = {
        "case_count": len(queue_records),
        "raw_review_cases": review_case_count,
        "source_case_row_count": len(row_plans),
        "remaining_candidates": remaining_count,
        "residual_request_count": len(residual_request_records),
        "residual_context_count": len(residual_context_records),
    }
    if (review_case_count != EXPECTED["raw_review_cases"]
            or remaining_count != EXPECTED["remaining_candidates"]
            or len(residual_context_records) != EXPECTED["residual_contexts"]):
        raise ValueError(f"queue metrics differ from expected frozen workload: {actual_counts}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    queue_path = output / "case-queue.jsonl"
    request_path = output / "residual-requests.jsonl"
    context_path = output / "residual-contexts.jsonl"
    _write_jsonl(queue_path, queue_records)
    _write_jsonl(request_path, residual_request_records)
    _write_jsonl(context_path, residual_context_records)

    source_files = [SOURCE / name for name in
                    ("manifest.json", "requests.jsonl", "row-plans.jsonl", "case-plans.jsonl")]
    selected_files = [SELECTION / name for name in
                      ("OUTPUT_FREEZE.json", "raw-cases.jsonl", "raw-rows.jsonl", "raw-requests.jsonl")]
    ranking_files = [RANKING / name for name in ("OUTPUT_FREEZE.json", "rankings.jsonl")]
    view_path = frozen["view_path"]
    source_pins = {str(path.relative_to(ROOT)): _sha(path)
                   for path in source_files + selected_files + ranking_files + [view_path]}
    output_paths = [queue_path, request_path, context_path]
    manifest = {
        "schema": "sku-installed-library-helper-queue-freeze-v1",
        "source_split": "dev",
        "independent_test_read": False,
        "labels_read": False,
        "annotations_read": False,
        "grader_output_read": False,
        "helper_analysis_read_as_input": False,
        "model_inference_count": 0,
        "custom_sku_parser_used": False,
        "category_or_semantic_rules_added": False,
        "generic_llm_prompt_or_api_call_created": False,
        "case_accept_reject_is_advisory_only": True,
        "production_adoption_allowed": False,
        "source_manifest_code_sha256": source_manifest.get("preparer_code_sha256"),
        "selection_code_sha256": selection_freeze.get("code_sha256"),
        "gate_code_sha256": selection_freeze.get("gate_code_sha256"),
        "ranking_input_code_sha256": _read_json(RANKING / "INPUT_FREEZE.json").get(
            "source_sha256", {}).get("ranking_code"),
        "queue_builder_code_sha256": _sha(Path(__file__).resolve()),
        "selected_mode": "raw",
        "selected_view_definition": "literal raw values with JPY excluded upstream",
        "priority_source": {"view": RANKING_VIEW, "scorer": RANKING_SCORER},
        "deduplication_key_fields": list(CONTEXT_KEYS),
        "priority_policy": {
            "first_three_are_initial_read_priority_only": True,
            "all_remaining_nonconflict_candidates_are_backfill": True,
            "no_candidate_permanently_pruned_by_priority": True,
            "acceptance_requires_all_unknown_competitors_checked": True,
        },
        "counts": actual_counts,
        "expected_counts": EXPECTED,
        "source_pins_sha256": source_pins,
        "outputs": {path.name: {"bytes": path.stat().st_size, "sha256": _sha(path)}
                    for path in output_paths},
    }
    with (output / "MANIFEST.json").open("xb") as stream:
        stream.write(_json_bytes(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=LAB / "price-free/helper-queue-v1",
                        help="new output directory; existing directories are refused")
    args = parser.parse_args(argv)
    manifest = build_queue(args.output)
    print(json.dumps({"output": str(args.output), "counts": manifest["counts"],
                      "outputs": manifest["outputs"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
