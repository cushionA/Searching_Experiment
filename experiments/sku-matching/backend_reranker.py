"""Pinned CPU ONNX cross-encoder for Japanese SKU candidate reranking."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, TypeAlias

import numpy as np

Pair: TypeAlias = tuple[str, str]


class Model:
    """Run the pinned xsmall-v2 cross-encoder on CPU and return raw relevance logits.

    The source model's official Transformers usage tokenizes ``(query, passage)``
    pairs with right padding, truncation, and max_length=512. The source's example
    applies sigmoid to its one-value regression logit; that monotonic transform
    does not affect ranking, so this adapter deliberately exposes raw logits and
    identifies them as such in ``metadata``.
    """

    MAX_LENGTH = 512
    SCORE_KIND = "raw_logit"

    def __init__(self, model_dir: str | Path, threads: int = 2, verify_files: bool = True):
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Reranker dependencies are missing. Use the isolated sku-matching runtime "
                "with onnxruntime and tokenizers installed."
            ) from exc

        if threads < 1:
            raise ValueError("threads must be at least 1")
        self.model_dir = Path(model_dir)
        manifest_path = Path(__file__).with_name("manifests") / "reranker.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Pinned reranker manifest is missing: {manifest_path}")
        self.metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.metadata.get("max_length") != self.MAX_LENGTH:
            raise RuntimeError("Manifest max_length differs from the backend")
        if self.metadata.get("score_kind") != self.SCORE_KIND:
            raise RuntimeError("Manifest score_kind differs from the backend")
        self.max_length = self.MAX_LENGTH

        if verify_files:
            for relative, expected in self.metadata["files"].items():
                path = self.model_dir / relative
                if not path.is_file():
                    raise FileNotFoundError(f"Pinned reranker file is missing: {path}")
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as stream:
                    while chunk := stream.read(4 * 1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                if size != expected["size_bytes"] or digest.hexdigest() != expected["sha256"]:
                    raise RuntimeError(f"Pinned reranker file failed integrity check: {path}")
            local_manifest = self.model_dir / "manifest.json"
            if local_manifest.is_file():
                local_data = json.loads(local_manifest.read_text(encoding="utf-8"))
                if local_data != self.metadata:
                    raise RuntimeError("Downloaded reranker manifest differs from pinned manifest")

        tokenizer_path = self.model_dir / "tokenizer.json"
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._length_tokenizer = Tokenizer.from_file(str(tokenizer_path))
        pad_id = self._tokenizer.token_to_id(self.metadata["pad_token"])
        if pad_id is None or pad_id != self.metadata["pad_token_id"]:
            raise RuntimeError(f"Unexpected tokenizer pad token ID: {pad_id}")
        self._tokenizer.enable_truncation(
            max_length=self.MAX_LENGTH,
            strategy=self.metadata["pair_truncation"],
        )
        self._tokenizer.enable_padding(
            direction=self.metadata["padding"],
            pad_id=pad_id,
            pad_type_id=0,
            pad_token=self.metadata["pad_token"],
        )
        self._length_tokenizer.no_truncation()
        self._length_tokenizer.no_padding()

        options = ort.SessionOptions()
        options.intra_op_num_threads = int(threads)
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        model_path = self.model_dir / self.metadata["onnx_file"]
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        if self._session.get_providers() != ["CPUExecutionProvider"]:
            raise RuntimeError(f"Expected CPUExecutionProvider, got {self._session.get_providers()!r}")
        self._input_names = {item.name for item in self._session.get_inputs()}
        if not {"input_ids", "attention_mask"}.issubset(self._input_names):
            raise RuntimeError(f"Unexpected ONNX inputs: {sorted(self._input_names)}")
        outputs = self._session.get_outputs()
        if len(outputs) != 1 or outputs[0].name != "logits":
            raise RuntimeError(f"Unexpected ONNX outputs: {[(o.name, o.shape) for o in outputs]}")
        self._output_shape = outputs[0].shape

    @staticmethod
    def _validate_pairs(pairs: Iterable[Pair]) -> list[Pair]:
        values = list(pairs)
        for index, pair in enumerate(values):
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise TypeError(f"pair at index {index} must contain exactly two strings")
            if not all(isinstance(text, str) for text in pair):
                raise TypeError(f"both texts in pair at index {index} must be strings")
        return [(pair[0], pair[1]) for pair in values]

    def token_lengths_pairs(self, pairs: Iterable[Pair]) -> np.ndarray:
        """Return untruncated pair token counts, including model special tokens."""
        values = self._validate_pairs(pairs)
        if not values:
            return np.empty((0,), dtype=np.int32)
        encodings = self._length_tokenizer.encode_batch(values)
        return np.asarray([len(encoding.ids) for encoding in encodings], dtype=np.int32)

    def score_pairs(self, pairs: list[Pair], batch_size: int = 8) -> np.ndarray:
        """Return one uncalibrated raw relevance logit per pair, in input order."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        values = self._validate_pairs(pairs)
        if not values:
            return np.empty((0,), dtype=np.float32)

        scores: list[np.ndarray] = []
        for offset in range(0, len(values), batch_size):
            batch = values[offset : offset + batch_size]
            encodings = self._tokenizer.encode_batch(batch)
            ids = np.asarray([item.ids for item in encodings], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in encodings], dtype=np.int64)
            logits = np.asarray(
                self._session.run(None, {"input_ids": ids, "attention_mask": mask})[0],
                dtype=np.float32,
            )
            if logits.ndim == 1:
                logits = logits.reshape(-1, 1)
            if logits.shape != (len(batch), 1):
                raise RuntimeError(
                    f"Expected one regression logit per pair, got output shape {logits.shape}"
                )
            scores.append(logits[:, 0])
        return np.concatenate(scores).astype(np.float32, copy=False)
