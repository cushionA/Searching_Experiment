#!/usr/bin/env python3
"""Fetch and normalize read-only au PAY Market item/options APIs for known IDs.

Usage: python3 -B experiments/sku-matching/fetch_au.py 704502086 [ITEM_ID ...]
Raw responses and normalized JSONL outputs are written beneath .lab-output/.
This intentionally accepts item IDs, not arbitrary URLs, and performs only GETs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / ".lab-output/sku-real-au-20261009"


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            value = " ".join(data.split())
            if value:
                self._text.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            href = self._href
            if href.startswith("/item/") and href.removeprefix("/item/").isdigit():
                self.anchors.append({"item_id": href.removeprefix("/item/"), "href": href, "anchor_text": " ".join(self._text)})
            self._href = None
            self._text = []


def fetch(url: str, referer: str) -> tuple[dict, bytes]:
    last_error: Exception | None = None
    for attempt in range(2):
        req = Request(url, headers={
            "x-triton-api": "1",
            "Accept": "application/json",
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 (compatible; read-only product observation)",
        })
        try:
            with urlopen(req, timeout=30, context=ssl.create_default_context()) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise RuntimeError("API response exceeded 8 MiB capture cap")
                status = response.status
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type")
            if status >= 500 and attempt == 0:
                time.sleep(1)
                continue
            return ({"requested_url": url, "final_url": final_url, "status": status,
                     "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                     "response_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                     "content_type": content_type}, body)
        except HTTPError as exc:
            body = exc.read()
            if exc.code >= 500 and attempt == 0:
                time.sleep(1)
                continue
            return ({"requested_url": url, "final_url": exc.geturl(), "status": exc.code,
                     "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                     "response_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                     "content_type": exc.headers.get("Content-Type")}, body)
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    raise RuntimeError(f"GET failed after one retry: {url}: {last_error}")


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_ids", nargs="+", help="Known au PAY Market item IDs")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if len(args.item_ids) > 30 or len(set(args.item_ids)) != len(args.item_ids):
        parser.error("Use at most 30 distinct known item IDs per collection")
    if any(not item_id.isdigit() for item_id in args.item_ids):
        parser.error("item IDs must contain digits only")
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=False)
    product_path, sku_path = out / "products.jsonl", out / "skus.jsonl"
    for item_id in args.item_ids:
        if not item_id.isdigit():
            parser.error(f"item ID must contain digits only: {item_id}")
        item_url = f"https://wowma.jp/api/item/{item_id}"
        options_url = f"https://wowma.jp/api/items/{item_id}/itemOptions"
        referer = f"https://wowma.jp/item/{item_id}"
        item_meta, item_body = fetch(item_url, referer)
        options_meta, options_body = fetch(options_url, referer)
        (out / f"{item_id}-item.json").write_bytes(item_body)
        (out / f"{item_id}-options.json").write_bytes(options_body)
        item_data = json.loads(item_body)
        option_data = json.loads(options_body)
        info = item_data["itemInfo"]
        sku = info.get("skuInfo") or {}
        rows, columns = sku.get("rowNames", []), sku.get("columnNames", [])
        matrix = sku.get("stockList", [])
        stock_counts = Counter(
            (cell.get("isSoldOut"), cell.get("isShortStock"), cell.get("shippingScheduleText"))
            for row in matrix for cell in row
        )
        # These counts summarize source flags. No stock quantity or price is inferred.
        non_sold_out = sum(1 for row in matrix for cell in row if cell.get("isSoldOut") is False)
        links = Links()
        links.feed(info.get("extraItemComment", ""))
        product = {
            "platform": "au_PAY_Market", "item_id": item_id,
            "item_api": item_meta, "options_api": options_meta,
            "item_title": info.get("itemTitle"),
            "current_price": info.get("currentPrice"),
            "current_price_json_type": type(info.get("currentPrice")).__name__,
            "shop_user_id": info.get("shopUserId"),
            "shop_name": item_data.get("shopSummaryInfo", {}).get("shopName"),
            "is_sku_product": info.get("isSkuProduct"),
            "is_item_not_for_sale": info.get("isItemNotForSale"),
            "has_free_options": info.get("hasFreeOptions"),
            "has_paid_options": info.get("hasPaidOptions"),
            "free_options": option_data.get("freeOptions"),
            "paid_options": option_data.get("paidOptions"),
            "sku": {
                "sku_id": sku.get("skuId"), "row_count": sku.get("rowCount"),
                "column_count": sku.get("columnCount"), "option_name": sku.get("optionName"),
                "row_names": rows, "column_names": columns,
                "stock_list_shape": [len(matrix), sorted({len(row) for row in matrix})],
                "stock_status_counts": [
                    {"is_sold_out": sold, "is_short_stock": short, "shipping_schedule_text": schedule, "count": count}
                    for (sold, short, schedule), count in sorted(stock_counts.items(), key=lambda x: str(x[0]))
                ],
                "combination_count": sum(len(row) for row in matrix),
                "is_sold_out_false_count": non_sold_out,
            },
            "description_item_links": links.anchors,
        }
        append_jsonl(product_path, product)
        for ri, row in enumerate(matrix):
            for ci, cell in enumerate(row):
                append_jsonl(sku_path, {
                    "platform": "au_PAY_Market", "item_id": item_id,
                    "sku_id": sku.get("skuId"), "row_index": ri, "column_index": ci,
                    "row_option_name": (sku.get("optionName") or {}).get("row"),
                    "row_option_value": rows[ri] if ri < len(rows) else None,
                    "column_option_name": (sku.get("optionName") or {}).get("column"),
                    "column_option_value": columns[ci] if ci < len(columns) else None,
                    "stock": cell,
                })
        print(json.dumps({"item_id": item_id, "title": product["item_title"],
                          "price": product["current_price"], "price_type": product["current_price_json_type"],
                          "sku_id": sku.get("skuId"), "matrix": product["sku"]["stock_list_shape"],
                          "non_sold_out_flag_count": non_sold_out,
                          "description_item_links": links.anchors,
                          "raw_sha256": [item_meta["sha256"], options_meta["sha256"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
