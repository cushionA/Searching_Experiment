"""Pinned pretrained CPU OCR adapter used by the offline text evaluation."""
from __future__ import annotations

import io
import time
from PIL import Image


class TextOCR:
    def __init__(self):
        import ddddocr
        import onnxruntime as ort

        started = time.perf_counter()
        self.engine = ddddocr.DdddOcr(show_ad=False, beta=False, use_gpu=False, ocr=True, det=False)
        graph_path = getattr(self.engine, "_DdddOcr__graph_path", None)
        if graph_path is None or not hasattr(self.engine, "_DdddOcr__ort_session"):
            raise RuntimeError("Unsupported ddddocr session API; this adapter targets version 1.5.6")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.engine._DdddOcr__ort_session = ort.InferenceSession(
            graph_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.providers = self.engine._DdddOcr__ort_session.get_providers()
        self.load_seconds = time.perf_counter() - started

    def predict(self, image: Image.Image) -> str:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        return str(self.engine.classification(buffer.getvalue()))
