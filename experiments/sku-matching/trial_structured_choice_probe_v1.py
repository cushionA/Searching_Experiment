#!/usr/bin/env python3
"""Offline development probe of frozen real-page choice requests.

The input must be prepared before inference. No labels, domain dictionaries,
threshold fitting, or production SKU decisions are used here.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import time

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write(path, rows):
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(input_dir: Path, output_dir: Path, backend="jev", threads=4, batch_size=8):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest = json.loads((input_dir / "manifest.json").read_text())
    if sha(input_dir / "requests.jsonl") != manifest["output_sha256"]["requests.jsonl"]:
        raise ValueError("request manifest SHA mismatch")
    requests = read(input_dir / "requests.jsonl")
    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(input_dir / "requests.jsonl", output_dir / "requests.jsonl")
    code = output_dir / "code"
    code.mkdir()
    name = "trial_generic_model_jev_v1" if backend == "jev" else "trial_generic_model_nli_v1"
    paths = [Path(__file__), HERE / (name + ".py")]
    if backend == "nli":
        paths.append(HERE / "trial_cpu_requirement_relations_v1.py")
    for path in paths:
        shutil.copyfile(path, code / path.name)
    freeze = {"backend": backend, "labels_read": False, "production_eligible": False,
              "thresholds_for_diagnostics": [0.5, 0.7, 0.9], "primary_threshold": 0.9,
              "softmax_is_calibrated_probability": False,
              "input_sha256": {n: sha(input_dir / n) for n in ("requests.jsonl", "manifest.json")},
              "code_sha256": {p.name: sha(code / p.name) for p in paths}}
    (output_dir / "freeze.json").write_text(json.dumps(freeze, indent=2) + "\n")
    module = load(name)
    started = time.perf_counter()
    ready = lambda pins, runtime: print(json.dumps({"event": "model_ready", "model": pins["model_id"]}), flush=True)
    if backend == "jev":
        result = module.run_choices(requests, threads=threads, batch_size=batch_size, on_ready=ready)
    else:
        pairs = []
        for request in requests:
            for choice in request["choices"]:
                if choice["label"] == "unknown":
                    continue
                pairs.append({"id": request["id"] + ":" + choice["label"],
                              "premise": request["state"],
                              "hypothesis": f"この商品の「{request['provenance']['axis_name']}」は「{choice['description']}」です。",
                              "choice_label": choice["label"], "choice_description": choice["description"],
                              "request_id": request["id"], "provenance": request["provenance"]})
        write(output_dir / "pairs.jsonl", pairs)
        result = module.run_pairs(pairs, threads=threads, batch_size=batch_size, on_ready=ready)
    write(output_dir / "predictions.jsonl", result["records"])
    summary = {**freeze, "pins": result["pins"], "runtime": result["runtime"],
               "request_count": len(requests), "prediction_count": len(result["records"]),
               "status_counts": dict(Counter(r.get("status") for r in result["records"])),
               "elapsed_seconds": time.perf_counter() - started,
               "output_sha256": {p.name: sha(p) for p in output_dir.iterdir() if p.is_file()}}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("backend", "request_count", "prediction_count", "status_counts", "elapsed_seconds")}), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=("jev", "nli"), default="jev")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    run(**vars(parser.parse_args()))
