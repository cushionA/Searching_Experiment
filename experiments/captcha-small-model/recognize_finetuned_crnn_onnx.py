#!/usr/bin/env python3
"""Standalone CPU ONNX inference for the exported fine-tuned CAPTCHA CRNN.

Runtime dependencies: onnxruntime, NumPy and Pillow. No PyTorch or training files
are needed. The graph hash must be specified explicitly. Whole images use the
original grayscale/bicubic150x40/[0,1] processor and greedy CTC decoder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


VOCABULARY = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def preprocess(image: Any) -> Any:
    import numpy as np
    image = image.convert("L").resize((150, 40))
    # Both the division and resulting NCHW tensor stay float32, matching the
    # original torchvision.to_tensor uint8 -> float32 -> /255 conversion.
    return (np.asarray(image, dtype=np.float32) / np.float32(255.0))[None, :, :]


def greedy_ctc_decode(logits: Any) -> list[str]:
    import numpy as np
    answers = []
    for tokens in np.argmax(logits, axis=-1):
        previous, letters = None, []
        for value in tokens:
            token = int(value)
            if token != previous and token != 0:
                letters.append(VOCABULARY[token - 1])
            previous = token  # Blank resets adjacency, preserving a/blank/a.
        answers.append("".join(letters))
    return answers


class CRNNOnnxOCR:
    def __init__(self, graph: Path, expected_sha256: str):
        import onnxruntime as ort
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ValueError("model SHA-256 must be explicit lowercase hex")
        self.graph_sha256 = sha256_file(graph)
        if self.graph_sha256 != expected_sha256:
            raise ValueError("ONNX model SHA-256 mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(graph), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        if self.session.get_providers() != ["CPUExecutionProvider"]:
            raise RuntimeError("expected exclusive ONNX Runtime CPU provider")
        metadata = self.session.get_modelmeta().custom_metadata_map
        if metadata.get("vocabulary") != VOCABULARY or metadata.get("blank_index") != "0":
            raise ValueError("unexpected exported CRNN vocabulary/blank metadata")
        if metadata.get("input_processor") != "PIL-L-bicubic150x40-float32-div255":
            raise ValueError("unexpected exported CRNN processor metadata")
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or inputs[0].name != "pixel_values" or inputs[0].type != "tensor(float)" or inputs[0].shape[1:] != [1, 40, 150]:
            raise ValueError("unexpected CRNN input signature")
        if len(outputs) != 1 or outputs[0].name != "logits" or outputs[0].type != "tensor(float)":
            raise ValueError("unexpected CRNN output signature")
        self.metadata = metadata

    def infer_logits(self, images: list[Any]) -> Any:
        import numpy as np
        if not images:
            raise ValueError("empty image batch")
        batch = np.stack([preprocess(image) for image in images]).astype(np.float32, copy=False)
        outputs = self.session.run(["logits"], {"pixel_values": batch})
        logits = outputs[0]
        if logits.shape != (len(images), 37, 63) or logits.dtype != np.float32 or not np.isfinite(logits).all():
            raise RuntimeError("exported CRNN returned invalid logits")
        return logits

    def predict_batch(self, images: list[Any]) -> list[str]:
        return greedy_ctc_decode(self.infer_logits(images))

    def predict(self, image: Any) -> str:
        return self.predict_batch([image])[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--image", type=Path, nargs="+", required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("batch size must be positive")
    from PIL import Image
    ocr = CRNNOnnxOCR(args.model.resolve(), args.model_sha256)
    rows = []
    for start in range(0, len(args.image), args.batch_size):
        paths = args.image[start:start + args.batch_size]
        images = []
        for path in paths:
            with Image.open(path) as image:
                images.append(image.copy())
        answers = ocr.predict_batch(images)
        rows.extend({"path": str(path), "answer": answer} for path, answer in zip(paths, answers, strict=True))
    print(json.dumps({"model_sha256": ocr.graph_sha256, "device": "CPUExecutionProvider", "answers": rows}))


if __name__ == "__main__":
    main()
