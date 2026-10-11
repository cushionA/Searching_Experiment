#!/usr/bin/env python3
"""Multi-shop collector for the independent real SKU evaluation (apparel, food, beauty; 2026-10-11).

New targets, authorised by the user on 2026-10-11 (other shops, no list confirmation, no search cap):
  - au PAY Marketplace keyword search across every shop (wowma.jp catalog API, no `user` filter);
  - Rakuten mall search by token (search.rakuten.co.jp/search/mall/<token>/).
Same-shop rule, identical to build_observed_pair_records: the ASCII letters/digits of the AU shop name
equal the Rakuten store slug, and the Rakuten manage number is a token of the AU primary image name.
Stages (each resumes from its files; every HTTP attempt is in a ledger):
  discover   keyword x page searches across all shops -> multishop/shops-<category>.json
  qualify    per candidate shop, Rakuten token searches on its own item images -> multishop/shop-tests.jsonl
  au         qualified shops of a category: enumerate their items by keyword, then item detail + options
  rakuten    AU items of qualified shops: token search -> item page -> parse_page (products/skus.jsonl)
  status     counts
Output layout per category is the same as collect_independent_real.py (<cat>/au, <cat>/rakuten), so
independent_eval.py picks the directories up unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote_from_bytes, urlparse
from urllib.request import Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import collect_independent_real as ci  # noqa: E402
import fetch_au  # noqa: E402
import fetch_rakuten  # noqa: E402

base, ROOT, RUN = ci.base, ci.ROOT, ci.RUN
MS = RUN / "multishop"
CATALOG_HEADERS = {"X-Catalog-API-Version": "1.0.0", "Accept": "application/json",
                   "User-Agent": "Mozilla/5.0 (compatible; read-only product observation)"}
HTML_HEADERS = {"User-Agent": fetch_rakuten.USER_AGENT, "Accept": "text/html"}
SEARCH_BASE = "https://search.rakuten.co.jp/search/mall/"
BIG = 10_000_000
DISCOVERY_PAGES = 8

CATEGORIES = {
    "apparel": ["ワンピース", "Tシャツ", "スニーカー", "パンツ", "ジャケット", "シャツ", "スカート", "コート", "ニット",
                "デニム", "レディース", "メンズ", "靴下", "バッグ", "帽子", "パジャマ", "ルームウェア", "水着", "下着",
                "ブーツ", "サンダル", "パーカー", "トレーナー", "ブラウス", "カーディガン", "スーツ", "シューズ"],
    "food": ["お米", "コーヒー", "お茶", "紅茶", "ナッツ", "プロテイン", "ジュース", "炭酸水", "麺", "パスタ", "調味料",
             "お菓子", "チョコレート", "海苔", "味噌", "醤油", "オイル", "冷凍", "ドライフルーツ", "ミネラルウォーター",
             "ギフト", "スナック", "ふりかけ", "ソース", "ビール", "ワイン", "お酒"],
    "beauty": ["シャンプー", "トリートメント", "化粧水", "乳液", "美容液", "ファンデーション", "リップ", "マスカラ",
               "アイシャドウ", "ヘアオイル", "ボディソープ", "洗顔", "日焼け止め", "ネイル", "香水", "ヘアカラー",
               "入浴剤", "歯磨き", "パック", "クリーム", "ハンドクリーム", "ボディクリーム", "コンディショナー"],
}
GENERIC_SLUGS = {"aupay", "au", "pay"}


def slug_of(shop_name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(shop_name or "").casefold())


def slug_ok(slug: str) -> bool:
    return len(slug) >= 4 and not slug.isdigit() and slug not in GENERIC_SLUGS


def stem_of(image_url: str) -> str:
    return urlparse(str(image_url or "")).path.rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()


def token_queries(stem: str) -> list[str]:
    """Search tokens for an AU primary-image stem: prefixes at -/_ delimiters, longest useful ones first."""
    parts = re.split(r"[-_.]", stem)
    prefixes = ["-".join(parts[:i]) for i in range(1, len(parts) + 1)]
    cands = []
    for s in (re.sub(r"[_-]\d{1,3}$", "", stem), stem, parts[0]):
        if len(s) >= 4 and s not in cands:
            cands.append(s)
    return cands[:3] if cands else [p for p in prefixes if len(p) >= 4][:1]


def manage_prefixes(stem: str) -> set[str]:
    parts = re.split(r"[-_.]", stem)
    return {sep.join(parts[:i]) for i in range(1, len(parts) + 1) for sep in "-_"} | {stem}


def jload(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def jsave(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            # Windows: another process (indexer, scanner) briefly holds the target.
            if attempt == 7:
                raise
            time.sleep(0.5 * (attempt + 1))


def rebuild_shops(category: str) -> dict:
    """Shops seen in this category's saved discovery pages (rebuilt from the raw responses, one file per category)."""
    state = jload(MS / f"discover-state-{category}.json", {"done": {}})
    shops: dict[str, dict] = {}
    for rel in state["done"].values():
        if not isinstance(rel, str):
            continue
        try:
            data = json.loads((ROOT / rel).read_bytes())
        except (OSError, json.JSONDecodeError):
            continue
        for h in data.get("hitItems", []):
            if str(h.get("lotNo", "")).isdigit():
                s = shops.setdefault(str(h["shopId"]), {"name": h.get("shopName"), "categories": [category], "items": {}})
                s["items"].setdefault(str(h["lotNo"]), {"image": h.get("imageUrl1"), "name": str(h.get("itemName") or "")[:80]})
    jsave(MS / f"shops-{category}.json", shops)
    return shops


def all_shops() -> dict:
    merged: dict[str, dict] = {}
    for cat in CATEGORIES:
        for sid, s in jload(MS / f"shops-{cat}.json", {}).items():
            m = merged.setdefault(sid, {"name": s["name"], "categories": [], "items": {}})
            m["categories"] = sorted(set(m["categories"]) | set(s["categories"]))
            for k, v in s["items"].items():
                m["items"].setdefault(k, v)
    return merged


class RakutenWeb:
    """Rakuten HTML GET with a cross-process throttle and a ledger of every attempt."""

    def __init__(self, ledger_path: Path):
        self.ledger_path = ledger_path
        self.search_throttle = ci.Throttle("search.rakuten.co.jp", 1.0)
        self.item_throttle = ci.Throttle("item.rakuten.co.jp", ci.RAKUTEN_INTERVAL)
        self.n = len(ci.read_jsonl(ledger_path))
        self.consecutive = 0
        self.blocked: str | None = None

    def get(self, url: str, kind: str, extra: dict | None = None) -> tuple[int | None, bytes, str]:
        (self.search_throttle if kind == "search" else self.item_throttle).wait()
        hops: list = []
        opener = build_opener(ci.SameStore(hops)) if kind == "item" else build_opener()
        started = ci.now()
        status, raw, final_url, ctype, error = None, b"", url, "", None
        try:
            with opener.open(Request(url, headers=HTML_HEADERS), timeout=30) as resp:
                raw = resp.read(fetch_rakuten.MAX_BODY_BYTES + 1)
                status, final_url, ctype = resp.status, resp.geturl(), resp.headers.get("Content-Type", "")
                if len(raw) > fetch_rakuten.MAX_BODY_BYTES:
                    error, raw = "response exceeded 8 MiB capture cap", b""
        except HTTPError as exc:
            status, error = exc.code, f"HTTPError: {exc.code}"
        except (URLError, TimeoutError, RuntimeError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        self.n += 1
        row = {"attempt_id": f"m{self.n:06d}", "kind": kind, "url": url, "final_url": final_url, "http_status": status,
               "error": error, "started_at_utc": started, "finished_at_utc": ci.now(), "response_bytes": len(raw),
               "sha256": hashlib.sha256(raw).hexdigest() if raw else None, "redirects": hops, **(extra or {})}
        ci.append_jsonl(self.ledger_path, row)
        if error or status != 200:
            self.consecutive += 1
            if status in (403, 429):
                self.blocked = f"blocked_status_{status}"
            elif self.consecutive >= 10:
                self.blocked = "ten_consecutive_failures"
        else:
            self.consecutive = 0
        return status, raw, ctype


def search_hits(html: str) -> set[tuple[str, str]]:
    return set(re.findall(r"https://item\.rakuten\.co\.jp/([^/\"'?#<>\s]+)/([^/\"'?#<>\s]+)/", html))


def catalog_client(out: Path) -> fetch_au.BudgetedClient:
    client = fetch_au.BudgetedClient(out, BIG, BIG, 500 * 1024 * 1024, 8 * 1024 * 1024, retries=1)
    throttle, raw_get = ci.Throttle("wowma.jp", ci.AU_INTERVAL), client.get

    def get(*a, **k):
        throttle.wait()
        return raw_get(*a, **k)
    client.get = get
    return client


def catalog_url(term: str, page: int, shop: str | None) -> str:
    user = f"user={shop}&" if shop else ""
    return f"https://wowma.jp/catalog/api/search/items?{user}keyword={quote_from_bytes(term.encode('cp932'))}&ipp=40&page={page}"


def discover(args) -> None:
    out = MS / "au-discovery"
    client = catalog_client(out)
    state_path = MS / f"discover-state-{args.category}.json"
    state = jload(state_path, {"done": {}})
    start, a0, stop = time.time(), client.charged_http, "finished"
    for page in range(1, DISCOVERY_PAGES + 1):
        for term in CATEGORIES[args.category]:
            key = f"{term}\t{page}"
            if key in state["done"] or page > state["done"].get(f"{term}\ttotal", DISCOVERY_PAGES):
                continue
            if time.time() - start > args.max_seconds or client.charged_http - a0 >= args.max_attempts:
                stop = "chunk_limit"
                break
            if not ci.claim(f"ms:discover:{term}:{page}", args.category):
                state["done"][key] = None
                continue
            try:
                meta, raw = client.get(catalog_url(term, page, None), CATALOG_HEADERS, "catalog", "catalog_search",
                                       logical_request_id=f"discover:{term}:{page}")
            except fetch_au.BudgetExceeded as exc:
                stop = f"budget: {exc}"
                break
            if meta.get("status") != 200 or not raw:
                ci.append_jsonl(out / "errors.jsonl", {"term": term, "page": page, "meta": meta, "at": ci.now()})
                state["done"][key] = None
                if meta.get("status") in (403, 429):
                    stop = f"blocked_status_{meta['status']}"
                    break
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                ci.append_jsonl(out / "errors.jsonl", {"term": term, "page": page, "error": "invalid json", "at": ci.now()})
                state["done"][key] = None
                continue
            rel = Path(client.response_dir / f"{meta['attempt_id']}.body").relative_to(ROOT).as_posix() \
                if meta.get("attempt_id") else None
            state["done"][key] = rel
            state["done"][f"{term}\ttotal"] = int(data.get("pageInformation", {}).get("totalPages") or 1)
            jsave(state_path, state)
        else:
            continue
        break
    jsave(state_path, state)
    shops = rebuild_shops(args.category)
    eligible = {sid: s for sid, s in shops.items() if slug_ok(slug_of(s["name"]))}
    print(json.dumps({"category": args.category, "stage": "discover", "stop": stop, "http": client.charged_http,
                      "this_call": client.charged_http - a0, "shops": len(shops), "slug_eligible": len(eligible)}, ensure_ascii=False))


def qualify(args) -> None:
    shops = all_shops()
    tests_path = MS / "shop-tests.jsonl"
    tested = {r["shop_id"] for r in ci.read_jsonl(tests_path)}
    web = RakutenWeb(MS / "rakuten-ledger.jsonl")
    start, calls, stop = time.time(), 0, "finished"
    for sid, s in shops.items():
        slug = slug_of(s["name"])
        if sid in tested or not slug_ok(slug):
            continue
        if time.time() - start > args.max_seconds or calls >= args.max_attempts:
            stop = "chunk_limit"
            break
        if not ci.claim(f"ms:qualify:{sid}", "qualify"):
            continue
        result = {"shop_id": sid, "shop_name": s["name"], "slug": slug, "categories": s["categories"], "tried": [], "hit": None}
        for item_id, it in list(s["items"].items())[:4]:
            stem = stem_of(it["image"])
            allowed = manage_prefixes(stem)
            for tok in token_queries(stem)[:2]:
                status, raw, _ = web.get(SEARCH_BASE + tok + "/", "search", {"purpose": "qualify", "token": tok, "shop_id": sid})
                calls += 1
                found = [m for sl, m in search_hits(raw.decode("utf-8", "replace")) if sl.casefold() == slug and m.casefold() in allowed]
                result["tried"].append({"au_item_id": item_id, "token": tok, "status": status, "hit": found[:1]})
                if found:
                    result["hit"] = {"au_item_id": item_id, "token": tok, "manage_number": found[0]}
                    break
                if web.blocked:
                    break
            if result["hit"] or web.blocked:
                break
        if web.blocked:
            stop = web.blocked
            break
        ci.append_jsonl(tests_path, result)
    tests = ci.read_jsonl(tests_path)
    print(json.dumps({"stage": "qualify", "stop": stop, "this_call": calls, "tested": len(tests),
                      "qualified": sum(1 for t in tests if t["hit"]), "remaining": sum(
                          1 for sid, s in shops.items() if slug_ok(slug_of(s["name"])) and sid not in {t['shop_id'] for t in tests})},
                     ensure_ascii=False))


def qualified(category: str) -> dict[str, dict]:
    return {t["shop_id"]: t for t in ci.read_jsonl(MS / "shop-tests.jsonl") if t["hit"] and category in t["categories"]}


def au(args) -> None:
    out = RUN / args.category / "au"
    base.OUT = out
    client = catalog_client(out)
    shops = qualified(args.category)
    if not shops:
        print(json.dumps({"category": args.category, "side": "au", "stop": "no_qualified_shops"}))
        return
    (out / "catalog").mkdir(parents=True, exist_ok=True)
    state_path = out / "ms-state.json"
    state = jload(state_path, {"pages": {}, "total_pages": {}, "evidence": {}, "failed": []})
    old_ids = ci.old_au_ids()
    for p in RUN.glob("*/au"):
        if p != out:
            old_ids |= {x.stem for x in (p / "records").glob("*.json")} if (p / "records").exists() else set()
    done = {r["item_id"] for r in base.saved_records() if r.get("state") in
            {"sku_product", "non_sku", "shop_mismatch", "item_http_error", "options_http_error"}} | set(state["failed"])
    start, a0, order, stop = time.time(), client.charged_http, len(done), "chunk_limit"
    terms = CATEGORIES[args.category]

    def next_page():
        for sid in shops:
            for page in range(1, ci.MAX_PAGES_PER_TERM + 1):
                for term in terms:
                    key = f"{sid}\t{term}\t{page}"
                    if key in state["pages"] or page > state["total_pages"].get(f"{sid}\t{term}", 1 if page == 1 else 0):
                        continue
                    return sid, term, page, key
        return None

    while client.charged_http - a0 < args.max_attempts and time.time() - start < args.max_seconds:
        pending = [i for i, ev in state["evidence"].items() if i not in old_ids and i not in done]
        try:
            if pending:
                item_id = pending[0]
                ev = state["evidence"][item_id]
                if not ci.claim(f"au:item:{item_id}", args.category):
                    done.add(item_id)
                    continue
                order += 1
                try:
                    base.process_id(client, item_id, ev[0]["shop_id"], ev, order)
                except fetch_au.BudgetExceeded:
                    raise
                except Exception as exc:
                    ci.append_jsonl(out / "errors.jsonl", {"request_type": "item_process", "item_id": item_id,
                                                           "error": f"{type(exc).__name__}: {exc}", "at": ci.now()})
                    state["failed"].append(item_id)
                done.add(item_id)
                continue
            nxt = next_page()
            if nxt is None:
                stop = "search_exhausted"
                break
            sid, term, page, key = nxt
            if not ci.claim(f"ms:au:search:{sid}:{term}:{page}", args.category):
                state["pages"][key] = None
                continue
            meta, raw = client.get(catalog_url(term, page, sid), CATALOG_HEADERS, "catalog", "catalog_search",
                                   logical_request_id=f"catalog:{sid}:{term}:{page}")
            if meta.get("status") != 200 or not raw:
                ci.append_jsonl(out / "errors.jsonl", {"request_type": "catalog_http_status", "shop": sid, "query": term, "page": page,
                                                       "meta": meta, "at": ci.now()})
                state["pages"][key] = None
                if meta.get("status") in (403, 429):
                    stop = f"blocked_status_{meta['status']}"
                    break
                continue
            digest = hashlib.sha1(f"{sid}\t{term}".encode()).hexdigest()[:10]
            path = out / "catalog" / f"search-{digest}-p{page:02d}.json"
            path.write_bytes(raw)
            (out / "catalog" / f"search-{digest}-p{page:02d}.meta.json").write_text(
                json.dumps({**meta, "query": term, "page": page, "shop_id": sid}, ensure_ascii=False, indent=1), encoding="utf-8")
            rel = path.relative_to(ROOT).as_posix()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                ci.append_jsonl(out / "errors.jsonl", {"request_type": "catalog_validation", "shop": sid, "query": term, "page": page, "at": ci.now()})
                state["pages"][key] = None
                continue
            state["total_pages"][f"{sid}\t{term}"] = int(data.get("pageInformation", {}).get("totalPages") or 1)
            state["pages"][key] = rel
            for h in data.get("hitItems", []):
                if str(h.get("lotNo", "")).isdigit() and str(h.get("shopId", "")) == sid:
                    state["evidence"].setdefault(str(h["lotNo"]), []).append(
                        {"query": term, "page": page, "raw_file": rel, "item_name": h.get("itemName"), "shop_id": sid})
        except fetch_au.BudgetExceeded as exc:
            stop = f"budget: {exc}"
            break
        finally:
            jsave(state_path, state)
    products, cells = base.rebuild_exports()
    pending_left = len([i for i in state["evidence"] if i not in old_ids and i not in done])
    print(json.dumps({"category": args.category, "side": "au", "stop": stop, "http": client.charged_http, "this_call": client.charged_http - a0,
                      "states": dict(Counter(r.get("state") for r in base.saved_records())), "sku_products": products, "sku_cells": cells,
                      "shops": len(shops), "pending_items": pending_left}, ensure_ascii=False))


def rakuten(args) -> None:
    out = RUN / args.category / "rakuten"
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "search").mkdir(parents=True, exist_ok=True)
    shops = qualified(args.category)
    web = RakutenWeb(out / "http-ledger.jsonl")
    state_path = out / "ms-state.json"
    state = jload(state_path, {"items": {}})
    au_dir = RUN / args.category / "au"
    products_path, skus_path = out / "products.jsonl", out / "skus.jsonl"
    fetched = {p["source_url"] for p in ci.read_jsonl(products_path)}
    searched: dict[str, dict] = {}
    for r in ci.read_jsonl(MS / "rakuten-token-cache.jsonl"):
        searched[r["key"]] = r
    items = []
    for rec_path in sorted((au_dir / "records").glob("*.json")) if (au_dir / "records").exists() else []:
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        if rec.get("state") != "sku_product":
            continue
        items.append(rec)
    start, calls, stop = time.time(), 0, "candidates_exhausted"
    for rec in items:
        item_id = str(rec["item_id"])
        if item_id in state["items"]:
            continue
        if calls >= args.max_attempts or time.time() - start > args.max_seconds:
            stop = "chunk_limit"
            break
        raw_item = au_dir / "raw" / f"{item_id}-item.json"
        try:
            info = json.loads(raw_item.read_text(encoding="utf-8")).get("itemInfo", {})
        except (OSError, json.JSONDecodeError):
            state["items"][item_id] = {"result": "au_item_unreadable"}
            continue
        shop_id = str(info.get("shopUserId", ""))
        slug = slug_of(shops[shop_id]["shop_name"]) if shop_id in shops else ""
        images = info.get("productImageUrls") or []
        stem = stem_of(images[0]) if images else ""
        if not slug or not stem:
            state["items"][item_id] = {"result": "no_slug_or_image"}
            continue
        allowed = manage_prefixes(stem)
        match = None
        for tok in token_queries(stem):
            key = f"{slug}\t{tok}"
            if key not in searched:
                status, raw, _ = web.get(SEARCH_BASE + tok + "/", "search", {"purpose": "counterpart", "token": tok, "au_item_id": item_id})
                calls += 1
                hit = sorted({m for sl, m in search_hits(raw.decode("utf-8", "replace")) if sl.casefold() == slug}) if status == 200 else []
                sha = hashlib.sha256(raw).hexdigest() if raw else None
                if hit:
                    (out / "search" / f"{hashlib.sha1(key.encode()).hexdigest()[:12]}.html").write_bytes(raw)
                searched[key] = {"key": key, "status": status, "manage_numbers": hit, "sha256": sha, "attempt": f"m{web.n:06d}"}
                ci.append_jsonl(MS / "rakuten-token-cache.jsonl", searched[key])
                if web.blocked:
                    break
            cands = [m for m in searched[key]["manage_numbers"] if m.casefold() in allowed]
            if cands:
                match = (tok, max(cands, key=len), searched[key])
                break
        if web.blocked:
            stop = web.blocked
            break
        if not match:
            state["items"][item_id] = {"result": "no_counterpart_found"}
            jsave(state_path, state)
            continue
        tok, manage, srch = match
        url = f"https://item.rakuten.co.jp/{slug}/{manage}/"
        if url in fetched:
            state["items"][item_id] = {"result": "counterpart_already_fetched", "url": url}
            jsave(state_path, state)
            continue
        if not ci.claim(f"rakuten:url:{url}", args.category):
            state["items"][item_id] = {"result": "counterpart_claimed_elsewhere", "url": url}
            continue
        started = ci.now()
        status, raw, ctype = web.get(url, "item", {"purpose": "counterpart_page", "au_item_id": item_id})
        calls += 1
        if web.blocked:
            stop = web.blocked
            break
        if status == 200 and raw:
            final = url
            dest = out / "raw" / f"{manage}-{hashlib.sha256(final.encode()).hexdigest()[:8]}.html"
            dest.write_bytes(raw)
            rel = dest.relative_to(ROOT).as_posix()
            evidence = {"kind": "product", "url": url, "evidence_kind": "rakuten_mall_search_token_same_shop",
                        "token": tok, "search_sha256": srch["sha256"], "search_attempt": srch["attempt"],
                        "au_item_id": item_id, "au_primary_image": images[0], "au_shop_name": shops[shop_id]["shop_name"]}
            record = {"requested_url": url, "url": final, "retrieved_at_utc": started, "raw_file": rel, "status": status,
                      "content_type": ctype, "candidate_evidence": evidence}
            product, sku_rows = fetch_rakuten.parse_page(record, raw)
            ci.append_jsonl(products_path, product)
            for row in sku_rows:
                ci.append_jsonl(skus_path, row)
            fetched.add(url)
            state["items"][item_id] = {"result": "fetched", "url": url, "sku_count": product.get("sku_count")}
        else:
            state["items"][item_id] = {"result": f"item_http_{status}", "url": url}
        jsave(state_path, state)
    jsave(state_path, state)
    products = ci.read_jsonl(products_path)
    print(json.dumps({"category": args.category, "side": "rakuten", "stop": stop, "this_call": calls,
                      "au_items": len(items), "judged": len(state["items"]),
                      "results": dict(Counter(v["result"] for v in state["items"].values())),
                      "product_pages": len(products), "with_skus": sum(1 for p in products if p.get("sku_count")),
                      "sku_rows": len(ci.read_jsonl(skus_path))}, ensure_ascii=False))


def status(args) -> None:
    shops = all_shops()
    tests = ci.read_jsonl(MS / "shop-tests.jsonl")
    rows = {"shops": len(shops), "slug_eligible": sum(1 for s in shops.values() if slug_ok(slug_of(s["name"]))),
            "tested": len(tests), "qualified": sum(1 for t in tests if t["hit"])}
    for cat in CATEGORIES:
        au_dir, rk_dir = RUN / cat / "au", RUN / cat / "rakuten"
        states = Counter(json.loads(p.read_text(encoding="utf-8")).get("state") for p in (au_dir / "records").glob("*.json")) \
            if (au_dir / "records").exists() else Counter()
        products = ci.read_jsonl(rk_dir / "products.jsonl")
        rows[cat] = {"qualified_shops": len(qualified(cat)), "au_states": dict(states), "rakuten_pages": len(products),
                     "rakuten_with_skus": sum(1 for p in products if p.get("sku_count")),
                     "rakuten_sku_rows": len(ci.read_jsonl(rk_dir / "skus.jsonl"))}
    print(json.dumps(rows, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["discover", "qualify", "au", "rakuten", "status"])
    parser.add_argument("--category", choices=sorted(CATEGORIES))
    parser.add_argument("--max-attempts", type=int, default=400)
    parser.add_argument("--max-seconds", type=int, default=480)
    args = parser.parse_args()
    if args.stage in ("discover", "au", "rakuten") and not args.category:
        parser.error("--category is required")
    {"discover": discover, "qualify": qualify, "au": au, "rakuten": rakuten, "status": status}[args.stage](args)


if __name__ == "__main__":
    main()
