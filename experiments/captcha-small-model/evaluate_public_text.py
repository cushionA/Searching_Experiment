#!/usr/bin/env python3
"""Evaluate the existing CPU OCR adapter against a source-labeled image manifest.

The input JSON must have a top-level ``samples`` array. Each item has ``id``,
``path``, ``label``, ``source``, and ``sha256``. Paths are resolved relative to
the manifest. No model training or router inference is performed.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def timing(values: list[float]) -> dict[str, float | int | None]:
    return {
        "samples": len(values),
        "mean_ms": statistics.fmean(values) if values else None,
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
    }


def load_and_check_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("manifest must contain a non-empty samples array")
    checked: list[dict[str, Any]] = []
    ids: set[str] = set()
    image_hashes: set[str] = set()
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ValueError(f"samples[{index}] must be an object")
        missing = {"id", "path", "label", "source", "sha256"} - sample.keys()
        if missing:
            raise ValueError(f"samples[{index}] missing fields: {sorted(missing)}")
        sample_id = str(sample["id"])
        if not sample_id or sample_id in ids:
            raise ValueError(f"empty or duplicate sample id: {sample_id!r}")
        ids.add(sample_id)
        if not str(sample["source"]) or not isinstance(sample["label"], str):
            raise ValueError(f"samples[{index}] requires a source and string label")
        image_path = (path.parent / str(sample["path"])).resolve()
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        actual = hashlib.sha256(image_path.read_bytes()).hexdigest()
        expected = str(sample["sha256"]).lower()
        if len(expected) != 64 or actual != expected:
            raise ValueError(f"SHA-256 mismatch for {sample_id}: expected {expected}, got {actual}")
        if actual in image_hashes:
            raise ValueError(f"duplicate image SHA-256 in manifest: {actual}")
        image_hashes.add(actual)
        checked.append({**sample, "id": sample_id, "path": str(image_path), "sha256": actual})
    return manifest, checked


def summarize(rows: list[dict[str, Any]], manifest: dict[str, Any], load_seconds: float,
              providers: list[str]) -> dict[str, Any]:
    by_source: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_source[row["source"]].append(row)
    groups: dict[str, Any] = {}
    for source, group in sorted(by_source.items()):
        chars = sum(len(row["label"]) for row in group)
        groups[source] = {
            "samples": len(group),
            "literal_exact_match": sum(row["exact"] for row in group) / len(group),
            "case_insensitive_exact_match": sum(row["case_insensitive_exact"] for row in group) / len(group),
            "character_error_rate": sum(row["distance"] for row in group) / chars if chars else None,
            "latency": timing([row["latency_ms"] for row in group]),
        }
    return {
        "scope": "Source-labeled local text CAPTCHA images; OCR only, no model training, router evaluation, or live-site pass rate.",
        "manifest": str(manifest.get("manifest_path", "")),
        "dataset": manifest.get("dataset"),
        "provenance": manifest.get("provenance"),
        "license": manifest.get("license"),
        "samples": len(rows),
        "ocr_backend": "ddddocr",
        "providers": providers,
        "model_load_seconds": load_seconds,
        "by_source": groups,
        "checkpointed_samples": len(rows),
    }


def atomic_json(path: Path, value: Any) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def evaluate(manifest_path: Path, output: Path, router_root: Path | None,
             checkpoint_every: int = 100) -> tuple[Path, Path]:
    if checkpoint_every <= 0:
        raise ValueError("checkpoint-every must be positive")
    output = output.resolve()
    json_path = output.with_suffix(".json")
    jsonl_path = output.with_suffix(".jsonl")
    if output.exists() or json_path.exists() or jsonl_path.exists():
        raise FileExistsError(f"output already exists: {output} (or {json_path}/{jsonl_path})")

    manifest_path = manifest_path.resolve()
    manifest, samples = load_and_check_manifest(manifest_path)
    manifest["manifest_path"] = str(manifest_path)
    if router_root is not None:
        sys.path.insert(0, str(router_root.resolve()))
        from captcha_router.ocr import TextOCR
    else:
        from text_ocr import TextOCR

    ocr = TextOCR()
    results: list[dict[str, Any]] = []
    distances = 0
    chars = 0
    for i, sample in enumerate(samples, 1):
        with Image.open(sample["path"]) as image:
            start = time.perf_counter()
            answer = ocr.predict(image)
            latency_ms = (time.perf_counter() - start) * 1000
        label = sample["label"]
        distance = levenshtein(answer, label)
        distances += distance
        chars += len(label)
        results.append({
            "id": sample["id"], "path": sample["path"], "source": sample["source"],
            "sha256": sample["sha256"], "label": label, "answer": answer,
            "exact": answer == label,
            "case_insensitive_exact": answer.casefold() == label.casefold(),
            "distance": distance, "characters": len(label), "latency_ms": latency_ms,
        })
        if i % checkpoint_every == 0 or i == len(samples):
            atomic_jsonl(jsonl_path, results)
            summary = summarize(results, manifest, ocr.load_seconds, ocr.providers)
            summary["completed"] = i
            summary["character_error_rate"] = distances / chars if chars else None
            atomic_json(json_path, summary)
            print(f"checkpoint {i}/{len(samples)}", file=sys.stderr, flush=True)
    return json_path, jsonl_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="JSON manifest with source-labeled samples")
    parser.add_argument("--output", type=Path, required=True,
                        help="output base path; writes .json and .jsonl, refusing existing files")
    parser.add_argument("--router-root", type=Path,
                        help="Optional external checkout containing captcha_router.ocr; defaults to the bundled adapter")
    parser.add_argument("--checkpoint-every", type=int, default=100,
                        help="flush JSON and JSONL after this many samples (default: 100)")
    args = parser.parse_args()
    json_path, jsonl_path = evaluate(args.manifest, args.output, args.router_root,
                                    args.checkpoint_every)
    print(json.dumps({"summary": str(json_path), "predictions": str(jsonl_path)}))


if __name__ == "__main__":
    main()
