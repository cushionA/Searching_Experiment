"""CPU ONNX backend for IBM Granite Embedding 97M Multilingual R2."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np


class Model:
    DIMENSIONS = 384
    MAX_LENGTH = 32768
    CACHE_SIZE = 50_000

    def __init__(self, model_dir: str | Path, threads: int = 2):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError("Granite requires onnxruntime and tokenizers from sku-matching-venv") from exc
        if threads < 1:
            raise ValueError("threads must be at least 1")
        self.model_dir = Path(model_dir)
        self.metadata = json.loads(Path(__file__).with_name("manifests").joinpath("granite.json").read_text())
        self._verify_files()
        self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self._length_tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self._length_tokenizer.no_truncation()
        self._length_tokenizer.no_padding()
        self._tokenizer.enable_truncation(max_length=self.MAX_LENGTH)
        self._tokenizer.enable_padding(pad_id=179935, pad_token="<|endoftext|>", direction="right")
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(threads)
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = ort.InferenceSession(
            str(self.model_dir / "onnx/model_quint8_avx2.onnx"),
            sess_options=options, providers=["CPUExecutionProvider"])
        if self._session.get_providers() != ["CPUExecutionProvider"]:
            raise RuntimeError(f"Expected CPUExecutionProvider, got {self._session.get_providers()!r}")
        self._inputs = {item.name for item in self._session.get_inputs()}
        if not {"input_ids", "attention_mask"}.issubset(self._inputs):
            raise RuntimeError(f"Unexpected Granite ONNX inputs: {sorted(self._inputs)}")
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()

    def _verify_files(self) -> None:
        for filename, info in self.metadata["files"].items():
            path = self.model_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"Pinned Granite file is missing: {path}")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                while chunk := stream.read(4 * 1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != info["size_bytes"] or digest.hexdigest() != info["sha256"]:
                raise RuntimeError(f"Pinned Granite file failed integrity check: {path}")

    def encode(self, texts: Iterable[str], batch_size: int = 32, mode: str = "similarity") -> np.ndarray:
        """Return normalized 384D CLS vectors; similarity uses plain symmetric text."""
        if mode != "similarity":
            raise ValueError("Granite adapter supports symmetric similarity mode only")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        values = list(texts)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("all texts must be strings")
        if not values:
            return np.empty((0, self.DIMENSIONS), dtype=np.float32)
        result: list[np.ndarray | None] = [None] * len(values)
        misses: OrderedDict[str, list[int]] = OrderedDict()
        for index, value in enumerate(values):
            cached = self._cache.get(value)
            if cached is None:
                misses.setdefault(value, []).append(index)
            else:
                self._cache.move_to_end(value)
                result[index] = cached
        unique = list(misses)
        for offset in range(0, len(unique), batch_size):
            batch = unique[offset:offset + batch_size]
            encodings = self._tokenizer.encode_batch(batch)
            ids = np.asarray([item.ids for item in encodings], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in encodings], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feeds["token_type_ids"] = np.asarray([item.type_ids for item in encodings], dtype=np.int64)
            hidden = np.asarray(self._session.run(None, feeds)[0], dtype=np.float32)
            if hidden.ndim == 3:
                pooled = hidden[:, 0, :]  # official model card: CLS pooling
            elif hidden.ndim == 2 and hidden.shape[1] == self.DIMENSIONS:
                pooled = hidden
            else:
                raise RuntimeError(f"Unexpected Granite ONNX output shape: {hidden.shape}")
            if pooled.shape != (len(batch), self.DIMENSIONS):
                raise RuntimeError(f"Unexpected Granite embedding shape: {pooled.shape}")
            vectors = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            for text, vector in zip(batch, vectors, strict=True):
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
        values = list(texts)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("all texts must be strings")
        if not values:
            return np.empty((0,), dtype=np.int32)
        return np.asarray([len(item.ids) for item in self._length_tokenizer.encode_batch(values)], dtype=np.int32)
