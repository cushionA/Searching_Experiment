#!/usr/bin/env python3
"""CPU diagnostic: judge a complete condition for one current product and row.

Claude's frozen alignment and input contract remain unchanged. This experiment
reuses received top-five passages and changes only the classification task. It
reads no annotations; model judgments and literal citations are not semantic
proof or permission to adopt a SKU in production.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import time

from trial_generic_gliner_choice_v1 import MAX_TOKENS, complete_input_token_counts, verify_snapshot

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEMANTIC_FIELDS = ("direction", "axis_name", "selected_value", "option_values",
                   "target_document_id", "target_document_sha256", "target_selected_axes",
                   "target_selected_axes_text")
RANKING_FIELDS = ("direction", "axis_name", "option_values", "target_document_id",
                  "target_document_sha256", "target_selected_axes_text")
SCHEMA_NAME = "現在の商品に対する条件の判定"
LABEL_TO_STATE = {
    "支持：現在の商品と選択行で仮説の条件全体が成立する": "support",
    "矛盾：現在の商品と選択行の仕様が仮説の条件と相容れない": "conflict",
    "不明：現在の商品に対する条件の成立を判断できない": "unknown",
}
BASE_INSTRUCTION = (
    "仮説は確認対象であり正解ではありません。商品名と現在の選択行で対象を固定し、"
    "これらの記載と引用本文を根拠に条件全体が成立するか判定します。複合条件の一部分だけでは支持できません。"
    "明確な支持は支持、両立しない仕様は矛盾、記載不足や適用範囲が不明なら不明です。"
    "条件の語が出現するだけでは支持できません。")
INSTRUCTIONS = {
    "entailment": BASE_INSTRUCTION,
    "ownership": BASE_INSTRUCTION + (
        "引用の仕様が現在の商品と選択行のものか確認します。別商品への案内、シリーズの"
        "選択肢一覧、別サイズや別構成の説明、使用例は現在の販売仕様を証明しません。"
        "現在の商品への帰属が確認できなければ不明です。"),
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_rows(path, rows):
    with Path(path).open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")


def validate_request(request):
    """Validate the exact producer identity without changing its raw strings."""
    semantic = {key: request[key] for key in SEMANTIC_FIELDS}
    axes = request["target_selected_axes"]
    if not isinstance(axes, list) or not all(
        isinstance(a, dict) and isinstance(a.get("axis_name"), str) and
        isinstance(a.get("value"), str) for a in axes
    ):
        raise ValueError("invalid complete target selected axes")
    rendered = "\n".join(a["axis_name"] + "：" + a["value"] for a in axes)
    if rendered != request["target_selected_axes_text"]:
        raise ValueError("complete target selected axes rendering differs")
    options = request["option_values"]
    if (not isinstance(options, list) or not options or
            any(not isinstance(v, str) or not v for v in options) or
            len(set(options)) != len(options) or request["selected_value"] not in options):
        raise ValueError("whole original alternatives must contain selected value")
    if not isinstance(request["axis_name"], str) or not request["axis_name"]:
        raise ValueError("empty raw axis name")
    if request["direction"] not in {"rakuten_to_au", "au_to_rakuten"}:
        raise ValueError("unsupported source direction")
    identity = digest(semantic)
    if request["request_sha256"] != identity or request["request_id"] != "generic-description:" + identity:
        raise ValueError("request identity differs from complete producer semantic input")
    return semantic


def model_payload(request):
    semantic = validate_request(request)
    payload = {key: semantic[key] for key in (
        "direction", "axis_name", "selected_value", "target_document_id",
        "target_document_sha256", "target_selected_axes_text")}
    # Preserve each received retrieval binding even when classifier texts match.
    payload["received_ranking_group_id"] = digest({k: semantic[k] for k in RANKING_FIELDS})
    return payload


def request_for(payload, document, variant, quote=None):
    if variant not in INSTRUCTIONS:
        raise ValueError("unsupported frozen task variant")
    text = "現在の商品名：" + document["title"]["text"]
    text += "\n現在の選択行：\n" + payload["target_selected_axes_text"]
    text += "\n検証する条件（仮説）：\n軸名：" + payload["axis_name"]
    text += "\n選択値：" + payload["selected_value"]
    if quote is not None:
        text += "\n引用本文：\n" + quote
    return {"text": text, "schema_task_name": SCHEMA_NAME,
            "schema_labels": list(LABEL_TO_STATE), "instruction": INSTRUCTIONS[variant],
            "schema_label_to_state": dict(LABEL_TO_STATE), "variant": variant,
            "hypothesis": {"axis_name": payload["axis_name"], "selected_value": payload["selected_value"]}}


def quote_ref(document, field, text, start=0, end=None):
    end = len(text) if end is None else end
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError("invalid literal quote span")
    return {"document_id": document["document_id"], "field": field,
            "start": start, "end": end, "quote": text[start:end]}


def verify_window(window, document):
    ref = window["quote_ref"]
    text = document["description"]["text"]
    expected = quote_ref(document, "description", text, window["derived_text_start"], window["derived_text_end"])
    if (ref != expected or window["text"] != ref["quote"] or
            window["offset_unit"] != "unicode_characters" or
            window["offset_space"] != document["document_id"] + ":description"):
        raise ValueError("received passage does not bind to full target description")
    if (not isinstance(window["score"], (int, float)) or isinstance(window["score"], bool) or
            not math.isfinite(window["score"]) or window["status"] != "ok"):
        raise ValueError("selected received passage has no valid relevance score")
    return window


def validate_result(result, request):
    probabilities = {key: float(value) for key, value in dict(result.probabilities).items()}
    if set(probabilities) != set(request["schema_labels"]):
        raise ValueError("classifier omitted or added a relation class")
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities.values()):
        raise ValueError("invalid relation class probability")
    if not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-4):
        raise ValueError("exclusive relation probabilities do not sum to one")
    label, confidence = result.label, float(result.confidence)
    if (label not in probabilities or not math.isfinite(confidence) or
            not math.isclose(confidence, probabilities[label], abs_tol=1e-5) or
            not math.isclose(probabilities[label], max(probabilities.values()), abs_tol=1e-5)):
        raise ValueError("selected relation label disagrees with probabilities")
    return {"chosen_schema_label": label, "chosen_state": request["schema_label_to_state"][label],
            "confidence": confidence, "schema_probabilities": probabilities,
            "probabilities": {request["schema_label_to_state"][k]: v for k, v in probabilities.items()}}


def length_status(record, token_count):
    if type(token_count) is not int or token_count < 1:
        raise ValueError("invalid actual encoder input length")
    record["actual_encoder_input_token_count"] = token_count
    if token_count > MAX_TOKENS:
        record.update(status="input_too_long", chosen_state="unknown", confidence=None)
        return False
    return True


def apply_model_results(accepted, classify_call):
    """Record a failed model batch as unknown, retaining complete inputs."""
    try:
        results = classify_call()
        if len(results) != len(accepted):
            raise ValueError("classification result count differs")
        validated = [validate_result(result[request["schema_task_name"]], request)
                     for (_, request), result in zip(accepted, results, strict=True)]
    except Exception as error:
        for record, _ in accepted:
            record.update(status="inference_error", chosen_state="unknown", confidence=None,
                          error_type=type(error).__name__, error_message=str(error)[:1000])
        return
    for (record, _), value in zip(accepted, validated, strict=True):
        record.update(value, status="ok")


def aggregate(records, confidence=.5):
    """Only direct high-confidence relation judgments count; conflict vetoes."""
    decisive = [r for r in records if r["status"] == "ok" and r["chosen_state"] in {"support", "conflict"}
                and r["confidence"] >= confidence]
    state = "conflict" if any(r["chosen_state"] == "conflict" for r in decisive) else (
        "support" if any(r["chosen_state"] == "support" for r in decisive) else "unknown")
    refs, seen = [], set()
    for record in decisive:
        if record["chosen_state"] != state:
            continue
        for ref in record["quote_refs"]:
            key = canonical(ref)
            if key not in seen:
                refs.append(ref)
                seen.add(key)
    return state, refs


def load_sources(input_dir, ranking_dir):
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    names = ["requests.jsonl", "documents.jsonl"]
    if (input_dir / "tasks.jsonl").exists():
        names.append("tasks.jsonl")
    for name in names:
        if manifest["output_sha256"].get(name) != sha(input_dir / name):
            raise ValueError("subset source SHA differs: " + name)
    requests, documents = read(input_dir / "requests.jsonl"), read(input_dir / "documents.jsonl")
    if not requests or len({r["request_id"] for r in requests}) != len(requests):
        raise ValueError("requests must be nonempty with unique IDs")
    by_document = {d["document_id"]: d for d in documents}
    if len(by_document) != len(documents):
        raise ValueError("duplicate target document")
    for document in documents:
        if digest({k: v for k, v in document.items() if k != "document_sha256"}) != document["document_sha256"]:
            raise ValueError("target document semantic SHA differs")
        if not all(isinstance(document[field]["text"], str) for field in ("title", "description")):
            raise ValueError("target title/description must remain original strings")
    for request in requests:
        validate_request(request)
        document = by_document[request["target_document_id"]]
        if document["document_sha256"] != request["target_document_sha256"]:
            raise ValueError("request target document SHA differs")
        if not document["title"]["text"] and not request["target_selected_axes_text"]:
            raise ValueError("no current product title or selected row context")
    bindings, task_ids = defaultdict(list), set()
    request_lookup = {r["request_id"]: r for r in requests}
    for task in read(input_dir / "tasks.jsonl") if "tasks.jsonl" in names else []:
        if task["request_id"] not in request_lookup:
            raise ValueError("source task binds outside selected request subset")
        if task["task_id"] in task_ids:
            raise ValueError("duplicate source task ID")
        task_ids.add(task["task_id"])
        request = request_lookup[task["request_id"]]
        if (task["direction"] != request["direction"] or
                task["raw_condition"]["value"] != request["selected_value"] or
                task["option_values"] != request["option_values"]):
            raise ValueError("source task whole condition binding differs")
        source_axis = task["raw_condition"].get("axis_label", task["raw_condition"].get("axis_name"))
        if source_axis is not None and source_axis != request["axis_name"]:
            raise ValueError("source task raw axis binding differs")
        bindings[task["request_id"]].append(task)
    if "tasks.jsonl" in names and set(bindings) != set(request_lookup):
        raise ValueError("selected request is missing its source task binding")
    rank_summary = json.loads((ranking_dir / "summary.json").read_text(encoding="utf-8"))
    rank_names = ["freeze.json", "model-groups.jsonl", "ranking-windows.jsonl", "source-requests.jsonl", "source-documents.jsonl"]
    for name in rank_names:
        if rank_summary["output_sha256"].get(name) != sha(ranking_dir / name):
            raise ValueError("received ranking source SHA differs: " + name)
    rank_freeze = json.loads((ranking_dir / "freeze.json").read_text(encoding="utf-8"))
    if (rank_freeze.get("top_k") != 5 or rank_freeze.get("document_window_chars") != 300 or
            rank_freeze.get("overlap_chars") != 75):
        raise ValueError("received ranking is not the frozen top5/300char/75overlap task")
    original_requests = {r["request_id"]: r for r in read(ranking_dir / "source-requests.jsonl")}
    original_documents = {d["document_id"]: d for d in read(ranking_dir / "source-documents.jsonl")}
    for request in requests:
        original = original_requests.get(request["request_id"])
        if original is None or {k: original[k] for k in SEMANTIC_FIELDS} != validate_request(request):
            raise ValueError("subset request differs from original ranked request")
    for document in documents:
        if original_documents.get(document["document_id"]) != document:
            raise ValueError("subset document differs from original ranked document")
    ranked_groups = {g["group_id"]: g for g in read(ranking_dir / "model-groups.jsonl")}
    wanted_groups = {}
    for request in requests:
        payload = {k: request[k] for k in RANKING_FIELDS}
        key = digest(payload)
        group = ranked_groups.get(key)
        if group is None or group["payload"] != payload or request["request_id"] not in group["request_ids"]:
            raise ValueError("request has no exact received ranking group")
        wanted_groups[key] = by_document[request["target_document_id"]]
    selected_windows = defaultdict(list)
    selected_ids = set()
    for window in read(ranking_dir / "ranking-windows.jsonl"):
        key = window["group_id"]
        if key not in wanted_groups or not window["selected"]:
            continue
        identity = key, window["window_id"]
        if identity in selected_ids:
            raise ValueError("duplicate selected received window")
        selected_ids.add(identity)
        selected_windows[key].append(verify_window(window, wanted_groups[key]))
    for key, windows in selected_windows.items():
        if len(windows) > 5:
            raise ValueError("received ranking selected more than top5")
        windows.sort(key=lambda w: (-w["score"], w["derived_text_start"]))
    return requests, by_document, bindings, selected_windows, names, rank_names, rank_summary


def run(input_dir, ranking_dir, output_dir, variant, gliner_dir, threads=4, batch_size=8, confidence=.5):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if variant not in INSTRUCTIONS or confidence != .5 or threads < 1 or batch_size < 1:
        raise ValueError("only fixed task variants and .5 development threshold are allowed")
    requests, documents, bindings, rankings, source_names, rank_names, rank_summary = load_sources(input_dir, ranking_dir)
    pin = verify_snapshot(gliner_dir)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from gliner2 import AutoExtractor
    from gliner2.classification import Classifier, ClassificationConfig, ClassificationSchema
    torch.set_num_threads(threads)
    model = AutoExtractor.from_pretrained(str(gliner_dir), local_files_only=True).to("cpu").eval()
    if any(p.device.type != "cpu" for p in model.parameters()):
        raise RuntimeError("CPU-only model required")
    classifier = Classifier(model)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ["manifest.json", *source_names]:
        shutil.copyfile(input_dir / name, output_dir / ("source-" + name))
    ranking_copy = output_dir / "received-ranking"
    ranking_copy.mkdir()
    for name in ["summary.json", *rank_names]:
        shutil.copyfile(ranking_dir / name, ranking_copy / name)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for path in [Path(__file__), HERE / "trial_generic_gliner_choice_v1.py", HERE / "try_gliner.py"]:
        shutil.copyfile(path, code_dir / path.name)
    groups = {}
    for request in requests:
        payload = model_payload(request)
        key = digest(payload)
        group = groups.setdefault(key, {"group_id": key, "payload": payload, "request_ids": [],
                                        "received_ranking_group_id": digest({k: request[k] for k in RANKING_FIELDS})})
        group["request_ids"].append(request["request_id"])
    write_rows(output_dir / "model-groups.jsonl", groups.values())
    freeze = {"schema_version": "scoped-condition-gliner-v1", "variant": variant, "labels_read": False,
              "synthetic_data": False, "development_diagnostic": True, "independent_evaluation": False,
              "production_adoption_allowed": False, "scope_status": "unproved", "citations_are_semantic_proof": False,
              "selected_source_value_role": "complete hypothesis to verify; not the expected answer",
              "full_source_option_values_preserved": True, "new_retrieval_run": False,
              "received_top_k": 5, "confidence": confidence, "threshold_policy": "fixed .5 development diagnostic",
              "threads": threads, "batch_size": batch_size, "device": "cpu",
              "input_sha256": {n: sha(input_dir / n) for n in ["manifest.json", *source_names]},
              "received_ranking_sha256": {n: sha(ranking_dir / n) for n in ["summary.json", *rank_names]},
              "gliner_pin": pin, "received_reranker_pin": rank_summary["reranker_pin"],
              "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()},
              "loader_dependency_sha256": {inspect.getfile(c): sha(inspect.getfile(c))
                                           for c in (Classifier, ClassificationConfig, ClassificationSchema)},
              "precision": sorted({str(p.dtype) for p in model.parameters()}), "instruction": INSTRUCTIONS[variant],
              "schema_labels": dict(LABEL_TO_STATE), "request_count": len(requests), "model_group_count": len(groups),
              "input_length_policy": "actual encoder+schema <=512; oversized complete inputs skipped without truncation",
              "aggregation_policy": "high-confidence direct conflict vetoes support; otherwise direct support or unknown",
              "quote_policy": "exact current title/selected-row/full-description Unicode references; scope remains unproved"}
    write_json(output_dir / "freeze.json", freeze)
    print(canonical({"event": "frozen", "requests": len(requests), "groups": len(groups), "variant": variant}), flush=True)
    cache, group_results, statuses = {}, {}, Counter()
    window_count = 0
    start = time.perf_counter()
    with (output_dir / "classification-windows.jsonl").open("x", encoding="utf-8") as stream:
        for index, (key, group) in enumerate(groups.items(), 1):
            payload = group["payload"]
            document = documents[payload["target_document_id"]]
            refs = []
            for field, text in (("title", document["title"]["text"]), ("selected_axes", payload["target_selected_axes_text"])):
                if text:
                    refs.append(quote_ref(document, field, text))
            work = [(request_for(payload, document, variant), refs, "title", None)]
            work += [(request_for(payload, document, variant, w["text"]), [*refs, w["quote_ref"]], w["window_id"], w)
                     for w in rankings.get(group["received_ranking_group_id"], [])]
            records, missing = [], []
            for model_request, quote_refs, window_id, received in work:
                model_key = digest(model_request)
                record = {"group_id": key, "window_id": window_id, "model_input_sha256": model_key,
                          "request": model_request, "quote_refs": quote_refs, "scope_status": "unproved",
                          "production_adoption_allowed": False, "received_ranking_window": received}
                records.append(record)
                if model_key in cache:
                    record.update(cache[model_key], reused_identical_model_input=True)
                else:
                    missing.append((record, model_request))
            for offset in range(0, len(missing), batch_size):
                batch = missing[offset:offset + batch_size]
                schemas = [ClassificationSchema().single(r["schema_task_name"], r["schema_labels"], instruction=r["instruction"])
                           for _, r in batch]
                lengths = complete_input_token_counts([r for _, r in batch], schemas, classifier, model.processor)
                accepted = [(record, request) for (record, request), n in zip(batch, lengths, strict=True)
                            if length_status(record, n)]
                if accepted:
                    req = accepted[0][1]
                    schema = ClassificationSchema().single(req["schema_task_name"], req["schema_labels"], instruction=req["instruction"])
                    config = ClassificationConfig(batch_size=batch_size, max_len=None, max_candidates_per_task=64)
                    apply_model_results(accepted, lambda: classifier.batch_classify(
                        [r["text"] for _, r in accepted], schema, config=config))
                for record, req in batch:
                    cache[record["model_input_sha256"]] = {k: v for k, v in record.items() if k not in (
                        "group_id", "window_id", "model_input_sha256", "request", "quote_refs", "scope_status",
                        "production_adoption_allowed", "received_ranking_window")}
            for record in records:
                statuses[record["status"]] += 1
                stream.write(canonical(record) + "\n")
            stream.flush()
            window_count += len(records)
            group_results[key] = records
            if index % 10 == 0 or index == len(groups):
                print(canonical({"event": "groups_complete", "completed": index, "total": len(groups),
                                 "seconds": round(time.perf_counter() - start, 3)}), flush=True)
    predictions = []
    for request in requests:
        key = digest(model_payload(request))
        state, refs = aggregate(group_results[key], confidence)
        predictions.append({"request_id": request["request_id"], "request_sha256": request["request_sha256"],
                            "selected_value": request["selected_value"], "state": state,
                            "winning_value": request["selected_value"] if state == "support" else None,
                            "quote_refs": refs, "direction": request["direction"], "variant": variant,
                            "scope_status": "unproved", "production_adoption_allowed": False,
                            "model_group_id": key, "source_request": request,
                            "source_task_bindings": bindings[request["request_id"]]})
    write_rows(output_dir / "predictions.jsonl", predictions)
    summary = {**freeze, "inference_seconds": time.perf_counter() - start, "classification_window_count": window_count,
               "byte_identical_model_input_count": len(cache), "window_status_counts": dict(statuses),
               "prediction_states": dict(Counter(p["state"] for p in predictions)),
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    print(canonical({k: summary[k] for k in ("variant", "request_count", "prediction_states", "inference_seconds")}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--ranking-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--variant", choices=tuple(INSTRUCTIONS), required=True)
    parser.add_argument("--gliner-dir", type=Path, default=ROOT / ".deps/sku-gliner-model")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--confidence", type=float, default=.5)
    run(**vars(parser.parse_args()))
