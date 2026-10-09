#!/usr/bin/env python3
"""Audit normalized rows and source provenance in real SKU captures only."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_rakuten

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PARTS = {"sku-synthetic", "sku-fixed-product-20261010"}


def iter_jsonl(path: Path, errors: list[dict[str, Any]]):
    if not path.exists():
        errors.append({"code": "missing_table", "path": str(path)})
        return
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("row is not an object")
                yield row
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append({"code": "invalid_jsonl", "path": str(path), "line": line_no,
                               "error": str(exc)})


def read_jsonl(path: Path, errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(iter_jsonl(path, errors))
    return rows


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def contains_synthetic_marker(value: Any) -> bool:
    if isinstance(value, dict):
        if str(value.get("record_kind", "")).upper() == "SYNTHETIC":
            return True
        return any(contains_synthetic_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_synthetic_marker(v) for v in value)
    return False


def validate_dirs(dirs: Iterable[Path], platform: str) -> list[Path]:
    result = []
    for directory in dirs:
        path = directory if directory.is_absolute() else ROOT / directory
        resolved = path.resolve()
        if any(part.lower() in FORBIDDEN_PARTS or part.lower().startswith("sku-synthetic")
               for part in resolved.parts):
            raise ValueError(f"refusing synthetic/fixed capture path: {directory}")
        if not resolved.is_dir():
            raise ValueError(f"capture directory does not exist: {directory}")
        if not (resolved / "products.jsonl").exists() or not (resolved / "skus.jsonl").exists():
            raise ValueError(f"{platform} capture needs products.jsonl and skus.jsonl: {directory}")
        result.append(resolved)
    return result


def source_table_record(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
            "sha256": sha256_file(path), "rows": len(rows)}


def streamed_table_record(path: Path, rows: int) -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
            "sha256": sha256_file(path), "rows": rows}


def summarize_rakuten_ledger(data: dict[str, Any]) -> dict[str, Any]:
    result = {k: v for k, v in data.items()
              if k not in {"runs", "product_urls_attempted", "successful_product_urls", "catalog_urls_attempted", "request_events",
                           "catalog_urls_successful", "candidate_urls", "successful_urls", "failed_urls"}
              and not isinstance(v, (list, dict))}
    for field in ("product_urls_attempted", "successful_product_urls", "catalog_urls_attempted",
                  "catalog_urls_successful", "candidate_urls", "successful_urls", "failed_urls", "request_events"):
        if isinstance(data.get(field), (list, dict)):
            result[field + "_count"] = len(data[field])
        elif field in data:
            result[field] = data[field]
    attempted = data.get("product_urls_attempted")
    successful = data.get("successful_product_urls")
    if isinstance(attempted, list) and isinstance(successful, list):
        result["product_url_failures_count"] = max(0, len(attempted) - len(successful))
    if isinstance(data.get("runs"), list):
        result["runs"] = [{k: v for k, v in row.items() if k in {
            "output_directory", "requested_product_url_count", "successful_product_page_count",
            "product_get_attempts_including_failures", "redirect_hops", "http_request_count"}}
                           for row in data["runs"]]
    return result


def display_path(path: Path) -> str:
    resolved = path.resolve()
    return resolved.relative_to(ROOT).as_posix() if resolved.is_relative_to(ROOT) else str(resolved)


def audit_au(dirs: list[Path]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    source_tables = []
    raw_integrity = Counter()
    request_ledger = Counter()
    legacy_ledger_total = 0
    legacy_ledger_by_capture: dict[str, dict[str, Any]] = {}
    budget_state_by_capture: dict[str, dict[str, Any]] = {}
    raw_item_data: dict[tuple[str, str], dict[str, Any]] = {}
    budget_attempt_ids: set[str] = set()
    metadata_attempt_refs: set[str] = set()
    budget_attempt_ids_by_capture: dict[str, set[str]] = defaultdict(set)
    metadata_attempt_refs_by_capture: dict[str, set[str]] = defaultdict(set)
    legacy_candidate_rows = 0
    non_sku_candidate_rows = 0
    candidate_raw_item_bodies = 0
    candidate_raw_sha_matches = 0
    candidate_raw_sha_mismatches = 0
    catalog_response_files = 0
    observed_candidate_item_calls = 0
    candidate_rows_by_capture: Counter[str] = Counter()
    candidate_raw_bodies_by_capture: Counter[str] = Counter()
    candidate_item_calls_by_capture: Counter[str] = Counter()
    catalog_files_by_capture: Counter[str] = Counter()
    non_sku_rows_by_capture: Counter[str] = Counter()
    non_sku_raw_bodies_by_capture: Counter[str] = Counter()
    api_timestamp_counts = Counter()
    for directory in dirs:
        ppath, spath = directory / "products.jsonl", directory / "skus.jsonl"
        p = read_jsonl(ppath, errors)
        source_tables.append(source_table_record(ppath, p))
        capture_id = str(directory.resolve())
        products.extend({**row, "_audit_capture": capture_id} for row in p)
        # Verify raw item/options response digests where the collector saved their bytes.
        for product in p:
            item_id = str(product.get("item_id", ""))
            for api_key, suffix in (("item_api", "item"), ("options_api", "options")):
                meta = product.get(api_key) or {}
                api_timestamp_counts["present" if meta.get("retrieved_at_utc") else "missing"] += 1
                for attempt in meta.get("network_attempts", []) or []:
                    if attempt.get("attempt_id"):
                        metadata_attempt_refs.add(str(attempt["attempt_id"]))
                        metadata_attempt_refs_by_capture[capture_id].add(str(attempt["attempt_id"]))
                if meta.get("attempt_id"):
                    metadata_attempt_refs.add(str(meta["attempt_id"]))
                    metadata_attempt_refs_by_capture[capture_id].add(str(meta["attempt_id"]))
                candidates = [directory / f"{item_id}-{suffix}.json",
                              directory / "raw" / f"{item_id}-{suffix}.json"]
                source_raw = product.get("source_raw_files") or []
                if len(source_raw) >= 2:
                    candidates.append(directory / source_raw[0 if suffix == "item" else 1])
                raw_path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
                if not raw_path.exists():
                    raw_integrity["missing"] += 1
                    errors.append({"code": "au_raw_response_missing", "directory": str(directory),
                                   "item_id": item_id, "source": api_key, "expected_path": str(raw_path)})
                    continue
                actual = sha256_file(raw_path)
                if meta.get("sha256") and actual == meta["sha256"]:
                    raw_integrity["sha256_match"] += 1
                    if suffix == "item":
                        try:
                            raw_item_data[(capture_id, item_id)] = json.loads(raw_path.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError) as exc:
                            errors.append({"code": "au_raw_item_json_invalid", "item_id": item_id, "error": str(exc)})
                else:
                    raw_integrity["sha256_mismatch_or_missing_metadata"] += 1
                    errors.append({"code": "au_raw_sha256_mismatch", "directory": str(directory),
                                   "item_id": item_id, "source": api_key,
                                   "expected": meta.get("sha256"), "actual": actual})
        ledger = directory / "http-ledger.jsonl"
        legacy_local_total = 0
        legacy_local_types = Counter()
        if ledger.exists():
            ledger_rows = read_jsonl(ledger, errors)
            source_tables.append(source_table_record(ledger, ledger_rows))
            for row in ledger_rows:
                count = int(row.get("attempts", 1) or 0)
                legacy_local_total += count
                legacy_local_types[str(row.get("request_type", "unknown"))] += count
        legacy_ledger_total += legacy_local_total
        legacy_ledger_by_capture[capture_id] = {"attempts": legacy_local_total,
                                                "attempts_by_request_type": dict(legacy_local_types),
                                                "log_present": ledger.exists()}
        budget_state = directory / "http-ledger-state.json"
        if budget_state.exists():
            try:
                state = json.loads(budget_state.read_text(encoding="utf-8"))
                source_tables.append({"path": budget_state.relative_to(ROOT).as_posix(),
                                      "sha256": sha256_file(budget_state), "rows": len(state.get("attempts", []))})
                outcomes = Counter(str((a.get("result") or {}).get("outcome", "missing_result"))
                                   for a in state.get("attempts", []))
                state_ids = {str(a.get("attempt_id")) for a in state.get("attempts", []) if a.get("attempt_id")}
                budget_attempt_ids.update(state_ids)
                budget_attempt_ids_by_capture[capture_id].update(state_ids)
                state_summary = {
                    "present": True,
                    "max_http": state.get("max_http"),
                    "max_catalog_http": state.get("max_catalog_http"),
                    "charged_attempts": len(state.get("attempts", [])),
                    "attempt_outcomes": dict(outcomes),
                    "attempts_with_http_status": sum(1 for a in state.get("attempts", [])
                                                       if (a.get("result") or {}).get("status") is not None),
                    "attempts_without_http_status": sum(1 for a in state.get("attempts", [])
                                                          if (a.get("result") or {}).get("status") is None),
                    "captured_response_bytes": sum(int((a.get("result") or {}).get("response_bytes_captured", 0))
                                                    for a in state.get("attempts", [])),
                    "charged_by_request_type": dict(Counter(str(a.get("request_type", "unknown"))
                                                              for a in state.get("attempts", []))),
                }
                budget_state_by_capture[capture_id] = state_summary
                request_ledger.update(Counter(str(a.get("request_type", "unknown")) for a in state.get("attempts", [])))
            except (json.JSONDecodeError, OSError, TypeError) as exc:
                errors.append({"code": "invalid_au_budget_state", "path": str(budget_state), "error": str(exc)})
        decisions = directory / "candidate-decisions.jsonl"
        if decisions.exists():
            decision_rows = read_jsonl(decisions, errors)
            legacy_candidate_rows += len(decision_rows)
            candidate_rows_by_capture[capture_id] += len(decision_rows)
            source_tables.append(source_table_record(decisions, decision_rows))
            candidate_raw_item_bodies += sum(1 for row in decision_rows
                                             if (directory / f"{row.get('item_id')}-item.json").exists())
            candidate_raw_bodies_by_capture[capture_id] += sum(
                1 for row in decision_rows if (directory / f"{row.get('item_id')}-item.json").exists())
            observed_candidate_item_calls += sum(1 for row in decision_rows
                                                 if str(row.get("decision", "")).startswith("excluded_"))
            candidate_item_calls_by_capture[capture_id] += sum(
                1 for row in decision_rows if str(row.get("decision", "")).startswith("excluded_"))
        non_sku_path = directory / "non-sku-candidates.jsonl"
        if non_sku_path.exists():
            non_sku_rows = read_jsonl(non_sku_path, errors)
            non_sku_candidate_rows += len(non_sku_rows)
            non_sku_rows_by_capture[capture_id] += len(non_sku_rows)
            source_tables.append(source_table_record(non_sku_path, non_sku_rows))
            for row in non_sku_rows:
                meta = row.get("item_api") or {}
                for attempt in (meta.get("network_attempts") or []):
                    if attempt.get("attempt_id"):
                        ref = str(attempt["attempt_id"])
                        metadata_attempt_refs.add(ref)
                        metadata_attempt_refs_by_capture[capture_id].add(ref)
                if meta.get("attempt_id"):
                    ref = str(meta["attempt_id"])
                    metadata_attempt_refs.add(ref)
                    metadata_attempt_refs_by_capture[capture_id].add(ref)
                capture_file = meta.get("capture_file")
                raw_path = (directory / capture_file) if capture_file else None
                if raw_path and raw_path.exists():
                    non_sku_raw_bodies_by_capture[capture_id] += 1
                    candidate_raw_item_bodies += 1
                    if meta.get("sha256") == sha256_file(raw_path):
                        candidate_raw_sha_matches += 1
                    else:
                        candidate_raw_sha_mismatches += 1
                        errors.append({"code": "au_non_sku_candidate_raw_sha256_mismatch",
                                       "capture_directory": display_path(directory),
                                       "item_id": row.get("item_id"), "capture_file": capture_file,
                                       "expected": meta.get("sha256"), "actual": sha256_file(raw_path)})
                else:
                    errors.append({"code": "au_non_sku_candidate_raw_body_missing",
                                   "capture_directory": display_path(directory), "item_id": row.get("item_id"),
                                   "capture_file": capture_file})
        catalog_files = sorted(directory.glob("catalog-search-*.json"))
        catalog_response_files += len(catalog_files)
        catalog_files_by_capture[capture_id] = len(catalog_files)
        for path in catalog_files:
            source_tables.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha256_file(path),
                                  "kind": "raw_catalog_response"})

    by_item: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen_products = set()
    unique_product_ids = set()
    product_ids_per_capture = Counter()
    for product in products:
        item_id = str(product.get("item_id", ""))
        capture_id = str(product.get("_audit_capture", ""))
        scoped_id = (capture_id, item_id)
        if scoped_id in seen_products:
            errors.append({"code": "duplicate_au_product", "item_id": item_id})
        seen_products.add(scoped_id)
        unique_product_ids.add(item_id)
        product_ids_per_capture[capture_id] += 1
        by_item[scoped_id].append(product)
        if contains_synthetic_marker(product):
            errors.append({"code": "synthetic_record_marker", "platform": "au", "item_id": item_id})
    cells_by_item: Counter[tuple[str, str]] = Counter()
    unique_cells: set[tuple[str, str, int, int]] = set()
    stock_counts = Counter()
    item_level_price_types = Counter()
    raw_matrix_cells_by_item: Counter[str] = Counter()
    raw_matrix_shapes = Counter()
    axis_empty_values = Counter()
    stock_source_mismatches = 0
    product_cells_mismatch = []
    actual_matrix_cells = 0
    normalized_cells_by_identity: set[tuple[str, int, int]] = set()
    sku_rows_total = 0
    for directory in dirs:
      spath = directory / "skus.jsonl"
      capture_id = str(directory.resolve())
      capture_row_count = 0
      for sku in iter_jsonl(spath, errors):
        sku_rows_total += 1
        capture_row_count += 1
        item_id = str(sku.get("item_id", ""))
        if contains_synthetic_marker(sku):
            errors.append({"code": "synthetic_record_marker", "platform": "au", "item_id": item_id})
        scoped_id = (capture_id, item_id)
        if scoped_id not in by_item:
            errors.append({"code": "orphan_au_sku_row", "item_id": item_id})
        try:
            ri, ci = int(sku["row_index"]), int(sku["column_index"])
        except (KeyError, TypeError, ValueError):
            errors.append({"code": "invalid_au_cell_index", "item_id": item_id})
            continue
        key = (capture_id, item_id, ri, ci)
        if key in unique_cells:
            errors.append({"code": "duplicate_au_cell", "item_id": item_id, "row_index": ri, "column_index": ci})
        unique_cells.add(key)
        normalized_cells_by_identity.add((item_id, ri, ci))
        cells_by_item[scoped_id] += 1
        actual_matrix_cells += 1
        stock = sku.get("stock")
        if not isinstance(stock, dict):
            errors.append({"code": "au_stock_not_source_object", "item_id": item_id})
        else:
            if "remainingStock" not in stock:
                stock_counts["missing_remainingStock"] += 1
            elif stock["remainingStock"] is None:
                stock_counts["null_remainingStock"] += 1
            else:
                stock_counts["non_null_remainingStock"] += 1
        source_sku = (((raw_item_data.get(scoped_id) or {}).get("itemInfo") or {}).get("skuInfo") or {})
        raw_matrix = source_sku.get("stockList") or []
        if ri < len(raw_matrix) and isinstance(raw_matrix[ri], list) and ci < len(raw_matrix[ri]):
            raw_cell = raw_matrix[ri][ci]
            expected_normalized_cell = raw_cell if isinstance(raw_cell, dict) else {"source_value": raw_cell}
            if stock != expected_normalized_cell:
                stock_source_mismatches += 1
                errors.append({"code": "au_normalized_stock_differs_from_raw_cell", "item_id": item_id,
                               "row_index": ri, "column_index": ci})
        else:
            # Raw matrix is absent only for captures lacking their item response body.
            if scoped_id in raw_item_data:
                errors.append({"code": "au_cell_index_outside_raw_matrix", "item_id": item_id,
                               "row_index": ri, "column_index": ci})
        product = (by_item.get(scoped_id) or [{}])[0]
        product_sku = product.get("sku") or {}
        row_names, column_names = product_sku.get("row_names") or [], product_sku.get("column_names") or []
        if ri >= len(row_names) or ci >= len(column_names):
            errors.append({"code": "au_cell_outside_named_axis", "item_id": item_id,
                           "row_index": ri, "column_index": ci})
        elif sku.get("row_option_value") != row_names[ri] or sku.get("column_option_value") != column_names[ci]:
            errors.append({"code": "au_cell_axis_value_differs_from_product_axis", "item_id": item_id,
                           "row_index": ri, "column_index": ci})
      source_tables.append(streamed_table_record(spath, capture_row_count))
    matrix_shapes = Counter()
    axis_counts = Counter()
    for product in products:
        item_id = str(product.get("item_id", ""))
        scoped_id = (str(product.get("_audit_capture", "")), item_id)
        sku = product.get("sku") or {}
        row_names, column_names = sku.get("row_names") or [], sku.get("column_names") or []
        matrix = sku.get("stock_list_shape") or []
        matrix_shapes[f"{matrix[0]}x{','.join(map(str, matrix[1]))}" if len(matrix) == 2 else "unknown"] += 1
        axis_counts[f"{len(row_names)}x{len(column_names)}"] += 1
        axis_empty_values["empty_row_axis_values"] += sum(1 for v in row_names if v is None or v == "")
        axis_empty_values["empty_column_axis_values"] += sum(1 for v in column_names if v is None or v == "")
        source_sku = (((raw_item_data.get(scoped_id) or {}).get("itemInfo") or {}).get("skuInfo") or {})
        raw_matrix = source_sku.get("stockList") or []
        raw_count = sum(len(row) for row in raw_matrix if isinstance(row, list))
        raw_matrix_cells_by_item[scoped_id] = raw_count
        raw_matrix_shapes[f"{len(raw_matrix)}x{','.join(map(str, sorted({len(r) for r in raw_matrix if isinstance(r, list)})))}"] += 1
        if scoped_id in raw_item_data and raw_count != cells_by_item[scoped_id]:
            errors.append({"code": "au_raw_matrix_cell_count_mismatch", "item_id": item_id,
                           "raw_stockList_cells": raw_count, "normalized_sku_rows": cells_by_item[item_id]})
        if scoped_id in raw_item_data:
            source_rows = source_sku.get("rowNames") or []
            source_cols = source_sku.get("columnNames") or []
            if row_names != source_rows or column_names != source_cols:
                errors.append({"code": "au_normalized_axis_values_differ_from_raw", "item_id": item_id})
        item_level_price_types[str(product.get("current_price_json_type", "missing"))] += 1
        if cells_by_item[scoped_id] != int(sku.get("combination_count", -1)):
            product_cells_mismatch.append({"item_id": item_id, "sku_rows": cells_by_item[scoped_id],
                                           "recorded_combination_count": sku.get("combination_count")})
    if product_cells_mismatch:
        errors.extend({"code": "au_product_sku_count_mismatch", **x} for x in product_cells_mismatch)

    raw_item_files = sum(1 for d in dirs for x in d.rglob("*-item.json"))
    raw_option_files = sum(1 for d in dirs for x in d.rglob("*-options.json"))
    capture_ledgers = []
    ledger_total = 0
    exact_capture_attempts = 0
    legacy_incomplete_attempts = 0
    for directory in dirs:
        capture_id = str(directory.resolve())
        legacy = legacy_ledger_by_capture.get(capture_id, {})
        state = budget_state_by_capture.get(capture_id)
        if state:
            effective_attempts = int(state["charged_attempts"])
            exact_capture_attempts += effective_attempts
            if legacy.get("log_present") and int(legacy.get("attempts", 0)) != effective_attempts:
                warnings.append({"code": "au_legacy_ledger_mirror_count_differs_from_state",
                                 "capture_directory": display_path(directory),
                                 "legacy_log_attempts": legacy.get("attempts"),
                                 "durable_state_attempts": effective_attempts,
                                 "interpretation": "durable attempt state is authoritative; mirror log is reported but not counted again"})
            outcomes = state["attempt_outcomes"]
            if outcomes.get("missing_result", 0) or any(k.startswith("interrupted") for k in outcomes):
                warnings.append({"code": "au_budget_state_has_incomplete_attempt_results",
                                 "capture_directory": display_path(directory),
                                 "outcomes": outcomes,
                                 "interpretation": "charged attempts with no complete response remain budget usage and are excluded from usable response totals"})
            missing_refs = sorted(ref for ref in metadata_attempt_refs_by_capture[capture_id]
                                  if ref not in budget_attempt_ids_by_capture[capture_id])
            if missing_refs:
                errors.append({"code": "au_product_metadata_attempt_not_in_durable_ledger",
                               "capture_directory": display_path(directory),
                               "missing_attempt_reference_count": len(missing_refs), "examples": missing_refs[:5]})
            request_types = state["charged_by_request_type"]
            completeness = "durable_state_exact_attempt_count"
        else:
            effective_attempts = int(legacy.get("attempts", 0))
            legacy_incomplete_attempts += effective_attempts
            request_types = legacy.get("attempts_by_request_type", {})
            product_rows = [p for p in products if p.get("_audit_capture") == capture_id]
            successful_calls = sum(1 for p in product_rows for k in ("item_api", "options_api")
                                   if (p.get(k) or {}).get("status") == 200)
            candidate_floor = candidate_item_calls_by_capture[capture_id]
            item_call_floor = successful_calls + candidate_floor
            if effective_attempts < item_call_floor:
                warnings.append({"code": "au_http_ledger_below_observed_item_call_minimum",
                                 "capture_directory": display_path(directory),
                                 "ledger_attempts_recorded": effective_attempts,
                                 "successful_sku_product_item_and_options_calls_at_least": successful_calls,
                                 "excluded_candidate_item_calls_at_least": candidate_floor,
                                 "item_api_call_floor": item_call_floor,
                                 "catalog_response_bodies_saved": catalog_files_by_capture[capture_id],
                                 "interpretation": "legacy ledger falls below item calls evidenced by normalized products and excluded candidates; saved catalog bodies do not establish requests or retries"})
            if product_rows and any("attempt_count" not in ((p.get(k) or {}))
                                    for p in product_rows for k in ("item_api", "options_api")):
                warnings.append({"code": "au_request_attempt_metadata_incomplete",
                                 "capture_directory": display_path(directory),
                                 "interpretation": "successful API metadata omits attempt_count; exact retries and total HTTP calls are unknown"})
            completeness = "legacy_ledger_incomplete"
            request_ledger.update(Counter(request_types))
        ledger_total += effective_attempts
        if candidate_rows_by_capture[capture_id] and candidate_raw_bodies_by_capture[capture_id] < candidate_rows_by_capture[capture_id]:
            warnings.append({"code": "au_non_sku_candidate_raw_item_body_missing",
                             "capture_directory": display_path(directory),
                             "candidate_decision_rows": candidate_rows_by_capture[capture_id],
                             "raw_item_bodies_found": candidate_raw_bodies_by_capture[capture_id],
                             "interpretation": "candidate records document exclusions but omit raw item responses and attempt metadata"})
        capture_ledgers.append({"capture_directory": display_path(directory),
                                "completeness": completeness,
                                "attempts_counted_once": effective_attempts,
                                "attempts_by_request_type": request_types,
                                "durable_budget_state": state,
                                "legacy_log_attempts_mirrored_or_logged": legacy.get("attempts", 0),
                                "legacy_log_present": legacy.get("log_present", False)})
    return {
        "capture_directories": [p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p) for p in dirs],
        "source_tables": source_tables,
        "counts": {"products": len(products), "unique_products": len(unique_product_ids),
                   "duplicate_capture_product_references": len(products) - len(unique_product_ids),
                   "sku_rows": sku_rows_total, "unique_observed_cells": len(unique_cells),
                   "unique_observed_cells_by_product_identity": len(normalized_cells_by_identity),
                   "duplicate_capture_cell_references": sku_rows_total - len(normalized_cells_by_identity),
                   "raw_item_response_files": raw_item_files, "raw_options_response_files": raw_option_files,
                   "non_sku_candidate_rows": legacy_candidate_rows,
                   "stage2_non_sku_candidate_rows": non_sku_candidate_rows,
                   "excluded_candidate_item_call_floor": observed_candidate_item_calls,
                   "candidate_raw_item_bodies": candidate_raw_item_bodies,
                   "non_sku_candidate_raw_sha256_matches": candidate_raw_sha_matches,
                   "non_sku_candidate_raw_sha256_mismatches": candidate_raw_sha_mismatches,
                   "catalog_response_files": catalog_response_files,
                   "normalized_observed_cell_rows": actual_matrix_cells,
                   "raw_source_expected_matrix_rows": sum(raw_matrix_cells_by_item.values()),
                   "raw_stockList_cells_for_products_with_raw_item": sum(raw_matrix_cells_by_item.values())},
        "layout": {"matrix_shape_counts": dict(matrix_shapes), "axis_dimension_counts": dict(axis_counts),
                   "raw_matrix_shape_counts": dict(raw_matrix_shapes), "axis_empty_value_counts": dict(axis_empty_values),
                   "row_key": ["item_id", "row_index", "column_index"],
                   "option_values_are_source_axis_entries": True,
                   "cells_are_observed_stockList_cells_only": True,
                   "product_level_current_price_json_types": dict(item_level_price_types)},
        "stock_source_value_counts": dict(stock_counts),
        "http_ledger": {"attempts_by_request_type": dict(request_ledger), "attempts_recorded_counting_each_capture_once": ledger_total,
                        "exact_attempts_from_durable_states": exact_capture_attempts,
                        "legacy_incomplete_attempts_logged_not_exact": legacy_incomplete_attempts,
                        "all_legacy_ledger_jsonl_rows_including_state_mirrors": legacy_ledger_total,
                        "combined_attempt_total_exact": all(x["completeness"] == "durable_state_exact_attempt_count" for x in capture_ledgers),
                        "capture_ledgers": capture_ledgers,
                        "product_api_metadata_attempt_references": len(metadata_attempt_refs),
                        "product_api_metadata_attempt_references_in_ledger": sum(
                            len(metadata_attempt_refs_by_capture[capture_id] & budget_attempt_ids_by_capture[capture_id])
                            for capture_id in metadata_attempt_refs_by_capture),
                        "api_retrieved_at_utc_counts": dict(api_timestamp_counts)},
        "raw_integrity": {**dict(raw_integrity), "normalized_stock_cell_mismatches": stock_source_mismatches},
        "warnings": warnings, "errors": errors,
    }


def audit_rakuten(dirs: list[Path]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    source_tables = []
    raw_integrity = Counter()
    request_ledger_info = []
    raw_sku_arrays: dict[tuple[str, str], list[dict[str, Any]]] = {}
    raw_axes_by_capture_url: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for directory in dirs:
        ppath, spath = directory / "products.jsonl", directory / "skus.jsonl"
        p = read_jsonl(ppath, errors)
        source_tables.append(source_table_record(ppath, p))
        capture_id = str(directory.resolve())
        products.extend({**row, "_audit_capture": capture_id} for row in p)
        # Batch-layout raw files are joined through each row's path, then checked by SHA.
        for product in p:
            raw_ref = product.get("raw_file")
            raw_path = (ROOT / raw_ref) if raw_ref else None
            if raw_path and raw_path.exists() and product.get("sha256") == sha256_file(raw_path):
                raw_integrity["sha256_match"] += 1
                try:
                    raw_data = fetch_rakuten.app_data(
                        fetch_rakuten.decoded_html(raw_path.read_bytes(), product.get("content_type") or ""))
                    source_sku_data = ((((raw_data or {}).get("api") or {}).get("data") or {}).get("itemInfoSku") or {})
                    raw_sku_arrays[(capture_id, str(product.get("source_url", "")))] = source_sku_data.get("sku") or []
                    raw_axes_by_capture_url[(capture_id, str(product.get("source_url", "")))] = fetch_rakuten.axis_values(
                        source_sku_data.get("variantSelectors") or [])
                except (OSError, ValueError, TypeError) as exc:
                    errors.append({"code": "rakuten_raw_sku_parse_failed", "source_url": product.get("source_url"),
                                   "raw_file": raw_ref, "error": str(exc)})
            else:
                raw_integrity["missing_or_sha256_mismatch"] += 1
                errors.append({"code": "rakuten_raw_sha256_mismatch_or_missing", "source_url": product.get("source_url"),
                               "raw_file": raw_ref, "sha256": product.get("sha256")})
        ledger = directory / "request-ledger.json"
        if ledger.exists():
            try:
                data = json.loads(ledger.read_text(encoding="utf-8"))
                source_tables.append({"path": ledger.relative_to(ROOT).as_posix(), "sha256": sha256_file(ledger), "rows": None})
                request_ledger_info.append(data)
            except (json.JSONDecodeError, OSError) as exc:
                errors.append({"code": "invalid_rakuten_request_ledger", "path": str(ledger), "error": str(exc)})
    products_by_capture_url: dict[tuple[str, str], dict[str, Any]] = {}
    unique_product_urls = set()
    products_per_capture = Counter()
    for product in products:
        url = str(product.get("source_url", ""))
        capture_id = str(product.get("_audit_capture", ""))
        scoped_url = (capture_id, url)
        if scoped_url in products_by_capture_url:
            errors.append({"code": "duplicate_rakuten_product_url", "source_url": url})
        products_by_capture_url[scoped_url] = product
        unique_product_urls.add(url)
        products_per_capture[capture_id] += 1
        if contains_synthetic_marker(product):
            errors.append({"code": "synthetic_record_marker", "platform": "rakuten", "source_url": url})
    key_counts = Counter()
    sku_ids = Counter()
    hidden_sku_counts = Counter()
    hidden_stock_counts = Counter()
    visible_availability = Counter()
    embedded_quantity_types = Counter()
    raku_provenance_consistency = Counter()
    axis_lengths = Counter()
    option_axis_counts = Counter()
    price_jpy_integer_count = 0
    seen_sku_key_rows: dict[tuple[str, str], dict[str, Any]] = {}
    seen_source_row_keys: set[tuple[str, str, int]] = set()
    seen_record_keys: set[tuple[str, str]] = set()
    source_row_index_count = 0
    merchant_id_collisions: list[dict[str, Any]] = []
    sku_row_count = 0
    sku_rows_per_url: Counter[tuple[str, str]] = Counter()
    unique_sku_refs: set[tuple[str, str, str]] = set()
    for product in products:
        axes = product.get("axes") or []
        axis_lengths[tuple(len((a.get("values") or [])) for a in axes)] += 1
        raku_provenance_consistency["products_with_retrieved_at_utc" if product.get("retrieved_at_utc") else
                                    "products_missing_retrieved_at_utc"] += 1
    for directory in dirs:
      capture_id = str(directory.resolve())
      spath = directory / "skus.jsonl"
      capture_row_count = 0
      for sku in iter_jsonl(spath, errors):
        sku_row_count += 1
        capture_row_count += 1
        url, sku_id = str(sku.get("source_url", "")), str(sku.get("sku_id", ""))
        scoped_url = (capture_id, url)
        sku_rows_per_url[scoped_url] += 1
        if contains_synthetic_marker(sku):
            errors.append({"code": "synthetic_record_marker", "platform": "rakuten", "source_url": url})
        if scoped_url not in products_by_capture_url:
            errors.append({"code": "orphan_rakuten_sku_row", "source_url": url, "sku_id": sku_id})
        key = (capture_id + "\0" + url, sku_id)
        key_counts[key] += 1
        sku_ids[sku_id] += 1
        if key_counts[key] > 1:
            first = seen_sku_key_rows[key]
            merchant_id_collisions.append({"source_url": url, "_capture_id": capture_id, "sku_id": sku_id,
                                           "first_variant_id": first.get("variant_id"), "duplicate_variant_id": sku.get("variant_id"),
                                           "first_option_values": first.get("option_values"), "duplicate_option_values": sku.get("option_values"),
                                           "interpretation": "source rows share merchant-defined sku_id; inspect variant_id and selector values"})
        else:
            seen_sku_key_rows[key] = sku
        source_row_index = sku.get("source_row_index")
        record_key = sku.get("sku_record_key")
        if source_row_index is not None:
            source_row_index_count += 1
            try:
                source_index = int(source_row_index)
                row_key = (capture_id + "\0" + url, str(sku.get("sha256", "")), source_index)
                if row_key in seen_source_row_keys:
                    errors.append({"code": "duplicate_rakuten_source_row_key", "source_url": url,
                                   "sha256": sku.get("sha256"), "source_row_index": source_index})
                seen_source_row_keys.add(row_key)
                expected_record_key = hashlib.sha256(
                    f"{url}\n{sku.get('sha256', '')}\n{source_index}".encode("utf-8")).hexdigest()
                if record_key is not None and str(record_key) != expected_record_key:
                    errors.append({"code": "rakuten_sku_record_key_does_not_match_source_identity", "source_url": url,
                                   "source_row_index": source_index, "sku_record_key": record_key,
                                   "expected": expected_record_key})
                source_rows = raw_sku_arrays.get(scoped_url, [])
                if source_rows:
                    if source_index < 0 or source_index >= len(source_rows):
                        errors.append({"code": "rakuten_source_row_index_outside_raw_sku_array", "source_url": url,
                                       "source_row_index": source_index, "raw_sku_array_count": len(source_rows)})
                    else:
                        source_row = source_rows[source_index]
                        for normalized_field, raw_field in (("merchant_defined_sku_id", "merchantDefinedSkuId"),
                                                            ("variant_id", "variantId")):
                            if sku.get(normalized_field) != source_row.get(raw_field):
                                errors.append({"code": "rakuten_source_identity_differs_from_raw_row", "source_url": url,
                                               "source_row_index": source_index, "field": normalized_field,
                                               "normalized": sku.get(normalized_field), "raw": source_row.get(raw_field)})
                        raw_options = source_row.get("selectorValues") or []
                        if [x.get("value") for x in (sku.get("option_values") or [])] != raw_options:
                            errors.append({"code": "rakuten_normalized_option_values_differ_from_raw_row", "source_url": url,
                                           "source_row_index": source_index})
                        raw_price = source_row.get("taxIncludedPrice")
                        expected_price = (int(raw_price) if isinstance(raw_price, (int, float)) and not isinstance(raw_price, bool) and float(raw_price).is_integer()
                                          else None)
                        if sku.get("price_jpy") != expected_price:
                            errors.append({"code": "rakuten_normalized_price_differs_from_raw_row", "source_url": url,
                                           "source_row_index": source_index, "normalized": sku.get("price_jpy"),
                                           "raw_expected": expected_price})
            except (TypeError, ValueError):
                errors.append({"code": "invalid_rakuten_source_row_index", "source_url": url,
                               "source_row_index": source_row_index})
        if record_key is not None:
            composite = (capture_id + "\0" + url, str(record_key))
            if composite in seen_record_keys:
                errors.append({"code": "duplicate_rakuten_sku_record_key", "source_url": url,
                               "sku_record_key": record_key})
            seen_record_keys.add(composite)
        hidden_sku_counts[str(sku.get("hidden_sku"))] += 1
        hidden_stock_counts[str(sku.get("inventory_display_setting"))] += 1
        visible_availability[str(sku.get("visible_availability"))] += 1
        q = sku.get("inventory_quantity_from_embedded_json")
        embedded_quantity_types["null" if q is None else type(q).__name__] += 1
        if isinstance(sku.get("price_jpy"), int) and not isinstance(sku.get("price_jpy"), bool):
            price_jpy_integer_count += 1
        opts = sku.get("option_values") or []
        option_axis_counts[len(opts)] += 1
        product = products_by_capture_url.get(scoped_url) or {}
        if sku.get("sha256") == product.get("sha256"):
            raku_provenance_consistency["sku_rows_matching_parent_raw_sha256"] += 1
        else:
            raku_provenance_consistency["sku_rows_mismatching_parent_raw_sha256"] += 1
            errors.append({"code": "rakuten_sku_row_parent_sha256_mismatch", "source_url": url,
                           "sku_id": sku_id, "sku_sha256": sku.get("sha256"),
                           "product_sha256": product.get("sha256")})
        if sku.get("retrieved_at_utc") == product.get("retrieved_at_utc") and sku.get("retrieved_at_utc"):
            raku_provenance_consistency["sku_rows_matching_parent_retrieved_at_utc"] += 1
        else:
            raku_provenance_consistency["sku_rows_mismatching_parent_retrieved_at_utc"] += 1
            errors.append({"code": "rakuten_sku_row_parent_retrieved_at_mismatch", "source_url": url,
                           "sku_id": sku_id, "sku_retrieved_at_utc": sku.get("retrieved_at_utc"),
                           "product_retrieved_at_utc": product.get("retrieved_at_utc")})
        axes = product.get("axes") or []
        for i, option in enumerate(opts):
            if i >= len(axes):
                errors.append({"code": "rakuten_option_axis_outside_product_axes", "source_url": url, "sku_id": sku_id, "axis_index": i})
            elif option.get("axis_key") != axes[i].get("key"):
                errors.append({"code": "rakuten_option_axis_key_mismatch", "source_url": url, "sku_id": sku_id, "axis_index": i})
            elif option.get("value") not in [v.get("value") for v in (axes[i].get("values") or [])]:
                errors.append({"code": "rakuten_option_value_outside_source_axis", "source_url": url,
                               "sku_id": sku_id, "axis_index": i, "value": option.get("value")})
        unique_identity = str(sku.get("sku_record_key") or sku.get("source_row_index") or
                              (sku.get("variant_id") or (sku_id, json.dumps(sku.get("option_values", []), ensure_ascii=False, sort_keys=True))))
        unique_sku_refs.add((url, str(sku.get("sha256", "")), unique_identity))
      source_tables.append(streamed_table_record(spath, capture_row_count))
    products_sku_mismatch = []
    for (capture_id, url), product in products_by_capture_url.items():
        expected = int(product.get("sku_count", 0) or 0)
        actual = sku_rows_per_url[(capture_id, url)]
        if expected != actual:
            products_sku_mismatch.append({"source_url": url, "product_sku_count": expected, "normalized_sku_rows": actual})
            errors.append({"code": "rakuten_product_sku_count_mismatch", **products_sku_mismatch[-1]})
        source_rows = raw_sku_arrays.get((capture_id, url))
        if source_rows is not None and len(source_rows) != expected:
            errors.append({"code": "rakuten_raw_sku_array_count_mismatch", "source_url": url,
                           "raw_sku_array_count": len(source_rows), "product_sku_count": expected})
        raw_axes = raw_axes_by_capture_url.get((capture_id, url))
        if raw_axes is not None and product.get("axes") != raw_axes:
            errors.append({"code": "rakuten_normalized_axes_differ_from_raw_source", "source_url": url})
    has_source_keys = bool(sku_row_count) and source_row_index_count == sku_row_count and len(seen_record_keys) == sku_row_count
    if has_source_keys:
        # Merchant-defined IDs can collide in source data. Source array positions and
        # explicit record keys preserve both variants and make that anomaly auditable.
        for collision in merchant_id_collisions:
            collision.update({"code": "rakuten_source_merchant_sku_id_collision",
                              "retained_source_rows": key_counts[(collision["_capture_id"] + "\0" + collision["source_url"],
                                                                   collision["sku_id"])]})
            capture_path = Path(collision.pop("_capture_id"))
            collision["capture_directory"] = capture_path.relative_to(ROOT).as_posix() if capture_path.is_relative_to(ROOT) else str(capture_path)
            warnings.append(collision)
        for (capture_url, sku_id), count in key_counts.items():
            if count > 1:
                url = capture_url.split("\0", 1)[-1]
                if not any(x.get("source_url") == url and x.get("sku_id") == sku_id for x in merchant_id_collisions):
                    warnings.append({"code": "rakuten_source_merchant_sku_id_collision", "source_url": url,
                                     "sku_id": sku_id, "retained_source_rows": count,
                                     "interpretation": "all rows retained under distinct source-row keys; compare variant ids and selector values"})
    else:
        for collision in merchant_id_collisions:
            collision["code"] = "duplicate_rakuten_source_sku_key"
            capture_path = Path(collision.pop("_capture_id"))
            collision["capture_directory"] = capture_path.relative_to(ROOT).as_posix() if capture_path.is_relative_to(ROOT) else str(capture_path)
            errors.append(collision)
    for ledger in request_ledger_info:
        reported = ledger.get("http_attempts_including_redirects")
        summed = sum(int(x.get("http_request_count", 0) or 0) for x in ledger.get("runs", []))
        if reported is not None and int(reported) != summed:
            errors.append({"code": "rakuten_request_ledger_sum_mismatch", "reported": reported, "sum_runs": summed})
        charged = ledger.get("http_requests_charged")
        events = ledger.get("request_events")
        event_count = len(events) if isinstance(events, list) else events
        if charged is not None and event_count is not None and int(charged) != int(event_count):
            errors.append({"code": "rakuten_request_ledger_event_count_mismatch",
                           "http_requests_charged": charged, "request_event_count": event_count})
        if int(ledger.get("body_bytes_reserved", 0) or 0) != 0:
            warnings.append({"code": "rakuten_request_ledger_has_reserved_body_bytes",
                             "body_bytes_reserved": ledger.get("body_bytes_reserved")})
    raw_source_sku_rows = sum(len(rows) for rows in raw_sku_arrays.values())
    return {
        "capture_directories": [p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p) for p in dirs],
        "source_tables": source_tables,
        "counts": {"products": len(products), "unique_products": len(unique_product_urls),
                   "duplicate_capture_product_references": len(products) - len(unique_product_urls),
                   "sku_rows": sku_row_count,
                   "unique_sku_rows_by_source_reference": len(unique_sku_refs),
                   "duplicate_capture_sku_row_references": sku_row_count - len(unique_sku_refs),
                   "products_with_sku_rows": sum(1 for scoped_url in products_by_capture_url if sku_rows_per_url[scoped_url]),
                   "normalized_sku_rows": sku_row_count, "raw_source_sku_array_rows": raw_source_sku_rows,
                   "rows_with_source_row_index": source_row_index_count,
                   "rows_with_sku_record_key": len(seen_record_keys)},
        "layout": {"row_key": ["source_url", "sha256", "source_row_index", "sku_record_key"] if has_source_keys else ["source_url", "sku_id"],
                   "axes_order_preserved_in_option_values": True,
                   "product_axis_cardinality_shapes": {"x".join(map(str, k)) or "zero_axes": n for k, n in axis_lengths.items()},
                   "sku_option_axis_count": dict(option_axis_counts),
                   "observed_sku_rows_only_no_cartesian_expansion": True},
        "availability_and_price": {"hidden_sku": dict(hidden_sku_counts),
                                   "inventory_display_setting": dict(hidden_stock_counts),
                                   "visible_availability": dict(visible_availability),
                                   "embedded_quantity_type": dict(embedded_quantity_types),
                                   "price_jpy_integer_rows": price_jpy_integer_count},
        "source_provenance_consistency": dict(raku_provenance_consistency),
        "raw_integrity": dict(raw_integrity), "request_ledgers": [summarize_rakuten_ledger(x) for x in request_ledger_info],
        "warnings": warnings, "errors": errors,
    }


def run(au_dirs: list[Path], rakuten_dirs: list[Path], output: Path, stage: str) -> dict[str, Any]:
    au = audit_au(validate_dirs(au_dirs, "au"))
    rakuten = audit_rakuten(validate_dirs(rakuten_dirs, "rakuten"))
    report = {"record_kind": "REAL_CAPTURE_AUDIT", "stage": stage,
              "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "real AU and Rakuten capture directories explicitly supplied to this audit",
              "au": au, "rakuten": rakuten,
              "errors_total": len(au["errors"]) + len(rakuten["errors"]),
              "warnings_total": len(au["warnings"]) + len(rakuten["warnings"])}
    out = output if output.is_absolute() else ROOT / output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--au-dir", type=Path, action="append", required=True, help="real AU capture directory; repeatable")
    ap.add_argument("--rakuten-dir", type=Path, action="append", required=True, help="real Rakuten capture directory; repeatable")
    ap.add_argument("--stage", default="stage1", help="label written to audit result")
    ap.add_argument("--output", type=Path, required=True, help="output JSON path")
    args = ap.parse_args()
    report = run(args.au_dir, args.rakuten_dir, args.output, args.stage)
    print(json.dumps({"output": str(args.output), "errors_total": report["errors_total"],
                      "warnings_total": report["warnings_total"],
                      "au": report["au"]["counts"], "rakuten": report["rakuten"]["counts"]}, ensure_ascii=False))
    if report["errors_total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
