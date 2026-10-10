#!/usr/bin/env python3
"""Read-only checks for this evidence checkpoint (no credentials/network)."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

RUN = Path(__file__).resolve().parent
EXPERIMENT = RUN.parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


manifest = read(RUN / "manifest.json")
assert digest(EXPERIMENT / "results/20261010-sku-generic-presence-checkpoint.zip") == manifest["source_input_hashes"]["zip_sha256"]
baseline = read(RUN / "r00-baseline/dispatch.json")
assert digest(RUN / "r00-baseline/inputs.json") == baseline["input_sha256"]
for entry, dispatch in zip(manifest["r00_requests"], baseline["calls"], strict=True):
    assert digest(RUN / entry["request"]) == entry["request_sha256"]
    assert digest(RUN / entry["answer"]) == dispatch["answer_sha256"]

links = []
for stage in ("r02-reference-development", "r03-reference-holdout"):
    path = RUN / stage
    subprocess.run([sys.executable, "-B", str(EXPERIMENT / "trial_luna_sku_matching.py"), "verify", "--round-dir", str(path)], check=True)
    cases = {c["case_id"]: c for c in read(path / "inputs.json")}
    stage_links = [json.loads(line) for line in (path / "links.jsonl").read_text().splitlines()]
    for link in stage_links:
        case = cases[link["case_id"]]
        row = next(r for r in case["au_rows"] if r["row_key"] == link["au_row_key"])
        assert link["rakuten_sku_key"] == case["rakuten_sku_key"]
        assert link["rakuten_variant_id"] == case["rakuten_variant_id"]
        assert link["au_sku_id"] == row["sku_id"]
        assert (link["au_row_index"], link["au_column_index"]) == (row["row_index"], row["column_index"])
    links.extend(stage_links)
assert links == [json.loads(line) for line in (RUN / "links.jsonl").read_text().splitlines()]
assert len(links) == 7 and len({x["case_id"] for x in links}) == 7
assert read(RUN / "summary.json")["counts"] == {"adopt": 7, "exclude": 9, "pending": 2}

checked = 0
for line in (RUN / "SHA256SUMS").read_text().splitlines():
    expected, rel = line.split("  ", 1)
    assert digest(RUN / rel) == expected, rel
    checked += 1
print(f"verified {checked} artifact hashes, frozen archive, and 7 original SKU/grid-cell links")
