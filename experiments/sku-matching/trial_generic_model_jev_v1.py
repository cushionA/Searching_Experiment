#!/usr/bin/env python3
"""Pinned, offline CPU cross-encoder scoring of caller-supplied choices.

The checkpoint produces one scalar per (question/state, candidate) pair.
Softmax is over the supplied candidates, with no fixed classification labels,
acceptance threshold, SKU rules, or interpretation of the winning label.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / ".deps/sku-modernbert-jev-model"
MODEL_ID = "argos1111/modernbert-ja-310m-jev"
MODEL_REVISION = "07cda23579443e7a33c0f474114279fa032340d6"
FORMAT_VERSION = "modernbert-jev/1"
CONTEXT_TEMPLATE = "質問: {question}\n状況: {state}"
CANDIDATE_TEMPLATE = "{label} — {description}"
EMPTY_DESCRIPTION_CANDIDATE_TEMPLATE = "{label}"
EMPTY_DESCRIPTION_RENDERING_VERSION = "modernbert-jev/1/empty-description-label-only-v1"
MAX_TOKENS = 512
FP32_PARAMETER_COUNT = 315203329
JEV_FILE_SHA256 = {
    "README.md": "fc2902bdae110c0ce565721a53f3076c587c47a193fd75e28c25d0ae55bfe363",
    "config.json": "22d564d0e22fb9f481da4b5a6a2602db35e40878596e4c69fdbaa4347c6b6a64",
    "jev_modernbert.json": "10bb3638b3177f805529dee2af68eef47ab52689e0194032438eab551ef6aec4",
    "tokenizer_config.json": "4e2b5f5a5159b1b0b5b75e6d099cf96f9b0399b9037685275ffcd4b18f02d759",
    "tokenizer.json": "eab16bb632cc4eb35ad4f2664ae8e102cb0dfa1d49d6fc9e49541bc54616cb02",
    "model.safetensors": "85ede5652889f5e4e114da5c6cdb923c31ae1f08cb0f5d0008a0631d6a4541d0",
}


def render_context(question: str, state: str) -> str:
    return CONTEXT_TEMPLATE.format(question=question, state=state)


def render_candidate(choice: dict[str, Any]) -> str:
    if choice["description"] == "":
        return EMPTY_DESCRIPTION_CANDIDATE_TEMPLATE.format(label=choice["label"])
    return CANDIDATE_TEMPLATE.format(label=choice["label"], description=choice["description"])


def request_key(question: str, state: str, choices: list[dict[str, Any]]) -> str:
    """Hash exact rendered inputs and their ordering, excluding caller metadata."""
    payload = [MODEL_ID, MODEL_REVISION, "fp32", FORMAT_VERSION, MAX_TOKENS,
               CONTEXT_TEMPLATE, CANDIDATE_TEMPLATE, question, state,
               [[choice["label"], choice["description"]] for choice in choices]]
    # Retain existing keys for nonempty descriptions; the formerly rendered
    # "label — " must never share a cache entry with the trained bare label.
    if any(choice["description"] == "" for choice in choices):
        payload.append(EMPTY_DESCRIPTION_RENDERING_VERSION)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_request(request: Any) -> None:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    for field in ("id", "question", "state"):
        if not isinstance(request.get(field), str) or not request[field].strip():
            raise ValueError(f"request requires nonempty raw {field}")
    choices = request.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("request requires a nonempty choices list")
    labels = set()
    for choice in choices:
        if not isinstance(choice, dict):
            raise ValueError("each choice must be an object")
        label = choice.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("each choice requires a nonempty raw label")
        if not isinstance(choice.get("description"), str):
            raise ValueError("each choice requires a raw string description")
        if label in labels:
            raise ValueError(f"duplicate choice label: {label}")
        labels.add(label)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pins() -> dict[str, Any]:
    return {"model_id": MODEL_ID, "revision": MODEL_REVISION,
            "parameter_count": FP32_PARAMETER_COUNT, "num_labels": 1,
            "precision": "fp32", "device": "cpu", "max_tokens": MAX_TOKENS,
            "format_version": FORMAT_VERSION, "context_template": CONTEXT_TEMPLATE,
            "candidate_template": CANDIDATE_TEMPLATE,
            "empty_description_candidate_template": EMPTY_DESCRIPTION_CANDIDATE_TEMPLATE,
            "file_sha256": dict(JEV_FILE_SHA256)}


def verify_model_files(model_dir: Path) -> dict[str, Any]:
    """Verify a root-created acquisition manifest and every frozen file hash.

    provenance.json must have schema_version=generic_model_jev_provenance_v1,
    model_id, revision, format_version, and files mapping each frozen filename
    to {sha256, source_url}. URLs must be the exact pinned HF /resolve/ URLs.
    Additional acquisition metadata is permitted; no network is used here.
    """
    model_dir = Path(model_dir)
    manifest_path = model_dir / "provenance.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = (manifest.get("schema_version"), manifest.get("model_id"),
                manifest.get("revision"), manifest.get("format_version"))
    if identity != ("generic_model_jev_provenance_v1", MODEL_ID, MODEL_REVISION, FORMAT_VERSION):
        raise RuntimeError("JEV provenance identity differs from the frozen pin")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(JEV_FILE_SHA256):
        raise RuntimeError("JEV provenance must describe exactly the pinned files")
    verified = {}
    for name, expected in JEV_FILE_SHA256.items():
        entry = files[name]
        source_url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}"
        if not isinstance(entry, dict) or entry.get("sha256") != expected or entry.get("source_url") != source_url:
            raise RuntimeError(f"JEV provenance file pin differs: {name}")
        path = model_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"pinned JEV file missing: {path}")
        if _sha256(path) != expected:
            raise RuntimeError(f"pinned JEV file SHA256 mismatch: {name}")
        verified[name] = {"sha256": expected, "size_bytes": path.stat().st_size,
                          "source_url": source_url}
    # Unpinned optional tokenizer files could otherwise change the inputs.
    for name in ("added_tokens.json", "special_tokens_map.json", "tokenizer.model",
                 "spiece.model", "vocab.txt", "vocab.json", "merges.txt"):
        if (model_dir / name).exists():
            raise RuntimeError(f"unpinned optional tokenizer file is present: {name}")
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if (config.get("model_type") != "modernbert"
            or config.get("architectures") != ["ModernBertForSequenceClassification"]
            or config.get("id2label") != {"0": "LABEL_0"}):
        raise RuntimeError("pinned JEV config must have one scalar classification logit")
    metadata = json.loads((model_dir / "jev_modernbert.json").read_text(encoding="utf-8"))
    if metadata.get("format_version") != FORMAT_VERSION or metadata.get("max_length") != MAX_TOKENS:
        raise RuntimeError("pinned JEV rendering or training token limit differs")
    return {**_pins(), "files": verified,
            "provenance_manifest_sha256": _sha256(manifest_path)}


def _load_model(model_dir: Path, threads: int) -> tuple[Any, Any, Any, dict[str, Any], dict[str, Any]]:
    verified_at = time.perf_counter()
    pins = verify_model_files(model_dir)
    verify_seconds = time.perf_counter() - verified_at
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(threads)
    started = time.perf_counter()
    options = {"local_files_only": True, "trust_remote_code": False, "revision": MODEL_REVISION}
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), **options)
    model = AutoModelForSequenceClassification.from_pretrained(
        str(model_dir), **options, use_safetensors=True, dtype=torch.float32,
        attn_implementation="eager").to("cpu").eval()
    if model.config.num_labels != 1:
        raise RuntimeError("pinned JEV model must produce exactly one scalar per candidate")
    parameters = list(model.parameters())
    if sum(parameter.numel() for parameter in parameters) != FP32_PARAMETER_COUNT:
        raise RuntimeError("pinned JEV FP32 parameter count differs")
    if any(parameter.device.type != "cpu" or parameter.dtype != torch.float32 for parameter in parameters):
        raise RuntimeError("pinned JEV parameters must all be FP32 on CPU")
    return tokenizer, model, torch, pins, {
        "file_verification_seconds": verify_seconds,
        "model_load_seconds": time.perf_counter() - started,
        "inference_seconds": 0.0, "tokenization_seconds": 0.0,
    }


def _empty_scores(status: str, note: str | None = None,
                  error_type: str | None = None) -> dict[str, Any]:
    return {"status": status, "logits": None, "probabilities": None,
            "argmax_label": None, "argmax_index": None, "argmax_probability": None,
            "token_counts": None, "latency_seconds": 0.0,
            "note": note, "error_type": error_type}


def _softmax(logits: list[float]) -> list[float]:
    if not logits or any(not math.isfinite(value) for value in logits):
        raise ValueError("candidate logits must be finite scalars")
    largest = max(logits)
    exps = [math.exp(value - largest) for value in logits]
    total = sum(exps)
    return [value / total for value in exps]


def _infer(requests: list[dict[str, Any]], model_dir: Path, threads: int,
           batch_size: int, on_ready: Callable) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    tokenizer, model, torch, pins, runtime = _load_model(model_dir, threads)
    on_ready(pins, runtime)
    records, prepared, scores = {}, [], {}
    for request in requests:
        key = request["cache_key"]
        counts, encodings = {}, []
        tokenization_started = time.perf_counter()
        try:
            context = render_context(request["question"], request["state"])
            for choice in request["choices"]:
                encoding = tokenizer(context, render_candidate(choice), truncation=False,
                                     padding=False, add_special_tokens=True)
                counts[choice["label"]] = len(encoding["input_ids"])
                encodings.append(encoding)
            if any(count > MAX_TOKENS for count in counts.values()):
                records[key] = {**_empty_scores("input_too_long", f"choice pair exceeds {MAX_TOKENS} tokens; no truncation"),
                                "token_counts": counts}
            else:
                scores[key] = {"values": [None] * len(encodings), "latency_seconds": 0.0,
                               "token_counts": counts, "request": request}
                prepared.extend((key, index, encoding) for index, encoding in enumerate(encodings))
        except Exception as exc:
            records[key] = {**_empty_scores("tokenization_error", str(exc), type(exc).__name__),
                            "token_counts": counts or None}
        runtime["tokenization_seconds"] += time.perf_counter() - tokenization_started
    for offset in range(0, len(prepared), batch_size):
        batch = [item for item in prepared[offset:offset + batch_size] if item[0] not in records]
        if not batch:
            continue
        started = time.perf_counter()
        try:
            features = tokenizer.pad([item[2] for item in batch], padding=True, return_tensors="pt")
            with torch.inference_mode():
                rows = model(**{name: value.cpu() for name, value in features.items()}).logits.detach().cpu().tolist()
            if len(rows) != len(batch):
                raise ValueError("JEV output row count differs from candidate batch")
            for row in rows:
                if not isinstance(row, list) or len(row) != 1 or not math.isfinite(float(row[0])):
                    raise ValueError("JEV output must have one finite scalar per candidate")
            elapsed = time.perf_counter() - started
            for (key, index, _), row in zip(batch, rows, strict=True):
                scores[key]["values"][index] = float(row[0])
                scores[key]["latency_seconds"] += elapsed / len(batch)
        except Exception as exc:
            for key, _, _ in batch:
                records[key] = {**_empty_scores("inference_error", str(exc), type(exc).__name__),
                                "token_counts": scores[key]["token_counts"]}
        runtime["inference_seconds"] += time.perf_counter() - started
    for key, score in scores.items():
        if key in records:
            continue
        values = score["values"]
        if any(value is None for value in values):
            records[key] = {**_empty_scores("inference_error", "candidate scoring incomplete", "RuntimeError"),
                            "token_counts": score["token_counts"]}
            continue
        probabilities = _softmax(values)
        labels = [choice["label"] for choice in score["request"]["choices"]]
        winner = max(range(len(values)), key=lambda index: values[index])
        records[key] = {"status": "ok", "logits": dict(zip(labels, values, strict=True)),
                        "probabilities": dict(zip(labels, probabilities, strict=True)),
                        "argmax_label": labels[winner], "argmax_index": winner,
                        "argmax_probability": probabilities[winner],
                        "token_counts": score["token_counts"],
                        "latency_seconds": score["latency_seconds"], "note": None, "error_type": None}
    return [{"cache_key": request["cache_key"], **records[request["cache_key"]]} for request in requests], pins, runtime


def _validate_scores(record: dict[str, Any], request: dict[str, Any]) -> None:
    labels = [choice["label"] for choice in request["choices"]]
    status = record.get("status")
    if status not in {"ok", "input_too_long", "tokenization_error", "inference_error"}:
        raise ValueError("choice score status is not a supported inference outcome")
    counts = record.get("token_counts")
    if counts is not None and (not isinstance(counts, dict) or any(
            label not in labels or type(count) is not int or count < 0 for label, count in counts.items())):
        raise ValueError("choice token counts must be nonnegative integers keyed by supplied labels")
    if status != "ok":
        if any(record.get(field) is not None for field in ("probabilities", "logits", "argmax_label", "argmax_index", "argmax_probability")):
            raise ValueError("failed requests cannot have scores or an argmax")
        if status in {"input_too_long", "inference_error"}:
            if not isinstance(counts, dict) or list(counts) != labels:
                raise ValueError("failed scored requests require all candidate token counts")
            overlong = any(count > MAX_TOKENS for count in counts.values())
            if overlong != (status == "input_too_long"):
                raise ValueError("failed request status differs from its token-limit outcome")
        elif counts is not None and list(counts) != labels[:len(counts)]:
            raise ValueError("partial tokenization counts must follow supplied candidate order")
        return
    if not isinstance(counts, dict) or list(counts) != labels or any(count > MAX_TOKENS for count in counts.values()):
        raise ValueError("successful choices require all untruncated token counts within 512")
    logits, probabilities = record.get("logits"), record.get("probabilities")
    if any(not isinstance(mapping, dict) or list(mapping) != labels for mapping in (logits, probabilities)):
        raise ValueError("choice score ordering differs from the supplied labels")
    values = [float(logits[label]) for label in labels]
    expected = _softmax(values)
    actual = [float(probabilities[label]) for label in labels]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in actual) or not math.isclose(sum(actual), 1, abs_tol=1e-8):
        raise ValueError("choice probabilities must be finite and sum to one")
    if any(not math.isclose(a, b, abs_tol=1e-8, rel_tol=1e-7) for a, b in zip(actual, expected, strict=True)):
        raise ValueError("choice probabilities differ from the scalar-logit softmax")
    winner = max(range(len(values)), key=lambda index: values[index])
    if (record.get("argmax_index") != winner or record.get("argmax_label") != labels[winner]
            or not math.isclose(float(record.get("argmax_probability", -1)), expected[winner], abs_tol=1e-8)):
        raise ValueError("choice argmax differs from supplied scalar logits")


def run_choices(requests: list[dict[str, Any]], model_dir: Path = DEFAULT_MODEL,
                threads: int = 4, batch_size: int = 8,
                cache: dict[str, dict[str, Any]] | None = None,
                on_ready: Callable | None = None) -> dict[str, Any]:
    """Score exact raw choices, preserving request order, text, and metadata.

    Equal question/state/ordered label-description inputs share inference;
    cached earlier output records may be reused across calls. No truncation or
    probability is permitted if any candidate pair exceeds 512 tokens.
    Invalid individual requests and inference failures produce error records.
    Duplicate IDs, invalid cache records, and unverified model files raise.
    """
    if type(threads) is not int or type(batch_size) is not int or threads < 1 or batch_size < 1:
        raise ValueError("threads and batch_size must be positive integers")
    if not isinstance(requests, list):
        raise ValueError("requests must be a list")
    if cache is not None and not isinstance(cache, dict):
        raise ValueError("cache must be a dictionary keyed by cache_key")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    started = time.perf_counter()
    unique, inputs, seen_ids = {}, [], set()
    for request in requests:
        raw = deepcopy(request)
        if isinstance(raw, dict) and isinstance(raw.get("id"), str):
            if raw["id"] in seen_ids:
                raise ValueError(f"duplicate request id: {raw['id']}")
            seen_ids.add(raw["id"])
        try:
            _validate_request(raw)
            key = request_key(raw["question"], raw["state"], raw["choices"])
            unique.setdefault(key, {**raw, "cache_key": key})
            inputs.append((raw, key, None))
        except ValueError as exc:
            inputs.append((raw, None, _empty_scores("invalid_request", str(exc), "ValueError")))
    available, pending, cached_pins = {}, [], None
    for key, request in unique.items():
        cached = (cache or {}).get(key)
        if cached is None:
            pending.append(request)
            continue
        if not isinstance(cached, dict) or (cached.get("cache_key"), cached.get("model_id"), cached.get("model_revision"),
                                           cached.get("precision"), cached.get("format_version")) != (key, MODEL_ID, MODEL_REVISION, "fp32", FORMAT_VERSION):
            raise ValueError("cached JEV model or rendering identity differs from frozen pin")
        try:
            cached_key = request_key(cached["question"], cached["state"], cached["choices"])
        except (KeyError, TypeError) as exc:
            raise ValueError("cached JEV raw text is missing") from exc
        if cached_key != key:
            raise ValueError("cached JEV raw text differs from its cache key")
        _validate_scores(cached, request)
        if cached.get("pins") != _pins() and not _same_pins(cached.get("pins")):
            raise ValueError("cached JEV file or parameter pins differ from frozen pin")
        cached_pins = cached.get("pins")
        available[key] = deepcopy(cached)
    pins = {**(deepcopy(cached_pins) if cached_pins else {}), **_pins()}
    runtime = {"file_verification_seconds": 0.0, "model_load_seconds": 0.0,
               "inference_seconds": 0.0, "tokenization_seconds": 0.0}
    if pending:
        inferred, actual_pins, actual_runtime = _infer(pending, Path(model_dir), threads, batch_size,
                                                      on_ready or (lambda *_: None))
        if not _same_pins(actual_pins):
            raise RuntimeError("inferred JEV file or parameter pins differ from frozen pin")
        pins = actual_pins
        runtime.update(actual_runtime)
        available.update({record["cache_key"]: record for record in inferred})
    elif on_ready is not None:
        on_ready(pins, runtime)
    records, seen_keys = [], set()
    score_fields = tuple(_empty_scores("ok"))
    for request, key, invalid in inputs:
        score = invalid if invalid is not None else available[key]
        if invalid is None:
            _validate_scores(score, request)
        raw = request if isinstance(request, dict) else {"input": request}
        records.append({**raw, "request": deepcopy(request), "cache_key": key,
                        "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
                        "precision": "fp32", "format_version": FORMAT_VERSION,
                        **{field: deepcopy(score.get(field)) for field in score_fields},
                        "pins": deepcopy(pins),
                        "cache_reused": key is not None and (key in seen_keys or key in (cache or {}))})
        seen_keys.add(key)
    runtime.update({"request_count": len(requests), "unique_request_count": len(unique),
                    "inferred_unique_request_count": len(pending),
                    "cached_unique_request_count": len(unique) - len(pending),
                    "input_too_long_count": sum(record["status"] == "input_too_long" for record in records),
                    "error_count": sum(record["status"] != "ok" for record in records),
                    "total_seconds": time.perf_counter() - started,
                    "threads": threads, "batch_size": batch_size, "device": "cpu", "precision": "fp32"})
    return {"schema_version": "generic_model_jev_v1", "records": records,
            "pins": pins, "runtime": runtime}


def _same_pins(pins: Any) -> bool:
    if not isinstance(pins, dict):
        return False
    expected = _pins()
    # Earlier nonempty-description caches retain the same text and key. Old
    # empty-description keys differ, so they cannot reach cache validation.
    if "empty_description_candidate_template" not in pins:
        expected.pop("empty_description_candidate_template")
    return all(pins.get(field) == value for field, value in expected.items())
