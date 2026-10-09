"""Prepare the user-supplied curtain example from saved, observed source tables.

This checks selected attributes within the supplied product family. It does not
prove product identity, fabric identity, or customer approval of a SKU policy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from match_skus import SIZE, normalize


def rows(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def prepare(au_dir, rakuten_dir, include_sibling=False):
    products = {r["item_id"]: r for r in rows(au_dir / "products.jsonl")}
    rakuten_products = {r.get("manage_number"): r for r in rows(rakuten_dir / "products.jsonl")}
    title = rakuten_products["ct0"]["title"]
    selected_ids = {"704502086": True}
    if include_sibling:
        selected_ids["704500131"] = False
    au = []
    for r in rows(au_dir / "skus.jsonl"):
        item_id = r["item_id"]
        if item_id not in selected_ids:
            continue
        p = products[item_id]
        raw_info = json.loads((au_dir / f"{item_id}-item.json").read_text(encoding="utf-8"))["itemInfo"]
        sold_out = r["stock"].get("isSoldOut")
        not_for_sale = raw_info.get("isItemNotForSale")
        available = (not sold_out) if type(sold_out) is bool and not_for_sale is False else None
        if not_for_sale is True:
            available = False
        if (r["row_option_name"], r["column_option_name"]) != ("カラー", "サイズ"):
            raise ValueError("Unexpected source axes; this adapter is for the supplied WEIMALL example")
        au.append({"product_id": item_id,
            "sku_id": f"{r['sku_id']}:row={r['row_index']}:col={r['column_index']}",
            "product_title": p["item_title"],
            "sku_label": f"{r['column_option_value']} / {r['row_option_value']}",
            "url": f"https://wowma.jp/item/{item_id}", "available": available,
            "price": p["current_price"], "price_basis": "observed_product_currentPrice_excludes_delivery_surcharge",
            "attributes": {"lace": selected_ids[item_id]},
            "provenance": {"record": "OBSERVED_API_SKU_CELL", "lace": "confirmed_product_contents_table",
                "price": "PRODUCT_LEVEL_ONLY_NOT_MEASURED_PER_SKU", "stock": r["stock"],
                "item_api": p["item_api"], "options_api": p["options_api"]}})

    rakuten = []
    for r in rows(rakuten_dir / "skus.jsonl"):
        if r["manage_number"] != "ct0":
            continue
        opts = r["option_values"]
        if set(opts) != {"サイズ", "カラー", "レースカーテン"} or opts["レースカーテン"] not in ("あり", "なし"):
            raise ValueError("Unexpected ct0 option schema")
        size = SIZE.fullmatch(normalize(opts["サイズ"]))
        if not size or float(size[1]) not in (100, 150):
            raise ValueError("Unexpected width; description-based composition rule is limited to 100/150cm")
        lace = opts["レースカーテン"] == "あり"
        drape_pieces = 2 if float(size[1]) == 100 else 1
        pieces = drape_pieces * (2 if lace else 1)
        available = {"in_stock": True, "out_of_stock": False}.get(r["availability_from_embedded_quantity"])
        if r.get("hidden_sku") is True:
            available = False
        rakuten.append({"product_id": "ct0", "sku_id": r["sku_id"], "product_title": title,
            "sku_label": " / ".join(opts[key] for key in ("サイズ", "カラー", "レースカーテン")),
            "url": r["source_url"], "price": r["price_jpy"],
            "price_basis": "observed_per_sku_taxIncludedPrice_excludes_delivery_coupon",
            "available": available, "attributes": {"lace": lace, "pieces": pieces},
            "provenance": {"record": "OBSERVED_EMBEDDED_JSON_SKU", "price": "OBSERVED_PER_SKU",
                "composition": "observed_ct0_description_width100_2drape_2lace_width150_1drape_1lace",
                "availability": "inferred_from_embedded_inventory_not_visible_stock_count",
                "sha256": r["sha256"], "retrieved_at_utc": r["retrieved_at_utc"]}})
    return {"pair_id": "observed-ct0-with-au-sibling" if include_sibling else "observed-ct0-au-original-only",
            "au": au, "rakuten": rakuten,
            "scope": "attribute_check_of_user_supplied_product_family; not_independent_product_identity_gold",
            "notes": ["au prices are observed product-level prices, not separately measured SKU prices",
                      "delivery fees, delivery-region paid options, coupons and membership conditions excluded",
                      "lace inheritance and piece-count rules use these exact products' descriptions only",
                      "sibling collection is an experimental comparison; customer SKU policy remains undecided"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--au-dir", type=Path, required=True)
    parser.add_argument("--rakuten-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-sibling", action="store_true")
    args = parser.parse_args()
    pair = prepare(args.au_dir, args.rakuten_dir, args.include_sibling)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        target.write(json.dumps(pair, ensure_ascii=False) + "\n")
    print(json.dumps({"output": str(args.output), "au_skus": len(pair["au"]), "rakuten_skus": len(pair["rakuten"])}))


if __name__ == "__main__":
    main()
