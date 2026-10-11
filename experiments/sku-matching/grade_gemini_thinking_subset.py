#!/usr/bin/env python3
"""Externally grade a frozen four-case Gemini LOW-thinking subset.

This script performs no inference and never stores source labels in the checkout.
It verifies the frozen four-case run against its frozen Gemini v2 source, then
scores both sets of predictions on the same external 1,439-label set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
SOURCE = EXP / "results/20261011T002300Z-gemini-luna50-replay-v2"
V13 = EXP / "results/20261010T210700Z-luna-width-contents-v13"
V13_EVAL = Path("/workspace/sku-width-contents-v13-evaluation-20261010T211800Z")
EXPECTED_RUN = EXP / "results/20261011T011921Z-gemini-thinking-off-errors4"

sys.path.insert(0, str(EXP))
import evaluate_frozen_sku_accuracy as scorer  # noqa: E402
import trial_luna_sku_matching as core  # noqa: E402


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path: Path) -> Any:
    return core.read(path)


def verify_freeze(run_dir: Path, filename: str) -> dict[str, str]:
    freeze_path = run_dir / filename
    if not freeze_path.is_file():
        raise ValueError(f"missing {filename}: {freeze_path}")
    manifest = read(freeze_path)
    entries = manifest.get("sha256")
    if not isinstance(entries, dict) or not entries:
        raise ValueError(f"empty SHA-256 freeze: {freeze_path}")
    for rel, expected in entries.items():
        path = (EXP / rel).resolve()
        if not path.is_relative_to(EXP.resolve()):
            raise ValueError(f"freeze path escapes experiment tree: {rel}")
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"freeze hash mismatch: {rel}")
    return entries


def cases_from(path: Path) -> list[dict[str, Any]]:
    obj = read(path)
    cases = obj.get("cases") if isinstance(obj, dict) else obj
    if not isinstance(cases, list) or any(not isinstance(c, dict) for c in cases):
        raise ValueError(f"expected an array of case objects: {path}")
    return cases


def unique_by_id(rows: list[dict[str, Any]], where: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row.get("case_id")
        if not isinstance(cid, str) or not cid or cid in result:
            raise ValueError(f"missing or duplicate case_id in {where}")
        result[cid] = row
    return result


def candidate_keys(cases: list[dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for case in cases:
        cid = case.get("case_id")
        rows = case.get("au_rows")
        if not isinstance(cid, str) or not isinstance(rows, list):
            raise ValueError("input case missing case_id or au_rows")
        keys = [row.get("row_key") for row in rows if isinstance(row, dict)]
        if len(keys) != len(rows) or any(not isinstance(k, str) or not k for k in keys) or len(set(keys)) != len(keys):
            raise ValueError(f"missing or duplicate AU row_key in {cid}")
        result[cid] = set(keys)
    return result


def jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSON object rows: {path}")
    return rows


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def verify_projection(run_dir: Path, case_ids: list[str]) -> tuple[Path, Path, Path, Path]:
    run_inputs_path = run_dir / "derived/inputs.json"
    run_contexts_path = run_dir / "derived/source-contexts.json"
    source_inputs_path = SOURCE / "derived/inputs.json"
    source_contexts_path = SOURCE / "derived/source-contexts.json"
    if not all(p.is_file() for p in (run_inputs_path, run_contexts_path, source_inputs_path, source_contexts_path)):
        raise ValueError("missing frozen inputs or source contexts")

    run_inputs = cases_from(run_inputs_path)
    source_inputs = cases_from(source_inputs_path)
    run_by_id = unique_by_id(run_inputs, "subset inputs")
    source_by_id = unique_by_id(source_inputs, "SOURCE inputs")
    if [case.get("case_id") for case in run_inputs] != case_ids:
        raise ValueError("subset inputs must cover the plan's four case IDs in plan order")
    if not set(case_ids) <= set(source_by_id):
        raise ValueError("planned case IDs are missing from SOURCE inputs")
    if [run_by_id[cid] for cid in case_ids] != [source_by_id[cid] for cid in case_ids]:
        raise ValueError("subset inputs do not equal the selected SOURCE input projection")

    run_contexts = read(run_contexts_path)
    source_contexts = read(source_contexts_path)
    if not isinstance(run_contexts, dict) or not isinstance(source_contexts, dict):
        raise ValueError("source contexts must be objects keyed by case_id")
    if set(run_contexts) != set(case_ids):
        raise ValueError("subset source contexts must cover exactly the plan's four case IDs")
    if any(run_contexts[cid] != source_contexts.get(cid) for cid in case_ids):
        raise ValueError("subset source contexts differ from the selected SOURCE projection")
    return run_inputs_path, run_contexts_path, source_inputs_path, source_contexts_path


def check_matching_bodies(run_dir: Path, plan: dict[str, Any]) -> tuple[int, list[str]]:
    dispatch_path = run_dir / "matching-dispatch-manifest.json"
    if not dispatch_path.is_file():
        raise ValueError("missing matching-dispatch-manifest.json")
    dispatch = read(dispatch_path)
    entries = dispatch.get("entries") if isinstance(dispatch, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("matching dispatch manifest has no entries")
    plan_ids = set(plan["case_ids"])
    if any(e.get("original_case_id") not in plan_ids for e in entries):
        raise ValueError("matching dispatch contains a case outside the four-case plan")
    if len(entries) != plan.get("matching_calls"):
        raise ValueError("matching call count differs from the frozen plan")

    checked_hashes: list[str] = []
    for entry in entries:
        request_rel = entry.get("request")
        control_rel = entry.get("control_request")
        if not isinstance(request_rel, str) or not isinstance(control_rel, str):
            raise ValueError("each matching entry must reference its request and SOURCE control request")
        request_path = (run_dir / request_rel).resolve()
        control_path = (EXP / control_rel).resolve()
        if not request_path.is_relative_to(run_dir.resolve()) or not control_path.is_relative_to(EXP.resolve()):
            raise ValueError("matching request reference escapes its allowed directory")
        if not request_path.is_file() or not control_path.is_file():
            raise ValueError("missing matching request body")
        request = read(request_path)
        control = read(control_path)
        body = request.get("body") if isinstance(request, dict) else None
        control_body = control.get("body") if isinstance(control, dict) else None
        if not isinstance(body, dict) or not isinstance(control_body, dict):
            raise ValueError("matching request must contain a body object")
        generation = body.get("generationConfig")
        if not isinstance(generation, dict) or generation.get("thinkingConfig") != plan.get("thinking_config_requested"):
            raise ValueError("matching request does not carry the planned LOW thinkingConfig")
        control_generation = control_body.get("generationConfig")
        if isinstance(control_generation, dict) and "thinkingConfig" in control_generation:
            raise ValueError("SOURCE control request unexpectedly has a thinkingConfig")
        normalized = json.loads(json.dumps(body))
        del normalized["generationConfig"]["thinkingConfig"]
        if normalized != control_body:
            raise ValueError(f"matching body differs from SOURCE control except thinkingConfig: {entry.get('case_id')}")
        if entry.get("control_request_sha256") != digest(control_path):
            raise ValueError("SOURCE control request hash differs from dispatch manifest")
        checked_hashes.extend([digest(request_path), digest(control_path)])
    return len(entries), checked_hashes


def read_labels(labels_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int]]:
    files = sorted(p for p in labels_dir.glob("*.jsonl") if p.is_file())
    if {p.name for p in files} != {"legacy-labels.jsonl", "novel-labels.jsonl"}:
        raise ValueError("external labels must be the unchanged legacy/novel V13 files")
    hashes = {p.name: digest(p) for p in files}
    counts: dict[str, int] = {}
    labels: list[dict[str, Any]] = []
    for path in files:
        rows = jsonl_rows(path)
        counts[path.name] = len(rows)
        labels.extend(rows)
    if len(labels) != 1439:
        raise ValueError(f"expected the frozen external 1439-label set; found {len(labels)}")
    if len({row.get("case_id") for row in labels}) != len(labels):
        raise ValueError("duplicate case_id in frozen external labels")
    return labels, hashes, counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path, help="completed four-case LOW run inside this experiment")
    ap.add_argument("--labels-dir", required=True, type=Path, help="external frozen V13 labels directory")
    ap.add_argument("--output-dir", required=True, type=Path, help="new external evaluation output directory")
    args = ap.parse_args()

    run_dir, labels_dir, output_dir = (p.resolve() for p in (args.run_dir, args.labels_dir, args.output_dir))
    if run_dir != EXPECTED_RUN.resolve():
        raise ValueError(f"run-dir must be the frozen four-case experiment: {EXPECTED_RUN}")
    if labels_dir != (V13_EVAL / "labels").resolve():
        raise ValueError("labels-dir must be the existing V13 external labels directory; do not copy labels")
    for name, path in (("labels-dir", labels_dir), ("output-dir", output_dir)):
        if path == REPO.resolve() or path.is_relative_to(REPO.resolve()):
            raise ValueError(f"{name} must stay outside the checkout")
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite evaluation output: {output_dir}")

    # Validate plan and frozen artifacts before any access to external labels.
    plan_path = run_dir / "replay-plan.json"
    if not plan_path.is_file():
        raise ValueError("missing replay-plan.json")
    plan = read(plan_path)
    case_ids = plan.get("case_ids")
    if (plan.get("case_count") != 4 or not isinstance(case_ids, list) or len(case_ids) != 4
            or len(set(case_ids)) != 4 or plan.get("thinking_mode") != "low"
            or plan.get("model_requested") != "gemini-3.5-flash-lite"
            or plan.get("thinking_config_requested") != {"thinkingLevel": "LOW", "includeThoughts": False}):
        raise ValueError("plan does not describe the frozen four-case Gemini LOW run")
    if plan.get("internal_thinking_disabled_verified") is not False:
        raise ValueError("plan must preserve that internal thinking disablement is unverified")
    run_protocol = verify_freeze(run_dir, "frozen-protocol.json")
    run_grading_freeze = verify_freeze(run_dir, "grading-freeze.json")
    source_protocol = verify_freeze(SOURCE, "frozen-protocol.json")
    source_grading_freeze = verify_freeze(SOURCE, "grading-freeze.json")
    input_paths = verify_projection(run_dir, case_ids)

    # Check selected input coverage and both prediction sets without labels.
    subset_inputs = cases_from(input_paths[0])
    keys = candidate_keys(subset_inputs)
    if len(keys) != 4 or sum(len(v) for v in keys.values()) != plan.get("candidate_rows"):
        raise ValueError("four-case input coverage or candidate-row count differs from the frozen plan")
    run_prediction_path = run_dir / "derived/reviewed-summary.json"
    source_prediction_path = SOURCE / "derived/reviewed-summary.json"
    if not run_prediction_path.is_file() or not source_prediction_path.is_file():
        raise ValueError("both completed runs must have derived/reviewed-summary.json")
    run_predictions = cases_from(run_prediction_path)
    source_predictions_all = cases_from(source_prediction_path)
    low_by_id = unique_by_id(run_predictions, "LOW predictions")
    source_by_id = unique_by_id(source_predictions_all, "SOURCE predictions")
    if set(low_by_id) != set(case_ids) or len(low_by_id) != 4:
        raise ValueError("LOW predictions must cover exactly the four planned case IDs")
    if not set(case_ids) <= set(source_by_id):
        raise ValueError("SOURCE predictions do not cover all four planned case IDs")
    source_predictions = [source_by_id[cid] for cid in case_ids]
    matching_count, matching_hashes = check_matching_bodies(run_dir, plan)

    source_record_path = V13_EVAL / "grading-source-manifest.json"
    if not source_record_path.is_file():
        raise ValueError("missing external V13 grading source manifest")
    source_record = read(source_record_path)
    if not source_record.get("source_hashes_unchanged") or source_record.get("human_verified") is not False:
        raise ValueError("external V13 label source manifest is not verified/frozen")
    pre_hashes = source_record.get("all_source_sha256_pre")
    if not isinstance(pre_hashes, dict) or pre_hashes != source_record.get("all_source_sha256_post"):
        raise ValueError("external V13 source hashes changed during its grading run")

    labels, label_hashes_before, label_counts = read_labels(labels_dir)
    low_score = scorer.score(run_predictions, labels, keys)
    source_score = scorer.score(source_predictions, labels, keys)
    label_hashes_after = {p.name: digest(p) for p in sorted(labels_dir.glob("*.jsonl")) if p.is_file()}
    if label_hashes_after != label_hashes_before:
        raise ValueError("external label files changed during grading")

    low_cases = {row["case_id"]: row for row in low_score["cases"]}
    source_cases = {row["case_id"]: row for row in source_score["cases"]}
    low_pred = {row["case_id"]: row for row in run_predictions}
    source_pred = {row["case_id"]: row for row in source_predictions}
    paired = []
    for cid in case_ids:
        before, after = source_cases[cid], low_cases[cid]
        paired.append({
            "case_id": cid,
            "source_decision": source_pred[cid]["decision"],
            "source_row_key": source_pred[cid].get("row_key"),
            "source_correct": before["correct"],
            "low_decision": low_pred[cid]["decision"],
            "low_row_key": low_pred[cid].get("row_key"),
            "low_correct": after["correct"],
            "correctness_changed": before["correct"] != after["correct"],
        })

    output_dir.mkdir(parents=True, exist_ok=False)
    save_json(output_dir / "summary.json", {
        "mode": "four-case selected error follow-up; frozen external labels; no inference",
        "interpretation_limit": "These four cases were selected after observing label-derived errors and were not human-verified; do not infer overall accuracy improvement.",
        "case_count": 4,
        "case_ids": case_ids,
        "candidate_row_count": sum(len(v) for v in keys.values()),
        "matching_call_count": matching_count,
        "labels_total": len(labels),
        "labels_dir": str(labels_dir),
        "label_source_manifest_sha256": digest(source_record_path),
        "label_file_sha256": label_hashes_before,
        "label_file_line_counts": label_counts,
        "human_verified": False,
        "source_metrics": source_score["metrics"],
        "low_metrics": low_score["metrics"],
    })
    save_jsonl(output_dir / "source-case-results.jsonl", source_score["cases"])
    save_jsonl(output_dir / "low-case-results.jsonl", low_score["cases"])
    save_json(output_dir / "paired-comparison.json", {
        "same_four_case_ids": True,
        "same_selected_input_projection": True,
        "same_selected_source_context_projection": True,
        "matching_body_equal_except_thinking_config": True,
        "thinking_config": plan["thinking_config_requested"],
        "human_verified": False,
        "population_accuracy_claim_supported": False,
        "cases": paired,
    })
    save_json(output_dir / "source-manifest.json", {
        "run_dir": str(run_dir),
        "run_replay_plan_sha256": digest(plan_path),
        "run_frozen_protocol_entry_count": len(run_protocol),
        "run_grading_freeze_entry_count": len(run_grading_freeze),
        "source_run_dir": str(SOURCE),
        "source_frozen_protocol_entry_count": len(source_protocol),
        "source_grading_freeze_entry_count": len(source_grading_freeze),
        "run_inputs_sha256": digest(input_paths[0]),
        "source_inputs_sha256": digest(input_paths[2]),
        "run_source_contexts_sha256": digest(input_paths[1]),
        "source_source_contexts_sha256": digest(input_paths[3]),
        "run_predictions_sha256": digest(run_prediction_path),
        "source_predictions_sha256": digest(source_prediction_path),
        "matching_request_source_hashes": matching_hashes,
        "labels_external_only": True,
        "label_source_manifest_sha256": digest(source_record_path),
        "label_file_sha256": label_hashes_before,
        "label_hashes_unchanged_during_grade": True,
    })
    print(json.dumps({
        "source_known_case_accuracy": source_score["metrics"]["known_case_accuracy"],
        "low_known_case_accuracy": low_score["metrics"]["known_case_accuracy"],
        "paired_cases": len(paired),
        "labels_total": len(labels),
        "human_verified": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
