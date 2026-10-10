#!/usr/bin/env python3
"""Replay frozen relevance windows with whole-value GLiNER choice diagnostics.

Retrieval is relevance only; exclusive class winners are not semantic proof.
This post hoc development replay does not select an operating threshold or
measure whole-SKU accuracy. It preserves skipped windows and every condition.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOP_KS = (1, 3, 5)
THRESHOLDS = (0.5, 0.7, 0.9)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


GLINER = module("ranked_gliner_diagnostics", HERE / "score_generic_gliner_choice_v1.py")
RANKING = module("ranked_gliner_relevance", HERE / "score_ranked_relation_probe_v1.py")
sha, read = GLINER.sha, GLINER.read


def bind_rows(records, rankings, selections, sources, text_source):
    """Join exact original identities across pre-quote and quote-only inputs."""
    if text_source not in {"premise", "original_relation_premise"}:
        raise ValueError("unexpected source premise policy")
    source_by_id = {}
    for source in sources:
        if source["id"] in source_by_id:
            raise ValueError("duplicate ranked original relation request ID")
        source_by_id[source["id"]] = source
    rank_by_key, task_bindings = {}, {}
    for ranking in rankings:
        key = (ranking["task_id"], ranking["source_request_id"])
        if any(type(value) is not str or not value for value in key) or key in rank_by_key:
            raise ValueError("invalid or duplicate original ranking window ID")
        p = ranking["provenance"]
        if (p["task_id"], p["source_request_id"]) != key:
            raise ValueError("ranking provenance window identity differs")
        binding = GLINER.raw_binding(p)
        if key[0] in task_bindings and task_bindings[key[0]] != binding:
            raise ValueError("ranking task changes a whole condition or fixed AU row")
        task_bindings[key[0]] = binding
        rank_by_key[key] = ranking
    records_by_key = {}
    for record in records:
        p = record["provenance"]
        key = (p["task_id"], p["source_request_id"])
        if (any(type(value) is not str or not value for value in key)
                or key in records_by_key or key not in rank_by_key):
            raise ValueError("invalid, duplicate or unranked GLiNER source window ID")
        ranking = rank_by_key[key]
        rp = ranking["provenance"]
        if (record["id"] != key[1] or record.get("task_id", key[0]) != key[0]
                or GLINER.raw_binding(p) != GLINER.raw_binding(rp)
                or p["window"] != rp["window"]):
            raise ValueError("GLiNER original whole condition, row or literal window binding differs")
        relation_ids = ranking["source_relation_request_ids"]
        original_sources = [source_by_id[source_id] for source_id in relation_ids]
        if "original_relation_premise" in rp:
            original_premise = rp["original_relation_premise"]
        elif ranking["status"] == "empty_literal_quote" and not ranking["passage"].strip():
            # The renderer retains blank windows before quote conversion, so
            # their source IDs/premises are the original frozen ones already.
            original_premise = original_sources[0]["premise"]
            if any(source["premise"] != original_premise for source in original_sources):
                raise ValueError("blank window alternatives change original premise")
        else:
            raise ValueError("ranked nonempty window lacks original full premise")
        if record["source_premise"] != original_premise or record["text"] != record["source_premise"]:
            raise ValueError("GLiNER full title, row and context differ from ranked original premise")
        original_ids = []
        for source in original_sources:
            if "original_relation_request_id" in source["provenance"]:
                original_ids.append(source["provenance"]["original_relation_request_id"])
            elif ranking["status"] == "empty_literal_quote":
                original_ids.append(source["id"])
            else:
                raise ValueError("ranked converted window lacks original relation request ID")
        wanted = relation_ids if text_source == "original_relation_premise" else original_ids
        if record["source_request_ids"] != wanted:
            raise ValueError("GLiNER source IDs do not preserve every exact original alternative")
        if text_source == "original_relation_premise":
            if (record["source_original_relation_request_ids"] != original_ids
                    or record["source_input_premise"] != ranking["passage"]):
                raise ValueError("GLiNER original relation ID or quote-only source binding differs")
        records_by_key[key] = record
    for task, selection in selections.items():
        if any((task, source_id) not in records_by_key for source_id in selection["top_k_source_request_ids"]["5"]):
            raise ValueError("GLiNER run is missing a required frozen top5 window")
    return records_by_key, rank_by_key


def join_verified(run_dir, ranking_dir):
    summary, records, receipt = GLINER.load_verified(run_dir)
    if receipt["input_scope"] != "all_windows" or not receipt["strict_512_run_eligible"]:
        raise ValueError("replay requires a strict complete untruncated whole-window run")
    ranking_summary, rankings, selections, sources = RANKING.load_rankings_verified(ranking_dir)
    by_key, rank_by_key = bind_rows(records, rankings, selections, sources, receipt["text_source"])
    receipt["topk_selected_before_gliner_inference_verified"] = False
    sidecars = summary.get("upstream_sidecar_bindings")
    if sidecars is not None:
        for binding in sidecars.values():
            path = Path(binding["path"])
            if not path.is_absolute():
                path = HERE.parents[1] / path
            if sha(path) != binding["sha256"]:
                raise ValueError("frozen upstream retrieval sidecar SHA mismatch")
        if (sidecars["source-requests.jsonl"]["sha256"] != ranking_summary["input_sha256"]["requests.jsonl"]
                or sidecars["source-empty-quote-requests.jsonl"]["sha256"] != ranking_summary["input_sha256"]["empty-quote-requests.jsonl"]):
            raise ValueError("frozen upstream original source differs from ranking source")
        path = Path(sidecars["selections.jsonl"]["path"])
        if not path.is_absolute():
            path = HERE.parents[1] / path
        upstream = read(path)
        if len(upstream) != len(selections) or len({r["task_id"] for r in upstream}) != len(upstream):
            raise ValueError("frozen upstream selection task set differs")
        for selected in upstream:
            expected = selections.get(selected["task_id"])
            if (expected is None or selected["top_k_source_request_ids"] != expected["top_k_source_request_ids"]
                    or selected["selected_source_request_ids"] != expected["top_k_source_request_ids"]["5"]):
                raise ValueError("frozen pre-inference retrieval selection differs from relevance order")
        expected_keys = {(task, source_id) for task, selection in selections.items()
                         for source_id in selection["top_k_source_request_ids"]["5"]}
        if set(by_key) != expected_keys:
            raise ValueError("pre-inference GLiNER subset differs from complete frozen top5 windows")
        receipt["topk_selected_before_gliner_inference_verified"] = True
    return summary, receipt, ranking_summary, rankings, selections, by_key, rank_by_key


def replay(rankings, selections, records_by_key, top_k, threshold):
    task_windows = defaultdict(list)
    for ranking in rankings:
        task_windows[ranking["task_id"]].append(ranking)
    result = []
    for task in sorted(task_windows):
        selected_ids = selections[task]["top_k_source_request_ids"][str(top_k)]
        rows = [records_by_key[(task, source_id)] for source_id in selected_ids]
        if not rows:
            # This is a non-inferred diagnostic placeholder made from a real
            # frozen source binding, never model input or synthetic SKU data.
            first = task_windows[task][0]
            rows = [{"id": first["source_request_id"], "provenance": deepcopy(first["provenance"]),
                     "diagnostic_inference_eligible": False, "chosen_value": None, "confidence": None,
                     "status": "no_retrieval_eligible_window"}]
        aggregated = GLINER.aggregate(rows, threshold)
        if len(aggregated) != 1 or aggregated[0]["task_id"] != task:
            raise ValueError("one retrieval task covers multiple whole residual conditions")
        condition = aggregated[0]
        condition.update(top_k=top_k, selected_source_request_ids=selected_ids,
                         not_inferred_window_ids=[r["id"] for r in rows if not r["diagnostic_inference_eligible"]],
                         excluded_retrieval_windows=[{"source_request_id": r["source_request_id"], "status": r["status"]}
                                                     for r in task_windows[task] if r["status"] != "ok"],
                         available_ranked_window_count=len(selected_ids), whole_sku_accuracy=False)
        result.append(condition)
    return result


def score(run_dir, ranking_dir, annotation_paths, output):
    if output.exists():
        raise FileExistsError(output)
    summary, receipt, ranking_summary, rankings, selections, by_key, rank_by_key = join_verified(run_dir, ranking_dir)
    # Labels are separate and opened only after output hashes, length limits,
    # exact whole options, fixed AU row and all required top5 windows bind.
    annotations = {}
    for path in annotation_paths:
        for annotation in read(path):
            key = tuple(annotation[name] for name in ("case_id", "au_row_key", "axis_name", "selected_value"))
            if key in annotations or annotation["relation"] not in {"support", "conflict", "unknown"}:
                raise ValueError("duplicate or invalid machine annotation")
            annotations[key] = annotation
    condition_keys = {tuple(r["provenance"][name] for name in ("case_id", "au_row_key", "axis_name", "selected_value"))
                      for r in rankings}
    if condition_keys != set(annotations):
        raise ValueError("annotation condition set differs from complete ranked task universe")
    result = {"schema_version": "ranked-gliner-whole-choice-diagnostics-v1",
              "run_summary_sha256": sha(run_dir / "summary.json"),
              "prediction_sha256": sha(run_dir / "predictions.jsonl"),
              "ranking_summary_sha256": sha(ranking_dir / "summary.json"),
              "rankings_sha256": sha(ranking_dir / "rankings.jsonl"),
              "code_sha256": {p.name: sha(p) for p in (Path(__file__), HERE / "score_generic_gliner_choice_v1.py",
                                                      HERE / "score_ranked_relation_probe_v1.py")},
              "annotation_sha256": {str(path): sha(path) for path in annotation_paths},
              "verification": {**receipt, "exact_ranking_full_title_row_context_binding_verified": True,
                               "all_required_top5_windows_present": True,
                               "verification_completed_before_labels_read": True},
              "top_ks_for_diagnostics": list(TOP_KS), "thresholds_for_diagnostics": list(THRESHOLDS),
              "primary_threshold": 0.9, "thresholds_fitted_to_labels": False,
              "retrieval_query_style": ranking_summary.get("query_style", "axis_options"),
              "retrieval_scores_are_condition_confidence": False,
              "retrospective_diagnostic_grid": True, "retrieval_replay_posthoc_development_diagnostic": True,
              "topk_selected_before_gliner_inference": receipt["topk_selected_before_gliner_inference_verified"],
              "gliner_operating_threshold_preregistered": False, "confidence_calibrated": False,
              "independent_final_test": False, "machine_labels_not_human_gold": True,
              "full_page_annotations_vs_available_evidence_only": True, "scope_proven": False,
              "production_eligible": False, "whole_sku_accuracy": False,
              "class_match_is_semantic_proof": False, "different_raw_winner_proves_selected_conflict": False,
              "inference_seconds": summary["inference_seconds"], "diagnostics": {}}
    for top_k in TOP_KS:
        result["diagnostics"][str(top_k)] = {}
        for threshold in THRESHOLDS:
            rows = replay(rankings, selections, by_key, top_k, threshold)
            result["diagnostics"][str(top_k)][str(threshold)] = {"metrics": GLINER.metrics(rows, annotations),
                                                               "conditions": rows}
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
    print(json.dumps({k: {t: d["metrics"] for t, d in ds.items()} for k, ds in result["diagnostics"].items()}, ensure_ascii=False))
