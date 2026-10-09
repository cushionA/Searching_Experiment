"""Package code, fixed comparison inputs and measured outputs without weights."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def package(output):
    spec = importlib.util.spec_from_file_location("sku_source_packager", ROOT/"scripts/package_cloud.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files = set(module.distribution_files(ROOT))
    files.add(ROOT/".lab-output/sku-synthetic-controls-20261009/evaluation.json")
    for directory in ("sku-real-au-20261009", "sku-real-au-select10-20261009", "sku-real-rakuten-20261009"):
        files.update(ROOT/".lab-output"/directory/name for name in ("products.jsonl", "skus.jsonl"))
    files.update((HERE/"results/model-comparison-20261009").glob("*/*.json"))
    files.update((HERE/"results/model-comparison-20261009").glob("*/*.jsonl"))
    files.update(HERE/"results"/name for name in (
        "real-original-pair.jsonl", "real-sibling-pair.jsonl", "20261009-model-comparison.json",
        "20261009-model-comparison-validation.json",
        "20261009-model-input-audit.json", "20261009-snapshot-audit.json",
        "20261009-real-tables.zip", "20261009-real-tables.manifest.json",
        "20261009-gliner-extraction-b1.json", "20261009-gliner-extraction-b8.json",
        "20261009-gliner-smoke.json", "20261009-cpu-benchmark-controls.json"))
    # Require all ten independent processes and both extraction configurations.
    for key in ("minilm", "bekko", "granite", "ruri", "reranker"):
        for batch in (1,8):
            if not (HERE/f"results/model-comparison-20261009/{key}-b{batch}/summary.json").is_file():
                raise FileNotFoundError(f"Incomplete comparison: {key} batch {batch}")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"purpose": "Reproducible SKU CPU diagnostic source, actual tables, fixed controls and results",
                "excludes": ["model weights", "credentials", "venv", "raw HTML/API bodies", "459k-record stress dataset"],
                "raw_checkpoint_sha256": "1762ff038b7caa740e0511355a91afcc42a6de443ba19667c2122abcf5473ee3",
                "files": {}}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise ValueError(f"Unsafe or missing package file: {path}")
            name = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            info = zipfile.ZipInfo(name, date_time=(2026,10,9,0,0,0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            manifest["files"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        archive.writestr("SKU-COMPARISON-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Comparison archive CRC failed")
        for name, expected in manifest["files"].items():
            data = archive.read(name)
            if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise RuntimeError(f"Comparison archive SHA256 failed: {name}")
    result = {"path": str(output.relative_to(ROOT)), "payload_files": len(files),
              "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
              "crc_and_all_payload_sha256_verified": True}
    with output.with_suffix(".manifest.json").open("x", encoding="utf-8") as target:
        target.write(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.output.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
