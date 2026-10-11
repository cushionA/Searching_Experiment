#!/usr/bin/env python3
"""Package explicitly selected integration runs with byte-verified provenance.

No model weights, environments or credential files are selected automatically.
The legacy raw checkpoint is a separately pinned dependency, not a new capture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
MEMBER = "SKU_GATE_CHECKPOINT.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def gather(root, directories, files):
    """Only explicitly requested directories are traversed; reject linked files."""
    payload = {}
    for source in [*(p for directory in directories for p in sorted(directory.rglob("*")) if p.is_file()), *files]:
        if source.is_symlink() or not source.is_file():
            raise ValueError("missing or linked payload file: " + str(source))
        relative = source.absolute().relative_to(root.absolute()).as_posix()
        parts = PurePosixPath(relative).parts
        if ".deps" in parts or ".." in parts or not parts or relative == MEMBER:
            raise ValueError("unpermitted payload path")
        if relative in payload:
            if payload[relative] != source.read_bytes():
                raise ValueError("duplicate payload differs")
            continue
        payload[relative] = source.read_bytes()
    return payload


def verify_archive(path, expected_sha=None):
    if expected_sha is not None and sha(path.read_bytes()) != expected_sha:
        raise ValueError("archive SHA differs")
    with ZipFile(path) as zipped:
        names = zipped.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive member")
        checkpoint = json.loads(zipped.read(MEMBER))
        entries = checkpoint["entries"]
        if set(names) != set(entries) | {MEMBER}:
            raise ValueError("payload members differ")
        for name, spec in entries.items():
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts or "\\" in name:
                raise ValueError("unsafe member")
            data = zipped.read(name)  # also checks CRC
            if len(data) != spec["size_bytes"] or sha(data) != spec["sha256"]:
                raise ValueError("payload size/SHA differs")
        if checkpoint["payload_file_count"] != len(entries):
            raise ValueError("payload count differs")
        if checkpoint["uncompressed_payload_bytes"] != sum(s["size_bytes"] for s in entries.values()):
            raise ValueError("payload bytes differ")
    return checkpoint


def package(output_prefix, directories, files, dependencies=(), root=ROOT):
    archive = output_prefix.with_suffix(".zip")
    manifest = output_prefix.with_suffix(".manifest.json")
    checksum = Path(str(archive) + ".sha256")
    if any(p.exists() for p in (archive, manifest, checksum)):
        raise FileExistsError("refusing to overwrite a package")
    if not directories or any(not d.is_dir() or d.is_symlink() for d in directories):
        raise ValueError("explicit regular run directories required")
    payload = gather(root, directories, files)
    if not payload:
        raise ValueError("empty payload")
    pinned = []
    for path in dependencies:
        pinned.append({"path": path.absolute().relative_to(root.absolute()).as_posix(),
                       "size_bytes": path.stat().st_size, "sha256": sha(path.read_bytes()), "included": False})
    checkpoint = {"schema_version": 1, "purpose": "PR26 v5 bidirectional CPU description integration experiment",
                  "production_adoption_allowed": False, "machine_reference_is_human_gold": False,
                  "scope_applicability_independently_proved": False, "weights_venvs_credentials_included": False,
                  "synthetic_sku_samples_included": False, "contract_tests_contain_marked_synthetic_fixtures": True,
                  "raw_capture_dependencies": pinned,
                  "entries": {name: {"size_bytes": len(data), "sha256": sha(data)} for name, data in sorted(payload.items())},
                  "payload_file_count": len(payload), "uncompressed_payload_bytes": sum(map(len, payload.values()))}
    body = (json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    archive.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive, "x", compression=ZIP_DEFLATED, compresslevel=6) as zipped:
        for name, data in sorted(payload.items()):
            zipped.writestr(name, data)
        zipped.writestr(MEMBER, body)
    archive_sha = sha(archive.read_bytes())
    verify_archive(archive, archive_sha)
    result = {"archive": archive.name, "size_bytes": archive.stat().st_size, "sha256": archive_sha,
              "embedded_manifest": MEMBER, "embedded_manifest_sha256": sha(body),
              "payload_file_count": len(payload), "uncompressed_payload_bytes": checkpoint["uncompressed_payload_bytes"],
              "crc_and_all_payload_sha256_verified": True, "checkpoint": {k: v for k, v in checkpoint.items() if k != "entries"}}
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum.write_text(archive_sha + "  " + archive.name + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--file", type=Path, action="append", default=[])
    parser.add_argument("--dependency", type=Path, action="append", default=[])
    args = parser.parse_args()
    result = package(args.output_prefix, args.run_dir, args.file, args.dependency)
    print(json.dumps({k: result[k] for k in ("archive", "size_bytes", "sha256", "payload_file_count")}))
