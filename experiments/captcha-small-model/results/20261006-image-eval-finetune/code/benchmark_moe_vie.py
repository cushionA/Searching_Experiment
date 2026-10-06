"""Benchmark the official MoE-ViE-B16-224 implementation on saved CAPTCHA tiles.

This is an embedding comparison, not a CAPTCHA success-rate benchmark. The
official repository is loaded at a pinned commit and the checkpoint is pinned
by Hugging Face revision and LFS SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


MODEL = "MoEViE-B16-224"
WEIGHT_REVISION = "b565ad0a1a8ccc36977752dbcfb22a2ecf7beef9"
WEIGHT_SHA256 = "d9f53359f73133c880ea9108635ec1a5306e053841c4d59e0981e7aa70be6be1"
WEIGHT_FILE = f"{MODEL}.pt"
OFFICIAL_REPO = "https://github.com/facebookresearch/moe_vie.git"
OFFICIAL_COMMIT = "7c34cb2f3897c53aa7131cdbbbd35b1c5412c365"
MEAN = (0.5, 0.5, 0.5)
STD = (0.5, 0.5, 0.5)

PROMPTS = {
    "ponies": ["a cartoon pony", "a pony from My Little Pony", "an illustration of a pony"],
    "minecraft": ["a screenshot of Minecraft", "a Minecraft video game scene", "a blocky Minecraft world"],
    "cats": ["a photograph of a cat"], "dogs": ["a photograph of a dog"],
    "birds": ["a photograph of a bird"], "fish": ["a photograph of a fish"],
    "people": ["a photograph of a person", "an anime illustration of a person"],
    "furries": ["an illustration of a furry anthropomorphic animal"],
    "landscape": ["a landscape photograph"],
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ensure_source(source: Path, commit: str) -> Path:
    source.mkdir(parents=True, exist_ok=True)
    repo = source if (source / "src" / "open_clip").is_dir() else source / "moe_vie"
    if not (repo / ".git").exists():
        subprocess.run(["git", "clone", "--filter=blob:none", OFFICIAL_REPO, str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "--depth", "1", "origin", commit], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", commit], check=True)
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if actual != commit:
        raise RuntimeError(f"Official code commit mismatch: {actual}")
    sys.path.insert(0, str(repo / "src"))
    return repo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=Path("/tmp/moe-vie-model"))
    parser.add_argument("--source-dir", type=Path, default=Path("/tmp/moe-vie-source"))
    parser.add_argument("--official-commit", default=OFFICIAL_COMMIT,
                        help="facebookresearch/moe_vie commit SHA (defaults to the pinned official revision)")
    parser.add_argument("--precision", choices=("fp32", "fp16"), default="fp16")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "model": MODEL, "device": "cuda", "precision": args.precision,
        "warmup": 1, "repeats": 3, "case_count": None,
        "is_success_rate_benchmark": False,
        "weights": {"repo": "facebook/MoEViE-B16-224", "revision": WEIGHT_REVISION,
                    "filename": WEIGHT_FILE, "sha256": WEIGHT_SHA256},
        "official_code": {"repo": OFFICIAL_REPO, "commit": args.official_commit},
        "cases_sha256": sha256(args.cases),
        "environment": {"python": sys.version, "platform": platform.platform(),
                         "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES")},
    }
    try:
        import torch
        result["torch"] = torch.__version__
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA GPU unavailable")
        result["cuda_runtime"] = torch.version.cuda
        result["gpu"] = torch.cuda.get_device_name(0)
        result["gpu_capability"] = list(torch.cuda.get_device_capability(0))
        result["gpu_total_memory_mib"] = torch.cuda.get_device_properties(0).total_memory / 1048576
        result["gpu_count"] = torch.cuda.device_count()
        try:
            import triton
            result["triton"] = triton.__version__
        except Exception as e:
            result["triton"] = None
            result["triton_import_error"] = str(e)[:300]

        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "600")
        import huggingface_hub
        from PIL import Image
        load_start = time.perf_counter()
        source_repo = ensure_source(args.source_dir, args.official_commit)
        from open_clip import create_model_and_transforms, get_tokenizer, image_to_device
        import open_clip.factory as factory
        original_load_checkpoint = factory.load_checkpoint

        def strict_load_checkpoint(model, path, **kwargs):
            return original_load_checkpoint(model, path, strict=True)

        factory.load_checkpoint = strict_load_checkpoint
        result["strict_checkpoint_loading"] = True
        result["official_code"]["commit_verified"] = True
        args.model_dir.mkdir(parents=True, exist_ok=True)
        weight_path = args.model_dir / WEIGHT_FILE
        if not weight_path.exists():
            downloaded = huggingface_hub.hf_hub_download(
                repo_id="facebook/MoEViE-B16-224", filename=WEIGHT_FILE,
                revision=WEIGHT_REVISION, local_dir=str(args.model_dir))
            weight_path = Path(downloaded)
        if sha256(weight_path) != WEIGHT_SHA256:
            raise RuntimeError("Weight SHA-256 mismatch")

        model, _, transform = create_model_and_transforms(
            MODEL, pretrained=str(weight_path), force_preprocess_cfg={
                "patch_size": 16, "size_range": (224, 224), "center_crop": True, "window_size": 1,
            }, image_mean=MEAN, image_std=STD, use_optimized_inference=False)
        model = model.to("cuda").eval()
        result["total_parameters"] = sum(p.numel() for p in model.parameters())
        result["image_parameters"] = sum(p.numel() for p in model.visual.parameters())
        tokenizer = get_tokenizer(MODEL)
        torch_dtype = torch.float16 if args.precision == "fp16" else torch.float32
        result["precision_detail"] = {
            "model_parameter_dtype": str(next(model.parameters()).dtype),
            "image_input_dtype": "torch.float32",
            "encoder_autocast_dtype": str(torch_dtype),
            "autocast_enabled": args.precision == "fp16",
        }
        result["weights"].update({"sha256_verified": True, "bytes": weight_path.stat().st_size})
        result["load_ms"] = (time.perf_counter() - load_start) * 1000

        prompt_list = [p for group in PROMPTS.values() for p in group]
        prompt_classes = [label for label, group in PROMPTS.items() for _ in group]
        sync = torch.cuda.synchronize
        sync()
        t0 = time.perf_counter()
        tokens = tokenizer(prompt_list).to("cuda")
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch_dtype, enabled=args.precision == "fp16"):
            text_features = model.encode_text(tokens, normalize=True)
        if not torch.isfinite(text_features).all().item():
            raise RuntimeError("Non-finite text embeddings")
        sync()
        result["text_ms"] = (time.perf_counter() - t0) * 1000

        data = json.loads(args.cases.read_text())
        cases = data["cases"]
        result["case_count"] = len(cases)
        results = []
        for case in cases:
            t0 = time.perf_counter()
            transformed = []
            for rel in case["tile_paths"]:
                with Image.open(args.cases.parent / rel) as im:
                    transformed.append((transform(im.convert("RGB")), 0))
            packed, _ = transform.collate_fn(transformed)
            packed = image_to_device(packed, "cuda", torch.float32, mean=MEAN, std=STD)
            preprocess_ms = (time.perf_counter() - t0) * 1000

            def encode():
                with torch.inference_mode(), torch.autocast("cuda", dtype=torch_dtype, enabled=args.precision == "fp16"):
                    return model.encode_image(packed, normalize=True)

            encode()
            sync()
            samples = []
            with torch.inference_mode():
                for _ in range(3):
                    sync()
                    t0 = time.perf_counter()
                    features = encode()
                    sync()
                    samples.append((time.perf_counter() - t0) * 1000)
            similarities = features @ text_features.T
            if not torch.isfinite(features).all().item() or not torch.isfinite(similarities).all().item():
                raise RuntimeError("Non-finite image embeddings or similarities")
            positive = [i for i, label in enumerate(prompt_classes) if label == case["target"]]
            negative = [i for i, label in enumerate(prompt_classes) if label != case["target"]]
            margins = similarities[:, positive].max(dim=1).values - similarities[:, negative].max(dim=1).values
            selection = sorted(margins.topk(case["requested_count"]).indices.cpu().tolist())
            reference = case["reference_selection"]
            results.append({"id": case["id"], "target": case["target"],
                            "reference_type": case["reference_type"], "selection": selection,
                            "exact_reference_match": selection == reference,
                            "overlap_count": len(set(selection) & set(reference)),
                            "requested_count": case["requested_count"],
                            "preprocess_ms": preprocess_ms,
                            "encoder_samples_ms": samples,
                            "encoder_median_ms": statistics.median(samples),
                            "margins": margins.cpu().tolist()})
            result["cases"] = results
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"model": MODEL, "case": case["id"], "selection": selection,
                              "image_ms": statistics.median(samples)}), flush=True)
        result["cases"] = results
        result["cuda_peak_allocated_mib"] = torch.cuda.max_memory_allocated() / 1048576
        result["cuda_peak_reserved_mib"] = torch.cuda.max_memory_reserved() / 1048576
        result["caveat"] = "T4/P100 operation and speed are not guaranteed; benchmark the target Kaggle GPU."
        result["status"] = "ok"
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {str(e)[:500]}"
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "output": str(args.output),
                      "error": result.get("error")}), flush=True)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
