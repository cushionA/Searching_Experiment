#!/usr/bin/env python3
"""One-pass, bounded capture of frozen Wowma product pages and NEXT_DATA tables.

This script performs one ordinary GET per frozen product ID. It does not search,
follow page links, retry failures, change proxy/TLS settings, or infer labels.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_PREPARED = ROOT / ".lab-output/sku-volume-unused-test-20261013-v1/prepared-source-v1/documents.jsonl"
DEFAULT_BODIES = ROOT / ".lab-output/sku-volume-unused-test-20261013-v1/scoped-native-source-v3/source-bodies.jsonl"
DEFAULT_SOURCE_ROOT = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-nextdata-table-20261013-v1/live-nextdata-captures-v1"
NEXTDATA_PATH = "$.props.pageProps.initialState.itemDetails.itemPageInfo.itemInfo.extraItemComment"
NEXTDATA_ITEM_PATH = "$.props.pageProps.initialState.itemDetails.itemPageInfo.itemInfo"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import build_scoped_html_blocks_v1 as HTML_BLOCKS  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def frozen_products(prepared_path: Path, bodies_path: Path, source_root: Path) -> list[dict[str, Any]]:
    prepared = [row for row in read_rows(prepared_path) if row.get("side") == "au"]
    bodies = {row.get("document_id"): row for row in read_rows(bodies_path) if row.get("side") == "au"}
    if len(prepared) != 46 or len({str(row.get("product_id")) for row in prepared}) != 46:
        raise ValueError("expected exactly 46 unique frozen AU product IDs")
    result = []
    for row in prepared:
        product_id = str(row["product_id"])
        body = bodies.get(row.get("document_id"))
        if not body:
            raise ValueError("prepared product has no matching source body")
        refs = (body.get("source_document_binding") or {}).get("description_refs") or []
        unique = {(str(ref.get("raw_file")), str(ref.get("sha256")))
                  for ref in refs if ref.get("raw_file") and ref.get("sha256")}
        if len(unique) != 1:
            raise ValueError("expected one logical AU raw capture per product")
        raw_file, expected_sha = next(iter(unique))
        raw_path = (source_root / raw_file).resolve()
        if source_root.resolve() not in raw_path.parents:
            raise ValueError("logical AU raw path escapes frozen source root")
        actual_sha = sha(raw_path.read_bytes())
        if actual_sha != expected_sha:
            raise ValueError("logical AU raw capture hash mismatch")
        result.append({"product_id": product_id, "document_id": row["document_id"],
                       "logical_au_raw_file": raw_file,
                       "logical_au_raw_capture_sha256": expected_sha,
                       "logical_au_raw_hash_verified": True})
    return sorted(result, key=lambda item: int(item["product_id"]))


def _fetch_once(product: dict[str, Any]) -> dict[str, Any]:
    product_id = product["product_id"]
    requested_url = f"https://wowma.jp/item/{product_id}"
    # No custom transport headers or retry logic. urllib uses inherited proxy and
    # TLS configuration and follows only normal HTTP redirects.
    request = Request(requested_url, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            return {**product, "requested_url": requested_url, "status": response.status,
                    "final_url": response.geturl(), "content_type": response.headers.get("Content-Type"),
                    "page_bytes": body, "error_type": None}
    except HTTPError as exc:
        try:
            body = exc.read()
        except Exception:
            body = b""
        return {**product, "requested_url": requested_url, "status": exc.code,
                "final_url": exc.geturl(), "content_type": exc.headers.get("Content-Type") if exc.headers else None,
                "page_bytes": body, "error_type": "HTTPError"}
    except Exception as exc:
        return {**product, "requested_url": requested_url, "status": None,
                "final_url": None, "content_type": None, "page_bytes": b"",
                "error_type": type(exc).__name__}


def _extract_nextdata(page: bytes) -> tuple[bytes | None, str, int | None, int | None]:
    # Locate the serialized script bytes in the captured page, preserving its
    # exact bytes. No HTML entity decoding or text normalization is performed.
    pattern = re.compile(rb"<script\b(?=[^>]*\bid\s*=\s*['\"]__NEXT_DATA__['\"])[^>]*>(.*?)</script\s*>",
                         re.IGNORECASE | re.DOTALL)
    match = pattern.search(page)
    if not match:
        return None, "missing", None, None
    return match.group(1), "present", match.start(1), match.end(1)


def _resolve_json_path(obj: Any, tokens: list[str]) -> Any:
    cur = obj
    for token in tokens:
        if not isinstance(cur, dict) or token not in cur:
            raise KeyError(token)
        cur = cur[token]
    return cur


def _table_rows(dom: Any, table: Any) -> list[Any]:
    return [node for node in dom.nodes if node.tag == "tr"
            and HTML_BLOCKS._nearest(node, {"table"}) is table]


def _extract_tables(product_dir: Path, nextdata_rel: str, script_sha: str,
                    nextdata: dict[str, Any], product_id: str,
                    actual_url_matches: bool) -> dict[str, Any]:
    item_tokens = ["props", "pageProps", "initialState", "itemDetails", "itemPageInfo", "itemInfo"]
    item_info = _resolve_json_path(nextdata, item_tokens)
    if not isinstance(item_info, dict):
        raise TypeError("itemInfo is not an object")
    query = nextdata.get("query") if isinstance(nextdata.get("query"), dict) else {}
    lot_number = item_info.get("lotNumber")
    query_id = query.get("item_id")
    title = item_info.get("itemTitle")
    identity_checks = {"query_item_id": str(query_id) if query_id is not None else None,
                       "item_info_lot_number": str(lot_number) if lot_number is not None else None,
                       "matches_requested_id": str(query_id) == product_id and str(lot_number) == product_id,
                       "name_field": "itemName" if "itemName" in item_info else ("itemTitle" if "itemTitle" in item_info else None),
                       "name_parent_json_path": NEXTDATA_ITEM_PATH if ("itemName" in item_info or "itemTitle" in item_info) else None,
                       "name_value": title if isinstance(title, str) else None,
                       "item_info_keys": sorted(item_info.keys())}
    if not actual_url_matches or not identity_checks["matches_requested_id"]:
        raise ValueError("page URL or NEXT_DATA identity does not match requested product ID")
    if "extraItemComment" not in item_info:
        return {"json_path": NEXTDATA_PATH, "value_type": "missing", "value_chars": 0,
                "value_sha256": None, "table_count": 0, "tables": [],
                "field_status": "missing", "identity": identity_checks}
    value = item_info["extraItemComment"]
    if not isinstance(value, str):
        raise TypeError("extraItemComment is not a string")

    dom = HTML_BLOCKS._DOM(value).finish()
    table_nodes = [node for node in dom.nodes if node.tag == "table"]
    table_ordinals = {node.path: index + 1 for index, node in enumerate(table_nodes)}
    table_dir = product_dir / "tables"
    table_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for node in table_nodes:
        start, end = node.start, node.end
        if not 0 <= start <= end <= len(value):
            raise ValueError("parsed table span is outside decoded field")
        table_html = value[start:end]
        table_rel = f"tables/table-{table_ordinals[node.path]:04d}.html"
        (product_dir / table_rel).write_bytes(table_html.encode("utf-8"))
        ancestors = [parent for parent in reversed(list(HTML_BLOCKS._ancestors(node)))
                     if parent.tag == "table"]
        ancestor_order = [table_ordinals[parent.path] for parent in ancestors] + [table_ordinals[node.path]]
        table_ref = {"raw_file": nextdata_rel, "sha256": script_sha,
                     "json_path": NEXTDATA_PATH,
                     "decoded_value_sha256": sha(value.encode("utf-8")),
                     "offset_unit": "unicode_code_points_in_decoded_json_string",
                     "start": start, "end": end, "quote_sha256": sha(table_html.encode("utf-8")),
                     "quote_file": table_rel, "dom_path": node.path}
        rows.append({"table_ordinal": table_ordinals[node.path], "dom_path": node.path,
                     "ancestor_table_ordinals_outer_to_self": ancestor_order,
                     "parent_table_ordinal": table_ordinals[ancestors[-1].path] if ancestors else None,
                     "start": start, "end": end, "offset_unit": "unicode_code_points_in_decoded_json_string",
                     "html_sha256": sha(table_html.encode("utf-8")), "html_chars": len(table_html),
                     "row_count_nearest_table": len(_table_rows(dom, node)),
                     "closure": node.closure, "source_ref": table_ref})
    return {"json_path": NEXTDATA_PATH, "value_type": "string", "field_status": "resolved",
            "value_chars": len(value), "value_sha256": sha(value.encode("utf-8")),
            "table_count": len(rows), "tables": rows, "identity": identity_checks}


def capture_all(prepared_path: Path = DEFAULT_PREPARED,
                bodies_path: Path = DEFAULT_BODIES,
                source_root: Path = DEFAULT_SOURCE_ROOT,
                output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    products = frozen_products(Path(prepared_path), Path(bodies_path), Path(source_root))
    (output / "products").mkdir()
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_once, product): product for product in products}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: int(item["product_id"]))

    records = []
    for result in results:
        product_id = result["product_id"]
        product_dir = output / "products" / product_id
        product_dir.mkdir()
        page = result.pop("page_bytes")
        page_rel = f"products/{product_id}/page.html"
        (output / page_rel).write_bytes(page)
        record = {k: v for k, v in result.items() if k != "page_bytes"}
        record.update({"page_raw_file": page_rel, "page_bytes": len(page),
                       "page_sha256": sha(page), "nextdata_status": "not_attempted",
                       "nextdata_raw_file": None, "nextdata_raw_script_sha256": None,
                       "nextdata_raw_script_bytes": 0, "route_status": "not_resolved",
                       "actual_url_path_matches_requested_id": False,
                       "field_status": "not_attempted", "table_count": 0, "tables": []})
        status = result.get("status")
        final_path_id = None
        if result.get("final_url"):
            parts = [part for part in urlsplit(result["final_url"]).path.split("/") if part]
            if len(parts) == 2 and parts[0] == "item":
                final_path_id = parts[1]
        record["actual_url_path_matches_requested_id"] = final_path_id == product_id
        record["route_status"] = "matched" if final_path_id == product_id else "mismatch_or_unresolved"
        if status == 200 and page:
            script, script_status, script_start, script_end = _extract_nextdata(page)
            record["nextdata_status"] = script_status
            if script is None:
                record["field_status"] = "nextdata_missing"
            if script is not None:
                script_rel = f"products/{product_id}/nextdata.json"
                (output / script_rel).write_bytes(script)
                script_sha = sha(script)
                record.update({"nextdata_raw_file": script_rel,
                               "nextdata_raw_script_sha256": script_sha,
                               "nextdata_raw_script_bytes": len(script),
                               "nextdata_container_ref": {"raw_file": page_rel,
                                   "sha256": record["page_sha256"],
                                   "script_selector": "script#__NEXT_DATA__",
                                   "script_start_byte": script_start,
                                   "script_end_byte": script_end,
                                   "script_raw_sha256": script_sha,
                                   "offset_unit": "raw_page_bytes"}})
                try:
                    parsed = json.loads(script.decode("utf-8"))
                    record["nextdata_root_keys"] = sorted(parsed.keys()) if isinstance(parsed, dict) else []
                    record["field_status"] = "resolved"
                    field = _extract_tables(product_dir, script_rel, script_sha, parsed, product_id,
                                            record["actual_url_path_matches_requested_id"])
                    record.update({"field_status": "resolved", **field})
                except Exception as exc:
                    record["field_status"] = "identity_mismatch" if "identity" in str(exc).lower() else "resolution_error"
                    record["field_error_type"] = type(exc).__name__
                    record["field_error"] = str(exc)[:240]
        elif status == 200:
            record["nextdata_status"] = "empty_page"
            record["field_status"] = "unknown"
        elif status is None:
            record["nextdata_status"] = "unavailable_no_response"
            record["field_status"] = "unknown"
        else:
            record["nextdata_status"] = "not_attempted_http_error"
            record["field_status"] = "unknown"
        records.append(record)

    summary = {"schema_version": "sku-au-nextdata-table-capture-v1",
               "source_dataset": "sku-volume-unused-test-20261013-v1",
               "scope": "Only the 46 frozen AU product IDs; one GET per fixed https://wowma.jp/item/{id} URL; no discovery or fallback URLs.",
               "request_policy": {"method": "GET", "max_concurrency": 4, "timeout_seconds": 30,
                                  "retries": 0, "proxy_tls": "inherited urllib defaults", "transport_bypass": False},
               "source_inputs": {"prepared_documents_sha256": sha(Path(prepared_path).read_bytes()),
                                 "source_bodies_sha256": sha(Path(bodies_path).read_bytes())},
               "requested_product_count": len(products),
               "http_status_counts": {}, "nextdata_status_counts": {}, "field_status_counts": {},
               "product_records": records,
               "labels_read": False, "predictions_read": False, "semantic_classification_run": False,
               "inference_run": False, "uploads_performed": False}
    for record in records:
        for counter, key in (("http_status_counts", str(record.get("status"))),
                              ("nextdata_status_counts", str(record.get("nextdata_status"))),
                              ("field_status_counts", str(record.get("field_status")))):
            summary[counter][key] = summary[counter].get(key, 0) + 1
    receipt_path = output / "capture-inventory.json"
    receipt_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["inventory_sha256"] = sha(receipt_path.read_bytes())
    return summary


if __name__ == "__main__":
    result = capture_all()
    print(json.dumps({k: result[k] for k in ("requested_product_count", "http_status_counts",
                                             "nextdata_status_counts", "field_status_counts",
                                             "inventory_sha256")}, ensure_ascii=False))
