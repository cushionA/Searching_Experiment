#!/usr/bin/env python3
"""Pinned CPU NLI for one raw condition and one verified quote.

This module does no SKU extraction, value normalization, axis correspondence,
row selection, or label evaluation. The caller owns condition/quote scope.
Only the pinned loader and probability mapping are reused from the CPU runner.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_MODEL = ROOT / ".deps/sku-nli-model"
THRESHOLD = 0.90
FP32_PARAMETER_COUNT = 111209475


def _loader():
    spec = importlib.util.spec_from_file_location(
        "generic_nli_pinned_loader", HERE / "trial_cpu_requirement_relations_v1.py")
    if spec is None or spec.loader is None:
        raise ImportError("pinned CPU NLI loader is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PINNED = _loader()
MODEL_ID = _PINNED.MODEL_ID
MODEL_REVISION = _PINNED.MODEL_REVISION
MAX_TOKENS = _PINNED.MAX_TOKENS
NLI_FILE_SHA256 = _PINNED.NLI_FILE_SHA256
relation_probabilities = _PINNED.relation_probabilities
exceeds_token_limit = _PINNED.exceeds_token_limit


def pair_key(premise: str, hypothesis: str) -> str:
    """Hash exact text and the model identity; never normalize the pair."""
    payload = [MODEL_ID, MODEL_REVISION, "fp32", premise, hypothesis]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def condition_pair(pair_id: str, condition: str, verified_quote: dict[str, Any],
                   fixed_row_meta: dict[str, Any]) -> dict[str, Any]:
    """Bind an opaque natural-language condition to a scoped literal quote."""
    quote = verified_quote.get("quote")
    span = verified_quote.get("span")
    scope = verified_quote.get("source_scope")
    if not isinstance(span, dict) or span.get("quote") != quote:
        raise ValueError("verified quote span must bind the exact quote")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("verified quote requires source_scope metadata")
    if not isinstance(fixed_row_meta, dict) or not fixed_row_meta:
        raise ValueError("condition requires fixed_row_meta")
    pair = {"id": pair_id, "premise": quote, "hypothesis": condition,
            "verified_quote": verified_quote, "fixed_row_meta": fixed_row_meta}
    _validate_pair(pair)
    return pair


def _validate_pair(pair: dict[str, Any]) -> None:
    for key in ("id", "premise", "hypothesis"):
        if not isinstance(pair.get(key), str) or not pair[key].strip():
            raise ValueError(f"pair requires nonempty raw {key}")
    binding = pair.get("verified_quote")
    if binding is not None:
        if not isinstance(binding, dict) or binding.get("quote") != pair["premise"]:
            raise ValueError("premise differs from its verified quote")
        if not isinstance(binding.get("span"), dict) or binding["span"].get("quote") != pair["premise"]:
            raise ValueError("verified quote span must bind the exact premise")


def _infer(unique_pairs: list[dict[str, Any]], model_dir: Path, threads: int,
           batch_size: int, on_ready: Callable) -> tuple:
    # Only raw text enters the permitted loader; no category templates or
    # legacy task validator are called. Its tokenizer never truncates inputs.
    tasks = [{"task_id": p["cache_key"], "case_id": p["id"], "au_row_key": "",
              "requirement": {"raw_condition": p["hypothesis"]},
              "hypothesis": p["hypothesis"],
              "evidence": {"evidence_id": p["cache_key"], "quote": p["premise"],
                           "span": {"quote": p["premise"]}},
              "source_scope": {"kind": "caller_bound_raw_pair"}} for p in unique_pairs]
    return _PINNED.run_nli(tasks, model_dir, threads, batch_size, "fp32", on_ready)


def _probabilities(record: dict[str, Any]) -> dict[str, float] | None:
    probabilities = record.get("probabilities")
    if probabilities is None:
        return None
    if not isinstance(probabilities, dict) or set(probabilities) != {"support", "conflict", "unknown"}:
        raise ValueError("NLI cache requires the frozen probability labels")
    values = {key: float(value) for key, value in probabilities.items()}
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in values.values()):
        raise ValueError("NLI probabilities must be finite values in [0, 1]")
    if not math.isclose(sum(values.values()), 1, abs_tol=1e-5):
        raise ValueError("NLI probabilities must sum to one")
    return values


def run_pairs(pairs: list[dict[str, Any]], model_dir: Path = DEFAULT_MODEL,
              threads: int = 4, batch_size: int = 16,
              cache: dict[str, dict[str, Any]] | None = None,
              on_ready: Callable | None = None) -> dict[str, Any]:
    """Return records, pins, and runtime; preserve input order and metadata.

    `cache` contains earlier output records keyed by `cache_key`. Exact repeated
    premise/hypothesis pairs share inference across rows, templates, and calls.
    Cached records must carry the same model/revision/precision identity.
    Inputs over 512 untruncated tokens and inference failures remain unknown.
    """
    if threads < 1 or batch_size < 1:
        raise ValueError("threads and batch_size must be positive")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    started = time.perf_counter()
    unique, seen_ids = {}, set()
    for pair in pairs:
        _validate_pair(pair)
        if pair["id"] in seen_ids:
            raise ValueError(f"duplicate pair id: {pair['id']}")
        seen_ids.add(pair["id"])
        key = pair_key(pair["premise"], pair["hypothesis"])
        unique.setdefault(key, {**pair, "cache_key": key})
    available, pending = {}, []
    for key in sorted(unique):
        cached = (cache or {}).get(key)
        if cached is None:
            pending.append(unique[key])
            continue
        identity = (cached.get("model_id"), cached.get("model_revision"), cached.get("precision"))
        if cached.get("cache_key") != key or identity != (MODEL_ID, MODEL_REVISION, "fp32"):
            raise ValueError("cached NLI model identity differs from the frozen pin")
        if pair_key(cached.get("premise", ""), cached.get("hypothesis", "")) != key:
            raise ValueError("cached NLI raw text differs from its cache key")
        _probabilities(cached)
        available[key] = cached
    pins = {"model_id": MODEL_ID, "revision": MODEL_REVISION,
            "parameter_count": FP32_PARAMETER_COUNT, "precision": "fp32", "device": "cpu",
            "file_sha256": NLI_FILE_SHA256,
            "provenance_manifest_sha256": _PINNED.NLI_PROVENANCE_MANIFEST_SHA256}
    runtime = {"model_load_seconds": 0.0, "inference_seconds": 0.0,
               "long_input_unknown_count": 0, "inference_error_count": 0, "precision": "fp32"}
    if pending:
        inferred, actual_pins, runtime = _infer(pending, Path(model_dir), threads, batch_size,
                                               on_ready or (lambda *_: None))
        if actual_pins.get("parameter_count") != FP32_PARAMETER_COUNT:
            raise RuntimeError("pinned FP32 NLI parameter count differs")
        pins.update(actual_pins)
        available.update({record["task_id"]: record for record in inferred})
    elif on_ready is not None:
        on_ready(pins, runtime)
    records, seen_keys = [], set()
    for pair in pairs:
        key = pair_key(pair["premise"], pair["hypothesis"])
        raw = available[key]
        probabilities = _probabilities(raw)
        overlong = exceeds_token_limit(raw["token_count_untruncated"]) if raw.get("token_count_untruncated") is not None else False
        winner = max(probabilities, key=probabilities.get) if probabilities is not None and not overlong else "unknown"
        confidence = probabilities[winner] if probabilities is not None and not overlong else None
        relation = winner if confidence is not None and confidence >= THRESHOLD else "unknown"
        records.append({**pair, "cache_key": key, "model_id": MODEL_ID,
                        "model_revision": MODEL_REVISION, "precision": "fp32",
                        "probabilities": probabilities, "predicted_class": winner,
                        "predicted_confidence": confidence, "relation": relation,
                        "confidence_threshold": THRESHOLD,
                        "token_count_untruncated": raw.get("token_count_untruncated"),
                        "latency_seconds": raw.get("latency_seconds", 0.0),
                        "note": raw.get("note"), "error_type": raw.get("error_type"),
                        "cache_reused": key in seen_keys or key in (cache or {})})
        seen_keys.add(key)
    runtime.update({"pair_count": len(pairs), "unique_pair_count": len(unique),
                    "inferred_unique_pair_count": len(pending),
                    "cached_unique_pair_count": len(unique) - len(pending),
                    "total_seconds": time.perf_counter() - started,
                    "threads": threads, "batch_size": batch_size, "device": "cpu"})
    return {"schema_version": "generic_model_nli_v1", "records": records,
            "pins": pins, "runtime": runtime, "labels_read": False}
