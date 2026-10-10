#!/usr/bin/env python3
"""Build a label-blind evidence-card input package from the frozen 196-case cohort.

Only the allowlisted v2 inputs, v3 source dossiers, and v10 product/source map are
read. No annotation, gold, or prediction files are opened.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any

MODEL = {"name": "Qwen/Qwen3.5-4B", "revision": "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a", "load": "nf4"}
DEFAULT_INPUTS = ".lab-output/sku-kaggle-gpu-20261010-v2/dataset-upload/inputs.jsonl"
DEFAULT_INPUT_MANIFEST = ".lab-output/sku-kaggle-gpu-20261010-v2/dataset-upload/manifest.json"
DEFAULT_PRODUCTS = ".lab-output/sku-cpu-residual-task-20261010-v10/products.jsonl"
DEFAULT_DOSSIERS = ".lab-output/sku-real-luna-annotation-inputs-20261010-v3/dossiers"
EXPECTED_FULL_COUNT = 196


@lru_cache(maxsize=256)
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{n}: expected an object")
            rows.append(row)
    return rows


def row_sku(axes: list[dict[str, Any]]) -> str:
    """Serialize raw axes exactly as the frozen 196-case input serializer did."""
    parts = []
    for axis in axes:
        name = str(axis.get("axis_name_raw") or "").strip()
        value = str(axis.get("value_raw") or "").strip()
        if name and value:
            parts.append(f"{name}={value}")
        elif value:
            parts.append(value)
    return " / ".join(parts)


def json_path_value(value: Any, json_path: str) -> Any:
    if not json_path.startswith("$."):
        raise ValueError(f"unsupported source JSONPath: {json_path}")
    cur = value
    for component in json_path[2:].split("."):
        if not isinstance(cur, dict) or component not in cur:
            raise ValueError(f"source JSONPath not found: {json_path}")
        cur = cur[component]
    return cur


def resolve_path(root: Path, path_value: str) -> Path:
    p = Path(path_value)
    return p.resolve() if p.is_absolute() else (root / p).resolve()


def add_evidence(registry: list[dict[str, Any]], source_texts: dict[str, list[str]], *,
                 eid: str, side: str, field: str, scope: str, quote: str,
                 source_ref: dict[str, Any], row_key: str | None = None) -> None:
    if not isinstance(quote, str) or not quote:
        return
    item: dict[str, Any] = {"id": eid, "side": side, "field": field, "scope": scope,
                            "quote": quote, "source_ref": source_ref}
    if row_key is not None:
        item["row_key"] = row_key
    registry.append(item)
    source_texts[side].append(quote)


def verify_raw_au_option(root: Path, source_grain: dict[str, Any], product_id: str,
                         axes: list[dict[str, Any]], row_cache: dict[tuple[str, int], dict[str, Any]]) -> str:
    rel_file = str(source_grain.get("file") or "")
    line_no = source_grain.get("line")
    if not rel_file or not isinstance(line_no, int) or line_no < 1:
        raise ValueError("AU row source grain lacks raw file/line provenance")
    cache_key = (rel_file, line_no)
    raw = row_cache.get(cache_key)
    if raw is None:
        path = resolve_path(root, rel_file)
        with path.open(encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if n == line_no:
                    raw = json.loads(line)
                    break
        if raw is None:
            raise ValueError(f"AU row source line not found: {rel_file}:{line_no}")
        row_cache[cache_key] = raw
    if (str(raw.get("item_id")) != product_id
            or str(raw.get("sku_id")) != str(source_grain.get("sku_id"))
            or raw.get("row_index") != source_grain.get("row_index")
            or raw.get("column_index") != source_grain.get("column_index")):
        raise ValueError(f"AU raw option provenance mismatch at {rel_file}:{line_no}")
    raw_axes = [
        {"axis_name_raw": str(raw.get("row_option_name") or ""), "value_raw": str(raw.get("row_option_value") or "")},
        {"axis_name_raw": str(raw.get("column_option_name") or ""), "value_raw": str(raw.get("column_option_value") or "")},
    ]
    if row_sku(axes) != row_sku(raw_axes):
        raise ValueError(f"AU raw option axes mismatch at {rel_file}:{line_no}")
    return rel_file


def make_case(root: Path, case: dict[str, Any], product: dict[str, Any], dossier: dict[str, Any],
              frozen_input_sha: str, hash_sources: dict[str, str],
              raw_au_row_cache: dict[tuple[str, int], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id, dossier_id = case["case_id"], case["dossier_id"]
    if dossier.get("dossier_id") != dossier_id or product.get("dossier_id") != dossier_id:
        raise ValueError(f"{case_id}: dossier/product id mismatch")
    au_product, r_product = dossier["au_product"], dossier["rakuten_product"]
    product_id = str(product["product_id"])
    if str(au_product.get("product_id")) != product_id:
        raise ValueError(f"{case_id}: AU product id disagrees with v10 product map")
    pair_ref = str(dossier.get("pair_ref", ""))
    if not pair_ref.startswith(f"au:{product_id}|"):
        raise ValueError(f"{case_id}: dossier pair_ref does not point to the fixed AU product")
    array_source = product.get("au_product_sku_array_source", {})
    array_source_path = str(array_source.get("path") or "")
    if not array_source_path:
        raise ValueError(f"{case_id}: source AU SKU array reference is missing")
    array_source_sha = sha256_file(resolve_path(root, array_source_path))
    hash_sources[array_source_path] = array_source_sha

    # Preserve and verify the original full AU option order and each selected axis string.
    original_rows = case.get("au", {}).get("sku_rows")
    dossier_rows = dossier.get("au_rows")
    if not isinstance(original_rows, list) or not original_rows or not isinstance(dossier_rows, list):
        raise ValueError(f"{case_id}: missing full AU SKU pool")
    if len(original_rows) != len(dossier_rows):
        raise ValueError(f"{case_id}: candidate pool size changed")
    normalized_axes: list[tuple[str, str, dict[str, Any]]] = []
    for source_row, frozen_row in zip(dossier_rows, original_rows):
        key = source_row.get("row_key")
        raw_sku = row_sku(source_row.get("axes_raw", []))
        if key != frozen_row.get("row_key") or raw_sku != frozen_row.get("sku"):
            raise ValueError(f"{case_id}: AU SKU values or candidate ordering differ from frozen v2 inputs")
        if source_row.get("source_grain", {}).get("item_id") not in (None, product_id):
            raise ValueError(f"{case_id}: AU option row source points at a different item")
        source_grain = source_row.get("source_grain", {})
        if source_grain.get("file"):
            raw_source_file = verify_raw_au_option(root, source_grain, product_id,
                source_row.get("axes_raw", []), raw_au_row_cache)
            raw_path = resolve_path(root, raw_source_file)
            hash_sources[raw_source_file] = sha256_file(raw_path)
        normalized_axes.append((key, raw_sku, source_grain))

    # The AU title is resolved from the raw API leaf via the v10 source map, not a semantic derivative.
    title_ref = product["title_source_ref"]
    au_raw_path = resolve_path(root, title_ref["file"])
    au_raw_sha = sha256_file(au_raw_path)
    if au_raw_sha != title_ref["sha256"]:
        raise ValueError(f"{case_id}: AU title source hash mismatch")
    au_raw_json = json.loads(au_raw_path.read_text(encoding="utf-8"))
    au_title = str(product["title_raw"])
    json_path = str(title_ref["json_path"])
    if json_path_value(au_raw_json, json_path) != au_title:
        raise ValueError(f"{case_id}: AU title is not the raw API title leaf")
    hash_sources[str(title_ref["file"])] = au_raw_sha

    registry: list[dict[str, Any]] = []
    source_texts: dict[str, list[str]] = {"rakuten": [], "au": []}
    add_evidence(registry, source_texts, eid="at", side="au", field="title", scope="fixed_page_title",
        quote=au_title, source_ref={"raw_file": title_ref["file"], "raw_sha256": au_raw_sha,
            "raw_json_path": json_path, "quote_verbatim": True, "product_id": product_id})

    raw_r_title = r_product.get("title_evidence_raw", {}).get("text")
    if not isinstance(raw_r_title, str) or not raw_r_title:
        raise ValueError(f"{case_id}: raw Rakuten title evidence is missing")
    r_source = r_product.get("source", {})
    r_raw_path = resolve_path(root, str(r_source["raw_file"]))
    r_raw_sha = sha256_file(r_raw_path)
    if r_raw_sha != r_source.get("sha256"):
        raise ValueError(f"{case_id}: Rakuten title source hash mismatch")
    hash_sources[str(r_source["raw_file"])] = r_raw_sha
    add_evidence(registry, source_texts, eid="rt", side="rakuten", field="title", scope="product_title",
        quote=raw_r_title, source_ref={"raw_file": r_source.get("raw_file"), "raw_sha256": r_raw_sha,
            "raw_json_path": r_source.get("json_path"), "page_url": r_source.get("url"),
            "quote_verbatim": True, "use": "title_evidence_raw"})

    selected_sku = str(case.get("rakuten", {}).get("sku") or "")
    add_evidence(registry, source_texts, eid="rs", side="rakuten", field="selected_sku", scope="selected_option",
        quote=selected_sku, source_ref={"derived": True, "transformation": "verbatim selected axis/value from frozen label-blind v2 input",
            "input_file_sha256": frozen_input_sha, "input_case_id": case_id})

    # Preserve every AU description block and its original scope, source field, and line number.
    au_desc = au_product.get("description", {})
    au_desc_source = au_desc.get("source", {})
    if au_desc_source.get("sha256"):
        hash_sources[str(au_desc_source.get("raw_file"))] = str(au_desc_source["sha256"])
    for block_index, block in enumerate(au_desc.get("blocks", [])):
        field_name = str(block.get("source_field") or "unknown")
        line = block.get("source_line")
        add_evidence(registry, source_texts, eid=f"ad{block_index + 1:04d}", side="au", field="description",
            scope=str(block.get("scope") or "unknown"), quote=str(block.get("text") or ""),
            source_ref={"raw_file": au_desc_source.get("raw_file"), "raw_sha256": au_desc_source.get("sha256"),
                "raw_json_path": field_name, "source_line": line, "block_index": block_index,
                "transformation": "prepared AU source block; exact block text, not raw HTML"})

    # Rakuten description lines are derived from a prepared extracted excerpt. Keep every nonblank line,
    # preserve original line ordinals and explicitly describe the extraction transform.
    r_desc = r_product.get("description", {})
    r_desc_source = r_desc.get("source", {})
    if r_desc_source.get("sha256"):
        hash_sources[str(r_desc_source.get("raw_file"))] = str(r_desc_source["sha256"])
    excerpt = str(r_desc.get("individual_description_excerpt") or "")
    for line_index, line in enumerate(excerpt.splitlines(), 1):
        if line.strip():
            add_evidence(registry, source_texts, eid=f"rd{line_index:04d}", side="rakuten", field="description",
                scope="individual_description_excerpt", quote=line,
                source_ref={"raw_file": r_desc_source.get("raw_file"), "raw_sha256": r_desc_source.get("sha256"),
                    "page_url": r_desc_source.get("page_url"), "source_line": line_index,
                    "transformation": "exact nonblank line of prepared extracted description excerpt; not raw HTML"})

    pool: list[dict[str, str]] = []
    for index, (row_key, sku, grain) in enumerate(normalized_axes):
        alias = f"a{index:03d}"
        pool.append({"alias": alias, "row_key": row_key, "sku": sku})
        source_grain_path = str(grain.get("file") or "")
        source_ref: dict[str, Any] = {"derived": True,
            "transformation": "raw AU option axes serialized as exact axis=value sequence; compound values are not split",
            "input_file_sha256": frozen_input_sha, "input_case_id": case_id, "row_key": row_key}
        if source_grain_path:
            source_ref.update({"raw_file": source_grain_path, "source_line": grain.get("line"),
                "row_index": grain.get("row_index"), "column_index": grain.get("column_index"),
                "sku_id": grain.get("sku_id")})
        add_evidence(registry, source_texts, eid=alias, side="au", field="selected_option", scope="sku_row",
            quote=sku, source_ref=source_ref, row_key=row_key)

    source_texts["au"] = list(dict.fromkeys(source_texts["au"]))
    source_texts["rakuten"] = list(dict.fromkeys(source_texts["rakuten"]))
    prepared = {"case_id": case_id, "dossier_id": dossier_id, "split": case.get("split"),
        "source_category": case.get("source_category"), "rakuten": {"sku": selected_sku},
        "au": {"sku_rows": pool}, "evidence_registry": registry, "source_texts": source_texts}
    fixed_au_url = f"https://wowma.jp/item/{product_id}"
    r_url = str(r_source.get("url") or r_desc_source.get("page_url") or "")
    if not r_url:
        raise ValueError(f"{case_id}: fixed Rakuten page URL is missing")
    fixed_sources = {"pair_ref": pair_ref, "au_product_id": product_id,
        "au_url": fixed_au_url, "au_url_source": "derived from source-backed AU API item id; canonical public item path",
        "rakuten_url": r_url, "rakuten_url_source_ref": {"raw_file": r_source.get("raw_file"),
            "raw_sha256": r_raw_sha, "page_url": r_url},
        "full_au_row_count": len(pool), "au_row_key_order": [r["row_key"] for r in pool],
        "selected_rakuten_sku": selected_sku}
    source_case = {"case_id": case_id, "dossier_id": dossier_id, "fixed_sources": fixed_sources,
        "source_evidence_registry": registry,
        "au_sku_array_source_ref": {"file": array_source_path, "sha256": array_source_sha,
            "product_id": array_source.get("array_product_id"), "json_path": "$.au_product_sku_array"},
        "source_hashes": {k: hash_sources[k] for k in sorted(hash_sources)}}
    return prepared, source_case


def build(*, root: Path, output_dir: Path, inputs_path: Path, input_manifest_path: Path,
          products_path: Path, dossiers_dir: Path, limit: int | None = None,
          case_ids: list[str] | None = None) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output directory: {output_dir}")
    original = jsonl(inputs_path)
    if len(original) != EXPECTED_FULL_COUNT:
        raise ValueError(f"expected frozen 196-case source cohort; got {len(original)}")
    original_ids = [c.get("case_id") for c in original]
    if any(not isinstance(x, str) or not x for x in original_ids) or len(set(original_ids)) != len(original_ids):
        raise ValueError("frozen cohort case IDs must be unique nonempty strings")
    input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    frozen_input_sha = sha256_file(inputs_path)
    if frozen_input_sha != input_manifest.get("inputs_sha256") or input_manifest.get("input_count") != EXPECTED_FULL_COUNT:
        raise ValueError("v2 frozen input manifest hash/count mismatch")

    products_rows = jsonl(products_path)
    products = {x.get("dossier_id"): x for x in products_rows}
    if len(products) != len(products_rows):
        raise ValueError("duplicate dossier_id in source product map")
    if case_ids is not None and limit is not None:
        raise ValueError("choose at most one of --limit or --case-id-file")
    if case_ids is not None:
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case selection repeats an ID")
        unknown = set(case_ids) - set(original_ids)
        if unknown:
            raise ValueError(f"unknown case IDs: {sorted(unknown)}")
        wanted = set(case_ids)
        selected = [c for c in original if c["case_id"] in wanted]
        if len(selected) != len(case_ids):
            raise ValueError("requested case selection did not resolve exactly")
    elif limit is not None:
        if limit < 1 or limit > EXPECTED_FULL_COUNT:
            raise ValueError("--limit must be between 1 and 196")
        selected = original[:limit]
    else:
        selected = original

    # Full-corpus mode asserts the exact ordered 196 identities; filtered mode is an ordered subset.
    if case_ids is None and limit is None and [c["case_id"] for c in selected] != original_ids:
        raise AssertionError("full 196-case order changed")
    output_dir.mkdir(parents=True, exist_ok=True)
    input_rows, source_rows = [], []
    dossier_hashes: dict[str, str] = {}
    raw_hashes: dict[str, str] = {}
    raw_au_row_cache: dict[tuple[str, int], dict[str, Any]] = {}
    for c in selected:
        dossier_path = dossiers_dir / f"{c['dossier_id']}.json"
        if not dossier_path.exists():
            raise FileNotFoundError(f"missing source dossier for {c['case_id']}: {dossier_path}")
        dossier_hashes[str(dossier_path.relative_to(root))] = sha256_file(dossier_path)
        dossier = json.loads(dossier_path.read_text(encoding="utf-8"))
        product = products.get(c["dossier_id"])
        if product is None:
            raise ValueError(f"no v10 product source record for {c['dossier_id']}")
        case_raw_hashes: dict[str, str] = {}
        prepared, source_case = make_case(root, c, product, dossier, frozen_input_sha, case_raw_hashes, raw_au_row_cache)
        raw_hashes.update(case_raw_hashes)
        # Candidate identities and selected Rakuten option are exactly those in the original frozen input.
        if prepared["rakuten"]["sku"] != c["rakuten"].get("sku", ""):
            raise AssertionError(f"{c['case_id']}: selected Rakuten SKU changed")
        if [r["row_key"] for r in prepared["au"]["sku_rows"]] != [r["row_key"] for r in c["au"]["sku_rows"]]:
            raise AssertionError(f"{c['case_id']}: full AU candidate order changed")
        if [r["sku"] for r in prepared["au"]["sku_rows"]] != [r["sku"] for r in c["au"]["sku_rows"]]:
            raise AssertionError(f"{c['case_id']}: AU candidate values changed")
        input_rows.append(prepared)
        source_rows.append(source_case)

    (output_dir / "inputs.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in input_rows), encoding="utf-8")
    (output_dir / "source-cases.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in source_rows), encoding="utf-8")
    config = {"models": [MODEL], "max_input_tokens": 12000, "max_new_tokens": 256, "batch_size": 1,
        "runtime_budget_seconds": 3600, "enable_thinking": False, "attention_implementation": "sdpa",
        "logits_to_keep": 1, "reason_char_range_japanese": [1, 80], "runner_version": "v8-compatible",
        "cohort_selection": {"kind": "full_frozen_196" if len(selected) == EXPECTED_FULL_COUNT else "label_blind_subset",
            "case_count": len(selected), "selection_order": "original v2 input order"}}
    (output_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    case_ids_payload = "\n".join(c["case_id"] for c in selected).encode("utf-8")
    unique_products = sorted({str(products[c["dossier_id"]]["product_id"]) for c in selected})
    manifest: dict[str, Any] = {"schema_version": "sku-gpu-evidence-input-v8-builder-v1",
        "label_blind": True, "labels_read": False, "source_case_count": EXPECTED_FULL_COUNT,
        "input_count": len(input_rows), "case_ids_sha256": hashlib.sha256(case_ids_payload).hexdigest(),
        "inputs_sha256": sha256_file(output_dir / "inputs.jsonl"), "inputs_bytes": (output_dir / "inputs.jsonl").stat().st_size,
        "source_cases_sha256": sha256_file(output_dir / "source-cases.jsonl"),
        "config_sha256": sha256_file(output_dir / "config.json"),
        "source_sha256": {"frozen_v2_inputs": frozen_input_sha,
            "frozen_v2_input_manifest": sha256_file(input_manifest_path),
            "v10_products": sha256_file(products_path),
            "v3_dossiers": dossier_hashes, "raw_sources": raw_hashes},
        "fixed_au_products": unique_products, "all_selected_skus_preserved": True,
        "all_au_candidate_rows_preserved_in_order": True,
        "labels_prices_stock_in_model_input": False,
        "evidence_id_rule": "rt raw Rakuten title; at raw AU title leaf; rs selected Rakuten SKU from frozen input; rd#### original nonblank excerpt line ordinal; ad#### original AU block index+1; a### case-local AU row alias and exact selected-option evidence.",
        "description_rule": "all AU source blocks unchanged with scope/source field/source line; all nonblank Rakuten extracted excerpt lines unchanged and line-indexed; Rakuten excerpt is derived extracted text, not raw HTML.",
        "row_alias_rule": "case-local a000... follows original frozen AU row order; registry row_key and quote must equal the source candidate row.",
        "records": [{"case_id": c["case_id"], "dossier_id": c["dossier_id"],
            "au_product_id": source_rows[i]["fixed_sources"]["au_product_id"],
            "fixed_au_url": source_rows[i]["fixed_sources"]["au_url"],
            "rakuten_url": source_rows[i]["fixed_sources"]["rakuten_url"],
            "selected_rakuten_sku": c["rakuten"]["sku"], "au_rows": len(c["au"]["sku_rows"]),
            "evidence_count": len(c["evidence_registry"])} for i, c in enumerate(input_rows)]}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path(".lab-output/sku-kaggle-gpu-20261010-v9-prepared"))
    parser.add_argument("--inputs", type=Path, default=Path(DEFAULT_INPUTS))
    parser.add_argument("--input-manifest", type=Path, default=Path(DEFAULT_INPUT_MANIFEST))
    parser.add_argument("--products", type=Path, default=Path(DEFAULT_PRODUCTS))
    parser.add_argument("--dossiers", type=Path, default=Path(DEFAULT_DOSSIERS))
    parser.add_argument("--limit", type=int, help="take first N cases in frozen input order")
    parser.add_argument("--case-id-file", type=Path, help="UTF-8 newline-separated explicit case IDs")
    args = parser.parse_args()
    root = args.root.resolve()
    path_arg = lambda p: p.resolve() if p.is_absolute() else (root / p).resolve()
    case_ids = None
    if args.case_id_file:
        case_ids = [line.strip() for line in path_arg(args.case_id_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    result = build(root=root, output_dir=path_arg(args.output_dir), inputs_path=path_arg(args.inputs),
        input_manifest_path=path_arg(args.input_manifest), products_path=path_arg(args.products),
        dossiers_dir=path_arg(args.dossiers), limit=args.limit, case_ids=case_ids)
    print(json.dumps({"input_count": result["input_count"], "inputs_sha256": result["inputs_sha256"],
        "config_sha256": result["config_sha256"], "case_ids_sha256": result["case_ids_sha256"],
        "labels_read": result["labels_read"]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
