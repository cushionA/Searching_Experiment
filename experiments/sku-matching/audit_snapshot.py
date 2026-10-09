#!/usr/bin/env python3
"""Read-only audit and compact handoff bundle for the 2026-10-09 SKU snapshot."""
from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments" / "sku-matching"
RESULTS = EXP / "results"
SOURCE_ZIP = RESULTS / "20261009-data-checkpoint.zip"
SOURCE_MANIFEST = RESULTS / "20261009-data-checkpoint.manifest.json"
AUDIT_PATH = RESULTS / "20261009-snapshot-audit.json"
BUNDLE_PATH = RESULTS / "20261009-real-tables.zip"
BUNDLE_MANIFEST_PATH = RESULTS / "20261009-real-tables.manifest.json"
GROUPS = {
    "au_weimall": ".lab-output/sku-real-au-20261009",
    "au_select10": ".lab-output/sku-real-au-select10-20261009",
    "rakuten_weimall": ".lab-output/sku-real-rakuten-20261009",
}
PAIR_FILES = ["real-original-pair.jsonl", "real-sibling-pair.jsonl"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_payload_entry(name: str, data: bytes, declared: dict) -> dict:
    """Return observed metadata or fail if a payload disagrees with its manifest."""
    observed = {"path": name, "bytes": len(data), "sha256": sha256(data)}
    if observed["bytes"] != declared.get("bytes") or observed["sha256"] != declared.get("sha256"):
        raise ValueError(f"manifest mismatch: {name}")
    observed["matches_embedded_manifest"] = True
    return observed


def read_jsonl(data: bytes, label: str) -> list[dict]:
    rows = []
    for line_no, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{label}:{line_no}: expected object")
        rows.append(value)
    return rows


def unique_count(rows: list[dict], key) -> int:
    return len({json.dumps(key(r), ensure_ascii=False, sort_keys=True) for r in rows})


def table_audit(products: list[dict], skus: list[dict], group: str) -> dict:
    if group.startswith("au_"):
        product_key = lambda r: str(r["item_id"])
        row_key = lambda r: (str(r["item_id"]), r["row_index"], r["column_index"])
        rowkey_fields = ["item_id", "row_index", "column_index"]
    else:
        product_key = lambda r: str(r["source_url"])
        row_key = lambda r: (str(r["source_url"]), str(r["sku_id"]))
        rowkey_fields = ["source_url", "sku_id"]

    product_ids = [product_key(p) for p in products]
    if len(set(product_ids)) != len(products):
        raise ValueError(f"duplicate product identity in {group}")
    sku_counts = Counter(product_key(r) for r in skus)
    product_set = set(product_ids)
    sku_product_set = set(sku_counts)
    if not sku_product_set <= product_set:
        raise ValueError(f"SKU row references missing product in {group}")
    duplicated = len(skus) - unique_count(skus, row_key)
    if duplicated:
        raise ValueError(f"duplicate row-grain key in {group}: {duplicated}")

    source_hashes = [r.get("sha256") for r in skus if r.get("sha256") is not None]
    row: dict = {
        "products": len(products),
        "sku_rows": len(skus),
        "product_ids": product_ids,
        "sku_row_grain": rowkey_fields,
        "products_with_zero_sku_rows": sorted(product_set - sku_product_set),
        "products_with_sku_rows": len(sku_product_set),
        "sku_rows_by_product": dict(sorted(sku_counts.items())),
        "sku_id_unique_count": unique_count(skus, lambda r: r.get("sku_id")),
        "sku_id_repeats_within_group": len(skus) - unique_count(skus, lambda r: r.get("sku_id")),
        "sku_rows_with_source_sha256": len(source_hashes),
        "source_sha256_unique_count": len(set(source_hashes)),
        "source_sha256_reused_sku_rows": len(source_hashes) - len(set(source_hashes)),
    }
    if group.startswith("au_"):
        axes = defaultdict(set)
        row_counts = Counter()
        col_counts = Counter()
        for p in products:
            pid = str(p["item_id"])
            names = p["sku"].get("option_name", {})
            axes["row"].add(str(names.get("row")))
            axes["column"].add(str(names.get("column")))
            row_counts[pid] = int(p["sku"].get("row_count", 0))
            col_counts[pid] = int(p["sku"].get("column_count", 0))
        row["sku_axes_by_role"] = {k: sorted(v) for k, v in axes.items()}
        row["row_column_shape_by_product"] = {
            pid: {"rows": row_counts[pid], "columns": col_counts[pid], "cells": row_counts[pid] * col_counts[pid], "observed_sku_rows": sku_counts.get(pid, 0)}
            for pid in product_ids
        }
    else:
        row["hidden_sku_rows"] = sum(bool(r.get("hidden_sku")) for r in skus)
        row["hidden_sku_values"] = dict(sorted(Counter(str(r.get("hidden_sku")) for r in skus).items()))
        row["visible_availability_values"] = dict(sorted(Counter(str(r.get("visible_availability")) for r in skus).items()))
        row["availability_from_embedded_quantity_values"] = dict(sorted(Counter(str(r.get("availability_from_embedded_quantity")) for r in skus).items()))
    return row


def main() -> None:
    archive_bytes = SOURCE_ZIP.read_bytes()
    source_hash = sha256(archive_bytes)
    expected = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if len(archive_bytes) != expected["archive_bytes"] or source_hash != expected["sha256"]:
        raise ValueError("source checkpoint does not match adjacent manifest")

    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"corrupt ZIP member: {bad_member}")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive member names")
        embedded = json.loads(archive.read("SKU-DATA-MANIFEST.json"))
        expected_entries = embedded["files"]
        payload_names = set(names) - {"SKU-DATA-MANIFEST.json"}
        if payload_names != set(expected_entries):
            raise ValueError("checkpoint payload paths disagree with embedded manifest")
        entry_audit = []
        for name in names:
            data = archive.read(name)
            if name != "SKU-DATA-MANIFEST.json":
                rec = verify_payload_entry(name, data, expected_entries[name])
            else:
                rec = {"path": name, "bytes": len(data), "sha256": sha256(data)}
            entry_audit.append(rec)

        groups = {}
        selected = []
        for name, prefix in GROUPS.items():
            pdata = archive.read(f"{prefix}/products.jsonl")
            sdata = archive.read(f"{prefix}/skus.jsonl")
            products = read_jsonl(pdata, f"{name}/products.jsonl")
            skus = read_jsonl(sdata, f"{name}/skus.jsonl")
            info = table_audit(products, skus, name)
            info["files"] = {
                f"{name}/products.jsonl": {"bytes": len(pdata), "sha256": sha256(pdata)},
                f"{name}/skus.jsonl": {"bytes": len(sdata), "sha256": sha256(sdata)},
            }
            groups[name] = info
            selected.extend([
                (f"data/{name}/products.jsonl", pdata),
                (f"data/{name}/skus.jsonl", sdata),
            ])

    products_total = sum(g["products"] for g in groups.values())
    sku_total = sum(g["sku_rows"] for g in groups.values())
    six_bytes = sum(v["bytes"] for g in groups.values() for v in g["files"].values())
    actual_expected = {"products": 32, "sku_rows": 1331, "table_file_bytes": 1_012_479}
    actual = {"products": products_total, "sku_rows": sku_total, "table_file_bytes": six_bytes}
    if actual != actual_expected:
        raise ValueError(f"unexpected observed-table totals: {actual}")

    pair_meta = {}
    for filename in PAIR_FILES:
        data = (RESULTS / filename).read_bytes()
        rows = read_jsonl(data, filename)
        pair_meta[filename] = {
            "bytes": len(data),
            "sha256": sha256(data),
            "jsonl_records": len(rows),
            "pair_id": rows[0].get("pair_id") if len(rows) == 1 else None,
            "au_sku_rows": len(rows[0].get("au", [])) if len(rows) == 1 else None,
            "rakuten_sku_rows": len(rows[0].get("rakuten", [])) if len(rows) == 1 else None,
        }
        selected.append((f"pair-inputs/{filename}", data))

    provenance = {
        "purpose": "Transferable observed SKU tables and existing candidate-pair inputs for model selection review.",
        "snapshot_date": "2026-10-09",
        "source_checkpoint": {
            "path": "experiments/sku-matching/results/20261009-data-checkpoint.zip",
            "bytes": len(archive_bytes),
            "sha256": source_hash,
            "archive_entries": len(entry_audit),
            "all_payload_entries_match_embedded_manifest": True,
        },
        "tables": groups,
        "totals": actual,
        "candidate_pair_inputs": pair_meta,
        "data_interpretation": [
            "SKU row grain is one observed option cell (au: item_id + row_index + column_index; Rakuten: source_url + sku_id).",
            "au sku_id repeats across item contexts and is not a globally unique row key; row/column axes vary by product.",
            "Rakuten repeated raw-page SHA256 values arise because many SKU rows come from one captured page; this alone does not imply duplicate SKU rows.",
            "Rakuten hidden_sku is source evidence; visible_availability and availability_from_embedded_quantity are separate fields and must not be collapsed into one asserted stock fact.",
            "The pair-input JSONL files are existing experiment inputs, not independently validated identity gold labels.",
            "The archive also contains large synthetic controls; this compact bundle intentionally omits them and all raw response bodies.",
        ],
    }
    provenance_bytes = (json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    selected.append(("PROVENANCE.json", provenance_bytes))

    with zipfile.ZipFile(BUNDLE_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for arcname, data in selected:
            info = zipfile.ZipInfo(arcname, date_time=(2026, 10, 9, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(BUNDLE_PATH) as bundle:
        bundle.testzip()
        bundle_entries = []
        for name in bundle.namelist():
            data = bundle.read(name)
            bundle_entries.append({"path": name, "bytes": len(data), "sha256": sha256(data)})
        bundle_bytes = BUNDLE_PATH.stat().st_size
        bundle_hash = sha256(BUNDLE_PATH.read_bytes())

    bundle_manifest = {
        "bundle": BUNDLE_PATH.name,
        "bytes": bundle_bytes,
        "sha256": bundle_hash,
        "source_checkpoint_sha256": source_hash,
        "entries": bundle_entries,
    }
    BUNDLE_MANIFEST_PATH.write_text(json.dumps(bundle_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    audit = {
        "audit_date": "2026-10-09",
        "source_archive": {
            "path": "experiments/sku-matching/results/20261009-data-checkpoint.zip",
            "bytes": len(archive_bytes),
            "sha256": source_hash,
            "matches_adjacent_manifest": True,
            "zip_integrity": "passed",
            "entry_count": len(entry_audit),
            "entries": entry_audit,
        },
        "groups": groups,
        "totals": actual,
        "arithmetic_check": {
            "au_weimall_skus": groups["au_weimall"]["sku_rows"],
            "au_select10_skus": groups["au_select10"]["sku_rows"],
            "au_total": groups["au_weimall"]["sku_rows"] + groups["au_select10"]["sku_rows"],
            "rakuten_total": groups["rakuten_weimall"]["sku_rows"],
            "observed_total": sku_total,
            "note": "440 + 891 = 1,331; 1,361 is not supported by these six observed JSONL tables.",
        },
        "candidate_pair_inputs": pair_meta,
        "handoff_bundle": {
            "path": "experiments/sku-matching/results/20261009-real-tables.zip",
            "bytes": bundle_bytes,
            "sha256": bundle_hash,
            "manifest_path": "experiments/sku-matching/results/20261009-real-tables.manifest.json",
            "entry_count": len(bundle_entries),
            "entries": bundle_entries,
        },
        "checks": {
            "all_checkpoint_payload_hashes_match_embedded_manifest": True,
            "duplicate_product_ids": 0,
            "duplicate_sku_grain_keys": 0,
            "observed_table_totals_match_expected": True,
            "raw_page_sha_reuse_means_duplicate_sku": False,
        },
    }
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"audit": str(AUDIT_PATH), "bundle": str(BUNDLE_PATH), "source_sha256": source_hash, "totals": actual, "bundle_bytes": bundle_bytes, "bundle_sha256": bundle_hash}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
