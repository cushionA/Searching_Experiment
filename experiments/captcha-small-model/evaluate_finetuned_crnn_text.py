#!/usr/bin/env python3
"""Safely score a validation-selected CRNN checkpoint on a frozen internal fold.

The full original manifest, deterministic grouped split and training config are
required. Only test or validation images can be scored. Safetensors and pinned
local architecture code are used; downloaded Python and pickle are never loaded.
The public corpora have already been used for model selection, so this does not
establish performance on a fresh external test corpus.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_selected_crnn_{name}", Path(__file__).with_name(name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crnn = sibling("evaluate_crnn_text")
splitting = sibling("split_crnn_text")


def verify_checkpoint_sha(path: Path, expected: str) -> str:
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValueError("checkpoint SHA-256 must be an explicit 64-character lowercase hex digest")
    actual = crnn.sha256_file(path)
    if actual != expected:
        raise ValueError(f"checkpoint SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def validate_provenance(metadata: dict[str, str], config: dict[str, Any],
                        manifest_sha: str, split_sha: str,
                        files: dict[str, Any], split: dict[str, Any]) -> int:
    """Bind checkpoint, config, immutable source pins and the exact frozen split."""
    pretrained = config.get("pretrained", {})
    if pretrained.get("repository") != crnn.REPOSITORY or pretrained.get("revision") != crnn.REVISION:
        raise ValueError("training config pretrained source mismatch")
    if pretrained.get("files") != files:
        raise ValueError("training config pretrained file pins mismatch")
    if config.get("manifest_sha256") != manifest_sha or config.get("split_sha256") != split_sha:
        raise ValueError("training config manifest/split SHA-256 mismatch")
    if config.get("partition_counts") != split.get("by_source") or config.get("seed") != split.get("seed"):
        raise ValueError("training config frozen split counts/seed mismatch")
    if config.get("vocabulary") != crnn.VOCABULARY or config.get("blank_index") != 0 or config.get("output_timesteps") != 37:
        raise ValueError("training config decoder/architecture mismatch")
    code_pins = config.get("code_sha256", {})
    training_sha = code_pins.get("finetune_crnn_text.py")
    if not isinstance(training_sha, str) or re.fullmatch(r"[0-9a-f]{64}", training_sha) is None:
        raise ValueError("missing training code SHA-256")
    # No training code is imported or executed. Its embedded fingerprint binds
    # the selected weight to the supplied training record; inference and split
    # code must match the immutable implementations recorded by that run.
    for filename in ("evaluate_crnn_text.py", "evaluate_public_text.py", "split_crnn_text.py"):
        if code_pins.get(filename) != crnn.sha256_file(Path(__file__).with_name(filename)):
            raise ValueError(f"training config trusted inference source mismatch: {filename}")
    expected_metadata = {
        "pretrained_repository": crnn.REPOSITORY, "pretrained_revision": crnn.REVISION,
        "pretrained_sha256": files[crnn.MODEL_FILENAME]["sha256"],
        "manifest_sha256": manifest_sha, "split_sha256": split_sha,
        "training_code_sha256": training_sha, "vocabulary": crnn.VOCABULARY,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise ValueError(f"checkpoint provenance mismatch: {key}")
    epoch = metadata.get("selected_epoch", "")
    if re.fullmatch(r"[0-9]+", epoch) is None:
        raise ValueError("checkpoint requires a nonnegative selected epoch")
    if int(epoch) > config.get("training", {}).get("max_epochs", -1):
        raise ValueError("checkpoint selected epoch exceeds recorded training budget")
    if metadata.get("role") is not None:
        raise ValueError("checkpoint is a continuation/smoke state rather than the validation-selected checkpoint")
    return int(epoch)


def load_selected_model(torch: Any, checkpoint: Path, device: Any) -> Any:
    from safetensors.torch import load_file
    model = crnn.create_model(torch, SimpleNamespace(num_chars=63))
    state = load_file(str(checkpoint), device="cpu")
    expected = model.state_dict()
    if set(state) != set(expected):
        raise ValueError("checkpoint state keys do not match the trusted CRNN")
    for key, value in state.items():
        if value.shape != expected[key].shape or value.dtype != expected[key].dtype:
            raise ValueError(f"checkpoint shape/dtype mismatch: {key}")
        if value.is_floating_point() and not torch.isfinite(value).all().item():
            raise ValueError(f"checkpoint contains nonfinite weights: {key}")
    model.load_state_dict(state, strict=True)
    model.to(device=device, dtype=torch.float32).eval()
    model.requires_grad_(False)
    return model


def setup_runtime(device_name: str) -> tuple[Any, Any]:
    import torch
    torch.set_num_threads(2)
    if torch.get_num_interop_threads() != 1:
        torch.set_num_interop_threads(1)
    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True, warn_only=True)
    return torch, torch.device(device_name)


def infer_fold(torch: Any, model: Any, samples: list[dict[str, Any]],
               device: Any, batch_size: int, predictions_path: Path) -> dict[str, Any]:
    from PIL import Image
    from torchvision.transforms.functional import to_tensor
    # Reuse the immutable original evaluator's exact preprocessing, including
    # shape/dtype assertions, without loading its original model a second time.
    processor = SimpleNamespace(torch=torch, to_tensor=to_tensor)
    rows = []
    started = time.perf_counter()
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(samples), batch_size):
            group = samples[start:start + batch_size]
            tensors = []
            for sample in group:
                with Image.open(sample["path"]) as image:
                    tensors.append(crnn.CaptchaCRNNOCR.preprocess(processor, image)[0])
            logits = model(torch.stack(tensors).to(device=device, dtype=torch.float32))
            if tuple(logits.shape) != (len(group), 37, 63) or not torch.isfinite(logits).all().item():
                raise RuntimeError("invalid selected CRNN logits")
            answers = crnn.greedy_ctc_decode(torch, logits.cpu())
            for sample, answer in zip(group, answers, strict=True):
                rows.append({key: sample[key] for key in ("id", "source", "sha256", "label")} |
                            {"answer": answer, "exact": answer == sample["label"],
                             "distance": crnn.reference().levenshtein(answer, sample["label"])})
    crnn.reference().atomic_jsonl(predictions_path, rows)

    def score(group: list[dict[str, Any]]) -> dict[str, Any]:
        chars = sum(len(row["label"]) for row in group)
        correct = sum(row["exact"] for row in group)
        distance = sum(row["distance"] for row in group)
        return {"samples": len(group), "exact_count": correct, "exact_match": correct / len(group),
                "reference_characters": chars, "edit_distance": distance,
                "character_error_rate": distance / chars,
                "case_insensitive_exact_count": sum(row["answer"].casefold() == row["label"].casefold() for row in group)}

    return score(rows) | {
        "by_source": {source: score([row for row in rows if row["source"] == source])
                      for source in sorted({row["source"] for row in rows})},
        "seconds": time.perf_counter() - started, "device": str(device),
        "evaluation_batch_size": batch_size,
        "decoder": "greedy CTC; no normalization or label-derived length",
    }


def evaluate(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.fold not in ("test", "validation"):
        raise ValueError("only frozen test or validation folds may be evaluated")
    if args.device not in ("cpu", "cuda") or args.batch_size <= 0:
        raise ValueError("device must be explicit cpu/cuda and batch size positive")
    output = args.output.resolve()
    summary_path, predictions_path = output.with_suffix(".json"), output.with_suffix(".jsonl")
    if output.exists() or summary_path.exists() or predictions_path.exists():
        raise FileExistsError(f"refusing to overwrite evaluation output: {output}")
    checkpoint, model_dir, manifest_path, split_path = (path.resolve() for path in
                                                      (args.checkpoint, args.model_dir, args.manifest, args.split))
    config_path = (args.train_config or checkpoint.parent / "config.json").resolve()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    checkpoint_sha = verify_checkpoint_sha(checkpoint, args.checkpoint_sha256)
    files = crnn.verify_model_files(model_dir)
    manifest, samples = crnn.reference().load_and_check_manifest(manifest_path)
    manifest_sha, split_sha = crnn.sha256_file(manifest_path), crnn.sha256_file(split_path)
    split = json.loads(split_path.read_text(encoding="utf-8"))
    partitions = splitting.validate_split(samples, split, manifest_sha)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    from safetensors import safe_open
    with safe_open(checkpoint, framework="pt", device="cpu") as handle:
        metadata = handle.metadata() or {}
    selected_epoch = validate_provenance(metadata, config, manifest_sha, split_sha, files, split)
    torch, device = setup_runtime(args.device)
    model = load_selected_model(torch, checkpoint, device)
    output.parent.mkdir(parents=True, exist_ok=True)
    metrics = infer_fold(torch, model, partitions[args.fold], device, args.batch_size, predictions_path)
    helpers = ("evaluate_finetuned_crnn_text.py", "evaluate_crnn_text.py",
               "evaluate_public_text.py", "split_crnn_text.py")
    summary = {
        "schema_version": 1, "status": "complete", "started_at_utc": started_at,
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": f"Frozen internal {args.fold} fold only; training images excluded; entire input manifest verified.",
        "validation_is_checkpoint_selection_data": args.fold == "validation",
        "limitation": splitting.LIMITATION, "fold": args.fold,
        "full_manifest_samples": len(samples), "evaluated_samples": len(partitions[args.fold]),
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha,
                     "dataset": manifest.get("dataset"), "provenance": manifest.get("provenance"), "license": manifest.get("license")},
        "split": {"path": str(split_path), "sha256": split_sha, "seed": split["seed"],
                  "algorithm": split["algorithm"], "by_source": split["by_source"]},
        "checkpoint": {"path": str(checkpoint), "sha256": checkpoint_sha, "bytes": checkpoint.stat().st_size,
                       "selected_epoch": selected_epoch, "selected_using": "validation only", "embedded_metadata": metadata},
        "pretrained": {"repository": crnn.REPOSITORY, "revision": crnn.REVISION, "files": files},
        "training_config": {"path": str(config_path), "sha256": crnn.sha256_file(config_path),
                            "config": config},
        "runtime_versions": crnn.runtime_versions(), "device": str(device),
        "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        "torch_intra_op_threads": torch.get_num_threads(), "torch_inter_op_threads": torch.get_num_interop_threads(),
        "inference_options": {"dtype": "float32", "batch_size": args.batch_size, "gradients": False,
                              "model_training_mode": False, "whole_image_as_single_text_line": True,
                              "preprocessing": "official PIL grayscale bicubic150x40 and float32 [0,1]",
                              "decoder": "greedy CTC; fixed63 classes; no prediction normalization or label-derived length"},
        "code_sha256": {filename: crnn.sha256_file(Path(__file__).with_name(filename)) for filename in helpers},
        "metrics": metrics, "predictions_sha256": crnn.sha256_file(predictions_path),
    }
    crnn.reference().atomic_json(summary_path, summary)
    return summary_path, predictions_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True, help="original nine pinned CRNN files")
    parser.add_argument("--checkpoint", type=Path, required=True, help="validation-selected best.safetensors")
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--train-config", type=Path, help="defaults to checkpoint directory/config.json")
    parser.add_argument("--manifest", type=Path, required=True, help="full unchanged original manifest, not a subset")
    parser.add_argument("--split", type=Path, required=True, help="the unchanged frozen split JSON")
    parser.add_argument("--fold", choices=("test", "validation"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="new output prefix (.json and .jsonl)")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    summary_path, predictions_path = evaluate(args)
    print(json.dumps({"summary": str(summary_path), "predictions": str(predictions_path)}))


if __name__ == "__main__":
    main()
