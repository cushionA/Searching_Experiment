#!/usr/bin/env python3
"""Bounded, auditable warm-start CTC adaptation of the pinned CAPTCHA CRNN.

Only frozen train-fold images receive gradients. Validation selects a checkpoint;
test is scored for the unchanged warm start and the final selected checkpoint.
This pilot's public corpora were already used for model selection, so its internal
test fold is not a fresh external final evaluation. No Hub Python is executed.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.util
import json
import math
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_finetune_{name}", Path(__file__).with_name(name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crnn = sibling("evaluate_crnn_text")
splitting = sibling("split_crnn_text")
VOCABULARY = crnn.VOCABULARY
CHAR_TO_INDEX = {char: index + 1 for index, char in enumerate(VOCABULARY)}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def validate_targets(samples: list[dict[str, Any]], timesteps: int = 37) -> None:
    for sample in samples:
        label = sample["label"]
        if any(char not in CHAR_TO_INDEX for char in label):
            raise ValueError(f"label outside fixed pretrained vocabulary: {sample['id']}")
        minimum_frames = len(label) + sum(left == right for left, right in zip(label, label[1:]))
        if minimum_frames > timesteps:
            raise ValueError(f"label cannot fit CTC timesteps: {sample['id']}")


def setup_runtime(device_name: str, seed: int) -> tuple[Any, Any]:
    import torch
    import numpy as np
    torch.set_num_threads(2)
    if torch.get_num_interop_threads() != 1:
        torch.set_num_interop_threads(1)
    random.seed(seed)
    np.random.seed(seed % 2 ** 32)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    # CUDA CTC backward can use nondeterministic accumulation. Warn rather than
    # claim bitwise reproducibility or silently switch training to CPU.
    torch.use_deterministic_algorithms(True, warn_only=True)
    if device_name == "auto":
        device_name = "cuda" if torch.cuda.is_available() else "cpu"
    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    return torch, torch.device(device_name)


def load_pretrained(torch: Any, model_dir: Path, device: Any) -> tuple[Any, dict[str, Any]]:
    from safetensors.torch import load_file
    files = crnn.verify_model_files(model_dir)
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    processor = json.loads((model_dir / "processor_config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "captcha_crnn" or config.get("num_chars") != 63 or processor.get("vocab") != VOCABULARY:
        raise ValueError("unexpected pinned pretrained configuration")
    model = crnn.create_model(torch, SimpleNamespace(num_chars=63))
    model.load_state_dict(load_file(str(model_dir / crnn.MODEL_FILENAME), device="cpu"), strict=True)
    return model.to(device=device, dtype=torch.float32), files


def preprocess(image: Any) -> Any:
    from torchvision.transforms.functional import to_tensor
    return to_tensor(image.convert("L").resize((150, 40)))


class ImageDataset:
    """Only the explicit partition passed here is visible to a loader."""
    def __init__(self, samples: list[dict[str, Any]], train_contrast: float = 0.0):
        self.samples, self.train_contrast = samples, train_contrast

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[Any, dict[str, Any]]:
        from PIL import Image, ImageEnhance
        sample = self.samples[index]
        with Image.open(sample["path"]) as image:
            image = image.convert("L")
            if self.train_contrast:
                image = ImageEnhance.Contrast(image).enhance(random.uniform(1 - self.train_contrast, 1 + self.train_contrast))
            tensor = preprocess(image)
        return tensor, sample


def collate(batch: list[tuple[Any, dict[str, Any]]]) -> tuple[Any, list[dict[str, Any]]]:
    import torch
    return torch.stack([row[0] for row in batch]), [row[1] for row in batch]


def ctc_loss(torch: Any, logits: Any, samples: list[dict[str, Any]], loss_function: Any) -> Any:
    if tuple(logits.shape[1:]) != (37, 63) or not torch.isfinite(logits).all().item():
        raise RuntimeError("unexpected/nonfinite CTC logits")
    # Lengths come from model output and training targets, never from test labels.
    targets = torch.tensor([CHAR_TO_INDEX[char] for sample in samples for char in sample["label"]],
                           dtype=torch.long, device=logits.device)
    target_lengths = torch.tensor([len(sample["label"]) for sample in samples], dtype=torch.long)
    input_lengths = torch.full((len(samples),), logits.shape[1], dtype=torch.long)
    log_probs = logits.log_softmax(dim=-1).transpose(0, 1)
    # Avoid the cuDNN-only CTC path's length constraints and algorithm selection.
    manager = torch.backends.cudnn.flags(enabled=False) if logits.device.type == "cuda" else contextlib.nullcontext()
    with manager:
        loss = loss_function(log_probs, targets, input_lengths, target_lengths)
    if not torch.isfinite(loss).item():
        raise RuntimeError("nonfinite CTC loss; zero_infinity is deliberately disabled")
    return loss


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def score(group: list[dict[str, Any]]) -> dict[str, Any]:
        count, chars = len(group), sum(len(row["label"]) for row in group)
        correct = sum(row["answer"] == row["label"] for row in group)
        distance = sum(row["distance"] for row in group)
        return {"samples": count, "exact_count": correct, "exact_match": correct / count if count else None,
                "reference_characters": chars, "edit_distance": distance,
                "character_error_rate": distance / chars if chars else None,
                "case_insensitive_exact_count": sum(row["answer"].casefold() == row["label"].casefold() for row in group)}
    return score(rows) | {"by_source": {source: score([row for row in rows if row["source"] == source])
                                       for source in sorted({row["source"] for row in rows})}}


def evaluate(torch: Any, model: Any, samples: list[dict[str, Any]], device: Any,
             batch_size: int, predictions_path: Path | None = None) -> dict[str, Any]:
    loader = torch.utils.data.DataLoader(ImageDataset(samples), batch_size=batch_size,
                                         shuffle=False, num_workers=0, collate_fn=collate)
    model.eval()
    rows = []
    started = time.perf_counter()
    with torch.inference_mode():
        for images, group in loader:
            logits = model(images.to(device=device, dtype=torch.float32))
            if tuple(logits.shape) != (len(group), 37, 63) or not torch.isfinite(logits).all().item():
                raise RuntimeError("invalid evaluation logits")
            answers = crnn.greedy_ctc_decode(torch, logits.cpu())
            for sample, answer in zip(group, answers, strict=True):
                rows.append({key: sample[key] for key in ("id", "source", "sha256", "label")} |
                            {"answer": answer, "exact": answer == sample["label"],
                             "distance": crnn.reference().levenshtein(answer, sample["label"])})
    if predictions_path is not None:
        crnn.reference().atomic_jsonl(predictions_path, rows)
    return aggregate(rows) | {"seconds": time.perf_counter() - started,
                             "device": str(device), "evaluation_batch_size": batch_size,
                             "decoder": "greedy CTC; no normalization or label-derived length"}


def selection_key(metrics: dict[str, Any]) -> tuple[int, float]:
    return metrics["exact_count"], -metrics["character_error_rate"]


def save_checkpoint(torch: Any, model: Any, output: Path, metadata: dict[str, str]) -> str:
    from safetensors.torch import save_file
    temporary = output.with_name(output.name + ".tmp")
    state = {key: value.detach().to("cpu").contiguous().clone() for key, value in model.state_dict().items()}
    save_file(state, str(temporary), metadata=metadata)
    temporary.replace(output)
    return crnn.sha256_file(output)


def save_training_state(torch: Any, optimizer: Any, generator: Any, output: Path,
                        last_epoch: int, best_epoch: int, seed: int, device: Any) -> None:
    """Safe-loadable tensors/builtins only; no custom classes or NumPy objects."""
    temporary = output.with_name(output.name + ".tmp")
    torch.save({"optimizer": optimizer.state_dict(), "last_epoch": last_epoch,
                "best_epoch": best_epoch, "seed": seed, "torch_rng_state": torch.get_rng_state(),
                "cuda_rng_state": torch.cuda.get_rng_state_all() if device.type == "cuda" else [],
                "python_rng_state": random.getstate(), "loader_generator_state": generator.get_state()},
               temporary)
    temporary.replace(output)


def train(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.max_epochs <= 100 or not 0 < args.max_seconds <= 1200:
        raise ValueError("training budget must be 1..100 epochs and at most 1200 training seconds")
    if args.batch_size <= 0 or args.learning_rate <= 0 or args.patience <= 0 or not 0 <= args.train_contrast <= 0.2:
        raise ValueError("invalid training settings")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite run: {args.output}")
    manifest_path, split_path, model_dir = (path.resolve() for path in (args.manifest, args.split, args.model_dir))
    _, samples = crnn.reference().load_and_check_manifest(manifest_path)
    manifest_sha = crnn.sha256_file(manifest_path)
    split = json.loads(split_path.read_text(encoding="utf-8"))
    partitions = splitting.validate_split(samples, split, manifest_sha)
    validate_targets(samples)
    if args.seed != split["seed"]:
        raise ValueError("training seed must equal the frozen split seed")
    torch, device = setup_runtime(args.device, args.seed)
    model, files = load_pretrained(torch, model_dir, device)
    output = args.output.resolve()
    output.mkdir(parents=True)
    code_files = {filename: crnn.sha256_file(Path(__file__).with_name(filename)) for filename in
                  ("finetune_crnn_text.py", "split_crnn_text.py", "evaluate_crnn_text.py", "evaluate_public_text.py")}
    config = {
        "schema_version": 1, "started_at_utc": now(), "manifest_sha256": manifest_sha,
        "split_sha256": crnn.sha256_file(split_path), "seed": args.seed,
        "pretrained": {"repository": crnn.REPOSITORY, "revision": crnn.REVISION, "files": files},
        "code_sha256": code_files, "runtime_versions": crnn.runtime_versions(),
        "device": str(device), "gpu": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
        "torch_intra_op_threads": torch.get_num_threads(), "torch_inter_op_threads": torch.get_num_interop_threads(),
        "deterministic_algorithms": "enabled with warn_only=True; CUDA CTC backward may be nondeterministic",
        "training": {"max_epochs": args.max_epochs, "max_seconds": args.max_seconds, "patience": args.patience,
                     "batch_size": args.batch_size, "optimizer": "AdamW", "learning_rate": args.learning_rate,
                     "weight_decay": 0.01, "gradient_clip_norm": 5.0, "dtype": "float32", "mixed_precision": False,
                     "loss": "CTCLoss(blank=0,reduction=mean,zero_infinity=False)", "train_contrast": args.train_contrast,
                     "augmentation": "none" if not args.train_contrast else "train-only random contrast factor in [1-amplitude,1+amplitude]",
                     "full_model_trainable": True, "data_loader_workers": 0},
        "preprocessing": "official whole-image grayscale PIL bicubic resize150x40; float32 [0,1]",
        "vocabulary": VOCABULARY, "blank_index": 0, "output_timesteps": 37,
        "checkpoint_selection": "validation exact_count descending, then CER ascending; includes unchanged epoch0; ties keep earlier epoch",
        "test_use": "unchanged pretrained baseline once; final validation-selected checkpoint once; never gradients/early stopping",
        "partition_counts": split["by_source"], "limitation": splitting.LIMITATION,
        "license": "pretrained MIT; public data licensing/provenance preserved in the frozen manifest",
    }
    crnn.reference().atomic_json(output / "config.json", config)
    crnn.reference().atomic_json(output / "split.json", split)
    # Preserve the exact portable manifest bytes; relative paths remain relative
    # to the input manifest's dataset root, not the training-result directory.
    (output / "input-manifest.json").write_bytes(manifest_path.read_bytes())
    for filename in code_files:
        (output / filename).write_bytes(Path(__file__).with_name(filename).read_bytes())
    print("scoring unchanged warm-start validation and test", file=sys.stderr, flush=True)
    baseline_val = evaluate(torch, model, partitions["validation"], device, args.batch_size, output / "baseline-validation.jsonl")
    baseline_test = evaluate(torch, model, partitions["test"], device, args.batch_size, output / "baseline-test.jsonl")
    crnn.reference().atomic_json(output / "baseline.json", {"validation": baseline_val, "test": baseline_test})
    checkpoint_metadata = {"pretrained_repository": crnn.REPOSITORY, "pretrained_revision": crnn.REVISION,
                           "pretrained_sha256": files[crnn.MODEL_FILENAME]["sha256"],
                           "manifest_sha256": manifest_sha, "split_sha256": config["split_sha256"],
                           "training_code_sha256": code_files["finetune_crnn_text.py"], "vocabulary": VOCABULARY}
    best_path = output / "best.safetensors"
    best_sha = save_checkpoint(torch, model, best_path, checkpoint_metadata | {"selected_epoch": "0"})
    best_val, best_epoch, stale = baseline_val, 0, 0
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    loss_function = torch.nn.CTCLoss(blank=0, reduction="mean", zero_infinity=False)
    generator = torch.Generator().manual_seed(args.seed)
    loader = torch.utils.data.DataLoader(ImageDataset(partitions["train"], args.train_contrast),
                                         batch_size=args.batch_size, shuffle=True, num_workers=0,
                                         collate_fn=collate, generator=generator)
    log: list[dict[str, Any]] = []
    training_started, stopping_reason = time.perf_counter(), "max_epochs"
    for epoch in range(1, args.max_epochs + 1):
        model.train()
        total_loss, seen, batches = 0.0, 0, 0
        budget_reached = False
        epoch_started = time.perf_counter()
        for images, group in loader:
            if time.perf_counter() - training_started >= args.max_seconds:
                budget_reached = True
                break
            optimizer.zero_grad(set_to_none=True)
            logits = model(images.to(device=device, dtype=torch.float32))
            loss = ctc_loss(torch, logits, group, loss_function)
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0, error_if_nonfinite=True)
            if not math.isfinite(float(norm)):
                raise RuntimeError("nonfinite gradient norm")
            optimizer.step()
            total_loss += float(loss.detach()) * len(group)
            seen += len(group)
            batches += 1
        if not seen:
            stopping_reason = "time_budget"
            break
        val = evaluate(torch, model, partitions["validation"], device, args.batch_size)
        improved = selection_key(val) > selection_key(best_val)
        if improved:
            best_val, best_epoch, stale = val, epoch, 0
            best_sha = save_checkpoint(torch, model, best_path, checkpoint_metadata | {"selected_epoch": str(epoch)})
        else:
            stale += 1
        row = {"epoch": epoch, "train_samples_seen": seen, "train_batches": batches,
               "complete_epoch": seen == len(partitions["train"]), "train_mean_ctc_loss": total_loss / seen,
               "validation": val, "selected_as_best": improved, "best_epoch": best_epoch,
               "epoch_seconds": time.perf_counter() - epoch_started,
               "training_elapsed_seconds": time.perf_counter() - training_started}
        log.append(row)
        crnn.reference().atomic_jsonl(output / "epochs.jsonl", log)
        crnn.reference().atomic_json(output / "progress.json", {"status": "training", "latest": row,
                                                                 "best_checkpoint_sha256": best_sha})
        print(json.dumps(row), file=sys.stderr, flush=True)
        if budget_reached or time.perf_counter() - training_started >= args.max_seconds:
            stopping_reason = "time_budget"
            break
        if stale >= args.patience:
            stopping_reason = "validation_patience"
            break
    # Preserve continuation data independently of the selected checkpoint. This
    # tensor/builtin-only file can be inspected with torch.load(weights_only=True).
    last_sha = save_checkpoint(torch, model, output / "last.safetensors", checkpoint_metadata |
                               {"selected_epoch": str(log[-1]["epoch"] if log else 0), "role": "last training state, not validation winner"})
    save_training_state(torch, optimizer, generator, output / "last-training-state.pt",
                        log[-1]["epoch"] if log else 0, best_epoch, args.seed, device)
    from safetensors.torch import load_file
    model.load_state_dict(load_file(str(best_path), device="cpu"), strict=True)
    final_val = evaluate(torch, model, partitions["validation"], device, args.batch_size, output / "finetuned-validation.jsonl")
    if selection_key(final_val) != selection_key(best_val):
        raise RuntimeError("selected checkpoint reload changed validation score")
    final_test = evaluate(torch, model, partitions["test"], device, args.batch_size, output / "finetuned-test.jsonl")
    checkpoint = {"selected_epoch": best_epoch, "best_sha256": best_sha, "last_sha256": last_sha,
                  "selected_using": "validation only", "unchanged_epoch0_can_win": True}
    result = {"schema_version": 1, "status": "complete", "started_at_utc": config["started_at_utc"],
              "completed_at_utc": now(), "manifest_sha256": manifest_sha, "split_sha256": config["split_sha256"],
              "checkpoint": checkpoint, "stopping_reason": stopping_reason,
              "training_epochs": len(log), "training_elapsed_seconds": time.perf_counter() - training_started,
              "baseline": {"validation": baseline_val, "test": baseline_test},
              "finetuned": {"validation": final_val, "test": final_test},
              "test_exact_count_delta": final_test["exact_count"] - baseline_test["exact_count"],
              "by_source_test_exact_count_delta": {source: final_test["by_source"][source]["exact_count"] - baseline_test["by_source"][source]["exact_count"]
                                                   for source in baseline_test["by_source"]},
              "limitation": splitting.LIMITATION, "config_sha256": crnn.sha256_file(output / "config.json")}
    artifacts = {path.name: {"sha256": crnn.sha256_file(path), "bytes": path.stat().st_size}
                 for path in sorted(output.iterdir()) if path.is_file() and path.name not in ("progress.json", "result.json")}
    result["artifacts"] = artifacts
    crnn.reference().atomic_json(output / "result.json", result)
    crnn.reference().atomic_json(output / "progress.json", {"status": "complete", "checkpoint": checkpoint})
    return result


def smoke_check(model_dir: Path, output: Path) -> dict[str, Any]:
    """Two synthetic images: finite gradients, weight update, safe reload parity."""
    if output.exists():
        raise FileExistsError(f"refusing to overwrite smoke directory: {output}")
    from PIL import Image, ImageDraw
    torch, device = setup_runtime("cpu", 20261006)
    model, files = load_pretrained(torch, model_dir, device)
    images = []
    samples = []
    for index, label in enumerate(("a1", "b2")):
        image = Image.new("L", (150, 40), color=230 - index * 30)
        ImageDraw.Draw(image).text((20, 10), label, fill=10)
        images.append(preprocess(image))
        samples.append({"id": f"synthetic-{index}", "label": label})
    validate_targets(samples)
    tensor = torch.stack(images)
    before = model.classifier.weight.detach().clone()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    model.train()
    logits = model(tensor)
    loss = ctc_loss(torch, logits, samples, torch.nn.CTCLoss(blank=0, reduction="mean", zero_infinity=False))
    loss.backward()
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0, error_if_nonfinite=True)
    optimizer.step()
    weight_delta = float((model.classifier.weight.detach() - before).abs().max())
    if weight_delta <= 0 or not math.isfinite(float(norm)):
        raise RuntimeError("smoke step did not update finite model parameters")
    model.eval()
    with torch.inference_mode():
        expected = model(tensor)
    output.mkdir(parents=True)
    path = output / "smoke.safetensors"
    digest = save_checkpoint(torch, model, path, {"role": "synthetic smoke only; not benchmark result"})
    state_path = output / "smoke-training-state.pt"
    generator = torch.Generator().manual_seed(20261006)
    save_training_state(torch, optimizer, generator, state_path, 1, 1, 20261006, device)
    continuation = torch.load(state_path, map_location="cpu", weights_only=True)
    if continuation["last_epoch"] != 1 or not continuation["optimizer"]["state"]:
        raise RuntimeError("weights_only continuation-state reload failed")
    from safetensors.torch import load_file
    restored = crnn.create_model(torch, SimpleNamespace(num_chars=63))
    restored.load_state_dict(load_file(str(path)), strict=True)
    restored.eval()
    with torch.inference_mode():
        actual = restored(tensor)
    if not torch.equal(expected, actual):
        raise RuntimeError("safetensors strict reload changed CPU logits")
    result = {"status": "passed", "synthetic_images": 2, "training_steps": 1,
              "ctc_loss": float(loss.detach()), "gradient_norm_before_clip": float(norm),
              "classifier_weight_max_change": weight_delta, "logits_shape": list(logits.shape),
              "checkpoint_sha256": digest, "strict_reload_bitwise_logits_match": True,
              "continuation_state_weights_only_reload": True,
              "pretrained_sha256": files[crnn.MODEL_FILENAME]["sha256"],
              "not_a_real_dataset_training_result": True}
    crnn.reference().atomic_json(output / "smoke.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--seed", type=int, default=20261006)
    parser.add_argument("--max-epochs", type=int, default=20)
    parser.add_argument("--max-seconds", type=float, default=1200)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--train-contrast", type=float, default=0.0, help="optional train-only contrast amplitude; default no augmentation")
    parser.add_argument("--smoke-check", action="store_true")
    args = parser.parse_args()
    if args.smoke_check:
        result = smoke_check(args.model_dir.resolve(), args.output.resolve())
    else:
        if args.manifest is None or args.split is None:
            parser.error("training requires --manifest and --split")
        result = train(args)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
