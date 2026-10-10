"""Prepare raw-backed family-mapping stress inputs for the frozen binary SKU gate.

These inputs are exploratory stress cases, not an independent accuracy holdout:
family mappings are image-family candidates and are not proven identical products.
Only raw pair records are consumed; no labels, model outputs, or predictions are read.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PAIR_ROOT = ".lab-output/sku-observed-product-pairs-20261010-final-v4"
PAIR_FILE = f"{PAIR_ROOT}/candidate_sku_review.jsonl"
ARRAYS_FILE = f"{PAIR_ROOT}/au_product_sku_arrays.jsonl"
OUT = ".lab-output/sku-nonllm-family-inputs-20261010-v1"
STATUS = "evidence_backed_primary_image_family_mapping"
FROZEN_CODE = ROOT / ".lab-output/sku-nonllm-binary-gate-20261010-v4/code_snapshot"

sys.path.insert(0, str(FROZEN_CODE / "claude-gate"))
sys.path.insert(0, str(FROZEN_CODE / "runner"))
sys.path.insert(0, str(HERE))
import build_luna_annotation_inputs as annot  # noqa: E402
import sku_gate_sources as src  # noqa: E402
import sku_gates as gates  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short(value: str, n: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:n]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        f.write(text)


def write_jsonl_new(path: Path, rows: list[dict]) -> None:
    write_new(path, "".join(gates.canonical_json(row) + "\n" for row in rows))


def au_rows(arrays: dict, product_id: str) -> list[dict]:
    rows = []
    for cell in arrays[product_id]["au_sku_cells_raw"]:
        grain = cell["source_grain"]
        rows.append({
            "row_key": f"au:{product_id}:{grain['sku_id']}:{grain['row_index']}:{grain['column_index']}",
            "axes_raw": cell["axes_raw"], "source_grain": grain,
            "source_record_ref": {"item_id": product_id, "sku_id": grain["sku_id"], "platform": "au_PAY_Market"},
        })
    return rows


def pseudo_dossier(pair_id: str, record: dict, arrays: dict) -> dict:
    au_raw = record["provenance"]["au_raw_item"]
    rak = record.get("rakuten_product_ref")
    rak_raw, rak_sha = ((rak["raw_file"], rak["sha256"]) if rak else
                        (record["rakuten_sku"]["raw_file"], record["rakuten_sku"]["sha256"]))
    product_id = record["au_product_ref"]["item_id"]
    return {
        "dossier_id": f"raw-{short(pair_id, 16)}", "pair_ref": pair_id,
        "group_id": f"raw-{STATUS}", "pair_status": STATUS,
        "au_product": {"product_id": product_id,
                       "source": {"raw_file": au_raw["file"], "sha256": au_raw["sha256"]},
                       "description": annot.extract_au_description(ROOT / au_raw["file"], au_raw["sha256"])},
        "au_rows": au_rows(arrays, product_id),
        "rakuten_product": {
            "source": {"url": record["rakuten_sku"]["source_url"], "raw_file": rak_raw, "sha256": rak_sha},
            "description": annot.extract_rakuten_description(ROOT / rak_raw, rak_sha),
            "title_evidence_raw": {"text": record.get("raku_title_raw") or ""},
        },
    }


def pseudo_case(pair_id: str, dossier: dict, record: dict, selectors: list[dict]) -> dict:
    sku = record["rakuten_sku"]
    labels = {s["key"]: s.get("label") for s in selectors}
    return {
        "case_id": f"case-{short(pair_id + '|' + sku['sku_record_key'], 20)}",
        "dossier_id": dossier["dossier_id"], "group_id": dossier["group_id"], "split": "exploration",
        "pair_status": STATUS,
        "rakuten": {
            "axes_labels": [{"key": o["axis_key"], "label": labels.get(o["axis_key"]) or o["axis_key"],
                             "name": o.get("axis_name")} for o in sku["option_values"]],
            "option_values": sku["option_values"],
            "source": {"raw_file": sku["raw_file"], "sha256": sku["sha256"], "url": sku["source_url"],
                       "sku_record_key": sku["sku_record_key"], "source_row_index": sku["source_row_index"],
                       "source_row_key": sku["source_row_key"], "source_sku_key": sku["source_sku_key"],
                       "source_sku_jsonl": {"file": record["rakuten_sku_source"]["file"],
                                             "line": record["rakuten_sku_source"]["line"]}},
        },
    }


def main() -> None:
    out = ROOT / OUT
    source_records = [r for r in read_jsonl(ROOT / PAIR_FILE) if r.get("pair_status") == STATUS]
    by_pair: dict[str, list[dict]] = defaultdict(list)
    for record in source_records:
        by_pair[record["pair_id"]].append(record)

    arrays = {}
    arrays_index = {}
    for i, record in enumerate(read_jsonl(ROOT / ARRAYS_FILE), 1):
        arrays.setdefault(record["au_product_id"], record)
        arrays_index.setdefault(record["au_product_id"], i)

    store = src.RawStore(ROOT)
    contexts, cases = {}, []
    failures = []
    for pair_id in sorted(by_pair):
        records = by_pair[pair_id]
        try:
            dossier = pseudo_dossier(pair_id, records[0], arrays)
            context = gates.build_product_context(store, dossier, None, ARRAYS_FILE, arrays_index)
            context["pair_status"] = STATUS
        except (ValueError, KeyError, OSError, IndexError) as exc:
            failures.append({"pair_id": pair_id, "stage": "product_context",
                             "error": f"{type(exc).__name__}: {exc}", "sku_records": len(records)})
            continue
        selectors = src.rakuten_variant_selectors(store, context["rakuten_product"]["raw_file"],
                                                   context["rakuten_product"]["encoding"])
        built = 0
        for record in records:
            try:
                case = pseudo_case(pair_id, dossier, record, selectors)
                case_input = gates.build_case_input(store, case, context)
                case_input["pair_status"] = STATUS
                case_input["source_record"] = {"file": PAIR_FILE, "pair_id": pair_id,
                                                "sku_record_key": record["rakuten_sku"]["sku_record_key"]}
                cases.append(case_input)
                built += 1
            except (ValueError, KeyError, IndexError) as exc:
                failures.append({"pair_id": pair_id, "stage": "case_input",
                                 "error": f"{type(exc).__name__}: {exc}",
                                 "sku_record_key": record["rakuten_sku"]["sku_record_key"]})
        if built:
            contexts[context["dossier_id"]] = context

    write_jsonl_new(out / "inputs" / "products.jsonl", [contexts[k] for k in sorted(contexts)])
    write_jsonl_new(out / "inputs" / "cases.jsonl", cases)
    write_jsonl_new(out / "inputs" / "failures.jsonl", failures)
    source_files = sorted(set(store._sha) | {PAIR_FILE, ARRAYS_FILE})
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "task_version": gates.TASK_VERSION, "labels_read": False,
        "pair_status": STATUS, "split": "exploration", "is_confirmed_benchmark": False,
        "claim": "unconfirmed product-candidate stress set; not an accuracy holdout",
        "pair_identity_caveat": "family mapping was inferred from primary-image family evidence; exact product identity is unconfirmed",
        "source_pair_count": len(by_pair), "source_sku_record_count": len(source_records),
        "case_count": len(cases), "product_pair_count": len(contexts),
        "unique_au_sku_count": len({(c["au_product"]["product_id"], r["sku_id"])
                                    for c in contexts.values() for r in c["au_rows"]}),
        "unique_rakuten_sku_record_count": len({r["rakuten_sku"]["sku_record_key"] for r in source_records}),
        "full_au_row_count": sum(len(c["au_rows"]) for c in contexts.values()),
        "failures": dict(Counter(f["stage"] for f in failures)),
        "source_sha256": {p: src.sha256_file(ROOT / p) for p in source_files},
        "outputs_sha256": {f"inputs/{name}": src.sha256_file(out / "inputs" / name)
                           for name in ("products.jsonl", "cases.jsonl", "failures.jsonl")},
        "frozen_gate_code_sha256": {
            "sku_gates.py": sha((FROZEN_CODE / "claude-gate/sku_gates.py").read_bytes()),
            "sku_gate_sources.py": sha((FROZEN_CODE / "claude-gate/sku_gate_sources.py").read_bytes()),
        },
        "excluded_non_identity_fields": list(gates.NON_IDENTITY_FIELDS),
    }
    write_new(out / "inputs" / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ("source_pair_count", "source_sku_record_count", "product_pair_count",
                                               "case_count", "unique_au_sku_count", "unique_rakuten_sku_record_count",
                                               "full_au_row_count", "failures")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
