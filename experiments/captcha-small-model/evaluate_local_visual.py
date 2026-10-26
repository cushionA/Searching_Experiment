#!/usr/bin/env python3
"""Evaluate the local visual solver on hash-verified public board annotations."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


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


def read_json(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _validate_case_metadata(case: dict, identifiers: set[str]) -> dict:
    if not isinstance(case, dict):
        raise ValueError("case must be an object")
    identifier = case.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("case id missing")
    if identifier in identifiers:
        raise ValueError(f"duplicate case id: {identifier}")
    identifiers.add(identifier)

    source_type = case.get("source_type")
    if source_type not in ("Type A", "Type B"):
        raise ValueError(f"invalid source_type in {identifier}")
    tile_count = 9 if source_type == "Type A" else 16
    target = case.get("target")
    prompt_target = case.get("original_keyword") or target
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"{identifier}: missing canonical target")
    if not isinstance(prompt_target, str) or not prompt_target.strip():
        raise ValueError(f"{identifier}: missing prompt target")

    has_correct = "correct_answers" in case
    has_reference = "reference_selection" in case
    if not has_correct and not has_reference:
        raise ValueError(f"{identifier}: missing reference selection")
    correct = case.get("correct_answers")
    reference = case.get("reference_selection")
    for label, value in (("correct_answers", correct), ("reference_selection", reference)):
        if value is None and ((label == "correct_answers" and not has_correct) or (label == "reference_selection" and not has_reference)):
            continue
        if not isinstance(value, list):
            raise ValueError(f"{identifier}: {label} must be a list")
        if any(type(index) is not int or index < 0 or index >= tile_count for index in value):
            raise ValueError(f"{identifier}: invalid {label}")
        if len(value) != len(set(value)):
            raise ValueError(f"{identifier}: duplicate index in {label}")
    if has_correct and has_reference and sorted(correct) != sorted(reference):
        raise ValueError(f"{identifier}: correct_answers and reference_selection disagree")
    answer = correct if has_correct else reference
    return {
        "id": identifier,
        "source_type": source_type,
        "tile_count": tile_count,
        "canonical_target": target,
        "prompt_target": prompt_target,
        "reference_selection": sorted(answer),
        "raw_case": case,
    }


def _verify_tiles(case: dict, image_root: Path) -> tuple[list[Path], list[str]]:
    identifier = case["id"]
    raw = case["raw_case"]
    paths, hashes = raw.get("tile_paths"), raw.get("tile_sha256")
    count = case["tile_count"]
    if not isinstance(paths, list) or len(paths) != count:
        raise ValueError(f"{identifier}: expected {count} tile paths")
    if not isinstance(hashes, list) or len(hashes) != count:
        raise ValueError(f"{identifier}: expected {count} tile hashes")
    verified_paths = []
    verified_hashes = []
    resolved_root = image_root.resolve()
    for relative, expected in zip(paths, hashes, strict=True):
        if (
            not isinstance(relative, str)
            or not relative
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
        ):
            raise ValueError(f"{identifier}: unsafe tile reference")
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in expected)
        ):
            raise ValueError(f"{identifier}: invalid tile hash")
        tile_path = (image_root / relative).resolve()
        try:
            tile_path.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"{identifier}: tile escapes image root") from exc
        if not tile_path.is_file():
            raise FileNotFoundError(tile_path)
        actual = digest(tile_path)
        if actual.lower() != expected.lower():
            raise ValueError(f"{identifier}: tile SHA-256 mismatch: {relative}")
        verified_paths.append(tile_path)
        verified_hashes.append(actual)
    return verified_paths, verified_hashes


def select_cases(manifest: dict, kind: str, limit: int | None) -> list[dict]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("manifest must contain cases array")
    identifiers: set[str] = set()
    selected = []
    for case in manifest["cases"]:
        metadata = _validate_case_metadata(case, identifiers)
        if kind != "all" and metadata["source_type"] != f"Type {kind}":
            continue
        selected.append(metadata)
    if limit is not None:
        selected = selected[:limit]
    return selected


def aggregate(rows: list[dict]) -> dict:
    def grouped(key_function):
        groups = defaultdict(list)
        for row in rows:
            groups[key_function(row)].append(row)
        result = {}
        for key, items in sorted(groups.items(), key=lambda pair: str(pair[0])):
            exact_matches = sum(item["exact_match"] for item in items)
            result[str(key)] = {
                "count": len(items),
                "exact_matches": exact_matches,
                "exact_match_rate": exact_matches / len(items),
            }
        return result

    exact_matches = sum(row["exact_match"] for row in rows)
    return {
        "count": len(rows),
        "exact_matches": exact_matches,
        "exact_match_rate": exact_matches / len(rows) if rows else None,
        "by_type": grouped(lambda row: row["challenge_type"]),
        "by_target": grouped(lambda row: row["canonical_target"]),
    }


def validate_prediction(prediction: dict, tile_count: int, case_id: str) -> tuple[list[int], list[float], float]:
    if not isinstance(prediction, dict):
        raise ValueError(f"{case_id}: solver output must be an object")
    selection = prediction.get("selection")
    if not isinstance(selection, list):
        raise ValueError(f"{case_id}: solver selection must be a list")
    if any(type(index) is not int or index < 0 or index >= tile_count for index in selection):
        raise ValueError(f"{case_id}: solver selection index out of bounds")
    if len(selection) != len(set(selection)):
        raise ValueError(f"{case_id}: solver selection contains duplicates")
    scores = prediction.get("scores")
    if not isinstance(scores, list) or len(scores) != tile_count:
        raise ValueError(f"{case_id}: solver scores must have length {tile_count}")
    try:
        scores = [float(score) for score in scores]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{case_id}: solver scores must be finite numbers") from exc
    if any(not math.isfinite(score) for score in scores):
        raise ValueError(f"{case_id}: solver scores must be finite numbers")
    try:
        elapsed = float(prediction.get("elapsed_ms"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{case_id}: solver elapsed_ms must be finite and nonnegative") from exc
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError(f"{case_id}: solver elapsed_ms must be finite and nonnegative")
    return sorted(selection), scores, elapsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", type=Path, required=True, help="public_full.json[.gz] or fixed test fold JSON")
    result.add_argument("--image-root", type=Path, required=True, help="root containing restored tile_paths")
    result.add_argument("--output-dir", type=Path, required=True, help="must be a new directory")
    result.add_argument("--type", choices=["A", "B", "all"], default="all")
    result.add_argument("--limit", type=int, help="evaluate first N selected cases in source order")
    result.add_argument("--model-root", type=Path, required=True)
    result.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    result.add_argument("--threads", type=int, default=2)
    result.add_argument("--clip-threshold", type=float, default=0.01)
    result.add_argument("--confidence-threshold", type=float, default=0.5)
    result.add_argument("--coverage-threshold", type=float, default=0.1)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.output_dir.exists():
        raise SystemExit(f"Output directory already exists: {args.output_dir}")
    if args.threads < 1:
        raise SystemExit("--threads must be positive")
    thresholds = (args.clip_threshold, args.confidence_threshold, args.coverage_threshold)
    if any(not math.isfinite(value) for value in thresholds):
        raise SystemExit("Thresholds must be finite")
    if not 0 <= args.confidence_threshold <= 1 or not 0 <= args.coverage_threshold <= 1:
        raise SystemExit("Confidence and coverage thresholds must be in [0, 1]")

    manifest_bytes = args.manifest.read_bytes()
    manifest = read_json(args.manifest)
    cases = select_cases(manifest, args.type, args.limit)
    if not cases:
        raise SystemExit("No board cases selected")
    # Only selected cases require available images and matching hashes.
    for case in cases:
        case["tile_paths"], case["tile_sha256"] = _verify_tiles(case, args.image_root)

    # Validate the model manifest before creating a run directory.
    model_manifest_path = args.model_root / "manifest.json"
    model_manifest_sha = digest(model_manifest_path)
    args.output_dir.mkdir(parents=True)
    predictions_path = args.output_dir / "predictions.jsonl"
    predictions_path.touch()
    protocol = {
        "status": "running",
        "manifest_path": str(args.manifest),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "image_root": str(args.image_root.resolve()),
        "model_root": str(args.model_root.resolve()),
        "model_manifest_sha256": model_manifest_sha,
        "challenge_type": args.type,
        "limit": args.limit,
        "selected_count": len(cases),
        "limit_semantics": "first N cases after type filtering, in source manifest order" if args.limit else None,
        "device": args.device,
        "threads": args.threads,
        "thresholds": {
            "clip": args.clip_threshold,
            "confidence": args.confidence_threshold,
            "coverage": args.coverage_threshold,
        },
        "inference_api": False,
        "network_policy": "offline; model loading requires local_files_only",
        "type_b_input": "reconstructed from hash-verified row-major 16 tiles, preserving tile order and pixel dimensions; identity with source full image was not verified",
        "gold_policy": "reference_selection and correct_answers used only after inference for scoring",
        "runtime": runtime_versions(),
        "source_sha256": {name: digest(HERE / name) for name in
                          ("local_visual.py", "evaluate_local_visual.py")},
    }
    (args.output_dir / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    rows = []
    try:
        from PIL import Image
        from local_visual import LocalVisualSolver

        solver = LocalVisualSolver(
            args.model_root,
            args.device,
            args.threads,
            args.clip_threshold,
            args.confidence_threshold,
            args.coverage_threshold,
        )
        for case in cases:
            if case["source_type"] == "Type A":
                prediction = solver.predict_type_a(case["tile_paths"], case["prompt_target"])
                derived_image = None
            else:
                images = []
                for path in case["tile_paths"]:
                    with Image.open(path) as image:
                        images.append(image.convert("RGB"))
                width, height = images[0].size
                if any(image.size != (width, height) for image in images):
                    raise ValueError(f"{case['id']}: inconsistent tile dimensions")
                board = Image.new("RGB", (4 * width, 4 * height))
                for index, image in enumerate(images):
                    board.paste(image, ((index % 4) * width, (index // 4) * height))
                derived_image = args.output_dir / (
                    f"{len(rows):06d}-{hashlib.sha256(case['id'].encode()).hexdigest()[:12]}-reconstructed.png"
                )
                board.save(derived_image, format="PNG")
                prediction = solver.predict_type_b(derived_image, case["prompt_target"], 4, 4)

            selection, scores, elapsed = validate_prediction(
                prediction, case["tile_count"], case["id"]
            )
            row = {
                "id": case["id"],
                "challenge_type": case["source_type"],
                "canonical_target": case["canonical_target"],
                "prompt_target": case["prompt_target"],
                "input_tile_sha256": case["tile_sha256"],
                "prediction": selection,
                "scores": scores,
                "model": prediction.get("model"),
                "elapsed_ms": elapsed,
                "reference_selection": case["reference_selection"],
                "exact_match": selection == case["reference_selection"],
            }
            if derived_image is not None:
                row["derived_image_path"] = derived_image.name
                row["derived_image_sha256"] = digest(derived_image)
                row["boxes"] = prediction.get("boxes")
                row["detection_scores"] = prediction.get("detection_scores")
            serialized = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
            with predictions_path.open("a", encoding="utf-8") as stream:
                stream.write(serialized)
            rows.append(row)
            (args.output_dir / "metrics.json").write_text(
                json.dumps(aggregate(rows), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            )
        protocol["status"] = "completed"
        protocol["completed_count"] = len(rows)
    except BaseException as exc:
        protocol["status"] = "failed"
        protocol["completed_count"] = len(rows)
        protocol["error"] = f"{type(exc).__name__}: {exc}"
        (args.output_dir / "metrics.json").write_text(
            json.dumps(aggregate(rows), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        (args.output_dir / "protocol.json").write_text(
            json.dumps(protocol, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        raise
    (args.output_dir / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
