"""Offline bulk evaluation of supplied labeled images.

Input JSON has optional classes/samples and optional board ``cases``. Image
paths are relative to the JSON. Cases declare ``selection_rule`` as
``select_all_margin_positive`` or ``known_count_topk``. The latter requires an
independently known requested_count and is a diagnostic, not solution rate.
Labels/references are supplied independently; predictions never become truth.
Public supplied labels are not independently verified, and synthetic board
selection results do not establish performance on live CAPTCHA boards.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

from PIL import Image

torch = None
model_helper = None


DEFAULT_MODELS = "TinyCLIP-ViT-40M-32-Text-19M,MobileCLIP2-S0,MobileCLIP2-S2"
MOE_MODEL = "MoE-ViE-B16"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def feature_similarities(image_features, text_features):
    """Preserve the wider encoder output dtype when scoring mixed precision."""
    dtype = torch.promote_types(image_features.dtype, text_features.dtype)
    return image_features.to(dtype=dtype) @ text_features.to(dtype=dtype).T


def load_input(input_path: Path) -> tuple[dict, list[dict], list[dict], list[str]]:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    classes = data.get("classes")
    samples = data.get("samples")
    cases = data.get("cases", [])
    classes = [] if classes is None else classes
    samples = [] if samples is None else samples
    if not isinstance(classes, list) or not isinstance(samples, list):
        raise ValueError("classes and samples must be arrays")
    labels = [c.get("label") for c in classes]
    if any(not isinstance(label, str) or not label for label in labels) or len(set(labels)) != len(labels):
        raise ValueError("Class labels must be unique nonempty strings")
    for c in classes:
        if not isinstance(c.get("prompts"), list) or not c["prompts"] or any(not isinstance(p, str) or not p for p in c["prompts"]):
            raise ValueError(f"Class {c.get('label')!r} needs one or more nonempty prompts")
    source_classes = data.get("source_classes", {})
    if not isinstance(source_classes, dict) or any(not isinstance(v, list) or not v for v in source_classes.values()):
        raise ValueError("source_classes must map source names to nonempty label lists")
    for source, allowed in source_classes.items():
        if any(label not in labels for label in allowed):
            raise ValueError(f"source_classes[{source!r}] contains an unsupported label")
    sample_ids = set()
    verified_sha_by_path = {}
    for sample in samples:
        for key in ("id", "path", "label", "source", "sha256"):
            if not isinstance(sample.get(key), str) or not sample[key]:
                raise ValueError(f"Each sample needs nonempty {key}")
        if sample["id"] in sample_ids:
            raise ValueError(f"Duplicate sample id: {sample['id']}")
        sample_ids.add(sample["id"])
        if sample["label"] not in labels:
            raise ValueError(f"Unsupported sample label {sample['label']!r} for {sample['id']}")
        if sample["source"] in source_classes and sample["label"] not in source_classes[sample["source"]]:
            raise ValueError(f"Sample {sample['id']} label is outside source_classes for {sample['source']}")
        sample["_allowed_labels"] = source_classes.get(sample["source"], labels)
        path = input_path.parent / sample["path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256_file(path)
        if actual.lower() != sample["sha256"].lower():
            raise ValueError(f"SHA-256 mismatch for sample {sample['id']}: expected {sample['sha256']}, got {actual}")
        verified_sha_by_path[sample["path"]] = actual.lower()
        sample["_sha256"] = actual.lower()
    if not isinstance(cases, list):
        raise ValueError("cases must be an array when supplied")
    for case in cases:
        for key in ("id", "target", "tile_paths", "tile_sha256", "reference_type", "source"):
            if key not in case:
                raise ValueError(f"Board case missing {key}")
        if case["target"] not in labels and not classes:
            labels.append(case["target"])
        if case["target"] not in labels:
            raise ValueError(f"Unsupported board target: {case['target']!r}")
        if case.get("selection_rule") not in ("select_all_margin_positive", "known_count_topk"):
            raise ValueError(f"Board {case['id']} requires a supported selection_rule")
        if case["selection_rule"] == "known_count_topk" and (not isinstance(case.get("requested_count"), int) or case["requested_count"] < 1):
            raise ValueError(f"known_count_topk board {case['id']} requires requested_count")
        if not isinstance(case["tile_paths"], list) or not case["tile_paths"] or (case["selection_rule"] == "known_count_topk" and case["requested_count"] > len(case["tile_paths"])):
            raise ValueError(f"Invalid tile_paths/requested_count in board {case['id']}")
        if len(set(case["tile_paths"])) != len(case["tile_paths"]):
            raise ValueError(f"Duplicate tile path in board {case['id']}")
        reference = case.get("correct_answers", case.get("reference_selection"))
        if reference is None:
            raise ValueError(f"Board {case['id']} needs correct_answers/reference_selection")
        if not isinstance(reference, list) or any(not isinstance(i, int) or i < 0 or i >= len(case["tile_paths"]) for i in reference):
            raise ValueError(f"Invalid board answer indices in {case['id']}")
        if len(set(reference)) != len(reference):
            raise ValueError(f"Duplicate board answer indices in {case['id']}")
        reference = sorted(reference)
        case["_reference"] = reference
        hashes = case["tile_sha256"]
        if not isinstance(hashes, list) or len(hashes) != len(case["tile_paths"]):
            raise ValueError(f"tile_sha256 must match tile_paths length in {case['id']}")
        case["_tile_sha256"] = [item.lower() if isinstance(item, str) else item for item in hashes]
        for rel, expected in zip(case["tile_paths"], hashes):
            if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
                raise ValueError(f"Invalid tile SHA-256 in {case['id']}")
            path = input_path.parent / rel
            if not path.is_file():
                raise FileNotFoundError(path)
            actual = verified_sha_by_path.get(rel)
            if actual is None:
                actual = sha256_file(path).lower()
                verified_sha_by_path[rel] = actual
            if actual != expected.lower():
                raise ValueError(f"SHA-256 mismatch for board {case['id']} tile {rel}: expected {expected}, got {actual}")
    if not classes:
        raise ValueError("Input needs classes with prompts for every target")
    if len(labels) < 2:
        raise ValueError("At least two labels are required for target-versus-other margins")
    return data, samples, cases, labels


def select_tiles(margins: list[float], rule: str, requested_count: int | None = None) -> list[int]:
    if rule == "select_all_margin_positive":
        return [i for i, margin in enumerate(margins) if margin > 0]
    if rule == "known_count_topk":
        return sorted(sorted(range(len(margins)), key=lambda i: margins[i], reverse=True)[:requested_count])
    raise ValueError(f"Unsupported selection rule: {rule}")


def synchronize(device: str) -> None:
    if device == "cuda":
        torch.cuda.synchronize()


def prep_batch(paths: list[Path], preprocess, device: str, memory_format: str):
    tensors = []
    started = time.perf_counter()
    for path in paths:
        with Image.open(path) as image:
            tensors.append(preprocess(image.convert("RGB")))
    batch = torch.stack(tensors).to(device)
    if memory_format == "channels_last":
        batch = batch.contiguous(memory_format=torch.channels_last)
    return batch, (time.perf_counter() - started) * 1000


def summarize(rows: list[dict], labels: list[str], key: str) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return {group: {"count": len(items), "accuracy": sum(x["correct"] for x in items) / len(items),
                    "confusion": {actual: dict(Counter(x["prediction"] for x in items if x["label"] == actual))
                                  for actual in labels if any(x["label"] == actual for x in items)}}
            for group, items in sorted(groups.items())}


def _groups_by_source_label(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["source"], row["label"])].append(row)
    return groups


def run_model(name: str, args, device: str, samples: list[dict], cases: list[dict], labels: list[str],
              prompt_rows: list[tuple[str, str]], output_dir: Path) -> dict:
    started = time.perf_counter()
    moe = name == MOE_MODEL
    if moe:
        if device != "cuda":
            raise ValueError("MoE-ViE-B16 requires CUDA")
        import benchmark_moe_vie as moe_loader
        import huggingface_hub
        source_repo = moe_loader.ensure_source(args.source_dir, args.official_commit)
        from open_clip import create_model_and_transforms, get_tokenizer, image_to_device
        import open_clip.factory as factory
        original_load_checkpoint = factory.load_checkpoint
        def strict_load_checkpoint(model, path, **kwargs):
            return original_load_checkpoint(model, path, strict=True)
        factory.load_checkpoint = strict_load_checkpoint
        args.model_dir.mkdir(parents=True, exist_ok=True)
        weight_path = args.model_dir / moe_loader.WEIGHT_FILE
        if not weight_path.exists():
            weight_path = Path(huggingface_hub.hf_hub_download(
                repo_id="facebook/MoEViE-B16-224", filename=moe_loader.WEIGHT_FILE,
                revision=moe_loader.WEIGHT_REVISION, local_dir=str(args.model_dir)))
        if sha256_file(weight_path) != moe_loader.WEIGHT_SHA256:
            raise ValueError("MoE-ViE-B16 weight SHA-256 mismatch")
        model, _, preprocess = create_model_and_transforms(
            "MoEViE-B16-224", pretrained=str(weight_path), force_preprocess_cfg={
                "patch_size": 16, "size_range": (224, 224), "center_crop": True, "window_size": 1},
            image_mean=moe_loader.MEAN, image_std=moe_loader.STD, use_optimized_inference=False)
        model = model.to(device).eval()
        tokenizer = get_tokenizer("MoEViE-B16-224")
        provenance = {"repo": "facebook/MoEViE-B16-224", "revision": moe_loader.WEIGHT_REVISION,
                      "sha256": moe_loader.WEIGHT_SHA256, "official_code_commit": moe_loader.OFFICIAL_COMMIT,
                      "source_path": str(source_repo), "strict_checkpoint_loading": True}
        args._moe_image_to_device = image_to_device
        args._moe_mean, args._moe_std = moe_loader.MEAN, moe_loader.STD
    else:
        # Import the regular OpenCLIP stack only for TinyCLIP/MobileCLIP.
        if model_helper is None:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import benchmark_embeddings as helper
            globals()["model_helper"] = helper
        model, preprocess, tokenizer, provenance = model_helper.load_model(name, args, device)
    if args.memory_format == "channels_last":
        model = model.to(memory_format=torch.channels_last)
    load_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    context = torch.autocast("cuda", dtype=torch.float16, enabled=args.precision == "fp16") if moe else model_helper.precision_context(args, device)
    with torch.inference_mode(), context:
        text_features = model.encode_text(tokenizer([p for _, p in prompt_rows]).to(device))
        text_features /= text_features.norm(dim=-1, keepdim=True)
    synchronize(device)
    text_ms = (time.perf_counter() - started) * 1000
    if not torch.isfinite(text_features).all().item():
        raise RuntimeError("Non-finite text features")

    paths_by_rel = {s["path"]: args.input.parent / s["path"] for s in samples}
    sha_by_rel = {s["path"]: s["_sha256"] for s in samples}
    for case in cases:
        for rel, digest in zip(case["tile_paths"], case["_tile_sha256"]):
            paths_by_rel.setdefault(rel, args.input.parent / rel)
            sha_by_rel.setdefault(rel, digest)
    rel_for_sha = {}
    for rel, digest in sha_by_rel.items():
        rel_for_sha.setdefault(digest, rel)
    rels = list(rel_for_sha.values())
    all_scores = {}
    image_ms = preprocess_ms = 0.0
    jsonl_path = output_dir / f"{name}.jsonl"
    with jsonl_path.open("x", encoding="utf-8") as out:
        for start in range(0, len(rels), args.batch_size):
            batch_rels = rels[start:start + args.batch_size]
            if moe:
                prep_started = time.perf_counter()
                transformed = []
                for rel in batch_rels:
                    with Image.open(paths_by_rel[rel]) as image:
                        transformed.append((preprocess(image.convert("RGB")), 0))
                packed, _ = preprocess.collate_fn(transformed)
                batch = args._moe_image_to_device(packed, device, torch.float32, mean=args._moe_mean, std=args._moe_std)
                prep_ms = (time.perf_counter() - prep_started) * 1000
            else:
                batch, prep_ms = prep_batch([paths_by_rel[r] for r in batch_rels], preprocess, device, args.memory_format)
            preprocess_ms += prep_ms
            if start == 0:
                with torch.inference_mode():
                    if moe:
                        with torch.autocast("cuda", dtype=torch.float16, enabled=args.precision == "fp16"):
                            # MoE input is a packed tuple with four metadata
                            # fields, so slicing it would drop required fields.
                            model.encode_image(batch, normalize=True)
                    else:
                        with model_helper.precision_context(args, device):
                            model.encode_image(batch[:1])
                synchronize(device)
            synchronize(device)
            tick = time.perf_counter()
            with torch.inference_mode():
                if moe:
                    with torch.autocast("cuda", dtype=torch.float16, enabled=args.precision == "fp16"):
                        features = model.encode_image(batch, normalize=True)
                else:
                    with model_helper.precision_context(args, device):
                        features = model.encode_image(batch)
                        features /= features.norm(dim=-1, keepdim=True)
                similarities = feature_similarities(features, text_features)
            synchronize(device)
            image_ms += (time.perf_counter() - tick) * 1000
            if not torch.isfinite(features).all().item() or not torch.isfinite(similarities).all().item():
                raise RuntimeError("Non-finite image features or class scores")
            scores = similarities.float().cpu().tolist()
            for rel, raw in zip(batch_rels, scores):
                per_label = {label: max(raw[i] for i, (owner, _) in enumerate(prompt_rows) if owner == label) for label in labels}
                all_scores[sha_by_rel[rel]] = per_label
            print(json.dumps({"model": name, "batch": start // args.batch_size + 1, "batches": math.ceil(len(rels) / args.batch_size), "unique_images_done": min(start + args.batch_size, len(rels))}), flush=True)
            del batch, features, similarities

        sample_rows = []
        for sample in samples:
            scores = all_scores[sample["_sha256"]]
            pred = max(sample["_allowed_labels"], key=scores.get)
            row = {"id": sample["id"], "path": sample["path"], "label": sample["label"], "source": sample["source"],
                   "source_type": sample.get("source_type", sample["source"]),
                   "prediction": pred, "correct": pred == sample["label"], "scores": scores}
            sample_rows.append(row)
            out.write(json.dumps({"type": "sample", **row}, ensure_ascii=False) + "\n")
        board_rows = []
        for case in cases:
            margins = []
            for tile_index, rel in enumerate(case["tile_paths"]):
                class_scores = all_scores[case["_tile_sha256"][tile_index]]
                margins.append(class_scores[case["target"]] - max(v for k, v in class_scores.items() if k != case["target"]))
            chosen = select_tiles(margins, case["selection_rule"], case.get("requested_count"))
            reference = case["_reference"]
            overlap = len(set(chosen) & set(reference))
            row = {"id": case["id"], "target": case["target"], "source": case["source"],
                   "reference_type": case["reference_type"], "source_type": case.get("source_type", case["source"]),
                   "selection_rule": case["selection_rule"],
                   "selection": chosen, "reference_selection": reference,
                   "requested_count": case.get("requested_count"), "board_exact": chosen == reference,
                   "positive_overlap": overlap,
                   "tile_precision": overlap / len(chosen) if chosen else (1.0 if not reference else 0.0),
                   "tile_recall": overlap / len(reference) if reference else (1.0 if not chosen else 0.0), "margins": margins}
            board_rows.append(row)
            out.write(json.dumps({"type": "board", **row}, ensure_ascii=False) + "\n")

    boards_by = defaultdict(list)
    for row in board_rows:
        boards_by[(row["source_type"], row["source"], row["target"], row["reference_type"], row["selection_rule"])].append(row)
    board_summary = {f"{source_type}/{source}/{target}/{reference_type}/{rule}": {"count": len(rows), "board_exact_rate": sum(r["board_exact"] for r in rows) / len(rows),
                    "mean_positive_overlap": statistics.mean(r["positive_overlap"] for r in rows),
                    "mean_tile_precision": statistics.mean(r["tile_precision"] for r in rows),
                    "mean_tile_recall": statistics.mean(r["tile_recall"] for r in rows)}
                     for (source_type, source, target, reference_type, rule), rows in sorted(boards_by.items())}
    return {"model": name, "device": device, "weights": provenance, "load_ms": load_ms,
            "total_parameters": sum(p.numel() for p in model.parameters()),
            "image_parameters": sum(p.numel() for p in model.visual.parameters()),
            "cuda_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1048576 if device == "cuda" else None,
            "cuda_peak_reserved_mib": torch.cuda.max_memory_reserved() / 1048576 if device == "cuda" else None,
            "cached_text_embedding_ms": text_ms, "preprocessing_ms_total": preprocess_ms,
            "image_encoder_and_scoring_ms_total": image_ms, "samples": len(samples), "boards": len(cases),
            "unique_images_encoded": len(rels),
            "accuracy": sum(r["correct"] for r in sample_rows) / len(sample_rows) if sample_rows else None,
            "by_source": summarize(sample_rows, labels, "source") if sample_rows else {}, "by_label": summarize(sample_rows, labels, "label") if sample_rows else {},
            "by_source_type": summarize(sample_rows, labels, "source_type") if sample_rows else {},
            "by_source_and_label": {f"{source}/{label}": {"count": len(rows), "accuracy": sum(x["correct"] for x in rows) / len(rows), "confusion": dict(Counter(x["prediction"] for x in rows))}
                 for (source, label), rows in _groups_by_source_label(sample_rows).items()},
            "confusion_matrix": {actual: {pred: sum(r["label"] == actual and r["prediction"] == pred for r in sample_rows) for pred in labels} for actual in labels},
            "board_by_source_target": board_summary, "sample_scores_jsonl": jsonl_path.name}


def main() -> None:
    global torch, model_helper
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Input JSON; image paths are relative to it")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for run protocol and outputs")
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--model-dir", type=Path, default=Path(".lab-output/model-cache/mobileclip2"))
    parser.add_argument("--baseline-dir", type=Path, default=Path(".lab-output/model-cache/tinyclip40"))
    parser.add_argument("--source-dir", type=Path, default=Path("/tmp/moe-vie-source"))
    parser.add_argument("--official-commit", default="7c34cb2f3897c53aa7131cdbbbd35b1c5412c365")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    parser.add_argument("--precision", choices=("fp32", "fp16"), default="fp32")
    parser.add_argument("--memory-format", choices=("contiguous", "channels_last"), default="channels_last")
    args = parser.parse_args()
    import torch as torch_module
    torch = torch_module
    if args.output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite {args.output_dir}")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be positive")
    data, samples, cases, labels = load_input(args.input)
    device = "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    if device == "auto":
        device = "cpu"
    if args.precision == "fp16" and device != "cuda":
        raise ValueError("fp16 is available only with CUDA")
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    if not models or len(models) != len(set(models)):
        raise ValueError("--models must contain unique model names")
    torch.set_num_threads(2)
    if MOE_MODEL in models and len(models) > 1:
        raise ValueError("Run MoE-ViE-B16 in a separate process from TinyCLIP/MobileCLIP to isolate OpenCLIP imports")
    if MOE_MODEL not in models:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import benchmark_embeddings as helper
        model_helper = helper
    args.input = args.input.resolve()
    args.output_dir.mkdir(parents=True)
    prompt_rows = [(c["label"], prompt) for c in data["classes"] for prompt in c["prompts"]]
    protocol = {"input": str(args.input), "input_sha256": sha256_file(args.input), "class_labels": labels,
                "sample_count": len(samples), "board_count": len(cases), "models": models, "device": device,
                "threads": 2, "batch_size": args.batch_size, "memory_format": args.memory_format, "precision": args.precision,
                "warmup_excluded": True, "timing_passes": 1,
                "timings": "preprocessing/transfer and image encoding plus similarity scoring totals are separate; loading and cached text encoding are also reported",
                "model_tuning": "no prompts, thresholds, or weights tuned on these samples or board cases",
                "similarity_dtype_policy": "promote image and text feature dtypes; matching dtypes stay unchanged",
                "source_classes": data.get("source_classes", {}),
                "dataset_notes": data.get("dataset_notes", data.get("source_note")),
                "ground_truth": "supplied sample labels and supplied board reference selections; independent of predictions",
                "torch": torch.__version__,
                "open_clip": model_helper.open_clip.__version__ if model_helper is not None else None}
    (args.output_dir / "run_protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    aggregate = {"protocol": protocol, "models": []}
    for name in models:
        print(json.dumps({"status": "model_start", "model": name}), flush=True)
        result = run_model(name, args, device, samples, cases, labels, prompt_rows, args.output_dir)
        aggregate["models"].append(result)
        (args.output_dir / "aggregate.json").write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "model_complete", "model": name, "accuracy": result["accuracy"]}), flush=True)


if __name__ == "__main__":
    main()
