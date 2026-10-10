#!/usr/bin/env python3
"""Rank original literal windows for a whole raw SKU axis, without reading labels.

The retrieval query contains every original alternative and the complete AU row.
It does not contain the desired Rakuten value separately. Scores are relevance
logits, never condition confidence or evidence applicability proof.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import time

HERE = Path(__file__).resolve().parent
TOP_KS = (1, 3, 5)
VARIABLE_PROVENANCE = {"candidate_option_index", "candidate_option_value", "original_relation_request_id"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write(path, rows):
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def query_for(provenance, query_style="axis_options"):
    axis = provenance["axis_name"]
    options = provenance["option_values"]
    if not isinstance(axis, str) or not isinstance(options, list) or not options or not all(isinstance(v, str) for v in options):
        raise ValueError("axis and all raw alternatives must be strings")
    row = provenance["selected_au_row"]
    if row.get("row_key") != provenance["au_row_key"]:
        raise ValueError("selected AU row key mismatch")
    axes = row.get("axes")
    if not isinstance(axes, list) or not axes:
        raise ValueError("complete raw AU row axes are required")
    if any(not isinstance(a.get("axis_name"), str) or not isinstance(a.get("value"), str) for a in axes):
        raise ValueError("AU row axis names and whole values must be strings")
    row_text = "\n".join(a["axis_name"] + "：" + a["value"] for a in axes)
    if query_style == "axis_options":
        return f'この商品の「{axis}」の値はどれですか？\n選択肢：' + " / ".join(options) + "\n選択中のAU行：\n" + row_text
    if query_style == "axis_only":
        return f'この商品の「{axis}」の仕様を説明した文章を探してください。\n選択中のAU行：\n' + row_text
    raise ValueError("unsupported generic retrieval query")


def prepare_rows(requests, empty_requests=(), query_style="axis_options"):
    """Collapse only option duplicates of the same original window identity."""
    groups = {}
    seen = set()
    for request, empty in [(r, False) for r in requests] + [(r, True) for r in empty_requests]:
        request_id = request["id"]
        if request_id in seen:
            raise ValueError("duplicate input relation request ID")
        seen.add(request_id)
        provenance = request["provenance"]
        query = query_for(provenance, query_style)
        window = provenance["window"]
        quote = window["quote"]
        if not isinstance(quote, str):
            raise ValueError("literal quote must be a string")
        if empty != (not bool(quote.strip())):
            raise ValueError("empty quote source classification mismatch")
        if not empty and request.get("premise") != quote:
            raise ValueError("relation premise is not the exact literal window")
        index = provenance["candidate_option_index"]
        options = provenance["option_values"]
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(options):
            raise ValueError("candidate option index out of range")
        if provenance["candidate_option_value"] != options[index]:
            raise ValueError("candidate option is not its original raw alternative")
        common = {k: deepcopy(v) for k, v in provenance.items() if k not in VARIABLE_PROVENANCE}
        key = (provenance["task_id"], provenance["source_request_id"])
        if key not in groups:
            digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:20]
            groups[key] = {"id": "evidence-rank:" + digest, "task_id": key[0], "source_request_id": key[1],
                           "query": query, "passage": quote, "provenance": common,
                           "source_relation_request_ids": [], "candidate_option_indices": [],
                           "excluded_from_inference": empty, "scope_proven": False}
        record = groups[key]
        if record["provenance"] != common or record["query"] != query or record["passage"] != quote or record["excluded_from_inference"] != empty:
            raise ValueError("duplicate original window has inconsistent binding or provenance")
        if index in record["candidate_option_indices"]:
            raise ValueError("duplicate original window alternative")
        record["source_relation_request_ids"].append(request_id)
        record["candidate_option_indices"].append(index)
    for record in groups.values():
        if sorted(record["candidate_option_indices"]) != list(range(len(record["provenance"]["option_values"]))):
            raise ValueError("original window does not contain all raw alternatives")
        record["nli_source_ids"] = list(record["source_relation_request_ids"])
    return sorted(groups.values(), key=lambda r: (r["task_id"], r["source_request_id"]))


def score_rows(rows, model, batch_size=8):
    output = deepcopy(rows)
    indices = [i for i, r in enumerate(output) if not r["excluded_from_inference"]]
    pairs = [(output[i]["query"], output[i]["passage"]) for i in indices]
    lengths = model.token_lengths_pairs(pairs)
    if len(lengths) != len(indices):
        raise ValueError("token length result count mismatch")
    accepted = []
    for i, length in zip(indices, lengths):
        output[i]["pair_token_length"] = int(length)
        output[i]["token_count_untruncated"] = int(length)
        if length > model.max_length:
            output[i].update(status="input_too_long", score=None, rank=None)
        else:
            accepted.append(i)
    logits = model.score_pairs([(output[i]["query"], output[i]["passage"]) for i in accepted], batch_size=batch_size)
    if len(logits) != len(accepted):
        raise ValueError("relevance score result count mismatch")
    for i, logit in zip(accepted, logits):
        score = float(logit)
        if not math.isfinite(score):
            raise ValueError("nonfinite relevance score")
        output[i].update(status="ok", score=score, score_kind="raw_logit", rank=None)
    for row in output:
        if row["excluded_from_inference"]:
            row.update(status="empty_literal_quote", score=None, rank=None, pair_token_length=None, token_count_untruncated=None)
    groups = defaultdict(list)
    for row in output:
        if row["status"] == "ok":
            groups[row["task_id"]].append(row)
    selections = []
    for task_id in sorted({r["task_id"] for r in output}):
        ordered = sorted(groups[task_id], key=lambda r: (-r["score"], r["source_request_id"], r["id"]))
        for rank, row in enumerate(ordered, 1):
            row["rank"] = rank
        selections.append({"task_id": task_id, "top_k_source_request_ids": {str(k): [r["source_request_id"] for r in ordered[:k]] for k in TOP_KS},
                           "eligible_window_count": len(ordered), "scope_proven": False, "production_eligible": False})
    return output, selections


def run(input_dir: Path, output_dir: Path, model_dir: Path, threads=1, batch_size=8, model_factory=None, query_style="axis_options"):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest = json.loads((input_dir / "manifest.json").read_text())
    names = ["requests.jsonl", "empty-quote-requests.jsonl"]
    for name in names:
        if sha(input_dir / name) != manifest["output_sha256"][name]:
            raise ValueError("input SHA mismatch: " + name)
    requests, empty = (read(input_dir / n) for n in names)
    if len(requests) != manifest["request_count"] or len(empty) != manifest["empty_literal_quote_request_count"]:
        raise ValueError("input manifest row count mismatch")
    rows = prepare_rows(requests, empty, query_style)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in names + ["manifest.json"]:
        shutil.copyfile(input_dir / name, output_dir / ("source-" + name))
    write(output_dir / "ranking-requests.jsonl", rows)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    code_paths = [Path(__file__), HERE / "backend_reranker.py", HERE / "manifests" / "reranker.json"]
    for path in code_paths:
        shutil.copyfile(path, code_dir / path.name)
    pins = json.loads((code_dir / "reranker.json").read_text())
    freeze = {"labels_read": False, "domain_rules": False, "desired_value_injected_into_query": False,
              "all_original_alternatives_in_query": query_style == "axis_options",
              "all_original_alternatives_retained_in_metadata": True, "query_style": query_style,
              "complete_raw_au_row_in_query": True,
              "passage_scope": "exact_literal_window_quote", "scope_proven": False, "production_eligible": False,
              "synthetic_sku_generation": False, "top_ks_for_diagnostics": list(TOP_KS),
              "score_kind": "raw_logit", "score_is_condition_confidence": False, "truncation_used": False,
              "input_sha256": {n: sha(input_dir / n) for n in names + ["manifest.json"]},
              "code_sha256": {p.name: sha(code_dir / p.name) for p in code_paths}, "pins": pins,
              "threads": threads, "batch_size": batch_size,
              "ranking_requests_sha256": sha(output_dir / "ranking-requests.jsonl")}
    (output_dir / "freeze.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n")
    if model_factory is None:
        spec = importlib.util.spec_from_file_location("evidence_reranker_backend", HERE / "backend_reranker.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        model_factory = module.Model
    started = time.perf_counter()
    model = model_factory(model_dir, threads=threads, verify_files=True)
    print(json.dumps({"event": "model_ready", "model": pins["repo"]}), flush=True)
    predictions, selections = score_rows(rows, model, batch_size)
    write(output_dir / "rankings.jsonl", predictions)
    write(output_dir / "top-k-windows.jsonl", selections)
    summary = {**freeze, "input_relation_request_count": len(requests), "empty_literal_quote_request_count": len(empty),
               "unique_original_window_count": len(rows), "task_count": len(selections),
               "status_counts": dict(Counter(r["status"] for r in predictions)),
               "elapsed_seconds": time.perf_counter() - started,
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("unique_original_window_count", "task_count", "status_counts", "elapsed_seconds")}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=HERE.parents[1] / ".deps" / "sku-reranker-model")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--query-style", choices=("axis_options", "axis_only"), default="axis_options")
    run(**vars(parser.parse_args()))
