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
import os
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, Request, build_opener,
                            urlopen)

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
                     "attempt_count": attempt + 1,
                     "response_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                     "content_type": content_type}, body)
        except HTTPError as exc:
            body = exc.read()
            if exc.code >= 500 and attempt == 0:
                time.sleep(1)
                continue
            return ({"requested_url": url, "final_url": exc.geturl(), "status": exc.code,
                     "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                     "attempt_count": attempt + 1,
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


class BudgetExceeded(RuntimeError):
    pass


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("wb") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dfd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    except OSError:
        pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BudgetedClient:
    """GET-only au/catalog client with an fsync'd pre-network request ledger."""
    def __init__(self, out: Path, max_http: int = 2600,
                 max_catalog_http: int = 200,
                 max_body_bytes: int = 500 * 1024 * 1024,
                 max_response_bytes: int = 8 * 1024 * 1024,
                 retries: int = 1) -> None:
        self.out = out
        self.max_http = max_http
        self.max_catalog_http = max_catalog_http
        self.max_body_bytes = max_body_bytes
        self.max_response_bytes = max_response_bytes
        self.retries = retries
        self.state_path = out / "http-ledger-state.json"
        self.response_dir = out / "http-responses"
        self.out.mkdir(parents=True, exist_ok=True)
        self.response_dir.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = {"version": 1, "max_http": max_http,
                          "max_catalog_http": max_catalog_http,
                          "max_body_bytes": max_body_bytes,
                          "max_response_bytes": max_response_bytes,
                          "attempts": []}
            self._save_state()
        for key, expected in (("max_http", max_http),
                              ("max_catalog_http", max_catalog_http),
                              ("max_body_bytes", max_body_bytes),
                              ("max_response_bytes", max_response_bytes)):
            if self.state.get(key) != expected:
                raise RuntimeError(f"budget configuration mismatch for {key}")
        self.opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        self._reconcile_pending()

    def _save_state(self) -> None:
        body = json.dumps(self.state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        atomic_write(self.state_path, body)

    def _reconcile_pending(self) -> None:
        changed = False
        for a in self.state["attempts"]:
            if a.get("result") is None:
                cap_path = self.response_dir / f"{a['attempt_id']}.body"
                if cap_path.exists():
                    b = cap_path.read_bytes()
                    a["result"] = {"outcome": "response_captured_status_unknown",
                                    "response_bytes_captured": len(b),
                                    "sha256": hashlib.sha256(b).hexdigest(),
                                    "completed_at_utc": datetime.now(timezone.utc).isoformat()}
                else:
                    a["result"] = {"outcome": "interrupted_after_charge_before_response_record",
                                    "response_bytes_captured": 0,
                                    "completed_at_utc": datetime.now(timezone.utc).isoformat()}
                changed = True
        if changed:
            self._save_state()

    @property
    def charged_http(self) -> int:
        return len(self.state["attempts"])

    @property
    def captured_bytes(self) -> int:
        return sum(int((a.get("result") or {}).get("response_bytes_captured", 0))
                   for a in self.state["attempts"])

    @property
    def catalog_http(self) -> int:
        return sum(1 for a in self.state["attempts"] if a.get("budget_class") == "catalog")

    def _charge(self, url: str, budget_class: str, request_type: str,
                item_id: str | None, logical_request_id: str, redirect_from: str | None = None) -> dict:
        if self.charged_http >= self.max_http:
            raise BudgetExceeded(f"global HTTP cap {self.max_http} reached")
        if budget_class == "catalog" and self.catalog_http >= self.max_catalog_http:
            raise BudgetExceeded(f"catalog HTTP cap {self.max_catalog_http} reached")
        if self.captured_bytes >= self.max_body_bytes:
            raise BudgetExceeded(f"captured body cap {self.max_body_bytes} reached")
        entry = {"attempt_id": f"a{self.charged_http + 1:05d}",
                 "budget_class": budget_class, "request_type": request_type,
                 "logical_request_id": logical_request_id, "item_id": item_id,
                 "method": "GET", "url": url, "redirect_from": redirect_from,
                 "charged_at_utc": datetime.now(timezone.utc).isoformat(), "result": None}
        self.state["attempts"].append(entry)
        # Atomic durable charge happens before any socket operation.
        self._save_state()
        return entry

    def _one_get(self, url: str, headers: dict[str, str], budget_class: str,
                 request_type: str, item_id: str | None, logical_id: str,
                 redirect_from: str | None = None) -> tuple[dict, bytes]:
        entry = self._charge(url, budget_class, request_type, item_id, logical_id, redirect_from)
        attempt_id = entry["attempt_id"]
        req = Request(url, headers=headers, method="GET")
        response = None
        status = None
        response_headers = None
        final_url = url
        try:
            response = self.opener.open(req, timeout=30)
            status = response.status
            response_headers = response.headers
            final_url = response.geturl()
        except HTTPError as exc:
            response = exc
            status = exc.code
            response_headers = exc.headers
            final_url = exc.geturl()
        except Exception as exc:
            entry["result"] = {"outcome": "network_error", "error": f"{type(exc).__name__}: {exc}",
                                "response_bytes_captured": 0,
                                "completed_at_utc": datetime.now(timezone.utc).isoformat()}
            self._save_state()
            raise
        remaining = self.max_body_bytes - self.captured_bytes
        read_cap = min(self.max_response_bytes + 1, remaining + 1)
        try:
            body = response.read(read_cap)
        finally:
            response.close()
        if len(body) > self.max_response_bytes or len(body) > remaining:
            entry["result"] = {"outcome": "body_capture_cap_exceeded", "status": status,
                                "response_bytes_read": len(body), "response_bytes_captured": 0,
                                "final_url": final_url,
                                "completed_at_utc": datetime.now(timezone.utc).isoformat()}
            self._save_state()
            raise BudgetExceeded("per-response or cumulative body capture cap exceeded")
        path = self.response_dir / f"{attempt_id}.body"
        atomic_write(path, body)
        meta = {"attempt_id": attempt_id, "request_type": request_type, "budget_class": budget_class,
                "logical_request_id": logical_id, "item_id": item_id, "requested_url": url,
                "final_url": final_url, "redirect_from": redirect_from, "status": status,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "response_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                "content_type": response_headers.get("Content-Type") if response_headers else None,
                "location": response_headers.get("Location") if response_headers else None,
                "capture_file": str(path.relative_to(self.out))}
        entry["result"] = {"outcome": "http_response", "status": status,
                           "response_bytes_captured": len(body), "sha256": meta["sha256"],
                           "final_url": final_url, "completed_at_utc": meta["retrieved_at_utc"],
                           "capture_file": meta["capture_file"]}
        self._save_state()
        return meta, body

    def get(self, url: str, headers: dict[str, str], budget_class: str,
            request_type: str, item_id: str | None = None,
            logical_request_id: str | None = None) -> tuple[dict, bytes]:
        logical_id = logical_request_id or f"{request_type}:{item_id or hashlib.sha1(url.encode()).hexdigest()[:12]}:{self.charged_http+1}"
        current = url
        redirect_from = None
        all_meta = []
        for redirect_n in range(6):
            last_error: Exception | None = None
            result = None
            for retry_n in range(self.retries + 1):
                try:
                    meta, body = self._one_get(current, headers, budget_class, request_type,
                                               item_id, logical_id, redirect_from)
                    all_meta.append(meta)
                    result = (meta, body)
                    if 500 <= meta["status"] <= 599 and retry_n < self.retries:
                        time.sleep(0.3)
                        continue
                    break
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    last_error = exc
                    if retry_n < self.retries:
                        time.sleep(0.3)
                        continue
            if result is None:
                raise RuntimeError(f"GET failed after retries: {current}: {last_error}")
            meta, body = result
            if meta["status"] not in (301, 302, 303, 307, 308):
                meta["network_attempts"] = [
                    {k: v for k, v in m.items() if k != "network_attempts"}
                    for m in all_meta
                ]
                return meta, body
            location = None
            location = meta.get("location")
            if not location:
                raise RuntimeError(f"redirect without Location header: {current}")
            target = urljoin(current, location)
            if urlparse(target).hostname not in {"wowma.jp", "www.wowma.jp"}:
                raise RuntimeError(f"redirect outside approved host: {target}")
            redirect_from, current = current, target
        raise RuntimeError(f"too many redirects: {url}")


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
        try:
            item_meta, item_body = fetch(item_url, referer)
            options_meta, options_body = fetch(options_url, referer)
        except Exception as exc:
            append_jsonl(out / "errors.jsonl", {
                "item_id": item_id, "error": f"{type(exc).__name__}: {exc}",
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            })
            print(json.dumps({"item_id": item_id, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            continue
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
