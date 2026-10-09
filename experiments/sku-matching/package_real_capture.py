"""Package explicitly selected real captures, keeping synthetic corpora out."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_PREFIXES = ("sku-real-au-", "sku-real-rakuten-")
DERIVED_PREFIXES = ("sku-observed-product-pairs-", "sku-fixed-au-capture-",
                    "sku-data-layout-", "sku-real-capture-audit-")
TABLE_EXTENSIONS = {".json", ".jsonl", ".csv", ".txt"}
RAW_EXTENSIONS = {".html", ".body"}
CODE = ("fetch_au.py", "collect_au_samples_expanded.py", "fetch_rakuten.py", "extract_rakuten_links.py",
        "match_skus.py", "prepare_real_pair.py", "check_fixed_au_capture.py", "audit_real_capture.py",
        "build_observed_pair_records.py", "package_real_capture.py", "real-data-handoff-20261010.txt")


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_directory(path, prefixes):
    path = path.resolve()
    if not path.is_relative_to(ROOT / ".lab-output"):
        raise ValueError("Only explicit experiment output directories can be packaged")
    if not path.is_dir() or not path.name.startswith(prefixes):
        raise ValueError(f"Directory is not an allowed real-data source: {path}")
    if "synthetic" in path.name.lower() or path.name.startswith("sku-fixed-product-"):
        raise ValueError("Synthetic data cannot enter a real-data package")
    return path


def files_in(directory, include_raw):
    for path in sorted(directory.rglob("*")):
        if any("synthetic" in part.lower() or part.startswith("sku-fixed-product-")
               for part in path.relative_to(directory).parts):
            raise ValueError(f"Synthetic path inside a real-data directory: {path}")
        if path.is_symlink():
            raise ValueError(f"Do not package symlinked sources: {path}")
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        if path.suffix not in TABLE_EXTENSIONS | RAW_EXTENSIONS:
            continue
        is_raw = (path.suffix in RAW_EXTENSIONS or
                  path.name.endswith(("-item.json", "-options.json")) or
                  path.name.startswith(("catalog-search-", "catalog-probe-")) or
                  any(part in {"raw", "catalog", "catalograw", "http-responses"}
                      for part in path.relative_to(directory).parts))
        if include_raw or not is_raw:
            yield path


def reject_synthetic_markers(value, path):
    """Catch explicit synthetic provenance; raw-source audits establish origin."""
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.lower()
            if (lowered in {"synthetic", "is_synthetic", "synthetic_data_included"}
                    and item not in (False, None, 0)):
                raise ValueError(f"Synthetic marker in real-data payload: {path}")
            if (lowered in {"synthetic_rows", "synthetic_data_rows"}
                    and item not in (None, 0)):
                raise ValueError(f"Synthetic rows in real-data payload: {path}")
            if (lowered in {"record_kind", "data_origin", "source_data_origin", "sample_type"}
                    and isinstance(item, str) and "synthetic" in item.lower()):
                raise ValueError(f"Synthetic provenance in real-data payload: {path}")
            reject_synthetic_markers(item, path)
    elif isinstance(value, list):
        for item in value:
            reject_synthetic_markers(item, path)


def validate_payload(path):
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    reject_synthetic_markers(json.loads(line), path)
    elif path.suffix == ".json":
        reject_synthetic_markers(json.loads(path.read_text(encoding="utf-8")), path)


def package(capture_dirs, derived_dirs, output, include_raw=False, reference_files=()):
    sources = [validate_directory(path, CAPTURE_PREFIXES) for path in capture_dirs]
    derived = [validate_directory(path, DERIVED_PREFIXES) for path in derived_dirs]
    payloads = {}
    for directory in sources + derived:
        for path in files_in(directory, include_raw):
            payloads[path.relative_to(ROOT).as_posix()] = path
    references = []
    for path in reference_files:
        path = path.resolve()
        if (not path.is_relative_to(ROOT / ".lab-output") or not path.is_file()
                or path.is_symlink() or not path.parent.name.startswith(CAPTURE_PREFIXES)):
            raise ValueError(f"Reference must be an explicit real-capture file: {path}")
        references.append(path.relative_to(ROOT).as_posix())
        payloads[references[-1]] = path
    for name in CODE:
        path = Path(__file__).parent / name
        if not path.is_file():
            raise ValueError(f"Required replay code is missing: {path}")
        payloads[path.relative_to(ROOT).as_posix()] = path
    if not payloads:
        raise ValueError("No real-data payloads selected")
    for path in payloads.values():
        validate_payload(path)
    sidecars = (output.with_suffix(output.suffix + ".manifest.json"),
                output.with_suffix(output.suffix + ".sha256"))
    if output.exists() or any(path.exists() for path in sidecars):
        raise FileExistsError(f"Package or sidecar already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "record_kind": "REAL_SKU_CAPTURE_PACKAGE",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic_data_included": False,
        "raw_bodies_included": include_raw,
        "capture_directories": [path.relative_to(ROOT).as_posix() for path in sources],
        "derived_directories": [path.relative_to(ROOT).as_posix() for path in derived],
        "reference_files": references,
        "files": [],
        "notes": ["Only explicitly allowlisted real-data directories are included",
                  "Derived tables reference recorded source snapshots; they are not independent gold",
                  "Source retrieval dates and normalization dates are distinct",
                  "Products and their SKU rows have different price and identity grains"],
    }
    # Exclusive creation protects previous captures and packages.
    with output.open("xb") as target, zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(payloads.items()):
            before = digest_file(path)
            size = path.stat().st_size
            archive.write(path, name)
            if digest_file(path) != before or path.stat().st_size != size:
                raise ValueError(f"Source changed during packaging: {name}")
            manifest["files"].append({"path": name, "bytes": size, "sha256": before})
        archive.writestr("REAL-SKU-CAPTURE-MANIFEST.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive CRC validation failed")
        for row in manifest["files"]:
            digest = hashlib.sha256()
            with archive.open(row["path"]) as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != row["sha256"]:
                raise ValueError(f"Archived SHA256 mismatch: {row['path']}")
    report = {"path": str(output), "bytes": output.stat().st_size,
              "sha256": digest_file(output), "payload_files": len(manifest["files"]),
              "raw_bodies_included": include_raw, "synthetic_data_included": False,
              "crc_and_payload_sha256_verified": True}
    with sidecars[0].open("x", encoding="utf-8") as target:
        target.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    with sidecars[1].open("x", encoding="utf-8") as target:
        target.write(report["sha256"] + "  " + output.name + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, action="append", required=True)
    parser.add_argument("--derived-dir", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--reference-file", type=Path, action="append", default=[],
                        help="Explicit historical real-capture reference; not an active sample")
    args = parser.parse_args()
    print(json.dumps(package(args.capture_dir, args.derived_dir, args.output,
                             args.include_raw, args.reference_file), ensure_ascii=False))


if __name__ == "__main__":
    main()
