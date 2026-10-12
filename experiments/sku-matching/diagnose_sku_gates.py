"""Row-level diagnostic table for one frozen SKU-gate system on the confirmed benchmark.

Runs after evaluation on a frozen run (it re-checks the freeze and the prediction manifest).
For every case it lists the Rakuten SKU (keys, SKU price and its source line), the fixed AU
product and the adopted or closest AU row, the judgment of every selected requirement and of
every AU-only condition with its quoted evidence, the adopt/exclude decision with reason
codes and notices, and the Luna machine label as is (matched / unmatched / review are kept).
Product identity is fixed upstream (confirmed source mapping) and is not re-judged; price and
stock are reported next to the decision and never used by it. The label is a machine
annotation, not human-verified gold, so error categories are diagnostics against it.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate_sku_gates as evaluation  # noqa: E402
import run_sku_gates as runner  # noqa: E402
import sku_gate_sources as src  # noqa: E402
import sku_gates as gates  # noqa: E402

ROOT = runner.ROOT
PAIRS = ".lab-output/sku-observed-product-pairs-20261010-final-v4"

# Reason codes of an exclusion, grouped into the risk categories of the diagnostic request.
EXCLUSION_CATEGORY = {
    "explicit_contradiction_on_every_row": "属性不一致",
    "different_value_on_row_axis": "属性不一致",
    "requirement_not_proven_on_au": "属性欠損・未記載",
    "absence_not_proven": "属性欠損・未記載",
    "au_only_condition_not_corroborated": "属性欠損・未記載",
    "mixed_sources": "根拠の食い違い",
    "page_spec_discrepancy": "商品同一性とSKU条件の混同（仕様差）",
    "several_rows_fully_proven": "兄弟SKU間の混同",
    "competing_row_not_excluded": "兄弟SKU間の混同",
    "partial_decomposition": "複合値の分解不足",
    "unquoted_requirement": "引用不足",
}


def outcome(gold: dict, binary: dict) -> str:
    if gold["decision"] == "review":
        return "gold_undetermined"
    if binary["decision"] == "matched":
        if gold["decision"] == "unmatched":
            return "false_accept"
        return "correct" if binary["top_row_key"] in gold["matching_au_row_keys"] else "wrong_row"
    return "correct" if gold["decision"] == "unmatched" else "false_unmatched"


def risk_category(kind: str, binary: dict) -> str | None:
    if kind == "correct":
        return None
    if kind == "wrong_row":
        return "兄弟SKU間の混同"
    if kind == "false_accept":
        return "同一AU行条件の見落とし"
    if kind == "gold_undetermined":
        return "商品同一性とSKU条件の混同（仕様差）" if binary.get("notices") else (
            EXCLUSION_CATEGORY.get(binary["reason_codes"][0], "その他") if binary["decision"] == "unmatched" else "その他")
    return EXCLUSION_CATEGORY.get(binary["reason_codes"][0], "その他")


def focus_row(pred: dict) -> dict | None:
    """The adopted row, else the detailed row with the most supported requirements."""
    rows = [r for r in pred["rows"] if r.get("atom_results") is not None]
    if pred["binary"]["top_row_key"]:
        return next(r for r in pred["rows"] if r["row_key"] == pred["binary"]["top_row_key"])
    return max(rows, key=lambda r: (r["status"] != "conflict", r["supported"]), default=None)


def evidence_text(evidence: list[dict]) -> list[dict]:
    return [{"source": e.get("source"), "relation": e.get("relation"), "scope": e.get("scope"),
             "quote": e.get("quote"), "span": e.get("span")} for e in evidence[:3]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=runner.DEFAULT_OUT)
    parser.add_argument("--method", default="A", choices=["A", "B"])
    args = parser.parse_args()
    out = ROOT / args.out
    runner.check_freeze(out)
    pmanifest = json.loads((out / "predictions" / "manifest.json").read_text(encoding="utf-8"))
    if pmanifest["freeze_sha256"] != src.sha256_file(out / "freeze.json"):
        raise RuntimeError("Predictions were not produced under this freeze")
    system = f"{args.method}-{gates.PRIMARY_CONFIG}"
    name = f"predictions/{system}.jsonl"
    if src.sha256_file(out / name) != pmanifest["files"][name]["sha256"]:
        raise RuntimeError(f"Prediction file changed: {name}")
    preds = {r["case_id"]: r for r in src.read_jsonl(out / name)}
    cases = {r["case_id"]: r for r in src.read_jsonl(out / "inputs" / "cases.jsonl")}
    contexts = {r["dossier_id"]: r for r in src.read_jsonl(out / "inputs" / "products.jsonl")}
    labels = {r["case_id"]: r for r in src.read_jsonl(ROOT / evaluation.LABELS)}
    expanded = {(r["pair_id"], r["rakuten_sku"]["sku_record_key"]): r
                for r in src.read_jsonl(ROOT / PAIRS / "rakuten_sku_expanded.jsonl")}
    arrays = {r["au_product_id"]: r for r in src.read_jsonl(ROOT / PAIRS / "au_product_sku_arrays.jsonl")}

    rows, table = [], []
    for cid in sorted(cases):
        case, pred, gold = cases[cid], preds[cid], labels[cid]
        ctx = contexts[case["dossier_id"]]
        sel = case["rakuten_selected"]
        exp = expanded.get((ctx["pair_ref"], sel["sku_record_key"]))
        au_id = ctx["au_product"]["product_id"]
        array = arrays[au_id]
        cells = {f"au:{au_id}:{c['source_grain']['sku_id']}:{c['source_grain']['row_index']}:{c['source_grain']['column_index']}": c
                 for c in array["au_sku_cells_raw"]}
        row = focus_row(pred)
        reqs = {q["requirement_id"]: q for q in pred["requirements"]}
        attributes = []
        if row is not None:
            for res in row.get("atom_results") or []:
                q = reqs[res["requirement_id"]]
                attributes.append({"requirement_id": q["requirement_id"], "axis": q["axis_label"], "type": q["type"],
                                   "value": q["value"], "quote": q["quote"], "status": res["status"],
                                   "note": res.get("note"), "evidence": evidence_text(res.get("evidence") or [])})
        au_only = [{"type": a["type"], "value": a["value"], "quote": a["quote"], "status": a["status"],
                    "evidence": evidence_text(a.get("evidence") or [])} for a in (row or {}).get("au_only_atoms") or []]
        binary = pred["binary"]
        kind = outcome(gold, binary)
        rak_price = exp["raku_price_jpy"] if exp else None
        au_price = exp["au_product_price_jpy"] if exp else array.get("au_product_price_jpy_once")
        stock = (cells.get(row["row_key"]) or {}).get("stock_raw") if row else None
        record = {
            "case_id": cid, "dossier_id": case["dossier_id"], "pair_ref": ctx["pair_ref"],
            "product_identity": "confirmed_source_mapping (fixed upstream; not re-judged here)",
            "rakuten": {"url": sel["url"], "source_sku_key": sel["source_sku_key"], "sku_record_key": sel["sku_record_key"],
                        "variant_id": sel["variant_id"], "raw_file": sel["raw_file"], "sha256": sel["sha256"],
                        "selection": [{"axis": a["axis_label"], "value": a["value"]} for a in sel["axes"]],
                        "price_jpy": rak_price, "price_source": exp["rakuten_sku_source"] if exp else None,
                        "availability": (exp or {}).get("raku_availability_raw")},
            "au": {"product_id": au_id, "item_api_url": array["provenance"]["item_api"]["final_url"],
                   "raw_file": ctx["au_product"]["raw_file"], "product_price_jpy": au_price,
                   "price_grain": "product-level (AU has no per-SKU price)", "row_key": row["row_key"] if row else None,
                   "row_status": row["status"] if row else None, "row_stock": stock},
            "price_comparison": ("missing" if rak_price is None or au_price is None else
                                 "equal" if rak_price == au_price else "different"),
            "decision": binary["decision"], "adopted_row_key": binary["top_row_key"],
            "reason_codes": binary["reason_codes"], "notices": binary.get("notices") or [],
            "gate": {"decision": pred["decision"], "reason": pred["reason"]},
            "requirements": attributes, "au_only_conditions": au_only,
            "label": {"decision": gold["decision"], "matching_au_row_keys": gold["matching_au_row_keys"],
                      "rationale": gold.get("rationale"), "human_verified": False},
            "outcome": kind, "risk_category": risk_category(kind, binary)}
        rows.append(record)
        table.append({
            "case_id": cid, "pair_ref": ctx["pair_ref"],
            "rakuten_selection": " / ".join(f"{a['axis']}={a['value']}" for a in record["rakuten"]["selection"]),
            "rakuten_sku_key": sel["source_sku_key"], "rakuten_price_jpy": rak_price,
            "rakuten_price_source": f"{exp['rakuten_sku_source']['file']}:{exp['rakuten_sku_source']['line']}" if exp else "",
            "au_product_id": au_id, "au_product_price_jpy": au_price, "price_comparison": record["price_comparison"],
            "decision": binary["decision"], "adopted_row_key": binary["top_row_key"] or "",
            "reason_codes": ";".join(binary["reason_codes"]), "notices": len(record["notices"]),
            "requirement_status": ";".join(f"{a['requirement_id']}={a['status']}" for a in attributes),
            "au_only_status": ";".join(f"{a['type']}:{a['value']}={a['status']}" for a in au_only),
            "label": gold["decision"], "outcome": kind, "risk_category": record["risk_category"] or ""})

    decided = [r for r in rows if r["outcome"] != "gold_undetermined"]
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "system": system,
        "freeze_sha256": src.sha256_file(out / "freeze.json"),
        "prediction_sha256": pmanifest["files"][name]["sha256"],
        "labels": {"path": evaluation.LABELS, "sha256": src.sha256_file(ROOT / evaluation.LABELS),
                   "human_verified": False, "independent_gold": False},
        "cases": len(rows), "label_decisions": dict(Counter(r["label"]["decision"] for r in rows)),
        "product_identity": dict(Counter(r["product_identity"] for r in rows)),
        "binary_decisions": dict(Counter(r["decision"] for r in rows)), "automatic_decision_rate": 1.0,
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "gold_decided_rows": len(decided), "correct_on_gold_decided": sum(r["outcome"] == "correct" for r in decided),
        "risk_categories": {k: dict(v) for k, v in sorted(_by(rows, "outcome", "risk_category").items())},
        "reason_codes": dict(Counter(c for r in rows for c in r["reason_codes"])),
        "requirement_status_by_type": {k: dict(v) for k, v in sorted(_req_status(rows).items())},
        "au_only_status": dict(Counter(f"{a['type']}={a['status']}" for r in rows for a in r["au_only_conditions"])),
        "price_comparison": dict(Counter(r["price_comparison"] for r in rows)),
        "rakuten_price_missing": sum(r["rakuten"]["price_jpy"] is None for r in rows),
        "rakuten_availability": dict(Counter((r["rakuten"]["availability"] or {}).get("availability_from_embedded_quantity")
                                             for r in rows)),
        "au_row_stock_sold_out": dict(Counter(str((r["au"]["row_stock"] or {}).get("isSoldOut")) for r in rows)),
        "notes": ["price and stock are reported, never used by the decision",
                  "AU price is product-level; Rakuten price is per SKU from the source SKU row",
                  "644 Rakuten source SKUs appear in more than one fixed AU pair context; rows are cases, not unique SKUs"]}
    target = out / "diagnostics" / args.method
    runner.write_jsonl_new(target / "rows.jsonl", rows)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(table[0]))
    writer.writeheader()
    writer.writerows(table)
    runner.write_new(target / "rows.csv", buffer.getvalue())
    runner.write_new(target / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("system", "outcomes", "risk_categories", "price_comparison",
                                              "rakuten_availability")}, ensure_ascii=False, indent=1))


def _by(rows, a, b):
    table = defaultdict(Counter)
    for r in rows:
        table[r[a]][r[b] or "-"] += 1
    return table


def _req_status(rows):
    table = defaultdict(Counter)
    for r in rows:
        for q in r["requirements"]:
            table[q["type"]][q["status"]] += 1
    return table


if __name__ == "__main__":
    main()
