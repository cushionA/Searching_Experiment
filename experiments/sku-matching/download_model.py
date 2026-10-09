#!/usr/bin/env python3
"""Download the pinned, quantized public Hugging Face model into a local directory."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "model-manifest.json").read_text(encoding="utf-8"))
DEFAULT_DEST = HERE.parents[1] / ".deps" / "sku-matching-model"


def fetch_file(repo: str, revision: str, filename: str, expected: dict, dest: Path) -> None:
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": "sku-matching-experiment/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response, tmp.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    actual = digest.hexdigest()
    if size != expected["size_bytes"] or actual != expected["sha256"]:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Integrity check failed for {filename}: got {size} bytes sha256={actual}"
        )
    os.replace(tmp, dest)
    print(f"verified {filename}: {size:,} bytes sha256={actual}")


def main() -> int:
    dest_root = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else DEFAULT_DEST
    for filename, expected in MANIFEST["files"].items():
        dest = dest_root / Path(filename).name
        if dest.exists():
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            if dest.stat().st_size == expected["size_bytes"] and digest == expected["sha256"]:
                print(f"verified existing {dest.name}: {dest.stat().st_size:,} bytes sha256={digest}")
                continue
            raise RuntimeError(f"Existing file does not match pinned manifest: {dest}")
        fetch_file(MANIFEST["repo"], MANIFEST["revision"], filename, expected, dest)
    (dest_root / "manifest.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"model directory: {dest_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
