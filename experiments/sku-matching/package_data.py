"""Package this experiment's explicit data directories; verify every entry."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
DIRECTORIES = (
    ".lab-output/sku-real-au-20261009",
    ".lab-output/sku-real-au-select10-20261009",
    ".lab-output/sku-real-rakuten-20261009",
    ".lab-output/sku-synthetic-controls-20261009",
    ".lab-output/sku-observation-20261009",
)


def sha_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_archive(output):
    output = output.resolve()
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("SKU-DATA-MANIFEST.json"))
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC verification failed")
        for name, meta in manifest["files"].items():
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != meta["sha256"]:
                    raise RuntimeError(f"Archived SHA256 mismatch: {name}")

        def count_lines(name):
            with archive.open(name) as source:
                return sum(bool(line.strip()) for line in source)

        observed_products = {}
        observed_skus = {}
        for key, directory in zip(("au_weimall", "au_select10", "rakuten_weimall"), DIRECTORIES[:3]):
            observed_products[key] = count_lines(directory + "/products.jsonl")
            observed_skus[key] = count_lines(directory + "/skus.jsonl")
        synthetic = json.loads(archive.read(DIRECTORIES[3] + "/manifest.json"))
    summary = {"archive": output.relative_to(ROOT).as_posix() if output.is_relative_to(ROOT) else str(output),
               "archive_bytes": output.stat().st_size, "sha256": sha_file(output), "files": len(manifest["files"]),
               "observed_products": observed_products, "observed_skus": observed_skus,
               "synthetic": {"product_pairs": synthetic["pair_count"],
                             "sku_records": synthetic["pair_count"] * (synthetic["au_skus_per_pair"] + synthetic["rakuten_skus_per_pair"]),
                             "evaluation_cases": synthetic["evaluation_cases"]}}
    output.with_suffix(".manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".zip.sha256").write_text(f"{summary['sha256']}  {output.name}\n", encoding="utf-8")
    return summary


def package(output):
    output = output.resolve()
    files = []
    for directory in DIRECTORIES:
        path = ROOT / directory
        if not path.is_dir():
            raise ValueError(f"Missing collected directory: {directory}")
        for source in sorted(path.iterdir()):
            if source.suffix in (".json", ".jsonl", ".html"):
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"Unexpected source: {source}")
                files.append(source)
    manifest = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                "scope": "Observed public source responses and separate synthetic controls; no model weights or runtime credentials",
                "files": {source.relative_to(ROOT).as_posix(): {"bytes": source.stat().st_size, "sha256": sha_file(source)}
                          for source in files}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in files:
            archive.write(source, source.relative_to(ROOT).as_posix())
        archive.writestr("SKU-DATA-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return verify_archive(output)


def restore(archive_path):
    """Verify everything before extracting; matching existing files are reused."""
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("SKU-DATA-MANIFEST.json"))
        for name, meta in manifest["files"].items():
            target = ROOT / name
            if name.startswith("/") or ".." in Path(name).parts or not target.resolve().is_relative_to(ROOT):
                raise ValueError("Unsafe checkpoint path")
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != meta["sha256"]:
                    raise RuntimeError(f"Archived SHA256 mismatch: {name}")
            if target.exists() and (target.is_symlink() or sha_file(target) != meta["sha256"]):
                raise ValueError(f"Refusing to overwrite different existing data: {target}")
        restored = 0
        for name in manifest["files"]:
            target = ROOT / name
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open("xb") as dest:
                while block := source.read(1024 * 1024):
                    dest.write(block)
            restored += 1
    return {"verified_entries": len(manifest["files"]), "restored_entries": restored}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--restore", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = restore(args.restore) if args.restore else verify_archive(args.verify) if args.verify else package(args.output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
