#!/usr/bin/env python3
"""Run one local, offline visual captcha prediction."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
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
    for name in ("torch", "torchvision", "transformers", "Pillow", "numpy", "scipy",
                 "huggingface_hub", "safetensors", "tokenizers"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "packages": packages,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--model-root", type=Path, required=True)
    result.add_argument("--type", choices=["A", "B"], required=True)
    result.add_argument("--target", required=True)
    result.add_argument("--images", type=Path, nargs="+", required=True)
    result.add_argument("--rows", type=int, default=4)
    result.add_argument("--columns", type=int, default=4)
    result.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    result.add_argument("--threads", type=int, default=2)
    result.add_argument("--clip-threshold", type=float, default=0.01)
    result.add_argument("--confidence-threshold", type=float, default=0.5)
    result.add_argument("--coverage-threshold", type=float, default=0.1)
    result.add_argument("--output", type=Path, help="new JSON output path; omit to print JSON")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    expected_images = 9 if args.type == "A" else 1
    if len(args.images) != expected_images:
        raise SystemExit(f"Type {args.type} requires exactly {expected_images} image(s)")
    for image_path in args.images:
        if not image_path.is_file():
            raise SystemExit(f"Image not found: {image_path}")
    if args.output and args.output.exists():
        raise SystemExit(f"Output already exists: {args.output}")
    if not args.target.strip():
        raise SystemExit("--target must not be empty")

    # Keep --help usable without importing model dependencies.
    from local_visual import LocalVisualSolver

    manifest_path = args.model_root / "manifest.json"
    manifest_sha = sha256(manifest_path)
    solver = LocalVisualSolver(
        args.model_root,
        args.device,
        args.threads,
        args.clip_threshold,
        args.confidence_threshold,
        args.coverage_threshold,
    )
    if args.type == "A":
        result = solver.predict_type_a(args.images, args.target)
    else:
        result = solver.predict_type_b(args.images[0], args.target, args.rows, args.columns)
    result.update(
        {
            "input_image_sha256": [sha256(path) for path in args.images],
            "model_manifest_sha256": manifest_sha,
            "thresholds": {
                "clip": args.clip_threshold,
                "confidence": args.confidence_threshold,
                "coverage": args.coverage_threshold,
            },
            "inference_api": False,
            "network_policy": "offline; local_files_only model loading",
            "runtime": runtime_versions(),
            "source_sha256": {name: sha256(HERE / name) for name in
                              ("local_visual.py", "recognize_local_visual.py")},
        }
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
