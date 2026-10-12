#!/usr/bin/env python3
"""Pre-label overlap audit for the frozen final 40-case subset."""
from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[2]
MEMBERSHIP_SHA256 = "212123b8851168a8895e8a035f8a1a032c173c496b76024b707030efca93778d"
DEFAULT_MEMBERSHIP = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/plan/subset-membership.jsonl"
DEFAULT_TEST_CASES = ROOT / ".lab-output/sku-final-test-subset-20261013-v1/inputs/source-cases.jsonl"
DEFAULT_TEST_PRODUCTS = ROOT / ".lab-output/sku-final-test-subset-20261013-v1/inputs/source-products.jsonl"
DEV_CASES = ROOT / ".lab-output/sku-mixed-gpu-prep-20261012-v5/gpu-cases.jsonl"
DEV_DOCUMENTS = ROOT / ".lab-output/sku-mixed-gpu-prep-20261012-v5/documents.jsonl"
DEV_UNITS = (
    ROOT / ".lab-output/sku-composition-evidence-20261013-v1/classification-units.jsonl",
    ROOT / ".lab-output/sku-scoped-request-composition-20261013-v1/classification-units.jsonl",
    ROOT / ".lab-output/sku-full-condition-adapter-20261013-v1/dev/composition-units.jsonl",
    ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev/composition-units.jsonl",
    ROOT / ".lab-output/sku-composition-nli-20261013-v1/source-classification-units.jsonl",
    ROOT / ".lab-output/sku-composition-nli-20261013-v1/evaluation/source-classification-units.jsonl",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rows(path: Path) -> list[dict]:
    values = []
    with path.open(encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_no}: expected object")
                values.append(row)
    return values


def norm_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.netloc or "").lower()
    path = unicodedata.normalize("NFKC", parsed.path).rstrip("/")
    if "rakuten" not in host and not host.endswith("rakuten.co.jp"):
        return ""
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def identifiers(row: dict) -> dict[str, set[str]]:
    result = {key: set() for key in ("case_id", "dossier_id", "au_product_id", "rakuten_url")}

    def walk(value, ancestry=()):
        if isinstance(value, dict):
            for key, item in value.items():
                key_low = key.lower()
                if key_low in {"case_id", "dossier_id", "au_product_id"} and isinstance(item, (str, int)):
                    result[key_low].add(str(item))
                if key_low == "product_id" and isinstance(item, (str, int)) and (
                    value.get("side") == "au" or any("au" in ancestor.lower() for ancestor in ancestry)
                ):
                    result["au_product_id"].add(str(item))
                if key_low in {"rakuten_url", "fixed_rakuten_url"} and isinstance(item, str):
                    normalized = norm_url(item)
                    if normalized:
                        result["rakuten_url"].add(normalized)
                if key_low == "url" and isinstance(item, str) and (
                    "rakuten" in item.lower() or any("rakuten" in ancestor.lower() for ancestor in ancestry)
                ):
                    normalized = norm_url(item)
                    if normalized:
                        result["rakuten_url"].add(normalized)
                walk(item, (*ancestry, key))
        elif isinstance(value, list):
            for item in value:
                walk(item, ancestry)

    walk(row)
    return result


def merge_ids(items: list[dict]) -> dict[str, set[str]]:
    out = {key: set() for key in ("case_id", "dossier_id", "au_product_id", "rakuten_url")}
    for item in items:
        found = identifiers(item)
        for key in out:
            out[key].update(found[key])
    return out


def overlap_report(
    membership_path: Path = DEFAULT_MEMBERSHIP,
    test_cases_path: Path = DEFAULT_TEST_CASES,
    test_products_path: Path = DEFAULT_TEST_PRODUCTS,
    dev_cases_path: Path = DEV_CASES,
    dev_documents_path: Path = DEV_DOCUMENTS,
    dev_unit_paths: tuple[Path, ...] = DEV_UNITS,
) -> dict:
    membership_bytes = membership_path.read_bytes()
    if sha(membership_bytes) != MEMBERSHIP_SHA256:
        raise ValueError("frozen 40-case membership SHA mismatch")
    membership = rows(membership_path)
    if len(membership) != 40 or len({row.get("case_id") for row in membership}) != 40:
        raise ValueError("frozen membership must contain 40 unique cases")
    split_counts = Counter(row.get("source_version", row.get("split", "unknown")) for row in membership)
    if set(split_counts) != {"v2", "v3"}:
        raise ValueError(f"expected separate frozen v2/v3 membership groups, got {dict(split_counts)}")

    test_entities = merge_ids(rows(test_cases_path) + rows(test_products_path))
    test_members = merge_ids(membership)
    for key in test_entities:
        test_entities[key].update(test_members[key])

    dev_groups = {"gpu_cases_83": merge_ids(rows(dev_cases_path)),
                  "gpu_source_documents": merge_ids(rows(dev_documents_path))}
    for path in dev_unit_paths:
        if path.is_file():
            dev_groups[f"classifier_units:{path.relative_to(ROOT).as_posix()}"] = merge_ids(rows(path))
    results = {}
    for group, values in dev_groups.items():
        overlaps = {key: sorted(test_entities[key] & values[key]) for key in test_entities}
        results[group] = {
            "overlap_counts": {key: len(value) for key, value in overlaps.items()},
            "au_product_disjoint": not overlaps["au_product_id"],
            "rurl_disjoint": not overlaps["rakuten_url"],
        }
    union = {key: set().union(*(group[key] for group in dev_groups.values())) for key in test_entities}
    total_overlap = {key: sorted(test_entities[key] & union[key]) for key in test_entities}
    return {
        "schema_version": "final-test-prelabel-overlap-audit-v1",
        "membership_sha256": MEMBERSHIP_SHA256,
        "membership_count": len(membership),
        "frozen_split_membership_counts": dict(sorted(split_counts.items())),
        "label_contents_opened": False,
        "model_outputs_opened": False,
        "dev_case_count": len(rows(dev_cases_path)),
        "overlap_by_dev_source": results,
        "dev_source_union_overlap_counts": {key: len(value) for key, value in total_overlap.items()},
        "dev_source_union_au_product_disjoint": not total_overlap["au_product_id"],
        "dev_source_union_rurl_disjoint": not total_overlap["rakuten_url"],
        "interpretation": "Case membership is split and frozen; report product and Rakuten URL overlap separately. No product-disjoint independent-test claim follows when overlaps are present.",
        "input_sha256": {path.relative_to(ROOT).as_posix(): sha(path.read_bytes()) for path in
                         (membership_path, test_cases_path, test_products_path, dev_cases_path, dev_documents_path,
                          *[p for p in dev_unit_paths if p.is_file()])},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = overlap_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"output": str(args.output), "membership_count": report["membership_count"],
                      "split_counts": report["frozen_split_membership_counts"],
                      "union_overlaps": report["dev_source_union_overlap_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
