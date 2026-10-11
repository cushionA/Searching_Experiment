#!/usr/bin/env python3
"""Pinned CPU NLI on previously incorrect complete-condition requests only.

This diagnostic reuses frozen current-product context and received passages.
It performs no alignment, retrieval, category parsing, or label evaluation.
An optional ensemble preserves GLiNER decisions except for a confident NLI
contradiction veto. Neither model judgments nor literal quotes prove scope.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil

import trial_generic_model_nli_v1 as NLI
from trial_scoped_condition_gliner_v1 import (
    HERE, ROOT, RANKING_FIELDS, aggregate, canonical, digest, load_sources,
    quote_ref, read, sha, write_json, write_rows,
)

HYPOTHESIS_TEMPLATE = "この商品の〈{axis_name}〉は〈{selected_value}〉である。"


def pairs_for(requests, documents, bindings, rankings):
    """Build exact pairs; a framed premise is not a single raw-source quote."""
    pairs = []
    for request in requests:
        doc = documents[request["target_document_id"]]
        group_id = digest({k: request[k] for k in RANKING_FIELDS})
        hypothesis = HYPOTHESIS_TEMPLATE.format(axis_name=request["axis_name"], selected_value=request["selected_value"])
        context = "現在の商品名：" + doc["title"]["text"]
        context += "\n現在の選択行：\n" + request["target_selected_axes_text"]
        refs = []
        for field, text in (("title", doc["title"]["text"]), ("selected_axes", request["target_selected_axes_text"])):
            if text:
                refs.append(quote_ref(doc, field, text))
        windows = [("title", None)] + [(w["window_id"], w) for w in rankings.get(group_id, [])]
        for index, (window_id, window) in enumerate(windows):
            premise = context if window is None else context + "\n引用本文：\n" + window["text"]
            pairs.append({"id": request["request_id"] + ":nli-window:" + str(index),
                          "premise": premise, "hypothesis": hypothesis,
                          "request_id": request["request_id"], "request_sha256": request["request_sha256"],
                          "window_id": window_id, "direction": request["direction"],
                          "quote_refs": list(refs) if window is None else [*refs, window["quote_ref"]],
                          "received_ranking_window": window, "received_ranking_group_id": group_id,
                          "source_request": request, "source_task_bindings": bindings[request["request_id"]],
                          "premise_kind": "framed_current_product_and_literal_source_parts",
                          "scope_status": "unproved", "production_adoption_allowed": False})
    return pairs


def window_record(record):
    count = record.get("token_count_untruncated")
    if record.get("error_type"):
        status = "inference_error"
    elif count is not None and NLI.exceeds_token_limit(count):
        status = "input_too_long"
    elif record.get("probabilities") is None:
        status = "no_probabilities"
    else:
        status = "ok"
    state = record["relation"]
    if state not in {"support", "conflict", "unknown"} or record["confidence_threshold"] != NLI.THRESHOLD:
        raise ValueError("NLI relation contract differs from frozen .90 three-state mapping")
    return {**record, "status": status, "chosen_state": state,
            "confidence": record["predicted_confidence"]}


def predictions_for(requests, records, bindings):
    by_request = defaultdict(list)
    for record in records:
        by_request[record["request_id"]].append(window_record(record))
    predictions = []
    for request in requests:
        state, refs = aggregate(by_request[request["request_id"]], NLI.THRESHOLD)
        predictions.append({"request_id": request["request_id"], "request_sha256": request["request_sha256"],
                            "selected_value": request["selected_value"], "direction": request["direction"],
                            "state": state, "winning_value": request["selected_value"] if state == "support" else None,
                            "quote_refs": refs, "scope_status": "unproved", "production_adoption_allowed": False,
                            "source_request": request, "source_task_bindings": bindings[request["request_id"]],
                            "variant": "pinned-nli-complete-condition", "confidence_threshold": NLI.THRESHOLD})
    return predictions


def verified_ref(ref, request, documents):
    if ref.get("document_id") != request["target_document_id"]:
        return False
    field = ref.get("field")
    if field == "selected_axes":
        text = request["target_selected_axes_text"]
    elif field in {"title", "description"}:
        text = documents[request["target_document_id"]][field]["text"]
    else:
        return False
    start, end = ref.get("start"), ref.get("end")
    return (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
            and ref.get("quote") == text[start:end])


def load_gliner(directory, requests, documents):
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    if (summary.get("schema_version") != "scoped-condition-gliner-v1" or summary.get("confidence") != .5 or
            summary.get("production_adoption_allowed") is not False):
        raise ValueError("GLiNER input is not the completed frozen scoped-condition diagnostic")
    expected = summary["output_sha256"].get("predictions.jsonl")
    if expected != sha(directory / "predictions.jsonl"):
        raise ValueError("completed GLiNER prediction SHA differs")
    predictions = read(directory / "predictions.jsonl")
    by_id = {p["request_id"]: p for p in predictions}
    if len(by_id) != len(predictions) or set(by_id) != {r["request_id"] for r in requests}:
        raise ValueError("GLiNER predictions must bind exactly the selected error subset")
    for request in requests:
        prediction = by_id[request["request_id"]]
        if (prediction["request_sha256"] != request["request_sha256"] or
                prediction["selected_value"] != request["selected_value"] or
                prediction.get("scope_status") != "unproved" or
                prediction.get("production_adoption_allowed") is not False):
            raise ValueError("GLiNER complete condition or scope binding differs")
        state = prediction["state"]
        if state not in {"support", "conflict", "unknown"}:
            raise ValueError("unsupported GLiNER state")
        refs = prediction.get("quote_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, dict) or not verified_ref(ref, request, documents) for ref in refs):
            raise ValueError("GLiNER quoted source does not bind the fixed target")
        if state != "unknown" and not refs:
            raise ValueError("GLiNER decisive state has no literal target citations")
        if state == "support" and prediction.get("winning_value") != request["selected_value"]:
            raise ValueError("GLiNER support does not bind complete selected value")
    return by_id, summary


def ensemble_prediction(gliner, nli):
    if (gliner["request_id"] != nli["request_id"] or gliner["request_sha256"] != nli["request_sha256"] or
            gliner["selected_value"] != nli["selected_value"]):
        raise ValueError("ensemble full condition identity differs")
    veto = gliner["state"] == "support" and nli["state"] == "conflict"
    state = "conflict" if veto else gliner["state"]
    refs = nli["quote_refs"] if veto else gliner["quote_refs"]
    return {**nli, "state": state, "winning_value": nli["selected_value"] if state == "support" else None,
            "quote_refs": refs, "variant": "gliner-with-nli-conflict-veto",
            "nli_state": nli["state"], "gliner_state": gliner["state"], "nli_conflict_veto": veto,
            "ensemble_policy": "only confident NLI conflict vetoes GLiNER support; NLI never upgrades a dropped condition",
            "scope_status": "unproved", "production_adoption_allowed": False}


def run(input_dir, ranking_dir, output_dir, model_dir, threads=4, batch_size=16,
        gliner_dir=None, ensemble_output_dir=None):
    if output_dir.exists() or (ensemble_output_dir is not None and ensemble_output_dir.exists()):
        raise FileExistsError("refusing to overwrite diagnostic output")
    if threads < 1 or batch_size < 1 or (gliner_dir is None) != (ensemble_output_dir is None):
        raise ValueError("positive CPU settings and both optional ensemble paths are required")
    if ensemble_output_dir == output_dir:
        raise ValueError("NLI and ensemble must have different output directories")
    subset_manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    if (subset_manifest.get("errors_only") is not True or subset_manifest.get("development_only") is not True or
            subset_manifest.get("independent_test") is not False or subset_manifest.get("labels_in_model_inputs") is not False):
        raise ValueError("this driver accepts only the frozen development error subset without model-input labels")
    requests, documents, bindings, rankings, source_names, rank_names, rank_summary = load_sources(input_dir, ranking_dir)
    gliner, gliner_summary = load_gliner(gliner_dir, requests, documents) if gliner_dir is not None else (None, None)
    pairs = pairs_for(requests, documents, bindings, rankings)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ["manifest.json", *source_names]:
        shutil.copyfile(input_dir / name, output_dir / ("source-" + name))
    for name in ("freeze.json", "summary.json"):
        shutil.copyfile(ranking_dir / name, output_dir / ("received-ranking-" + name))
    write_rows(output_dir / "received-selected-ranking-windows.jsonl", [w for rows in rankings.values() for w in rows])
    write_rows(output_dir / "model-pairs.jsonl", pairs)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in (Path(__file__).name, "trial_scoped_condition_gliner_v1.py", "trial_generic_gliner_choice_v1.py",
                 "try_gliner.py", "trial_generic_model_nli_v1.py", "trial_cpu_requirement_relations_v1.py"):
        shutil.copyfile(HERE / name, code_dir / name)
    freeze = {"schema_version": "scoped-condition-nli-v1", "labels_read": False, "errors_only": True,
              "development_diagnostic": True, "independent_evaluation": False, "synthetic_data": False,
              "request_count": len(requests), "pair_count": len(pairs), "scope_status": "unproved",
              "production_adoption_allowed": False, "citations_are_semantic_proof": False,
              "hypothesis_template": HYPOTHESIS_TEMPLATE, "confidence": NLI.THRESHOLD,
              "threshold_policy": "unchanged pinned .90 development diagnostic; no threshold search",
              "new_retrieval_run": False, "received_top_k": 5, "threads": threads, "batch_size": batch_size,
              "input_length_policy": "exact premise+hypothesis pair, truncation=False, >512 remains unknown",
              "aggregation_policy": "high-confidence direct conflict vetoes support; otherwise direct support or unknown",
              "input_sha256": {n: sha(input_dir / n) for n in ["manifest.json", *source_names]},
              "received_ranking_sha256": {n: sha(ranking_dir / n) for n in ["summary.json", *rank_names]},
              "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()},
              "model_pin": {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
                            "parameter_count": NLI.FP32_PARAMETER_COUNT, "precision": "fp32", "device": "cpu",
                            "files_sha256": NLI.NLI_FILE_SHA256},
              "received_reranker_pin": rank_summary["reranker_pin"],
              "gliner_dependency_sha256": {n: sha(gliner_dir / n) for n in ("summary.json", "predictions.jsonl")}
                                          if gliner_dir is not None else None}
    write_json(output_dir / "freeze.json", freeze)
    print(canonical({"event": "frozen", "requests": len(requests), "pairs": len(pairs), "threshold": NLI.THRESHOLD}), flush=True)
    result = NLI.run_pairs(pairs, model_dir=model_dir, threads=threads, batch_size=batch_size,
                           on_ready=lambda pins, runtime: write_json(output_dir / "model-ready.json", {"pins": pins, "runtime": runtime}))
    records = result["records"]
    if len(records) != len(pairs) or [r["id"] for r in records] != [p["id"] for p in pairs]:
        raise ValueError("NLI records do not preserve exact request-window bindings")
    write_rows(output_dir / "classification-windows.jsonl", [window_record(r) for r in records])
    predictions = predictions_for(requests, records, bindings)
    write_rows(output_dir / "predictions.jsonl", predictions)
    summary = {**freeze, "model_pin": result["pins"], "runtime": result["runtime"],
               "inference_seconds": result["runtime"]["inference_seconds"],
               "prediction_states": dict(Counter(p["state"] for p in predictions)),
               "window_status_counts": dict(Counter(window_record(r)["status"] for r in records)),
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    if gliner is not None:
        ensemble_output_dir.mkdir(parents=True, exist_ok=False)
        ensemble = [ensemble_prediction(gliner[p["request_id"]], p) for p in predictions]
        for source, name in ((output_dir / "predictions.jsonl", "source-nli-predictions.jsonl"),
                             (output_dir / "summary.json", "source-nli-summary.json"),
                             (gliner_dir / "predictions.jsonl", "source-gliner-predictions.jsonl"),
                             (gliner_dir / "summary.json", "source-gliner-summary.json")):
            shutil.copyfile(source, ensemble_output_dir / name)
        write_rows(ensemble_output_dir / "predictions.jsonl", ensemble)
        ensemble_summary = {"schema_version": "scoped-condition-nli-veto-v1", "request_count": len(ensemble),
                            "labels_read": False, "errors_only": True, "development_diagnostic": True,
                            "independent_evaluation": False, "production_adoption_allowed": False, "scope_status": "unproved",
                            "nli_inference_reused": True, "nli_confidence": NLI.THRESHOLD, "gliner_confidence": .5,
                            "nli_conflict_veto_count": sum(p["nli_conflict_veto"] for p in ensemble),
                            "prediction_states": dict(Counter(p["state"] for p in ensemble)),
                            "source_sha256": {"nli_summary": sha(output_dir / "summary.json"),
                                              "gliner_summary": sha(gliner_dir / "summary.json")},
                            "output_sha256": {p.name: sha(p) for p in ensemble_output_dir.iterdir() if p.is_file()}}
        write_json(ensemble_output_dir / "summary.json", ensemble_summary)
    print(canonical({"event": "complete", "requests": len(requests), "prediction_states": summary["prediction_states"],
                     "inference_seconds": summary["inference_seconds"]}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--ranking-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=ROOT / ".deps/sku-nli-model")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--gliner-dir", type=Path)
    parser.add_argument("--ensemble-output-dir", type=Path)
    run(**vars(parser.parse_args()))
