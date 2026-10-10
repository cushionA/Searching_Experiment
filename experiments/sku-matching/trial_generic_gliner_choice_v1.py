#!/usr/bin/env python3
"""Classify whole, raw SKU alternatives against a fixed AU title and row.

This diagnostic reads no annotations. A class score is not evidence that a
condition applies, and no result from this script adopts a SKU.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "fastino/GLiNER2.5-multi-Decide"
REVISION = "e173bca1f0c4217d7a55b3b0c705d5ece8a0218d"
INSTRUCTION = ("商品名と選択された行から、現在の商品に当てはまる選択値を1つ選びます。"
               "別の商品や別の行の仕様は使いません。記載が不足する場合は不明を選びます。")
ALL_WINDOWS_INSTRUCTION = ("商品名、選択された行と引用本文から、現在の商品に当てはまる選択値を1つ選びます。"
                           "別の商品や別の行の仕様は使いません。記載が不足する場合は不明を選びます。")
MAX_TOKENS = 512
RESERVED_TOKENS = ("[P]", "[L]", "[C]", "[E]", "[R]", "[DESCRIPTION]",
                   "[EXAMPLE]", "[OUTPUT]", "(", ")")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def unknown_label(values: list[str]) -> str:
    candidate, suffix = "不明", 0
    while candidate in values:
        suffix += 1
        candidate = f"不明（情報不足）#{suffix}"
    return candidate


def schema_text(value: str) -> str:
    """Escape the library's prompt delimiters; never remove a whole condition."""
    for token in RESERVED_TOKENS:
        escaped = token.translate(str.maketrans("[]()", "［］（）"))
        value = value.replace(token, escaped)
    return value


def schema_choices(values: list[str], unknown: str) -> tuple[list[str], dict[str, str | None]]:
    labels = [f"選択肢{index + 1}：{schema_text(value)}" for index, value in enumerate(values)]
    labels.append(schema_text(unknown))
    if len(set(labels)) != len(labels):
        raise ValueError("escaped schema labels collide")
    return labels, dict(zip(labels, [*values, None], strict=True))


def binding(row: dict) -> dict:
    provenance = row["provenance"]
    return {key: provenance[key] for key in (
        "task_id", "case_id", "condition_id", "au_row_key", "axis_name",
        "option_values", "selected_value", "source_sku_key", "fixed_au_product_ref", "selected_au_row")}


def selected_premise(row: dict, text_source: str) -> str:
    if text_source == "premise":
        value = row.get("premise")
    elif text_source == "original_relation_premise":
        value = row["provenance"].get("original_relation_premise")
    else:
        raise ValueError("unsupported text source")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("selected source premise must be a nonempty original string")
    return value


def prepare(rows: list[dict], scope: str = "title", text_source: str = "premise") -> list[dict]:
    """Keep whole original alternatives, either by task or by input window."""
    if scope not in ("title", "all_windows"):
        raise ValueError("unsupported input scope")
    if text_source not in ("premise", "original_relation_premise"):
        raise ValueError("unsupported text source")
    if text_source != "premise" and scope != "all_windows":
        raise ValueError("original relation text source requires all_windows mode")
    first: dict[tuple[str, str], dict] = {}
    first_task: dict[str, dict] = {}
    sources: dict[tuple[str, str], list[str]] = {}
    ids: set[str] = set()
    window_owners: dict[str, str] = {}
    for row in rows:
        if row["id"] in ids:
            raise ValueError("duplicate source request id")
        ids.add(row["id"])
        provenance = row["provenance"]
        selected_premise(row, text_source)
        task_id = provenance["task_id"]
        values = provenance["option_values"]
        if (not isinstance(values, list) or not values or
                any(not isinstance(value, str) or not value for value in values) or
                len(set(values)) != len(values)):
            raise ValueError("raw alternatives must be nonempty, unique strings")
        if provenance["selected_value"] not in values:
            raise ValueError("selected value not in complete raw alternatives")
        if not isinstance(provenance["axis_name"], str) or not provenance["axis_name"]:
            raise ValueError("empty raw axis name")
        if scope == "title" and (provenance.get("scope") != "title" or
                                  provenance["window"].get("field_kind") != "plain_title"):
            raise ValueError("this probe accepts original title windows only")
        if provenance["selected_au_row"]["row_key"] != provenance["au_row_key"]:
            raise ValueError("fixed AU row key mismatch")
        option_index = provenance["candidate_option_index"]
        if (not isinstance(option_index, int) or option_index < 0 or option_index >= len(values) or
                provenance["candidate_option_value"] != values[option_index]):
            raise ValueError("candidate index does not address the original alternative")
        source_window = provenance["source_request_id"]
        if not isinstance(source_window, str) or not source_window:
            raise ValueError("missing original source window identifier")
        if source_window in window_owners and window_owners[source_window] != task_id:
            raise ValueError("one source window id belongs to different tasks")
        window_owners[source_window] = task_id
        if task_id in first_task:
            if binding(first_task[task_id]) != binding(row):
                raise ValueError("same task_id has inconsistent fixed row or raw alternatives")
        else:
            first_task[task_id] = row
        key = (task_id, source_window) if scope == "all_windows" else (task_id, "first")
        if key in first:
            old = first[key]
            if source_window == old["provenance"]["source_request_id"]:
                old_window = {k: v for k, v in old["provenance"].items()
                              if k not in ("candidate_option_index", "candidate_option_value", "original_relation_request_id")}
                this_window = {k: v for k, v in provenance.items()
                               if k not in ("candidate_option_index", "candidate_option_value", "original_relation_request_id")}
                if (row["premise"] != old["premise"] or old_window != this_window or
                        selected_premise(row, text_source) != selected_premise(old, text_source)):
                    raise ValueError("one source window has inconsistent AU premise or provenance")
        else:
            first[key] = row
        sources.setdefault(key, []).append(row["id"])
    requests = []
    for key, source in first.items():
        task_id = key[0]
        provenance = source["provenance"]
        au_row = provenance["selected_au_row"]
        source_text = selected_premise(source, text_source)
        title_prefix, delimiter, _ = source_text.partition("\n現在のAU選択行：\n")
        if not delimiter or not title_prefix.startswith("現在のAU商品名："):
            raise ValueError("original premise has no AU title and selected row frame")
        title = title_prefix.removeprefix("現在のAU商品名：")
        text = "現在のAU商品名：" + title + "\n現在のAU選択行：\n"
        text += "\n".join(axis["axis_name"] + "：" + axis["value"] for axis in au_row["axes"])
        quote = provenance["window"]["quote"]
        if scope == "title":
            if title != quote or source_text != text + "\n引用本文：" + title:
                raise ValueError("title and fixed selected row do not bind to original premise")
        else:
            if not isinstance(quote, str) or not source_text.startswith(text + "\n引用本文：" + quote):
                raise ValueError("full premise does not bind original selected row and window quote")
            text = source_text
        instruction = INSTRUCTION if scope == "title" else ALL_WINDOWS_INSTRUCTION
        values = provenance["option_values"]
        unknown = unknown_label(values)
        labels, label_map = schema_choices(values, unknown)
        request_id = task_id if scope == "title" else provenance["source_request_id"]
        requests.append({"id": request_id, "task_id": task_id, "input_scope": scope,
                         "text": text, "axis_name": provenance["axis_name"],
                         "instruction": instruction, "option_values": values,
                         "candidate_labels": [*values, unknown], "unknown_label": unknown,
                         "schema_task_name": schema_text(provenance["axis_name"]),
                         "schema_labels": labels, "schema_label_to_original_value": label_map,
                         "schema_unknown_label": labels[-1],
                         "source_request_ids": sources[key], "source_premise": source_text,
                         "source_input_premise": source["premise"], "text_source": text_source,
                         "source_original_relation_request_ids": [row["provenance"].get("original_relation_request_id")
                             for row in rows if row["id"] in sources[key]],
                         "provenance": provenance, "selected_value_metadata_only": provenance["selected_value"],
                         "scope_proven": False, "desired_value_injected_into_premise": False})
    if not requests:
        raise ValueError("no original input windows")
    return requests


def snapshot_pins(path: Path = Path(__file__).with_name("try_gliner.py")) -> dict:
    """Read only literal model pins without executing a loader or old fixtures."""
    pins = {}
    for statement in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in ("REPO", "REVISION", "FILES"):
                    if target.id in pins:
                        raise ValueError("duplicate pinned model constant")
                    pins[target.id] = ast.literal_eval(statement.value)
    if set(pins) != {"REPO", "REVISION", "FILES"}:
        raise ValueError("official model constants missing from pinned loader")
    return {**pins, "source_sha256": sha(path)}


def verify_snapshot(model_dir: Path) -> dict:
    pinned = snapshot_pins()
    if pinned["REPO"] != MODEL_ID or pinned["REVISION"] != REVISION:
        raise ValueError("loader constants disagree with declared official model identity")
    manifest_path = model_dir / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("repo") != MODEL_ID or manifest.get("revision") != REVISION:
        raise ValueError("GLiNER model identity differs from pinned revision")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files or files != pinned["FILES"]:
        raise ValueError("model manifest differs from independent loader byte pins")
    for name, expected in files.items():
        path = model_dir / name
        if (Path(name).is_absolute() or ".." in Path(name).parts or not path.is_file() or
                path.stat().st_size != expected.get("size_bytes") or sha(path) != expected.get("sha256")):
            raise ValueError(f"GLiNER model file pin mismatch: {name}")
    return {"model_id": MODEL_ID, "revision": REVISION,
            "source_manifest_sha256": sha(manifest_path), "files": files,
            "pinned_loader_sha256": pinned["source_sha256"], "independent_loader_file_pins_verified": True}


def validate_result(result, request: dict) -> dict:
    probabilities = dict(result.probabilities)
    if set(probabilities) != set(request["schema_labels"]):
        raise ValueError("classifier omitted or added a raw alternative")
    probabilities = {label: float(probabilities[label]) for label in request["schema_labels"]}
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities.values()):
        raise ValueError("invalid class probability")
    if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-4):
        raise ValueError("exclusive class probabilities do not sum to one")
    label = result.label
    confidence = float(result.confidence)
    if (label not in probabilities or not math.isfinite(confidence) or
            not math.isclose(confidence, probabilities[label], abs_tol=1e-5) or
            not math.isclose(probabilities[label], max(probabilities.values()), abs_tol=1e-5)):
        raise ValueError("selected class and confidence disagree with probabilities")
    value = request["schema_label_to_original_value"][label]
    raw_probabilities = {request["unknown_label"] if request["schema_label_to_original_value"][key] is None
                         else request["schema_label_to_original_value"][key]: probability
                         for key, probability in probabilities.items()}
    return {"chosen_label": request["unknown_label"] if value is None else value,
            "chosen_schema_label": label, "chosen_value": value,
            "confidence": confidence, "probabilities": raw_probabilities,
            "schema_probabilities": probabilities}


def complete_input_token_counts(requests: list[dict], schemas: list, classifier, processor) -> list[int]:
    """Count the actual untruncated encoder input, including compiled schema."""
    counts = []
    for request, schema in zip(requests, schemas, strict=True):
        compiled = classifier.compile_schema(schema)
        batch = processor.collate_fn_inference([(request["text"], compiled.build())],
                                               max_len=None, error_policy="raise")
        counts.append(int(batch.attention_mask[0].sum()))
    return counts


def plan_batches(requests: list[dict], actual_token_counts: list[int], batch_size: int, scope: str) -> list[list[int]]:
    """Only actual encoder lengths decide which complete windows can run."""
    if len(requests) != len(actual_token_counts) or batch_size < 1:
        raise ValueError("invalid batch planning inputs")
    grouped: dict[str, list[int]] = {}
    for index, (request, actual_count) in enumerate(zip(requests, actual_token_counts, strict=True)):
        if not isinstance(actual_count, int) or actual_count < 1:
            raise ValueError("invalid actual encoder token length")
        group = request["task_id"] if scope == "all_windows" else request["id"]
        if actual_count <= MAX_TOKENS:
            grouped.setdefault(group, []).append(index)
    return [indices[start:start + batch_size] for indices in grouped.values()
            for start in range(0, len(indices), batch_size)]


def run(input_path: Path, output_dir: Path, model_dir: Path = ROOT / ".deps/sku-gliner-model",
        threads: int = 1, batch_size: int = 8, scope: str = "title", text_source: str = "premise") -> dict:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if threads < 1 or batch_size < 1:
        raise ValueError("threads and batch size must be positive")
    original_rows = read(input_path)
    requests = prepare(original_rows, scope, text_source)
    pin = verify_snapshot(model_dir)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from gliner2 import AutoExtractor
    from gliner2.classification import Classifier, ClassificationConfig, ClassificationSchema
    if torch.cuda.is_available():
        raise RuntimeError("CPU-only probe refuses an available CUDA device")
    torch.set_num_threads(threads)
    load_start = time.perf_counter()
    model = AutoExtractor.from_pretrained(str(model_dir), local_files_only=True).to("cpu").eval()
    load_seconds = time.perf_counter() - load_start
    if any(parameter.device.type != "cpu" for parameter in model.parameters()):
        raise RuntimeError("model contains a non-CPU parameter")
    classifier = Classifier(model)
    schemas = [ClassificationSchema().single(request["schema_task_name"], request["schema_labels"],
                                            instruction=request["instruction"]) for request in requests]
    estimated_token_counts = [len(model.processor.tokenizer(" ".join((request["text"], request["schema_task_name"],
                                                                     request["instruction"], *request["schema_labels"])),
                                                           add_special_tokens=True)["input_ids"]) + 16
                              for request in requests]
    token_counts = complete_input_token_counts(requests, schemas, classifier, model.processor)
    batches = plan_batches(requests, token_counts, batch_size, scope)
    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(input_path, output_dir / "source_requests.jsonl")
    write(output_dir / "requests.jsonl", requests)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    shutil.copyfile(Path(__file__), code_dir / Path(__file__).name)
    shutil.copyfile(Path(__file__).with_name("try_gliner.py"), code_dir / "try_gliner.py")
    loader_paths = {Path(inspect.getfile(value)).resolve() for value in (
        AutoExtractor, Classifier, ClassificationSchema, ClassificationConfig, type(model.processor))}
    loader_shas = {str(path): sha(path) for path in sorted(loader_paths)}
    freeze = {"schema_version": "generic-original-choice-gliner-v1", "labels_read": False,
              "production_eligible": False, "class_match_is_semantic_proof": False,
              "scope_proven": False, "input_scope": scope,
              "text_source": text_source,
              "text_source_policy": ("original top-level premise" if text_source == "premise" else
                                     "preserved provenance.original_relation_premise; original quote-only input retained separately"),
              "probe_scope": ("fixed_au_title_and_selected_row_only" if scope == "title"
                              else "all_provided_condition_windows_with_complete_original_premises"),
              "input_windows_count": len({(r["provenance"]["task_id"], r["provenance"]["source_request_id"])
                                          for r in original_rows}),
              "condition_count": len({r["provenance"]["task_id"] for r in original_rows}),
              "source_page_coverage_claim": None, "whole_sku_accuracy_measured": False,
              "source_input_sha256": sha(input_path), "requests_sha256": sha(output_dir / "requests.jsonl"),
              "upstream_sidecar_bindings": {
                  name: {"path": str(input_path.parent / name), "sha256": sha(input_path.parent / name)}
                  for name in ("unselected-requests.jsonl", "source-requests.jsonl", "source-manifest.json",
                               "manifest.json", "selections.jsonl", "source-empty-quote-requests.jsonl",
                               "empty-quote-requests.jsonl") if (input_path.parent / name).is_file()},
              "code_sha256": {path.name: sha(path) for path in code_dir.iterdir()},
              "loader_dependency_sha256": loader_shas, "model": pin,
              "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
              "instruction": INSTRUCTION if scope == "title" else ALL_WINDOWS_INSTRUCTION,
              "unknown_selection_means": "unknown; never SKU absence",
              "schema_transport": {"reserved_tokens": list(RESERVED_TOKENS),
                                   "encoding": "index-prefixed whole raw values; reserved ASCII delimiters use full-width forms",
                                   "original_values_preserved_in_requests_and_output": True},
              "desired_value_injected_into_premise": False,
              "preflight": {"max_tokens": MAX_TOKENS, "includes_schema": True,
                            "tokenizer_margin": 0, "token_counts": token_counts, "max_len": None,
                            "actual_encoder_input_token_counts": token_counts,
                            "estimated_schema_token_counts_plus_16_margin_diagnostic_only": estimated_token_counts,
                            "estimated_counts_control_inference": False,
                            "count_method": "actual compiled schema plus model.processor.collate_fn_inference attention_mask sum",
                            "error_policy": "raise"},
              "device": "cpu", "threads": threads, "batch_size": batch_size,
              "inference_batch_sizes": [len(batch) for batch in batches],
              "batch_grouping": "same original condition and raw schema; title mode keeps independent calls",
              "long_input_status": "input_too_long" if scope == "title" else "inference_too_long",
              "precision": sorted({str(parameter.dtype) for parameter in model.parameters()}),
              "source_request_count": len(original_rows), "request_count": len(requests)}
    write_json(output_dir / "freeze.json", freeze)
    print(json.dumps({"event": "frozen_before_inference", "request_count": len(requests),
                      "token_max": max(token_counts), "long_input_count": sum(n > MAX_TOKENS for n in token_counts)}), flush=True)
    records: list[dict | None] = [None] * len(requests)
    elapsed = 0.0
    config = ClassificationConfig(batch_size=batch_size, max_len=None,
                                  max_candidates_per_task=max(64, max(len(r["schema_labels"]) for r in requests)))
    for index, request in enumerate(requests):
        record = {**request, "token_count_with_schema_and_margin": token_counts[index],
                  "actual_encoder_input_token_count": token_counts[index],
                  "estimated_input_token_count_diagnostic_only": estimated_token_counts[index],
                  "class_match_is_semantic_proof": False, "production_eligible": False}
        if token_counts[index] > MAX_TOKENS:
            record.update({"status": freeze["long_input_status"], "chosen_label": request["unknown_label"],
                           "chosen_value": None, "confidence": None, "probabilities": None})
        records[index] = record
    # Only windows with the same complete raw schema share an inference batch.
    completed_windows = 0
    for indices in batches:
        first = requests[indices[0]]
        if any((requests[index]["schema_task_name"], requests[index]["schema_labels"], requests[index]["instruction"])
               != (first["schema_task_name"], first["schema_labels"], first["instruction"]) for index in indices):
            raise ValueError("cannot batch different original choice schemas")
        start = time.perf_counter()
        try:
            output = classifier.batch_classify([requests[index]["text"] for index in indices],
                                               schemas[indices[0]], config=config)
            if len(output) != len(indices):
                raise ValueError("classifier output row count mismatch")
            validated = [validate_result(result[requests[index]["schema_task_name"]], requests[index])
                         for index, result in zip(indices, output, strict=True)]
            for index, result in zip(indices, validated, strict=True):
                records[index].update(result)
                records[index]["status"] = "ok"
        except Exception as error:
            for index in indices:
                request = requests[index]
                records[index].update({"status": "inference_error", "chosen_label": request["unknown_label"],
                                       "chosen_value": None, "confidence": None, "probabilities": None,
                                       "error_type": type(error).__name__, "error": str(error)})
        elapsed += time.perf_counter() - start
        completed_windows += len(indices)
        print(json.dumps({"event": "batch_complete", "completed_inference_windows": completed_windows,
                          "total_inference_windows": sum(len(batch) for batch in batches),
                          "inference_seconds": elapsed}), flush=True)
    write(output_dir / "predictions.jsonl", records)
    summary = {**freeze, "model_load_seconds": load_seconds, "inference_seconds": elapsed,
               "status_counts": dict(Counter(record["status"] for record in records)),
               "unknown_choice_count": sum(record["chosen_value"] is None for record in records),
               "output_sha256": {name: sha(output_dir / name) for name in (
                   "source_requests.jsonl", "requests.jsonl", "freeze.json", "predictions.jsonl")}}
    write_json(output_dir / "summary.json", summary)
    print(json.dumps({key: summary[key] for key in (
        "request_count", "status_counts", "unknown_choice_count", "inference_seconds")}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=ROOT / ".deps/sku-gliner-model")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--scope", choices=("title", "all_windows"), default="title")
    parser.add_argument("--text-source", choices=("premise", "original_relation_premise"), default="premise")
    run(**vars(parser.parse_args()))
