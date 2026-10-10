"""Posthoc diagnostics for persisted trials against reused machine annotations."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from evaluate_nonllm_binary_gate_v1 import adapt_label
from run_integrated_sku_gate_v1 import ROOT, dump, read, sha


def verify_run(path):
    manifest = json.loads((path / "manifest.json").read_text())
    records = manifest.get("files", manifest.get("artifacts", {}))
    if not records or "predictions.jsonl" not in records:
        raise ValueError("Prediction manifest missing")
    for name, expected in records.items():
        digest = expected["sha256"] if isinstance(expected, dict) else expected
        if sha(path / name) != digest:
            raise ValueError("Frozen output changed: " + str(path / name))


def score(predictions, annotations):
    refs = {row["case_id"]: adapt_label(row) for row in annotations}
    selected = [row for row in predictions if row["case_id"] in refs]
    if len(selected) != len(refs) or len({p["case_id"] for p in selected}) != len(refs):
        raise ValueError("Prediction/reference case set differs")
    counts, errors, unresolved = Counter(), [], []
    for pred in selected:
        reference = refs[pred["case_id"]]
        matched = reference["decision"] == "matched"
        accepted = pred["decision"] == "accept"
        if reference["decision"] not in ("matched", "unmatched"):
            counts["unresolved_reference_accept" if accepted else "unresolved_reference_drop"] += 1
            if accepted:
                unresolved.append(pred["case_id"])
            continue
        correct_row = pred["au_row_key"] in reference.get("matching_au_row_keys", [])
        key = ("correct_accept" if matched and correct_row else "wrong_row_accept" if matched else "false_accept") if accepted else ("missed_match" if matched else "correct_drop")
        counts[key] += 1
        if key in ("wrong_row_accept", "false_accept", "missed_match"):
            errors.append({"case_id": pred["case_id"], "kind": key,
                           "au_row_key": pred["au_row_key"], "reason": pred["reason"]})
    denom = counts["correct_accept"] + counts["wrong_row_accept"] + counts["false_accept"]
    positives = sum(r["decision"] == "matched" for r in refs.values())
    return {"reference_cases": len(refs), "counts": dict(counts),
            "precision_against_resolved_machine_references": counts["correct_accept"] / denom if denom else None,
            "recall_against_machine_references": counts["correct_accept"] / positives if positives else None,
            "errors": errors, "unresolved_reference_accepted_case_ids": unresolved}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--hybrid-run", type=Path, action="append", default=[])
    parser.add_argument("--model-run", type=Path, action="append", default=[])
    parser.add_argument("--tasks-run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    sku_runs = [args.source_run, *args.hybrid_run]
    for path in [*sku_runs, *args.model_run]:
        verify_run(path)
    # All predictions have been persisted and hash verified before labels open.
    labels_paths = {"legacy": ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl",
                    "novel": ROOT / ".lab-output/sku-novel-luna-labels-20261010-v1/labels.jsonl"}
    labels = {k: read(p) for k, p in labels_paths.items()}
    scores, changes = {}, {}
    baseline = {p["case_id"]: p for p in read(args.source_run / "predictions.jsonl")}
    for path in sku_runs:
        predictions = read(path / "predictions.jsonl")
        if {p["case_id"] for p in predictions} != set(baseline):
            raise ValueError("Hybrid lost or added a SKU case")
        scores[path.name] = {name: score(predictions, rows) for name, rows in labels.items()}
        changes[path.name] = [{"case_id": p["case_id"], "before": baseline[p["case_id"]]["decision"],
                              "after": p["decision"], "au_row_key": p["au_row_key"]}
                             for p in predictions if (p["decision"], p["au_row_key"]) !=
                             (baseline[p["case_id"]]["decision"], baseline[p["case_id"]]["au_row_key"])]
    controls = {}
    if args.tasks_run:
        control_rows = read(args.tasks_run / "structural-controls.jsonl")
        for path in args.model_run:
            by_task = {r["task_id"]: r for r in read(path / "predictions.jsonl")}
            counts = Counter((r["reference_relation"], by_task[r["task_id"]]["proposal"]) for r in control_rows)
            controls[path.name] = {"reference_kind": "reused structural source annotations, not gold",
                                   "target_counts": {a + "->" + b: n for (a, b), n in counts.items()},
                                   "distinct_model_tasks": len({r["task_id"] for r in control_rows})}
    dump(args.output, {"classification": "development diagnostics against reused human-unverified machine labels; not holdout or gold",
                      "predictions_persisted_and_verified_before_label_read": True,
                      "label_sha256": {k: sha(p) for k, p in labels_paths.items()},
                      "scores": scores, "changed_cases": changes, "structural_relation_controls": controls,
                      "evaluator_sha256": sha(Path(__file__))})
    print(json.dumps({k: {g: s["counts"] for g, s in v.items()} for k, v in scores.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
