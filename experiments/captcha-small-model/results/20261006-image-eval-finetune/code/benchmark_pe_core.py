"""Strict adapter for the official PE-Core-S16-384 image/text checkpoint.

Only the reviewed, immutable inference modules are downloaded. Their content
hashes are checked before import; checkpoint loading does not execute pickle
objects. The shared benchmark retains its original prompts and scoring.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib
import importlib.metadata
from pathlib import Path
import sys
import time
import urllib.request


MODEL_NAME = "PE-Core-S16-384"
WEIGHT_REPOSITORY = "facebook/PE-Core-S16-384"
WEIGHT_SPEC = {
    "revision": "aabf3b990573d8114ae6e501b4697106beac8f19",
    "filename": "PE-Core-S16-384.pt",
    "bytes": 348852712,
    "sha256": "ccc8340a14ea3ebf557a288ba4ed4a5bc026ab98bb4da42fc745d44b4c5c5ffb",
}
SOURCE_COMMIT = "3e352cca660658d4b5c90f42a7808b11469e4c66"
SOURCE_REPOSITORY = "facebookresearch/perception_models"
# Apache-2.0 official inference code; tokenizer derives from OpenAI CLIP (MIT).
SOURCE_FILES = {
    "core/__init__.py": (0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    "core/vision_encoder/__init__.py": (0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    "core/vision_encoder/pe.py": (24000, "36b62204c4cf12a709946facce2df8980e0880bf06deb6e49a2aa288158c3d53"),
    "core/vision_encoder/config.py": (5175, "37c10ea7b68889241c8d9ebce27c62577c7379a33c444f9b0129bc90946aaff5"),
    "core/vision_encoder/rope.py": (10507, "85a16113138f26389ea35fccd313f2aca12945c7ed2eb24e5d52dbe680dad121"),
    "core/vision_encoder/transforms.py": (861, "ded9fdbd3a2e3a1d46e05d83f64074281e9c3b6f630257d051308d27e74bd7d3"),
    "core/vision_encoder/tokenizer.py": (11525, "42db8b142e1fe7a9b7d294049e063c8bd454e6902e94af2eebf3769c3927186d"),
    "core/vision_encoder/bpe_simple_vocab_16e6.txt.gz": (1356917, "924691ac288e54409236115652ad4aa250f48203de50a9e4722a6ecd48d6804a"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_source(directory: Path) -> Path:
    """Cache only the exact allowlisted source paths, with a bounded transfer."""
    root = directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 180
    for relative, (size, expected_sha) in SOURCE_FILES.items():
        path = root / relative
        if not path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("PE source download exceeded its deadline")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".partial")
            url = f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{SOURCE_COMMIT}/{relative}"
            try:
                with urllib.request.urlopen(url, timeout=min(45, max(1, deadline - time.monotonic()))) as response:
                    raw = response.read(size + 1)
                if time.monotonic() >= deadline:
                    raise TimeoutError("PE source download exceeded its deadline")
                if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected_sha:
                    raise ValueError(f"PE source download hash/size mismatch: {relative}")
                temporary.write_bytes(raw)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        if not path.is_file() or path.stat().st_size != size or sha256_file(path) != expected_sha:
            raise ValueError(f"PE cached source hash/size mismatch: {relative}")
    return root


def import_source(source: Path):
    # The official package uses the generic top-level namespace "core". Refuse
    # a different package already imported rather than silently using its code.
    for name, module in list(sys.modules.items()):
        if name == "core" or name.startswith("core."):
            location = getattr(module, "__file__", None)
            if location is None or not Path(location).resolve().is_relative_to(source):
                raise RuntimeError(f"PE official source namespace conflicts with loaded module: {name}")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    return importlib.import_module("core.vision_encoder.pe"), importlib.import_module("core.vision_encoder.transforms")


def load_model(args, device: str):
    import torch
    from benchmark_embeddings import pinned_weights

    checkpoint = pinned_weights(WEIGHT_REPOSITORY, WEIGHT_SPEC, args.model_dir)
    source = ensure_source(args.model_dir / f"perception-models-{SOURCE_COMMIT}")
    pe, transforms = import_source(source)
    model = pe.CLIP.from_config(MODEL_NAME, pretrained=False)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if isinstance(state, dict):
        state = state.get("state_dict", state.get("weights", state))
    if not isinstance(state, dict) or not state or any(not isinstance(key, str) for key in state):
        raise ValueError("PE checkpoint must be a nonempty tensor state dictionary")
    mapped = {key.removeprefix("module."): value for key, value in state.items()}
    if len(mapped) != len(state):
        raise ValueError("PE checkpoint has colliding module-prefix keys")
    # Unlike the generic official convenience loader's strict=False, every
    # parameter must be supplied for this complete CLIP checkpoint.
    model.load_state_dict(mapped, strict=True)
    del state, mapped
    model = model.eval().to(device)
    preprocess = transforms.get_image_transform(model.image_size, center_crop=False)
    tokenizer = transforms.get_text_tokenizer(model.context_length)
    provenance = {
        **WEIGHT_SPEC,
        "repository": WEIGHT_REPOSITORY,
        "file_sha256": WEIGHT_SPEC["sha256"],
        "strict_checkpoint_loading": True,
        "weights_only": True,
        "official_code_repository": SOURCE_REPOSITORY,
        "official_code_commit": SOURCE_COMMIT,
        "source_files": {
            name: {"bytes": size, "sha256": digest}
            for name, (size, digest) in SOURCE_FILES.items()
        },
        "model_config": {
            "vision": asdict(pe.PE_VISION_CONFIG[MODEL_NAME]),
            "text": asdict(pe.PE_TEXT_CONFIG[MODEL_NAME]),
        },
        "preprocess": {
            "image_size": model.image_size,
            "mean": [0.5, 0.5, 0.5],
            "std": [0.5, 0.5, 0.5],
            "interpolation": "bilinear",
            "resize_mode": "squash",
            "center_crop": False,
        },
        "tokenizer": {"implementation": "official SimpleTokenizer", "context_length": model.context_length},
        "implementation_packages": {
            package: importlib.metadata.version(package)
            for package in ("timm", "torch", "torchvision", "einops", "ftfy", "regex", "huggingface_hub")
        },
        "inference_reparameterized": False,
    }
    return model, preprocess, tokenizer, provenance
