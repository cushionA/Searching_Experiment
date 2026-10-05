"""Compare CPU/GPU image encoders on exactly the same saved CAPTCHA tiles."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import time
import urllib.request

from PIL import Image
import open_clip
import torch


MOBILE_MODELS = {
    "MobileCLIP2-S0": {
        "revision": "3136ea51c8ed56b9f9abfab04cb816735aaad6cb", "filename": "mobileclip2_s0.pt",
        "sha256": "bc3bc861baa680df2f9e3dc1aa0acbe5a21af21032df385026dda70c33505aa6",
    },
    "MobileCLIP2-S2": {
        "revision": "72424e7025436db18f15c3eff6ee8c7c15ad4481", "filename": "mobileclip2_s2.pt",
        "sha256": "37c2d839a856491f2fcc82c40dc28672dbd0907235b4cd4c38dfff6457f0c09f",
    },
}
PROMPTS = {
    "ponies": ["a cartoon pony", "a pony from My Little Pony", "an illustration of a pony"],
    "minecraft": ["a screenshot of Minecraft", "a Minecraft video game scene", "a blocky Minecraft world"],
    "cats": ["a photograph of a cat"], "dogs": ["a photograph of a dog"],
    "birds": ["a photograph of a bird"], "fish": ["a photograph of a fish"],
    "people": ["a photograph of a person", "an anime illustration of a person"],
    "furries": ["an illustration of a furry anthropomorphic animal"],
    "landscape": ["a landscape photograph"],
}


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mobile_weights(name, directory):
    spec = MOBILE_MODELS[name]
    destination = directory / spec["filename"]
    directory.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_suffix(".partial")
        url = f"https://huggingface.co/apple/{name}/resolve/{spec['revision']}/{spec['filename']}"
        deadline = time.monotonic() + 80
        try:
            with urllib.request.urlopen(url, timeout=25) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Model download exceeded its deadline")
                    output.write(chunk)
            if file_sha(temporary) != spec["sha256"]:
                raise ValueError("Downloaded weight hash mismatch")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    if file_sha(destination) != spec["sha256"]:
        raise ValueError("Cached weight hash mismatch")
    return destination


def synchronize(device):
    if device == "cuda":
        torch.cuda.synchronize()


def precision_context(args, device):
    return torch.autocast(device_type=device, dtype=torch.float16,
                          enabled=args.precision == "fp16")


def load_model(name, args, device):
    if name in MOBILE_MODELS:
        checkpoint = mobile_weights(name, args.model_dir)
        cfg = open_clip.get_pretrained_cfg(name, "dfndr2b")
        model, _, preprocess = open_clip.create_model_and_transforms(
            name, pretrained=str(checkpoint), device=device,
            image_mean=cfg["mean"], image_std=cfg["std"],
            image_interpolation=cfg["interpolation"], image_resize_mode=cfg["resize_mode"],
        )
        model.eval()
        from timm.utils import reparameterize_model
        model = reparameterize_model(model)
        tokenizer = open_clip.get_tokenizer(name)
        return model.eval(), preprocess, tokenizer, {
            "file_sha256": file_sha(checkpoint), "bytes": checkpoint.stat().st_size, **MOBILE_MODELS[name],
            "preprocess": {key: cfg[key] for key in ("mean", "std", "interpolation", "resize_mode")},
            "inference_reparameterized": True,
        }
    architecture = "TinyCLIP-ViT-40M-32-Text-19M"
    if name != architecture:
        raise ValueError("Unsupported model")
    checkpoint = args.baseline_dir / f"{architecture}-LAION400M.pt"
    expected = "af5042c3e3662c43573c16113d4a72014cf3aa19267a7a50c4b94be3c4e8366a"
    if file_sha(checkpoint) != expected:
        raise ValueError("Baseline weight hash mismatch")
    open_clip.add_model_config(args.baseline_dir / f"{architecture}.json")
    model, _, preprocess = open_clip.create_model_and_transforms(architecture, pretrained=None, load_weights=False, device=device)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    state = state.get("state_dict", state)
    model.load_state_dict({key.removeprefix("module."): value for key, value in state.items()}, strict=True)
    return model.eval(), preprocess, open_clip.get_tokenizer(architecture), {"file_sha256": expected, "bytes": checkpoint.stat().st_size}


def benchmark(name, cases, args, device):
    started = time.perf_counter()
    model, preprocess, tokenizer, provenance = load_model(name, args, device)
    if args.memory_format == "channels_last":
        model = model.to(memory_format=torch.channels_last)
    loaded_ms = (time.perf_counter() - started) * 1000
    total_params = sum(p.numel() for p in model.parameters())
    image_params = sum(p.numel() for p in model.visual.parameters())
    prompt_list = [prompt for group in PROMPTS.values() for prompt in group]
    prompt_classes = [key for key, group in PROMPTS.items() for _ in group]
    synchronize(device)
    started = time.perf_counter()
    with torch.inference_mode(), precision_context(args, device):
        text_features = model.encode_text(tokenizer(prompt_list).to(device))
        text_features /= text_features.norm(dim=-1, keepdim=True)
    synchronize(device)
    text_ms = (time.perf_counter() - started) * 1000
    if not torch.isfinite(text_features).all().item():
        raise RuntimeError("Non-finite text embeddings")
    results = []
    for case in cases:
        started = time.perf_counter()
        image_batch = torch.stack([preprocess(Image.open(path).convert("RGB")) for path in case["absolute_tile_paths"]]).to(device)
        if args.memory_format == "channels_last":
            image_batch = image_batch.contiguous(memory_format=torch.channels_last)
        preprocess_ms = (time.perf_counter() - started) * 1000
        timings = []
        with torch.inference_mode(), precision_context(args, device):
            # Warm-up is excluded from timing; text embeddings are cached above.
            model.encode_image(image_batch)
            synchronize(device)
            for _ in range(args.repeats):
                started = time.perf_counter()
                features = model.encode_image(image_batch)
                synchronize(device)
                timings.append((time.perf_counter() - started) * 1000)
            features /= features.norm(dim=-1, keepdim=True)
            similarities = features @ text_features.T
            if not torch.isfinite(features).all().item() or not torch.isfinite(similarities).all().item():
                raise RuntimeError("Non-finite image embeddings or similarities")
            positive = [i for i, label in enumerate(prompt_classes) if label == case["target"]]
            negative = [i for i, label in enumerate(prompt_classes) if label != case["target"]]
            margins = similarities[:, positive].max(dim=1).values - similarities[:, negative].max(dim=1).values
            selection = sorted(margins.topk(case["requested_count"]).indices.cpu().tolist())
        reference = case["reference_selection"]
        results.append({"id": case["id"], "target": case["target"], "reference_type": case["reference_type"],
                        "selection": selection, "exact_reference_match": selection == reference,
                        "overlap_count": len(set(selection) & set(reference)), "requested_count": case["requested_count"],
                        "encoder_median_ms": statistics.median(timings), "encoder_samples_ms": timings,
                        "preprocess_ms": preprocess_ms, "margins": margins.cpu().tolist()})
        print(json.dumps({"model": name, "case": case["id"], "selected": selection,
                          "reference_match": selection == reference, "image_ms": statistics.median(timings)}), flush=True)
    output = {"model": name, "device": device, "total_parameters": total_params, "image_parameters": image_params,
              "non_image_parameters": total_params - image_params, "load_ms": loaded_ms,
              "cached_text_embedding_ms": text_ms, "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
              "peak_rss_is_cumulative_process_peak": True, "weights": provenance, "cases": results}
    if device == "cuda":
        output["cuda_peak_allocated_mib"] = torch.cuda.max_memory_allocated() / (1024 * 1024)
    del model, text_features, image_batch
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=Path(".lab-output/model-cache/mobileclip2"))
    parser.add_argument("--baseline-dir", type=Path, default=Path(".lab-output/model-cache/tinyclip40"))
    parser.add_argument("--models", default="MobileCLIP2-S0,MobileCLIP2-S2")
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--memory-format", choices=("contiguous", "channels_last"), default="contiguous")
    parser.add_argument("--precision", choices=("fp32", "fp16"), default="fp32")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cases = json.loads(args.cases.read_text())["cases"]
    for case in cases:
        case["absolute_tile_paths"] = [args.cases.parent / p for p in case["tile_paths"]]
    torch.set_num_threads(2)
    device = "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    if device == "auto":
        device = "cpu"
    if args.precision == "fp16" and device != "cuda":
        raise ValueError("FP16 comparison requires a CUDA GPU")
    output = {"device": device, "threads": 2, "repeats": args.repeats, "case_count": len(cases),
              "memory_format": args.memory_format,
              "precision": args.precision,
              "gpu": torch.cuda.get_device_name(0) if device == "cuda" else None,
              "is_success_rate_benchmark": False, "models": [], "torch": torch.__version__, "open_clip": open_clip.__version__}
    for name in args.models.split(","):
        output["models"].append(benchmark(name, cases, args, device))
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
