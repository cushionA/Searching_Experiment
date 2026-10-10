#!/usr/bin/env python3
"""Download the audited ModernBERT choice scorer without remote Python code."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request

MODEL_ID = "argos1111/modernbert-ja-310m-jev"
REVISION = "07cda23579443e7a33c0f474114279fa032340d6"
FILES = ("README.md", "config.json", "jev_modernbert.json",
         "tokenizer_config.json", "tokenizer.json", "model.safetensors")
ROOT = Path(__file__).resolve().parents[2]


def fetch(destination: Path, audit_dir: Path) -> dict:
    tree = {entry["path"]: entry for entry in json.loads(
        (audit_dir / "repo-tree.json").read_text(encoding="utf-8"))}
    if sum(tree[name]["size"] for name in FILES) > 1_300_000_000:
        raise ValueError("audited model exceeds download budget")
    destination.mkdir(parents=True, exist_ok=True)
    verified = {}
    for name in FILES:
        entry = tree[name]
        source = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}"
        path = destination / name
        if not path.exists():
            partial = path.with_suffix(path.suffix + ".partial")
            with urllib.request.urlopen(source, timeout=60) as response, partial.open("wb") as out:
                while chunk := response.read(8 * 1024 * 1024):
                    out.write(chunk)
            os.replace(partial, path)
        content_sha = hashlib.sha256()
        git_sha = hashlib.sha1(f"blob {entry['size']}\0".encode())
        with path.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                content_sha.update(chunk)
                git_sha.update(chunk)
        sha = content_sha.hexdigest()
        if path.stat().st_size != entry["size"]:
            raise ValueError(f"audited size mismatch: {name}")
        if entry.get("lfs"):
            if sha != entry["lfs"]["oid"]:
                raise ValueError(f"audited LFS hash mismatch: {name}")
        elif git_sha.hexdigest() != entry["oid"]:
            raise ValueError(f"audited Git blob hash mismatch: {name}")
        verified[name] = {"sha256": sha, "size_bytes": entry["size"], "source_url": source}
        print(f"verified {name}: {entry['size']} bytes sha256={sha}", flush=True)
    manifest = {"schema_version": "generic_model_jev_provenance_v1",
                "model_id": MODEL_ID, "revision": REVISION,
                "format_version": "modernbert-jev/1", "files": verified}
    (destination / "provenance.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=ROOT / ".deps/sku-modernbert-jev-model")
    parser.add_argument("--audit-dir", type=Path, default=ROOT / ".lab-output/sku-modernbert-jev-discovery-20261010-v1")
    arguments = parser.parse_args()
    fetch(arguments.model_dir, arguments.audit_dir)
