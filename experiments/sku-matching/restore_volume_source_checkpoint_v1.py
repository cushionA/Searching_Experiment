#!/usr/bin/env python3
"""Restore byte-exact logical captures to the pinned physical source layout."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".lab-output/sku-volume-unused-test-20261013-v1"
SOURCE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    manifest = json.loads((BASE / "prepared-source-v1/manifest.json").read_text())
    inventory = SOURCE.parent / "inventory.json"
    if digest(inventory) != manifest["source_bundle_inventory_sha256"]:
        raise ValueError("source bundle inventory does not match the frozen source")
    captures = json.loads((BASE / "prepared-source-v1/raw-source-capture-inventory.json").read_text())["captures"]
    copied = 0
    for capture in captures:
        relative = Path(capture["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid capture path")
        original, target = ROOT / relative, SOURCE / relative
        if target.exists():
            if target.stat().st_size != capture["bytes"] or digest(target) != capture["sha256"]:
                raise ValueError("existing physical capture differs from frozen bytes")
            continue
        if original.stat().st_size != capture["bytes"] or digest(original) != capture["sha256"]:
            raise ValueError("archived logical capture differs from frozen bytes")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output, original.open("rb") as source:
            shutil.copyfileobj(source, output)
        if target.stat().st_size != capture["bytes"] or digest(target) != capture["sha256"]:
            raise ValueError("restored physical capture differs from frozen bytes")
        copied += 1
    print(json.dumps({"verified_captures": len(captures), "copied_captures": copied,
                      "semantic_input_bytes_changed": False}))


if __name__ == "__main__":
    main()
