#!/usr/bin/env python3
"""Pinned CPU retrieval and whole-value classification for a frozen integration.

No annotations are read. Results are experimental; literal citation existence
does not prove semantic support or ownership. Desired values never enter model
queries or texts. Byte-identical model inputs share predictions, not labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import time

from prepare_generic_residual_evidence_v1 import make_windows
from trial_generic_gliner_choice_v1 import (
    MAX_TOKENS, complete_input_token_counts, schema_choices, schema_text,
    unknown_label, validate_result, verify_snapshot,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INSTRUCTION = ("商品名、現在選択された条件と引用本文から、対象の商品に当てはまる選択値を1つ選びます。"
               "別の商品や別の選択条件の仕様は使いません。記載が不足する場合は不明を選びます。")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_rows(path, rows):
    with Path(path).open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")


def model_payload(request):
    """A selected source value is binding metadata, never a model input."""
    axes = request["target_selected_axes"]
    rendered = "\n".join(a["axis_name"] + "：" + a["value"] for a in axes)
    if rendered != request["target_selected_axes_text"]:
        raise ValueError("complete selected target axes rendering differs")
    options = request["option_values"]
    if (not options or len(set(options)) != len(options) or
            any(not isinstance(v, str) or not v for v in options) or
            request["selected_value"] not in options):
        raise ValueError("whole options must contain selected value without duplicates")
    return {key: request[key] for key in (
        "direction", "axis_name", "option_values", "target_document_id",
        "target_document_sha256", "target_selected_axes_text")}


def quote_ref(document, field, text, start=0, end=None):
    end = len(text) if end is None else end
    return {"document_id": document["document_id"], "field": field,
            "start": start, "end": end, "quote": text[start:end]}


def windows_for(document, char_budget=300, overlap=75):
    text = document["description"]["text"]
    return [{**w, "quote_ref": quote_ref(document, "description", text,
                                          w["derived_text_start"], w["derived_text_end"])}
            for w in make_windows(text, document["document_id"] + ":description", char_budget, overlap)]


def request_for(payload, document, quote=None):
    values = payload["option_values"]
    unknown = unknown_label(values)
    labels, mapping = schema_choices(values, unknown)
    title = document["title"]["text"]
    text = "商品名：" + title + "\n現在選択された条件：\n" + payload["target_selected_axes_text"]
    if quote is not None:
        text += "\n引用本文：\n" + quote
    return {"text": text, "schema_task_name": schema_text(payload["axis_name"]),
            "schema_labels": labels, "schema_label_to_original_value": mapping,
            "unknown_label": unknown, "instruction": INSTRUCTION}


def aggregate(records, selected_value, confidence):
    known = [r for r in records if r["status"] == "ok" and r["chosen_value"] is not None
             and r["confidence"] >= confidence]
    values = {r["chosen_value"] for r in known}
    if len(values) != 1:
        return "unknown", None, []
    winner = next(iter(values))
    if winner != selected_value:
        return "unknown", winner, []
    refs, seen = [], set()
    for record in known:
        for ref in record["quote_refs"]:
            key = canonical(ref)
            if key not in seen:
                seen.add(key)
                refs.append(ref)
    return "support", winner, refs


def run(input_dir, output_dir, gliner_dir, reranker_dir, threads=4, batch_size=8,
        top_k=5, confidence=.5):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if not 0 < confidence <= 1 or top_k < 1 or threads < 1 or batch_size < 1:
        raise ValueError("invalid fixed diagnostic settings")
    manifest = json.loads((input_dir / "manifest.json").read_text())
    for name in ("requests.jsonl", "documents.jsonl"):
        if manifest["output_sha256"][name] != sha(input_dir / name):
            raise ValueError("frozen input differs: " + name)
    requests, documents = read(input_dir / "requests.jsonl"), read(input_dir / "documents.jsonl")
    by_document = {d["document_id"]: d for d in documents}
    if len(by_document) != len(documents):
        raise ValueError("duplicate document")
    for doc in documents:
        if digest({k: v for k, v in doc.items() if k != "document_sha256"}) != doc["document_sha256"]:
            raise ValueError("document semantic SHA differs")
    groups = {}
    for request in requests:
        payload = model_payload(request)
        doc = by_document[payload["target_document_id"]]
        # Producer freezes document_sha256 explicitly; retain and verify that contract.
        if payload["target_document_sha256"] != doc["document_sha256"]:
            raise ValueError("target document SHA binding differs")
        key = digest(payload)
        group = groups.setdefault(key, {"group_id": key, "payload": payload, "request_ids": []})
        if group["payload"] != payload:
            raise ValueError("model payload hash collision")
        group["request_ids"].append(request["request_id"])
    pin = verify_snapshot(gliner_dir)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from gliner2 import AutoExtractor
    from gliner2.classification import Classifier, ClassificationConfig, ClassificationSchema
    from backend_reranker import Model
    torch.set_num_threads(threads)
    model = AutoExtractor.from_pretrained(str(gliner_dir), local_files_only=True).to("cpu").eval()
    if any(p.device.type != "cpu" for p in model.parameters()):
        raise RuntimeError("CPU-only model required")
    classifier = Classifier(model)
    reranker = Model(reranker_dir, threads=threads)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ("manifest.json", "requests.jsonl", "documents.jsonl"):
        shutil.copyfile(input_dir / name, output_dir / ("source-" + name))
    code_dir = output_dir / "code"
    code_dir.mkdir()
    code_paths = [Path(__file__), HERE / "trial_generic_gliner_choice_v1.py",
                  HERE / "try_gliner.py", HERE / "prepare_generic_residual_evidence_v1.py",
                  HERE / "backend_reranker.py", HERE / "manifests/reranker.json"]
    for p in code_paths:
        shutil.copyfile(p, code_dir / p.name)
    freeze = {"schema_version": "integrated-description-cpu-v1", "labels_read": False,
              "synthetic_data": False, "production_adoption_allowed": False,
              "scope_status": "unproved", "citations_are_semantic_proof": False,
              "desired_source_value_in_model_input": False,
              "top_k": top_k, "confidence": confidence,
              "threshold_policy": "existing .5 development diagnostic; not independently calibrated",
              "document_window_chars": 300, "overlap_chars": 75,
              "input_sha256": {n: sha(input_dir / n) for n in ("manifest.json", "requests.jsonl", "documents.jsonl")},
              "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()},
              "gliner_pin": pin, "reranker_pin": reranker.metadata,
              "request_count": len(requests), "model_group_count": len(groups),
              "threads": threads, "batch_size": batch_size, "instruction": INSTRUCTION,
              "device": "cpu", "precision": sorted({str(p.dtype) for p in model.parameters()}),
              "loader_dependency_sha256": {inspect.getfile(c): sha(inspect.getfile(c))
                                             for c in (Classifier, ClassificationConfig, ClassificationSchema)},
              "input_length_policy": "actual encoder/schema token count <=512, no truncation",
              "text_coverage_policy": "all description windows retained; title always included; body top5 only read by classifier"}
    write_rows(output_dir / "model-groups.jsonl", groups.values())
    freeze["model_groups_sha256"] = sha(output_dir / "model-groups.jsonl")
    write(output_dir / "freeze.json", freeze)
    print(canonical({"event": "frozen", "requests": len(requests), "groups": len(groups)}), flush=True)
    prediction_cache, group_results = {}, {}
    rank_count, window_count, statuses = 0, 0, Counter()
    start = time.perf_counter()
    with (output_dir / "ranking-windows.jsonl").open("x", encoding="utf-8") as ranking_stream, \
            (output_dir / "classification-windows.jsonl").open("x", encoding="utf-8") as class_stream, \
            (output_dir / "group-results.jsonl").open("x", encoding="utf-8") as group_stream:
        for group_index, (key, group) in enumerate(groups.items(), 1):
            payload, doc = group["payload"], by_document[group["payload"]["target_document_id"]]
            body_windows = windows_for(doc)
            query = f'この商品の「{payload["axis_name"]}」の仕様を説明した文章を探してください。\n現在選択された条件：\n' + payload["target_selected_axes_text"]
            pairs = [(query, w["text"]) for w in body_windows]
            lengths = reranker.token_lengths_pairs(pairs)
            eligible = [i for i, n in enumerate(lengths) if n <= reranker.max_length and body_windows[i]["text"].strip()]
            logits = reranker.score_pairs([pairs[i] for i in eligible], batch_size=batch_size)
            ranked = sorted(zip(eligible, map(float, logits), strict=True), key=lambda t: (-t[1], t[0]))
            if any(not math.isfinite(score) for _, score in ranked):
                raise ValueError("nonfinite relevance logit")
            selected = {i for i, _ in ranked[:top_k]}
            scores = dict(ranked)
            for i, w in enumerate(body_windows):
                ranking_stream.write(canonical({"group_id": key, **w, "query": query,
                    "pair_token_count": int(lengths[i]), "score": scores.get(i), "selected": i in selected,
                    "status": "ok" if i in scores else "empty_or_too_long", "scope_status": "unproved"}) + "\n")
            rank_count += len(body_windows)
            title_ref = quote_ref(doc, "title", doc["title"]["text"])
            axes_ref = quote_ref(doc, "selected_axes", payload["target_selected_axes_text"])
            base_refs = [r for r in (title_ref, axes_ref) if r["end"] > r["start"]]
            work = [(request_for(payload, doc), base_refs, "title")]
            work += [(request_for(payload, doc, body_windows[i]["text"]),
                      [*base_refs, body_windows[i]["quote_ref"]], body_windows[i]["window_id"])
                     for i, _ in ranked[:top_k]]
            records, missing = [], []
            for request, refs, window_id in work:
                model_key = digest(request)
                record = {"group_id": key, "window_id": window_id, "model_input_sha256": model_key,
                          "request": request, "quote_refs": refs, "scope_status": "unproved"}
                records.append(record)
                if model_key in prediction_cache:
                    record.update(prediction_cache[model_key], reused_identical_model_input=True)
                else:
                    missing.append((record, request))
            # Every window of this group has the same original whole-value schema.
            for offset in range(0, len(missing), batch_size):
                batch = missing[offset:offset + batch_size]
                schemas = [ClassificationSchema().single(r["schema_task_name"], r["schema_labels"],
                                                         instruction=r["instruction"]) for _, r in batch]
                counts = complete_input_token_counts([r for _, r in batch], schemas, classifier, model.processor)
                accepted = []
                for (record, req), n in zip(batch, counts, strict=True):
                    record["actual_encoder_input_token_count"] = n
                    if n > MAX_TOKENS:
                        record.update(status="input_too_long", chosen_value=None, confidence=None)
                    else:
                        accepted.append((record, req))
                if accepted:
                    req = accepted[0][1]
                    schema = ClassificationSchema().single(req["schema_task_name"], req["schema_labels"], instruction=req["instruction"])
                    config = ClassificationConfig(batch_size=batch_size, max_len=None,
                                                  max_candidates_per_task=max(64, len(req["schema_labels"])))
                    results = classifier.batch_classify([r["text"] for _, r in accepted], schema, config=config)
                    if len(results) != len(accepted):
                        raise ValueError("classification result count differs")
                    for (record, req), result in zip(accepted, results, strict=True):
                        record.update(validate_result(result[req["schema_task_name"]], req), status="ok")
                for record, req in batch:
                    prediction_cache[record["model_input_sha256"]] = {k: v for k, v in record.items()
                        if k not in ("group_id", "window_id", "model_input_sha256", "request", "quote_refs", "scope_status")}
            for record in records:
                statuses[record["status"]] += 1
                class_stream.write(canonical(record) + "\n")
            window_count += len(records)
            group_results[key] = records
            group_stream.write(canonical({"group_id": key, "classification_windows": len(records),
                                           "request_ids": group["request_ids"]}) + "\n")
            for stream in (ranking_stream, class_stream, group_stream):
                stream.flush()
            if group_index % 10 == 0 or group_index == len(groups):
                print(canonical({"event": "groups_complete", "completed": group_index, "total": len(groups),
                                 "seconds": round(time.perf_counter() - start, 3)}), flush=True)
    predictions = []
    for request in requests:
        key = digest(model_payload(request))
        state, winner, refs = aggregate(group_results[key], request["selected_value"], confidence)
        predictions.append({"request_id": request["request_id"], "request_sha256": request["request_sha256"],
                            "selected_value": request["selected_value"], "state": state, "winning_value": winner,
                            "quote_refs": refs, "scope_status": "unproved", "model_group_id": key,
                            "production_adoption_allowed": False})
    write_rows(output_dir / "predictions.jsonl", predictions)
    summary = {**freeze, "inference_seconds": time.perf_counter() - start,
               "ranked_description_windows": rank_count, "classification_window_count": window_count,
               "byte_identical_model_input_count": len(prediction_cache), "window_status_counts": dict(statuses),
               "prediction_states": dict(Counter(p["state"] for p in predictions)),
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write(output_dir / "summary.json", summary)
    print(canonical({k: summary[k] for k in ("request_count", "model_group_count", "prediction_states", "inference_seconds")}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gliner-dir", type=Path, default=ROOT / ".deps/sku-gliner-model")
    parser.add_argument("--reranker-dir", type=Path, default=ROOT / ".deps/sku-reranker-model")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--confidence", type=float, default=.5)
    run(**vars(parser.parse_args()))
