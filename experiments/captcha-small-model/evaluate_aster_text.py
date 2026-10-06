#!/usr/bin/env python3
"""Pinned ASTER PyTorch-port recognition on a checked offline text manifest.

Uses the author-linked ayumiymk port, its v1.0 release weights, and a reviewed
compatibility copy. Default input/beam settings follow the official test script:
RGB 256x64, TPS rectification, beam width 5, maximum 100 tokens. Beam width 1
selects the port's greedy sample method. No ground-truth inputs, teacher-forced
loss, lexicon, or case/punctuation normalization are used. The port omits the
paper's bidirectional attention decoder; it is not the full paper model.
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
from pathlib import Path
import string
import sys
import time
from typing import Any


SOURCE_COMMIT = "be670046c775b54de79766208f0c59321ae1eccf"
SOURCE_REPOSITORY = "https://github.com/ayumiymk/aster.pytorch"
WEIGHT_URL = SOURCE_REPOSITORY + "/releases/download/v1.0/demo.pth.tar"
WEIGHT_SHA256 = "c2d730c1f96357bb605bc7ef5d263537c4b0ec47de9491c766eda3132cdd6021"
MANIFEST_SHA256 = "434e637ca82ac844817c02a144425b1af83db938f4ed1a667bfc9643b41f97b7"
SOURCE_TREE_SHA256 = "a9a534f4040fc0a6a49b0353ee81c2914912f5debe19cb48b057b9d060aff785"
VOCABULARY = list(string.printable[:-6]) + ["EOS", "PADDING", "UNKNOWN"]
MAX_LENGTH = 100


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@functools.lru_cache(maxsize=1)
def reference() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_aster_public_text_reference", Path(__file__).with_name("evaluate_public_text.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_assets(model_dir: Path) -> dict[str, Any]:
    weight = model_dir / "demo.pth.tar"
    if sha256_file(weight) != WEIGHT_SHA256 or weight.stat().st_size != 84424941:
        raise ValueError("ASTER release weight SHA-256/size mismatch")
    manifest_path = model_dir / "compat/source_manifest.json"
    if sha256_file(manifest_path) != MANIFEST_SHA256:
        raise ValueError("ASTER source compatibility manifest SHA-256 mismatch")
    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    if source["commit"] != SOURCE_COMMIT or source["repository"] != SOURCE_REPOSITORY:
        raise ValueError("ASTER source identity mismatch")
    for name, expected in source["original_files"].items():
        if sha256_file(model_dir / "official-source" / name) != expected:
            raise ValueError(f"ASTER original source hash mismatch: {name}")
    tree_hash = hashlib.sha256("".join(name + "\0" + source["original_files"][name] + "\n"
                                      for name in sorted(source["original_files"])).encode()).hexdigest()
    if tree_hash != SOURCE_TREE_SHA256 or source["original_tree_sha256"] != tree_hash:
        raise ValueError("ASTER original full source tree identity mismatch")
    for name, expected in source["compat_files"].items():
        if sha256_file(model_dir / "compat" / name) != expected:
            raise ValueError(f"ASTER compatibility source hash mismatch: {name}")
    if sha256_file(model_dir / "compat/compatibility.patch") != source["compatibility_patch_sha256"]:
        raise ValueError("ASTER compatibility patch hash mismatch")
    return source


def runtime_versions() -> dict[str, str]:
    return {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "numpy", "Pillow")}


def recognition_options(beam_width: int, device: str) -> dict[str, Any]:
    return {
        "device": device, "dtype": "float32", "batch_size": 1,
        "torch_intra_op_threads": 2, "torch_inter_op_threads": 1,
        "whole_image_as_single_text_line": True, "use_det": False, "use_cls": False,
        "model_training_mode": False, "torch_inference_mode": True,
        "input_size_width_height": [256, 64],
        "input_preprocessing": "official main_test_all.sh/ResizeNormalize: RGB, PIL bilinear resize, torchvision to_tensor, (x-0.5)/0.5; no EXIF transpose",
        "preprocessing_note": "official demo.py uses 100x32 by default; this evaluator follows the official test-all 256x64 recipe",
        "arch": "ResNet_ASTER", "with_lstm": True, "STN_ON": True,
        "tps_inputsize": [32, 64], "tps_outputsize": [32, 100], "tps_margins": [0.05, 0.05],
        "num_control_points": 20, "stn_activation": "none",
        "interpolate_align_corners": True, "grid_sample_align_corners": True,
        "decoder_sdim": 512, "attDim": 512, "max_len": MAX_LENGTH,
        "decoder": "official greedy sample" if beam_width == 1 else "official beam_search",
        "beam_width": beam_width, "official_test_beam_width": 5,
        "ground_truth_inputs": False, "teacher_forced_loss": False,
        "voc_type": "ALLCASES_SYMBOLS", "vocabulary": VOCABULARY,
        "special_token_decoding": "stop at EOS; retain PADDING/UNKNOWN token names if predicted before EOS, matching labels2strs",
        "paper_bidirectional_attention_decoder_present": False,
        "prediction_normalization": None, "lexicon": None, "allowlist": None, "confidence_filter": None,
        "confidence": None,
        "confidence_note": "beam_search returns placeholder ones; no calibrated sequence confidence is reported",
        "warmup": "first manifest image once, excluded from sample timings",
        "sample_timing": "PIL file open excluded; decode, image preprocessing, TPS, encoder and attention decoding included; CUDA synchronized if selected",
    }


def load_compat_module(model_dir: Path, name: str) -> Any:
    path = model_dir / "compat" / name
    module_name = "_aster_compat_" + path.stem + "_" + sha256_file(path)[:12]
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def decode_token_ids(tokens: list[int]) -> str:
    answer = []
    for token in tokens:
        if not 0 <= token < len(VOCABULARY):
            raise ValueError(f"ASTER emitted out-of-vocabulary token: {token}")
        if VOCABULARY[token] == "EOS":
            break
        answer.append(VOCABULARY[token])
    return "".join(answer)


class AsterOCR:
    def __init__(self, model_dir: Path, beam_width: int = 5, device: str = "cpu"):
        import torch
        from torch.nn import functional as F
        from torchvision.transforms.functional import to_tensor

        if not 1 <= beam_width <= len(VOCABULARY):
            raise ValueError("beam-width must be between 1 and 97")
        self.source = verify_assets(model_dir)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA explicitly requested but unavailable; CPU fallback is refused")
        if device not in ("cpu", "cuda"):
            raise ValueError("device must be cpu or cuda")
        torch.set_num_threads(2)
        if torch.get_num_interop_threads() != 1:
            torch.set_num_interop_threads(1)
        if (torch.get_num_threads(), torch.get_num_interop_threads()) != (2, 1):
            raise RuntimeError("PyTorch did not retain the fixed 2/1 CPU thread configuration")
        self.torch, self.functional, self.to_tensor = torch, F, to_tensor
        self.device, self.beam_width = device, beam_width
        started = time.perf_counter()
        encoder_class = load_compat_module(model_dir, "resnet_aster.py").ResNet_ASTER
        head_class = load_compat_module(model_dir, "attention_recognition_head.py").AttentionRecognitionHead
        tps_class = load_compat_module(model_dir, "tps_spatial_transformer.py").TPSSpatialTransformer
        stn_class = load_compat_module(model_dir, "stn_head.py").STNHead

        class Recognizer(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = encoder_class(with_lstm=True, n_group=1)
                self.decoder = head_class(num_classes=len(VOCABULARY), in_planes=self.encoder.out_planes,
                                          sDim=512, attDim=512, max_len_labels=MAX_LENGTH)
                self.tps = tps_class(output_image_size=(32, 100), num_control_points=20, margins=(0.05, 0.05))
                self.stn_head = stn_class(in_planes=3, num_ctrlpoints=20, activation="none")

        self.model = Recognizer()
        checkpoint = torch.load(model_dir / "demo.pth.tar", map_location="cpu", weights_only=True)
        state = checkpoint.get("state_dict")
        if not isinstance(state, dict) or not all(isinstance(name, str) and torch.is_tensor(value) for name, value in state.items()):
            raise ValueError("ASTER checkpoint must contain a plain tensor state_dict")
        self.model.load_state_dict(state, strict=True)
        self.model.eval().to(device=device, dtype=torch.float32)
        self.synchronize()
        self.load_seconds = time.perf_counter() - started
        if any(parameter.device.type != device or parameter.dtype != torch.float32 for parameter in self.model.parameters()):
            raise RuntimeError("ASTER parameters must match the requested device and FP32 dtype")
        self.providers = ["PyTorchCPU" if device == "cpu" else "PyTorchCUDA"]
        self.metadata = {
            "model": "ASTER PyTorch port", "source_repository": SOURCE_REPOSITORY, "source_commit": SOURCE_COMMIT,
            "author_link": "bgshih/aster README identifies Mingkun Yang / ayumiymk as the PyTorch porter",
            "release": "v1.0", "weight_url": WEIGHT_URL, "filename": "demo.pth.tar",
            "sha256": WEIGHT_SHA256, "bytes": (model_dir / "demo.pth.tar").stat().st_size,
            "source_tree_sha256": SOURCE_TREE_SHA256, "compatibility_manifest_sha256": MANIFEST_SHA256,
            "compatibility_patch_sha256": self.source["compatibility_patch_sha256"], "source_files": self.source,
            "checkpoint_loading": "torch.load(weights_only=True, map_location=cpu); strict tensor state_dict",
            "checkpoint_iters": checkpoint.get("iters"), "checkpoint_reported_best_res": checkpoint.get("best_res"),
            "state_dict_entries": len(state), "parameters": sum(p.numel() for p in self.model.parameters()),
            "runtime_versions": runtime_versions(), "options": recognition_options(beam_width, device),
            "paper_bidirectional_attention_decoder_present": False,
        }

    def synchronize(self) -> None:
        if self.device == "cuda":
            self.torch.cuda.synchronize()

    def encode(self, image: Any) -> Any:
        from PIL import Image

        image = image.convert("RGB").resize((256, 64), Image.Resampling.BILINEAR)
        tensor = self.to_tensor(image).sub_(0.5).div_(0.5).unsqueeze(0).to(self.device)
        stn_input = self.functional.interpolate(tensor, [32, 64], mode="bilinear", align_corners=True)
        _, control_points = self.model.stn_head(stn_input)
        rectified, _ = self.model.tps(tensor, control_points)
        return self.model.encoder(rectified).contiguous()

    def predict(self, image: Any) -> tuple[str, None]:
        with self.torch.inference_mode():
            features = self.encode(image)
            if self.beam_width == 1:
                predicted, _ = self.model.decoder.sample([features, None, None])
            else:
                predicted, _ = self.model.decoder.beam_search(features, self.beam_width, VOCABULARY.index("EOS"))
            if tuple(predicted.shape) != (1, MAX_LENGTH):
                raise RuntimeError(f"unexpected ASTER prediction shape: {tuple(predicted.shape)}")
            answer = decode_token_ids(predicted[0].tolist())
        self.synchronize()
        return answer, None


def recognize_sample(ocr: AsterOCR, sample: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image

    answer, confidence, error = "", None, None
    stage = "image_open"
    ocr.synchronize()
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
    ocr.synchronize()
    latency_ms = (time.perf_counter() - started) * 1000
    label = sample["label"]
    return {key: sample[key] for key in ("id", "path", "source", "sha256", "label")} | {
        "answer": answer, "exact": answer == label, "case_insensitive_exact": answer.casefold() == label.casefold(),
        "distance": reference().levenshtein(answer, label), "characters": len(label),
        "latency_ms": latency_ms, "confidence": confidence, "error": error,
    }


def evaluate(manifest_path: Path, output: Path, model_dir: Path, beam_width: int = 5,
             device: str = "cpu", checkpoint_every: int = 100) -> tuple[Path, Path]:
    if checkpoint_every <= 0:
        raise ValueError("checkpoint-every must be positive")
    output, model_dir, manifest_path = output.resolve(), model_dir.resolve(), manifest_path.resolve()
    json_path, jsonl_path = output.with_suffix(".json"), output.with_suffix(".jsonl")
    if output.exists() or json_path.exists() or jsonl_path.exists():
        raise FileExistsError(f"output already exists: {output} (or {json_path}/{jsonl_path})")
    manifest, samples = reference().load_and_check_manifest(manifest_path)
    manifest["manifest_path"] = str(manifest_path)
    ocr = AsterOCR(model_dir, beam_width, device)
    identity = {
        "model": "ASTER PyTorch port", "model_dir": str(model_dir), "model_sha256": WEIGHT_SHA256,
        "source_commit": SOURCE_COMMIT, "source_tree_sha256": SOURCE_TREE_SHA256,
        "compatibility_manifest_sha256": MANIFEST_SHA256,
        "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
        "runtime_versions": runtime_versions(), "recognition_options": recognition_options(beam_width, device),
        "evaluator_sha256": sha256_file(Path(__file__)),
        "reference_evaluator_sha256": sha256_file(Path(__file__).with_name("evaluate_public_text.py")),
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    warmup_row = recognize_sample(ocr, samples[0])
    warmup = {"sample_id": samples[0]["id"], "seconds": warmup_row["latency_ms"] / 1000,
              "error": warmup_row["error"], "excluded_from_sample_timings": True}
    if warmup_row["error"] is not None and warmup_row["error"]["stage"] == "recognition":
        raise RuntimeError(f"ASTER warmup failed; evaluation is not started: {warmup_row['error']}")
    rows: list[dict[str, Any]] = []
    output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint() -> None:
        reference().atomic_jsonl(jsonl_path, rows)
        summary = reference().summarize(rows, manifest, ocr.load_seconds, ocr.providers)
        chars = sum(row["characters"] for row in rows)
        failures = collections.Counter(row["error"]["type"] for row in rows if row["error"] is not None)
        for source, group_summary in summary["by_source"].items():
            group = [row for row in rows if row["source"] == source]
            group_summary["failed_samples"] = sum(row["error"] is not None for row in group)
            group_summary["empty_answers"] = sum(row["answer"] == "" for row in group)
        summary.update({
            "schema_version": 1, "ocr_backend": "ASTER PyTorch port", "model": ocr.metadata,
            "evaluation_identity": identity, "manifest_sha256": identity["manifest_sha256"],
            "model_sha256": WEIGHT_SHA256, "runtime_versions": identity["runtime_versions"],
            "recognition_options": identity["recognition_options"], "evaluator_sha256": identity["evaluator_sha256"],
            "started_at_utc": identity["started_at_utc"], "completed": len(rows), "total_samples": len(samples),
            "status": "complete" if len(rows) == len(samples) else "incomplete",
            "literal_exact_match": sum(row["exact"] for row in rows) / len(rows) if rows else None,
            "case_insensitive_exact_match": sum(row["case_insensitive_exact"] for row in rows) / len(rows) if rows else None,
            "character_error_rate": sum(row["distance"] for row in rows) / chars if chars else None,
            "latency": reference().timing([row["latency_ms"] for row in rows]),
            "failed_samples": sum(failures.values()), "failure_counts": dict(sorted(failures.items())),
            "empty_answers": sum(row["answer"] == "" for row in rows), "warmup": warmup,
            "predictions_sha256": sha256_file(jsonl_path),
        })
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
    parser.add_argument("--model-dir", type=Path, required=True, help="pinned ASTER weights, official-source and compat directories")
    parser.add_argument("--beam-width", type=int, default=5, help="official beam search width; 1 selects greedy sample (default: 5)")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--checkpoint-every", type=int, default=100)
    args = parser.parse_args()
    summary_path, predictions_path = evaluate(args.manifest, args.output, args.model_dir,
                                              args.beam_width, args.device, args.checkpoint_every)
    print(json.dumps({"summary": str(summary_path), "predictions": str(predictions_path)}))


if __name__ == "__main__":
    main()
