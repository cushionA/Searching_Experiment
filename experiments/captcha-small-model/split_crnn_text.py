#!/usr/bin/env python3
"""Freeze source-stratified, content-grouped OCR train/validation/test folds.

These corpora have already been used for model selection. Their held-out folds
measure this pilot's adaptation only, not generalization on fresh external data.
No image, label value, or model prediction is used to rank assignment groups.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any


ALGORITHM = "sha256-group-source-stratified-ranked-v1"
FOLDS = ("train", "validation", "test")
RATIOS = {"train": 0.7, "validation": 0.1, "test": 0.2}
LIMITATION = (
    "All source images have already been used in earlier model-selection benchmarks. "
    "These frozen internal folds prevent gradient/checkpoint-selection leakage within "
    "this pilot, but are not a fresh independently collected final test set. "
    "The pretrained model's training-image overlap with these public corpora is unknown."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_samples(samples: list[dict[str, Any]]) -> None:
    if not samples:
        raise ValueError("empty manifest")
    ids: set[str] = set()
    labels: dict[str, str] = {}
    for sample in samples:
        for key in ("id", "source", "sha256", "label"):
            if not isinstance(sample.get(key), str) or not sample[key]:
                raise ValueError(f"missing or invalid sample {key}")
        if sample["id"] in ids:
            raise ValueError("duplicate sample id")
        ids.add(sample["id"])
        digest = sample["sha256"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid lowercase SHA-256")
        if digest in labels and labels[digest] != sample["label"]:
            raise ValueError("identical image SHA-256 has conflicting labels")
        labels[digest] = sample["label"]


def quotas(count: int) -> dict[str, int]:
    """Largest-remainder rounding; ties use the fixed train/validation/test order."""
    sizes = {fold: math.floor(count * RATIOS[fold]) for fold in FOLDS}
    remaining = count - sum(sizes.values())
    ranked = sorted(FOLDS, key=lambda fold: (-(count * RATIOS[fold] - sizes[fold]), FOLDS.index(fold)))
    for fold in ranked[:remaining]:
        sizes[fold] += 1
    return sizes


def make_split(samples: list[dict[str, Any]], manifest_sha256: str,
               seed: int = 20261006) -> dict[str, Any]:
    check_samples(samples)
    groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for sample in samples:
        groups[sample["sha256"]].append(sample)
    strata: dict[tuple[str, ...], list[str]] = collections.defaultdict(list)
    for digest, group in groups.items():
        # Identical images spanning sources remain one group in a composite stratum.
        strata[tuple(sorted({sample["source"] for sample in group}))].append(digest)
    assigned: dict[str, str] = {}
    stratum_counts = []
    for sources, digests in sorted(strata.items()):
        def rank(digest: str) -> str:
            value = json.dumps([ALGORITHM, seed, list(sources), digest], separators=(",", ":"))
            return hashlib.sha256(value.encode("utf-8")).hexdigest()
        ranked = sorted(digests, key=lambda digest: (rank(digest), digest))
        sizes = quotas(len(ranked))
        if any(sizes[fold] == 0 for fold in FOLDS):
            raise ValueError(f"source stratum too small for all three folds: {sources}")
        start = 0
        for fold in FOLDS:
            for digest in ranked[start:start + sizes[fold]]:
                assigned[digest] = fold
            start += sizes[fold]
        stratum_counts.append({"sources": list(sources), "groups": len(ranked), "by_fold": sizes})
    assignments = [{"id": sample["id"], "sha256": sample["sha256"],
                    "source": sample["source"], "fold": assigned[sample["sha256"]]}
                   for sample in sorted(samples, key=lambda item: item["id"])]
    by_source: dict[str, dict[str, int]] = {}
    for source in sorted({sample["source"] for sample in samples}):
        by_source[source] = {fold: sum(row["source"] == source and row["fold"] == fold
                                     for row in assignments) for fold in FOLDS}
    split = {"schema_version": 1, "algorithm": ALGORITHM, "seed": seed,
             "ratios": RATIOS, "rounding": "largest remainder; ties train, validation, test",
             "group_key": "image byte SHA-256 (all aliases and cross-source duplicates stay together)",
             "assignment_ranking_uses_labels": False, "manifest_sha256": manifest_sha256,
             "sample_count": len(samples), "unique_image_groups": len(groups),
             "by_source": by_source, "strata": stratum_counts,
             "limitation": LIMITATION, "assignments": assignments}
    return split


def validate_split(samples: list[dict[str, Any]], split: dict[str, Any],
                   manifest_sha256: str) -> dict[str, list[dict[str, Any]]]:
    """Reject changed manifests, omitted/extra IDs, conflicting identities or fold overlap."""
    check_samples(samples)
    if split.get("manifest_sha256") != manifest_sha256:
        raise ValueError("split manifest SHA-256 mismatch")
    if split.get("algorithm") != ALGORITHM or split.get("ratios") != RATIOS:
        raise ValueError("unsupported split protocol")
    sample_by_id = {sample["id"]: sample for sample in samples}
    seen: set[str] = set()
    digest_fold: dict[str, str] = {}
    partitions: dict[str, list[dict[str, Any]]] = {fold: [] for fold in FOLDS}
    for row in split.get("assignments", []):
        sample_id, fold = row.get("id"), row.get("fold")
        if sample_id in seen or sample_id not in sample_by_id or fold not in FOLDS:
            raise ValueError("duplicate, extra, or invalid split assignment")
        sample = sample_by_id[sample_id]
        if row.get("sha256") != sample["sha256"] or row.get("source") != sample["source"]:
            raise ValueError("split image identity mismatch")
        digest = sample["sha256"]
        if digest in digest_fold and digest_fold[digest] != fold:
            raise ValueError("image SHA-256 crosses train/validation/test folds")
        digest_fold[digest] = fold
        seen.add(sample_id)
        partitions[fold].append(sample)
    if seen != set(sample_by_id) or any(not partitions[fold] for fold in FOLDS):
        raise ValueError("split must assign every image exactly once and populate all folds")
    actual_by_source = {source: {fold: sum(sample["source"] == source for sample in partitions[fold])
                                for fold in FOLDS}
                        for source in sorted({sample["source"] for sample in samples})}
    if split.get("by_source") != actual_by_source:
        raise ValueError("split counts do not match assignments")
    if type(split.get("seed")) is not int:
        raise ValueError("split seed must be an integer")
    # Recompute the original label-independent ranked assignment, rather than
    # merely accepting any partition whose edited counts happen to add up.
    expected = make_split(samples, manifest_sha256, split["seed"])
    for key in ("assignments", "strata", "sample_count", "unique_image_groups"):
        if split.get(key) != expected[key]:
            raise ValueError("split differs from the frozen seed-ranked protocol")
    return {fold: sorted(rows, key=lambda sample: sample["id"])
            for fold, rows in partitions.items()}


def freeze(manifest_path: Path, output: Path, seed: int) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite frozen split: {output}")
    # The shared loader verifies every local image byte hash before freezing.
    spec = importlib.util.spec_from_file_location("_split_public_reference", Path(__file__).with_name("evaluate_public_text.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, samples = module.load_and_check_manifest(manifest_path.resolve())
    manifest_sha256 = sha256_file(manifest_path)
    split = make_split(samples, manifest_sha256, seed)
    validate_split(samples, split, manifest_sha256)
    output.parent.mkdir(parents=True, exist_ok=True)
    module.atomic_json(output, split)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261006)
    args = parser.parse_args()
    freeze(args.manifest, args.output, args.seed)
    print(json.dumps({"split": str(args.output), "sha256": sha256_file(args.output)}))


if __name__ == "__main__":
    main()
