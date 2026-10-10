#!/usr/bin/env python3
"""Freeze and run caller-prepared relation inputs with pinned CPU backends.

The preparer supplies the exact pretrained question and candidate labels for
JEV, and the complete premise/hypothesis for NLI. This runner adds no SKU
templates, label lookup, fitted thresholds, or final SKU decisions.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import time
from typing import Any

HERE = Path(__file__).resolve().parent
BACKENDS = {"jev": "trial_generic_model_jev_v1", "nli": "trial_generic_model_nli_v1"}
SNAPSHOT_NAMES = (
    "trial_trained_relation_probe_v1.py",
    "trial_generic_model_jev_v1.py",
    "trial_generic_model_nli_v1.py",
    "trial_cpu_requirement_relations_v1.py",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"nonfinite JSON value is not permitted: {value}")


def _json(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_nonfinite)


def read(path: Path) -> list[dict[str, Any]]:
    return [_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    if spec is None or spec.loader is None:
        raise ImportError(f"pinned backend is unavailable: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_requests(requests: list[dict[str, Any]]) -> None:
    if not requests:
        raise ValueError("requests must contain at least one prepared input")
    seen = set()
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        for field in ("id", "question", "state", "premise", "hypothesis"):
            if not isinstance(request.get(field), str) or not request[field].strip():
                raise ValueError(f"request requires nonempty raw {field}")
        if request["id"] in seen:
            raise ValueError(f"duplicate request id: {request['id']}")
        seen.add(request["id"])
        if not isinstance(request.get("provenance"), dict) or not request["provenance"]:
            raise ValueError("request requires provenance with the fixed input binding")
        if "request_id" in request and request["request_id"] != request["id"]:
            raise ValueError("request_id must equal the source request id")
        choices = request.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("request requires ordered raw choices")
        labels = set()
        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError("choice must be an object")
            label = choice.get("label")
            if not isinstance(label, str) or not label.strip() or label in labels:
                raise ValueError("choice requires a unique nonempty raw label")
            if not isinstance(choice.get("description"), str):
                raise ValueError("choice requires a raw string description")
            labels.add(label)


def _validate_returned_metadata(requests: list[dict[str, Any]], records: Any) -> None:
    if not isinstance(records, list) or len(records) != len(requests):
        raise ValueError("backend prediction count differs from the source requests")
    for request, record in zip(requests, records, strict=True):
        if not isinstance(record, dict) or record.get("id") != request["id"]:
            raise ValueError("backend prediction ids/order differ from the source requests")
        for field, value in request.items():
            if record.get(field) != value:
                raise ValueError(f"backend changed the source request field: {field}")


def run(input_dir: Path, output_dir: Path, backend: str = "jev",
        threads: int = 2, batch_size: int = 8) -> dict[str, Any]:
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if backend not in BACKENDS:
        raise ValueError("backend must be jev or nli")
    if type(threads) is not int or type(batch_size) is not int or threads < 1 or batch_size < 1:
        raise ValueError("threads and batch_size must be positive integers")
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest_path, request_path = input_dir / "manifest.json", input_dir / "requests.jsonl"
    manifest = _json(manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("output_sha256", {}).get("requests.jsonl") if isinstance(manifest, dict) else None
    if not isinstance(expected, str) or sha(request_path) != expected:
        raise ValueError("request manifest SHA mismatch")
    input_hashes = {"requests.jsonl": expected, "manifest.json": sha(manifest_path)}
    requests = read(request_path)
    validate_requests(requests)

    # The output folder is always new. Freeze the actual copied bytes before
    # loading the model, then re-read those copies for the inference call.
    output_dir.mkdir(parents=True, exist_ok=False)
    for name in input_hashes:
        shutil.copyfile(input_dir / name, output_dir / name)
        if sha(output_dir / name) != input_hashes[name]:
            raise ValueError("input bytes changed while freezing")
    requests = read(output_dir / "requests.jsonl")
    code = output_dir / "code"
    code.mkdir()
    for name in SNAPSHOT_NAMES:
        shutil.copyfile(HERE / name, code / name)
    freeze = {
        "schema_version": "trained_relation_probe_freeze_v1",
        "backend": backend, "threads": threads, "batch_size": batch_size,
        "labels_read": False, "scope_proven": False, "production_eligible": False,
        "thresholds_for_diagnostics": [0.5, 0.7, 0.9], "primary_threshold": 0.9,
        "softmax_is_calibrated_probability": False,
        "question_candidates_modified_by_runner": False,
        "premise_hypothesis_modified_by_runner": False,
        "request_pair_binding": "one_pair_per_request_with_same_id",
        "input_sha256": input_hashes,
        "code_sha256": {name: sha(code / name) for name in SNAPSHOT_NAMES},
    }
    if backend == "nli":
        pairs = [{**deepcopy(request), "request_id": request["id"]} for request in requests]
        write(output_dir / "pairs.jsonl", pairs)
        freeze["pair_sha256"] = sha(output_dir / "pairs.jsonl")
    _write_json(output_dir / "freeze.json", freeze)

    module = load(BACKENDS[backend])
    started = time.perf_counter()

    def ready(pins: dict[str, Any], runtime: dict[str, Any]) -> None:
        print(json.dumps({"event": "model_ready", "model": pins["model_id"]}), flush=True)

    if backend == "jev":
        result = module.run_choices(deepcopy(requests), threads=threads,
                                    batch_size=batch_size, on_ready=ready)
    else:
        result = module.run_pairs(pairs, threads=threads, batch_size=batch_size, on_ready=ready)
    _validate_returned_metadata(requests, result["records"])
    write(output_dir / "predictions.jsonl", result["records"])
    summary = {
        **freeze, "schema_version": "trained_relation_probe_summary_v1",
        "pins": result["pins"], "runtime": result["runtime"],
        "request_count": len(requests), "prediction_count": len(result["records"]),
        "status_counts": dict(Counter(record.get("status", "backend_no_status") for record in result["records"])),
        "elapsed_seconds": time.perf_counter() - started,
        "output_sha256": {path.name: sha(path) for path in output_dir.iterdir() if path.is_file()},
    }
    _write_json(output_dir / "summary.json", summary)
    print(json.dumps({name: summary[name] for name in (
        "backend", "request_count", "prediction_count", "status_counts", "elapsed_seconds")}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=tuple(BACKENDS), default="jev")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    run(**vars(parser.parse_args()))
