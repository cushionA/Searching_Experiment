"""Offline visual tile solver using locally pinned CLIP and OWLv2 snapshots.

This is an independent implementation inspired by the general model families
used in the paper; the paper's exact checkpoints were not identified.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Iterable, Sequence


def _finite(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def select_clip_tiles(positive_scores: Sequence[float], negative_scores: Sequence[float], threshold: float) -> list[int]:
    """Select row-major tile indices when normalized cosine margin meets threshold."""
    threshold = _finite(threshold, "threshold")
    if len(positive_scores) != len(negative_scores):
        raise ValueError("positive and negative score lengths differ")
    margins = [_finite(p, "positive score") - _finite(n, "negative score") for p, n in zip(positive_scores, negative_scores)]
    return [i for i, margin in enumerate(margins) if margin >= threshold]


def _grid_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def boxes_to_tiles(boxes: Iterable[Sequence[float]], scores: Sequence[float], image_size: Sequence[float], rows: int, columns: int, confidence_threshold: float, coverage_threshold: float) -> tuple[list[int], list[float]]:
    """Map clipped xyxy detections to tiles; each box is evaluated independently."""
    rows, columns = _grid_int(rows, "rows"), _grid_int(columns, "columns")
    if len(image_size) != 2:
        raise ValueError("image_size must be (width, height)")
    width, height = (_finite(v, "image dimension") for v in image_size)
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    confidence_threshold = _finite(confidence_threshold, "confidence_threshold")
    coverage_threshold = _finite(coverage_threshold, "coverage_threshold")
    if not 0 <= confidence_threshold <= 1 or not 0 <= coverage_threshold <= 1:
        raise ValueError("confidence and coverage thresholds must be in [0, 1]")
    boxes = list(boxes)
    if len(boxes) != len(scores):
        raise ValueError("box and score lengths differ")
    tile_w, tile_h = width / columns, height / rows
    tile_scores = [0.0] * (rows * columns)
    chosen: set[int] = set()
    for box, raw_score in zip(boxes, scores):
        score = _finite(raw_score, "detection score")
        if not 0 <= score <= 1:
            raise ValueError("detection score must be in [0, 1]")
        if len(box) != 4:
            raise ValueError("each box must have four xyxy coordinates")
        x1, y1, x2, y2 = (_finite(v, "bbox coordinate") for v in box)
        x1, x2 = min(width, max(0.0, x1)), min(width, max(0.0, x2))
        y1, y2 = min(height, max(0.0, y1)), min(height, max(0.0, y2))
        if x2 <= x1 or y2 <= y1 or score < confidence_threshold:
            continue
        for row in range(rows):
            iy1, iy2 = row * tile_h, (row + 1) * tile_h
            for col in range(columns):
                ix1, ix2 = col * tile_w, (col + 1) * tile_w
                inter = max(0.0, min(x2, ix2) - max(x1, ix1)) * max(0.0, min(y2, iy2) - max(y1, iy1))
                index = row * columns + col
                coverage = inter / (tile_w * tile_h)
                if coverage >= coverage_threshold and coverage > 0:
                    chosen.add(index)
                    tile_scores[index] = max(tile_scores[index], score)
    return sorted(chosen), tile_scores


def _manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"Model manifest missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != "local-visual-models-v1":
        raise ValueError("Unsupported local visual model manifest")
    models = manifest.get("models")
    if not isinstance(models, dict) or set(models) != {"clip", "owl"}:
        raise ValueError("Manifest must contain exactly clip and owl models")
    allowed = {
        "clip": {"laion/CLIP-ViT-B-32-laion2B-s34B-b79K", "laion/CLIP-ViT-H-14-laion2B-s32B-b79K"},
        "owl": {"google/owlv2-base-patch16-ensemble"},
    }
    expected_types = {"clip": "clip", "owl": "owlv2"}
    for name, model in models.items():
        if not isinstance(model, dict) or model.get("repo_id") not in allowed[name]:
            raise ValueError(f"Unexpected {name} model provenance")
        revision = model.get("revision", "")
        if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision.lower()):
            raise ValueError(f"{name} revision must be an immutable commit SHA")
        if model.get("model_type") != expected_types[name]:
            raise ValueError(f"Unexpected {name} model type")
        directory_name = model.get("directory")
        if directory_name != name:
            raise ValueError(f"Unexpected {name} snapshot directory")
        directory = root / directory_name
        if not directory.is_dir():
            raise FileNotFoundError(f"Model snapshot missing: {directory}")
        files = model.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(f"{name} manifest has no files")
        seen = set()
        has_weights = False
        for entry in files:
            relative = Path(entry.get("path", ""))
            if relative.is_absolute() or ".." in relative.parts or ".cache" in relative.parts or not relative.parts:
                raise ValueError(f"Unsafe {name} manifest path")
            if relative.as_posix() in seen:
                raise ValueError(f"Duplicate {name} manifest path")
            seen.add(relative.as_posix())
            expected_hash = entry.get("sha256", "")
            if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash.lower()):
                raise ValueError(f"Invalid {name} file SHA-256")
            file_path = directory / relative
            if not file_path.is_file() or _sha256(file_path) != expected_hash:
                raise ValueError(f"Model snapshot hash mismatch: {file_path}")
            has_weights |= relative.name.endswith(".safetensors")
        if not has_weights or "config.json" not in seen:
            raise ValueError(f"{name} snapshot lacks safetensors weights or config")
        actual_files = {
            item.relative_to(directory).as_posix()
            for item in directory.rglob("*")
            if item.is_file() and ".cache" not in item.relative_to(directory).parts
        }
        if actual_files != seen:
            raise ValueError(f"{name} snapshot has unverified files")
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalVisualSolver:
    def __init__(self, model_root: Path, device: str = "cpu", threads: int = 2, clip_threshold: float = 0.01, confidence_threshold: float = 0.5, coverage_threshold: float = 0.1):
        self.model_root = Path(model_root)
        if device not in ("cpu", "cuda"):
            raise ValueError("device must be cpu or cuda")
        self.device = device
        self.threads = _grid_int(threads, "threads")
        self.clip_threshold = _finite(clip_threshold, "clip_threshold")
        self.confidence_threshold = _finite(confidence_threshold, "confidence_threshold")
        self.coverage_threshold = _finite(coverage_threshold, "coverage_threshold")
        if not 0 <= self.confidence_threshold <= 1 or not 0 <= self.coverage_threshold <= 1:
            raise ValueError("confidence and coverage thresholds must be in [0, 1]")
        self.manifest = _manifest(self.model_root)
        self._torch = self._clip_model = self._clip_processor = self._owl_model = self._owl_processor = None

    def _load_base(self) -> None:
        if self._torch is not None:
            return
        import torch
        torch.set_num_threads(self.threads)
        self._torch = torch

    def _load_clip(self) -> None:
        if self._clip_model is not None:
            return
        self._load_base()
        from transformers import AutoProcessor, CLIPModel
        directory = str(self.model_root / self.manifest["models"]["clip"]["directory"])
        self._clip_processor = AutoProcessor.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False, use_fast=False
        )
        self._clip_model = CLIPModel.from_pretrained(directory, local_files_only=True, use_safetensors=True).to(self.device).eval()

    def _load_owl(self) -> None:
        if self._owl_model is not None:
            return
        self._load_base()
        from transformers import AutoProcessor, Owlv2ForObjectDetection
        directory = str(self.model_root / self.manifest["models"]["owl"]["directory"])
        self._owl_processor = AutoProcessor.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False, use_fast=False
        )
        self._owl_model = Owlv2ForObjectDetection.from_pretrained(directory, local_files_only=True, use_safetensors=True).to(self.device).eval()

    def _provenance(self) -> dict:
        return {key: {"repo_id": value["repo_id"], "revision": value["revision"], "files_sha256": {f["path"]: f["sha256"] for f in value["files"]}} for key, value in self.manifest["models"].items()}

    def predict_type_a(self, tile_paths: list[Path], target: str) -> dict:
        from PIL import Image
        started = time.perf_counter()
        if not tile_paths or not target.strip():
            raise ValueError("tiles and target are required")
        self._load_clip()
        images = []
        for path in tile_paths:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
        prompts = [f"a photo of {target}", "a photo of something else"]
        inputs = self._clip_processor(text=prompts, images=images, return_tensors="pt", padding=True).to(self.device)
        with self._torch.inference_mode():
            image_emb = self._clip_model.get_image_features(pixel_values=inputs["pixel_values"])
            text_emb = self._clip_model.get_text_features(input_ids=inputs["input_ids"], attention_mask=inputs.get("attention_mask"))
            image_emb = self._torch.nn.functional.normalize(image_emb, dim=-1)
            text_emb = self._torch.nn.functional.normalize(text_emb, dim=-1)
            cosine = image_emb @ text_emb.T
        positive = cosine[:, 0].cpu().tolist()
        negative = cosine[:, 1].cpu().tolist()
        selection = select_clip_tiles(positive, negative, self.clip_threshold)
        return {"selection": selection, "scores": [float(p - n) for p, n in zip(positive, negative)], "target": target, "challenge_type": "Type A", "model": self._provenance(), "elapsed_ms": (time.perf_counter() - started) * 1000}

    def predict_type_b(self, image_path: Path, target: str, rows: int = 4, columns: int = 4) -> dict:
        from PIL import Image
        started = time.perf_counter()
        rows, columns = _grid_int(rows, "rows"), _grid_int(columns, "columns")
        if not target.strip():
            raise ValueError("target is required")
        self._load_owl()
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        inputs = self._owl_processor(text=[[target]], images=image, return_tensors="pt").to(self.device)
        with self._torch.inference_mode():
            outputs = self._owl_model(**inputs)
        result = self._owl_processor.post_process_object_detection(outputs, target_sizes=self._torch.tensor([[image.height, image.width]], device=self.device), threshold=0.0)[0]
        boxes = result["boxes"].detach().cpu().tolist()
        scores = result["scores"].detach().cpu().tolist()
        selection, tile_scores = boxes_to_tiles(boxes, scores, image.size, rows, columns, self.confidence_threshold, self.coverage_threshold)
        return {"selection": selection, "scores": tile_scores, "target": target, "challenge_type": "Type B", "boxes": boxes, "detection_scores": scores, "model": self._provenance(), "elapsed_ms": (time.perf_counter() - started) * 1000}
