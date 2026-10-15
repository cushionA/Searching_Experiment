#!/usr/bin/env python3
"""Prepare a label-blind full DEV population for classical SKU parsing.

This builder deliberately never reads description text or evaluation labels.
It emits only fixed-document metadata and whole-axis condition inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / ".lab-output/sku-classical-parser-20261015-v1/source-dev/population/run/inputs"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-classical-parser-20261015-v1/population-inputs"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(path: Path):
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if line.strip():
                yield line_no, json.loads(line)


def write_jsonl(path: Path, values: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for value in values:
            f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def axis_text(axes: list[dict]) -> str:
    return "\n".join(f"{a.get('axis_name', '')}：{a.get('value', '')}" for a in axes)


def span_identity(span: Any) -> tuple:
    if not isinstance(span, dict):
        return ("missing",)
    loc = canonical(span.get("locator"))
    return (span.get("raw_file"), span.get("sha256"), span.get("start"), span.get("end"),
            span.get("quote"), loc)


def safe_source_version(case: dict) -> str:
    # Provenance only: use the filename token and never open the referenced file.
    name = Path(str(case.get("source_record", {}).get("file", ""))).name
    for version in ("eval-v2", "eval-v3"):
        if version in name or version in str(case.get("source_record", {}).get("file", "")):
            return version.removeprefix("eval-")
    return "unknown"


def document(product: dict, side: str, source_product_sha: str) -> dict:
    if side == "au":
        meta = product["au_product"]
        raw_sha = meta.get("sha256")
        title = meta.get("title") or ""
        product_id = meta.get("product_id")
        title_refs = [meta.get("title_span")] if meta.get("title_span") else []
    else:
        meta = product["rakuten_product"]
        raw_sha = meta.get("sha256")
        title = meta.get("title") or ""
        product_id = meta.get("product_id")
        title_refs = [meta.get("title_span")] if meta.get("title_span") else []
    if not isinstance(title, str):
        title = ""
    semantic = {"document_id": f"fixed-{side}:{product['dossier_id']}", "side": side,
                "dossier_id": product["dossier_id"], "title": title, "raw_sha256": raw_sha,
                "product_id": product_id, "source_product_sha256": source_product_sha}
    return {**semantic, "document_sha256": digest(semantic), "fixed_page": True,
            "title_source_refs": title_refs, "scope_applicability_proved": False,
            "description": {"complete": False, "text": "", "segments": [],
                            "offset_unit": "unicode_characters", "omitted_by_design": True}}


def build(source: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    case_path, product_path = source / "cases.jsonl", source / "products.jsonl"
    source_hashes = {"cases.jsonl": file_sha(case_path), "products.jsonl": file_sha(product_path)}

    cases_by_id = {}
    case_hashes = {}
    case_count = 0
    for line_no, case in rows(case_path):
        case_count += 1
        cid = case["case_id"]
        if cid in cases_by_id:
            raise ValueError(f"duplicate case_id at line {line_no}: {cid}")
        cases_by_id[cid] = case
        case_hashes[cid] = hashlib.sha256(canonical(case)).hexdigest()

    products = {}
    product_hashes = {}
    source_row_count = 0
    for line_no, product in rows(product_path):
        did = product["dossier_id"]
        if did in products:
            raise ValueError(f"duplicate dossier_id at line {line_no}: {did}")
        products[did] = product
        product_hashes[did] = hashlib.sha256(canonical(product)).hexdigest()
        source_row_count += len(product.get("au_rows", []))

    docs: dict[str, dict] = {}
    reqs: dict[str, dict] = {}
    tasks: list[dict] = []
    row_plans: list[dict] = []
    case_plans: list[dict] = []
    request_to_tasks: dict[str, list[str]] = defaultdict(list)
    counters = defaultdict(int)

    for cid, case in cases_by_id.items():
        did = case["dossier_id"]
        if did not in products:
            raise ValueError(f"case product missing: {cid}/{did}")
        product = products[did]
        if str(case["au_product_id"]) != str(product["au_product"].get("product_id")):
            raise ValueError(f"AU product binding mismatch: {cid}")
        rak_axes = case.get("rakuten_selected", {}).get("axes", [])
        au_rows = product.get("au_rows", [])
        if not rak_axes or not au_rows:
            raise ValueError(f"missing selected axes or AU rows: {cid}")

        for side in ("au", "rakuten"):
            docid = f"fixed-{side}:{did}"
            docs.setdefault(docid, document(product, side, product_hashes[did]))

        # Native AU family: same position AND axis name across all source rows.
        # Exact repeated source locator+quote is one option. Same value from a
        # different locator remains a duplicate and marks the family ambiguous.
        au_families: dict[tuple[int, str], dict] = {}
        for row in au_rows:
            for pos, axis in enumerate(row.get("axes", [])):
                key = (pos, axis.get("axis_name", ""))
                family = au_families.setdefault(key, {"values": [], "identities": [], "unknown": False})
                value = axis.get("value")
                ident = span_identity(axis.get("value_span"))
                if not isinstance(value, str) or not value or ident == ("missing",):
                    family["unknown"] = True
                    family["values"].append(value)
                    family["identities"].append(ident)
                    continue
                # Dedup only exact value+underlying locator identity.
                if not any(oldv == value and oldi == ident for oldv, oldi in zip(family["values"], family["identities"])):
                    family["values"].append(value)
                    family["identities"].append(ident)
        for row in au_rows:
            for pos, axis in enumerate(row.get("axes", [])):
                matching = [k for k in au_families if k[0] == pos]
                if len(matching) != 1 or (pos, axis.get("axis_name", "")) not in au_families:
                    for key in matching:
                        au_families[key]["unknown"] = True
        for key, family in au_families.items():
            pos, axis_name = key
            if any(pos >= len(row.get("axes", [])) or
                   row["axes"][pos].get("axis_name", "") != axis_name for row in au_rows):
                family["unknown"] = True

        target_axes = [{"axis_name": a.get("axis_label"), "value": a.get("value")} for a in rak_axes]
        for row_index, row in enumerate(au_rows):
            bindings = []
            au_row_axes = row.get("axes", [])
            au_target = [{"axis_name": a.get("axis_name"), "value": a.get("value")} for a in au_row_axes]

            def add_condition(direction: str, axis_name: str, value: Any, options: list,
                              axis_key: str, source_axis: dict, actual_target_axes: list,
                              family_status: str) -> None:
                target_side = "au" if direction == "rakuten_to_au" else "rakuten"
                doc = docs[f"fixed-{target_side}:{did}"]
                core = {"direction": direction, "axis_name": axis_name, "option_values": options,
                        "selected_value": value, "target_document_id": doc["document_id"],
                        "target_selected_axes": actual_target_axes}
                rh = digest(core)
                rid = "classical-condition:" + rh
                request = {"request_id": rid, "request_sha256": rh, **core,
                           "target_document_sha256": doc["document_sha256"],
                           "target_selected_axes_text": axis_text(actual_target_axes),
                           "selected_value_is_binding_only": True, "applicability_required": True,
                           "production_adoption_allowed": False, "strict_typed_proof": False,
                           "source_family_status": family_status}
                reqs.setdefault(rid, request)
                identity = {"case_id": cid, "au_row_key": row["row_key"],
                            "direction": direction, "axis_key": axis_key}
                tid = "classical-task:" + digest(identity)
                spans = []
                for k in ("axis_label_span", "axis_name_span", "value_span"):
                    if source_axis.get(k):
                        spans.append(source_axis[k])
                rawref = {"axis_key": axis_key, "axis_name": axis_name, "value": value,
                          "axis_label": source_axis.get("axis_label"),
                          "axis_label_span": source_axis.get("axis_label_span"),
                          "axis_name_span": source_axis.get("axis_name_span"),
                          "value_span": source_axis.get("value_span")}
                task = {"task_id": tid, **identity, "request_id": rid, "dossier_id": did,
                        "au_product_id": case["au_product_id"], "raw_condition_ref": rawref,
                        "provenance": {"source_version": safe_source_version(case),
                            "case_source_sha256": case_hashes[cid], "product_source_sha256": product_hashes[did],
                            "row_array_source": row.get("array_source"), "row_key": row["row_key"],
                            "source_condition_spans": spans},
                        "origin": "full_raw_condition_unaligned", "status": "not_evaluated",
                        "verification_required": True, "strict_core_residual_proof": False,
                        "strict_typed_proof": False, "production_adoption_allowed": False,
                        "source_family_status": family_status}
                tasks.append(task)
                bindings.append({"task_id": tid, "request_id": rid, "direction": direction,
                                 "axis_key": axis_key})
                request_to_tasks[rid].append(tid)
                counters[direction] += 1

            # Rakuten -> AU for every selected Rakuten whole axis.
            for ra in rak_axes:
                values = ra.get("family_values")
                complete = isinstance(values, list) and bool(values) and ra.get("value") in values
                family_status = "complete" if complete else "unknown"
                options = list(values) if isinstance(values, list) else []
                add_condition("rakuten_to_au", str(ra.get("axis_label", "")), ra.get("value"),
                              options, str(ra.get("axis_key", f"rakuten-axis:{ra.get('axis_index')}")),
                              ra, au_target, family_status)

            # AU -> Rakuten for every original row axis, full family by native locator.
            for pos, aa in enumerate(au_row_axes):
                key = (pos, aa.get("axis_name", ""))
                fam = au_families.get(key, {"values": [], "unknown": True})
                values = list(fam["values"])
                ambiguous = any(v1 == v2 for i, v1 in enumerate(values) for v2 in values[i + 1:])
                complete = bool(values) and aa.get("value") in values and not fam.get("unknown")
                status = "unknown" if (not complete or ambiguous) else "complete"
                add_condition("au_to_rakuten", str(aa.get("axis_name", "")), aa.get("value"),
                              values, f"au-axis-index:{pos}", aa, target_axes, status)

            row_plans.append({"case_id": cid, "source_version": safe_source_version(case), "split": "dev",
                "dossier_id": did, "au_product_id": case["au_product_id"], "au_row_key": row["row_key"],
                "au_row_index": row_index, "au_row_sku_id": row.get("sku_id"),
                "au_axis_count": len(au_row_axes), "rakuten_axis_count": len(rak_axes),
                "au_row_axis_source_spans": [a.get("value_span") for a in au_row_axes],
                "bindings": bindings, "row_retained_regardless_of_stock": True,
                "native_au_family_complete": all(
                    au_families.get((i, a.get("axis_name", "")), {}).get("unknown") is False
                    for i, a in enumerate(au_row_axes)), "strict_typed_proof_count": 0})
            counters["row_plans"] += 1

        raw_case_sha = case.get("rakuten_selected", {}).get("sha256")
        case_plans.append({"case_id": cid, "source_version": safe_source_version(case), "split": "dev",
            "dossier_id": did, "au_product_id": case["au_product_id"],
            "source_sku_key": case.get("rakuten_selected", {}).get("sku_key"),
            "sku_record_key": case.get("rakuten_selected", {}).get("sku_record_key"),
            "variant_id": case.get("rakuten_selected", {}).get("variant_id"),
            "raw_selected_sku_source_sha256": raw_case_sha,
            "total_au_rows": len(au_rows), "rakuten_axis_count": len(rak_axes),
            "total_task_bindings": sum(len(x["bindings"]) for x in row_plans if x["case_id"] == cid),
            "row_keys": [r["row_key"] for r in au_rows], "raw_au_row_keys_count": len(au_rows),
            "all_original_rakuten_axes_covered": True, "all_original_au_rows_covered": True,
            "all_original_au_axes_covered": True, "strict_core_residual_proof_used": False,
            "typed_proof_used": False})
        counters["case_plans"] += 1

    output.mkdir(parents=True)
    names = {"documents": "documents.jsonl", "requests": "requests.jsonl", "tasks": "tasks.jsonl",
             "row_plans": "row-plans.jsonl", "case_plans": "case-plans.jsonl"}
    values = {"documents": [docs[k] for k in sorted(docs)],
              "requests": list(reqs.values()), "tasks": tasks,
              "row_plans": row_plans, "case_plans": case_plans}
    for key, name in names.items():
        write_jsonl(output / name, values[key])
    manifest = {"schema_version": "classical-full-population-inputs-v1", "split": "dev",
        "phase": "label_blind_population_prepare", "source_inputs": source_hashes,
        "preparer_code_sha256": file_sha(Path(__file__)), "labels_read": False,
        "model_inference_run": False, "descriptions_read": False, "network_used": False,
        "test_data_read": False, "untrusted_trace_read": False, "evaluation_annotations_read": False,
        "case_count": len(case_plans), "product_count": len(products),
        "source_au_row_population_count": source_row_count,
        "expanded_case_au_row_coverage_count": len(row_plans),
        "row_plan_count": len(row_plans), "task_binding_count": len(tasks),
        "unique_request_count": len(reqs), "document_count": len(docs),
        "counts_by_direction": {k: counters[k] for k in ("rakuten_to_au", "au_to_rakuten")},
        "output_sha256": {name: file_sha(output / name) for name in names.values()},
        "all_original_au_rows_including_stock_states_retained": True,
        "all_original_axis_tasks_preserved": True,
        "native_au_family_source": "all source AU rows in the same dossier, same axis position and exact axis name; source row order retained",
        "native_au_family_deduplication": "deduplicate only identical value plus identical underlying value-span locator; preserve same-value different-locator duplicates and mark family unknown",
        "unknown_axis_or_family_policy": "preserve raw task and source spans; mark family status unknown; never infer a substitute axis",
        "request_deduplication_core": ["direction", "axis_name", "option_values", "selected_value", "target_document_id", "target_selected_axes"],
        "no_gold_or_model_data_in_condition_construction": True}
    write_json(output / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.source, args.output)
    print(json.dumps({k: result[k] for k in ("case_count", "product_count", "source_au_row_population_count",
        "row_plan_count", "task_binding_count", "unique_request_count", "document_count", "output_sha256")},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
