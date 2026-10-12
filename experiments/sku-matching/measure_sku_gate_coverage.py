"""Label-free coverage of the SKU-gate atomizer and source spans over every captured SKU.

Reads every Rakuten SKU record (normalized-v2 + expanded captures) and every AU SKU cell
(au_product_sku_arrays), resolves each selected value to its original file, and classifies
how the atomizer decomposes it. Pair status (confirmed / family / candidate / unpaired) is
reported separately; nothing here is a match decision and no label is read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_sku_gates as runner  # noqa: E402
import sku_gate_sources as src  # noqa: E402
import sku_gates as gates  # noqa: E402
from sku_gate_atoms import atomize, color_base_vocab  # noqa: E402

ROOT = HERE.parents[1]
PAIRS = ".lab-output/sku-observed-product-pairs-20261010-final-v4"
RAKUTEN_SKUS = (".lab-output/sku-real-rakuten-20261010-normalized-v2/skus.jsonl",
                ".lab-output/sku-real-rakuten-20261010-expanded/skus.jsonl")
AU_RAW_DIRS = (".lab-output/sku-real-au-20261010", ".lab-output/sku-real-au-20261010-expanded/raw")
STATUS_ORDER = ("confirmed_source_mapping", "evidence_backed_primary_image_family_mapping", "candidate_review")


def classify(parsed: dict) -> str:
    types = {a["type"] for a in parsed["atoms"]}
    if parsed["decomposition"] != "complete":
        return "partial_residue"
    if not types:
        return "no_atom"
    if types <= {"variant"}:
        return "literal_only"
    if types <= {"color", "variant"}:
        return "color"
    return "typed"


def page_encoding(store: src.RawStore, rel: str) -> str:
    head = store.raw(rel)[:4096]
    m = re.search(rb"charset\s*=\s*[\"']?([\w.-]+)", head, re.I)
    return m.group(1).decode("ascii") if m else "utf-8"


def pair_groups():
    """Strongest pair status per Rakuten page URL and per AU product."""
    rank = {s: i for i, s in enumerate(STATUS_ORDER)}
    by_url, by_au = {}, {}
    for line in (ROOT / PAIRS / "observed_product_pairs.jsonl").read_text(encoding="utf-8").splitlines():
        p = json.loads(line)
        s = p["pair_status"]
        if s not in rank:
            continue
        for key, table in ((p.get("rakuten_url"), by_url), (p.get("au_product_id"), by_au)):
            if key and (key not in table or rank[s] < rank[table[key]]):
                table[key] = s
    return by_url, by_au


def measure_rakuten(store, by_url, values_out):
    rows = [json.loads(x) for rel in RAKUTEN_SKUS for x in (ROOT / rel).read_text(encoding="utf-8").splitlines()]
    page_cache, stats = {}, defaultdict(Counter)
    for rec in rows:
        rel, url = rec["raw_file"], rec["source_url"]
        group = by_url.get(url, "unpaired")
        if rel not in page_cache:
            enc = page_encoding(store, rel)
            selectors = src.rakuten_variant_selectors(store, rel, enc)
            colors = [v for s in selectors if any(w in (s["label"] or s["key"]) for w in ("カラー", "色")) for v in s["values"]]
            page_cache[rel] = (enc, {s["key"]: s for s in selectors}, color_base_vocab(colors))
        enc, selectors, vocab = page_cache[rel]
        try:
            spans = src.rakuten_selected_values(store, rel, enc, rec["variant_id"])
            span_status = "resolved"
        except ValueError as exc:
            spans, span_status = [], "variant_missing" if "missing" in str(exc) else "conflicting_copies"
        if rec["sha256"] != store.sha(rel):
            span_status = "sha_mismatch"
        stats[group]["sku_rows"] += 1
        for i, ov in enumerate(rec["option_values"]):
            sel = selectors.get(ov["axis_key"]) or {}
            label = sel.get("label") or ov["axis_key"]
            span = spans[i] if i < len(spans) else None
            status = span_status
            if span_status == "resolved":
                status = "resolved" if span and span.get("quote") == ov["value"] else \
                    "unresolved_escape" if span and "unresolved_escape" in span else "quote_mismatch"
            parsed = atomize(ov["value"], label, vocab, tuple(sel.get("values") or ()))
            cls = classify(parsed)
            stats[group][f"span:{status}"] += 1
            stats[group][f"class:{cls}"] += 1
            values_out.append({"side": "rakuten", "group": group, "page": url, "raw_file": rel,
                               "variant_id": rec["variant_id"], "axis_label": label, "value": ov["value"],
                               "span_status": status, "class": cls,
                               "atom_types": sorted({a["type"] for a in parsed["atoms"]}),
                               "residue": parsed.get("residue")})
    return {g: dict(c) for g, c in stats.items()}, len(rows), len(page_cache)


def measure_au(store, by_au, values_out):
    arrays = [json.loads(x) for x in (ROOT / PAIRS / "au_product_sku_arrays.jsonl").read_text(encoding="utf-8").splitlines()]
    stats = defaultdict(Counter)
    for rec in arrays:
        pid = rec["au_product_id"]
        group = by_au.get(pid, "unpaired")
        want = rec["provenance"]["item_api"]["sha256"]
        rel = next((f"{d}/{pid}-item.json" for d in AU_RAW_DIRS
                    if (ROOT / d / f"{pid}-item.json").exists() and store.sha(f"{d}/{pid}-item.json") == want), None)
        stats[group]["products"] += 1
        if rel is None:
            stats[group]["raw_item_missing"] += 1
            continue
        info = store.json(rel)["itemInfo"]["skuInfo"]
        cells = rec["au_sku_cells_raw"]
        colors = [a["value_raw"] for c in cells for a in c["axes_raw"] if any(w in a["axis_name_raw"] for w in ("カラー", "色"))]
        vocab = color_base_vocab(colors)
        fams = defaultdict(set)
        for c in cells:
            for a in c["axes_raw"]:
                if a["value_raw"]:
                    fams[a["axis_name_raw"]].add(a["value_raw"])
        for c in cells:
            stats[group]["cells"] += 1
            g = c["source_grain"]
            for kind, (a, path) in zip(("row", "column"), zip(c["axes_raw"], (f"$.itemInfo.skuInfo.rowNames[{g['row_index']}]",
                                                                              f"$.itemInfo.skuInfo.columnNames[{g['column_index']}]"))):
                if not a["value_raw"]:
                    continue
                span = src.json_leaf_span(store, rel, path)
                status = "resolved" if span and span["quote"] == a["value_raw"] else "quote_mismatch"
                parsed = atomize(a["value_raw"], a["axis_name_raw"], vocab, tuple(sorted(fams[a["axis_name_raw"]])))
                cls = classify(parsed)
                stats[group][f"span:{status}"] += 1
                stats[group][f"class:{cls}"] += 1
                values_out.append({"side": "au", "group": group, "au_product_id": pid, "raw_file": rel,
                                   "row_key": f"au:{pid}:{info['skuId']}:{g['row_index']}:{g['column_index']}",
                                   "axis_label": a["axis_name_raw"], "value": a["value_raw"], "span_status": status,
                                   "class": cls, "atom_types": sorted({x["type"] for x in parsed["atoms"]}),
                                   "residue": parsed.get("residue")})
    return {g: dict(c) for g, c in stats.items()}, len(arrays)


def by_axis(values):
    """Distinct (page or product, axis, value) triples per axis label and class."""
    seen, table = set(), defaultdict(Counter)
    for v in values:
        key = (v["side"], v.get("page") or v.get("au_product_id"), v["axis_label"], v["value"])
        if key in seen:
            continue
        seen.add(key)
        table[(v["side"], v["axis_label"])][v["class"]] += 1
    rows = [{"side": s, "axis_label": a, "distinct_values": sum(c.values()), **dict(c)} for (s, a), c in table.items()]
    return sorted(rows, key=lambda r: (r["side"], -r["distinct_values"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".lab-output/sku-gate-coverage-20261010-v1")
    args = parser.parse_args()
    out = ROOT / args.out
    store = src.RawStore(ROOT)
    by_url, by_au = pair_groups()
    values = []
    rak_stats, rak_rows, rak_pages = measure_rakuten(store, by_url, values)
    au_stats, au_products = measure_au(store, by_au, values)
    axes = by_axis(values)
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False,
               "task_version": gates.TASK_VERSION,
               "code_sha256": {p: src.sha256_file(HERE / p) for p in
                               ("measure_sku_gate_coverage.py", "sku_gate_atoms.py", "sku_gate_sources.py")},
               "inputs": {rel: src.sha256_file(ROOT / rel) for rel in
                          (*RAKUTEN_SKUS, f"{PAIRS}/au_product_sku_arrays.jsonl", f"{PAIRS}/observed_product_pairs.jsonl")},
               "rakuten": {"sku_rows": rak_rows, "pages": rak_pages, "by_pair_group": rak_stats},
               "au": {"products": au_products, "by_pair_group": au_stats},
               "group_note": "strongest observed pair status per Rakuten page URL / AU product; candidate and family "
                             "groups are not confirmed product pairs"}
    runner.write_new(out / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    runner.write_new(out / "by_axis.json", json.dumps(axes, ensure_ascii=False, indent=1) + "\n")
    runner.write_new(out / "values.jsonl", "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values))
    print(json.dumps({"rakuten": summary["rakuten"], "au": summary["au"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
