"""CPU Transformers backend for cl-nagoya Ruri v3 30M."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np


class Model:
    DIMENSIONS = 256
    MAX_LENGTH = 8192

    def __init__(self, model_dir: str | Path, threads: int = 2):
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("Ruri requires CPU torch and transformers from sku-gliner-venv") from exc
        if threads < 1:
            raise ValueError("threads must be at least 1")
        self.model_dir = Path(model_dir)
        self.metadata = json.loads(Path(__file__).with_name("manifests").joinpath("ruri.json").read_text())
        self._verify_files()
        torch.set_num_threads(int(threads))
        torch.set_num_interop_threads(1)
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            str(self.model_dir), local_files_only=True, trust_remote_code=False, use_fast=True)
        self._model = AutoModel.from_pretrained(
            str(self.model_dir), local_files_only=True, trust_remote_code=False)
        self._model.eval()
        self._model.to("cpu")

    def _verify_files(self) -> None:
        for filename, info in self.metadata["files"].items():
            path = self.model_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"Pinned Ruri file is missing: {path}")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                while chunk := stream.read(4 * 1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != info["size_bytes"] or digest.hexdigest() != info["sha256"]:
                raise RuntimeError(f"Pinned Ruri file failed integrity check: {path}")

    def encode(self, texts: Iterable[str], batch_size: int = 32, mode: str = "similarity") -> np.ndarray:
        """Return normalized 256D mean-pooled vectors with the official empty prefix."""
        if mode != "similarity":
            raise ValueError("Ruri v3 adapter supports symmetric similarity mode only")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        values = list(texts)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("all texts must be strings")
        if not values:
            return np.empty((0, self.DIMENSIONS), dtype=np.float32)
        results = []
        torch = self._torch
        for offset in range(0, len(values), batch_size):
            batch = values[offset:offset + batch_size]
            # Ruri v3 docs specify an empty prefix for semantic similarity.
            encoded = self._tokenizer(batch, padding=True, truncation=True,
                                      max_length=self.MAX_LENGTH, return_tensors="pt")
            with torch.inference_mode():
                hidden = self._model(**encoded).last_hidden_state
                mask = encoded["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            vectors = pooled.detach().cpu().numpy().astype(np.float32, copy=False)
            if vectors.shape != (len(batch), self.DIMENSIONS):
                raise RuntimeError(f"Unexpected Ruri embedding shape: {vectors.shape}")
            results.append(vectors)
        return np.concatenate(results, axis=0)

    def token_lengths(self, texts: Iterable[str]) -> np.ndarray:
        values = list(texts)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("all texts must be strings")
        if not values:
            return np.empty((0,), dtype=np.int32)
        counts = []
        for start in range(0, len(values), 128):
            batch = values[start:start + 128]
            encoded = self._tokenizer(batch, add_special_tokens=True, truncation=False, padding=False)
            counts.extend(len(ids) for ids in encoded["input_ids"])
        return np.asarray(counts, dtype=np.int32)
