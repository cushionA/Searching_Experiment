#!/usr/bin/env python3
"""Evaluate local EasyOCR English recognition on a checked text-image manifest.

Each whole image is supplied as one grayscale crop. Detection is disabled, the
recognizer uses CPU FP32 with greedy decoding, and raw text is scored without
alphabet restrictions or normalization. EasyOCR's standard contrast retry is
enabled and recorded. This script never downloads models or overwrites results.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import string
import sys
import time
from pathlib import Path
from typing import Any


MODEL_FILENAME = "english_g2.pth"
MODEL_URL = "https://github.com/JaidedAI/EasyOCR/releases/download/v1.3/english_g2.zip"
MODEL_SHA256 = "e2272681d9d67a04e2dff396b6e95077bc19001f8f6d3593c307b9852e1c29e8"
EASYOCR_VERSION = "1.7.2"


@functools.lru_cache(maxsize=1)
def reference() -> Any:
    # The existing helper imports Pillow. Keep it and OCR dependencies lazy so
    # importing this evaluator or displaying --help does not require EasyOCR.
    spec = importlib.util.spec_from_file_location(
        "_easyocr_public_text_reference", Path(__file__).with_name("evaluate_public_text.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_versions() -> dict[str, str]:
    required = ("easyocr", "torch", "torchvision", "numpy", "Pillow")
    versions = {name: importlib.metadata.version(name) for name in required}
    if versions["easyocr"] != EASYOCR_VERSION:
        raise RuntimeError(f"easyocr must be {EASYOCR_VERSION}, found {versions['easyocr']}")
    for name in ("opencv-python", "opencv-python-headless", "scipy", "scikit-image", "python-bidi", "PyYAML"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return versions


def recognition_options(ascii_alnum: bool = False) -> dict[str, Any]:
    return {
        "languages": ["en"], "gpu": False, "device": "cpu",
        "detector": False, "recognizer": True, "recog_network": "standard",
        "download_enabled": False, "quantize": False, "precision": "FP32",
        "cudnn_benchmark": False, "user_network_directory": "<model-dir>/user-network",
        "input_conversion": "PIL convert(RGB), NumPy RGB, OpenCV COLOR_RGB2GRAY; no EXIF transpose",
        "model_image_height": 64,
        "recognizer_preprocessing": "EasyOCR get_image_list and AlignCollate defaults: aspect-ratio resize, bicubic interpolation, right-edge padding, normalization to [-1, 1]",
        "horizontal_list": "[[0, image_width, 0, image_height]]", "free_list": [],
        "whole_image_as_single_text_line": True,
        "decoder": "greedy", "beamWidth": 5, "batch_size": 1, "workers": 0,
        "allowlist": string.digits + string.ascii_letters if ascii_alnum else None,
        "allowlist_source": "fixed ASCII digits and both letter cases; independent of sample labels" if ascii_alnum else None,
        "blocklist": None, "detail": 1,
        "rotation_info": None, "paragraph": False,
        "contrast_ths": 0.1, "adjust_contrast": 0.5, "filter_ths": 0.003,
        "contrast_retry": "EasyOCR default: low-confidence crops are retried with contrast adjustment; higher-confidence result is returned",
        "y_ths": 0.5, "x_ths": 1.0, "reformat": True, "output_format": "standard",
        "prediction_normalization": None, "external_confidence_filter": None,
        "torch_intra_op_threads": 2, "torch_inter_op_threads": 1, "opencv_num_threads": 1,
        "warmup": "first manifest image once, excluded from sample timings",
        "sample_timing": "PIL file open excluded; image decoding/grayscale conversion, EasyOCR crop preprocessing, inference (including any contrast retry) and text decoding included",
    }


class EasyOCRTextOCR:
    def __init__(self, model_dir: Path, ascii_alnum: bool = False):
        import cv2
        import easyocr
        import numpy as np
        import torch
        from easyocr.config import imgH, recognition_models

        self.np, self.cv2 = np, cv2
        self.allowlist = string.digits + string.ascii_letters if ascii_alnum else None
        torch.set_num_threads(2)
        if torch.get_num_interop_threads() != 1:
            torch.set_num_interop_threads(1)
        cv2.setNumThreads(1)
        if torch.get_num_threads() != 2 or torch.get_num_interop_threads() != 1:
            raise RuntimeError("PyTorch did not retain the fixed 2/1 CPU thread configuration")
        official = recognition_models["gen2"]["english_g2"]
        if official["filename"] != MODEL_FILENAME or official["url"] != MODEL_URL:
            raise RuntimeError("EasyOCR English model configuration differs from the expected official model")
        if imgH != 64:
            raise RuntimeError(f"unexpected EasyOCR standard recognition height: {imgH}")
        started = time.perf_counter()
        self.reader = easyocr.Reader(
            ["en"], gpu=False, detector=False, recognizer=True,
            download_enabled=False, model_storage_directory=str(model_dir),
            user_network_directory=str(model_dir / "user-network"), recog_network="standard",
            quantize=False, cudnn_benchmark=False, verbose=False,
        )
        self.load_seconds = time.perf_counter() - started
        if str(self.reader.device) != "cpu":
            raise RuntimeError(f"unexpected EasyOCR device: {self.reader.device}")
        parameters = list(self.reader.recognizer.parameters())
        if not parameters or any(parameter.device.type != "cpu" or parameter.dtype != torch.float32 for parameter in parameters):
            raise RuntimeError("EasyOCR recognizer must have CPU FP32 parameters")
        self.providers = ["PyTorchCPU"]
        self.model_metadata = {
            "filename": MODEL_FILENAME, "model_network": "english_g2", "generation": "gen2",
            "url": MODEL_URL, "official_md5": official["md5sum"],
            "character_dictionary_entries": len(self.reader.character),
            "character_dictionary_sha256": hashlib.sha256(self.reader.character.encode()).hexdigest(),
            "effective_ignored_characters": "".join(sorted(set(self.reader.character) - set(self.reader.lang_char))),
            "model_image_height": imgH,
            "easyocr_config_sha256": sha256_file(Path(easyocr.__file__).with_name("config.py")),
            "opencv_runtime_version": cv2.__version__,
            "actual_torch_intra_op_threads": torch.get_num_threads(),
            "actual_torch_inter_op_threads": torch.get_num_interop_threads(),
            "actual_opencv_num_threads": cv2.getNumThreads(),
        }

    def predict(self, image: Any) -> tuple[str, float | None]:
        rgb = self.np.array(image.convert("RGB"))
        grayscale = self.cv2.cvtColor(rgb, self.cv2.COLOR_RGB2GRAY)
        height, width = grayscale.shape
        result = self.reader.recognize(
            grayscale, horizontal_list=[[0, width, 0, height]], free_list=[],
            decoder="greedy", beamWidth=5, batch_size=1, workers=0,
            allowlist=self.allowlist, blocklist=None, detail=1, rotation_info=None,
            paragraph=False, contrast_ths=0.1, adjust_contrast=0.5, filter_ths=0.003,
            y_ths=0.5, x_ths=1.0, reformat=True, output_format="standard",
        )
        if len(result) != 1 or len(result[0]) != 3 or not isinstance(result[0][1], str):
            raise RuntimeError("EasyOCR must return exactly one raw string for the whole image")
        confidence = float(result[0][2]) if result[0][2] is not None else None
        if confidence is not None and not math.isfinite(confidence):
            raise RuntimeError("EasyOCR returned non-finite confidence")
        return result[0][1], confidence


def recognize_sample(ocr: EasyOCRTextOCR, sample: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image

    answer, confidence, error = "", None, None
    started = time.perf_counter()
    stage = "image_open"
    try:
        with Image.open(sample["path"]) as image:
            started = time.perf_counter()
            stage = "image_decode"
            image.load()
            stage = "recognition"
            answer, confidence = ocr.predict(image)
    except Exception as exc:
        answer, confidence = "", None
        error = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
    latency_ms = (time.perf_counter() - started) * 1000
    label = sample["label"]
    return {
        "id": sample["id"], "path": sample["path"], "source": sample["source"],
        "sha256": sample["sha256"], "label": label, "answer": answer,
        "exact": answer == label,
        "case_insensitive_exact": answer.casefold() == label.casefold(),
        "distance": reference().levenshtein(answer, label), "characters": len(label),
        "latency_ms": latency_ms, "confidence": confidence, "error": error,
    }


def build_summary(rows: list[dict[str, Any]], manifest: dict[str, Any], identity: dict[str, Any],
                  ocr: EasyOCRTextOCR, warmup: dict[str, Any]) -> dict[str, Any]:
    summary = reference().summarize(rows, manifest, ocr.load_seconds, ocr.providers)
    chars = sum(row["characters"] for row in rows)
    failures = collections.Counter(row["error"]["type"] for row in rows if row["error"] is not None)
    for source, group_summary in summary["by_source"].items():
        group = [row for row in rows if row["source"] == source]
        group_summary["failed_samples"] = sum(row["error"] is not None for row in group)
        group_summary["empty_answers"] = sum(row["answer"] == "" for row in group)
    summary.update({
        "schema_version": 1, "ocr_backend": "EasyOCR.Reader.recognize/PyTorchCPU",
        "evaluation_identity": identity, "model": ocr.model_metadata,
        "manifest_sha256": identity["manifest_sha256"], "model_sha256": identity["model_sha256"],
        "runtime_versions": identity["runtime_versions"], "recognition_options": identity["recognition_options"],
        "started_at_utc": identity["started_at_utc"],
        "completed": len(rows), "total_samples": len(manifest["samples"]),
        "status": "complete" if len(rows) == len(manifest["samples"]) else "incomplete",
        "literal_exact_match": sum(row["exact"] for row in rows) / len(rows) if rows else None,
        "case_insensitive_exact_match": sum(row["case_insensitive_exact"] for row in rows) / len(rows) if rows else None,
        "character_error_rate": sum(row["distance"] for row in rows) / chars if chars else None,
        "latency": reference().timing([row["latency_ms"] for row in rows]),
        "failed_samples": sum(failures.values()), "failure_counts": dict(sorted(failures.items())),
        "empty_answers": sum(row["answer"] == "" for row in rows), "warmup": warmup,
    })
    return summary


def evaluate(manifest_path: Path, output: Path, model_dir: Path,
             checkpoint_every: int = 100, ascii_alnum: bool = False) -> tuple[Path, Path]:
    if checkpoint_every <= 0:
        raise ValueError("checkpoint-every must be positive")
    output = output.resolve()
    json_path, jsonl_path = output.with_suffix(".json"), output.with_suffix(".jsonl")
    if output.exists() or json_path.exists() or jsonl_path.exists():
        raise FileExistsError(f"output already exists: {output} (or {json_path}/{jsonl_path})")
    model_dir = model_dir.resolve()
    model_path = model_dir / MODEL_FILENAME
    if not model_path.is_file():
        raise FileNotFoundError(model_path)
    model_sha256 = sha256_file(model_path)
    if model_sha256 != MODEL_SHA256:
        raise ValueError(f"model SHA-256 mismatch: expected {MODEL_SHA256}, got {model_sha256}")
    manifest_path = manifest_path.resolve()
    manifest, samples = reference().load_and_check_manifest(manifest_path)
    manifest["manifest_path"] = str(manifest_path)
    identity = {
        "model": "easyocr-english-g2", "model_path": str(model_path), "model_sha256": model_sha256,
        "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
        "runtime_versions": runtime_versions(), "recognition_options": recognition_options(ascii_alnum),
        "evaluator_sha256": sha256_file(Path(__file__)),
        "reference_evaluator_sha256": sha256_file(Path(__file__).with_name("evaluate_public_text.py")),
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    ocr = EasyOCRTextOCR(model_dir, ascii_alnum)
    warmup_row = recognize_sample(ocr, samples[0])
    warmup = {"sample_id": samples[0]["id"], "seconds": warmup_row["latency_ms"] / 1000,
              "error": warmup_row["error"], "excluded_from_sample_timings": True}
    rows: list[dict[str, Any]] = []
    output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint() -> None:
        reference().atomic_jsonl(jsonl_path, rows)
        summary = build_summary(rows, manifest, identity, ocr, warmup)
        summary["predictions_sha256"] = sha256_file(jsonl_path)
        reference().atomic_json(json_path, summary)
        print(f"checkpoint {len(rows)}/{len(samples)} failures={summary['failed_samples']}", file=sys.stderr, flush=True)

    checkpoint()
    try:
        for sample in samples:
            rows.append(recognize_sample(ocr, sample))
            if len(rows) % checkpoint_every == 0 or len(rows) == len(samples):
                checkpoint()
    except BaseException:
        checkpoint()
        raise
    return json_path, jsonl_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new output base path (.json + .jsonl)")
    parser.add_argument("--model-dir", type=Path, required=True, help="directory containing official english_g2.pth")
    parser.add_argument("--checkpoint-every", type=int, default=100)
    parser.add_argument("--ascii-alnum", action="store_true",
                        help="restrict decoding to a fixed ASCII alphanumeric alphabet; recorded as a separate protocol")
    args = parser.parse_args()
    summary_path, predictions_path = evaluate(args.manifest, args.output, args.model_dir, args.checkpoint_every, args.ascii_alnum)
    print(json.dumps({"summary": str(summary_path), "predictions": str(predictions_path)}))


if __name__ == "__main__":
    main()
