#!/usr/bin/env python3
"""Fetch public Rakuten item pages and preserve their embedded SKU records.

This is a bounded data collector, not a discovery crawler. Supply URLs observed
in an authorized source (for example an already captured same-store page or
the matching au product record). Each invocation writes to a new directory.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import heapq
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWN_PRODUCTS = ROOT / ".lab-output/sku-real-rakuten-20261009/products.jsonl"
USER_AGENT = "SearchingExperiment-SKU-observation/1.1 (read-only public product data)"
MAX_BODY_BYTES = 8 * 1024 * 1024


class BoundedRedirect(HTTPRedirectHandler):
    def __init__(self, is_over_limit):
        super().__init__()
        self.is_over_limit = is_over_limit
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.netloc != "item.rakuten.co.jp":
            raise RuntimeError(f"redirect outside authorized origin: {newurl}")
        if self.is_over_limit():
            raise RuntimeError("HTTP request cap reached during redirect")
        self.count += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def valid_item_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    p = urlparse(url)
    segs = p.path.strip("/").split("/")
    return p.scheme == "https" and p.netloc == "item.rakuten.co.jp" and len(segs) == 2 and segs[0] == "weimall"


def valid_category_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    p = urlparse(url)
    segs = p.path.strip("/").split("/")
    return p.scheme == "https" and p.netloc == "item.rakuten.co.jp" and len(segs) >= 2 and segs[:2] == ["weimall", "c"]


def valid_store_page_url(url: str) -> bool:
    return valid_item_url(url) or valid_category_url(url)


def load_urls(path: Path, allow_category: bool = False) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = None
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            url = line.strip()
        else:
            url = row.get("source_url") or row.get("url") or row.get("rakuten_url") or ""
        allowed = valid_store_page_url(url) if allow_category else valid_item_url(url)
        if url and allowed and url not in [x["url"] for x in urls]:
            evidence = {k: v for k, v in row.items() if k not in {"source_url", "url", "rakuten_url"}} if isinstance(row, dict) else {}
            urls.append({"url": url, "candidate_evidence": evidence or None})
    return urls


def decoded_html(raw: bytes, content_type: str = "") -> str:
    enc = None
    match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.I)
    if match:
        enc = match.group(1)
    if not enc:
        head = raw[:4096].decode("ascii", "ignore")
        match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", head, re.I)
        enc = match.group(1) if match else "utf-8"
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def app_data(html: str) -> dict | None:
    marker = '<script type="application/json" id="item-page-app-data">'
    pos = html.find(marker)
    if pos < 0:
        return None
    start = html.find("{", pos + len(marker))
    end = html.find("</script>", start)
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(html[start:end].strip())
    except json.JSONDecodeError:
        return None


def axis_values(selectors: list[dict]) -> list[dict]:
    return [{"key": a.get("key"), "name": a.get("name"), "label": a.get("label"),
             "values": [{"value": v.get("value"), "label": v.get("label")} for v in (a.get("values") or [])]}
            for a in selectors]


def parse_page(record: dict, raw: bytes) -> tuple[dict, list[dict]]:
    html = decoded_html(raw, record.get("content_type", ""))
    data = app_data(html)
    sku_data = (((data or {}).get("api") or {}).get("data") or {}).get("itemInfoSku") or {}
    source = {
        "source_url": record["url"], "requested_url": record["requested_url"],
        "retrieved_at_utc": record["retrieved_at_utc"], "http_status": record["status"],
        "raw_file": record["raw_file"], "response_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(), "content_type": record.get("content_type"),
    }
    if record.get("candidate_evidence"):
        source["candidate_evidence"] = record["candidate_evidence"]
    if not sku_data:
        return ({**source, "structured_item_data": False, "sku_count": 0}, [])

    selectors = sku_data.get("variantSelectors") or []
    purchase = sku_data.get("purchaseInfo") or {}
    inventories = purchase.get("variantMappedInventories") or []
    inventory = {}
    for entry in inventories:
        for key in (entry.get("sku"), entry.get("merchantDefinedSkuId"), entry.get("variantId")):
            if key is not None:
                inventory[key] = entry.get("quantity")
    item = {
        **source, "structured_item_data": True,
        "item_id": sku_data.get("itemId"), "manage_number": sku_data.get("manageNumber"),
        "title": sku_data.get("title"), "axes": axis_values(selectors),
        "sku_count": len(sku_data.get("sku") or []),
        "item_price_range_jpy": purchase.get("purchaseBySellType", {}).get("normalPurchase", {}).get("price"),
        "inventory_display_setting": sku_data.get("inventory_display_setting") or sku_data.get("inventoryDisplay") or (sku_data.get("features") or {}).get("inventoryDisplay"),
        "raw_inventory_record_count": len(inventories),
    }
    rows = []
    for source_row_index, sku in enumerate(sku_data.get("sku") or []):
        sid, variant_id = sku.get("merchantDefinedSkuId"), sku.get("variantId")
        inventory_key = sid if sid in inventory else (variant_id if variant_id in inventory else None)
        quantity = inventory.get(inventory_key) if inventory_key is not None else None
        raw_price = sku.get("taxIncludedPrice")
        price = int(raw_price) if type(raw_price) in (int, float) and float(raw_price).is_integer() else None
        hidden = sku.get("hidden")
        display = item["inventory_display_setting"]
        quantity_availability = (("in_stock" if quantity > 0 else "out_of_stock")
                                 if type(quantity) in (int, float) and hidden is not True
                                 else ("unknown_hidden_sku" if hidden is True else "unknown"))
        visible_availability = "unknown_hidden_stock_display" if display == "HIDDEN_STOCK" else "unknown_not_exposed"
        values = sku.get("selectorValues") or []
        option_values = []
        for i, val in enumerate(values):
            axis = selectors[i] if i < len(selectors) else {}
            option_values.append({"axis_key": axis.get("key"), "axis_name": axis.get("name"),
                                  "value": val})
        stable_identity = hashlib.sha256(
            f"{record['url']}\n{source['sha256']}\n{source_row_index}".encode("utf-8")
        ).hexdigest()
        rows.append({
            "source_url": record["url"], "retrieved_at_utc": record["retrieved_at_utc"],
            "raw_file": record["raw_file"], "sha256": source["sha256"],
            "item_id": sku_data.get("itemId"), "manage_number": sku_data.get("manageNumber"),
            "source_row_index": source_row_index,
            "source_row_key": f"{record['url']}#sku-row-{source_row_index}-{stable_identity[:16]}",
            "sku_record_key": stable_identity,
            "source_sku_key": f"{record['url']}#{variant_id or sid or f'row-{source_row_index}'}:{source_row_index}",
            "sku_id": sid or variant_id, "merchant_defined_sku_id": sid, "variant_id": variant_id,
            "inventory_match_key": inventory_key, "option_values": option_values,
            "price_jpy": price, "price_raw_as_in_source": raw_price,
            "hidden_sku": hidden, "inventory_quantity_from_embedded_json": quantity,
            "inventory_display_setting": display,
            "visible_availability": visible_availability,
            "availability_from_embedded_quantity": quantity_availability,
            "availability_evidence": "embedded purchaseInfo.variantMappedInventories quantity; this is distinct from visible stock display",
        })
    return item, rows


def _japanese_ngrams(text: str) -> set[str]:
    normalized = re.sub(r"[^一-龥ぁ-んァ-ヴーa-z0-9]", "", (text or "").lower())
    return {normalized[i:i + 2] for i in range(max(0, len(normalized) - 1))}


def _raw_path_key(raw_file: str) -> Path:
    path = Path(raw_file)
    return (path if path.is_absolute() else ROOT / path).resolve()


def _overlap_score(left: str, right: str) -> float:
    a, b = _japanese_ngrams(left), _japanese_ngrams(right)
    return len(a & b) / max(1, len(a | b))


class Stage2Ledger:
    """Atomic request/body budget ledger shared by collector workers and runs."""
    def __init__(self, path: Path, max_http: int, max_body_bytes: int, max_success: int):
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_http = max_http
        self.max_body_bytes = max_body_bytes
        self.max_success = max_success
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in (("max_http_requests", max_http), ("max_body_bytes", max_body_bytes),
                               ("max_successful_products", max_success)):
                if data.get(key) != value:
                    raise SystemExit(f"stage2 ledger cap mismatch at {self.path}: {key}")
            data.setdefault("catalog_urls_attempted", [])
            data.setdefault("catalog_urls_successful", [])
            self._write_unlocked(data)
        else:
            data = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "max_http_requests": max_http, "max_body_bytes": max_body_bytes,
                    "max_response_body_bytes": MAX_BODY_BYTES,
                    "max_successful_products": max_success,
                    "http_requests_charged": 0, "body_bytes_charged": 0, "body_bytes_reserved": 0,
                    "product_urls_attempted": [], "successful_product_urls": [],
                    "catalog_urls_attempted": [], "catalog_urls_successful": [], "request_events": []}
            self._write_unlocked(data)

    def _write_unlocked(self, data: dict) -> None:
        fd, tmpname = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmpname, self.path)
        finally:
            if os.path.exists(tmpname):
                os.unlink(tmpname)

    def mutate(self, fn):
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            data = json.loads(self.path.read_text(encoding="utf-8"))
            result = fn(data)
            self._write_unlocked(data)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            return result

    def reserve(self, url: str, request_kind: str, product_url: str | None = None) -> int | None:
        def update(data):
            if data["http_requests_charged"] >= self.max_http:
                return None
            if data["body_bytes_charged"] + data["body_bytes_reserved"] + MAX_BODY_BYTES > self.max_body_bytes:
                return None
            if request_kind == "product" and len(data["successful_product_urls"]) >= self.max_success:
                return None
            data["http_requests_charged"] += 1
            data["body_bytes_reserved"] += MAX_BODY_BYTES
            if request_kind == "product" and product_url and product_url not in data["product_urls_attempted"]:
                data["product_urls_attempted"].append(product_url)
            if request_kind == "catalog" and url not in data["catalog_urls_attempted"]:
                data["catalog_urls_attempted"].append(url)
            event_id = len(data["request_events"]) + 1
            data["request_events"].append({"request_id": event_id, "request_kind": request_kind,
                                           "product_url": product_url, "requested_url": url,
                                           "started_at_utc": datetime.now(timezone.utc).isoformat(),
                                           "reserved_body_bytes": MAX_BODY_BYTES, "status": "reserved"})
            return event_id
        return self.mutate(update)

    def finish(self, event_id: int, body_bytes: int, status: int | None, body_sha256: str | None,
               error: str | None = None) -> None:
        def update(data):
            event = data["request_events"][event_id - 1]
            if event.get("status") != "reserved":
                return
            actual = min(max(0, int(body_bytes)), MAX_BODY_BYTES)
            data["body_bytes_reserved"] -= MAX_BODY_BYTES
            data["body_bytes_charged"] += actual
            event.update({"finished_at_utc": datetime.now(timezone.utc).isoformat(),
                          "reserved_body_bytes": 0, "response_body_bytes_charged": actual,
                          "http_status": status, "body_sha256": body_sha256,
                          "status": "error" if error else "complete", "error": error})
        self.mutate(update)

    def mark_success(self, url: str) -> None:
        def update(data):
            if url not in data["successful_product_urls"]:
                data["successful_product_urls"].append(url)
        self.mutate(update)

    def mark_catalog_success(self, url: str) -> None:
        def update(data):
            if url not in data["catalog_urls_successful"]:
                data["catalog_urls_successful"].append(url)
        self.mutate(update)

    def snapshot(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Stage2FetchError(Exception):
    pass


def stage2_fetch(url: str, evidence: dict, ledger: Stage2Ledger, request_kind: str = "product") -> dict:
    current = url
    redirects = 0
    first_retrieved = datetime.now(timezone.utc).isoformat()
    opener = build_opener(NoRedirect())
    while True:
        event_id = ledger.reserve(current, request_kind if redirects == 0 else "redirect",
                                  url if request_kind == "product" and redirects == 0 else None)
        if event_id is None:
            raise Stage2FetchError("stage2 request/body budget exhausted before request")
        request = Request(current, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        response = None
        finished = False
        body_bytes = 0
        status = None
        body_sha = None
        try:
            try:
                response = opener.open(request, timeout=35)
            except HTTPError as exc:
                response = exc
            status = getattr(response, "status", None) or getattr(response, "code", None)
            ctype = response.headers.get("Content-Type", "")
            final_url = response.geturl() if hasattr(response, "geturl") else current
            location = response.headers.get("Location") if status and 300 <= status < 400 else None
            content_length = response.headers.get("Content-Length")
            try:
                declared_length = int(content_length) if content_length else None
            except ValueError:
                declared_length = None
            if declared_length and declared_length > MAX_BODY_BYTES:
                response.close()
                ledger.finish(event_id, 0, status, None, "response Content-Length exceeds 8 MiB cap")
                finished = True
                raise Stage2FetchError("response Content-Length exceeds 8 MiB cap")
            body = response.read(MAX_BODY_BYTES + 1)
            too_large = len(body) > MAX_BODY_BYTES
            body_bytes = min(len(body), MAX_BODY_BYTES)
            body_sha = hashlib.sha256(body[:MAX_BODY_BYTES]).hexdigest() if body else hashlib.sha256(b"").hexdigest()
            ledger.finish(event_id, body_bytes, status, body_sha,
                          "response exceeded 8 MiB capture cap" if too_large else None)
            finished = True
            if too_large:
                raise Stage2FetchError("response exceeded 8 MiB capture cap")
            if location:
                redirects += 1
                if redirects > 10:
                    raise Stage2FetchError("redirect chain exceeded 10 hops")
                next_url = urljoin(current, location)
                if not (valid_item_url(next_url) if request_kind == "product" else valid_category_url(next_url)):
                    raise Stage2FetchError(f"redirect outside WEIMALL {request_kind} path: {next_url}")
                current = next_url
                continue
            if not (status and 200 <= status < 300):
                raise Stage2FetchError(f"HTTP status {status}")
            if not (valid_item_url(final_url) if request_kind == "product" else valid_category_url(final_url)):
                raise Stage2FetchError(f"final URL outside WEIMALL {request_kind} path: {final_url}")
            return {"requested_url": url, "url": final_url, "retrieved_at_utc": first_retrieved,
                    "status": status, "content_type": ctype, "raw": body,
                    "candidate_evidence": evidence or None, "http_redirect_count": redirects}
        except Stage2FetchError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            if not finished:
                ledger.finish(event_id, body_bytes, status, body_sha, f"{type(exc).__name__}: {exc}")
            raise Stage2FetchError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            if response is not None:
                response.close()


def extract_direct_candidates(html: str, page_url: str, raw_path: str, raw_sha256: str) -> list[dict]:
    from extract_rakuten_links import ProductLinks
    parser = ProductLinks(page_url)
    parser.feed(html)
    found, seen = [], set()
    for row in parser.rows:
        if row["url"] in seen:
            continue
        seen.add(row["url"])
        found.append({**row, "anchor_text": " ".join(row["anchor_text"].split()),
                      "image_alt": list(dict.fromkeys(x.strip() for x in row["image_alt"] if x.strip())),
                      "evidence_source_url": page_url, "evidence_raw_file": raw_path,
                      "evidence_html_sha256": raw_sha256,
                      "evidence_kind": "observed_direct_same_store_anchor_in_fresh_item_html"})
    return found


def load_source_pages(source_dirs: list[Path]) -> tuple[list[dict], dict[Path, dict]]:
    pages, metadata_by_raw = [], {}
    for source_dir in source_dirs:
        for meta_path in source_dir.rglob("products.jsonl"):
            try:
                for line in meta_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        row = json.loads(line)
                        if row.get("source_url"):
                            if row.get("raw_file"):
                                metadata_by_raw[_raw_path_key(row["raw_file"])] = row
            except (OSError, json.JSONDecodeError):
                continue
        for meta_path in source_dir.rglob("catalog_manifest.jsonl"):
            try:
                for line in meta_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        row = json.loads(line)
                        if row.get("category_url"):
                            if row.get("raw_file"):
                                metadata_by_raw[_raw_path_key(row["raw_file"])] = row
            except (OSError, json.JSONDecodeError):
                continue
        for html_path in source_dir.rglob("*.html"):
            pages.append({"path": html_path, "source_dir": source_dir})
    return pages, metadata_by_raw


def stage2_candidates(source_dirs: list[Path], excluded: set[str], au_titles: list[str]) -> list[dict]:
    pages, metadata_by_raw = load_source_pages(source_dirs)
    by_source: dict[str, list[dict]] = {}
    for page in pages:
        html_path = page["path"]
        raw = html_path.read_bytes()
        raw_sha = hashlib.sha256(raw).hexdigest()
        meta = metadata_by_raw.get(html_path.resolve())
        if not meta:
            continue
        page_url = meta.get("source_url") or meta.get("category_url")
        if not valid_store_page_url(page_url):
            continue
        decoded = decoded_html(raw, meta.get("content_type", ""))
        raw_reference = html_path.resolve().relative_to(ROOT.resolve()).as_posix() if html_path.resolve().is_relative_to(ROOT.resolve()) else str(html_path.resolve())
        anchors = extract_direct_candidates(decoded, page_url, raw_reference, raw_sha)
        source_title = meta.get("title", "")
        rows = []
        for row in anchors:
            if row["url"] in excluded:
                continue
            anchor = " ".join([row.get("anchor_text", ""), " ".join(row.get("image_alt", [])), urlparse(row["url"]).path])
            score = max((_overlap_score(anchor, title) for title in au_titles), default=0.0)
            source_score = max((_overlap_score(source_title, title) for title in au_titles), default=0.0)
            row["au_title_link_similarity"] = round(score, 4)
            row["au_title_source_similarity"] = round(source_score, 4)
            rows.append(row)
        if rows:
            by_source[page_url] = rows
    # Prefer evidence with strong overlap to real au catalog titles, while
    # retaining a round-robin path through all observed source pages.
    for rows in by_source.values():
        rows.sort(key=lambda r: (-max(r["au_title_link_similarity"], r["au_title_source_similarity"] * 0.6), r["url"]))
    queues = {key: list(rows) for key, rows in sorted(by_source.items())}
    result, seen = [], set()
    while queues:
        for key in list(queues):
            rows = queues[key]
            while rows:
                row = rows.pop(0)
                if row["url"] not in seen:
                    result.append(row)
                    seen.add(row["url"])
                    break
            if not rows:
                del queues[key]
    result.sort(key=lambda r: (-max(r["au_title_link_similarity"], r["au_title_source_similarity"] * 0.6), r["url"]))
    return result


def reparse_main() -> None:
    ap = argparse.ArgumentParser(description="Reparse already captured Rakuten raw pages without any network requests")
    ap.add_argument("--reparse-source", type=Path, required=True, help="stage1 aggregate directory containing products.jsonl")
    ap.add_argument("--out-dir", type=Path, required=True, help="new exclusive normalized output directory")
    args = ap.parse_args()
    source_dir = args.reparse_source if args.reparse_source.is_absolute() else ROOT / args.reparse_source
    out = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty reparse output directory: {out}")
    source_products = source_dir / "products.jsonl"
    if not source_products.is_file():
        raise SystemExit(f"stage1 products.jsonl missing: {source_products}")
    out.mkdir(parents=True, exist_ok=True)
    products, skus, errors = [], [], []
    rows = [json.loads(line) for line in source_products.read_text(encoding="utf-8").splitlines() if line.strip()]
    for old in rows:
        raw_path = ROOT / old["raw_file"]
        try:
            raw = raw_path.read_bytes()
            raw_sha = hashlib.sha256(raw).hexdigest()
            if raw_sha != old.get("sha256"):
                raise ValueError("captured raw SHA256 differs from original product row")
            record = {"url": old["source_url"], "requested_url": old.get("requested_url", old["source_url"]),
                      "retrieved_at_utc": old["retrieved_at_utc"], "status": old.get("http_status", 200),
                      "content_type": old.get("content_type", ""), "raw_file": old["raw_file"]}
            product, sku_rows = parse_page(record, raw)
            products.append(product)
            skus.extend(sku_rows)
        except (OSError, KeyError, ValueError, TypeError) as exc:
            errors.append({"source_url": old.get("source_url"), "raw_file": old.get("raw_file"),
                           "error": f"{type(exc).__name__}: {exc}"})
    (out / "products.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in products), encoding="utf-8")
    (out / "skus.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in skus), encoding="utf-8")
    duplicate_merchant_ids = {}
    counts = {}
    for row in skus:
        key = (row.get("source_url"), row.get("merchant_defined_sku_id"))
        counts[key] = counts.get(key, 0) + 1
    for (url, merchant_id), count in counts.items():
        if merchant_id is not None and count > 1:
            duplicate_merchant_ids[f"{url}|{merchant_id}"] = count
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "network_requests": 0,
               "source_directory": source_dir.relative_to(ROOT).as_posix() if source_dir.is_relative_to(ROOT) else str(source_dir),
               "source_rows": len(rows), "reparsed_product_rows": len(products), "sku_rows": len(skus),
               "raw_files_referenced_without_copy": True, "stable_sku_identity": "sha256(source_url + raw_sha256 + source SKU array index)",
               "sku_rows_with_duplicate_merchant_defined_sku_id": sum(v for v in duplicate_merchant_ids.values()),
               "duplicate_merchant_ids_by_source_product": duplicate_merchant_ids, "errors": errors}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"products": len(products), "skus": len(skus), "errors": len(errors),
                      "duplicate_merchant_id_groups": len(duplicate_merchant_ids), "network_requests": 0,
                      "output": str(out)}, ensure_ascii=False))


def expanded_main() -> None:
    ap = argparse.ArgumentParser(description="Capped recursive collector over observed WEIMALL same-store item links")
    ap.add_argument("--source-html-dir", type=Path, action="append", required=True)
    ap.add_argument("--au-products", type=Path, required=True, help="real au API product JSONL used only for candidate priority")
    ap.add_argument("--ledger", type=Path, required=True, help="shared cumulative stage2 atomic budget ledger")
    ap.add_argument("--skip-urls-file", type=Path, help="JSONL of URLs already attempted in immutable stage1")
    ap.add_argument("--out-dir", type=Path, required=True, help="new exclusive batch directory")
    ap.add_argument("--max-success", type=int, default=100, help="per-batch successful item pages")
    ap.add_argument("--max-success-total", type=int, default=908)
    ap.add_argument("--max-http-total", type=int, default=1500)
    ap.add_argument("--max-body-total", type=int, default=2 * 1024 * 1024 * 1024)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()
    if not 1 <= args.max_success <= 100 or not 1 <= args.max_success_total <= 908:
        raise SystemExit("stage2 success caps must be 1..100 per batch and <=908 cumulative")
    if args.max_http_total != 1500 or args.max_body_total != 2 * 1024 * 1024 * 1024 or args.concurrency != 2:
        raise SystemExit("stage2 caps are fixed at 1500 HTTP, 2 GiB response bodies and concurrency 2")
    out = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    raw_dir = out / "raw"
    raw_dir.mkdir()
    ledger_path = args.ledger if args.ledger.is_absolute() else ROOT / args.ledger
    ledger = Stage2Ledger(ledger_path, args.max_http_total, args.max_body_total, args.max_success_total)
    au_rows = [json.loads(line) for line in args.au_products.read_text(encoding="utf-8").splitlines() if line.strip()]
    au_titles = [row.get("item_title", "") for row in au_rows if row.get("item_title")]
    state = ledger.snapshot()
    excluded = set(state["product_urls_attempted"])
    if args.skip_urls_file:
        skip_path = args.skip_urls_file if args.skip_urls_file.is_absolute() else ROOT / args.skip_urls_file
        excluded.update(row["url"] for row in load_urls(skip_path))
    candidates = stage2_candidates(args.source_html_dir, excluded, au_titles)
    products, skus, errors = [], [], []
    attempted_before = set(excluded)
    max_this_batch = min(args.max_success, args.max_success_total - len(state["successful_product_urls"]))
    queue = list(candidates)
    sequence = 0
    in_flight = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        while (queue or in_flight) and len(products) < max_this_batch:
            while queue and len(in_flight) < 2 and len(products) + len(in_flight) < max_this_batch:
                candidate = queue.pop(0)
                url = candidate["url"]
                if url in attempted_before:
                    continue
                attempted_before.add(url)
                sequence += 1
                future = pool.submit(stage2_fetch, url, candidate, ledger)
                in_flight[future] = candidate
            if not in_flight:
                break
            done, _ = concurrent.futures.wait(in_flight, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                candidate = in_flight.pop(future)
                url = candidate["url"]
                try:
                    record = future.result()
                except Stage2FetchError as exc:
                    errors.append({"url": url, "error": str(exc), "candidate_evidence": candidate})
                    continue
                if record["url"] in ledger.snapshot()["successful_product_urls"]:
                    errors.append({"url": url, "error": f"duplicate final product URL after redirect: {record['url']}"})
                    continue
                raw = record.pop("raw")
                slug = urlparse(record["url"]).path.strip("/").split("/")[-1]
                filename = f"{slug}-{hashlib.sha256(record['url'].encode()).hexdigest()[:8]}.html"
                raw_path = raw_dir / filename
                raw_path.write_bytes(raw)
                record["raw_file"] = raw_path.relative_to(ROOT).as_posix()
                record["response_bytes"] = len(raw)
                record["sha256"] = hashlib.sha256(raw).hexdigest()
                product, sku_rows = parse_page(record, raw)
                products.append(product)
                skus.extend(sku_rows)
                ledger.mark_success(record["url"])
                html = decoded_html(raw, record.get("content_type", ""))
                discoveries = extract_direct_candidates(html, record["url"], record["raw_file"], record["sha256"])
                for row in discoveries:
                    if row["url"] in attempted_before or any(item["url"] == row["url"] for item in queue):
                        continue
                    link = " ".join([row.get("anchor_text", ""), " ".join(row.get("image_alt", [])), urlparse(row["url"]).path])
                    row["au_title_link_similarity"] = round(max((_overlap_score(link, title) for title in au_titles), default=0.0), 4)
                    row["au_title_source_similarity"] = round(max((_overlap_score(product.get("title", ""), title) for title in au_titles), default=0.0), 4)
                    queue.append(row)
                queue.sort(key=lambda r: (-max(r.get("au_title_link_similarity", 0), r.get("au_title_source_similarity", 0) * 0.6), r["url"]))
    (out / "products.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in products), encoding="utf-8")
    (out / "skus.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in skus), encoding="utf-8")
    (out / "errors.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in errors), encoding="utf-8")
    snapshot = ledger.snapshot()
    (out / "request-ledger-snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
               "scope": {"origin": "https://item.rakuten.co.jp", "store_path": "/weimall/<observed-item>/",
                         "discovery": "Observed direct same-store item links in fresh raw HTML only; no slug guessing or external search.",
                         "concurrency": 2, "product_success_rows_this_batch": len(products),
                         "product_url_attempts_total_stage2": len(snapshot["product_urls_attempted"]),
                         "http_requests_charged_total_stage2": snapshot["http_requests_charged"],
                         "body_bytes_charged_total_stage2": snapshot["body_bytes_charged"],
                         "remaining_http_requests": args.max_http_total - snapshot["http_requests_charged"],
                         "remaining_body_bytes": args.max_body_total - snapshot["body_bytes_charged"] - snapshot["body_bytes_reserved"],
                         "remaining_successful_products": args.max_success_total - len(snapshot["successful_product_urls"]),
                         "candidate_urls_ready_from_current_sources": len(candidates),
                         "output_directory": out.relative_to(ROOT).as_posix()},
               "metrics": {"structured_products": sum(p.get("structured_item_data", False) for p in products),
                           "products_with_skus": sum(p.get("sku_count", 0) > 0 for p in products),
                           "sku_rows": len(skus), "priced_sku_rows": sum(s.get("price_jpy") is not None for s in skus)},
               "errors": errors}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"products": len(products), "skus": len(skus), "errors": len(errors),
                      "cumulative_stage2_products": len(snapshot["successful_product_urls"]),
                      "cumulative_stage2_http": snapshot["http_requests_charged"],
                      "cumulative_stage2_body_bytes": snapshot["body_bytes_charged"],
                      "candidate_urls_ready": len(queue), "output": str(out)}, ensure_ascii=False))


def catalog_main() -> None:
    ap = argparse.ArgumentParser(description="GET observed same-store /c/ category URLs into catalograw only")
    ap.add_argument("--category-urls-file", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--max-http-total", type=int, default=1500)
    ap.add_argument("--max-body-total", type=int, default=2 * 1024 * 1024 * 1024)
    if "--catalog-stage2" in sys.argv:
        sys.argv.remove("--catalog-stage2")
    args = ap.parse_args()
    if not 1 <= args.max_pages <= 100 or args.max_http_total != 1500 or args.max_body_total != 2 * 1024 * 1024 * 1024:
        raise SystemExit("category batches must be 1..100 pages and share the fixed 1500 HTTP / 2 GiB stage2 caps")
    out = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty category output: {out}")
    out.mkdir(parents=True, exist_ok=True)
    raw_dir = out / "catalograw"
    raw_dir.mkdir()
    ledger_path = args.ledger if args.ledger.is_absolute() else ROOT / args.ledger
    ledger = Stage2Ledger(ledger_path, args.max_http_total, args.max_body_total, 908)
    candidates = load_urls(args.category_urls_file, allow_category=True)
    state = ledger.snapshot()
    already = set(state["catalog_urls_attempted"])
    candidates = [row for row in candidates if row["url"] not in already][:args.max_pages]
    manifest, errors = [], []
    in_flight = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        iterator = iter(candidates)
        while True:
            while len(in_flight) < 2:
                try:
                    candidate = next(iterator)
                except StopIteration:
                    break
                future = pool.submit(stage2_fetch, candidate["url"], candidate.get("candidate_evidence") or {}, ledger, "catalog")
                in_flight[future] = candidate
            if not in_flight:
                break
            done, _ = concurrent.futures.wait(in_flight, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                candidate = in_flight.pop(future)
                try:
                    record = future.result()
                except Stage2FetchError as exc:
                    errors.append({"category_url": candidate["url"], "error": str(exc),
                                   "candidate_evidence": candidate.get("candidate_evidence")})
                    continue
                raw = record.pop("raw")
                slug = urlparse(record["url"]).path.strip("/").replace("/", "-") or "category"
                filename = f"{slug}-{hashlib.sha256(record['url'].encode()).hexdigest()[:8]}.html"
                raw_path = raw_dir / filename
                raw_path.write_bytes(raw)
                row = {"category_url": record["url"], "requested_url": record["requested_url"],
                       "retrieved_at_utc": record["retrieved_at_utc"], "http_status": record["status"],
                       "content_type": record["content_type"], "raw_file": raw_path.relative_to(ROOT).as_posix(),
                       "response_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                       "http_redirect_count": record.get("http_redirect_count", 0),
                       "candidate_evidence": candidate.get("candidate_evidence")}
                manifest.append(row)
                ledger.mark_catalog_success(row["category_url"])
    (out / "catalog_manifest.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in manifest), encoding="utf-8")
    (out / "catalog_errors.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in errors), encoding="utf-8")
    snapshot = ledger.snapshot()
    new_catalog_attempts = sorted(set(snapshot["catalog_urls_attempted"]) - already)
    (out / "catalog_attempted_urls.jsonl").write_text(
        "".join(json.dumps({"url":x},ensure_ascii=False)+"\n" for x in new_catalog_attempts),encoding="utf-8")
    (out / "request-ledger-snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "product_rows": 0, "sku_rows": 0,
               "category_pages_requested": len(candidates), "category_pages_successful": len(manifest),
               "http_requests_stage2_total": snapshot["http_requests_charged"],
               "body_bytes_stage2_total": snapshot["body_bytes_charged"],
               "catalog_urls_attempted_stage2": len(snapshot["catalog_urls_attempted"]),
               "product_urls_attempted_stage2": len(snapshot["product_urls_attempted"]),
               "remaining_http_requests": args.max_http_total - snapshot["http_requests_charged"],
               "remaining_body_bytes": args.max_body_total - snapshot["body_bytes_charged"] - snapshot["body_bytes_reserved"],
               "errors": errors}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"category_pages": len(manifest), "errors": len(errors),
                      "cumulative_http": snapshot["http_requests_charged"],
                      "cumulative_products": len(snapshot["successful_product_urls"]),
                      "cumulative_body_bytes": snapshot["body_bytes_charged"], "output": str(out)}, ensure_ascii=False))


def main() -> None:
    if "--reparse-source" in sys.argv:
        reparse_main()
        return
    if "--stage2" in sys.argv:
        sys.argv.remove("--stage2")
        expanded_main()
        return
    if "--catalog-stage2" in sys.argv:
        catalog_main()
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls-file", type=Path, default=DEFAULT_KNOWN_PRODUCTS,
                    help="JSONL with source_url/url fields, or one observed URL per line")
    ap.add_argument("--out-dir", type=Path, required=True, help="new, exclusive output directory")
    ap.add_argument("--max-products", type=int, default=20, help="total product-page ceiling, including prior runs")
    ap.add_argument("--max-http", type=int, default=40, help="includes failed requests and redirects")
    ap.add_argument("--prior-products", type=int, default=0, help="pages already consumed under the overall ceiling")
    ap.add_argument("--prior-http", type=int, default=0, help="HTTP requests already consumed under the overall ceiling")
    ap.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args()
    if not 1 <= args.max_products <= 100 or not 1 <= args.max_http <= 120 or args.prior_products < 0 or args.prior_http < 0:
        raise SystemExit("caps must be within the authorized 1..100 products and 1..120 HTTP requests")
    out = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    page_remaining = args.max_products - args.prior_products
    request_remaining = args.max_http - args.prior_http
    if page_remaining <= 0 or request_remaining <= 0:
        raise SystemExit("the cumulative product-page or HTTP ceiling has already been consumed")
    urls = load_urls(args.urls_file)[:page_remaining]
    product_attempts = 0
    redirects = BoundedRedirect(lambda: args.prior_http + product_attempts + redirects.count >= args.max_http)
    opener = build_opener(redirects)
    records, errors = [], []
    for i, url_record in enumerate(urls):
        requested_url = url_record["url"]
        if args.prior_http + product_attempts + redirects.count >= args.max_http:
            errors.append({"url": requested_url, "error": "request cap reached before fetch"})
            break
        product_attempts += 1
        retrieved = datetime.now(timezone.utc).isoformat()
        req = Request(requested_url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        try:
            with opener.open(req, timeout=30) as response:
                raw = response.read(MAX_BODY_BYTES + 1)
                if len(raw) > MAX_BODY_BYTES:
                    errors.append({"url": requested_url, "error": "response exceeded 8 MiB capture cap"})
                    continue
                final_url = response.geturl()
                if not valid_item_url(final_url):
                    errors.append({"url": requested_url, "error": f"final URL outside item-page scope: {final_url}"})
                    continue
                slug = urlparse(final_url).path.strip("/").split("/")[-1]
                filename = f"{slug}-{hashlib.sha256(final_url.encode()).hexdigest()[:8]}.html"
                dest = out / filename
                dest.write_bytes(raw)
                records.append({"requested_url": requested_url, "url": final_url,
                                "candidate_evidence": url_record.get("candidate_evidence"),
                                "retrieved_at_utc": retrieved, "raw_file": dest.relative_to(ROOT).as_posix(),
                                "raw": raw, "status": response.status,
                                "content_type": response.headers.get("Content-Type", "")})
        except (HTTPError, URLError, TimeoutError, RuntimeError, OSError) as exc:
            errors.append({"url": requested_url, "error": f"{type(exc).__name__}: {exc}"})
        if i + 1 < len(urls) and args.prior_http + product_attempts + redirects.count < args.max_http:
            time.sleep(args.delay)

    products, skus = [], []
    for record in records:
        raw = record.pop("raw")
        product, sku_rows = parse_page(record, raw)
        products.append(product)
        skus.extend(sku_rows)
    (out / "products.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in products), encoding="utf-8")
    (out / "skus.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in skus), encoding="utf-8")
    summary = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {"origin": "https://item.rakuten.co.jp", "read_only": True,
                  "actions": "GET public item pages only; no interaction endpoints",
                  "requested_product_url_count": len(urls), "successful_product_page_count": len(records),
                  "max_product_pages": args.max_products, "http_request_count_including_redirects": product_attempts + redirects.count,
                  "max_http_requests_including_redirects": args.max_http,
                  "cumulative_product_pages_including_prior": args.prior_products + len(records),
                  "cumulative_http_requests_including_prior": args.prior_http + product_attempts + redirects.count,
                  "product_get_attempts_including_failures": product_attempts, "redirect_hops": redirects.count,
                  "output_directory": out.relative_to(ROOT).as_posix() if out.is_relative_to(ROOT) else str(out),
                  "products_jsonl": "products.jsonl", "skus_jsonl": "skus.jsonl"},
        "metrics": {"product_rows": len(products), "structured_products": sum(p.get("structured_item_data", False) for p in products),
                    "products_with_skus": sum(p.get("sku_count", 0) > 0 for p in products), "sku_rows": len(skus),
                    "priced_sku_rows": sum(s.get("price_jpy") is not None for s in skus),
                    "matched_inventory_rows": sum(s.get("inventory_match_key") is not None for s in skus),
                    "sku_rows_by_manage_number": {p.get("manage_number"): p.get("sku_count", 0) for p in products}},
        "availability_note": "visible_availability remains unknown when not exposed; embedded quantity-derived availability is stored separately.",
        "errors": errors,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"requested_urls": len(urls), "products": len(products), "structured_products": summary["metrics"]["structured_products"],
                      "skus": len(skus), "errors": len(errors), "http_requests": product_attempts + redirects.count,
                      "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
