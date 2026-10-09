#!/usr/bin/env python3
"""Collect public WEIMALL Rakuten item-page SKU data from saved same-store links.

The seed is a user-provided, already-saved item page. This script follows only
direct product links in that saved page, with hard caps of 20 product URLs and
40 HTTP requests. It does not access cart, account, or checkout endpoints.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / ".lab-output/sku-observation-20261009/rakuten.html"
OUT = ROOT / ".lab-output/sku-real-rakuten-20261009"
SUMMARY = ROOT / "experiments/sku-matching/rakuten-collection-summary.json"
MAX_PRODUCT_PAGES = 20
MAX_HTTP_REQUESTS = 40
USER_AGENT = "SearchingExperiment-SKU-observation/1.0 (read-only public product data)"


class StoreProductLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.slugs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        parsed = urlparse(href)
        if (parsed.scheme == "https" and parsed.netloc == "item.rakuten.co.jp"
                and parsed.path.startswith("/weimall/") and not parsed.query):
            slug = parsed.path.removeprefix("/weimall/").strip("/")
            if slug and slug != "c" and not slug.startswith("c/") and slug not in self.slugs:
                self.slugs.append(slug)


class BoundedRedirect(HTTPRedirectHandler):
    def __init__(self) -> None:
        self.requests = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.requests += 1
        if self.requests + 1 > MAX_HTTP_REQUESTS:
            raise RuntimeError("HTTP request cap reached during redirect")
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.netloc != "item.rakuten.co.jp" or not parsed.path.startswith("/weimall/"):
            raise RuntimeError(f"redirect outside authorized store origin/path: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def decoded_html(raw: bytes, content_type: str = "") -> str:
    enc = None
    match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.I)
    if match:
        enc = match.group(1)
    if not enc:
        head = raw[:2048].decode("ascii", "ignore")
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


def provenance(url: str, retrieved: str, raw_path: str, raw: bytes, status: int) -> dict:
    return {
        "source_url": url,
        "retrieved_at_utc": retrieved,
        "http_status": status,
        "raw_file": raw_path,
        "response_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main() -> None:
    if not SEED.is_file():
        raise SystemExit(f"saved seed page missing: {SEED}")
    OUT.mkdir(parents=True, exist_ok=True)
    if any(OUT.glob("*.html")) or SUMMARY.exists():
        raise SystemExit(f"refusing to overwrite existing Rakuten collection: {OUT}")
    seed_raw = SEED.read_bytes()
    seed_html = decoded_html(seed_raw)
    parser = StoreProductLinks()
    parser.feed(seed_html)
    product_urls = ["https://item.rakuten.co.jp/weimall/ct0/"]
    product_urls.extend(f"https://item.rakuten.co.jp/weimall/{slug}/" for slug in parser.slugs if slug != "ct0")
    product_urls = product_urls[:MAX_PRODUCT_PAGES]

    seed_dest = OUT / "ct0.html"
    if SEED.resolve() != seed_dest.resolve():
        shutil.copyfile(SEED, seed_dest)
    saved: list[dict] = [{
        "url": product_urls[0],
        "retrieved_at_utc": "2026-10-09T12:52:21.994296+00:00",
        "raw_file": seed_dest.relative_to(ROOT).as_posix(),
        "raw": seed_raw,
        "status": 200,
        "content_type": "text/html; charset=EUC-JP",
    }]

    redirects = BoundedRedirect()
    opener = build_opener(redirects)
    errors: list[dict] = []
    direct_urls = product_urls[1:]
    request_attempts = 0
    # Count product requests and redirect hops together; leave capacity for redirects.
    for index, url in enumerate(direct_urls, 1):
        if len(direct_urls[:index]) + redirects.requests >= MAX_HTTP_REQUESTS:
            errors.append({"url": url, "error": "request cap reached before fetch"})
            break
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        retrieved = datetime.now(timezone.utc).isoformat()
        request_attempts += 1
        try:
            with opener.open(request, timeout=25) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    errors.append({"url": url, "error": "response exceeded 8 MiB capture cap"})
                    continue
                status = response.status
                final_url = response.geturl()
                ctype = response.headers.get("Content-Type", "")
                if final_url != url:
                    url = final_url
                filename = urlparse(url).path.rstrip("/").split("/")[-1] + ".html"
                dest = OUT / filename
                dest.write_bytes(raw)
                saved.append({"url": url, "retrieved_at_utc": retrieved,
                              "raw_file": dest.relative_to(ROOT).as_posix(),
                              "raw": raw, "status": status, "content_type": ctype})
        except (HTTPError, URLError, TimeoutError, RuntimeError, OSError) as exc:
            errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
        if index < len(direct_urls):
            time.sleep(0.5)

    products: list[dict] = []
    skus: list[dict] = []
    for record in saved:
        raw = record.pop("raw")
        html = decoded_html(raw, record.get("content_type", ""))
        data = app_data(html)
        sku_data = (((data or {}).get("api") or {}).get("data") or {}).get("itemInfoSku") or {}
        source = provenance(record["url"], record["retrieved_at_utc"], record["raw_file"], raw, record["status"])
        if not sku_data:
            products.append({**source, "structured_item_data": False})
            continue
        inventory = {item.get("sku"): item.get("quantity")
                     for item in (sku_data.get("purchaseInfo", {}).get("variantMappedInventories") or [])}
        selectors = sku_data.get("variantSelectors") or []
        axes = [{"key": axis.get("key"), "values": [v.get("label", v.get("value")) for v in axis.get("values", [])]}
                for axis in selectors]
        item = {
            **source,
            "structured_item_data": True,
            "item_id": sku_data.get("itemId"),
            "manage_number": sku_data.get("manageNumber"),
            "title": sku_data.get("title"),
            "axes": axes,
            "sku_count": len(sku_data.get("sku") or []),
            "item_price_range_jpy": sku_data.get("purchaseInfo", {}).get("purchaseBySellType", {}).get("normalPurchase", {}).get("price"),
            "inventory_display_setting": sku_data.get("features", {}).get("inventoryDisplay"),
        }
        products.append(item)
        for sku in sku_data.get("sku") or []:
            sid = sku.get("merchantDefinedSkuId")
            variant_id = sku.get("variantId")
            if sid in inventory:
                inventory_match_key = sid
            elif variant_id in inventory:
                inventory_match_key = variant_id
            else:
                inventory_match_key = None
            quantity = inventory.get(inventory_match_key) if inventory_match_key else None
            raw_price = sku.get("taxIncludedPrice")
            price_jpy = int(raw_price) if isinstance(raw_price, (int, float)) and float(raw_price).is_integer() else None
            hidden_sku = sku.get("hidden")
            skus.append({
                "source_url": record["url"],
                "retrieved_at_utc": record["retrieved_at_utc"],
                "raw_file": record["raw_file"],
                "sha256": source["sha256"],
                "item_id": sku_data.get("itemId"),
                "manage_number": sku_data.get("manageNumber"),
                "sku_id": sid or variant_id,
                "variant_id": variant_id,
                "inventory_match_key": inventory_match_key,
                "option_values": {axis.get("key", f"axis_{i+1}"): value
                                  for i, (axis, value) in enumerate(zip(selectors, sku.get("selectorValues") or []))},
                "price_jpy": price_jpy,
                "price_raw_as_in_source": raw_price,
                "hidden_sku": hidden_sku,
                "inventory_quantity_from_embedded_json": quantity,
                "visible_availability": "unknown_hidden_stock_display" if item.get("features", {}).get("inventoryDisplay") == "HIDDEN_STOCK" else "unknown_not_exposed",
                "availability_from_embedded_quantity": ("in_stock" if quantity > 0 else "out_of_stock") if isinstance(quantity, (int, float)) and hidden_sku is not True else ("unknown_hidden_sku" if hidden_sku is True else "unknown"),
                "availability_evidence": "purchaseInfo.variantMappedInventories.quantity; item-level inventoryDisplay is stored separately and may be HIDDEN_STOCK",
            })

    product_table_path = OUT / "products.jsonl"
    sku_table_path = OUT / "skus.jsonl"
    product_table_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in products), encoding="utf-8")
    sku_table_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in skus), encoding="utf-8")
    products_with_skus = [row for row in products if row.get("structured_item_data")]
    summary = {
        "retrieved_date_utc": datetime.now(timezone.utc).date().isoformat(),
        "scope": {
            "store": "WEIMALL official Rakuten store",
            "origin": "https://item.rakuten.co.jp",
            "seed_url": "https://item.rakuten.co.jp/weimall/ct0/",
            "discovery": "Direct same-store item links found in saved user-provided seed HTML; category links were excluded.",
            "read_only": True,
            "actions": "Public HTML GET only. No cart, account, checkout, or page interaction requests.",
            "product_page_limit": MAX_PRODUCT_PAGES,
            "http_request_limit": MAX_HTTP_REQUESTS,
            "successful_product_pages": len(saved),
            "http_request_count": request_attempts + redirects.requests,
            "request_count_note": "Counts product GETs and redirect hops; saved seed HTML was copied from the earlier user-provided observation and is not fetched again.",
            "raw_directory": OUT.relative_to(ROOT).as_posix(),
            "product_table": product_table_path.relative_to(ROOT).as_posix(),
            "sku_table": sku_table_path.relative_to(ROOT).as_posix(),
        },
        "availability_note": "Rakuten's embedded item JSON includes variantMappedInventories quantities, but some pages indicate HIDDEN_STOCK. The dataset labels quantities as embedded JSON data; it derives in_stock/out_of_stock only from a matched quantity when the SKU hidden flag is false.",
        "metrics": {
            "product_rows": len(products),
            "structured_product_rows": len(products_with_skus),
            "products_with_nonempty_sku_matrices": sum(row.get("sku_count", 0) > 0 for row in products_with_skus),
            "sku_rows": len(skus),
            "sku_rows_with_integer_jpy_price": sum(row.get("price_jpy") is not None for row in skus),
            "sku_rows_with_matched_inventory_quantity": sum(row.get("inventory_match_key") is not None for row in skus),
            "sku_rows_using_variant_id_inventory_fallback": sum(
                row.get("inventory_match_key") == row.get("variant_id")
                and row.get("inventory_match_key") != row.get("sku_id")
                for row in skus
            ),
            "distinct_option_axes": sorted({axis.get("key") for row in products_with_skus for axis in row.get("axes", []) if axis.get("key")}),
            "sku_rows_by_product": {row.get("manage_number"): row.get("sku_count", 0) for row in products_with_skus},
        },
        "errors": errors,
    }
    out_file = ROOT / "experiments/sku-matching/rakuten-collection-summary.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"products": len(products), "structured_products": sum(bool(x.get("structured_item_data")) for x in products),
                      "skus": len(skus), "errors": len(errors), "http_request_count": summary["scope"]["http_request_count"],
                      "summary": out_file.relative_to(ROOT).as_posix()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
