#!/usr/bin/env python3
"""Grade frozen SKU decisions against supplied semantic labels, without inference."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

PREDICTIONS = {"adopt", "exclude", "pending", "not_evaluated"}
LABELS = {"matched", "unmatched", "review", "label_unresolved"}


def _object(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} must be a non-empty string")
    return value


def _unique_cases(rows: Any, where: str) -> dict[str, dict]:
    if not isinstance(rows, list):
        raise ValueError(f"{where} must be an array")
    result = {}
    for i, raw in enumerate(rows):
        row = _object(raw, f"{where}[{i}]")
        case_id = _str(row.get("case_id"), f"{where}[{i}].case_id")
        if case_id in result:
            raise ValueError(f"duplicate case_id: {case_id}")
        result[case_id] = row
    return result


def _label_decision(row: dict) -> str:
    if "decision" in row and "label" in row and row["decision"] != row["label"]:
        raise ValueError(f"conflicting label decision and label for {row.get('case_id')!r}")
    decision = row.get("decision", row.get("label"))
    if not isinstance(decision, str) or decision not in LABELS:
        raise ValueError(f"invalid label decision for {row.get('case_id')!r}")
    return decision


def _expected_rows(row: dict, decision: str) -> list[str]:
    keys = row.get("matching_au_row_keys", [])
    if not isinstance(keys, list) or any(not isinstance(k, str) or not k for k in keys):
        raise ValueError(f"matching_au_row_keys must be an array of strings: {row.get('case_id')}")
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate matching AU row key: {row.get('case_id')}")
    if decision == "matched" and not keys:
        raise ValueError(f"matched label has no matching AU row: {row.get('case_id')}")
    if decision == "unmatched" and keys:
        raise ValueError(f"unmatched label has matching AU rows: {row.get('case_id')}")
    if decision in {"review", "label_unresolved"} and keys:
        raise ValueError(f"unknown label has matching AU rows: {row.get('case_id')}")
    return keys


def score(predictions: list[dict], labels: list[dict], candidate_keys_by_case: dict[str, set[str]]) -> dict:
    """Score predicted cases; candidate keys define the admissible rows per case."""
    preds = _unique_cases(predictions, "predictions")
    labs = _unique_cases(labels, "labels")
    if not isinstance(candidate_keys_by_case, dict):
        raise ValueError("candidate_keys_by_case must be an object")
    for case_id, keys in candidate_keys_by_case.items():
        _str(case_id, "candidate case_id")
        if not isinstance(keys, set) or any(not isinstance(k, str) or not k for k in keys):
            raise ValueError(f"candidate keys for {case_id} must be a set of strings")
    result_rows = []
    counts = Counter()
    cm = {truth: {pred: 0 for pred in sorted(PREDICTIONS)} for truth in ("matched", "unmatched")}
    correct = decided_correct = known = known_pred = accepted_known = accepted_known_correct = 0
    row_recall_num = row_recall_den = false_accepts = wrong_rows = missed_positive = positive_abstentions = unknown_accepted = 0
    normalized_labels = {}
    for case_id, label in labs.items():
        truth = _label_decision(label)
        expected = _expected_rows(label, truth)
        # A label may describe cases outside this prediction set; only their
        # intrinsic schema is checked. Candidate membership is checked below.
        normalized_labels[case_id] = (truth, expected)
    for case_id, pred in preds.items():
        decision = pred.get("decision")
        if not isinstance(decision, str) or decision not in PREDICTIONS:
            raise ValueError(f"invalid prediction decision for {case_id}")
        row_key = pred.get("row_key")
        if row_key is not None and (not isinstance(row_key, str) or not row_key):
            raise ValueError(f"invalid predicted row_key for {case_id}")
        if decision == "adopt" and row_key is None:
            raise ValueError(f"adopt prediction has no row_key: {case_id}")
        if decision in {"exclude", "pending", "not_evaluated"} and row_key is not None:
            raise ValueError(f"{decision} prediction must not have row_key: {case_id}")
        if case_id not in candidate_keys_by_case:
            raise ValueError(f"prediction case missing from inputs: {case_id}")
        candidates = candidate_keys_by_case[case_id]
        if row_key is not None and row_key not in candidates:
            raise ValueError(f"predicted row is outside fixed candidates: {case_id}")
        counts[decision] += 1
        label = labs.get(case_id)
        if label is None:
            truth, expected, is_correct, failure = "not_labelled", [], None, "not_labelled"
            counts["unlabelled_count"] += 1
        else:
            truth, expected = normalized_labels[case_id]
            if any(k not in candidates for k in expected):
                raise ValueError(f"label row is outside fixed candidates: {case_id}")
            if truth in {"review", "label_unresolved"}:
                is_correct, failure = None, "unknown_truth"
                counts["unknown_truth_count"] += 1
                if decision == "adopt":
                    unknown_accepted += 1
            else:
                known += 1
                if truth == "matched":
                    row_recall_den += 1
                    if decision == "adopt" and row_key in expected:
                        row_recall_num += 1
                    if decision == "exclude":
                        missed_positive += 1
                    if decision in {"pending", "not_evaluated"}:
                        positive_abstentions += 1
                if decision == "adopt":
                    accepted_known += 1
                    row_ok = truth == "matched" and row_key in expected
                    accepted_known_correct += int(row_ok)
                    false_accepts += int(truth == "unmatched")
                    wrong_rows += int(truth == "matched" and not row_ok)
                is_correct = ((decision == "adopt" and truth == "matched" and row_key in expected)
                              or (decision == "exclude" and truth == "unmatched"))
                failure = None if is_correct else ("positive_abstention" if decision in {"pending", "not_evaluated"} and truth == "matched" else
                          "wrong_row" if decision == "adopt" and truth == "matched" else
                          "false_accept" if decision == "adopt" else "missed_positive" if truth == "matched" else
                          "pending_or_not_evaluated")
                correct += int(is_correct)
                if decision in {"adopt", "exclude"}:
                    known_pred += 1
                    decided_correct += int(is_correct)
                cm[truth][decision] += 1
        result_rows.append({"case_id": case_id, "prediction_decision": decision, "predicted_row_key": row_key,
                            "label_decision": truth, "expected_row_keys": expected, "correct": is_correct,
                            "failure_type": failure})
    counts["unknown_truth_accepted"] = unknown_accepted
    counts["known_false_accepts"] = false_accepts
    counts["wrong_row_accepts"] = wrong_rows
    counts["missed_positive"] = missed_positive
    counts["positive_abstentions"] = positive_abstentions
    counts["known_case_accuracy_numerator"] = correct
    counts["known_case_accuracy_denominator"] = known
    counts["known_decided_accuracy_numerator"] = decided_correct
    counts["known_decided_accuracy_denominator"] = known_pred
    counts["accepted_known_row_precision_numerator"] = accepted_known_correct
    counts["accepted_known_row_precision_denominator"] = accepted_known
    counts["row_match_recall_numerator"] = row_recall_num
    counts["row_match_recall_denominator"] = row_recall_den
    counts["known_coverage_numerator"] = known_pred
    counts["known_coverage_denominator"] = known
    def rate(n, d): return n / d if d else None
    metrics = {
        "known_case_accuracy": {"numerator": correct, "denominator": known, "rate": rate(correct, known)},
        "known_decided_accuracy": {"numerator": decided_correct, "denominator": known_pred, "rate": rate(decided_correct, known_pred)},
        "accepted_known_row_precision": {"numerator": accepted_known_correct, "denominator": accepted_known, "rate": rate(accepted_known_correct, accepted_known)},
        "row_match_recall": {"numerator": row_recall_num, "denominator": row_recall_den, "rate": rate(row_recall_num, row_recall_den)},
        "known_coverage": {"numerator": known_pred, "denominator": known, "rate": rate(known_pred, known)},
        "confusion_matrix": cm,
        **{k: counts[k] for k in ("known_false_accepts", "wrong_row_accepts", "missed_positive", "positive_abstentions", "unknown_truth_count", "unknown_truth_accepted", "unlabelled_count")},
        "decision_counts": {d: counts[d] for d in sorted(PREDICTIONS)},
    }
    return {"metrics": metrics, "cases": result_rows}


def _read_rows(path: Path, key: str | None = None) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".jsonl":
            data = [json.loads(line) for line in raw.splitlines() if line.strip()]
        else:
            data = json.loads(raw)
        if key is not None:
            data = _object(data, str(path)).get(key)
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain an array" + (f" at {key}" if key else ""))
        return data
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON data from {path}: {exc}") from exc


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _inputs(paths: list[Path]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for path in paths:
        rows = _read_rows(path) if path.suffix.lower() == ".jsonl" else _read_array_or_cases(path)
        for i, case in enumerate(rows):
            _object(case, f"{path}[{i}]")
            cid = _str(case.get("case_id"), "input.case_id")
            rows = case.get("au_rows")
            if not isinstance(rows, list):
                raise ValueError(f"input au_rows must be an array: {cid}")
            keys = set()
            for j, row in enumerate(rows):
                _object(row, f"input {cid}.au_rows[{j}]")
                key = _str(row.get("row_key"), f"input {cid}.au_rows[{j}].row_key")
                if key in keys:
                    raise ValueError(f"duplicate AU row_key in input case {cid}: {key}")
                keys.add(key)
            if cid in merged:
                raise ValueError(f"duplicate input case_id: {cid}")
            merged[cid] = keys
    return merged


def _read_array_or_cases(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON data from {path}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("cases")
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain an array or an object with cases array")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--labels", required=True, action="append", type=Path)
    parser.add_argument("--inputs", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError(f"output directory already exists: {args.output}")
    paths = [args.predictions, *args.labels, *args.inputs]
    before = {str(p): _sha(p) for p in paths}
    pred_rows = _read_rows(args.predictions, "cases")
    label_rows = []
    for path in args.labels:
        label_rows.extend(_read_rows(path) if path.suffix.lower() == ".jsonl" else _read_array_or_cases(path))
    candidates = _inputs(args.inputs)
    if not set(r.get("case_id") for r in pred_rows) <= set(candidates):
        raise ValueError("inputs do not contain every prediction case")
    scored = score(pred_rows, label_rows, candidates)
    after = {str(p): _sha(p) for p in paths}
    if before != after:
        raise ValueError("an input file changed during grading")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "summary.json").write_text(json.dumps({
        "mode": "grading-only; no new inference", "created_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256_pre": before, "input_sha256_post": after, "metrics": scored["metrics"]
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output / "case-results.jsonl").open("w", encoding="utf-8") as f:
        for row in scored["cases"]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
