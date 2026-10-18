"""Audit source coverage of the frozen product-level SKU split.

Reads only TRAINdict/EVALtest products.jsonl and cases.jsonl plus raw files
named by those product records. Output is created with exclusive mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPLIT_ROOT = ROOT / ".lab-output/sku-dictionary-half-20261015-v1/split-v1"
VARIANT_MARKER = re.compile(r'\{"variantId":("(?:\\.|[^"\\])*"),\s*"selectorValues":\[')


def jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def source_path(value: str) -> Path:
    return ROOT / value.replace("\\", "/")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_rakuten(product: dict, cases: list[dict]) -> dict:
    rak = product["rakuten_product"]
    rel = rak["raw_file"].replace("\\", "/")
    path = source_path(rel)
    out = {
        "raw_file": rel,
        "expected_sha256": rak["sha256"],
        "raw_present": path.is_file(),
        "sha256_match": False,
        "native_unique_variant_rows": 0,
        "native_variant_ids": 0,
        "embedded_record_count": 0,
        "duplicate_embedded_record_excess": 0,
        "variant_id_selector_value_conflicts": 0,
        "case_ids_total": len(cases),
        "case_ids_verified_exact_variant_and_axes": [],
        "case_ids_axes_mismatch": [],
        "case_ids_variant_absent": [],
        "additional_native_variants": [],
    }
    if not path.is_file():
        out["case_ids_variant_absent"] = [c["case_id"] for c in cases]
        return out
    raw = path.read_bytes()
    out["actual_sha256"] = sha256(raw)
    out["sha256_match"] = out["actual_sha256"] == rak["sha256"]
    if not out["sha256_match"]:
        return out
    text = raw.decode(rak.get("encoding") or "EUC-JP")
    decoder = json.JSONDecoder()
    records: list[tuple[str, tuple[str, ...]]] = []
    for match in VARIANT_MARKER.finditer(text):
        try:
            variant_id = json.loads(match.group(1))
            obj, _ = decoder.raw_decode(text, match.start())
        except (json.JSONDecodeError, ValueError):
            continue
        values = obj.get("selectorValues", [])
        if isinstance(values, list):
            records.append((variant_id, tuple(values)))
    native = set(records)  # identical embedded page-data copies are one row
    by_id: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for variant_id, values in native:
        by_id[variant_id].add(values)
    selected_ids = {c["rakuten_selected"].get("variant_id") for c in cases}
    verified, mismatch, absent = [], [], []
    for case in cases:
        selected = case["rakuten_selected"]
        variant_id = selected.get("variant_id")
        expected_values = tuple(axis.get("value") for axis in selected.get("axes", []))
        if variant_id not in by_id:
            absent.append(case["case_id"])
        elif expected_values in by_id[variant_id]:
            verified.append(case["case_id"])
        else:
            mismatch.append(case["case_id"])
    out.update({
        "native_unique_variant_rows": len(native),
        "native_variant_ids": len(by_id),
        "embedded_record_count": len(records),
        "duplicate_embedded_record_excess": len(records) - len(native),
        "variant_id_selector_value_conflicts": sum(len(values) > 1 for values in by_id.values()),
        "case_ids_verified_exact_variant_and_axes": verified,
        "case_ids_axes_mismatch": mismatch,
        "case_ids_variant_absent": absent,
        "additional_native_variants": [
            {"variant_id": variant_id, "selector_values": list(values)}
            for variant_id, values in sorted(native, key=lambda row: (row[0], row[1]))
            if variant_id not in selected_ids
        ],
    })
    return out


def audit_au(product: dict) -> dict:
    au = product["au_product"]
    rel = au["raw_file"].replace("\\", "/")
    path = source_path(rel)
    rows = product["au_rows"]
    out = {
        "raw_file": rel,
        "expected_sha256": au["sha256"],
        "raw_present": path.is_file(),
        "sha256_match": False,
        "provided_au_rows": len(rows),
        "native_cell_count": 0,
        "stockList_structural_empty_cell_count": None,
        "missing_native_cells": None,
        "extra_provided_cells": None,
        "duplicate_provided_cell_keys": None,
        "row_key_or_sku_id_failures": None,
        "axis_name_value_failures": None,
        "all_native_cells_match_provided_rows": False,
    }
    if not path.is_file():
        return out
    raw = path.read_bytes()
    out["actual_sha256"] = sha256(raw)
    out["sha256_match"] = out["actual_sha256"] == au["sha256"]
    if not out["sha256_match"]:
        return out
    document = json.loads(raw)
    sku_info = document["itemInfo"]["skuInfo"]
    row_count, column_count = sku_info["rowCount"], sku_info["columnCount"]
    row_names, column_names = sku_info["rowNames"], sku_info["columnNames"]
    stock_list = sku_info["stockList"]
    sku_id = sku_info["skuId"]
    shape_valid = (
        len(row_names) == row_count == len(stock_list)
        and len(column_names) == column_count
        and all(isinstance(row, list) and len(row) == column_count for row in stock_list)
    )
    if not shape_valid:
        out.update({"row_count": row_count, "column_count": column_count, "stockList_shape_valid": False})
        return out
    native_cells = {
        (r, c)
        for r in range(row_count)
        for c in range(column_count)
        if isinstance(stock_list[r][c], dict)
    }
    provided = [(row["row_index"], row["column_index"]) for row in rows]
    provided_set = set(provided)
    missing, extra = native_cells - provided_set, provided_set - native_cells
    key_failures = 0
    axis_failures = 0
    for row in rows:
        r, c = row["row_index"], row["column_index"]
        expected_key = f"au:{au['product_id']}:{sku_id}:{r}:{c}"
        if (str(row.get("sku_id")) != str(sku_id) or row.get("row_key") != expected_key
                or not (0 <= r < row_count and 0 <= c < column_count)):
            key_failures += 1
            continue
        expected_axes = []
        if row_names[r]:
            expected_axes.append((sku_info["optionName"]["row"], row_names[r]))
        if column_names[c]:
            expected_axes.append((sku_info["optionName"]["column"], column_names[c]))
        actual_axes = [(axis.get("axis_name"), axis.get("value")) for axis in row.get("axes", [])]
        if actual_axes != expected_axes:
            axis_failures += 1
    empty_cells = row_count * column_count - len(native_cells)
    complete = not missing and not extra and len(provided) == len(provided_set) and not key_failures and not axis_failures
    out.update({
        "sku_id": str(sku_id),
        "row_count": row_count,
        "column_count": column_count,
        "stockList_shape_valid": True,
        "native_cell_count": len(native_cells),
        "stockList_structural_empty_cell_count": empty_cells,
        "missing_native_cells": len(missing),
        "extra_provided_cells": len(extra),
        "duplicate_provided_cell_keys": len(provided) - len(provided_set),
        "row_key_or_sku_id_failures": key_failures,
        "axis_name_value_failures": axis_failures,
        "all_native_cells_match_provided_rows": complete,
    })
    return out


def audit_split(split: str) -> dict:
    input_dir = SPLIT_ROOT / split / "inputs"
    products = jsonl(input_dir / "products.jsonl")
    cases = jsonl(input_dir / "cases.jsonl")
    cases_by_product: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        cases_by_product[case["dossier_id"]].append(case)
    per_product = []
    for product in products:
        dossier_id = product["dossier_id"]
        rk = audit_rakuten(product, cases_by_product[dossier_id])
        au = audit_au(product)
        per_product.append({
            "dossier_id": dossier_id,
            "rakuten": rk,
            "au": au,
            "both_site_raws_sha_pinned": rk["sha256_match"] and au["sha256_match"],
        })
    verified_cases = sum(len(p["rakuten"]["case_ids_verified_exact_variant_and_axes"]) for p in per_product)
    au_present = [p for p in per_product if p["au"]["sha256_match"]]
    rk_present = [p for p in per_product if p["rakuten"]["sha256_match"]]
    both = [p for p in per_product if p["both_site_raws_sha_pinned"]]
    return {
        "split": split,
        "products": len(products),
        "cases": len(cases),
        "rakuten_raws_present_sha_pinned": len(rk_present),
        "rakuten_raws_missing_or_sha_invalid": len(products) - len(rk_present),
        "rakuten_native_unique_variant_rows": sum(p["rakuten"]["native_unique_variant_rows"] for p in rk_present),
        "rakuten_native_variant_ids": sum(p["rakuten"]["native_variant_ids"] for p in rk_present),
        "rakuten_duplicate_embedded_record_excess": sum(p["rakuten"]["duplicate_embedded_record_excess"] for p in rk_present),
        "rakuten_additional_native_variant_ids": sum(len(p["rakuten"]["additional_native_variants"]) for p in rk_present),
        "case_ids_verified_exact_variant_and_axes": verified_cases,
        "case_ids_axes_mismatch": sum(len(p["rakuten"]["case_ids_axes_mismatch"]) for p in per_product),
        "case_ids_variant_absent_or_raw_missing": sum(len(p["rakuten"]["case_ids_variant_absent"]) for p in per_product),
        "au_raws_present_sha_pinned": len(au_present),
        "au_raws_missing_or_sha_invalid": len(products) - len(au_present),
        "au_native_skuinfo_matrix_products_complete": sum(p["au"]["all_native_cells_match_provided_rows"] for p in au_present),
        "au_native_skuinfo_matrix_cells": sum(p["au"]["native_cell_count"] for p in au_present),
        "au_native_skuinfo_matrix_max_cells_per_product": max((p["au"]["native_cell_count"] for p in au_present), default=0),
        "au_native_missing_cells": sum(p["au"]["missing_native_cells"] for p in au_present),
        "au_native_extra_cells": sum(p["au"]["extra_provided_cells"] for p in au_present),
        "au_native_structural_empty_cells": sum(p["au"]["stockList_structural_empty_cell_count"] for p in au_present),
        "both_site_raws_sha_pinned_products": len(both),
        "per_product": per_product,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new output JSON path; existing paths are refused")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "schema": "sku-product-source-coverage-v1",
        "source_scope": "named old split inputs and only raw files named in their product records",
        "rakuten_method": "extract native variantId/selectorValues JSON objects; deduplicate identical pairs; never expand selector options",
        "au_method": "compare every native skuInfo stockList row/column dictionary cell to supplied au_rows and validate original indices, keys, skuId, axis names and values",
        "website_completeness_claim": False,
        "splits": {split: audit_split(split) for split in ("TRAINdict", "EVALtest")},
    }
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    for split, stats in result["splits"].items():
        print(json.dumps({key: value for key, value in stats.items() if key != "per_product"}, ensure_ascii=False))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
