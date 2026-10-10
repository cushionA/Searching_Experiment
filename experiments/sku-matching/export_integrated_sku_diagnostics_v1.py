"""Export reviewable real SKU decisions with prices kept at their source grain."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from evaluate_integrated_sku_trials_v1 import verify_run
from run_integrated_sku_gate_v1 import ROOT, DEFAULT_INPUT, dump, read, sha
import sku_integrated_gate_v1 as gate

PRICE_TABLES = (".lab-output/sku-real-rakuten-20261010-normalized-v2/skus.jsonl",
                ".lab-output/sku-real-rakuten-20261010-expanded/skus.jsonl")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    verify_run(args.run)
    verify_run(args.source_run)
    baseline = {r["case_id"]: r for r in read(args.source_run / "predictions.jsonl")}
    freeze = json.loads((args.run / "freeze.json").read_text())
    expected = freeze.get("input_sha256", freeze.get("inputs_sha256"))
    if expected is None:
        # Hybrid freezes bind the exact inputs in their artifact map.
        artifacts = freeze["artifact_sha256"]
        expected = {n: artifacts[str(args.input / n)] for n in ("cases.jsonl", "products.jsonl", "manifest.json")}
    for name, digest in expected.items():
        if sha(args.input / name) != digest:
            raise ValueError("Input differs from prediction freeze")
    price_rows = {}
    for table in PRICE_TABLES:
        for row in read(ROOT / table):
            key = (row["source_sku_key"], row["raw_file"], row["sha256"])
            if key in price_rows and price_rows[key] != row:
                raise ValueError("Conflicting original price records")
            price_rows[key] = row
    meta = json.loads((args.input / "manifest.json").read_text())
    cohort = {c: name for name, bundle in meta["bundle_inputs"].items() for c in bundle["case_ids"]}
    contexts = {p["dossier_id"]: p for p in read(args.input / "products.jsonl")}
    cases = {c["case_id"]: c for c in read(args.input / "cases.jsonl")}
    store = gate.load_gate().src.RawStore(ROOT)
    rows = []
    for pred in read(args.run / "predictions.jsonl"):
        case, context = cases[pred["case_id"]], contexts[pred["dossier_id"]]
        selected, au = case["rakuten_selected"], context["au_product"]
        source_price = price_rows[(selected["source_sku_key"], selected["raw_file"], selected["sha256"])]
        if source_price["price_jpy"] is None:
            raise ValueError("Missing selected SKU price; no range fallback")
        if [a["value"] for a in selected["axes"]] != [a["value"] for a in source_price["option_values"]]:
            raise ValueError("Price record describes another SKU")
        store.raw(au["raw_file"], au["sha256"])
        store.raw(selected["raw_file"], selected["sha256"])
        au_price = store.json(au["raw_file"])["itemInfo"]["currentPrice"]
        au_row = next((r for r in context["au_rows"] if r["row_key"] == pred["au_row_key"]), None)
        variant = " / ".join(a["value"] for a in selected["axes"])
        rows.append({"cohort": cohort[pred["case_id"]], "case_id": pred["case_id"],
                     "decision": pred["decision"], "reason": pred["reason"],
                     "rakuten_url": selected["url"], "rakuten_variant_id": selected["variant_id"],
                     "rakuten_sku": variant, "rakuten_title": context["rakuten_product"]["title"] + " " + variant,
                     "rakuten_selected_sku_price_jpy": source_price["price_jpy"],
                     "au_url": "https://wowma.jp/item/" + au["product_id"], "au_row_key": pred["au_row_key"],
                     "au_sku": " / ".join(a["value"] for a in au_row["axes"]) if au_row else "",
                     "au_title": au["title"] + " " + variant if au_row else au["title"],
                     "au_fixed_page_price_jpy": au_price,
                     "rakuten_availability": source_price.get("availability_from_embedded_quantity"),
                     "notices": json.dumps(pred["notices"], ensure_ascii=False, separators=(",", ":"))})
    if len(rows) != len(cases):
        raise ValueError("Lost SKU cases")
    args.output.mkdir(parents=True)
    for name, subset in (("all-decisions.csv", rows), ("accepted-diagnostics.csv", [r for r in rows if r["decision"] == "accept"]),
                         ("newly-accepted-diagnostics.csv", [r for r in rows if r["decision"] == "accept" and baseline[r["case_id"]]["decision"] == "drop"])):
        with (args.output / name).open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(subset)
    dump(args.output / "manifest.json", {"purpose": "diagnostic source-price join, not an availability-filtered production CSV",
        "inventory_eligibility_applied": False, "synthetic": False, "cases": len(rows),
        "counts": dict(Counter(r["decision"] for r in rows)),
        "price_grain": {"rakuten": "selected SKU", "au": "fixed product page"},
        "source_price_table_sha256": {name: sha(ROOT / name) for name in PRICE_TABLES},
        "prediction_sha256": sha(args.run / "predictions.jsonl"), "raw_sha256": store._sha,
        "code_sha256": sha(Path(__file__)), "files": {p.name: sha(p) for p in args.output.iterdir()}})


if __name__ == "__main__":
    main()
