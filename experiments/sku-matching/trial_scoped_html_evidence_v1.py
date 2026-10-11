#!/usr/bin/env python3
"""Offline structured-description retrieval and condition checking on real errors.

This diagnostic keeps the original fixed-URL row gate and reverse requests.
Relevance chooses a source block; it never proves that block applies to a row.
No labels, category branches, source-value injection, or score fusion are used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import nullcontext
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import time

import backend_reranker as RERANKER
import trial_full_scoped_condition_choices_v1 as FULL
from trial_scoped_condition_nli_v1 import HYPOTHESIS_TEMPLATE, verified_ref

JEV, NLI = FULL.JEV, FULL.NLI
HERE, ROOT = FULL.HERE, FULL.ROOT
read, sha, digest = FULL.read, FULL.sha, FULL.digest
write_json, write_rows = FULL.write_json, FULL.write_rows
UNKNOWN, QUESTION, DESCRIPTION = FULL.UNKNOWN, FULL.QUESTION, FULL.DESCRIPTION
UNKNOWN_DESCRIPTION = "この商品の「{axis_name}」に該当する値は、商品名、選択行と引用された説明から特定できない。"
FORWARD = "rakuten_to_au"
DEFAULT_RERANKER = ROOT / ".deps/sku-reranker-model"
JEV_CHUNK_REQUESTS, NLI_CHUNK_PAIRS = 8, 128
SOURCE_NAMES = ("requests.jsonl", "documents.jsonl", "tasks.jsonl",
                "baseline-predictions.jsonl", "blocks.jsonl", "retrieval-requests.jsonl")
CODE_NAMES = tuple(dict.fromkeys((Path(__file__).name, "backend_reranker.py",
                                 "trial_full_scoped_condition_choices_v1.py", *FULL.CODE_NAMES)))


def query_for(request, document):
    """Symmetric retrieval query; the desired source value is not separately added."""
    return ("現在の商品名：" + document["title"]["text"]
            + "\n現在の選択行：\n" + request["target_selected_axes_text"]
            + "\n確認する軸：" + request["axis_name"]
            + "\n元の選択肢：\n" + "\n".join(request["option_values"]))


def state_for(request, document, block):
    return ("現在の商品名：" + document["title"]["text"]
            + "\n現在の選択行：\n" + request["target_selected_axes_text"]
            + "\nこのページの説明（表の文脈とリンク先を含む）：\n" + block["model_passage"])


def load_inputs(prepared_dir):
    requests, documents, bindings, manifest, _ = FULL.load_inputs(prepared_dir)
    if (manifest.get("errors_only") is not True or manifest.get("development_only") is not True
            or manifest.get("independent_test") is not False):
        raise ValueError("only the frozen real development error subset is allowed")
    for name in SOURCE_NAMES:
        if manifest.get("output_sha256", {}).get(name) != sha(prepared_dir / name):
            raise ValueError("input file SHA256 differs: " + name)
    baseline = read(prepared_dir / "baseline-predictions.jsonl")
    old = {p["request_id"]: p for p in baseline}
    lookup = {r["request_id"]: r for r in requests}
    if len(old) != len(baseline) or set(old) != set(lookup):
        raise ValueError("baseline request coverage differs")
    for request in requests:
        prediction = old[request["request_id"]]
        for field in ("request_sha256", "selected_value", "direction"):
            if prediction.get(field) != request[field]:
                raise ValueError("baseline source request binding differs")
        original_bindings = prediction.get("source_task_bindings")
        if (prediction.get("source_request") != request or not isinstance(original_bindings, list)
                or any(task not in original_bindings for task in bindings[request["request_id"]])):
            raise ValueError("baseline complete source/task binding differs")
        if request["direction"] not in {FORWARD, "au_to_rakuten"}:
            raise ValueError("unsupported source direction")
    blocks = read(prepared_dir / "blocks.jsonl")
    block_lookup = {b["block_id"]: b for b in blocks}
    if len(block_lookup) != len(blocks):
        raise ValueError("duplicate block identity")
    for block in blocks:
        if (block.get("target_document_id") not in documents
                or not isinstance(block.get("model_passage"), str)
                or not block["model_passage"].strip()):
            raise ValueError("block must bind a target document and complete passage")
    retrieval = read(prepared_dir / "retrieval-requests.jsonl")
    seen, expected, by_doc = set(), set(), defaultdict(list)
    for block in blocks:
        by_doc[block["target_document_id"]].append(block["block_id"])
    for request in requests:
        if request["direction"] == FORWARD:
            expected.update((request["request_id"], bid) for bid in by_doc[request["target_document_id"]])
    seen_pairs = set()
    for item in retrieval:
        if item["id"] in seen or item["request_id"] not in lookup or item["block_id"] not in block_lookup:
            raise ValueError("duplicate or unbound retrieval request")
        seen.add(item["id"])
        request, block = lookup[item["request_id"]], block_lookup[item["block_id"]]
        pair = (item["request_id"], item["block_id"])
        if pair in seen_pairs:
            raise ValueError("duplicate request/block retrieval binding")
        seen_pairs.add(pair)
        if (request["direction"] != FORWARD
                or item.get("target_document_id") != request["target_document_id"]
                or block["target_document_id"] != request["target_document_id"]
                or item["query"] != query_for(request, documents[request["target_document_id"]])
                or item["passage"] != block["model_passage"]
                or item.get("quote_refs") != block.get("quote_refs")):
            raise ValueError("retrieval input differs from symmetric source renderer")
    if seen_pairs != expected:
        raise ValueError("all forward request/document blocks must be preserved")
    return requests, documents, bindings, old, block_lookup, retrieval, manifest


def block_refs(request, documents, block):
    refs = block.get("quote_refs")
    if (not isinstance(refs, list) or not refs
            or any(ref.get("field") != "description" or not verified_ref(ref, request, documents) for ref in refs)):
        return None
    return [*FULL.current_refs(request, documents), *deepcopy(refs)]


def retrieval_key(row):
    return digest(["structured-html-retrieval-v1", row["query"], row["passage"]])


def rank_retrieval(rows, model, output_dir, progress, batch_size=16):
    """Score exact pairs once, skipping overlong inputs before backend truncation."""
    unique = FULL.unique_inputs(rows, retrieval_key)
    cache, runtime = {}, {"tokenization_seconds": 0.0, "inference_seconds": 0.0}
    for offset in range(0, len(unique), 128):
        chunk = unique[offset:offset + 128]
        pairs = [(r["query"], r["passage"]) for r in chunk]
        started = time.perf_counter()
        try:
            lengths = [int(v) for v in model.token_lengths_pairs(pairs)]
            if len(lengths) != len(chunk) or any(v < 1 for v in lengths):
                raise ValueError("invalid untruncated reranker token lengths")
        except Exception as error:
            lengths = [None] * len(chunk)
            token_error = {"error_type": type(error).__name__, "note": str(error)}
        else:
            token_error = None
        runtime["tokenization_seconds"] += time.perf_counter() - started
        pending = []
        for row, count in zip(chunk, lengths, strict=True):
            key = retrieval_key(row)
            status = "tokenization_error" if count is None else "input_too_long" if count > 512 else "pending"
            cache[key] = {"cache_key": key, "status": status, "raw_logit": None,
                          "token_count_untruncated": count,
                          "error_type": token_error["error_type"] if token_error else None,
                          "note": token_error["note"] if token_error else None}
            if status == "pending":
                pending.append(row)
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            started = time.perf_counter()
            try:
                scores = [float(v) for v in model.score_pairs([(r["query"], r["passage"]) for r in batch], batch_size)]
                if len(scores) != len(batch) or any(not math.isfinite(v) for v in scores):
                    raise ValueError("reranker must return one finite raw logit per pair")
            except Exception as error:
                for row in batch:
                    cache[retrieval_key(row)].update(status="inference_error", error_type=type(error).__name__, note=str(error))
            else:
                for row, score in zip(batch, scores, strict=True):
                    cache[retrieval_key(row)].update(status="ok", raw_logit=score)
            runtime["inference_seconds"] += time.perf_counter() - started
        progress.emit(stage="reranker", completed_pairs=min(offset + len(chunk), len(unique)), total_pairs=len(unique))
    records = [{**row, **cache[retrieval_key(row)]} for row in rows]
    write_rows(output_dir / "retrieval-scores.jsonl", records)
    write_rows(output_dir / "retrieval-unique-scores.jsonl", [cache[retrieval_key(r)] for r in unique])
    grouped = defaultdict(list)
    for record in records:
        grouped[record["request_id"]].append(record)
    selections = {}
    for request_id, group in grouped.items():
        ranked = sorted((r for r in group if r["status"] == "ok"), key=lambda r: (-r["raw_logit"], r["block_id"]))
        leader = ranked[0] if ranked else None
        selections[request_id] = {"request_id": request_id, "block_id": leader["block_id"] if leader else None,
                                  "raw_logit": leader["raw_logit"] if leader else None,
                                  "top_score_tie": bool(leader and sum(r["raw_logit"] == leader["raw_logit"] for r in ranked) > 1),
                                  "ordered_block_ids": [r["block_id"] for r in ranked],
                                  "skipped_block_ids": [r["block_id"] for r in group if r["status"] != "ok"],
                                  "reason": "raw_logit_top1" if leader else "no_usable_block"}
    write_rows(output_dir / "retrieval-selections.jsonl", selections.values())
    runtime.update(pair_count=len(rows), unique_pair_count=len(unique),
                   statuses=dict(Counter(r["status"] for r in records)))
    return selections, runtime


def model_requests(requests, documents, blocks, selections):
    choices, refs, exclusions = [], {}, {}
    for request in requests:
        if request["direction"] != FORWARD:
            continue
        selection = selections.get(request["request_id"], {})
        block = blocks.get(selection.get("block_id"))
        current_refs = block_refs(request, documents, block) if block else None
        if current_refs is None:
            exclusions[request["request_id"]] = "literal_description_reference_invalid" if block else "no_usable_block"
            continue
        refs[request["request_id"]] = current_refs
        choices.append({"id": request["request_id"], "question": QUESTION.format(axis_name=request["axis_name"]),
                        "state": state_for(request, documents[request["target_document_id"]], block),
                        "choices": [{"label": value, "description": DESCRIPTION.format(axis_name=request["axis_name"], value=value)}
                                    for value in request["option_values"]]
                                   + [{"label": UNKNOWN, "description": UNKNOWN_DESCRIPTION.format(axis_name=request["axis_name"])}],
                        "selected_block_id": block["block_id"]})
    return choices, refs, exclusions


def nli_pairs_for(requests, choices, refs, bindings, eligible):
    by_id = {c["id"]: c for c in choices}
    pairs = []
    for request in requests:
        if request["request_id"] not in eligible:
            continue
        for index, value in enumerate(request["option_values"]):
            pairs.append({"id": request["request_id"] + ":structured-nli:" + str(index),
                          "request_id": request["request_id"], "request_sha256": request["request_sha256"],
                          "premise": by_id[request["request_id"]]["state"],
                          "hypothesis": HYPOTHESIS_TEMPLATE.format(axis_name=request["axis_name"], selected_value=value),
                          "option_index": index, "option_value": value, "quote_refs": refs[request["request_id"]],
                          "direction": request["direction"], "source_request": request,
                          "source_task_bindings": bindings[request["request_id"]],
                          "scope_status": "unproved", "production_adoption_allowed": False})
    return pairs


def unique_raw_support(record):
    if record is None or record.get("error_type"):
        return False
    count = record.get("token_count_untruncated")
    if type(count) is not int or count < 1 or NLI.exceeds_token_limit(count):
        return False
    try:
        probabilities = NLI._probabilities(record)
    except (TypeError, ValueError):
        return False
    return bool(probabilities and probabilities["support"] > max(probabilities["conflict"], probabilities["unknown"]))


def prediction(request, baseline, record, refs, exclusion, selection, nli_records, verified=False):
    """Keep source identity byte-for-byte; reverse predictions are unchanged."""
    if request["direction"] != FORWARD:
        return deepcopy(baseline)
    base = {field: deepcopy(baseline[field]) for field in
            ("request_id", "request_sha256", "selected_value", "direction", "source_request", "source_task_bindings")}
    eligible, reason, tie = FULL.jev_eligibility(record) if record else (False, exclusion, False)
    raw_winner = record.get("argmax_label") if record else None
    support = bool(eligible and raw_winner == request["selected_value"] and refs)
    reason = reason or ("selected_value_selected" if support else "alternative_value_selected")
    if verified and support:
        selected = [r for r in nli_records if r["option_value"] == request["selected_value"]]
        support = len(selected) == 1 and unique_raw_support(selected[0])
        reason = "selected_value_and_raw_nli_support" if support else "raw_nli_did_not_support"
    return {**base, "state": "support" if support else "unknown", "winning_value": raw_winner if support else None,
            "diagnostic_winning_value": raw_winner, "reason": reason, "tie": tie,
            "quote_refs": deepcopy(refs) if support else [], "selected_block": deepcopy(selection),
            "candidate_record": record, "nli_candidate_records": nli_records if verified else [],
            "scope_status": "unproved", "production_adoption_allowed": False,
            "variant": "structured-verified" if verified else "structured-jev"}


def run(prepared_dir, output_dir, reranker_model_dir=DEFAULT_RERANKER,
        jev_model_dir=JEV.DEFAULT_MODEL, nli_model_dir=NLI.DEFAULT_MODEL,
        threads=4, batch_size=16, *, reranker_factory=None, jev_runner=None, nli_runner=None):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if threads != 4 or batch_size != 16:
        raise ValueError("frozen CPU settings are four threads and batch size 16")
    started = time.perf_counter()
    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    requests, documents, bindings, baseline, blocks, retrieval, manifest = load_inputs(prepared_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ("manifest.json", *SOURCE_NAMES):
        shutil.copyfile(prepared_dir / name, output_dir / ("source-" + name))
    # Preserve the old variant as an exact file copy, including line formatting.
    shutil.copyfile(prepared_dir / "baseline-predictions.jsonl", output_dir / "baseline-predictions.jsonl")
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in CODE_NAMES:
        shutil.copyfile(HERE / name, code_dir / name)
    reranker_manifest = HERE / "manifests/reranker.json"
    shutil.copyfile(reranker_manifest, code_dir / "reranker.json")
    freeze = {"schema_version": "scoped-html-evidence-v1", "labels_read": False, "labels_in_model_inputs": False,
              "errors_only": True, "development_only": True, "independent_test": False,
              "synthetic_data": False, "scope_status": "unproved", "production_adoption_allowed": False,
              "request_count": len(requests), "forward_request_count": sum(r["direction"] == FORWARD for r in requests),
              "block_count": len(blocks), "retrieval_pair_count": len(retrieval), "body_model_input": True,
              "selected_value_separately_injected": False, "all_raw_original_options_preserved": True,
              "retrieval_policy": "unique raw relevance top1; stable block-ID order for ties; relevance is not scope proof",
              "verification_policy": "JEV unique desired winner and selected candidate raw three-class NLI support unique argmax",
              "input_length_policy": "no truncation; reranker >512 skips block; JEV/NLI overlong or errors become unknown",
              "threads": threads, "batch_size": batch_size, "device": "cpu", "jev_precision": "fp32",
              "nli_precision": "fp32", "reranker_precision": "int8", "reverse_requests": "exact baseline copy",
              "jev_chunk_requests": JEV_CHUNK_REQUESTS, "nli_chunk_pairs": NLI_CHUNK_PAIRS,
              "question_template": QUESTION, "choice_description_template": DESCRIPTION,
              "unknown_description_template": UNKNOWN_DESCRIPTION, "hypothesis_template": HYPOTHESIS_TEMPLATE,
              "input_sha256": {name: sha(prepared_dir / name) for name in ("manifest.json", *SOURCE_NAMES)},
              "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()}, "jev_pin": JEV._pins(),
              "nli_pin": {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
                          "parameter_count": NLI.FP32_PARAMETER_COUNT, "files_sha256": NLI.NLI_FILE_SHA256},
              "reranker_pin": json.loads(reranker_manifest.read_text(encoding="utf-8"))}
    write_json(output_dir / "freeze.json", freeze)
    jev_runtime, nli_runtime, jev_pins, nli_pins = {}, {}, None, None
    with FULL.Progress(output_dir) as progress:
        progress.emit(stage="reranker_load")
        load_started = time.perf_counter()
        model = (reranker_factory or RERANKER.Model)(reranker_model_dir, threads=threads, verify_files=True)
        if model.metadata != freeze["reranker_pin"]:
            raise ValueError("verified reranker identity differs from frozen pin")
        load_seconds = time.perf_counter() - load_started
        selections, reranker_runtime = rank_retrieval(retrieval, model, output_dir, progress, batch_size)
        reranker_runtime["model_load_and_file_verification_seconds"] = load_seconds
        del model
        choices, refs, exclusions = model_requests(requests, documents, blocks, selections)
        write_rows(output_dir / "model-requests.jsonl", choices)
        write_rows(output_dir / "model-skips.jsonl", [{"request_id": rid, "reason": reason} for rid, reason in exclusions.items()])
        jev_call, nli_call = jev_runner or JEV.run_choices, nli_runner or NLI.run_pairs
        unique_choices = FULL.unique_inputs(choices, lambda r: JEV.request_key(r["question"], r["state"], r["choices"]))
        cache = {}
        with (FULL.reuse_jev_loader() if jev_runner is None and choices else nullcontext()):
            with (output_dir / "jev-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
                for offset in range(0, len(unique_choices), JEV_CHUNK_REQUESTS):
                    chunk = unique_choices[offset:offset + JEV_CHUNK_REQUESTS]
                    result = jev_call(chunk, model_dir=jev_model_dir, threads=threads, batch_size=batch_size)
                    if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                        raise ValueError("JEV output identity differs")
                    FULL._append(handle, result["records"])
                    cache.update({r["cache_key"]: r for r in result["records"]})
                    jev_pins = result["pins"]
                    FULL._accumulate(jev_runtime, result["runtime"])
                    progress.emit(stage="jev", completed_groups=min(offset + len(chunk), len(unique_choices)), total_groups=len(unique_choices))
            jev_records = jev_call(choices, model_dir=jev_model_dir, threads=threads, batch_size=batch_size, cache=cache)["records"] if choices else []
        write_rows(output_dir / "jev-classification-options.jsonl", jev_records)
        by_jev = {r["id"]: r for r in jev_records}
        eligible = {rid for rid, record in by_jev.items() if FULL.jev_eligibility(record)[0]}
        original_bindings = {rid: old["source_task_bindings"] for rid, old in baseline.items()}
        pairs = nli_pairs_for(requests, choices, refs, original_bindings, eligible)
        write_rows(output_dir / "model-nli-pairs.jsonl", pairs)
        unique_pairs = FULL.unique_inputs(pairs, lambda p: NLI.pair_key(p["premise"], p["hypothesis"]))
        nli_cache = {}
        with (FULL.reuse_nli_factories() if nli_runner is None and pairs else nullcontext()):
            with (output_dir / "nli-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
                for offset in range(0, len(unique_pairs), NLI_CHUNK_PAIRS):
                    chunk = unique_pairs[offset:offset + NLI_CHUNK_PAIRS]
                    result = FULL.score_nli_chunk(nli_call, chunk, nli_model_dir, threads, batch_size)
                    if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                        raise ValueError("NLI output identity differs")
                    FULL._append(handle, result["records"])
                    nli_cache.update({r["cache_key"]: r for r in result["records"]})
                    nli_pins = result["pins"]
                    FULL._accumulate(nli_runtime, result["runtime"])
                    progress.emit(stage="nli", completed_pairs=min(offset + len(chunk), len(unique_pairs)), total_pairs=len(unique_pairs))
            nli_records = nli_call(pairs, model_dir=nli_model_dir, threads=threads, batch_size=batch_size, cache=nli_cache)["records"] if pairs else []
        write_rows(output_dir / "nli-classification-options.jsonl", nli_records)
        grouped = defaultdict(list)
        for record in nli_records:
            grouped[record["request_id"]].append(record)
        predictions = {}
        for verified in (False, True):
            name = "structured-verified" if verified else "structured-jev"
            rows = [prediction(r, baseline[r["request_id"]], by_jev.get(r["request_id"]), refs.get(r["request_id"]),
                               exclusions.get(r["request_id"]), selections.get(r["request_id"]), grouped[r["request_id"]], verified)
                    for r in requests]
            write_rows(output_dir / (name + "-predictions.jsonl"), rows)
            predictions[name] = dict(Counter(r["state"] for r in rows))
        for name, expected in freeze["input_sha256"].items():
            if sha(prepared_dir / name) != expected:
                raise ValueError("source input changed during inference: " + name)
        for name, expected in freeze["code_sha256"].items():
            current = reranker_manifest if name == "reranker.json" else HERE / name
            if sha(current) != expected:
                raise ValueError("source code changed during inference: " + name)
        progress.emit(stage="complete", request_count=len(requests))
    summary = {**freeze, "prediction_states": predictions, "total_seconds": time.perf_counter() - started,
               "reranker_runtime": reranker_runtime, "jev_runtime": jev_runtime, "nli_runtime": nli_runtime,
               "verified_jev_pin": jev_pins, "verified_nli_pin": nli_pins,
               "unique_jev_request_count": len(unique_choices), "nli_pair_count": len(pairs),
               "unique_nli_pair_count": len(unique_pairs), "model_skips": dict(Counter(exclusions.values())),
               "input_and_code_sha256_verified_after_inference": True,
               "baseline_exact_file_copy": sha(output_dir / "baseline-predictions.jsonl") == sha(prepared_dir / "baseline-predictions.jsonl"),
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reranker-model-dir", type=Path, default=DEFAULT_RERANKER)
    parser.add_argument("--jev-model-dir", type=Path, default=JEV.DEFAULT_MODEL)
    parser.add_argument("--nli-model-dir", type=Path, default=NLI.DEFAULT_MODEL)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    run(**vars(parser.parse_args()))
