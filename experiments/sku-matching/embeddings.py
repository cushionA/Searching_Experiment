"""Optional local multilingual sentence embeddings backed by quantized ONNX Runtime."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np


class EmbeddingModel:
    """Encode text with the pinned MiniLM ONNX model on CPU.

    The tokenizer and model files are downloaded separately by ``setup-runtime.sh``.
    Results use attention-mask mean pooling and are L2 normalized (384 dimensions).
    Repeated strings are served from a bounded in-process cache.
    """

    DIMENSIONS = 384
    MAX_LENGTH = 128
    CACHE_SIZE = 50_000

    def __init__(self, model_dir: str | Path, threads: int = 2, verify_files: bool = True):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Optional ONNX embedding dependencies are missing. Run "
                "experiments/sku-matching/setup-runtime.sh to install the isolated runtime."
            ) from exc

        model_dir = Path(model_dir)
        model_path = model_dir / "model_quantized.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        missing = [str(path) for path in (model_path, tokenizer_path) if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Pinned embedding model files are missing: " + ", ".join(missing) +
                ". Run experiments/sku-matching/setup-runtime.sh."
            )
        if threads < 1:
            raise ValueError("threads must be at least 1")

        if verify_files:
            manifest_path = model_dir / "manifest.json"
            if not manifest_path.is_file():
                raise FileNotFoundError(
                    f"Pinned model manifest is missing: {manifest_path}. Run setup-runtime.sh."
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            pinned = json.loads(
                Path(__file__).with_name("model-manifest.json").read_text(encoding="utf-8")
            )
            if manifest != pinned:
                raise RuntimeError("Downloaded model manifest differs from the pinned manifest")
            for filename, metadata in pinned["files"].items():
                path = model_dir / Path(filename).name
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as stream:
                    while chunk := stream.read(4 * 1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                if size != metadata["size_bytes"] or digest.hexdigest() != metadata["sha256"]:
                    raise RuntimeError(f"Pinned model file failed integrity check: {path}")

        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._length_tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._length_tokenizer.no_truncation()
        self._length_tokenizer.no_padding()
        pad_id = self._tokenizer.token_to_id("<pad>")
        if pad_id is None:
            raise RuntimeError("Tokenizer is missing the expected <pad> token")
        self._tokenizer.enable_truncation(max_length=self.MAX_LENGTH)
        self._tokenizer.enable_padding(pad_id=pad_id, pad_token="<pad>", direction="right")

        options = ort.SessionOptions()
        options.intra_op_num_threads = int(threads)
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        if self._session.get_providers() != ["CPUExecutionProvider"]:
            raise RuntimeError(
                "Expected CPUExecutionProvider, got " + repr(self._session.get_providers())
            )
        self._inputs = {item.name for item in self._session.get_inputs()}
        required = {"input_ids", "attention_mask"}
        if not required.issubset(self._inputs):
            raise RuntimeError(f"Unexpected ONNX model inputs: {sorted(self._inputs)}")
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()

    def encode(self, texts: Iterable[str], batch_size: int = 32) -> np.ndarray:
        """Return one normalized 384-float vector per input, preserving order."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        values = list(texts)
        if not values:
            return np.empty((0, self.DIMENSIONS), dtype=np.float32)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("all texts must be strings")

        result: list[np.ndarray | None] = [None] * len(values)
        misses: OrderedDict[str, list[int]] = OrderedDict()
        for index, text in enumerate(values):
            cached = self._cache.get(text)
            if cached is not None:
                self._cache.move_to_end(text)
                result[index] = cached
            else:
                misses.setdefault(text, []).append(index)

        unique_texts = list(misses)
        for offset in range(0, len(unique_texts), batch_size):
            batch_texts = unique_texts[offset : offset + batch_size]
            encodings = self._tokenizer.encode_batch(batch_texts)
            input_ids = np.asarray([item.ids for item in encodings], dtype=np.int64)
            attention_mask = np.asarray([item.attention_mask for item in encodings], dtype=np.int64)
            feed = {"input_ids": input_ids, "attention_mask": attention_mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.asarray(
                    [item.type_ids for item in encodings], dtype=np.int64
                )
            hidden = np.asarray(self._session.run(None, feed)[0], dtype=np.float32)
            if hidden.ndim != 3 or hidden.shape[0] != len(batch_texts) or hidden.shape[2] != self.DIMENSIONS:
                raise RuntimeError(f"Unexpected ONNX output shape: {hidden.shape}")
            mask = attention_mask.astype(np.float32, copy=False)[..., None]
            pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1.0)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            vectors = pooled / np.maximum(norms, 1e-12)
            for text, vector in zip(batch_texts, vectors, strict=True):
                stored = np.asarray(vector, dtype=np.float32)
                stored.setflags(write=False)
                self._cache[text] = stored
                self._cache.move_to_end(text)
                for index in misses[text]:
                    result[index] = stored
            while len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)

        return np.stack(result).astype(np.float32, copy=False)

    def token_lengths(self, texts: Iterable[str]) -> np.ndarray:
        """Return untruncated token counts, including special tokens.

        Compare these counts with ``MAX_LENGTH`` before encoding title+SKU strings;
        any value above the model limit is truncated from the right by ``encode``.
        """
        values = list(texts)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("all texts must be strings")
        if not values:
            return np.empty((0,), dtype=np.int32)
        encodings = self._length_tokenizer.encode_batch(values)
        return np.asarray([len(item.ids) for item in encodings], dtype=np.int32)
