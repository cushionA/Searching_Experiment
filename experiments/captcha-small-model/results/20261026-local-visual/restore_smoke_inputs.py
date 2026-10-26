"""Fetch only the 25 hash-pinned public tiles used in this functionality smoke."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new input directory")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    source = Path(__file__).with_name("smoke-inputs.json")
    manifest = json.loads(source.read_bytes())
    args.output.mkdir(parents=True)
    count = 0
    for case in manifest["cases"]:
        revision = case["commit"]
        for relative, expected in zip(case["tile_paths"], case["tile_sha256"], strict=True):
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe tile path")
            upstream_path = relative.removeprefix("data/recaptcha/")
            url = f"https://raw.githubusercontent.com/ssivakorn/reCAPTCHA-study/{revision}/{upstream_path}"
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read(10 * 1024 * 1024 + 1)
            if len(content) > 10 * 1024 * 1024:
                raise ValueError("Public tile exceeded the size limit")
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"Tile SHA-256 mismatch: {relative}")
            destination = args.output / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            count += 1
    shutil.copyfile(source, args.output / "manifest.json")
    print(json.dumps({"verified_tiles": count, "manifest": str(args.output / "manifest.json")}))


if __name__ == "__main__":
    main()
