#!/usr/bin/env python3
"""Replay frozen retrieval and relation outputs against separate machine labels.

Retrieval logits select literal windows only. They never prove a condition or
its applicability to the selected AU row. This reports whole residual relation
diagnostics, not SKU accuracy, and does not select operating thresholds.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOP_KS = (1, 3, 5)
THRESHOLDS = (0.5, 0.7, 0.9)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


RELATION = module("ranked_probe_relation", HERE / "score_trained_relation_probe_v1.py")
RANKING = module("ranked_probe_ranking", HERE / "trial_generic_evidence_reranker_v1.py")
sha, read = RELATION.sha, RELATION.read


def verify_named_files(directory, hashes, prefix=""):
    for name, expected in hashes.items():
        if Path(name).name != name:
            raise ValueError("unsafe frozen artifact filename")
        if sha(directory / (prefix + name)) != expected:
            raise ValueError("frozen artifact SHA mismatch: " + prefix + name)


def unique_by_id(rows, label):
    result = {}
    for row in rows:
        if row["id"] in result:
            raise ValueError("duplicate " + label + " ID")
        result[row["id"]] = row
    return result


def load_rankings_verified(ranking_dir):
    summary = json.loads((ranking_dir / "summary.json").read_text(encoding="utf-8"))
    freeze = json.loads((ranking_dir / "freeze.json").read_text(encoding="utf-8"))
    required = {"source-requests.jsonl", "source-empty-quote-requests.jsonl", "source-manifest.json",
                "ranking-requests.jsonl", "rankings.jsonl", "top-k-windows.jsonl", "freeze.json"}
    if not required <= set(summary["output_sha256"]):
        raise ValueError("ranking completion receipt lacks required output hashes")
    verify_named_files(ranking_dir, summary["output_sha256"])
    verify_named_files(ranking_dir / "code", freeze["code_sha256"])
    verify_named_files(ranking_dir, freeze["input_sha256"], "source-")
    if tuple(freeze["top_ks_for_diagnostics"]) != TOP_KS:
        raise ValueError("ranking topKs differ from the frozen diagnostic contract")
    if freeze.get("score_is_condition_confidence") is not False or freeze.get("scope_proven") is not False:
        raise ValueError("retrieval scores must not attest condition confidence or scope")
    if sha(ranking_dir / "ranking-requests.jsonl") != freeze["ranking_requests_sha256"]:
        raise ValueError("frozen ranking request SHA mismatch")
    source_manifest = json.loads((ranking_dir / "source-manifest.json").read_text(encoding="utf-8"))
    for name in ("requests.jsonl", "empty-quote-requests.jsonl"):
        if sha(ranking_dir / ("source-" + name)) != source_manifest["output_sha256"][name]:
            raise ValueError("original relation source manifest mismatch")
    requests = read(ranking_dir / "source-requests.jsonl")
    empty = read(ranking_dir / "source-empty-quote-requests.jsonl")
    if len(requests) != source_manifest["request_count"] or len(empty) != source_manifest["empty_literal_quote_request_count"]:
        raise ValueError("original relation source count mismatch")
    expected = RANKING.prepare_rows(requests, empty, freeze.get("query_style", "axis_options"))
    ranking_requests = read(ranking_dir / "ranking-requests.jsonl")
    if ranking_requests != expected:
        raise ValueError("ranking request differs from complete original alternative/window binding")
    predictions = read(ranking_dir / "rankings.jsonl")
    expected_by_id = unique_by_id(expected, "ranking request")
    predictions_by_id = unique_by_id(predictions, "ranking prediction")
    if set(predictions_by_id) != set(expected_by_id):
        raise ValueError("missing or extra ranking output window")
    groups = defaultdict(list)
    for prediction in predictions:
        if any(prediction.get(k) != v for k, v in expected_by_id[prediction["id"]].items()):
            raise ValueError("returned ranking original window/alternative binding mismatch")
        status = prediction["status"]
        if status == "ok":
            if not isinstance(prediction["score"], (int, float)) or not math.isfinite(prediction["score"]):
                raise ValueError("nonfinite relevance logit")
            if prediction.get("score_kind") != "raw_logit" or prediction["excluded_from_inference"]:
                raise ValueError("invalid eligible ranking record")
            groups[prediction["task_id"]].append(prediction)
        elif status not in {"input_too_long", "empty_literal_quote", "missing"}:
            raise ValueError("unrecognized ranking exclusion")
        elif prediction.get("rank") is not None or prediction.get("score") is not None:
            raise ValueError("excluded window must not have a retrieval score or rank")
        if status == "empty_literal_quote" and prediction["passage"].strip():
            raise ValueError("empty literal quote status is inconsistent")
    selections = read(ranking_dir / "top-k-windows.jsonl")
    selection_by_task = {}
    all_tasks = {r["task_id"] for r in expected}
    for selection in selections:
        task = selection["task_id"]
        if task in selection_by_task or task not in all_tasks:
            raise ValueError("duplicate or foreign ranking selection task")
        ordered = sorted(groups[task], key=lambda r: (-r["score"], r["source_request_id"], r["id"]))
        if any(r["rank"] != i for i, r in enumerate(ordered, 1)):
            raise ValueError("ranking order does not match original relevance logits")
        wanted = {str(k): [r["source_request_id"] for r in ordered[:k]] for k in TOP_KS}
        if selection["top_k_source_request_ids"] != wanted or selection["eligible_window_count"] != len(ordered):
            raise ValueError("topK selection differs from frozen relevance order")
        selection_by_task[task] = selection
    if set(selection_by_task) != all_tasks:
        raise ValueError("ranking selection silently omits an empty task")
    return summary, predictions, selection_by_task, requests + empty


def join_verified(run_dir, ranking_dir):
    relation_summary, records = RELATION.load_verified(run_dir)
    relation_freeze = json.loads((run_dir / "freeze.json").read_text(encoding="utf-8"))
    verify_named_files(run_dir, relation_freeze["input_sha256"])
    if (tuple(relation_summary["thresholds_for_diagnostics"]) != THRESHOLDS
            or tuple(relation_freeze.get("thresholds_for_diagnostics", THRESHOLDS)) != THRESHOLDS
            or relation_summary["primary_threshold"] != 0.9):
        raise ValueError("relation thresholds differ from the frozen diagnostic contract")
    ranking_summary, rankings, selections, source_requests = load_rankings_verified(ranking_dir)
    source_by_id = unique_by_id(source_requests, "source relation request")
    by_id = unique_by_id(records, "relation prediction")
    covered_windows = defaultdict(set)
    for record in records:
        source = source_by_id.get(record["id"])
        if source is None or any(record.get(k) != v for k, v in source.items()):
            raise ValueError("relation prediction is not exactly bound to the ranked source request")
        p = record["provenance"]
        covered_windows[(p["task_id"], p["source_request_id"])].add(record["id"])
        probabilities = record.get("probabilities")
        if probabilities is not None:
            values = list(probabilities.values())
            if (not values or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                                  or not math.isfinite(v) or not 0 <= v <= 1 for v in values)
                    or abs(sum(values) - 1) > 1e-5):
                raise ValueError("invalid relation probabilities")
            RELATION.record_relation(record, THRESHOLDS[0])
    for ranking in rankings:
        key = (ranking["task_id"], ranking["source_request_id"])
        present = covered_windows.get(key, set())
        expected = set(ranking["source_relation_request_ids"])
        if present and present != expected:
            raise ValueError("relation run contains only part of an original window's alternatives")
        required = ranking["source_request_id"] in selections[ranking["task_id"]]["top_k_source_request_ids"]["5"]
        if required and present != expected:
            raise ValueError("relation run is missing a required frozen top5 window")
    return relation_summary, ranking_summary, rankings, selections, source_by_id, by_id


def replay(rankings, selections, source_by_id, predictions_by_id, top_k, threshold):
    """Retain the complete task universe, even if all its windows are skipped."""
    task_windows = defaultdict(list)
    for ranking in rankings:
        task_windows[ranking["task_id"]].append(ranking)
    result = []
    for task in sorted(task_windows):
        selected_ids = set(selections[task]["top_k_source_request_ids"][str(top_k)])
        windows = [r for r in task_windows[task] if r["source_request_id"] in selected_ids]
        rows, unavailable = [], []
        for window in windows:
            for source_id in window["source_relation_request_ids"]:
                if source_id in predictions_by_id:
                    rows.append(predictions_by_id[source_id])
                else:
                    source = deepcopy(source_by_id[source_id])
                    source["probabilities"] = None
                    rows.append(source)
                    unavailable.append(source_id)
        if not rows:
            # Unknown-only placeholders preserve every raw alternative of a
            # real original source window; they never manufacture model input.
            for source_id in task_windows[task][0]["source_relation_request_ids"]:
                source = deepcopy(source_by_id[source_id])
                source["probabilities"] = None
                rows.append(source)
                unavailable.append(source_id)
        aggregate = RELATION.aggregate(rows, threshold)
        if len(aggregate) != 1 or aggregate[0]["task_id"] != task:
            raise ValueError("ranking task covers more than one whole residual condition")
        row = aggregate[0]
        row.update(top_k=top_k, selected_source_request_ids=sorted(selected_ids),
                   missing_relation_output_ids=unavailable,
                   not_inferred_relation_output_ids=[r["id"] for r in rows if r.get("probabilities") is None],
                   excluded_windows=[{"source_request_id": r["source_request_id"], "status": r["status"]}
                                     for r in task_windows[task] if r["status"] != "ok"],
                   available_ranked_window_count=len(windows),
                   production_eligible=False, whole_sku_accuracy=False)
        result.append(row)
    return result


def score(run_dir, ranking_dir, annotation_paths, output):
    if output.exists():
        raise FileExistsError(output)
    relation_summary, ranking_summary, rankings, selections, source_by_id, predictions_by_id = join_verified(run_dir, ranking_dir)
    # No label is read until both completed outputs and their exact cross-run
    # task/window/whole-alternative binding have been verified.
    annotations = {}
    for path in annotation_paths:
        for annotation in read(path):
            key = tuple(annotation[k] for k in ("case_id", "au_row_key", "axis_name", "selected_value"))
            if key in annotations or annotation["relation"] not in {"support", "conflict", "unknown"}:
                raise ValueError("duplicate or invalid machine annotation")
            annotations[key] = annotation
    result = {"relation_run_summary_sha256": sha(run_dir / "summary.json"),
              "ranking_run_summary_sha256": sha(ranking_dir / "summary.json"),
              "relation_prediction_sha256": sha(run_dir / "predictions.jsonl"),
              "rankings_sha256": sha(ranking_dir / "rankings.jsonl"),
              "code_sha256": {p.name: sha(p) for p in (Path(__file__), HERE / "score_trained_relation_probe_v1.py",
                                                      HERE / "trial_generic_evidence_reranker_v1.py")},
              "annotation_sha256": {str(path): sha(path) for path in annotation_paths},
              "verification_completed_before_labels_read": True,
              "top_ks_for_diagnostics": list(TOP_KS), "thresholds_for_diagnostics": list(THRESHOLDS),
              "primary_threshold": relation_summary["primary_threshold"],
              "retrieval_query_style": ranking_summary.get("query_style", "axis_options"),
              "retrieval_scores_are_condition_confidence": False, "scope_proven": False,
              "production_eligible": False, "whole_sku_accuracy": False,
              "machine_labels_not_human_gold": True, "full_page_annotations_vs_available_evidence_only": True,
              "window_status_counts": dict(Counter(r["status"] for r in rankings)),
              "diagnostics": {}}
    for top_k in TOP_KS:
        result["diagnostics"][str(top_k)] = {}
        for threshold in THRESHOLDS:
            rows = replay(rankings, selections, source_by_id, predictions_by_id, top_k, threshold)
            diagnostic = RELATION.metrics(rows, annotations)
            diagnostic["conditions"] = rows
            result["diagnostics"][str(top_k)][str(threshold)] = diagnostic
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--ranking-dir", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.run_dir, args.ranking_dir, args.annotations, args.output)
    print(json.dumps({k: {t: {n: v for n, v in d.items() if n != "conditions"} for t, d in ds.items()}
                      for k, ds in result["diagnostics"].items()}, ensure_ascii=False))
