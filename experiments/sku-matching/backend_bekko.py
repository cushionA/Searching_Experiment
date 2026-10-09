"""CPU sentence embeddings for the pinned bekko a8m ONNX export."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np


class Model:
    """Encode text with hotchpotch/bekko-embedding-v1-a8m on CPU.

    The official ONNX export returns token hidden states. This adapter applies
    the model's Sentence Transformers attention-mask mean pooling and L2
    normalization; the export itself does not contain a pooling head.
    """

    DIMENSIONS = 384
    MAX_LENGTH = 8192
    CACHE_SIZE = 50_000
    REPO = "hotchpotch/bekko-embedding-v1-a8m"

    def __init__(self, model_dir: str | Path, threads: int = 2, verify_files: bool = True):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Bekko requires the optional ONNX Runtime and tokenizers dependencies; "
                "use the SKU matching runtime environment."
            ) from exc

        model_dir = Path(model_dir)
        model_path = model_dir / "model.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        required_paths = (model_path, tokenizer_path)
        missing = [str(path) for path in required_paths if not path.is_file()]
        if missing:
            raise FileNotFoundError("Pinned bekko files are missing: " + ", ".join(missing))
        if threads < 1:
            raise ValueError("threads must be at least 1")

        manifest_path = Path(__file__).with_name("manifests") / "bekko.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Pinned bekko manifest is missing: {manifest_path}")
        self.model_identity = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.identity = f"{self.model_identity['repo']}@{self.model_identity['revision']}"
        if verify_files:
            for filename, metadata in self.model_identity["files"].items():
                path = model_dir / filename
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as stream:
                    while chunk := stream.read(4 * 1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                if size != metadata["size_bytes"] or digest.hexdigest() != metadata["sha256"]:
                    raise RuntimeError(f"Pinned bekko file failed integrity check: {path}")

        self.dimensions = self.DIMENSIONS
        self.max_length = self.MAX_LENGTH
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._length_tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._length_tokenizer.no_truncation()
        self._length_tokenizer.no_padding()
        pad_id = self._tokenizer.token_to_id("<pad>")
        if pad_id is None:
            raise RuntimeError("Bekko tokenizer is missing the expected <pad> token")
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
            raise RuntimeError("Expected CPUExecutionProvider, got " + repr(self._session.get_providers()))
        inputs = {item.name: item for item in self._session.get_inputs()}
        if not {"input_ids", "attention_mask"}.issubset(inputs):
            raise RuntimeError(f"Unexpected Bekko ONNX inputs: {sorted(inputs)}")
        outputs = self._session.get_outputs()
        if len(outputs) != 1 or outputs[0].name != "last_hidden_state":
            raise RuntimeError(
                "Expected the official token-state ONNX output 'last_hidden_state'; got "
                + repr([(item.name, item.shape) for item in outputs])
            )
        self._inputs = set(inputs)
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()

    def encode(
        self, texts: Iterable[str], batch_size: int = 32, mode: str = "similarity"
    ) -> np.ndarray:
        """Return normalized 384-float vectors in input order.

        The model card specifies empty query and document prompts and explicitly
        recommends the same encode call for both sides of retrieval. ``mode``
        is accepted for the shared benchmark backend interface; only symmetric
        similarity encoding is supported.
        """
        if mode != "similarity":
            raise ValueError("Bekko backend supports only mode='similarity'")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        values = list(texts)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("all texts must be strings")
        if not values:
            return np.empty((0, self.DIMENSIONS), dtype=np.float32)

        result: list[np.ndarray | None] = [None] * len(values)
        misses: OrderedDict[str, list[int]] = OrderedDict()
        for index, text in enumerate(values):
            cached = self._cache.get(text)
            if cached is None:
                misses.setdefault(text, []).append(index)
            else:
                self._cache.move_to_end(text)
                result[index] = cached

        unique_texts = list(misses)
        for offset in range(0, len(unique_texts), batch_size):
            batch_texts = unique_texts[offset : offset + batch_size]
            encodings = self._tokenizer.encode_batch(batch_texts)
            input_ids = np.asarray([item.ids for item in encodings], dtype=np.int64)
            attention_mask = np.asarray([item.attention_mask for item in encodings], dtype=np.int64)
            hidden = np.asarray(
                self._session.run(["last_hidden_state"], {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                })[0],
                dtype=np.float32,
            )
            expected = (len(batch_texts), input_ids.shape[1], self.DIMENSIONS)
            if hidden.shape != expected:
                raise RuntimeError(f"Unexpected Bekko ONNX output shape: {hidden.shape}; expected {expected}")
            mask = attention_mask.astype(np.float32, copy=False)[..., None]
            pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1.0)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            if not np.isfinite(norms).all() or np.any(norms <= 1e-12):
                raise RuntimeError("Bekko produced an invalid or zero sentence embedding")
            vectors = pooled / norms
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
        """Return tokenizer counts including BOS/EOS before sequence truncation."""
        values = list(texts)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("all texts must be strings")
        if not values:
            return np.empty((0,), dtype=np.int32)
        encodings = self._length_tokenizer.encode_batch(values)
        return np.asarray([len(item.ids) for item in encodings], dtype=np.int32)
