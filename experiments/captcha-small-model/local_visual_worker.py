#!/usr/bin/env python3
"""Persistent JSONL subprocess for offline local visual predictions."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_versions() -> dict:
    packages = {}
    for name in ("torch", "torchvision", "transformers", "Pillow", "numpy",
                 "scipy", "huggingface_hub", "safetensors", "tokenizers"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.platform(), "packages": packages}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--model-root", type=Path, required=True)
    result.add_argument("--image-root", type=Path, required=True)
    result.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    result.add_argument("--threads", type=int, default=2)
    result.add_argument("--clip-threshold", type=float, default=0.01)
    result.add_argument("--confidence-threshold", type=float, default=0.5)
    result.add_argument("--coverage-threshold", type=float, default=0.1)
    return result


def _deny_network(*args, **kwargs):
    raise OSError("network access is disabled in the local visual worker")


def _disable_network() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.create_connection = _deny_network
    socket.getaddrinfo = _deny_network


def _validate_request(request: object, image_root: Path) -> tuple[str, str, str, list[Path], int, int]:
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    request_id = request.get("id")
    if not isinstance(request_id, str) or len(request_id) > 200:
        raise ValueError("id must be a string of at most 200 characters")
    kind = request.get("type")
    if kind not in ("A", "B"):
        raise ValueError("type must be A or B")
    target = request.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string")
    images = request.get("images")
    expected = 9 if kind == "A" else 1
    if not isinstance(images, list) or len(images) != expected:
        raise ValueError(f"type {kind} requires exactly {expected} image(s)")
    root = image_root.resolve(strict=True)
    paths = []
    for value in images:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("image paths must be absolute strings")
        path = Path(value).resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"image is not a file: {value}")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"image path is outside image-root: {value}") from exc
        paths.append(path)
    rows, columns = request.get("rows", 4), request.get("columns", 4)
    for key, value in (("rows", rows), ("columns", columns)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} must be a positive integer")
    return request_id, kind, target, paths, rows, columns


def _validate_prediction(result: object, count: int) -> dict:
    if not isinstance(result, dict):
        raise ValueError("solver prediction must be an object")
    selection = result.get("selection")
    scores = result.get("scores")
    if not isinstance(selection, list):
        raise ValueError("prediction selection must be a list")
    if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= count for i in selection):
        raise ValueError("prediction selection contains an invalid index")
    if len(set(selection)) != len(selection):
        raise ValueError("prediction selection contains duplicate indices")
    if not isinstance(scores, list) or len(scores) != count:
        raise ValueError("prediction scores have an invalid length")
    for score in scores:
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError("prediction scores must be finite numbers")
    # Ensure all fields can be safely represented by strict JSON.
    json.dumps(result, allow_nan=False)
    return result


def main(argv=None, *, solver_factory=None, stdin=None, stdout=None) -> int:
    args = parser().parse_args(argv)
    source_hashes = {name: sha256(HERE / name) for name in ("local_visual.py", "local_visual_worker.py")}
    manifest_path = args.model_root / "manifest.json"
    try:
        manifest_hash = sha256(manifest_path)
        _disable_network()
        if solver_factory is None:
            from local_visual import LocalVisualSolver
            solver_factory = LocalVisualSolver
        stream_in = stdin if stdin is not None else sys.stdin
        stream_out = stdout if stdout is not None else sys.stdout
        with contextlib.redirect_stdout(sys.stderr):
            solver = solver_factory(args.model_root, args.device, args.threads, args.clip_threshold,
                                    args.confidence_threshold, args.coverage_threshold)
        print(json.dumps({"event": "ready", "model_manifest_sha256": manifest_hash,
                          "runtime": runtime_versions(), "source_sha256": source_hashes},
                         allow_nan=False), file=stream_out, flush=True)
    except Exception as exc:
        print(json.dumps({"event": "fatal", "error": {"type": type(exc).__name__, "message": str(exc)}},
                         ensure_ascii=False), file=stdout if stdout is not None else sys.stdout, flush=True)
        return 1

    for line in stream_in:
        request = None
        try:
            request = json.loads(line)
            request_id, kind, target, paths, rows, columns = _validate_request(request, args.image_root)
            count = 9 if kind == "A" else rows * columns
            with contextlib.redirect_stdout(sys.stderr):
                if kind == "A":
                    prediction = solver.predict_type_a(paths, target)
                else:
                    prediction = solver.predict_type_b(paths[0], target, rows, columns)
            prediction = _validate_prediction(prediction, count)
            prediction["input_image_sha256"] = [sha256(path) for path in paths]
            response = {"id": request_id, "ok": True, "prediction": prediction}
        except Exception as exc:
            response = {"id": request.get("id") if isinstance(request, dict) else None,
                        "ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
        print(json.dumps(response, ensure_ascii=False, allow_nan=False), file=stream_out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
