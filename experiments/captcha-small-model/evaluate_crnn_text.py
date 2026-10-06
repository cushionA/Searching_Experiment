#!/usr/bin/env python3
"""Pinned CPU CAPTCHA CRNN recognition on a checked local image manifest.

The reviewed Graf-J architecture and processor are transcribed locally. Only
safetensors weights and JSON configuration are loaded; no Transformers, Hub
loader, downloaded Python execution, detector, or label-dependent processing is
used. Whole images are converted to 150x40 grayscale and greedily CTC decoded.
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
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPOSITORY = "Graf-J/captcha-crnn-finetuned"
REVISION = "8ca7bfadc2608b007b5cafe20a7d0c29888a5cbb"
MODEL_FILENAME = "model.safetensors"
VOCABULARY = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FILE_SHA256 = {
    "model.safetensors": "df4c6fc59d1a6c0e3c7b7ef4ae9466a55285df18877f104b58a9c05eaffb26e5",
    "modeling_captcha.py": "d406cf7e9db440e221741e90dcb4018e817013cdc77e60397631aefbdb88917a",
    "processing_captcha.py": "8f6e681a59ae16d08566e55ec0707ac8c692d78368a594081a38009dff92cd99",
    "config.json": "ed4ae2ab276950989babfd514d5a2466ed3566fc1004f3baf3e440ee4423ba84",
    "processor_config.json": "ec6118dcdcca92dd25217cdbd7a2fca30b35b1326bd981f5bcad528e3b2f5701",
    "configuration_captcha.py": "447437e7fe7787ac283da0057e8414223c9744d98351d0c8832b17d341be949e",
    "pipeline.py": "1471be4ef06839054e381646b732a09d3299ba6ffcf1f6d75a8a770fa77efb9c",
    "handler.py": "2f6b1e19d076a3bf4306d187e2831410f6da15f668dc17c432291837fda82995",
    "README.md": "ff7d3541e4a9701f3812afffcbe8f32582044fbcc98bd551e667d23bcfce7dc4",
}


@functools.lru_cache(maxsize=1)
def reference() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_crnn_public_text_reference", Path(__file__).with_name("evaluate_public_text.py")
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


def verify_model_files(model_dir: Path) -> dict[str, Any]:
    files = {}
    for filename, expected in FILE_SHA256.items():
        path = model_dir / filename
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"{filename} SHA-256 mismatch: expected {expected}, got {actual}")
        files[filename] = {"sha256": actual, "bytes": path.stat().st_size}
    return files


def runtime_versions() -> dict[str, str]:
    return {name: importlib.metadata.version(name) for name in
            ("torch", "torchvision", "safetensors", "numpy", "Pillow")}


def recognition_options() -> dict[str, Any]:
    return {
        "device": "cpu", "dtype": "float32", "batch_size": 1,
        "torch_intra_op_threads": 2, "torch_inter_op_threads": 1,
        "model_training_mode": False, "torch_inference_mode": True,
        "use_det": False, "use_cls": False, "whole_image_as_single_text_line": True,
        "architecture": "local transcription of pinned modeling_captcha.py; strict safetensors state loading",
        "input_size_width_height": [150, 40], "image_channels": 1,
        "input_conversion": "PIL convert(L), PIL resize((150, 40)) using the official implicit default (bicubic for L images), torchvision.transforms.functional.to_tensor",
        "input_scaling": "[0, 1]; no mean/std normalization; no EXIF transpose",
        "decoder": "official greedy CTC: argmax; discard blank 0; merge adjacent equal tokens before blank removal",
        "vocabulary": VOCABULARY, "blank_index": 0, "num_classes": 63,
        "allowlist": None, "confidence_filter": None, "prediction_normalization": None,
        "confidence": None,
        "warmup": "first manifest image once, excluded from sample timings",
        "sample_timing": "PIL file open excluded; image decoding, grayscale/resize/tensor preprocessing, inference and greedy CTC decoding included",
    }


def create_model(torch: Any, config: Any) -> Any:
    nn = torch.nn

    class CaptchaCRNN(nn.Module):
        def __init__(self, config):
            super().__init__()
            self.conv_layer = nn.Sequential(
                nn.Conv2d(1, 32, kernel_size=3, padding=1),
                nn.BatchNorm2d(32),
                nn.SiLU(),
                nn.MaxPool2d(2, 2),
                nn.Conv2d(32, 64, kernel_size=3, padding=1),
                nn.BatchNorm2d(64),
                nn.SiLU(),
                nn.MaxPool2d(2, 2),
                nn.Conv2d(64, 128, kernel_size=3, padding=1),
                nn.BatchNorm2d(128),
                nn.SiLU(),
                nn.MaxPool2d(kernel_size=(2, 1)),
                nn.Conv2d(128, 256, kernel_size=3, padding=1),
                nn.BatchNorm2d(256),
                nn.SiLU(),
            )
            self.lstm = nn.LSTM(input_size=1280, hidden_size=256, bidirectional=True, batch_first=True)
            self.classifier = nn.Linear(512, config.num_chars)

        def forward(self, x, labels=None):
            x = self.conv_layer(x)
            x = x.permute(0, 3, 1, 2)
            batch, width, channels, height = x.size()
            x = x.view(batch, width, -1)
            x, _ = self.lstm(x)
            logits = self.classifier(x)
            return logits

    return CaptchaCRNN(config)


def greedy_ctc_decode(torch: Any, logits: Any, vocabulary: str = VOCABULARY) -> list[str]:
    idx_to_char = {i + 1: char for i, char in enumerate(vocabulary)}
    idx_to_char[0] = ""
    tokens = torch.argmax(logits, dim=-1)
    if len(tokens.shape) == 1:
        tokens = tokens.unsqueeze(0)
    decoded_strings = []
    for batch_item in tokens:
        char_list = []
        for i in range(len(batch_item)):
            token = batch_item[i].item()
            if token != 0:
                if i > 0 and batch_item[i] == batch_item[i - 1]:
                    continue
                char_list.append(idx_to_char.get(token, ""))
        decoded_strings.append("".join(char_list))
    return decoded_strings


class CaptchaCRNNOCR:
    def __init__(self, model_dir: Path, files: dict[str, Any] | None = None):
        import torch
        from safetensors.torch import load_file
        from torchvision.transforms.functional import to_tensor

        files = verify_model_files(model_dir) if files is None else files
        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        processor = json.loads((model_dir / "processor_config.json").read_text(encoding="utf-8"))
        if config.get("num_chars") != 63 or config.get("model_type") != "captcha_crnn":
            raise ValueError("unexpected pinned CRNN configuration")
        if processor.get("vocab") != VOCABULARY:
            raise ValueError("unexpected pinned processor vocabulary")
        torch.set_num_threads(2)
        if torch.get_num_interop_threads() != 1:
            torch.set_num_interop_threads(1)
        if torch.get_num_threads() != 2 or torch.get_num_interop_threads() != 1:
            raise RuntimeError("PyTorch did not retain the fixed 2/1 CPU thread configuration")
        self.torch, self.to_tensor = torch, to_tensor
        started = time.perf_counter()
        self.model = create_model(torch, SimpleNamespace(num_chars=config["num_chars"]))
        state = load_file(str(model_dir / MODEL_FILENAME), device="cpu")
        self.model.load_state_dict(state, strict=True)
        self.model.eval().to(device="cpu", dtype=torch.float32)
        self.load_seconds = time.perf_counter() - started
        if any(parameter.device.type != "cpu" or parameter.dtype != torch.float32 for parameter in self.model.parameters()):
            raise RuntimeError("CRNN parameters must be CPU FP32")
        self.providers = ["PyTorchCPU"]
        self.metadata = {
            "model": REPOSITORY, "source_url": "https://huggingface.co/" + REPOSITORY,
            "revision": REVISION, "filename": MODEL_FILENAME,
            "weight_url": f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{MODEL_FILENAME}",
            **files[MODEL_FILENAME], "files": files,
            "parameters": sum(parameter.numel() for parameter in self.model.parameters()),
            "state_dict_entries": len(state), "strict_state_loading": True,
            "character_dictionary_entries": len(VOCABULARY),
            "character_dictionary_sha256": hashlib.sha256(VOCABULARY.encode()).hexdigest(),
            "runtime_versions": runtime_versions(), "options": recognition_options(),
            "actual_torch_intra_op_threads": torch.get_num_threads(),
            "actual_torch_inter_op_threads": torch.get_num_interop_threads(),
            "implementation": "reviewed local nn.Module transcription; no Transformers or source-file execution",
        }

    def preprocess(self, image: Any) -> Any:
        image = image.convert("L")
        image = image.resize((150, 40))
        tensor = self.to_tensor(image)
        if tuple(tensor.shape) != (1, 40, 150) or tensor.dtype != self.torch.float32:
            raise RuntimeError("unexpected official processor tensor shape or dtype")
        return self.torch.stack([tensor])

    def predict(self, image: Any) -> tuple[str, None]:
        tensor = self.preprocess(image)
        with self.torch.inference_mode():
            logits = self.model(tensor)
            if tuple(logits.shape) != (1, 37, 63) or not self.torch.isfinite(logits).all().item():
                raise RuntimeError("CRNN returned invalid logits")
            answers = greedy_ctc_decode(self.torch, logits)
        if len(answers) != 1 or not isinstance(answers[0], str):
            raise RuntimeError("expected one raw decoded string")
        return answers[0], None


def recognize_sample(ocr: CaptchaCRNNOCR, sample: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image

    answer, confidence, error = "", None, None
    stage = "image_open"
    started = time.perf_counter()
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
    return {key: sample[key] for key in ("id", "path", "source", "sha256", "label")} | {
        "answer": answer, "exact": answer == label,
        "case_insensitive_exact": answer.casefold() == label.casefold(),
        "distance": reference().levenshtein(answer, label), "characters": len(label),
        "latency_ms": latency_ms,
        "confidence": confidence, "error": error,
    }


def build_summary(rows: list[dict[str, Any]], manifest: dict[str, Any], identity: dict[str, Any],
                  ocr: CaptchaCRNNOCR, warmup: dict[str, Any]) -> dict[str, Any]:
    summary = reference().summarize(rows, manifest, ocr.load_seconds, ocr.providers)
    chars = sum(row["characters"] for row in rows)
    failures = collections.Counter(row["error"]["type"] for row in rows if row["error"] is not None)
    for source, group_summary in summary["by_source"].items():
        group = [row for row in rows if row["source"] == source]
        group_summary["failed_samples"] = sum(row["error"] is not None for row in group)
        group_summary["empty_answers"] = sum(row["answer"] == "" for row in group)
    summary.update({
        "schema_version": 1, "ocr_backend": "CaptchaCRNN/PyTorchCPU",
        "evaluation_identity": identity, "model": ocr.metadata,
        "manifest_sha256": identity["manifest_sha256"], "model_sha256": identity["model_sha256"],
        "runtime_versions": identity["runtime_versions"], "recognition_options": recognition_options(),
        "evaluator_sha256": identity["evaluator_sha256"], "started_at_utc": identity["started_at_utc"],
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
             checkpoint_every: int = 100) -> tuple[Path, Path]:
    if checkpoint_every <= 0:
        raise ValueError("checkpoint-every must be positive")
    output = output.resolve()
    json_path, jsonl_path = output.with_suffix(".json"), output.with_suffix(".jsonl")
    if output.exists() or json_path.exists() or jsonl_path.exists():
        raise FileExistsError(f"output already exists: {output} (or {json_path}/{jsonl_path})")
    model_dir = model_dir.resolve()
    files = verify_model_files(model_dir)
    manifest_path = manifest_path.resolve()
    manifest, samples = reference().load_and_check_manifest(manifest_path)
    manifest["manifest_path"] = str(manifest_path)
    identity = {
        "model": REPOSITORY, "revision": REVISION, "model_dir": str(model_dir),
        "model_sha256": files[MODEL_FILENAME]["sha256"], "files": files,
        "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
        "runtime_versions": runtime_versions(), "recognition_options": recognition_options(),
        "evaluator_sha256": sha256_file(Path(__file__)),
        "reference_evaluator_sha256": sha256_file(Path(__file__).with_name("evaluate_public_text.py")),
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    ocr = CaptchaCRNNOCR(model_dir, files)
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
    parser.add_argument("--model-dir", type=Path, required=True, help="directory containing pinned reviewed CRNN model files")
    parser.add_argument("--checkpoint-every", type=int, default=100)
    args = parser.parse_args()
    summary_path, predictions_path = evaluate(args.manifest, args.output, args.model_dir, args.checkpoint_every)
    print(json.dumps({"summary": str(summary_path), "predictions": str(predictions_path)}))


if __name__ == "__main__":
    main()
