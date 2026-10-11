#!/usr/bin/env python3
"""Development-only NLI ranking of complete raw alternatives against title/row.

Only the frozen previous-error subset is permitted. Relative entailment scores
are an uncalibrated choice diagnostic, not semantic proof or contradiction.
No annotations, body passages, retrieval, or category dictionaries are used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil

from trial_scoped_condition_nli_v1 import HYPOTHESIS_TEMPLATE, NLI
from trial_scoped_condition_gliner_v1 import (
    HERE, ROOT, canonical, load_sources, quote_ref, sha, write_json, write_rows,
)

RELATIVE_THRESHOLD = .5


def pairs_for(requests, documents, bindings):
    pairs = []
    for request in requests:
        doc = documents[request["target_document_id"]]
        premise = "現在の商品名：" + doc["title"]["text"]
        premise += "\n現在の選択行：\n" + request["target_selected_axes_text"]
        refs = [quote_ref(doc, field, text) for field, text in (
            ("title", doc["title"]["text"]), ("selected_axes", request["target_selected_axes_text"])) if text]
        for index, value in enumerate(request["option_values"]):
            pairs.append({"id": request["request_id"] + ":nli-option:" + str(index),
                          "request_id": request["request_id"], "request_sha256": request["request_sha256"],
                          "premise": premise, "hypothesis": HYPOTHESIS_TEMPLATE.format(
                              axis_name=request["axis_name"], selected_value=value),
                          "option_index": index, "option_value": value, "quote_refs": refs,
                          "direction": request["direction"], "source_request": request,
                          "source_task_bindings": bindings[request["request_id"]],
                          "scope_status": "unproved", "production_adoption_allowed": False})
    return pairs


def choose(options, selected_value, records):
    """Keep structural uncertainty; never call a relative choice contradiction."""
    result = {"state": "unknown", "reason": None, "winning_value": None,
              "ranking_winning_value": None, "relative_entailment_scores": None,
              "selected_option_entailment_probability": None}
    if len(records) != len(options) or [r["option_value"] for r in records] != options:
        return {**result, "reason": "incomplete_candidate_scores"}
    for record in records:
        if record.get("error_type"):
            return {**result, "reason": "candidate_inference_error"}
        count = record.get("token_count_untruncated")
        if count is None or type(count) is not int or count < 1:
            return {**result, "reason": "candidate_token_count_missing"}
        if NLI.exceeds_token_limit(count):
            return {**result, "reason": "candidate_input_too_long"}
    try:
        probabilities = [NLI._probabilities(record) for record in records]
    except (ValueError, TypeError):
        return {**result, "reason": "invalid_candidate_probabilities"}
    if any(p is None for p in probabilities):
        return {**result, "reason": "incomplete_candidate_scores"}
    entailment = [p["support"] for p in probabilities]
    total = sum(entailment)
    result["selected_option_entailment_probability"] = entailment[options.index(selected_value)]
    if total <= 0:
        return {**result, "reason": "no_positive_entailment_mass"}
    relative = [value / total for value in entailment]
    result["relative_entailment_scores"] = dict(zip(options, relative, strict=True))
    maximum = max(entailment)
    winners = [index for index, value in enumerate(entailment) if value == maximum]
    if len(winners) != 1:
        return {**result, "reason": "top_support_probability_tie"}
    winner = winners[0]
    result["ranking_winning_value"] = options[winner]
    if options[winner] == selected_value and result["selected_option_entailment_probability"] >= NLI.THRESHOLD:
        return {**result, "state": "support", "winning_value": selected_value, "reason": "absolute_entailment"}
    if len(options) < 2:
        return {**result, "reason": "single_option_without_absolute_entailment"}
    if relative[winner] < RELATIVE_THRESHOLD:
        return {**result, "reason": "relative_mass_below_threshold"}
    if options[winner] != selected_value:
        return {**result, "winning_value": options[winner], "reason": "alternative_value_selected"}
    return {**result, "state": "support", "winning_value": selected_value, "reason": "relative_choice_diagnostic"}


def run(input_dir, ranking_dir, output_dir, model_dir, threads=4, batch_size=16):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if threads < 1 or batch_size < 1:
        raise ValueError("positive CPU settings required")
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("errors_only") is not True or manifest.get("development_only") is not True or
            manifest.get("independent_test") is not False or manifest.get("labels_in_model_inputs") is not False):
        raise ValueError("only the frozen development error subset without model-input labels is allowed")
    requests, documents, bindings, _, source_names, rank_names, _ = load_sources(input_dir, ranking_dir)
    pairs = pairs_for(requests, documents, bindings)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ["manifest.json", *source_names]:
        shutil.copyfile(input_dir / name, output_dir / ("source-" + name))
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in (Path(__file__).name, "trial_scoped_condition_nli_v1.py", "trial_scoped_condition_gliner_v1.py",
                 "trial_generic_gliner_choice_v1.py", "try_gliner.py", "trial_generic_model_nli_v1.py",
                 "trial_cpu_requirement_relations_v1.py"):
        shutil.copyfile(HERE / name, code_dir / name)
    write_rows(output_dir / "model-pairs.jsonl", pairs)
    freeze = {"schema_version": "scoped-condition-nli-choice-v1", "variant": "nli-title-choice",
              "labels_read": False, "errors_only": True, "development_diagnostic": True, "independent_evaluation": False,
              "synthetic_data": False, "production_adoption_allowed": False, "scope_status": "unproved",
              "model_role": "NLI probability ranking, not entailment proof or contradiction proof",
              "request_count": len(requests), "pair_count": len(pairs), "threads": threads, "batch_size": batch_size,
              "body_model_input": False, "retrieval_run": False, "source_contract_verified_using_received_ranking": True,
              "hypothesis_template": HYPOTHESIS_TEMPLATE, "desired_source_value_injected_into_premise": False,
              "all_original_options_are_hypotheses": True,
              "absolute_support_threshold": NLI.THRESHOLD, "relative_choice_threshold": RELATIVE_THRESHOLD,
              "relative_choice_policy": "uncalibrated forced choice diagnostic, two or more candidates only; no threshold search",
              "unknown_policy": "any missing/invalid/overlong/error candidate or tied leader; alternate choice is unknown, never conflict",
              "input_length_policy": "exact title/full selected-row premise+hypothesis, truncation=False, >512 unknown",
              "input_sha256": {n: sha(input_dir / n) for n in ["manifest.json", *source_names]},
              "received_source_contract_sha256": {n: sha(ranking_dir / n) for n in ["summary.json", *rank_names]},
              "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()},
              "model_pin": {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
                            "parameter_count": NLI.FP32_PARAMETER_COUNT, "precision": "fp32", "device": "cpu",
                            "files_sha256": NLI.NLI_FILE_SHA256}}
    write_json(output_dir / "freeze.json", freeze)
    print(canonical({"event": "frozen", "requests": len(requests), "pairs": len(pairs)}), flush=True)
    result = NLI.run_pairs(pairs, model_dir=model_dir, threads=threads, batch_size=batch_size,
                          on_ready=lambda pins, runtime: write_json(output_dir / "model-ready.json", {"pins": pins, "runtime": runtime}))
    records = result["records"]
    if [r["id"] for r in records] != [p["id"] for p in pairs]:
        raise ValueError("NLI candidate records do not preserve original input order and identity")
    write_rows(output_dir / "classification-options.jsonl", records)
    grouped = defaultdict(list)
    for record in records:
        grouped[record["request_id"]].append(record)
    predictions = []
    for request in requests:
        candidates = grouped[request["request_id"]]
        decision = choose(request["option_values"], request["selected_value"], candidates)
        predictions.append({**decision, "request_id": request["request_id"], "request_sha256": request["request_sha256"],
                            "selected_value": request["selected_value"], "direction": request["direction"],
                            "quote_refs": candidates[0]["quote_refs"] if decision["state"] == "support" else [],
                            "source_request": request, "source_task_bindings": bindings[request["request_id"]],
                            "candidate_records": candidates, "scope_status": "unproved", "production_adoption_allowed": False,
                            "variant": "nli-title-choice", "model_role": freeze["model_role"]})
    write_rows(output_dir / "predictions.jsonl", predictions)
    summary = {**freeze, "model_pin": result["pins"], "runtime": result["runtime"],
               "inference_seconds": result["runtime"]["inference_seconds"],
               "prediction_states": dict(Counter(p["state"] for p in predictions)),
               "prediction_reasons": dict(Counter(p["reason"] for p in predictions)),
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    print(canonical({"event": "complete", "prediction_states": summary["prediction_states"],
                     "prediction_reasons": summary["prediction_reasons"], "inference_seconds": summary["inference_seconds"]}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--ranking-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=ROOT / ".deps/sku-nli-model")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    run(**vars(parser.parse_args()))
