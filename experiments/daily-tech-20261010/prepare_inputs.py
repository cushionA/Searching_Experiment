"""Download five tiny upstream MIT regression PDFs at an immutable revision."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
REVISION = "d0f55469c56d38d93ed47049b8c9d17f6785e94b"
BASE = f"https://raw.githubusercontent.com/docling-project/docling/{REVISION}/"
# Coordinates and expected eligibility adapted from upstream's MIT-licensed
# tests/test_ocr_rects_vector_shapes.py. These are upstream regression cases,
# NOT an independent corpus or OCR transcript ground truth.
CASES = [
    ("ruled_text", "text_with_vector_rule.pdf", [60,70,380,105], False),
    ("empty", "text_with_vector_rule.pdf", [60,400,380,450], True),
    ("native_and_vector", "text_with_vector_glyphs.pdf", [30,25,220,75], True),
    ("vector_only", "text_with_vector_glyphs.pdf", [140,125,200,175], True),
    ("highlight", "text_with_vector_glyphs.pdf", [30,225,180,260], False),
    ("stroked_glyphs", "vector_glyph_edge_cases.pdf", [30,70,220,105], True),
    ("rule_through_glyphs", "vector_glyph_edge_cases.pdf", [30,170,220,205], True),
    ("filled_rule", "vector_glyph_edge_cases.pdf", [30,235,280,270], False),
    ("glyphs_fused_with_panel", "vector_glyphs_merged_fill.pdf", [30,25,220,75], True),
    ("panel_edge", "vector_glyphs_merged_fill.pdf", [30,125,130,175], False),
    ("figure_with_caption", "vector_glyphs_in_figure.pdf", [30,25,280,120], True),
    ("shaded_band", "vector_glyphs_in_figure.pdf", [30,130,280,165], False),
    ("shaded_sidebar", "vector_glyphs_in_figure.pdf", [30,175,280,275], False),
]

def main():
    target = ROOT / "inputs"
    target.mkdir(exist_ok=False)
    records = []
    paths = ["tests/data/pdf/" + x for x in sorted({c[1] for c in CASES})]
    paths += ["LICENSE", "tests/test_ocr_rects_vector_shapes.py"]
    for path in paths:
        start = time.perf_counter()
        with urllib.request.urlopen(BASE + path, timeout=30) as response:
            data = response.read(500_001)
        if len(data) > 500_000:
            raise ValueError("input exceeds 500 KB")
        filename = path.rsplit("/", 1)[-1]
        (target / filename).write_bytes(data)
        records.append({"file": filename, "url": BASE + path, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "seconds": time.perf_counter() - start})
    protocol = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "revision": REVISION, "files": records,
                "cases": [dict(zip(["id", "pdf", "bbox_ltrb", "expected_ocr_eligible"], c)) for c in CASES],
                "versions": ["2.135.0", "2.137.0"], "warm_repeats": 20,
                "scope": "fixed upstream regions; no layout inference or OCR recognition; three backend paths"}
    (ROOT / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    print(json.dumps({"files": len(records), "bytes": sum(r["bytes"] for r in records)}))

if __name__ == "__main__":
    main()
