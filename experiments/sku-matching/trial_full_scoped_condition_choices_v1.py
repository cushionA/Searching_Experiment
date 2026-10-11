#!/usr/bin/env python3
"""Frozen full-cohort CPU choice diagnostic; no labels or category rules.

This adapts the previous error-subset task without changing its text, model
pins, or score fusion. Literal citations establish existence, not applicability.
Unknown means automatic drop at the separate final gate. This is development
evaluation on previously used real data, not an independent production test.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
import shutil
import threading
import time

import trial_generic_model_jev_v1 as JEV
import trial_generic_model_nli_v1 as NLI
from trial_scoped_condition_gliner_v1 import (
    HERE, ROOT, canonical, digest, quote_ref, read, sha, validate_request,
    write_json, write_rows,
)
from trial_scoped_condition_nli_choice_v1 import choose, pairs_for
from trial_scoped_condition_nli_v1 import HYPOTHESIS_TEMPLATE, verified_ref

UNKNOWN = "記載から特定できない（診断用）"
QUESTION = "現在の商品について、「{axis_name}」に該当する選択肢はどれですか。記載から特定できない場合は、その選択肢を選んでください。"
DESCRIPTION = "この商品の「{axis_name}」は「{value}」である。"
UNKNOWN_DESCRIPTION = "この商品の「{axis_name}」に該当する値は、商品名と選択行から特定できない。"
CODE_NAMES = (
    Path(__file__).name, "trial_generic_model_jev_v1.py",
    "trial_generic_model_nli_v1.py", "trial_cpu_requirement_relations_v1.py",
    "trial_scoped_condition_nli_choice_v1.py", "trial_scoped_condition_nli_v1.py",
    "trial_scoped_condition_gliner_v1.py", "trial_generic_gliner_choice_v1.py",
    "try_gliner.py",
)


def load_inputs(input_dir):
    """Validate immutable prep files directly, without loading old retrieval."""
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("labels_read") is True or manifest.get("labels_in_model_inputs") is True or
            (manifest.get("labels_read") is not False and manifest.get("labels_in_model_inputs") is not False)):
        raise ValueError("input producer must explicitly exclude labels")
    names = ("requests.jsonl", "documents.jsonl", "tasks.jsonl")
    for name in names:
        if manifest.get("output_sha256", {}).get(name) != sha(input_dir / name):
            raise ValueError("input file SHA256 differs: " + name)
    requests = read(input_dir / "requests.jsonl")
    documents = read(input_dir / "documents.jsonl")
    tasks = read(input_dir / "tasks.jsonl")
    lookup = {r["request_id"]: r for r in requests}
    by_document = {d["document_id"]: d for d in documents}
    if not requests or len(lookup) != len(requests) or len(by_document) != len(documents):
        raise ValueError("nonempty requests and unique request/document IDs required")
    for field, count in (("request_count", len(requests)), ("document_count", len(documents)), ("task_count", len(tasks))):
        if field in manifest and manifest[field] != count:
            raise ValueError("manifest count differs: " + field)
    for document in documents:
        if digest({k: v for k, v in document.items() if k != "document_sha256"}) != document["document_sha256"]:
            raise ValueError("complete target document semantic SHA differs")
        if not all(isinstance(document[field]["text"], str) for field in ("title", "description")):
            raise ValueError("raw title and full description strings required")
    for request in requests:
        validate_request(request)
        doc = by_document[request["target_document_id"]]
        if doc["document_sha256"] != request["target_document_sha256"]:
            raise ValueError("request target document SHA differs")
        if not doc["title"]["text"] and not request["target_selected_axes_text"]:
            raise ValueError("current product title or selected row is required")
        if UNKNOWN in request["option_values"]:
            raise ValueError("unknown control label collides with a source option")
    bindings, seen_tasks = defaultdict(list), set()
    for task in tasks:
        if task["task_id"] in seen_tasks or task["request_id"] not in lookup:
            raise ValueError("duplicate or unbound source task")
        seen_tasks.add(task["task_id"])
        request = lookup[task["request_id"]]
        raw_axis = task["raw_condition"].get("axis_label", task["raw_condition"].get("axis_name"))
        if (task["direction"] != request["direction"] or
                task["raw_condition"]["value"] != request["selected_value"] or
                task["option_values"] != request["option_values"] or
                (raw_axis is not None and raw_axis != request["axis_name"])):
            raise ValueError("whole source condition binding differs")
        bindings[task["request_id"]].append(task)
    if set(bindings) != set(lookup):
        raise ValueError("request is missing its source task binding")
    return requests, by_document, dict(bindings), manifest, names


def choice_requests(requests, documents):
    """Exact previous described-choice rendering, without the desired value."""
    return [{"id": r["request_id"], "question": QUESTION.format(axis_name=r["axis_name"]),
             "state": "現在の商品名：" + documents[r["target_document_id"]]["title"]["text"]
                      + "\n現在の選択行：\n" + r["target_selected_axes_text"],
             "choices": [{"label": v, "description": DESCRIPTION.format(axis_name=r["axis_name"], value=v)}
                         for v in r["option_values"]]
                        + [{"label": UNKNOWN, "description": UNKNOWN_DESCRIPTION.format(axis_name=r["axis_name"])}]}
            for r in requests]


def unique_inputs(inputs, key_for):
    """Keep first exact model input; metadata and desired values never key it."""
    unique = {}
    for row in inputs:
        unique.setdefault(key_for(row), row)
    return list(unique.values())


def current_refs(request, documents):
    doc = documents[request["target_document_id"]]
    return [quote_ref(doc, field, text) for field, text in (
        ("title", doc["title"]["text"]), ("selected_axes", request["target_selected_axes_text"])) if text]


def jev_eligibility(record):
    if record.get("status") != "ok":
        return False, "jev_error", False
    values = list(record["logits"].values())
    tie = sum(value == max(values) for value in values) != 1
    if tie:
        return False, "jev_tie", True
    if record["argmax_label"] == UNKNOWN:
        return False, "jev_unknown_control", False
    return True, None, False


def prediction_base(request, bindings):
    return {"request_id": request["request_id"], "request_sha256": request["request_sha256"],
            "selected_value": request["selected_value"], "direction": request["direction"],
            "source_request": request, "source_task_bindings": bindings[request["request_id"]],
            "scope_status": "unproved", "production_adoption_allowed": False}


def jev_prediction(request, record, documents, bindings):
    eligible, reason, tie = jev_eligibility(record)
    raw_winner = record.get("argmax_label")
    winner = None if raw_winner == UNKNOWN else raw_winner
    support = eligible and winner == request["selected_value"]
    return {**prediction_base(request, bindings), "state": "support" if support else "unknown",
            "winning_value": winner, "diagnostic_winning_value": raw_winner,
            "reason": reason or ("selected_value_selected" if support else "alternative_value_selected"),
            "quote_refs": current_refs(request, documents) if support else [],
            "candidate_record": record, "tie": tie, "variant": "jev-title-choice-described"}


def fusion_prediction(request, jev, nli, documents, bindings):
    """The previous equal-score fusion, including its single-option behavior."""
    valid, reason, _ = jev_eligibility(jev["candidate_record"])
    relative = nli.get("relative_entailment_scores")
    scores, winner = None, None
    if valid and relative is not None:
        probabilities = jev["candidate_record"]["probabilities"]
        if set(relative) != set(request["option_values"]) or set(probabilities) != set(request["option_values"]) | {UNKNOWN}:
            raise ValueError("fusion candidate sets differ from whole original options")
        scores = {v: .5 * relative[v] + .5 * probabilities[v] for v in request["option_values"]}
        leaders = [v for v, score in scores.items() if score == max(scores.values())]
        winner = leaders[0] if len(leaders) == 1 else None
        reason = "fused_score_tie" if winner is None else "equal_score_fusion"
    elif valid:
        reason = "nli_relative_scores_unavailable"
    support = winner == request["selected_value"]
    refs = current_refs(request, documents) if support else []
    if support and (not refs or not all(verified_ref(ref, request, documents) for ref in refs)):
        raise ValueError("support quote does not bind to the current literal title/row")
    return {**prediction_base(request, bindings), "state": "support" if support else "unknown",
            "winning_value": winner, "quote_refs": refs, "fused_scores": scores,
            "reason": reason, "source_jev_prediction": jev, "source_nli_prediction": nli,
            "variant": "equal-score-fusion-full"}


class Progress:
    """Emit a flushed heartbeat even while an existing backend scores a chunk."""
    def __init__(self, output_dir, interval=15):
        self.output_dir, self.interval = output_dir, interval
        self.started = time.perf_counter()
        self.state = {"stage": "preparing"}
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def emit(self, **state):
        with self.lock:
            self.state.update(state)
            row = {"event": "progress", **self.state, "elapsed_seconds": time.perf_counter() - self.started}
            temporary = self.output_dir / "progress.json.tmp"
            temporary.write_text(canonical(row) + "\n", encoding="utf-8")
            temporary.replace(self.output_dir / "progress.json")
            print(canonical(row), flush=True)

    def _heartbeat(self):
        while not self.stop.wait(self.interval):
            self.emit()

    def __enter__(self):
        self.thread.start()
        self.emit()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=1)


@contextmanager
def reuse_jev_loader():
    """Reuse verified loaded objects between chunks; keep backend scoring exact."""
    original, loaded = JEV._load_model, []

    def once(model_dir, threads):
        if not loaded:
            loaded.append((Path(model_dir).resolve(), threads, original(model_dir, threads)))
            return loaded[0][2]
        if (Path(model_dir).resolve(), threads) != loaded[0][:2]:
            raise ValueError("cached loader identity changed")
        tokenizer, model, torch, pins, _ = loaded[0][2]
        return tokenizer, model, torch, pins, {
            "file_verification_seconds": 0.0, "model_load_seconds": 0.0,
            "inference_seconds": 0.0, "tokenization_seconds": 0.0}

    JEV._load_model = once
    try:
        yield
    finally:
        JEV._load_model = original


@contextmanager
def reuse_nli_factories():
    """Keep the unchanged pinned NLI loader while memoizing its local factories.

    The single-threaded scoring run restores both constructors on exit. Every
    chunk still checks frozen files and uses the existing no-truncation path.
    No other model inference may run concurrently in this Python process.
    """
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    restored = []
    for factory in (AutoTokenizer, AutoModelForSequenceClassification):
        original = factory.from_pretrained
        descriptor = factory.__dict__["from_pretrained"]
        restored.append((factory, descriptor))
        factory.from_pretrained = staticmethod(memoized_factory(original))
    try:
        yield
    finally:
        for factory, descriptor in restored:
            factory.from_pretrained = descriptor


def memoized_factory(original):
    cache = {}

    def wrapper(*args, **kwargs):
        safe = {k: str(v) if k in {"torch_dtype", "dtype"} else v for k, v in kwargs.items()}
        key = (tuple(str(v) for v in args), canonical(safe))
        if key not in cache:
            # Preserve typed arguments passed to the verified local loader.
            cache[key] = original(*args, **kwargs)
        return cache[key]

    return wrapper


def score_nli_chunk(call, chunk, model_dir, threads, batch_size):
    """A tokenizer/scorer exception after pin verification becomes unknown."""
    ready = []

    def on_ready(pins, runtime):
        if (pins.get("model_id"), pins.get("revision"), pins.get("parameter_count")) != (
                NLI.MODEL_ID, NLI.MODEL_REVISION, NLI.FP32_PARAMETER_COUNT):
            raise ValueError("verified NLI model identity differs from frozen pin")
        ready.append((pins, runtime))

    try:
        return call(chunk, model_dir=model_dir, threads=threads, batch_size=batch_size, on_ready=on_ready)
    except Exception as error:
        if not ready:
            raise  # Never disguise acquisition/pin/load failures as model output.
        records = [{**p, "cache_key": NLI.pair_key(p["premise"], p["hypothesis"]),
                    "model_id": NLI.MODEL_ID, "model_revision": NLI.MODEL_REVISION,
                    "precision": "fp32", "probabilities": None, "relation": "unknown",
                    "token_count_untruncated": None, "error_type": type(error).__name__,
                    "note": "verified model chunk failed: " + str(error)} for p in chunk]
        return {"records": records, "pins": ready[0][0],
                "runtime": {**ready[0][1], "inference_error_count": len(records)}}


def _accumulate(total, runtime):
    for key in ("file_verification_seconds", "model_load_seconds", "inference_seconds", "tokenization_seconds", "total_seconds"):
        total[key] = total.get(key, 0.0) + runtime.get(key, 0.0)


def _append(handle, rows):
    for row in rows:
        handle.write(canonical(row) + "\n")
    handle.flush()


def run(input_dir, output_dir, jev_model_dir=JEV.DEFAULT_MODEL, nli_model_dir=NLI.DEFAULT_MODEL,
        threads=4, batch_size=16, jev_chunk_requests=16, nli_chunk_pairs=256,
        *, jev_runner=None, nli_runner=None):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if threads != 4 or batch_size != 16:
        raise ValueError("frozen CPU settings are four threads and batch size 16")
    if type(jev_chunk_requests) is not int or type(nli_chunk_pairs) is not int or min(jev_chunk_requests, nli_chunk_pairs) < 1:
        raise ValueError("positive integer chunk sizes required")
    requests, documents, bindings, manifest, names = load_inputs(input_dir)
    choices = choice_requests(requests, documents)
    jev_key = lambda r: JEV.request_key(r["question"], r["state"], r["choices"])
    unique_choices = unique_inputs(choices, jev_key)
    nli_pairs = pairs_for(requests, documents, bindings)
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in ("manifest.json", *names):
        shutil.copyfile(input_dir / name, output_dir / ("source-" + name))
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name in CODE_NAMES:
        shutil.copyfile(HERE / name, code_dir / name)
    write_rows(output_dir / "model-requests.jsonl", choices)
    write_rows(output_dir / "model-nli-pairs.jsonl", nli_pairs)
    freeze = {
        "schema_version": "full-scoped-condition-choices-v1", "labels_read": False,
        "errors_only": manifest.get("errors_only", False), "development_only": True, "independent_test": False,
        "production_adoption_allowed": False, "scope_status": "unproved", "synthetic_data": False,
        "body_model_input": False, "full_description_preserved_in_source_documents": True,
        "ranking_loaded": False, "new_retrieval_run": False, "source_selected_value_in_question_or_state": False,
        "all_original_options_preserved": True, "source_input_errors_only": manifest.get("errors_only", False),
        "request_count": len(requests), "unique_jev_request_count": len(unique_choices),
        "candidate_pair_count": sum(len(c["choices"]) for c in choices),
        "unique_jev_candidate_pair_count": sum(len(c["choices"]) for c in unique_choices),
        "nli_pair_count_before_skipping": len(nli_pairs), "unknown_control_label": UNKNOWN,
        "threads": threads, "batch_size": batch_size, "precision": "fp32", "device": "cpu",
        "jev_chunk_requests": jev_chunk_requests, "nli_chunk_pairs": nli_chunk_pairs,
        "question_template": QUESTION, "choice_description_template": DESCRIPTION,
        "unknown_description_template": UNKNOWN_DESCRIPTION, "hypothesis_template": HYPOTHESIS_TEMPLATE,
        "fusion_weights": {"nli_relative_entailment": .5, "jev_candidate_probability": .5},
        "fusion_policy": "JEV unknown/error/tie remains unknown; otherwise unique original-option winner by equal score mean supports only its exact source value",
        "single_option_policy": "preserve previous relative mass (1 if positive); fusion does not add an absolute-support check",
        "input_length_policy": "untruncated inputs; any missing, invalid, overlong or failed candidate yields unknown",
        "input_sha256": {n: sha(input_dir / n) for n in ("manifest.json", *names)},
        "code_sha256": {p.name: sha(p) for p in code_dir.iterdir()},
        "jev_pin": JEV._pins(),
        "nli_pin": {"model_id": NLI.MODEL_ID, "revision": NLI.MODEL_REVISION,
                    "parameter_count": NLI.FP32_PARAMETER_COUNT, "files_sha256": NLI.NLI_FILE_SHA256},
    }
    write_json(output_dir / "freeze.json", freeze)
    jev_call, nli_call = jev_runner or JEV.run_choices, nli_runner or NLI.run_pairs
    jev_cache, jev_runtime, nli_runtime = {}, {}, {}
    jev_pins, nli_pins = None, None
    with Progress(output_dir) as progress:
        progress.emit(stage="jev", total_groups=len(unique_choices), completed_groups=0)
        with (reuse_jev_loader() if jev_runner is None else nullcontext()), (output_dir / "jev-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
            for offset in range(0, len(unique_choices), jev_chunk_requests):
                chunk = unique_choices[offset:offset + jev_chunk_requests]
                result = jev_call(chunk, model_dir=jev_model_dir, threads=threads, batch_size=batch_size)
                if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                    raise ValueError("JEV candidate output order/identity differs")
                _append(handle, result["records"])
                jev_cache.update({r["cache_key"]: r for r in result["records"]})
                jev_pins = result["pins"]
                _accumulate(jev_runtime, result["runtime"])
                progress.emit(completed_groups=min(offset + len(chunk), len(unique_choices)))
        jev_records = jev_call(choices, model_dir=jev_model_dir, threads=threads, batch_size=batch_size,
                              cache=jev_cache)["records"]
        write_rows(output_dir / "jev-classification-options.jsonl", jev_records)
        jev_predictions = [jev_prediction(r, rec, documents, bindings) for r, rec in zip(requests, jev_records, strict=True)]
        write_rows(output_dir / "jev-predictions.jsonl", jev_predictions)
        eligibility = {r["request_id"]: jev_eligibility(rec) for r, rec in zip(requests, jev_records, strict=True)}
        eligible_pairs = [p for p in nli_pairs if eligibility[p["request_id"]][0]]
        unique_pairs = unique_inputs(eligible_pairs, lambda p: NLI.pair_key(p["premise"], p["hypothesis"]))
        write_rows(output_dir / "nli-skips.jsonl", [
            {"request_id": r["request_id"], "request_sha256": r["request_sha256"],
             "reason": eligibility[r["request_id"]][1], "source_request": r,
             "skipped_pair_count": len(r["option_values"])} for r in requests if not eligibility[r["request_id"]][0]])
        progress.emit(stage="nli", total_pairs=len(unique_pairs), completed_pairs=0)
        nli_cache = {}
        with (reuse_nli_factories() if nli_runner is None and unique_pairs else nullcontext()), (output_dir / "nli-unique-scores.jsonl").open("x", encoding="utf-8") as handle:
            for offset in range(0, len(unique_pairs), nli_chunk_pairs):
                chunk = unique_pairs[offset:offset + nli_chunk_pairs]
                result = score_nli_chunk(nli_call, chunk, nli_model_dir, threads, batch_size)
                if [r["id"] for r in result["records"]] != [r["id"] for r in chunk]:
                    raise ValueError("NLI candidate output order/identity differs")
                _append(handle, result["records"])
                nli_cache.update({r["cache_key"]: r for r in result["records"]})
                nli_pins = result["pins"]
                _accumulate(nli_runtime, result["runtime"])
                progress.emit(completed_pairs=min(offset + len(chunk), len(unique_pairs)))
        nli_records = nli_call(eligible_pairs, model_dir=nli_model_dir, threads=threads, batch_size=batch_size,
                               cache=nli_cache)["records"] if eligible_pairs else []
        write_rows(output_dir / "nli-classification-options.jsonl", nli_records)
        grouped = defaultdict(list)
        for record in nli_records:
            grouped[record["request_id"]].append(record)
        nli_predictions = []
        for request in requests:
            records = grouped[request["request_id"]]
            decision = choose(request["option_values"], request["selected_value"], records)
            if not eligibility[request["request_id"]][0]:
                decision["reason"] = "skipped_" + eligibility[request["request_id"]][1]
            nli_predictions.append({**prediction_base(request, bindings), **decision,
                                    "candidate_records": records,
                                    "quote_refs": current_refs(request, documents) if decision["state"] == "support" else [],
                                    "variant": "nli-title-choice"})
        write_rows(output_dir / "nli-predictions.jsonl", nli_predictions)
        fusion = [fusion_prediction(r, j, n, documents, bindings) for r, j, n in zip(requests, jev_predictions, nli_predictions, strict=True)]
        write_rows(output_dir / "fusion-predictions.jsonl", fusion)
        # Common name permits the existing finish CLI to consume the fusion.
        write_rows(output_dir / "predictions.jsonl", fusion)
        for name, expected in freeze["input_sha256"].items():
            if sha(input_dir / name) != expected:
                raise ValueError("source input changed during inference: " + name)
        for name, expected in freeze["code_sha256"].items():
            if sha(HERE / name) != expected:
                raise ValueError("source code changed during inference: " + name)
        progress.emit(stage="complete", request_count=len(fusion))
    summary = {**freeze, "jev_runtime": jev_runtime, "nli_runtime": nli_runtime,
               "verified_jev_pin": jev_pins, "verified_nli_pin": nli_pins,
               "nli_pair_count_after_skipping": len(eligible_pairs), "unique_nli_pair_count": len(unique_pairs),
               "nli_skipped_requests": dict(Counter(reason for valid, reason, _ in eligibility.values() if not valid)),
               "jev_prediction_states": dict(Counter(p["state"] for p in jev_predictions)),
               "fusion_prediction_states": dict(Counter(p["state"] for p in fusion)),
               "input_and_code_sha256_verified_after_inference": True,
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jev-model-dir", type=Path, default=JEV.DEFAULT_MODEL)
    parser.add_argument("--nli-model-dir", type=Path, default=NLI.DEFAULT_MODEL)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--jev-chunk-requests", type=int, default=16)
    parser.add_argument("--nli-chunk-pairs", type=int, default=256)
    run(**vars(parser.parse_args()))
