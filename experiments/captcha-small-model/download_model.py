"""Download official TinyCLIP assets with pinned hashes and a finite deadline."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path


MODELS = {
    "small": {
        "architecture": "TinyCLIP-ViT-8M-16-Text-3M",
        "training": "YFCC15M",
        "config_sha256": "9ca063ed7d11827ec00ed8f07cf8754e864e23145db8ca6f2544acc0327b2c63",
        "weights_sha256": "3d9f86a556cd13acc1aa6cc18495a79ffd1f58b6f17a552400bdefd244c3a92c",
    },
    "medium": {
        "architecture": "TinyCLIP-ViT-40M-32-Text-19M",
        "training": "LAION400M",
        "config_sha256": "a5714f29d65f20d92ebe9896eba0da358e43e80569003f31c36cc993c5fa0a7b",
        "weights_sha256": "af5042c3e3662c43573c16113d4a72014cf3aa19267a7a50c4b94be3c4e8366a",
    },
}


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=MODELS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    spec = MODELS[args.size]
    architecture = spec["architecture"]
    files = [
        (f"{architecture}.json", f"https://raw.githubusercontent.com/wkcn/TinyCLIP/main/src/open_clip/model_configs/{architecture}.json", spec["config_sha256"]),
        (f"{architecture}-{spec['training']}.pt", f"https://github.com/wkcn/TinyCLIP-model-zoo/releases/download/checkpoints/{architecture}-{spec['training']}.pt", spec["weights_sha256"]),
    ]
    provenance = []
    for name, url, expected in files:
        destination = args.output / name
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".partial")
            deadline = time.monotonic() + 60
            try:
                with urllib.request.urlopen(url, timeout=25) as response, temporary.open("wb") as target:
                    while chunk := response.read(1024 * 1024):
                        if time.monotonic() > deadline:
                            raise TimeoutError("Model download exceeded its deadline")
                        target.write(chunk)
                if digest(temporary) != expected:
                    raise RuntimeError(f"Downloaded file hash does not match: {name}")
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        if digest(destination) != expected:
            raise RuntimeError(f"Cached file hash does not match: {name}")
        provenance.append({"name": name, "url": url, "bytes": destination.stat().st_size, "sha256": expected})
        print(f"Verified {name}: {destination.stat().st_size} bytes", flush=True)
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
