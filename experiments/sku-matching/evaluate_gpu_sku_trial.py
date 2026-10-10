#!/usr/bin/env python3
"""Locally score frozen, label-blind Kaggle SKU GPU outputs after artifact checks."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DECISIONS = ("matched", "unmatched", "review")
STATUSES = {"ok", "invalid_output", "invalid_input_over_limit", "error"}
MODEL_ALLOWLIST = {
    "Qwen/Qwen3.5-9B": {"revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a", "load": "nf4",
                        "model_type": "qwen3_5", "parameter_class": "9B"},
    "Qwen/Qwen3.5-4B": {"revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a", "load": "nf4",
                        "model_type": "qwen3_5", "parameter_class": "4B"},
}


def model_slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()


def configured_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    models = config.get("models")
    if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict):
        raise ValueError("GPU config must request exactly one allowlisted model")
    model = models[0]
    name = model.get("name")
    allowed = MODEL_ALLOWLIST.get(name)
    if not allowed or any(model.get(k) != v for k, v in allowed.items()):
        raise ValueError(f"GPU config contains an unapproved model revision/load: {name}")
    if model.get("quantization_origin") != "official base checkpoint quantized at load time with bitsandbytes NF4":
        raise ValueError("GPU config must declare official-base load-time NF4 quantization")
    return models


def validate_actual_nf4(result: dict[str, Any], name: str, revision: str) -> None:
    quant_fields = {
        "runtime_quantization": "nf4", "bnb_4bit_quant_type": "nf4",
        "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True,
        "is_loaded_in_4bit": True,
    }
    if any(result.get(k) != v for k, v in quant_fields.items()):
        raise ValueError(f"GPU model actual quantization configuration is not requested NF4: {name}")
    hf_quant = result.get("hf_quantization_config")
    expected_hf = {"load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
                   "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True}
    if not isinstance(hf_quant, dict) or any(hf_quant.get(k) != v for k, v in expected_hf.items()):
        raise ValueError(f"Transformers quantization config does not confirm NF4: {name}")
    if (result.get("config_name") != name or result.get("config_revision") != revision
            or not isinstance(result.get("model_config_sha256"), str)
            or len(result["model_config_sha256"]) != 64
            or result.get("nf4_linear4bit_module_count", 0) <= 0
            or result.get("nf4_logical_parameter_count", 0) <= 0
            or result.get("base_logical_parameter_count", 0) <= 0
            or not 0 < result.get("nf4_coverage_ratio", 0) <= 1):
        raise ValueError(f"GPU model lacks evidence of actual NF4 module/parameter quantization: {name}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return obj


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{n}: expected object")
            rows.append(row)
    return rows


def ids(rows: list[dict[str, Any]], label: str) -> set[str]:
    values = [r.get("case_id") for r in rows]
    if any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
        raise ValueError(f"{label} has missing or duplicate case_id")
    return set(values)


def validate_artifacts(inputs: Path, artifacts: Path) -> tuple[list[dict], dict, dict]:
    """Complete all label-free integrity and run-validity checks first."""
    source_manifest_path = inputs.parent / "manifest.json"
    source_manifest = read_json(source_manifest_path)
    config_path = inputs.parent / "config.json"
    if not config_path.is_file():
        raise ValueError("Missing GPU inference config.json")
    config_sha = sha256(config_path)
    if source_manifest.get("config_sha256") != config_sha:
        raise ValueError("GPU config.json SHA does not match input manifest")
    config = read_json(config_path)
    requested_models = configured_models(config)
    input_sha = sha256(inputs)
    declared_input_sha = source_manifest.get("inputs_sha256", source_manifest.get("input_sha256"))
    if declared_input_sha != input_sha:
        raise ValueError("inputs.jsonl SHA does not match source manifest")
    cases = read_jsonl(inputs)
    case_ids = ids(cases, "inputs")
    declared_count = source_manifest.get("input_count", source_manifest.get("case_count"))
    if declared_count is not None and declared_count != len(cases):
        raise ValueError("inputs.jsonl count does not match source manifest")

    manifest = read_json(artifacts / "run-manifest.json")
    if manifest.get("labels_used") is not False:
        raise ValueError("GPU run manifest does not certify label-blind inference")
    if manifest.get("input_sha256") != input_sha or manifest.get("case_count") != len(cases):
        raise ValueError("GPU run manifest input SHA or case count mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("GPU run manifest has no file inventory")
    run_models = manifest.get("models")
    model_projection = [{k: m.get(k) for k in ("name", "revision", "load")} for m in requested_models]
    if run_models != model_projection:
        raise ValueError("GPU run manifest model plan differs from verified config.json")
    prediction_files = {f"predictions-{model_slug(m['name'])}.jsonl" for m in requested_models}
    required = {"runtime.json", "summary.json"} | prediction_files
    if not required.issubset(files):
        raise ValueError("GPU run artifact inventory is incomplete")
    actual_files = {p.name for p in artifacts.iterdir() if p.is_file()}
    if actual_files != set(files) | {"run-manifest.json"}:
        raise ValueError("GPU artifact directory contains missing or unmanifested files")
    for name, meta in files.items():
        path = artifacts / name
        if not path.is_file() or not isinstance(meta, dict):
            raise ValueError(f"Missing or invalid manifested GPU artifact: {name}")
        if path.stat().st_size != meta.get("bytes") or sha256(path) != meta.get("sha256"):
            raise ValueError(f"GPU artifact digest/byte mismatch: {name}")
    runtime, summary = read_json(artifacts / "runtime.json"), read_json(artifacts / "summary.json")
    if runtime.get("input_sha256_before") != input_sha or runtime.get("input_sha256_after") != input_sha or runtime.get("input_unchanged") is not True:
        raise ValueError("GPU runtime input integrity check failed")
    if summary.get("labels_used") is not False or summary.get("input_manifest_sha256") != sha256(source_manifest_path):
        raise ValueError("GPU summary source manifest or label-blind check failed")
    if any(x.get("config_sha256") != config_sha for x in (runtime, summary, manifest)):
        raise ValueError("GPU runtime/summary/run manifest config SHA does not match uploaded config")
    if runtime.get("gpu", {}).get("used") is not True:
        raise ValueError("GPU runtime does not confirm actual GPU inference")
    expected_models = {m["name"]: (m["revision"], m["load"]) for m in requested_models}
    model_results = {m.get("model"): m for m in summary.get("models", []) if isinstance(m, dict)}
    if set(model_results) != set(expected_models):
        raise ValueError("GPU run models differ from the single model in verified config.json")
    if manifest.get("model_results") != summary.get("models"):
        raise ValueError("Run manifest model results differ from summary.json")
    for name, (revision, load) in expected_models.items():
        result = model_results[name]
        if (result.get("status") != "complete" or result.get("revision") != revision
                or result.get("load") != load or result.get("completed") != len(cases)
                or result.get("inference_count", 0) <= 0
                or not result.get("parameter_devices")
                or any(not str(d).startswith("cuda:") for d in result["parameter_devices"])):
            raise ValueError(f"GPU model did not complete the full sample with expected pin/load: {name}")
        validate_actual_nf4(result, name, revision)
    if not runtime.get("gpu", {}).get("devices"):
        raise ValueError("GPU runtime has no CUDA device inventory")
    if summary.get("partial") is not False:
        raise ValueError("GPU run is partial, unavailable, or has a model load failure")
    for name in prediction_files:
        rows = read_jsonl(artifacts / name)
        if [r.get("case_id") for r in rows] != [r["case_id"] for r in cases]:
            raise ValueError(f"{name} case IDs do not exactly match frozen inputs")
        if any(r.get("status") not in STATUSES for r in rows):
            raise ValueError(f"{name} has an unknown row status")
    return cases, manifest, {"runtime": runtime, "summary": summary,
                             "source_manifest_sha256": sha256(source_manifest_path),
                             "config_sha256": config_sha, "configured_models": requested_models}


def prediction_for(row: dict) -> dict:
    parsed = row.get("parsed")
    if "status" not in row and row.get("decision") in DECISIONS:
        return {"decision": row["decision"], "au_row_key": row.get("top_row_key", row.get("au_row_key"))}
    if row.get("status") == "ok" and isinstance(parsed, dict) and parsed.get("decision") in DECISIONS:
        return {"decision": parsed["decision"], "au_row_key": parsed.get("au_row_key")}
    return {"decision": "review", "au_row_key": None}


def validate_gpu_predictions(cases: list[dict], records: list[dict], filename: str) -> None:
    """Recheck model JSON, exact literal quotes, row identity, and row metadata."""
    if [r.get("case_id") for r in records] != [c["case_id"] for c in cases]:
        raise ValueError(f"{filename} order differs from frozen input order")
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import kaggle_gpu_sku_runner as runner
    for case, record in zip(cases, records, strict=True):
        for key in ("dossier_id", "split"):
            if record.get(key) != case.get(key):
                raise ValueError(f"{filename} {key} mismatch for {case['case_id']}")
        if record.get("status") == "ok":
            if not isinstance(record.get("raw_output"), str):
                raise ValueError(f"{filename} successful output lacks raw text: {case['case_id']}")
            checked = runner.validate_prediction(record["raw_output"], case)
            if not checked.get("valid") or checked.get("prediction") != record.get("parsed"):
                raise ValueError(f"{filename} output validation mismatch for {case['case_id']}")


def gold_join(tasks: list[dict], labels_path: Path, selected_ids: set[str]) -> dict[str, dict]:
    labels = read_jsonl(labels_path)
    labels_by_id = {r.get("case_id"): r for r in labels}
    if len(labels_by_id) != len(labels) or set(labels_by_id) != ids(tasks, "tasks"):
        raise ValueError("Labels must have unique case IDs exactly matching full task set")
    task_by_id = {t["case_id"]: t for t in tasks}
    for cid, label in labels_by_id.items():
        if label.get("decision") not in DECISIONS:
            raise ValueError(f"Invalid label decision: {cid}")
        keys = label.get("matching_au_row_keys", [])
        if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
            raise ValueError(f"Invalid label row keys: {cid}")
        pool = {r["row_key"] for r in task_by_id[cid].get("au_candidates", [])}
        if label["decision"] == "matched" and not keys:
            raise ValueError(f"Matched label without positive row keys: {cid}")
        if label["decision"] != "matched" and keys:
            raise ValueError(f"Nonmatched label has row keys: {cid}")
        if set(keys) - pool:
            raise ValueError(f"Gold row key outside fixed pool: {cid}")
    if not selected_ids.issubset(labels_by_id):
        raise ValueError("Labels do not cover every selected GPU case")
    return {cid: labels_by_id[cid] for cid in selected_ids}


def constrained_prediction(task: dict, raw: dict) -> dict:
    """Apply the existing fixed-pool field gate to a GPU decision conservatively."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import evaluate_structured_skus as module
    gate = module.gate
    parsed = prediction_for(raw)
    if parsed["decision"] == "review":
        return parsed
    candidates = task.get("au_candidates", [])
    checked = [(c, gate(task, c)) for c in candidates]
    if parsed["decision"] == "matched":
        selected = next((c for c, _ in checked if c["row_key"] == parsed["au_row_key"]), None)
        if selected is None:
            return {"decision": "review", "au_row_key": None}
        return parsed if gate(task, selected)["decision"] == "matched" else {"decision": "review", "au_row_key": None}
    if candidates and all(g["decision"] == "unmatched" for _, g in checked):
        return parsed
    return {"decision": "review", "au_row_key": None}


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def select_by_ids(full_rows: list[dict], selected_rows: list[dict], label: str) -> list[dict]:
    full_ids, selected_ids = ids(full_rows, label), ids(selected_rows, "selected inputs")
    if not selected_ids.issubset(full_ids):
        raise ValueError(f"{label} does not cover every selected case")
    by_id = {r["case_id"]: r for r in full_rows}
    return [by_id[r["case_id"]] for r in selected_rows]


def opposite_lace_accepts(tasks: list[dict], scored: list[dict]) -> list[str]:
    found = []
    for task, result in zip(tasks, scored, strict=True):
        if result["prediction"]["decision"] != "matched":
            continue
        left = task.get("rakuten", {}).get("attrs", {}).get("lace_count")
        key = result["prediction"].get("au_row_key")
        candidate = next((c for c in task.get("au_candidates", []) if c.get("row_key") == key), None)
        right = candidate.get("attrs", {}).get("lace_count") if candidate else None
        if left is not None and right is not None and left != right:
            found.append(task["case_id"])
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--cpu-predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(f"Refusing existing output directory: {args.output}")
    cases, run_manifest, provenance = validate_artifacts(args.inputs, args.artifacts_dir)
    gpu_manifest_sha = sha256(args.artifacts_dir / "run-manifest.json")
    task_source_sha = sha256(args.tasks)
    cpu_source_sha = sha256(args.cpu_predictions)
    input_source_sha = sha256(args.inputs)
    label_source_sha = sha256(args.labels)
    tasks_full = read_jsonl(args.tasks)
    task_ids = ids(tasks_full, "tasks")
    sample_ids = ids(cases, "inputs")
    if not sample_ids.issubset(task_ids):
        raise ValueError("GPU case sample must be a subset of the full task set")
    task_by_id = {t["case_id"]: t for t in tasks_full}
    tasks = [dict(task_by_id[c["case_id"]]) for c in cases]
    for case in cases:
        task = task_by_id[case["case_id"]]
        pool_a = [r.get("row_key") for r in case.get("au", {}).get("sku_rows", [])]
        pool_b = [r.get("row_key") for r in task.get("au_candidates", [])]
        if pool_a != pool_b:
            raise ValueError(f"Fixed AU candidate pool differs for {case['case_id']}")
        for key in ("split", "dossier_id"):
            if case.get(key) != task.get(key):
                raise ValueError(f"Task/input {key} mismatch for {case['case_id']}")
        task["source_category"] = case.get("source_category")
    cpu_full = read_jsonl(args.cpu_predictions)
    if ids(cpu_full, "CPU predictions") != task_ids:
        raise ValueError("CPU prediction IDs must exactly match full task set")
    cpu = select_by_ids(cpu_full, cases, "CPU predictions")
    cpu = [{**row, "status": ("ok" if row.get("decision") in DECISIONS
                              and (row.get("decision") != "matched" or row.get("top_row_key") in
                                   {c["row_key"] for c in task_by_id[row["case_id"]].get("au_candidates", [])})
                              else "invalid_output"), "evaluation_adapter": "CPU decision/top_row_key"}
           for row in cpu]

    gpu_records = {}
    model_names = [model_slug(m["name"]) for m in run_manifest["models"]]
    for model in model_names:
        filename = f"predictions-{model}.jsonl"
        gpu_records[model] = read_jsonl(args.artifacts_dir / filename)
        validate_gpu_predictions(cases, gpu_records[model], filename)

    # The immutable prediction bundle and all upstream ID/pool checks are now validated.
    labels = gold_join(tasks_full, args.labels, sample_ids)
    args.output.mkdir(parents=True, exist_ok=False)
    model_metrics, timings = {}, {}
    for model in model_names:
        filename = f"predictions-{model}.jsonl"
        raw = gpu_records[model]
        raw_by_id = {r["case_id"]: r for r in raw}
        scored, metrics = score_rows(tasks, labels, raw)
        guarded_records = [{**raw_by_id[t["case_id"]], "parsed": constrained_prediction(t, raw_by_id[t["case_id"]])}
                           for t in tasks]
        guarded_scored, guarded_metrics = score_rows(tasks, labels, guarded_records)
        model_metrics[model] = {"raw_model_alone": {"overall": metrics, **strata_metrics(tasks, scored)},
                                "conservative_guard": {"overall": guarded_metrics, **strata_metrics(tasks, guarded_scored)},
                                "guard_effect_vs_raw": {
                                    "gold_matched_raw_unmatched_guard_review_case_ids": [r["case_id"] for r, g in zip(scored, guarded_scored, strict=True)
                                        if r["gold_decision"] == "matched" and r["prediction"]["decision"] == "unmatched" and g["prediction"]["decision"] == "review"],
                                    "gold_matched_raw_matched_guard_review_case_ids": [r["case_id"] for r, g in zip(scored, guarded_scored, strict=True)
                                        if r["gold_decision"] == "matched" and r["prediction"]["decision"] == "matched" and g["prediction"]["decision"] == "review"]},
                                "selected_sample_safety_audit": {
                                    "gold_review_case_ids": [r["case_id"] for r in scored if r["gold_decision"] == "review"],
                                    "accepted_gold_review_case_ids": [r["case_id"] for r in scored if r["gold_decision"] == "review" and r["prediction"]["decision"] == "matched"],
                                    "accepted_opposite_explicit_lace_crosslink_case_ids": opposite_lace_accepts(tasks, scored)},
                                "latency_tokens_memory": latency_stats(raw)}
        timings[model] = latency_stats(raw)
        with (args.output / f"predictions-scored-{model}.jsonl").open("x", encoding="utf-8") as f:
            for row, original_case in zip(scored, cases, strict=True):
                f.write(json.dumps({**row, "input_record": original_case}, ensure_ascii=False) + "\n")
    cpu_scored, cpu_metrics = score_rows(tasks, labels, cpu)
    model_metrics["cpu_baseline"] = {"overall": cpu_metrics, **strata_metrics(tasks, cpu_scored)}
    with (args.output / "predictions-scored-cpu.jsonl").open("x", encoding="utf-8") as f:
        for row in cpu_scored:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    # Long-form CSV keeps every reported population/mode inspectable.
    with (args.output / "metrics.csv").open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["model", "mode", "stratum", "metric", "value"])
        writer.writeheader()
        for model, report in model_metrics.items():
            modes = (("cpu_baseline", report),) if model == "cpu_baseline" else (("raw_model_alone", report["raw_model_alone"]), ("conservative_guard", report["conservative_guard"]))
            for mode, data in modes:
                for metric, value in data["overall"].items():
                    if metric != "confusion_matrix":
                        writer.writerow({"model": model, "mode": mode, "stratum": "all", "metric": metric, "value": value})
                for stratum, metrics in data.get("strata", {}).items():
                    for metric, value in metrics.items():
                        if metric != "confusion_matrix":
                            writer.writerow({"model": model, "mode": mode, "stratum": stratum, "metric": metric, "value": value})
    if (sha256(args.tasks) != task_source_sha or sha256(args.cpu_predictions) != cpu_source_sha
            or sha256(args.inputs) != input_source_sha or sha256(args.labels) != label_source_sha
            or sha256(args.artifacts_dir / "run-manifest.json") != gpu_manifest_sha):
        raise ValueError("Frozen inputs/tasks/CPU prediction files changed during evaluation")
    validate_artifacts(args.inputs, args.artifacts_dir)
    category_counts = Counter(c.get("source_category", "unknown") for c in cases)
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "case_count": len(cases),
              "sample_source_category_counts": dict(category_counts),
              "model_configuration": run_manifest["models"],
              "run_manifest_sha256": gpu_manifest_sha,
              "label_source": "machine labels; human unverified", "test_status": "reused test-label cases; curtain sample labels are diagnostic, not a fresh holdout",
              "gpu_run_status": "complete", "artifacts_sha256": {k: v["sha256"] for k, v in run_manifest["files"].items()},
              "inputs_sha256": input_source_sha, "tasks_sha256": task_source_sha, "labels_sha256": label_source_sha,
              "cpu_predictions_sha256": sha256(args.cpu_predictions), "provenance": provenance, "models": model_metrics,
              "safety_checks": "selected-sample counts only; no source-gate precision claim",
              "kaggle_measurements_only": timings}
    write_json(args.output / "metrics.json", report)
    text = [f"GPU SKU trial evaluation ({len(cases)} cases; source categories {dict(category_counts)})",
            "Labels: machine annotated, human unverified. Test labels were reused; curtain rows are diagnostic, not a fresh holdout.",
            "Runtime, latency, token and memory figures describe Kaggle execution only; no GCP cost or speed model."]
    for model, data in model_metrics.items():
        if model == "cpu_baseline":
            m = data["overall"]
            text.append(f"CPU baseline: precision={m['accepted_precision_including_wrong_rows']} recall={m['matched_recall']} automatic_coverage={m['automatic_decision_coverage']}")
        else:
            m = data["raw_model_alone"]["overall"]
            g = data["conservative_guard"]["overall"]
            text.append(f"{model}: raw precision={m['accepted_precision_including_wrong_rows']} recall={m['matched_recall']} FPR={m['false_positive_rate_known_negative']} automatic_coverage={m['automatic_decision_coverage']}; guard precision={g['accepted_precision_including_wrong_rows']} recall={g['matched_recall']} automatic_coverage={g['automatic_decision_coverage']}")
    with (args.output / "report.txt").open("x", encoding="utf-8") as f:
        f.write("\n".join(text) + "\n")
    output_manifest = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
                       for p in sorted(args.output.iterdir()) if p.is_file()}
    write_json(args.output / "manifest.json", {"created_at_utc": datetime.now(timezone.utc).isoformat(),
              "evaluator_sha256": sha256(Path(__file__)), "inputs_sha256": input_source_sha,
              "tasks_sha256": task_source_sha, "cpu_predictions_sha256": cpu_source_sha,
              "labels_sha256": label_source_sha, "gpu_artifact_manifest_sha256": gpu_manifest_sha,
              "outputs": output_manifest})
    return 0


def score_rows(tasks: list[dict], gold: dict[str, dict], records: list[dict]) -> tuple[list[dict], dict]:
    by_id = {r["case_id"]: r for r in records}
    scored = []
    for task in tasks:
        raw = by_id[task["case_id"]]
        pred = prediction_for(raw)
        label = gold[task["case_id"]]
        positives = set(label.get("matching_au_row_keys", []))
        pool = {c["row_key"] for c in task.get("au_candidates", [])}
        if pred["decision"] == "matched" and pred["au_row_key"] not in pool:
            pred = {"decision": "review", "au_row_key": None}
        scored.append({"case_id": task["case_id"], "dossier_id": task.get("dossier_id"),
                       "split": task.get("split"), "gold_decision": label["decision"],
                       "gold_row_keys": sorted(positives), "prediction": pred,
                       "raw_record": raw})
    metrics = aggregate(scored)
    return scored, metrics


def aggregate(rows: list[dict]) -> dict:
    matrix = {g: {p: 0 for p in DECISIONS} for g in DECISIONS}
    correct = accepted = wrong_row = false_accept = positive = negative = gold_review = review_accept = 0
    automatic = known = automatic_known = correct_known_auto = 0
    matched_correct = matched_pred = accepted_review = deferred_match = false_reject = 0
    statuses = Counter()
    for row in rows:
        g, p = row["gold_decision"], row["prediction"]["decision"]
        matrix[g][p] += 1
        statuses[row["raw_record"].get("status", "not_reported")] += 1
        accepted += p == "matched"
        automatic += p in {"matched", "unmatched"}
        known += g != "review"
        if g != "review" and p in {"matched", "unmatched"}:
            automatic_known += 1
            correct_known_auto += ((g == "matched" and p == "matched" and
                                    row["prediction"].get("au_row_key") in row["gold_row_keys"])
                                   or (g == "unmatched" and p == "unmatched"))
        positive += g == "matched"
        negative += g == "unmatched"
        gold_review += g == "review"
        if g == "matched" and p == "matched":
            matched_pred += 1
            if row["prediction"].get("au_row_key") in row["gold_row_keys"]:
                correct += 1
                matched_correct += 1
            else:
                wrong_row += 1
        elif g == "unmatched" and p == "matched":
            false_accept += 1
        if g == "review" and p == "matched":
            review_accept += 1
            accepted_review += 1
        if g == "matched" and p == "review":
            deferred_match += 1
        if g == "matched" and p == "unmatched":
            false_reject += 1
    precision = correct / accepted if accepted else None
    recall = correct / positive if positive else None
    return {"case_count": len(rows), "confusion_matrix": matrix, "status_counts": dict(statuses),
            "accepted_count": accepted, "correct_row_accept_count": correct,
            "wrong_row_accept_count": wrong_row, "false_accept_known_negative_count": false_accept,
            "accepted_gold_review_count": review_accept,
            "accepted_precision_including_wrong_rows": precision, "matched_recall": recall,
            "matched_detection_recall": matched_pred / positive if positive else None,
            "matched_detection_precision": matched_pred / (matched_pred + false_accept) if matched_pred + false_accept else None,
            "false_positive_rate_known_negative": false_accept / negative if negative else None,
            "false_reject_matched_to_unmatched": false_reject,
            "deferred_matched_to_review": deferred_match,
            "accepted_review_gold_count": accepted_review,
            "review_gold_count": gold_review,
            "accepted_rate": accepted / len(rows) if rows else None,
            "automatic_decision_coverage": automatic / len(rows) if rows else None,
            "automatic_known_coverage": automatic_known / known if known else None,
            "automatic_known_accuracy": correct_known_auto / automatic_known if automatic_known else None,
            "invalid_or_error_count": sum(v for k, v in statuses.items()
                                           if k in {"invalid_output", "invalid_input_over_limit", "error", "missing"})}


def strata_metrics(tasks: list[dict], scored: list[dict]) -> dict:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, task in enumerate(tasks):
        for key in ("split", "source_category"):
            val = task.get(key)
            if val is not None:
                groups[f"{key}:{val}"].append(i)
        category = str(task.get("source_category") or "").casefold()
        group = "curtain" if category == "curtain" else "noncurtain"
        groups[group].append(i)
    result = {name: aggregate([scored[i] for i in idx]) for name, idx in sorted(groups.items())}
    dossiers = defaultdict(list)
    for i, row in enumerate(scored):
        dossiers[str(row.get("dossier_id"))].append(i)
    per_dossier = {k: aggregate([scored[i] for i in idx]) for k, idx in sorted(dossiers.items())}
    macro = {}
    for metric in ("accepted_precision_including_wrong_rows", "matched_recall", "automatic_decision_coverage"):
        vals = [m[metric] for m in per_dossier.values() if m[metric] is not None]
        macro[metric] = statistics.mean(vals) if vals else None
    return {"strata": result, "per_dossier": per_dossier, "dossier_macro": macro}


def latency_stats(records: list[dict]) -> dict:
    vals = sorted(float(r["latency_seconds"]) for r in records if isinstance(r.get("latency_seconds"), (int, float)) and math.isfinite(r["latency_seconds"]))
    def percentile(p):
        return vals[min(len(vals) - 1, math.ceil(p * len(vals)) - 1)] if vals else None
    return {"count": len(vals), "median_seconds": statistics.median(vals) if vals else None,
            "p95_seconds": percentile(.95), "input_tokens_total": sum(r.get("input_tokens", 0) or 0 for r in records),
            "output_tokens_total": sum(r.get("output_tokens", 0) or 0 for r in records),
            "peak_vram_bytes_max": max((r.get("peak_vram_bytes", 0) or 0 for r in records), default=0)}


if __name__ == "__main__":
    raise SystemExit(main())

