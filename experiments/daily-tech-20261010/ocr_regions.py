"""CPU mechanism reproduction, not a full OCR or independent accuracy benchmark.

Fixture geometry / minimal model adapter adapted from Docling's MIT-licensed
test_ocr_rects_vector_shapes.py; copyright The Docling Contributors.
See inputs/LICENSE and inputs/test_ocr_rects_vector_shapes.py.
"""
import argparse
import datetime as dt
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import time
import traceback

START = time.perf_counter()
from docling_core.types.doc import BoundingBox, CoordOrigin
from docling_core.types.doc.labels import DocItemLabel
from docling.backend.docling_parse_backend import ThreadedDoclingParseDocumentBackend
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import Cluster, InputFormat, LayoutPrediction, Page
from docling.datamodel.document import InputDocument
from docling.datamodel.pipeline_options import OcrMode, OcrOptions
from docling.models.base_ocr_model import BaseOcrModel
import docling.models.base_ocr_model as implementation
IMPORT_SECONDS = time.perf_counter() - START

class Selector(BaseOcrModel):
    def __call__(self, *args):
        raise RuntimeError("OCR recognition is deliberately not run")

    @classmethod
    def get_options_type(cls):
        return OcrOptions

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    raw_protocol = (root / "protocol.json").read_bytes()
    protocol = json.loads(raw_protocol)
    for entry in protocol["files"]:
        assert hashlib.sha256((root / "inputs" / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]
    rows = []
    for path_name, backend_cls, native in [
        ("threaded", ThreadedDoclingParseDocumentBackend, True),
        ("pypdfium2", PyPdfiumDocumentBackend, True),
        ("threaded-spatial", ThreadedDoclingParseDocumentBackend, False),
    ]:
        for case in protocol["cases"]:
            record = {"backend": path_name, **case}
            doc = None
            try:
                t = time.perf_counter()
                doc = InputDocument(path_or_stream=root / "inputs" / case["pdf"],
                                    format=InputFormat.PDF, backend=backend_cls)._backend
                backend = next(iter(doc.iter_pages())) if backend_cls is ThreadedDoclingParseDocumentBackend else doc.load_page(0)
                if not native:
                    backend.has_content_in = lambda **kwargs: None
                bbox = BoundingBox(**dict(zip(["l", "t", "r", "b"], case["bbox_ltrb"])), coord_origin=CoordOrigin.TOPLEFT)
                page = Page(page_no=0)
                page._backend = backend
                page.size = backend.get_size()
                page.predictions.layout = LayoutPrediction(clusters=[Cluster(id=0, label=DocItemLabel.TEXT, bbox=bbox)])
                model = Selector(enabled=True, artifacts_path=None,
                                 options=OcrOptions(kind="test", lang=["en"], mode=OcrMode.PDF_AWARE_LAYOUT_REGIONS),
                                 accelerator_options=AcceleratorOptions())
                record["setup_seconds"] = time.perf_counter() - t
                record["native_text"] = backend.get_text_in_rect(bbox)
                t = time.perf_counter()
                rectangles = model._find_pdf_aware_layout_ocr_rects(page)
                record["first_selection_ms"] = (time.perf_counter() - t) * 1000
                record["rectangles"] = [box.model_dump(mode="json") for box in rectangles]
                record["ocr_eligible"] = bool(rectangles)
                record["matches_upstream_expectation"] = bool(rectangles) == case["expected_ocr_eligible"]
                timings = []
                for _ in range(protocol["warm_repeats"]):
                    t = time.perf_counter()
                    repeat = model._find_pdf_aware_layout_ocr_rects(page)
                    timings.append((time.perf_counter() - t) * 1000)
                    assert [box.model_dump(mode="json") for box in repeat] == record["rectangles"]
                record["warm_selection_ms"] = timings
                record["warm_median_ms"] = statistics.median(timings)
            except Exception:
                record["error"] = traceback.format_exc()
            finally:
                if doc is not None:
                    doc.unload()
            rows.append(record)
    report = {
        "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "version": metadata.version("docling-slim"),
        "dependencies": {key: metadata.version(key) for key in ["docling-core", "docling-parse", "pypdfium2", "rtree", "numpy", "scipy"]},
        "python": platform.python_version(), "platform": platform.platform(),
        "visible_cpu_count": os.cpu_count(), "cpu_affinity_count": len(os.sched_getaffinity(0)),
        "implementation_sha256": hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256(raw_protocol).hexdigest(),
        "import_seconds": IMPORT_SECONDS, "elapsed_seconds": time.perf_counter() - START,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "rows": rows,
    }
    with args.output.open("x") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"version": report["version"], "rows": len(rows), "errors": sum("error" in r for r in rows),
                      "eligible": sum(r.get("ocr_eligible", False) for r in rows),
                      "expectation_matches": sum(r.get("matches_upstream_expectation", False) for r in rows)}))

if __name__ == "__main__":
    main()
