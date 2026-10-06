#!/usr/bin/env python3
"""Recognition-only, pinned CPU PaddleOCR evaluation on a checked local manifest.

Uses RapidOCR's TextRecognizer directly: each whole image is one text line, with
no detector, orientation classifier, confidence filter, or text normalization.
Only local models with the pinned SHA-256 and an embedded dictionary are allowed.
Outputs are new .json/.jsonl files unless --resume explicitly verifies a prior run.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image


_reference_spec = importlib.util.spec_from_file_location(
    "_paddle_public_text_reference", Path(__file__).with_name("evaluate_public_text.py")
)
assert _reference_spec is not None and _reference_spec.loader is not None
_reference = importlib.util.module_from_spec(_reference_spec)
_reference_spec.loader.exec_module(_reference)
levenshtein = _reference.levenshtein
load_and_check_manifest = _reference.load_and_check_manifest
atomic_json = _reference.atomic_json
atomic_jsonl = _reference.atomic_jsonl


MODELS = {
    "ppocr-v6-tiny": {
        "filename": "PP-OCRv6_rec_tiny.onnx", "version": "PP-OCRv6", "size": "tiny", "language": "multi",
        "sha256": "e16e242de5937ad92609223f19bc2aff3727ee40b095f996907c24749bad251b",
    },
    "ppocr-v6-small": {
        "filename": "PP-OCRv6_rec_small.onnx", "version": "PP-OCRv6", "size": "small", "language": "multi",
        "sha256": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    },
    "ppocr-v6-medium": {
        "filename": "PP-OCRv6_rec_medium.onnx", "version": "PP-OCRv6", "size": "medium", "language": "multi",
        "sha256": "eef444829dbbe18d7fea59a3f6eb75647518d2b3a9568d27c92e42940204894b",
    },
    "ppocr-v5-en-mobile": {
        "filename": "en_PP-OCRv5_rec_mobile.onnx", "version": "PP-OCRv5", "size": "mobile", "language": "en",
        "sha256": "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8",
    },
    "ppocr-v5-server": {
        "filename": "ch_PP-OCRv5_rec_server.onnx", "version": "PP-OCRv5", "size": "server", "language": "ch",
        "sha256": "e09385400eaaaef34ceff54aeb7c4f0f1fe014c27fa8b9905d4709b65746562a",
    },
}
PINNED_VERSIONS = {"rapidocr": "3.9.2", "onnxruntime": "1.30.0"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_versions() -> dict[str, str]:
    versions = {name: importlib.metadata.version(name) for name in
                ("rapidocr", "onnxruntime", "numpy", "Pillow", "opencv-python", "omegaconf")}
    for name, expected in PINNED_VERSIONS.items():
        if versions[name] != expected:
            raise RuntimeError(f"{name} must be {expected}, found {versions[name]}")
    return versions


def recognition_options(warmup: bool) -> dict[str, Any]:
    return {
        "use_det": False, "use_cls": False, "use_rec": True,
        "whole_image_as_single_text_line": True, "rec_batch_num": 1,
        "rec_img_shape": [3, 48, 320],
        "rec_img_shape_source": "RapidOCR 3.9.2 config.yaml default; validated against ONNX input",
        "resize": "RapidOCR resize_norm_img; aspect-preserving height 48, dynamic width padded to at least 320",
        "input_conversion": "PIL convert(RGB), then NumPy RGB to OpenCV BGR; no EXIF transpose",
        "confidence_filter": None, "prediction_normalization": None,
        "return_word_box": False,
        "providers": ["CPUExecutionProvider"], "intra_op_num_threads": 2,
        "inter_op_num_threads": 1, "execution_mode": "ORT_SEQUENTIAL",
        "graph_optimization_level": "ORT_ENABLE_ALL", "enable_cpu_mem_arena": True,
        "opencv_num_threads": 1,
        "warmup": "first manifest image, once per process, excluded from sample timing" if warmup else None,
        "sample_timing": "PIL file open excluded; image decoding/RGB-to-BGR conversion, recognition preprocessing, inference and decoding included",
    }


class PaddleTextOCR:
    def __init__(self, model_path: Path, model: dict[str, str]):
        import cv2
        import numpy as np
        import onnxruntime as ort
        import rapidocr
        from rapidocr.ch_ppocr_rec import TextRecInput, TextRecognizer
        from rapidocr.utils.parse_parameters import ParseParams
        from rapidocr.utils.typings import ModelType, OCRVersion

        self.np, self.cv2, self.input_type = np, cv2, TextRecInput
        cv2.setNumThreads(1)
        started = time.perf_counter()
        config_path = Path(rapidocr.__file__).with_name("config.yaml")
        cfg = ParseParams.load(config_path)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_cpu_mem_arena = True
        options.log_severity_level = 4
        session = ort.InferenceSession(str(model_path), sess_options=options,
                                       providers=["CPUExecutionProvider"])
        self.providers = session.get_providers()
        if self.providers != ["CPUExecutionProvider"]:
            raise RuntimeError(f"unexpected providers: {self.providers}")
        actual_options = session.get_session_options()
        if (actual_options.intra_op_num_threads, actual_options.inter_op_num_threads) != (2, 1):
            raise RuntimeError("ONNXRuntime did not retain the fixed CPU thread configuration")
        metadata = session.get_modelmeta().custom_metadata_map
        if not metadata.get("character"):
            raise RuntimeError("model has no embedded character dictionary; offline evaluator refuses a dictionary download")
        input_shape = session.get_inputs()[0].shape
        configured_shape = list(cfg.Rec.rec_img_shape)
        if configured_shape != [3, 48, 320]:
            raise RuntimeError(f"unexpected RapidOCR default recognition shape: {configured_shape}")
        if (len(input_shape) != 4 or any(isinstance(actual, int) and actual != expected
                                        for actual, expected in zip(input_shape[1:3], configured_shape[:2]))):
            raise RuntimeError(f"ONNX input {input_shape} is incompatible with RapidOCR default {configured_shape}")
        if isinstance(input_shape[3], int):
            raise RuntimeError(f"fixed ONNX input width {input_shape[3]} is incompatible with dynamic-width recognition")
        cfg.Rec.model_path = str(model_path)
        cfg.Rec.model_root_dir = str(model_path.parent)
        cfg.Rec.ocr_version = OCRVersion(model["version"])
        cfg.Rec.model_type = ModelType(model["size"])
        cfg.Rec.lang_type = model["language"]
        cfg.Rec.rec_batch_num = 1
        cfg.Rec.font_path = None
        cfg.Rec.engine_cfg = cfg.EngineConfig[cfg.Rec.engine_type.value]
        # Inject the already verified local session. This avoids both provider
        # fallback and any detector/classifier/model/dictionary download path.
        cfg.Rec._set_flag("allow_objects", True)
        cfg.Rec.session = session
        self.engine = TextRecognizer(cfg.Rec)
        self.load_seconds = time.perf_counter() - started
        self.model_metadata = {
            "inputs": [{"name": item.name, "shape": item.shape, "type": item.type} for item in session.get_inputs()],
            "outputs": [{"name": item.name, "shape": item.shape, "type": item.type} for item in session.get_outputs()],
            "metadata_keys": sorted(metadata),
            "character_dictionary_entries": len(metadata["character"].splitlines()),
            "character_dictionary_sha256": hashlib.sha256(metadata["character"].encode()).hexdigest(),
            "rapidocr_config_sha256": sha256_file(config_path),
        }

    def predict(self, image: Image.Image) -> tuple[str, float | None]:
        bgr = self.cv2.cvtColor(self.np.array(image.convert("RGB")), self.cv2.COLOR_RGB2BGR)
        result = self.engine(self.input_type(img=bgr, return_word_box=False))
        if result.txts is None or len(result.txts) != 1 or not isinstance(result.txts[0], str):
            raise RuntimeError("recognizer must return exactly one raw string for the whole image")
        confidence = float(result.scores[0]) if result.scores is not None and len(result.scores) else None
        if confidence is not None and not math.isfinite(confidence):
            raise RuntimeError("recognizer returned a non-finite confidence")
        return result.txts[0], confidence


def recognize_sample(ocr: PaddleTextOCR, sample: dict[str, Any]) -> dict[str, Any]:
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
        "distance": levenshtein(answer, label), "characters": len(label),
        "latency_ms": latency_ms, "confidence": confidence, "error": error,
    }


def verify_resume_rows(rows: list[dict[str, Any]], samples: list[dict[str, Any]]) -> None:
    if len(rows) > len(samples):
        raise ValueError("resume contains more predictions than manifest samples")
    for index, (row, sample) in enumerate(zip(rows, samples)):
        if not isinstance(row, dict):
            raise ValueError(f"resume row {index} must be an object")
        for field in ("id", "path", "source", "sha256", "label"):
            if row.get(field) != sample[field]:
                raise ValueError(f"resume row {index} has mismatched {field}")
        answer = row.get("answer")
        if not isinstance(answer, str):
            raise ValueError(f"resume row {index} answer must be a raw string")
        expected = {"exact": answer == sample["label"],
                    "case_insensitive_exact": answer.casefold() == sample["label"].casefold(),
                    "distance": levenshtein(answer, sample["label"]),
                    "characters": len(sample["label"])}
        for field, value in expected.items():
            if row.get(field) != value or type(row.get(field)) is not type(value):
                raise ValueError(f"resume row {index} has invalid {field}")
        latency = row.get("latency_ms")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
            raise ValueError(f"resume row {index} has invalid latency_ms")
        if "error" not in row or "confidence" not in row:
            raise ValueError(f"resume row {index} is missing error/confidence status")
        error = row["error"]
        if error is not None:
            if (not isinstance(error, dict) or not all(isinstance(error.get(key), str) for key in ("stage", "type", "message"))
                    or answer != "" or row["confidence"] is not None):
                raise ValueError(f"resume row {index} has invalid error status")
        confidence = row["confidence"]
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence)):
            raise ValueError(f"resume row {index} has invalid confidence")


def build_summary(rows: list[dict[str, Any]], manifest: dict[str, Any], identity: dict[str, Any],
                  metadata: dict[str, Any], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    load_seconds = sessions[0]["model_load_seconds"] if sessions else 0.0
    summary = _reference.summarize(rows, manifest, load_seconds, ["CPUExecutionProvider"])
    chars = sum(row["characters"] for row in rows)
    failure_counts = collections.Counter(row["error"]["type"] for row in rows if row["error"] is not None)
    for source, group_summary in summary["by_source"].items():
        group = [row for row in rows if row["source"] == source]
        group_summary["failed_samples"] = sum(row["error"] is not None for row in group)
        group_summary["empty_answers"] = sum(row["answer"] == "" for row in group)
    summary.update({
        "schema_version": 1, "ocr_backend": "RapidOCR.TextRecognizer/ONNXRuntime",
        "evaluation_identity": identity, "model": {**MODELS[identity["model"]], **metadata},
        "manifest_sha256": identity["manifest_sha256"],
        "model_sha256": identity["model_sha256"], "runtime_versions": identity["runtime_versions"],
        "recognition_options": identity["recognition_options"],
        "completed": len(rows), "total_samples": len(manifest["samples"]),
        "status": "complete" if len(rows) == len(manifest["samples"]) else "incomplete",
        "literal_exact_match": sum(row["exact"] for row in rows) / len(rows) if rows else None,
        "case_insensitive_exact_match": sum(row["case_insensitive_exact"] for row in rows) / len(rows) if rows else None,
        "character_error_rate": sum(row["distance"] for row in rows) / chars if chars else None,
        "latency": _reference.timing([row["latency_ms"] for row in rows]),
        "failed_samples": sum(failure_counts.values()), "failure_counts": dict(sorted(failure_counts.items())),
        "empty_answers": sum(row["answer"] == "" for row in rows), "sessions": sessions,
    })
    return summary


def evaluate(manifest_path: Path, output: Path, model_name: str, model_dir: Path,
             checkpoint_every: int = 100, resume: bool = False, warmup: bool = False) -> tuple[Path, Path]:
    if checkpoint_every <= 0:
        raise ValueError("checkpoint-every must be positive")
    model = MODELS[model_name]
    model_path = (model_dir / model["filename"]).resolve()
    output = output.resolve()
    json_path, jsonl_path = output.with_suffix(".json"), output.with_suffix(".jsonl")
    if not resume and (output.exists() or json_path.exists() or jsonl_path.exists()):
        raise FileExistsError(f"output already exists: {output} (or {json_path}/{jsonl_path})")
    if resume and (not json_path.is_file() or not jsonl_path.is_file()):
        raise FileNotFoundError("--resume requires both existing .json and .jsonl checkpoint files")
    actual_model_sha256 = sha256_file(model_path)
    if actual_model_sha256 != model["sha256"]:
        raise ValueError(f"model SHA-256 mismatch: expected {model['sha256']}, got {actual_model_sha256}")
    manifest_path = manifest_path.resolve()
    manifest, samples = load_and_check_manifest(manifest_path)
    manifest["manifest_path"] = str(manifest_path)
    identity = {
        "model": model_name, "model_path": str(model_path), "model_sha256": actual_model_sha256,
        "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
        "runtime_versions": runtime_versions(), "recognition_options": recognition_options(warmup),
        "evaluator_sha256": sha256_file(Path(__file__)),
        "reference_evaluator_sha256": sha256_file(Path(__file__).with_name("evaluate_public_text.py")),
    }
    rows, sessions, prior = [], [], None
    if resume:
        prior = json.loads(json_path.read_text(encoding="utf-8"))
        if prior.get("evaluation_identity") != identity:
            raise ValueError("resume identity mismatch (manifest, model, versions, evaluator, or recognition options)")
        raw_lines = jsonl_path.read_bytes().splitlines(keepends=True)
        rows = [json.loads(line) for line in raw_lines]
        verify_resume_rows(rows, samples)
        completed = prior.get("completed")
        if type(completed) is not int or not 0 <= completed <= len(rows):
            raise ValueError("resume summary points beyond valid prediction rows")
        prefix_sha256 = hashlib.sha256(b"".join(raw_lines[:completed])).hexdigest()
        if prefix_sha256 != prior.get("predictions_sha256"):
            raise ValueError("resume predictions SHA-256 mismatch")
        # A crash between the two atomic writes can leave additional valid rows.
        # Their order, identities and score calculations were all checked above.
        sessions = prior.get("sessions", [])
        if not isinstance(sessions, list) or not sessions:
            raise ValueError("resume is missing recorded sessions")
        if len(rows) > completed:
            sessions[-1]["processed_samples"] += len(rows) - completed
        if len(rows) == len(samples) and completed == len(rows):
            return json_path, jsonl_path
    output.parent.mkdir(parents=True, exist_ok=True)
    ocr = PaddleTextOCR(model_path, model)
    if prior is not None and prior.get("model") != {**model, **ocr.model_metadata}:
        raise ValueError("resume model metadata/configuration changed")
    invocation = {
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "resumed_from": len(rows), "model_load_seconds": ocr.load_seconds,
        "providers": ocr.providers, "warmup_seconds": None, "warmup_error": None,
        "processed_samples": 0,
    }
    if warmup:
        warmup_row = recognize_sample(ocr, samples[0])
        invocation["warmup_seconds"] = warmup_row["latency_ms"] / 1000
        invocation["warmup_error"] = warmup_row["error"]
    sessions.append(invocation)

    def checkpoint() -> None:
        atomic_jsonl(jsonl_path, rows)
        summary = build_summary(rows, manifest, identity, ocr.model_metadata, sessions)
        summary["predictions_sha256"] = sha256_file(jsonl_path)
        atomic_json(json_path, summary)
        print(f"checkpoint {len(rows)}/{len(samples)} failures={summary['failed_samples']}", file=sys.stderr, flush=True)

    # Persist run identity before the first sample, so interruption can resume.
    checkpoint()
    try:
        for sample in samples[len(rows):]:
            rows.append(recognize_sample(ocr, sample))
            invocation["processed_samples"] += 1
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
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--model-dir", type=Path, required=True, help="directory containing the pinned local recognition ONNX")
    parser.add_argument("--checkpoint-every", type=int, default=100)
    parser.add_argument("--resume", action="store_true", help="verify and continue an existing checkpoint")
    parser.add_argument("--warmup", action="store_true", help="recognize the first manifest image once before timed samples")
    args = parser.parse_args()
    summary_path, predictions_path = evaluate(args.manifest, args.output, args.model, args.model_dir,
                                              args.checkpoint_every, args.resume, args.warmup)
    print(json.dumps({"summary": str(summary_path), "predictions": str(predictions_path)}))


if __name__ == "__main__":
    main()
