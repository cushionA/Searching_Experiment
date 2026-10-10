#!/usr/bin/env python3
"""Freeze source-cited residual-axis tasks for the local CPU trial.

This file only reads frozen source material and existing label-free v10 tasks.
It never opens annotation labels, predictions, or gold fields.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUTS = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v3"
DEFAULT_AU_ARRAYS = ROOT / ".lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl"
DEFAULT_V10 = ROOT / ".lab-output/sku-structured-task-trials-20261010-v10"
DEFAULT_GPU_INPUTS = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v2/dataset-upload/inputs.jsonl"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v5"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import evaluate_luna_real_skus as real
import structured_sku_task as structured


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json_path(doc: dict[str, Any], path: str) -> Any:
    parts = path.removeprefix("$").lstrip(".").split(".")
    value: Any = doc
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _case_axes(case: dict[str, Any]) -> list[dict[str, Any]]:
    rak = case.get("rakuten", {})
    labels = {str(x.get("key")): x.get("label") or x.get("name") or x.get("key")
              for x in rak.get("axes_labels", []) if x.get("key")}
    source = rak.get("source", {})
    out = []
    for i, option in enumerate(rak.get("option_values", [])):
        name = option.get("axis_name") or labels.get(str(option.get("axis_key"))) or option.get("axis_key") or ""
        value = option.get("value", "")
        if not str(value).strip():
            continue
        key = structured._axis_key(name)
        norm = structured._value(key, value)
        out.append({
            "source_order": i, "axis_name_raw": str(name), "axis_key_raw": option.get("axis_key"),
            "value_raw": str(value), "semantic_key": key, "normalized_value": norm,
            "source_ref": {"raw_file": source.get("raw_file"), "sha256": source.get("sha256"),
                           "json_path": source.get("json_path"),
                           "source_row_key": source.get("source_row_key"),
                           "source_sku_key": source.get("source_sku_key")},
        })
    return out


def _au_axes(cell: dict[str, Any], product: dict[str, Any], row_key: str) -> list[dict[str, Any]]:
    grain = cell.get("source_grain", {})
    source_ref = {"raw_file": grain.get("file"), "source_grain": grain,
                  "product_id": product.get("au_product_id"), "row_key": row_key}
    out = []
    for i, axis in enumerate(cell.get("axes_raw", [])):
        name, value = axis.get("axis_name_raw", ""), axis.get("value_raw", "")
        if not str(name).strip() and not str(value).strip():
            continue
        key = structured._axis_key(name)
        out.append({"source_order": i, "axis_name_raw": str(name), "value_raw": str(value),
                    "semantic_key": key, "normalized_value": structured._value(key, value),
                    "source_ref": source_ref})
    return out


def _safe_entity(entity: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    evidence_id = entity.get("evidence_ref")
    cited = evidence.get(evidence_id, {}).get("evidence", {}) if evidence_id else entity.get("evidence", {})
    # Preserve the complete source-verified semantic entity. Restricting this
    # to a hand-picked schema silently dropped required_fields used by hybrid.
    safe = copy.deepcopy(entity)
    safe.update({
        "row_key": entity.get("row_key"), "raw_sku": entity.get("raw_sku"),
        "selected_fields": entity.get("selected_fields", []),
        "attrs": entity.get("attrs", {}), "raw_attrs": entity.get("raw_attrs"),
        "unknown_fields": entity.get("unknown_fields", []),
        "unresolved_axes": entity.get("unresolved_axes", []),
        "source_conflicts": entity.get("source_conflicts", []),
        "contents_list_assumption": entity.get("contents_list_assumption"),
        "evidence_ref": evidence_id, "evidence": cited,
    })
    return safe


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(f"Refusing existing output directory: {args.output}")
    cases_path = args.inputs / "cases.jsonl"
    dossiers_dir = args.inputs / "dossiers"
    v10_tasks = args.v10 / "tasks.jsonl"
    v10_evidence = args.v10 / "evidence.jsonl"
    paths = [cases_path, args.inputs / "manifest.json", args.au_arrays,
             args.au_arrays.parent / "manifest.json", v10_tasks, v10_evidence,
             args.gpu_inputs, args.gpu_inputs.parent / "manifest.json"]
    paths += sorted(dossiers_dir.glob("*.json"))
    if not all(p.is_file() for p in paths):
        raise FileNotFoundError("One or more frozen source/task inputs are missing")
    source_pre = {str(p.resolve()): sha256(p) for p in paths}

    cases = read_jsonl(cases_path)
    dossiers = {d["dossier_id"]: d for d in
                (json.loads(p.read_text(encoding="utf-8")) for p in dossiers_dir.glob("*.json"))}
    arrays = read_jsonl(args.au_arrays)
    arrays_by_pid = {str(x["au_product_id"]): x for x in arrays}
    frozen_tasks = read_jsonl(v10_tasks)
    evidence = {x["evidence_id"]: x for x in read_jsonl(v10_evidence)}
    task_by_id = {x["case_id"]: x for x in frozen_tasks}
    cases_by_id = {x["case_id"]: x for x in cases}
    if len(cases) != 1383 or len(cases_by_id) != len(cases) or len(task_by_id) != len(frozen_tasks):
        raise ValueError("Frozen source IDs/counts do not match the 1,383-case task set")
    if set(cases_by_id) != set(task_by_id):
        raise ValueError("Source case IDs and current task IDs differ")
    gpu_ids = {x["case_id"] for x in read_jsonl(args.gpu_inputs)}
    if len(gpu_ids) != 196 or not gpu_ids <= cases_by_id.keys():
        raise ValueError("Frozen GPU case cohort is not the expected 196-case subset")

    product_rows = []
    product_index = {}
    cases_by_dossier: dict[str, list[dict[str, Any]]] = {}
    for source_case in cases:
        cases_by_dossier.setdefault(source_case["dossier_id"], []).append(source_case)
    for dossier_id, dossier in sorted(dossiers.items()):
        au = dossier["au_product"]
        pid = str(au["product_id"])
        array = arrays_by_pid.get(pid)
        if array is None:
            raise ValueError(f"Missing frozen AU SKU array for {pid}")
        desc = au.get("description", {})
        product_cases = cases_by_dossier.get(dossier_id, [])
        if not product_cases:
            continue
        seed_task = task_by_id[product_cases[0]["case_id"]]
        seed_candidates = {x["row_key"]: x for x in seed_task["au_candidates"]}
        seed_signature = {k: json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                          for k, v in seed_candidates.items()}
        for case in product_cases[1:]:
            current = {x["row_key"]: x for x in task_by_id[case["case_id"]]["au_candidates"]}
            if set(current) != set(seed_signature) or any(
                    json.dumps(current[k], ensure_ascii=False, sort_keys=True, separators=(",", ":")) != sig
                    for k, sig in seed_signature.items()):
                raise ValueError(f"Candidate source entities vary across cases for fixed product {pid}")
        au_sku_rows = []
        for cell in array.get("au_sku_cells_raw", []):
            key = real.au_row_key(pid, cell)
            candidate = seed_candidates.get(key)
            if candidate is None:
                raise ValueError(f"Current v10 task lost real AU row {key}")
            au_sku_rows.append({"row_key": key, "axes": _au_axes(cell, array, key),
                                "current_entity": _safe_entity(candidate, evidence)})
        if set(seed_candidates) != {x["row_key"] for x in au_sku_rows}:
            raise ValueError(f"AU SKU row pool differs from original array for {pid}")
        title_source = au.get("source", {})
        raw_rel = title_source.get("raw_file")
        raw_path = (ROOT / raw_rel).resolve() if raw_rel else None
        raw_title = None
        title_json_path = None
        if raw_path and raw_path.is_file():
            raw_doc = json.loads(raw_path.read_text(encoding="utf-8"))
            for candidate_path in ("$.itemInfo.itemTitle", "$.itemInfo.itemName"):
                value = _json_path(raw_doc, candidate_path)
                if isinstance(value, str) and value.strip():
                    raw_title, title_json_path = value, candidate_path
                    break
        if raw_title is None:
            raise ValueError(f"No resolvable AU title leaf for {pid}")
        record = {
            "dossier_id": dossier_id, "product_id": pid,
            "title_raw": raw_title,
            "title_source_ref": {"file": str(raw_path), "sha256": sha256(raw_path),
                                 "product_id": pid, "json_path": title_json_path,
                                 "quote_is_verbatim": raw_title == _json_path(raw_doc, title_json_path),
                                 "derived_semantic_line": au.get("title_raw", "") if au.get("title_raw") != raw_title else None,
                                 "derived_source_ref": {"file": str(args.au_arrays.resolve()),
                                                        "json_path": "$.au_product_title_raw",
                                                        "row_key": pid}},
            "description_source_ref": desc.get("source", {}),
            "description_blocks": [
                {"text": b.get("text", ""), "scope": b.get("scope", ""),
                 "source_field": b.get("source_field"), "source_line": b.get("source_line"),
                 "block_index": i}
                for i, b in enumerate(desc.get("blocks", [])) if str(b.get("text", "")).strip()
            ],
            "au_product_sku_array_source": {"path": str(args.au_arrays.resolve()),
                                             "array_product_id": array.get("au_product_id"),
                                             "array_title": array.get("au_product_title_raw")},
            "au_sku_rows": au_sku_rows,
            "current_page_context": seed_task.get("page_context", {}),
        }
        product_index[dossier_id] = record
        product_rows.append(record)

    rows = []
    for source_case in cases:
        cid = source_case["case_id"]
        dossier = dossiers[source_case["dossier_id"]]
        pid = str(dossier["au_product"]["product_id"])
        array = arrays_by_pid[pid]
        frozen = task_by_id[cid]
        if frozen.get("task_version") != "structured-sku-task-v3-page-enriched":
            raise ValueError(f"Unexpected current task version for {cid}")
        source = source_case.get("rakuten", {}).get("source", {})
        r_entity = _safe_entity(frozen["rakuten"], evidence)
        r_entity["axes"] = _case_axes(source_case)
        rows.append({
            "case_id": cid, "group_id": source_case.get("group_id"),
            "split": source_case.get("split", source_case.get("shard_id")),
            "dossier_id": source_case["dossier_id"], "product_id": pid,
            "gpu196_cohort": cid in gpu_ids,
            "rakuten_product_title_raw": dossier["rakuten_product"].get("title_raw", ""),
            "rakuten_title_source_ref": dossier["rakuten_product"].get("source", {}),
            "rakuten_sku_source": source,
            "rakuten": r_entity,
            "fixed_au_product_ref": {"dossier_id": source_case["dossier_id"], "product_id": pid},
            "au_sku_row_count": len(product_index[source_case["dossier_id"]]["au_sku_rows"]),
            "current_page_context": frozen.get("page_context", {}),
        })
    source_post = {str(p.resolve()): sha256(p) for p in paths}
    if source_pre != source_post:
        raise RuntimeError("Source material changed during preparation")

    args.output.mkdir(parents=True, exist_ok=False)
    write_jsonl(args.output / "tasks.jsonl", rows)
    write_jsonl(args.output / "products.jsonl", product_rows)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "task_schema": "cpu-residual-axis-v1", "case_count": len(rows),
        "gpu196_case_count": sum(r["gpu196_cohort"] for r in rows),
        "unique_product_count": len(product_rows),
        "au_candidate_row_reference_count": sum(r["au_sku_row_count"] for r in rows),
        "unique_au_sku_row_count": sum(len(p["au_sku_rows"]) for p in product_rows),
        "source_sha256_pre": source_pre, "source_sha256_post": source_post,
        "tasks_sha256": sha256(args.output / "tasks.jsonl"),
        "products_sha256": sha256(args.output / "products.jsonl"),
        "prepare_code_sha256": sha256(Path(__file__)),
        "current_v10_task_sha256": sha256(v10_tasks),
        "labels_read": False, "predictions_read": False, "gold_read": False,
        "synthetic_data_included": False,
        "notes": [
            "Original case_id and split retained; GPU 196 cohort is a case_id subset of the 1,383 source cases.",
            "Current v10 attrs, unknowns, conflicts and cited evidence are retained alongside original selected axis names/values.",
            "All real AU SKU rows are retained; title and description citations are held once per fixed product.",
            "No labels, predictions, gold, prices, inventory or routing decisions were used to prepare tasks."
        ],
    }
    with (args.output / "manifest.json").open("x", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2); f.write("\n")
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    p.add_argument("--au-arrays", type=Path, default=DEFAULT_AU_ARRAYS)
    p.add_argument("--v10", type=Path, default=DEFAULT_V10)
    p.add_argument("--gpu-inputs", type=Path, default=DEFAULT_GPU_INPUTS)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args()
    print(json.dumps(prepare(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
