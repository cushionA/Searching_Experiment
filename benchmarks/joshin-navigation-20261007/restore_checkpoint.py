#!/usr/bin/env python3
"""Restore deduplicated evidence blobs after extracting the checkpoint ZIP."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: restore_checkpoint.py EXTRACTED_CHECKPOINT NEW_OUTPUT_DIRECTORY")
    root = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    if not (root / "blob-map.json").is_file() or not (root / "shared-blobs").is_dir():
        raise SystemExit("checkpoint is missing blob-map.json or shared-blobs/")
    if output.exists():
        raise SystemExit(f"output already exists: {output}")
    mapping = json.loads((root / "blob-map.json").read_text(encoding="utf-8"))
    source_runs = root / "runs"
    if not source_runs.is_dir():
        raise SystemExit("checkpoint is missing runs/")
    shutil.copytree(source_runs, output / "runs")
    restored = 0
    for relative_cell, names in mapping.items():
        cell = (output / "runs" / relative_cell).resolve()
        try:
            cell.relative_to((output / "runs").resolve())
        except ValueError as error:
            raise SystemExit(f"invalid cell path in blob map: {relative_cell}") from error
        blob_dir = cell / "blobs"
        blob_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            if len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
                raise SystemExit(f"invalid blob name: {name}")
            source = root / "shared-blobs" / name
            if not source.is_file() or digest(source) != name:
                raise SystemExit(f"missing or corrupt shared blob: {name}")
            shutil.copyfile(source, blob_dir / name)
            restored += 1
    receipt = {"restored_evidence_blobs": restored, "evidence_cells": len(mapping)}
    (output / "RESTORE.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
