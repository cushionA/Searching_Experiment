#!/usr/bin/env python3
"""Restore source-labeled OCR images from previously downloaded archives.

The archives are inspected in place; their paths are never extracted. Images
are copied byte-for-byte to a new output directory and recorded in a manifest
compatible with ``evaluate_public_text.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = False

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
SOURCES = (
    {
        "key": "project_sloth_captcha_images_test",
        "count": 2000,
        "dataset": "project-sloth/captcha-images",
        "url": "https://huggingface.co/datasets/project-sloth/captcha-images",
        "revision": "eeaf2b6ec9086645f270f7e2aaa9ea90730683de",
        "archive_sha256": "730bf94a1b857caa1de00bc7b5d9840c25bcd9767718f91ba9f8a3d5fd2a1912",
        "archive_name": "test.tar.gz",
    },
    {
        "key": "kaggle_fournierp_captcha_version_2",
        "count": 1070,
        "dataset": "fournierp/captcha-version-2-images",
        "url": "https://www.kaggle.com/datasets/fournierp/captcha-version-2-images",
        "revision": None,
        "archive_sha256": "6d26195a1647581990b05e8d40039b8f9ddfee422758c5cdfb165a1ec3fe1e14",
        "archive_name": None,
        "archive_image_entries": 2140,
    },
)


def sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(1024 * 1024):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def archive_receipt(path: Path) -> dict[str, int | str]:
    with path.open("rb") as stream:
        digest, size = sha256_stream(stream)
    return {"path": str(path.resolve()), "bytes": size, "sha256": digest}


def checked_member_name(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ValueError(f"Archive member uses a backslash path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"Unsafe archive member path: {name!r}")
    return path


def iter_tar_images(path: Path) -> Iterator[tuple[str, BinaryIO]]:
    archive = tarfile.open(path, mode="r:*")
    try:
        for member in archive:
            if not member.isfile():
                continue
            checked = checked_member_name(member.name)
            if checked.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"Could not read image member {member.name!r}")
            yield checked.as_posix(), stream
            stream.close()
    finally:
        archive.close()


def iter_zip_images(path: Path) -> Iterator[tuple[str, BinaryIO]]:
    archive = zipfile.ZipFile(path)
    try:
        for member in archive.infolist():
            unix_mode = member.external_attr >> 16
            if stat.S_ISLNK(unix_mode) or member.is_dir():
                continue
            if stat.S_IFMT(unix_mode) not in (0, stat.S_IFREG):
                raise ValueError(f"Unsupported ZIP member type: {member.filename!r}")
            checked = checked_member_name(member.filename)
            if checked.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            yield checked.as_posix(), archive.open(member, "r")
    finally:
        archive.close()


def label_for(source_key: str, member_name: str) -> str:
    basename = PurePosixPath(member_name).name
    if source_key == "project_sloth_captcha_images_test":
        return basename.split(".", 1)[0]
    return PurePosixPath(basename).stem


def write_source_images(
    archive_path: Path,
    destination: Path,
    source: dict,
    global_hashes: set[str],
) -> list[dict]:
    source_key = source["key"]
    iterator = iter_tar_images if tarfile.is_tarfile(archive_path) else iter_zip_images
    grouped: dict[str, dict] = {}
    raw_count = 0
    for member_name, stream in iterator(archive_path):
        raw_count += 1
        try:
            data = stream.read()
        finally:
            stream.close()
        digest = hashlib.sha256(data).hexdigest()
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        except Exception as exc:
            raise ValueError(f"Invalid image {member_name!r}: {exc}") from exc

        label = label_for(source_key, member_name)
        prior = grouped.get(digest)
        if prior is not None:
            if prior["label"] != label:
                raise ValueError(
                    f"Duplicate image SHA has conflicting labels in {source_key}: "
                    f"{prior['label']!r} vs {label!r} ({member_name!r})"
                )
            prior["archive_members"].append(member_name)
            continue
        grouped[digest] = {
            "data": data,
            "label": label,
            "archive_members": [member_name],
            "sha256": digest,
        }

    if source.get("archive_image_entries") is not None and raw_count != source["archive_image_entries"]:
        raise ValueError(
            f"{source_key}: expected {source['archive_image_entries']} image entries in archive, found {raw_count}"
        )
    records: list[dict] = []
    basenames: set[str] = set()
    for image_record in grouped.values():
        aliases = sorted(image_record["archive_members"])
        canonical_member = aliases[0]
        basename = PurePosixPath(canonical_member).name
        if basename in basenames:
            raise ValueError(f"Duplicate canonical image basename in {source_key}: {basename!r}")
        basenames.add(basename)
        digest = image_record["sha256"]
        if digest in global_hashes:
            raise ValueError(f"Duplicate image SHA-256 across OCR sources: {digest}")
        global_hashes.add(digest)

        relative_path = Path("images") / source_key / basename
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image_record["data"])
        records.append(
            {
                "id": f"{source_key}:{canonical_member}",
                "path": relative_path.as_posix(),
                "label": image_record["label"],
                "source": source_key,
                "sha256": digest,
                "archive_member": canonical_member,
                "archive_aliases": aliases,
            }
        )
    records.sort(key=lambda item: item["archive_member"])
    if len(records) != source["count"]:
        raise ValueError(f"{source_key}: expected {source['count']} images, found {len(records)}")
    return records


def prepare(sloth_archive: Path, kaggle_archive: Path, output_dir: Path) -> Path:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Refusing existing output directory: {output_dir}")
    sloth_archive = sloth_archive.resolve(strict=True)
    kaggle_archive = kaggle_archive.resolve(strict=True)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent))
    archives = [sloth_archive, kaggle_archive]
    try:
        receipts = [archive_receipt(path) for path in archives]
        for receipt, source in zip(receipts, SOURCES, strict=True):
            if receipt["sha256"] != source["archive_sha256"]:
                raise ValueError(f"{source['key']}: archive SHA-256 differs from the pinned evaluation archive")
        global_hashes: set[str] = set()
        samples: list[dict] = []
        for archive_path, source in zip(archives, SOURCES, strict=True):
            samples.extend(write_source_images(archive_path, stage, source, global_hashes))

        manifest = {
            "dataset": "Public source-labeled text CAPTCHA image evaluation",
            "provenance": {
                "restoration": "New manifest reconstructed from the supplied local archives; the earlier per-image manifest is unavailable.",
                "original_kaggle_split_assignment": "Unavailable; all 1070 Kaggle images are included without inventing split fields.",
                "sources": [
                    {
                        "key": source["key"],
                        "dataset": source["dataset"],
                        "url": source["url"],
                        "revision": source["revision"],
                        "archive": {**receipt, "archive_name": source["archive_name"] or Path(receipt["path"]).name},
                        "sample_count": source["count"],
                    }
                    for source, receipt in zip(SOURCES, receipts, strict=True)
                ],
            },
            "license": {
                "project_sloth_captcha_images_test": "WTFPL v2 (reported in the previous evaluation; license file not included in the supplied test archive).",
                "kaggle_fournierp_captcha_version_2": "Other (specified in dataset description); unavailable for verification from the supplied archive.",
            },
            "samples": samples,
        }
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if len(samples) != 3070 or len({sample["id"] for sample in samples}) != 3070:
            raise ValueError("Expected 3070 unique sample IDs")
        os.replace(stage, output_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return output_dir / "manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sloth-archive", type=Path, required=True)
    parser.add_argument("--kaggle-archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.sloth_archive, args.kaggle_archive, args.output_dir))


if __name__ == "__main__":
    main()
