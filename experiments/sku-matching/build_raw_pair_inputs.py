"""Build SKU-gate inputs for observed pairs straight from the raw captures.

The 29 confirmed pairs have annotation dossiers; family-mapped and candidate pairs do not.
This builder makes the same pseudo-dossier from the raw AU item JSON, the AU SKU array and the
Rakuten page HTML (descriptions through the annotation builder's own extractors), then reuses
`build_product_context` / `build_case_input`, so every quote keeps its original file span.

Family-mapped and candidate pairs are NOT confirmed product pairs. Their cases carry the pair
status and split `exploration`; they never enter the confirmed benchmark or its metrics. A pair
or SKU that cannot be built from its sources is written to failures.jsonl, never filled in.
Labels are not read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_luna_annotation_inputs as annot  # noqa: E402
import run_sku_gates as runner  # noqa: E402
import sku_gate_sources as src  # noqa: E402
import sku_gates as gates  # noqa: E402

ROOT = runner.ROOT
PAIRS = ".lab-output/sku-observed-product-pairs-20261010-final-v4"
SOURCES = {"confirmed": f"{PAIRS}/rakuten_sku_expanded.jsonl", "family": f"{PAIRS}/candidate_sku_review.jsonl",
           "candidate": f"{PAIRS}/candidate_sku_review.jsonl"}
STATUS = {"confirmed": "confirmed_source_mapping", "family": "evidence_backed_primary_image_family_mapping",
          "candidate": "candidate_review"}


def short(text: str, n: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def au_rows(arrays: dict, product_id: str) -> list[dict]:
    record = arrays[product_id]
    rows = []
    for cell in record["au_sku_cells_raw"]:
        g = cell["source_grain"]
        rows.append({"row_key": f"au:{product_id}:{g['sku_id']}:{g['row_index']}:{g['column_index']}",
                     "axes_raw": cell["axes_raw"], "source_grain": g,
                     "source_record_ref": {"item_id": product_id, "sku_id": g["sku_id"], "platform": "au_PAY_Market"}})
    return rows


def pseudo_dossier(pair_id: str, record: dict, arrays: dict) -> dict:
    au_raw = record["provenance"]["au_raw_item"]
    rak = record["rakuten_product_ref"] if "rakuten_product_ref" in record else None
    rak_raw, rak_sha = (rak["raw_file"], rak["sha256"]) if rak else (record["rakuten_sku"]["raw_file"],
                                                                     record["rakuten_sku"]["sha256"])
    product_id = record["au_product_ref"]["item_id"]
    return {"dossier_id": f"raw-{short(pair_id, 16)}", "pair_ref": pair_id, "group_id": f"raw-{record['pair_status']}",
            "au_product": {"product_id": product_id,
                           "source": {"raw_file": au_raw["file"], "sha256": au_raw["sha256"]},
                           "description": annot.extract_au_description(ROOT / au_raw["file"], au_raw["sha256"])},
            "au_rows": au_rows(arrays, product_id),
            "rakuten_product": {"source": {"url": record["rakuten_sku"]["source_url"], "raw_file": rak_raw, "sha256": rak_sha},
                                "description": annot.extract_rakuten_description(ROOT / rak_raw, rak_sha),
                                "title_evidence_raw": {"text": record.get("raku_title_raw") or ""}}}


def pseudo_case(pair_id: str, dossier: dict, record: dict, selectors: list[dict]) -> dict:
    sku = record["rakuten_sku"]
    labels = {s["key"]: s.get("label") for s in selectors}
    return {"case_id": f"case-{short(pair_id + '|' + sku['sku_record_key'], 20)}", "dossier_id": dossier["dossier_id"],
            "group_id": dossier["group_id"], "split": "exploration",
            "rakuten": {"axes_labels": [{"key": o["axis_key"], "label": labels.get(o["axis_key"]) or o["axis_key"],
                                         "name": o.get("axis_name")} for o in sku["option_values"]],
                        "option_values": sku["option_values"],
                        "source": {"raw_file": sku["raw_file"], "sha256": sku["sha256"], "url": sku["source_url"],
                                   "sku_record_key": sku["sku_record_key"], "source_row_index": sku["source_row_index"],
                                   "source_row_key": sku["source_row_key"], "source_sku_key": sku["source_sku_key"],
                                   "source_sku_jsonl": {"file": record["rakuten_sku_source"]["file"],
                                                        "line": record["rakuten_sku_source"]["line"]}}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("status", choices=sorted(SOURCES))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = ROOT / args.out
    store = src.RawStore(ROOT)
    arrays_rel = f"{PAIRS}/au_product_sku_arrays.jsonl"
    arrays, arrays_index = {}, {}
    for i, line in enumerate((ROOT / arrays_rel).read_text(encoding="utf-8").splitlines()):
        rec = json.loads(line)
        arrays.setdefault(rec["au_product_id"], rec)
        arrays_index.setdefault(rec["au_product_id"], i + 1)
    by_pair = defaultdict(list)
    for line in (ROOT / SOURCES[args.status]).read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["pair_status"] == STATUS[args.status]:
            by_pair[rec["pair_id"]].append(rec)
    contexts, cases, failures = {}, [], []
    for pair_id in sorted(by_pair):
        records = by_pair[pair_id]
        try:
            dossier = pseudo_dossier(pair_id, records[0], arrays)
            context = gates.build_product_context(store, dossier, None, arrays_rel, arrays_index)
            context["pair_status"] = STATUS[args.status]
        except (ValueError, KeyError, OSError, IndexError) as exc:
            failures.append({"pair_id": pair_id, "stage": "product_context", "error": f"{type(exc).__name__}: {exc}",
                             "sku_records": len(records)})
            continue
        selectors = src.rakuten_variant_selectors(store, context["rakuten_product"]["raw_file"],
                                                  context["rakuten_product"]["encoding"])
        built = 0
        for rec in records:
            try:
                case = pseudo_case(pair_id, dossier, rec, selectors)
                ci = gates.build_case_input(store, case, context)
                ci["pair_status"] = STATUS[args.status]
                ci["source_record"] = {"file": SOURCES[args.status], "pair_id": pair_id,
                                       "sku_record_key": rec["rakuten_sku"]["sku_record_key"]}
                cases.append(ci)
                built += 1
            except (ValueError, KeyError, IndexError) as exc:
                failures.append({"pair_id": pair_id, "stage": "case_input", "error": f"{type(exc).__name__}: {exc}",
                                 "sku_record_key": rec["rakuten_sku"]["sku_record_key"]})
        if built:
            contexts[context["dossier_id"]] = context
    ctx_validator = runner.schema("sku_gate_product_context.schema.json")
    case_validator = runner.schema("sku_gate_case_input.schema.json")
    for ctx in contexts.values():
        ctx_validator.validate({k: v for k, v in ctx.items() if k != "pair_status"})
    for ci in cases:
        case_validator.validate({k: v for k, v in ci.items() if k not in ("pair_status", "source_record")})
    runner.write_jsonl_new(out / "inputs" / "products.jsonl", [contexts[k] for k in sorted(contexts)])
    runner.write_jsonl_new(out / "inputs" / "cases.jsonl", cases)
    runner.write_jsonl_new(out / "inputs" / "failures.jsonl", failures)
    manifest = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "task_version": gates.TASK_VERSION,
                "labels_read": False, "pair_status": STATUS[args.status],
                "is_confirmed_benchmark": args.status == "confirmed",
                "note": "built from raw captures without annotation dossiers; family/candidate pairs are not confirmed "
                        "product pairs and stay outside the benchmark",
                "source_pairs": len(by_pair), "source_sku_records": sum(len(v) for v in by_pair.values()),
                "case_count": len(cases), "product_pairs": len(contexts),
                "au_rows": sum(len(c["au_rows"]) for c in contexts.values()),
                "failures": dict(Counter(f["stage"] for f in failures)),
                "source_sha256": {p: src.sha256_file(ROOT / p) for p in sorted(set(store._sha) | {SOURCES[args.status], arrays_rel})},
                "outputs_sha256": {f"inputs/{n}": src.sha256_file(out / "inputs" / n) for n in
                                   ("products.jsonl", "cases.jsonl", "failures.jsonl")},
                "excluded_non_identity_fields": list(gates.NON_IDENTITY_FIELDS)}
    runner.write_new(out / "inputs" / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ("pair_status", "source_pairs", "source_sku_records", "product_pairs",
                                               "case_count", "failures")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
