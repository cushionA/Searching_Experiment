#!/usr/bin/env python3
"""Download and verify a pinned SKU model snapshot from its Hugging Face revision."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any
from urllib.parse import quote
import urllib.request


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST_DIR = HERE / "manifests"
DEFAULT_DIRS = {
    "bekko": ".deps/sku-bekko-model",
    "granite": ".deps/sku-granite-model",
    "ruri": ".deps/sku-ruri-model",
    "reranker": ".deps/sku-reranker-model",
    "gliner-extract": ".deps/sku-gliner-extract-model",
}
CHUNK_SIZE = 8 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"Unsafe manifest path: {value!r}")
    return path.as_posix()


def _resolve_identity(manifest: dict[str, Any]) -> tuple[str, str]:
    repo = manifest.get("repo") or manifest.get("candidate")
    revision = manifest.get("revision")
    if not revision:
        for source in manifest.get("official_sources", []):
            if source.get("revision"):
                revision = source["revision"]
                break
            fields = source.get("observed_fields", {})
            if fields.get("revision"):
                revision = fields["revision"]
                break
    if not repo or not revision:
        raise ValueError("Manifest must pin a Hugging Face repo and full revision")
    return str(repo), str(revision)


def _gliner_source_hashes() -> dict[str, dict[str, Any]]:
    """Read the already pinned extraction file checksums without importing its runtime."""
    source = HERE / "backend_gliner_extract.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "FILES" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "FILES":
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return value
    raise ValueError("Could not locate the pinned GLiNER extraction FILES mapping")


def _file_records(name: str, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    files = manifest.get("files")
    if isinstance(files, dict) and files:
        records = files
    elif name == "gliner-extract":
        # This manifest records the official tree listing and download filenames;
        # the extraction adapter is the repository's hash-pinned source of content
        # digests for this >1 GB checkpoint and its config files.
        adapter_files = _gliner_source_hashes()
        requested = manifest.get("download", {}).get("files", [])
        if not requested:
            raise ValueError("GLiNER extraction manifest has no download.files list")
        records = {}
        for file_key in requested:
            if file_key not in adapter_files:
                raise ValueError(f"No pinned size and SHA-256 for GLiNER file {file_key!r}")
            records[file_key] = adapter_files[file_key]
    else:
        raise ValueError(f"Manifest for {name} has no pinned files mapping")

    checked: dict[str, dict[str, Any]] = {}
    for key, metadata in records.items():
        source_path = _safe_relative_path(str(metadata.get("source_path", key)))
        local_path = _safe_relative_path(str(key))
        size = metadata.get("size_bytes")
        digest = metadata.get("sha256") or metadata.get("lfs_sha256")
        if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"Manifest entry {key!r} must include size_bytes and SHA-256")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ValueError(f"Manifest entry {key!r} has an invalid SHA-256") from exc
        checked[local_path] = {
            "source_path": source_path,
            "size_bytes": size,
            "sha256": digest.lower(),
        }
    return checked


def _verify_file(path: Path, expected: dict[str, Any]) -> tuple[int, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    digest = _sha256(path)
    if size != expected["size_bytes"] or digest != expected["sha256"]:
        raise RuntimeError(
            f"Existing file does not match the pinned revision; refusing to overwrite: {path} "
            f"(got {size} bytes sha256={digest})"
        )
    return size, digest


def _download_file(repo: str, revision: str, local_name: str, expected: dict[str, Any], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    source_path = quote(expected["source_path"], safe="/")
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{source_path}"
    request = urllib.request.Request(url, headers={"User-Agent": "sku-candidate-model-download/1.0"})
    partial = dest.with_name(dest.name + ".partial")
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
            while chunk := response.read(CHUNK_SIZE):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        actual = digest.hexdigest()
        if size != expected["size_bytes"] or actual != expected["sha256"]:
            raise RuntimeError(
                f"Pinned integrity check failed for {local_name}: "
                f"got {size} bytes sha256={actual}"
            )
        os.replace(partial, dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    print(f"verified {local_name}: {size:,} bytes sha256={actual}", flush=True)


def _write_if_same_or_absent(path: Path, content: dict[str, Any]) -> None:
    encoded = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Existing local manifest cannot be read; refusing to overwrite: {path}") from exc
        if existing != content:
            raise RuntimeError(f"Existing local manifest differs; refusing to overwrite: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


def download_candidate(name: str, output_dir: Path) -> Path:
    if name not in DEFAULT_DIRS:
        raise ValueError(f"Unknown model {name!r}; choose one of {', '.join(DEFAULT_DIRS)}")
    manifest_path = MANIFEST_DIR / f"{name}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repo, revision = _resolve_identity(manifest)
    records = _file_records(name, manifest)
    output_dir.mkdir(parents=True, exist_ok=True)

    verified_files: dict[str, dict[str, Any]] = {}
    for local_name, expected in records.items():
        target = output_dir / local_name
        if target.exists():
            size, digest = _verify_file(target, expected)
            print(f"verified existing {local_name}: {size:,} bytes sha256={digest}", flush=True)
        else:
            _download_file(repo, revision, local_name, expected, target)
        verified_files[local_name] = {
            "size_bytes": expected["size_bytes"],
            "sha256": expected["sha256"],
        }

    # Preserve the candidate record exactly; also maintain GLiNER's established
    # compact source-manifest.json sidecar for its offline loader workflow.
    _write_if_same_or_absent(output_dir / "manifest.json", manifest)
    if name == "gliner-extract":
        _write_if_same_or_absent(
            output_dir / "source-manifest.json",
            {"repo": repo, "revision": revision, "files": verified_files},
        )
    return output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=DEFAULT_DIRS, required=True)
    parser.add_argument("--output-dir", type=Path, help="destination (defaults to the model's .deps directory)")
    args = parser.parse_args(argv)
    destination = args.output_dir or ROOT / DEFAULT_DIRS[args.model]
    destination = destination.expanduser().resolve()
    try:
        path = download_candidate(args.model, destination)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        return 1
    print(f"model directory: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
