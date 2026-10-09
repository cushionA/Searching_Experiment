"""Evaluate complete fixed-AU-pool decisions, with embeddings for suggestions.

A low score or a wrong top-1 proposal cannot prove that the complete AU SKU
array lacks a match. Specifications decide acceptance and rejection; unresolved
cases retain model-ranked suggestions for review. This is a reused-data trial.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import evaluate_structured_skus as evaluation


def constrained_pair(task: dict, candidate: dict) -> dict:
    query = task["rakuten"]
    if ("required_fields" in query and "required_fields" in candidate
            and query["attrs"].get("category") != "curtain"):
        # Product identity has already been checked upstream. Descriptions need
        # not list identical optional background facts. Keep every selected SKU
        # axis mandatory, and keep all jointly known facts as conflict guards.
        required = set(query["required_fields"]) | set(candidate["required_fields"])
        jointly_known = set(query["attrs"]) & set(candidate["attrs"])
        fields = required | jointly_known
        scoped_query = {**query, "attrs": {k: v for k, v in query["attrs"].items() if k in fields},
                        "unknown_fields": [k for k in query.get("unknown_fields", []) if k in required or k.startswith("axis:")]}
        scoped_candidate = {**candidate, "attrs": {k: v for k, v in candidate["attrs"].items() if k in fields},
                            "unknown_fields": [k for k in candidate.get("unknown_fields", []) if k in required or k.startswith("axis:")]}
        result = evaluation.gate({**task, "rakuten": scoped_query}, scoped_candidate)
        # A shared explicit named-size selection establishes the SKU condition.
        # A coordinate-order difference in a supplementary flat measurement
        # table does not prove a different named SKU. Numeric selected dimensions
        # remain mandatory and never use this exception.
        if (query["attrs"].get("named_size") is not None
                and query["attrs"].get("named_size") == candidate["attrs"].get("named_size")
                and "size_cm" not in required
                and isinstance(query["attrs"].get("size_cm"), list)
                and isinstance(candidate["attrs"].get("size_cm"), list)
                and sorted(query["attrs"]["size_cm"]) == sorted(candidate["attrs"]["size_cm"])):
            scoped_query["attrs"].pop("size_cm", None)
            scoped_candidate["attrs"].pop("size_cm", None)
            result = evaluation.gate({**task, "rakuten": scoped_query}, scoped_candidate)
    else:
        result = evaluation.gate(task, candidate)
    left = evaluation.identity_attrs(task["rakuten"])
    right = evaluation.identity_attrs(candidate)
    named_standard = {"シングル", "セミシングル", "セミダブル", "ダブル", "クイーン", "キング", "ハーフ", "クォーター"}
    a_name, b_name = left.get("named_size"), right.get("named_size")
    a_dims, b_dims = left.get("size_cm"), right.get("size_cm")
    same_shape = (isinstance(a_dims, list) and isinstance(b_dims, list) and sorted(a_dims) == sorted(b_dims))
    if a_name in named_standard and b_name in named_standard and a_name != b_name and not same_shape:
        result = {**result, "decision": "unmatched", "conflicting_fields": sorted(set(result["conflicting_fields"] + ["named_size"]))}
    # These fields are ordered, unit-normalized selected dimensions, including
    # explicit named-size crosswalks. Body/folded/accessory measurement disputes
    # deliberately retain the original review policy.
    differences = []
    for key in ("size_cm", "panel_size_cm"):
        a, b = left.get(key), right.get(key)
        if (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
                and all(isinstance(x, (int, float)) for x in a + b) and a != b
                and sorted(a) != sorted(b)
                and (not left.get("dimension_role") and not right.get("dimension_role")
                     or left.get("dimension_role") == right.get("dimension_role"))
                and not any(key in x for x in result["unresolved_fields"])):
            differences.append(key)
    if differences:
        result = {**result, "decision": "unmatched",
                  "conflicting_fields": sorted(set(result["conflicting_fields"] + differences))}
    return result


def fixed_pool_prediction(task: dict, ranking: dict) -> dict:
    checked = [(c, constrained_pair(task, c)) for c in task["au_candidates"]]
    if not checked:
        raise ValueError("An actual AU SKU pool must not be empty")
    keys = {c["row_key"] for c, _ in checked}
    if ranking["case_id"] != task["case_id"] or ranking["top_row_key"] not in keys:
        raise ValueError("Ranking does not belong to the fixed AU product")
    matches = [(c, g) for c, g in checked if g["decision"] == "matched"]
    reviews = [(c, g) for c, g in checked if g["decision"] == "review"]
    ranked_keys = [x["row_key"] for x in ranking.get("top10", [])]
    rank = {key: i for i, key in enumerate(ranked_keys)}
    if len(matches) == 1:
        candidate, constraint = matches[0]
        decision, reason = "matched", "unique_source_proven_match"
    elif matches:
        candidate, constraint = min(matches, key=lambda x: rank.get(x[0]["row_key"], len(keys)))
        decision, reason = "review", "multiple_source_proven_rows"
    elif reviews:
        candidate, constraint = min(reviews, key=lambda x: (
            len(x[1]["missing_fields"]) + len(x[1]["unresolved_spelling_fields"]),
            rank.get(x[0]["row_key"], len(keys)), x[0]["row_key"]))
        decision, reason = "review", "at_least_one_unresolved_candidate"
    else:
        candidate, constraint = next(x for x in checked if x[0]["row_key"] == ranking["top_row_key"])
        decision, reason = "unmatched", "every_actual_au_row_has_explicit_conflict"
    return {"case_id": task["case_id"], "top_row_key": candidate["row_key"],
            "decision": decision, "reason": reason, "gate": constraint,
            "actual_au_row_count": len(checked), "proven_match_count": len(matches),
            "unresolved_candidate_count": len(reviews),
            "model_top_row_key": ranking["top_row_key"], "model_score": ranking.get("score"),
            "model_top1_overridden": candidate["row_key"] != ranking["top_row_key"]}


def decision_metrics(tasks: list[dict], gold: list[dict], predictions: list[dict]) -> dict:
    result = evaluation.diagnostic_metrics(tasks, gold, predictions)
    known = [(g, p) for g, p in zip(gold, predictions, strict=True) if g["decision"] != "review"]
    auto = [(g, p) for g, p in known if p["decision"] != "review"]
    correct = sum(g["decision"] == p["decision"] and
                  (p["decision"] != "matched" or p["top_row_key"] in g["gold_row_keys"])
                  for g, p in auto)
    result["automatic_known_case_coverage"] = len(auto) / len(known) if known else None
    result["automatic_known_case_accuracy"] = correct / len(auto) if auto else None
    result["false_reject_count"] = sum(g["decision"] == "matched" and p["decision"] == "unmatched" for g, p in known)
    result["model_top1_override_count"] = sum(p["model_top1_overridden"] for p in predictions)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--inputs-dir", type=Path, required=True)
    parser.add_argument("--au-arrays", type=Path, default=evaluation.DEFAULT_AU_ARRAYS)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing existing output")
    input_paths = [args.tasks, args.ranking, args.labels]
    before = {str(p): evaluation.sha256(p) for p in input_paths}
    tasks = evaluation.read_jsonl(args.tasks)
    rankings = json.loads(args.ranking.read_text(encoding="utf-8"))["rows"]
    if [r["case_id"] for r in rankings] != [t["case_id"] for t in tasks]:
        raise ValueError("Task/ranking case order differs")
    predictions = [fixed_pool_prediction(t, r) for t, r in zip(tasks, rankings, strict=True)]
    args.output.mkdir(parents=True, exist_ok=False)
    evaluation.write_jsonl_new(args.output / "predictions.jsonl", predictions)
    # The model and specification predictions exist before the label join.
    context = evaluation.load_context(args.inputs_dir, args.au_arrays)
    if [c["case_id"] for c in context] != [t["case_id"] for t in tasks]:
        raise ValueError("Source case order differs")
    gold = evaluation.join_labels(context, args.labels)
    results = {}
    for split in ("dev", "test", "all"):
        idx = [i for i, t in enumerate(tasks) if split == "all" or t["split"] == split]
        results[split] = decision_metrics([tasks[i] for i in idx], [gold[i] for i in idx], [predictions[i] for i in idx])
    after = {str(p): evaluation.sha256(p) for p in input_paths}
    if before != after:
        raise ValueError("Input changed")
    evaluation.write_new(args.output / "summary.json", {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": "Complete fixed AU pool; selected axes mandatory; shared page facts guard conflicts; curtain selling composition strict; canonical embeddings suggest review rows",
        "input_sha256_pre": before, "input_sha256_post": after,
        "code_sha256": {Path(__file__).name: evaluation.sha256(Path(__file__)),
                        "evaluate_structured_skus.py": evaluation.sha256(Path(evaluation.__file__))},
        "predictions_sha256": evaluation.sha256(args.output / "predictions.jsonl"),
        "labels_used_for_prediction": False, "synthetic_input_count": 0,
        "human_verified_gold": False, "test_status": "Reused inspected diagnostic; not a fresh holdout",
        "results": results})
    print(json.dumps({s: m["overall"] for s, m in results.items()}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
