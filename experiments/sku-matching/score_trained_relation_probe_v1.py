#!/usr/bin/env python3
"""Frozen-output diagnostics against independently prepared machine labels.

Confidence is uncalibrated. Evidence scope and whole-SKU acceptance are not
proved by this scorer. It never changes labels or selects a threshold.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

RELATIONS = {"entailment": "support", "contradiction": "conflict", "neutral": "unknown"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_verified(run_dir):
    summary = json.loads((run_dir / "summary.json").read_text())
    freeze = json.loads((run_dir / "freeze.json").read_text())
    for name, expected in summary["output_sha256"].items():
        if sha(run_dir / name) != expected:
            raise ValueError(f"completed run SHA mismatch: {name}")
    for name, expected in freeze["code_sha256"].items():
        if sha(run_dir / "code" / name) != expected:
            raise ValueError(f"frozen run code SHA mismatch: {name}")
    requests = read(run_dir / "requests.jsonl")
    records = read(run_dir / "predictions.jsonl")
    if len(requests) != len(records):
        raise ValueError("incomplete prediction output")
    for request, record in zip(requests, records, strict=True):
        if any(record.get(key) != value for key, value in request.items()):
            raise ValueError("request/returned prediction binding mismatch")
    return summary, records


def record_relation(record, threshold):
    probabilities = record.get("probabilities")
    if probabilities is None:
        return "unknown"
    if set(probabilities) == set(RELATIONS):
        probabilities = {RELATIONS[k]: v for k, v in probabilities.items()}
    if set(probabilities) != {"support", "conflict", "unknown"}:
        raise ValueError("unexpected classifier relation labels")
    winner = max(probabilities, key=probabilities.get)
    return winner if probabilities[winner] >= threshold else "unknown"


def aggregate(records, threshold):
    """One fixed condition, with whole-value alternative consistency.

    Alternative support never proves that another choice is contradicted.
    Opposing selected-value evidence or an ambiguous supported alternative
    prevents a support decision. No category-specific cases are used.
    """
    groups = defaultdict(list)
    for record in records:
        p = record["provenance"]
        groups[(p["case_id"], p["au_row_key"], p["axis_name"], p["selected_value"], p["hypothesis_style"])].append(record)
    result = []
    for key, rows in groups.items():
        selected = key[3]
        selected_records = [r for r in rows if r["provenance"]["candidate_option_value"] == selected]
        relations = [record_relation(r, threshold) for r in selected_records]
        other_supported = [r for r in rows if r["provenance"]["candidate_option_value"] != selected
                           and record_relation(r, threshold) == "support"]
        support, conflict = "support" in relations, "conflict" in relations
        relation = ("unknown" if support and (conflict or other_supported) else
                    "support" if support else "conflict" if conflict else "unknown")
        first = rows[0]["provenance"]
        result.append({"case_id": key[0], "au_row_key": key[1], "axis_name": key[2],
            "selected_value": selected, "hypothesis_style": key[4], "task_id": first["task_id"],
            "relation": relation, "threshold": threshold, "scope_proven": False,
            "selected_value_relations": dict(Counter(relations)),
            "support_evidence_ids": [r["id"] for r in selected_records if record_relation(r, threshold) == "support"],
            "conflict_evidence_ids": [r["id"] for r in selected_records if record_relation(r, threshold) == "conflict"],
            "other_supported_evidence_ids": [r["id"] for r in other_supported],
            "fixed_au_product_ref": first["fixed_au_product_ref"], "whole_sku_adoption": "not_decided"})
    return result


def metrics(rows, annotations):
    confusion, total, true_support, predicted_support, actual_support, agreement = Counter(), 0, 0, 0, 0, 0
    for row in rows:
        key = tuple(row[k] for k in ("case_id", "au_row_key", "axis_name", "selected_value"))
        if key not in annotations:
            raise ValueError(f"machine annotation missing exact condition binding: {key}")
        expected, predicted = annotations[key]["relation"], row["relation"]
        confusion[f"{expected}->{predicted}"] += 1
        total += 1
        agreement += expected == predicted
        actual_support += expected == "support"
        predicted_support += predicted == "support"
        true_support += expected == predicted == "support"
    return {"count": total, "confusion": dict(confusion), "agreement": agreement,
            "true_support": true_support, "actual_support": actual_support,
            "predicted_support": predicted_support, "false_support": predicted_support - true_support,
            "support_precision": true_support / predicted_support if predicted_support else None,
            "support_recall": true_support / actual_support if actual_support else None,
            "support_precision_defined": bool(predicted_support)}


def score(run_dir: Path, annotation_paths: list[Path], output: Path):
    if output.exists():
        raise FileExistsError(output)
    summary, records = load_verified(run_dir)  # Verify predictions before reading labels.
    annotations = {}
    for path in annotation_paths:
        for annotation in read(path):
            key = tuple(annotation[k] for k in ("case_id", "au_row_key", "axis_name", "selected_value"))
            if key in annotations:
                raise ValueError("duplicate annotation condition")
            if annotation["relation"] not in {"support", "conflict", "unknown"}:
                raise ValueError("unexpected machine relation")
            annotations[key] = annotation
    result = {"run_summary_sha256": sha(run_dir / "summary.json"),
        "prediction_sha256": sha(run_dir / "predictions.jsonl"), "code_sha256": sha(Path(__file__)),
        "annotation_sha256": {str(path): sha(path) for path in annotation_paths},
        "machine_labels_not_human_gold": True, "primary_threshold": summary["primary_threshold"],
        "full_page_annotations_vs_available_evidence_only": True, "scope_proven": False,
        "whole_sku_accuracy": False, "production_eligible": False, "diagnostics": {}}
    for threshold in summary["thresholds_for_diagnostics"]:
        rows = aggregate(records, threshold)
        styles = sorted({r["hypothesis_style"] for r in rows})
        result["diagnostics"][str(threshold)] = {style: metrics([r for r in rows if r["hypothesis_style"] == style], annotations) for style in styles}
        result["diagnostics"][str(threshold)]["conditions"] = rows
    result["unthresholded_diagnostic"] = {}
    rows = aggregate(records, 0.0)
    for style in sorted({r["hypothesis_style"] for r in rows}):
        result["unthresholded_diagnostic"][style] = metrics([r for r in rows if r["hypothesis_style"] == style], annotations)
    output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.run_dir, args.annotations, args.output)
    print(json.dumps({"primary": {k:v for k,v in result["diagnostics"][str(result['primary_threshold'])].items() if k != 'conditions'},
                      "comparison": {t:{k:v for k,v in d.items() if k!='conditions'} for t,d in result['diagnostics'].items()}},ensure_ascii=False))
