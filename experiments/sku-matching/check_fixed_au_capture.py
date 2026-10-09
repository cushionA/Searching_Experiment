"""Validate the supplied curtain's complete, observed fixed-au SKU matrices.

This checks recorded attributes and export safeguards. It does not evaluate a
model or infer product composition for arbitrary products.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from match_skus import canonical_key, export_matches, match_pair, normalize_sku
from prepare_real_pair import AU_COMPOSITIONS, prepare


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(au_dir, rakuten_dir, output_dir):
    sources = [directory / filename for directory in (au_dir, rakuten_dir)
               for filename in ("products.jsonl", "skus.jsonl")]
    sources.extend(au_dir / f"{item_id}-{kind}.json"
                   for item_id in AU_COMPOSITIONS for kind in ("item", "options"))
    before = {str(path): sha256(path) for path in sources}
    pairs, checks = [], []
    for item_id, lace in AU_COMPOSITIONS.items():
        pair = prepare(au_dir, rakuten_dir, au_item_id=item_id)
        au = [normalize_sku(row) for row in pair["au"]]
        rakuten = [normalize_sku(row) for row in pair["rakuten"]]
        if {row["product_id"] for row in au} != {item_id}:
            raise ValueError("Expected one fixed au product per pair")
        if len(au) != 153 or len(rakuten) != 306:
            raise ValueError("The captured example no longer has its expected 153/306 matrix")
        if any(row["missing"] or row["issues"] for row in au + rakuten):
            raise ValueError("Source attributes are incomplete or inconsistent")
        if {row["canonical"]["lace"] for row in au} != {lace}:
            raise ValueError("au product composition was not inherited consistently")
        au_keys = {canonical_key(row) for row in au}
        same = [row for row in rakuten if row["canonical"]["lace"] == lace]
        opposite = [row for row in rakuten if row["canonical"]["lace"] != lace]
        if len(au_keys) != 153 or {canonical_key(row) for row in same} != au_keys:
            raise ValueError("The same-composition SKU grids do not align")
        if len(same) != 153 or len(opposite) != 153:
            raise ValueError("Expected both full Rakuten composition grids")
        if any(canonical_key(row) in au_keys for row in opposite):
            raise ValueError("Opposite-composition SKU was treated as identical")
        accepted, audit = match_pair(pair)
        opposite_ids = {row["sku_id"] for row in opposite}
        if any(row["rakuten_sku_id"] in opposite_ids for row in accepted):
            raise ValueError("Opposite-composition SKU reached final output")
        if any(row["au_product_id"] != item_id for row in accepted):
            raise ValueError("Output switched to a different au product")
        checks.append({
            "au_product_id": item_id, "au_lace": lace,
            "au_sku_rows": len(au), "rakuten_sku_rows": len(rakuten),
            "same_composition_attribute_matches_before_stock_filter": len(same),
            "opposite_composition_rows": len(opposite),
            "opposite_composition_accepted_rows": 0,
            "final_available_single_price_rows": len(accepted),
            "rakuten_decisions": dict(Counter(row["status"] for row in audit
                                             if row["site"] == "rakuten")),
        })
        pairs.append(pair)
    if any(sha256(path) != before[str(path)] for path in sources):
        raise ValueError("Source tables changed while validation was running")
    output_dir.mkdir(parents=True, exist_ok=False)
    input_path = output_dir / "fixed-product-pairs.jsonl"
    input_path.write_text("".join(json.dumps(pair, ensure_ascii=False) + "\n"
                                  for pair in pairs), encoding="utf-8")
    exported = export_matches(input_path, output_dir / "matched-output")
    report = {
        "record_kind": "OBSERVED_FIXED_PRODUCT_CAPTURE_CHECK",
        "synthetic_rows": 0, "model_used": False,
        "composition_source": "Explicit mapping for these two user-supplied products; not general model extraction",
        "checks": checks, "export": exported,
        "source_sha256": before, "source_mutated": False,
        "limits": ["One curtain series; not independent general SKU-matching gold",
                   "au prices are API product prices, not measured per-selection prices",
                   "Rakuten stock is derived from embedded quantity, not displayed stock"],
    }
    (output_dir / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--au-dir", type=Path, required=True)
    parser.add_argument("--rakuten-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = check(args.au_dir, args.rakuten_dir, args.output_dir)
    print(json.dumps({"checks": report["checks"], "output": str(args.output_dir)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
