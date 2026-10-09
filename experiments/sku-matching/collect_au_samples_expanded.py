#!/usr/bin/env python3
"""Resumable, budgeted au PAY Market real SKU sampler (stage 2)."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote_from_bytes, unquote_to_bytes, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_au

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".lab-output/sku-real-au-20261010-expanded"
STAGE1 = ROOT / ".lab-output/sku-real-au-20261010"
RAKU = ROOT / ".lab-output/sku-real-rakuten-20261009/products.jsonl"
SHOP_WEIMALL = "36356553"
SHOP_NISSEN = "45442552"
MAX_HTTP = 2600
MAX_CATALOG_HTTP = 200
MAX_BODY_BYTES = 500 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_STAGE2_SKU_PRODUCTS = 897  # stage 1 already contains 103, total cap 1000
MAX_CATALOG_PAGES_PER_TERM = 8
IPP = 40

# Explicitly known IDs, traced to prior saved catalog responses, not guessed IDs.
KNOWN_IDS = [
    "778818828", "778809347",
    "711740046", "711740052", "711740050", "711740060",
    "711740054", "711740056", "711740048", "711740058",
]
KNOWN_SHOP = {x: SHOP_WEIMALL for x in KNOWN_IDS[:2]}
KNOWN_SHOP.update({x: SHOP_NISSEN for x in KNOWN_IDS[2:]})

# Product categories and phrases come from the saved 20-item Rakuten sample titles.
# Queries are encoded as CP932 exactly as observed in the existing catalog responses.
SEARCH_TERMS = [
    "カーテン", "マットレス", "毛布", "ヘアタオル", "スチールラック", "座椅子",
    "丸テーブル", "チェスト収納", "おもちゃ収納", "スリングベルト", "ジョイントマット",
    "犬 ケージ", "ペットカート", "猫 ベッド", "ステップ台", "脚立", "ソファーベッド",
    "踏み台", "キャットタワー", "ランドリーバスケット", "カーテンレース セット",
    "折りたたみマットレス", "ペットベッド", "ペットフェンス", "キッチンワゴン",
    "レンジラック", "ベッド", "敷布団", "ラグ", "収納ラック", "ダイニングテーブル",
    "ペット用品", "タオル", "猫用品", "犬用品", "折りたたみチェア", "収納ボックス",
    "ソファ", "デスク", "カラーボックス", "椅子", "マット", "棚", "カーペット",
    "布団カバー", "ブランケット", "カーテン 遮光", "座椅子 リクライニング",
    "マットレス 高反発", "ラック 収納",
    "カーテンセット 1級遮光", "完全遮光カーテン", "レースカーテン", "ヘアドライタオル",
    "マイクロファイバータオル", "収納家具", "スチール棚", "三つ折りマットレス",
    "低反発マットレス", "高反発マット", "毛布 シングル", "ひざ掛け", "フロアチェア",
    "リクライニングチェア", "収納チェスト", "フラップ収納", "おもちゃ箱", "玩具収納",
    "荷締めベルト", "吊りベルト", "プレイマット", "折りたたみペットケージ",
    "ペットサークル", "犬用カート", "ペット用ベッド", "猫用ベッド", "エアロビクスステップ",
    "運動ステップ", "ステップラダー", "キッズステップ", "子供踏み台", "ソファベッド 3way",
    "カウチソファ", "ベッドマット", "敷きパッド", "ラウンドテーブル", "ダイニングチェア",
    "本棚", "シェルフ", "キッチン収納", "ランドリー収納", "洗濯かご", "猫タワー",
    "ドッグカート", "タオルセット", "遮熱カーテン", "ベビーゲート", "収納棚",
]
RAKUTEN_QUERY_SOURCES: dict[str, list[dict]] = {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    fetch_au.atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def atomic_bytes(path: Path, body: bytes) -> None:
    fetch_au.atomic_write(path, body)


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # One O_APPEND write keeps each JSON record indivisible for the sequential collector.
    body = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)


def source_known_evidence() -> dict[str, list[dict]]:
    evidence: dict[str, list[dict]] = {}
    sources = [
        (STAGE1.parent / "sku-real-au-20261009/catalog-probe-1.json", "カーテン"),
        (STAGE1.parent / "sku-real-au-20261009/catalog-probe-3.json", "カーテン"),
        (STAGE1.parent / "sku-real-au-select10-20261009/catalog-select10.json", "Select10"),
    ]
    for path, query in sources:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for hit in data.get("hitItems", []):
            iid = str(hit.get("lotNo", ""))
            if iid not in KNOWN_IDS:
                continue
            evidence.setdefault(iid, []).append({
                "query": query, "raw_file": str(path.relative_to(ROOT)),
                "item_id": iid, "item_name": hit.get("itemName"),
                "shop_id": str(hit.get("shopId", "")), "shop_name": hit.get("shopName"),
                "source_record": hit,
            })
    return evidence


def stage1_ids() -> set[str]:
    p = STAGE1 / "products.jsonl"
    if not p.exists():
        return set()
    return {str(json.loads(line)["item_id"]) for line in p.read_text(encoding="utf-8").splitlines() if line}


def append_rakuten_grounded_search_terms() -> None:
    """Add observed title phrases from the 92-item Rakuten source table."""
    if not RAKU.exists():
        return
    known = set(SEARCH_TERMS)
    stop = {"楽天1位", "送料無料", "クーポン", "配布中", "新商品", "公式", "おしゃれ",
            "人気", "おすすめ", "セット", "対応", "用", "大容量", "洗える", "収納"}
    additions = []
    for line in RAKU.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        title = str(row.get("title") or "")
        clean = re.sub(r"【[^】]*】|［[^］]*］|\[[^\]]*\]|＼[^／]*／", " ", title)
        tokens = re.findall(r"[一-龯ぁ-んァ-ヶーA-Za-z0-9]+", clean)
        tokens = [t for t in tokens if len(t) >= 2 and t not in stop and not t.isdigit()]
        # Prefer exact short phrases found in source titles, plus their high-value leading terms.
        candidates = []
        candidates.extend(tokens[:4])
        candidates.extend(f"{tokens[i]} {tokens[i+1]}" for i in range(min(4, len(tokens)-1)))
        for term in candidates:
            term = " ".join(term.split())
            if not term or term in known:
                continue
            known.add(term)
            additions.append(term)
            RAKUTEN_QUERY_SOURCES.setdefault(term, []).append({
                "manage_number": row.get("manage_number"), "item_id": row.get("item_id"),
                "title": title, "source_url": row.get("source_url"),
            })
    # The 200 physical catalog-attempt cap, not this phrase list, is the hard limiter.
    SEARCH_TERMS.extend(additions)


def saved_records() -> list[dict]:
    result = []
    records_dir = OUT / "records"
    if not records_dir.exists():
        return result
    for path in sorted(records_dir.glob("*.json")):
        try:
            result.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return result


def persist_record(record: dict) -> None:
    atomic_json(OUT / "records" / f"{record['item_id']}.json", record)


def meta_from_ledger(client: fetch_au.BudgetedClient, logical_id: str) -> dict:
    attempts = [a for a in client.state["attempts"] if a.get("logical_request_id") == logical_id]
    if not attempts:
        raise RuntimeError(f"missing charged HTTP attempt for {logical_id}")
    last = attempts[-1]
    result = last.get("result") or {}
    return {"requested_url": last.get("url"), "final_url": result.get("final_url", last.get("url")),
            "status": result.get("status"), "retrieved_at_utc": result.get("completed_at_utc"),
            "attempt_count": len(attempts), "response_bytes": result.get("response_bytes_captured", 0),
            "sha256": result.get("sha256"), "capture_file": result.get("capture_file"),
            "network_attempts": [{"attempt_id": a.get("attempt_id"), "status": (a.get("result") or {}).get("status"),
                                  "url": a.get("url"), "outcome": (a.get("result") or {}).get("outcome")}
                                 for a in attempts]}


def rebuild_exports() -> tuple[int, int]:
    records = saved_records()
    products = [r["product"] for r in records if r.get("state") == "sku_product" and r.get("product")]
    products.sort(key=lambda x: (int(x.get("collection_order", 0)), str(x["item_id"])))
    product_body = "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in products)
    atomic_bytes(OUT / "products.jsonl", product_body.encode("utf-8"))
    cells = []
    for product in products:
        cell_path = OUT / "cells" / f"{product['item_id']}.jsonl"
        if cell_path.exists():
            cells.append(cell_path.read_text(encoding="utf-8"))
    atomic_bytes(OUT / "skus.jsonl", "".join(cells).encode("utf-8"))
    excluded = [r for r in records if r.get("state") != "sku_product"]
    atomic_bytes(OUT / "non-sku-candidates.jsonl",
                 "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in excluded).encode("utf-8"))
    return len(products), sum(x["sku"]["combination_count"] for x in products)


def parse_saved_catalog(path: Path, query: str, page: int) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    condition = data.get("condition", {})
    if str(condition.get("shopId")) != SHOP_WEIMALL or str(condition.get("keyword")) != query:
        raise ValueError(f"catalog condition mismatch in {path}: {condition.get('shopId')}/{condition.get('keyword')}")
    page_info = data.get("pageInformation", {})
    hits = []
    for hit in data.get("hitItems", []):
        item_id = str(hit.get("lotNo", ""))
        if not item_id.isdigit() or str(hit.get("shopId", "")) != SHOP_WEIMALL:
            continue
        hits.append(hit)
    return page_info, hits


def query_catalog(client: fetch_au.BudgetedClient) -> tuple[dict[str, list[dict]], list[dict]]:
    catalog_dir = OUT / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, list[dict]] = {}
    term_pages: dict[str, int] = {}
    summaries: list[dict] = []
    cached: dict[tuple[str, int], tuple[Path, Path]] = {}
    for path in catalog_dir.glob("search-*.json"):
        if path.name.endswith("-meta.json") or path.name.endswith(".meta.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            condition = data.get("condition", {})
            term = str(condition.get("keyword", ""))
            page = int(data.get("pageInformation", {}).get("currentPage") or condition.get("page") or 1)
            if term and page > 0:
                if path.name.startswith("search-") and path.stem.split("-")[-1].isdigit():
                    meta_path = path.with_name(path.stem + "-meta.json")
                else:
                    meta_path = path.with_suffix(".meta.json")
                cached[(term, page)] = (path, meta_path)
        except Exception:
            continue
    plan = [(term, page) for page in range(1, MAX_CATALOG_PAGES_PER_TERM + 1) for term in SEARCH_TERMS]
    for seq, (term, page) in enumerate(plan, 1):
        if page > term_pages.get(term, MAX_CATALOG_PAGES_PER_TERM):
            continue
        cached_files = cached.get((term, page))
        if cached_files and cached_files[0].exists() and cached_files[1].exists():
            file, meta_file = cached_files
            raw = file.read_bytes()
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        else:
            if client.catalog_http >= MAX_CATALOG_HTTP:
                break
            digest = hashlib.sha1(term.encode("utf-8")).hexdigest()[:10]
            file = catalog_dir / f"search-{digest}-p{page:02d}.json"
            meta_file = catalog_dir / f"search-{digest}-p{page:02d}.meta.json"
            cp932 = quote_from_bytes(term.encode("cp932"))
            url = f"https://wowma.jp/catalog/api/search/items?user={SHOP_WEIMALL}&keyword={cp932}&ipp={IPP}&page={page}"
            meta, raw = client.get(url, {
                "X-Catalog-API-Version": "1.0.0", "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; read-only product observation)",
            }, "catalog", "catalog_search", logical_request_id=f"catalog:{seq}:{term}:{page}")
            atomic_bytes(file, raw)
            atomic_json(meta_file, meta)
            cached[(term, page)] = (file, meta_file)
        try:
            if meta.get("status") != 200:
                append_jsonl(OUT / "errors.jsonl", {"request_type": "catalog_http_status", "query": term,
                                                      "page": page, "meta": meta, "at": now()})
                continue
            data = json.loads(raw)
            condition = data.get("condition", {})
            if str(condition.get("shopId")) != SHOP_WEIMALL or str(condition.get("keyword")) != term:
                append_jsonl(OUT / "errors.jsonl", {"request_type": "catalog_validation", "query": term,
                                                      "page": page, "condition": condition, "at": now()})
                continue
            page_info = data.get("pageInformation", {})
            term_pages[term] = int(page_info.get("totalPages") or 1)
            hits = []
            for hit in data.get("hitItems", []):
                item_id = str(hit.get("lotNo", ""))
                if not item_id.isdigit() or str(hit.get("shopId", "")) != SHOP_WEIMALL:
                    continue
                evidence_row = {"query": term, "page": page, "catalog_url": meta.get("final_url"),
                                "item_id": item_id, "shop_id": str(hit.get("shopId")),
                                "shop_name": hit.get("shopName"), "item_name": hit.get("itemName"),
                                "catalog_item": hit,
                                "raw_file": str(file.relative_to(ROOT))}
                evidence.setdefault(item_id, []).append(evidence_row)
                hits.append(item_id)
            summaries.append({"query": term, "page": page, "total_pages": term_pages[term],
                              "hit_count": len(hits), "response_status": meta.get("status"),
                              "raw_file": str(file.relative_to(ROOT)), "meta": meta})
            # Higher pages are meaningful only through the API-reported page count.
            if page >= term_pages[term]:
                continue
        except Exception as exc:
            append_jsonl(OUT / "errors.jsonl", {"request_type": "catalog_parse", "query": term,
                                                  "page": page, "error": f"{type(exc).__name__}: {exc}", "at": now()})
            continue
        # After a complete round of pages, stop once the unique pool can support the cap.
        if seq % len(SEARCH_TERMS) == 0 and len(evidence) >= MAX_STAGE2_SKU_PRODUCTS + len(stage1_ids()) + 300:
            break
    # Include every previously saved, condition-validated catalog response, even if the
    # current Rakuten title table generated a different query phrase list on resume.
    summary_keys = {(x.get("query"), int(x.get("page", 0))) for x in summaries}
    evidence_keys = {(iid, ev.get("query"), int(ev.get("page", 0)))
                     for iid, evs in evidence.items() for ev in evs}
    for file in catalog_dir.glob("search-*.json"):
        if file.name.endswith("-meta.json") or file.name.endswith(".meta.json"):
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            condition = data.get("condition", {})
            if str(condition.get("shopId")) != SHOP_WEIMALL:
                continue
            term = str(condition.get("keyword", ""))
            page = int(data.get("pageInformation", {}).get("currentPage") or condition.get("page") or 1)
            if file.stem.split("-")[-1].isdigit():
                meta_file = file.with_name(file.stem + "-meta.json")
            else:
                meta_file = file.with_suffix(".meta.json")
            meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
            key = (term, page)
            if key not in summary_keys:
                summaries.append({"query": term, "page": page,
                                  "total_pages": data.get("pageInformation", {}).get("totalPages"),
                                  "hit_count": len(data.get("hitItems", [])),
                                  "response_status": meta.get("status", 200),
                                  "raw_file": str(file.relative_to(ROOT)), "meta": meta,
                                  "reused_saved_response": True})
                summary_keys.add(key)
            for hit in data.get("hitItems", []):
                item_id = str(hit.get("lotNo", ""))
                if not item_id.isdigit() or str(hit.get("shopId", "")) != SHOP_WEIMALL:
                    continue
                ekey = (item_id, term, page)
                if ekey in evidence_keys:
                    continue
                evidence.setdefault(item_id, []).append({
                    "query": term, "page": page, "catalog_url": meta.get("final_url"),
                    "item_id": item_id, "shop_id": str(hit.get("shopId")),
                    "shop_name": hit.get("shopName"), "item_name": hit.get("itemName"),
                    "catalog_item": hit, "raw_file": str(file.relative_to(ROOT)),
                    "reused_saved_response": True,
                })
                evidence_keys.add(ekey)
        except Exception:
            continue
    return evidence, summaries


def rank_catalog_candidates(candidates: list[str], evidence: dict[str, list[dict]]) -> list[str]:
    """Prioritize observed query groups and titles that signal genuine SKU variation."""
    outcomes: dict[str, Counter] = {}
    for record in saved_records():
        state = record.get("state")
        if state not in {"sku_product", "non_sku"}:
            continue
        label = "sku" if state == "sku_product" else "non_sku"
        for ev in record.get("product", {}).get("catalog_evidence", record.get("catalog_evidence", [])):
            query = ev.get("query")
            if query:
                outcomes.setdefault(query, Counter())[label] += 1
    variation = re.compile(r"サイズ|カラー|色|幅|長さ|丈|選べる|枚組|段階|タイプ|セット|号|段|cm|枚|色展開")
    number = re.compile(r"\d")
    def score(iid: str) -> tuple[float, str]:
        evs = evidence.get(iid, [])
        if not evs:
            return (0.0, iid)
        title = " ".join(str(e.get("item_name") or "") for e in evs)
        rates = []
        for ev in evs:
            stat = outcomes.get(ev.get("query"), Counter())
            total = stat["sku"] + stat["non_sku"]
            if total:
                rates.append((stat["sku"] + 1) / (total + 2))
        query_yield = max(rates, default=0.5)
        variant_signal = min(1.0, (len(variation.findall(title)) * 0.22) + (0.18 if number.search(title) else 0))
        direct_rakuten_query = 1.0 if any(e.get("query") in RAKUTEN_QUERY_SOURCES for e in evs) else 0.0
        source_terms = {e.get("query", "") for e in evs}
        query_signal = 0.2 if direct_rakuten_query else 0.0
        if any(any(w in q for w in ("サイズ", "カラー", "折りたたみ", "3way", "遮光")) for q in source_terms):
            query_signal += 0.08
        return (0.65 * query_yield + 0.25 * variant_signal + query_signal, iid)
    return sorted(candidates, key=lambda x: (-score(x)[0], score(x)[1]))


def normalize_sku(item_id: str, shop_id: str, im: dict, ib: bytes, om: dict, ob: bytes,
                  item_data: dict, option_data: dict, catalog_evidence: list[dict], order: int) -> tuple[dict, list[str]]:
    info = item_data["itemInfo"]
    sku = info.get("skuInfo") or {}
    rows, cols = sku.get("rowNames") or [], sku.get("columnNames") or []
    matrix = sku.get("stockList") or []
    counts = Counter((c.get("isSoldOut"), c.get("isShortStock"), c.get("shippingScheduleText"))
                     for row in matrix for c in row if isinstance(c, dict))
    cell_lines = []
    cell_count = 0
    for ri, row in enumerate(matrix):
        if not isinstance(row, list):
            continue
        for ci, cell in enumerate(row):
            stock = cell if isinstance(cell, dict) else {"source_value": cell}
            cell_count += 1
            cell_lines.append(json.dumps({
                "platform": "au_PAY_Market", "item_id": item_id, "sku_id": sku.get("skuId"),
                "row_index": ri, "column_index": ci,
                "row_option_name": (sku.get("optionName") or {}).get("row"),
                "row_option_value": rows[ri] if ri < len(rows) else None,
                "column_option_name": (sku.get("optionName") or {}).get("column"),
                "column_option_value": cols[ci] if ci < len(cols) else None,
                "stock": stock,
            }, ensure_ascii=False, separators=(",", ":")) + "\n")
    stock_counts = [{"is_sold_out": sold, "is_short_stock": short,
                     "shipping_schedule_text": sched, "count": n}
                    for (sold, short, sched), n in sorted(counts.items(), key=lambda x: str(x[0]))]
    product = {
        "platform": "au_PAY_Market", "item_id": item_id, "shop_user_id": info.get("shopUserId"),
        "shop_name": item_data.get("shopSummaryInfo", {}).get("shopName"),
        "collection_order": order, "collection_stage": "stage2",
        "item_api": im, "options_api": om,
        "item_title": info.get("itemTitle"), "current_price": info.get("currentPrice"),
        "current_price_json_type": type(info.get("currentPrice")).__name__,
        "is_sku_product": info.get("isSkuProduct"),
        "is_item_not_for_sale": info.get("isItemNotForSale"),
        "has_free_options": info.get("hasFreeOptions"), "has_paid_options": info.get("hasPaidOptions"),
        "free_options": option_data.get("freeOptions"), "paid_options": option_data.get("paidOptions"),
        "sku": {"sku_id": sku.get("skuId"), "row_count": sku.get("rowCount"),
                "column_count": sku.get("columnCount"), "option_name": sku.get("optionName"),
                "row_names": rows, "column_names": cols,
                "stock_list_shape": [len(matrix), sorted({len(r) for r in matrix if isinstance(r, list)})],
                "stock_status_counts": stock_counts, "combination_count": cell_count,
                "is_sold_out_false_count": sum(1 for row in matrix for c in row
                                                if isinstance(c, dict) and c.get("isSoldOut") is False)},
        "catalog_evidence": catalog_evidence,
        "source_raw_files": [f"raw/{item_id}-item.json", f"raw/{item_id}-options.json"],
    }
    return product, cell_lines


def captured_body(client: fetch_au.BudgetedClient, logical_id: str) -> tuple[dict, bytes] | None:
    attempts = [x for x in client.state["attempts"] if x.get("logical_request_id") == logical_id]
    for attempt in reversed(attempts):
        result = attempt.get("result") or {}
        rel = result.get("capture_file")
        if result.get("status") == 200 and rel:
            path = OUT / rel
            if path.exists():
                return meta_from_ledger(client, logical_id), path.read_bytes()
    return None


def process_id(client: fetch_au.BudgetedClient, item_id: str, shop_id: str,
               catalog_evidence: list[dict], order: int) -> str:
    record_path = OUT / "records" / f"{item_id}.json"
    terminal = {"sku_product", "non_sku", "shop_mismatch", "item_http_error", "options_http_error"}
    previous = {}
    if record_path.exists():
        try:
            previous = json.loads(record_path.read_text(encoding="utf-8"))
            if previous.get("state") in terminal:
                return previous["state"]
        except Exception:
            previous = {}
    item_path = OUT / "raw" / f"{item_id}-item.json"
    options_path = OUT / "raw" / f"{item_id}-options.json"
    item_pair = None
    if item_path.exists():
        try:
            item_pair = (meta_from_ledger(client, f"item:{item_id}"), item_path.read_bytes())
        except Exception:
            item_pair = None
    if item_pair is None:
        item_pair = captured_body(client, f"item:{item_id}")
        if item_pair:
            atomic_bytes(item_path, item_pair[1])
    if item_pair is None:
        # Persist work state before the next network request; charged attempts remain in the ledger.
        persist_record({"item_id": item_id, "state": "in_progress", "shop_id_expected": shop_id,
                        "catalog_evidence": catalog_evidence, "collection_order": order, "at": now()})
        referer = f"https://wowma.jp/item/{item_id}"
        item_url = f"https://wowma.jp/api/item/{item_id}"
        try:
            im, ib = client.get(item_url, {
                "x-triton-api": "1", "Accept": "application/json", "Referer": referer,
                "User-Agent": "Mozilla/5.0 (compatible; read-only product observation)",
            }, "detail", "item_detail", item_id=item_id, logical_request_id=f"item:{item_id}")
        except fetch_au.BudgetExceeded:
            raise
        except Exception as exc:
            record = {"item_id": item_id, "state": "request_or_parse_error", "shop_id_expected": shop_id,
                      "catalog_evidence": catalog_evidence, "collection_order": order,
                      "error": f"{type(exc).__name__}: {exc}", "at": now()}
            persist_record(record)
            append_jsonl(OUT / "errors.jsonl", record)
            return "request_or_parse_error"
        atomic_bytes(item_path, ib)
        item_pair = (im, ib)
    im, ib = item_pair
    try:
        item_data = json.loads(ib)
        info = item_data.get("itemInfo") or {}
    except Exception as exc:
        record = {"item_id": item_id, "state": "request_or_parse_error", "shop_id_expected": shop_id,
                  "collection_order": order, "item_api": im,
                  "raw_item": f"raw/{item_id}-item.json", "error": f"{type(exc).__name__}: {exc}", "at": now()}
        persist_record(record)
        append_jsonl(OUT / "errors.jsonl", record)
        return "request_or_parse_error"
    actual_shop = str(info.get("shopUserId", ""))
    if actual_shop != shop_id:
        record = {"item_id": item_id, "state": "shop_mismatch", "shop_id_expected": shop_id,
                  "shop_id_observed": actual_shop, "item_api": im, "collection_order": order,
                  "catalog_evidence": catalog_evidence, "raw_item": f"raw/{item_id}-item.json", "at": now()}
        persist_record(record)
        append_jsonl(OUT / "errors.jsonl", record)
        return "shop_mismatch"
    sku = info.get("skuInfo") or {}
    matrix = sku.get("stockList") or []
    if info.get("isSkuProduct") is not True or not matrix:
        record = {"item_id": item_id, "state": "non_sku", "shop_id": actual_shop,
                  "item_title": info.get("itemTitle"), "item_api": im, "collection_order": order,
                  "catalog_evidence": catalog_evidence, "raw_item": f"raw/{item_id}-item.json", "at": now()}
        persist_record(record)
        return "non_sku"
    # A SKU item response is checkpointed before the options GET, so resume never repeats it.
    persist_record({"item_id": item_id, "state": "awaiting_options", "shop_id": actual_shop,
                    "item_title": info.get("itemTitle"), "item_api": im,
                    "catalog_evidence": catalog_evidence, "collection_order": order,
                    "raw_item": f"raw/{item_id}-item.json", "at": now()})
    option_pair = None
    if options_path.exists():
        try:
            option_pair = (meta_from_ledger(client, f"options:{item_id}"), options_path.read_bytes())
        except Exception:
            option_pair = None
    if option_pair is None:
        option_pair = captured_body(client, f"options:{item_id}")
        if option_pair:
            atomic_bytes(options_path, option_pair[1])
    if option_pair is None:
        referer = f"https://wowma.jp/item/{item_id}"
        options_url = f"https://wowma.jp/api/items/{item_id}/itemOptions"
        try:
            om, ob = client.get(options_url, {
                "x-triton-api": "1", "Accept": "application/json", "Referer": referer,
                "User-Agent": "Mozilla/5.0 (compatible; read-only product observation)",
            }, "detail", "options_detail", item_id=item_id, logical_request_id=f"options:{item_id}")
        except fetch_au.BudgetExceeded:
            raise
        except Exception as exc:
            record = {"item_id": item_id, "state": "options_http_error", "shop_id": actual_shop,
                      "item_title": info.get("itemTitle"), "item_api": im, "collection_order": order,
                      "catalog_evidence": catalog_evidence, "raw_item": f"raw/{item_id}-item.json",
                      "error": f"{type(exc).__name__}: {exc}", "at": now()}
            persist_record(record)
            append_jsonl(OUT / "errors.jsonl", record)
            return "options_http_error"
        atomic_bytes(options_path, ob)
        option_pair = (om, ob)
    om, ob = option_pair
    if om.get("status") != 200:
        record = {"item_id": item_id, "state": "options_http_error", "shop_id": actual_shop,
                  "item_title": info.get("itemTitle"), "item_api": im, "options_api": om,
                  "collection_order": order, "catalog_evidence": catalog_evidence,
                  "raw_item": f"raw/{item_id}-item.json", "raw_options": f"raw/{item_id}-options.json", "at": now()}
        persist_record(record)
        append_jsonl(OUT / "errors.jsonl", record)
        return "options_http_error"
    try:
        option_data = json.loads(ob)
        product, cell_lines = normalize_sku(item_id, actual_shop, im, ib, om, ob,
                                             item_data, option_data, catalog_evidence, order)
        atomic_bytes(OUT / "cells" / f"{item_id}.jsonl", "".join(cell_lines).encode("utf-8"))
        persist_record({"item_id": item_id, "state": "sku_product", "product": product, "at": now()})
        return "sku_product"
    except Exception as exc:
        record = {"item_id": item_id, "state": "request_or_parse_error", "shop_id_expected": shop_id,
                  "item_title": info.get("itemTitle"), "item_api": im, "options_api": om,
                  "collection_order": order, "catalog_evidence": catalog_evidence,
                  "raw_item": f"raw/{item_id}-item.json", "raw_options": f"raw/{item_id}-options.json",
                  "error": f"{type(exc).__name__}: {exc}", "at": now()}
        persist_record(record)
        append_jsonl(OUT / "errors.jsonl", record)
        return "request_or_parse_error"

def build_manifest(client: fetch_au.BudgetedClient, catalog_summaries: list[dict], status_counts: Counter) -> dict:
    products_count, cells_count = rebuild_exports()
    products = [json.loads(x) for x in (OUT / "products.jsonl").read_text(encoding="utf-8").splitlines() if x]
    previous_ids = stage1_ids()
    current_ids = {str(x["item_id"]) for x in products}
    processed_ids = {str(json.loads(p.read_text(encoding="utf-8")).get("item_id"))
                     for p in (OUT / "records").glob("*.json")}
    attempts = client.state["attempts"]
    request_type_counts = Counter(a.get("request_type") for a in attempts)
    result_status_counts = Counter(str((a.get("result") or {}).get("status", "no_response")) for a in attempts)
    catalog_candidate_ids = set()
    for path in (OUT / "catalog").glob("search-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            condition = data.get("condition", {})
            if str(condition.get("shopId")) == SHOP_WEIMALL:
                catalog_candidate_ids.update(str(h.get("lotNo")) for h in data.get("hitItems", [])
                                             if str(h.get("shopId")) == SHOP_WEIMALL and str(h.get("lotNo", "")).isdigit())
        except Exception:
            continue
    # Audit that every saved catalog response is tied to its actual source query/page.
    catalog_files = [p for p in (OUT / "catalog").glob("search-*.json")
                     if not p.name.endswith("-meta.json") and not p.name.endswith(".meta.json")]
    catalog_keys: list[tuple[str, int]] = []
    catalog_audit_counts = Counter()
    empty_result_pages = 0
    for path in catalog_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            condition = data.get("condition") or {}
            page_info = data.get("pageInformation") or {}
            meta_path = (path.with_name(path.stem + "-meta.json") if path.stem.split("-")[-1].isdigit()
                         else path.with_suffix(".meta.json"))
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            raw_query = dict(part.split("=", 1) if "=" in part else (part, "")
                             for part in urlsplit(meta.get("requested_url", "")).query.split("&") if part)
            requested_term = unquote_to_bytes(raw_query.get("keyword", "")).decode("cp932")
            requested_page = int(raw_query.get("page", "0") or 0)
            catalog_keys.append((requested_term, requested_page))
            catalog_audit_counts["source_shop_mismatch"] += raw_query.get("user") != SHOP_WEIMALL
            catalog_audit_counts["condition_shop_mismatch"] += str(condition.get("shopId")) != SHOP_WEIMALL
            catalog_audit_counts["condition_keyword_mismatch"] += condition.get("keyword") != requested_term
            catalog_audit_counts["condition_page_mismatch"] += str(condition.get("page")) != str(requested_page)
            catalog_audit_counts["query_params_page_mismatch"] += str((condition.get("queryParams") or {}).get("page")) != str(requested_page)
            current_page = int(page_info.get("currentPage") or 0)
            total_count = int(page_info.get("totalCount") or 0)
            if total_count == 0:
                empty_result_pages += 1
                catalog_audit_counts["empty_result_page_info_valid"] += current_page == 0 and int(page_info.get("totalPages") or 0) == 0
            else:
                catalog_audit_counts["nonempty_page_info_mismatch"] += current_page != requested_page
                catalog_audit_counts["past_api_total_pages"] += requested_page > int(page_info.get("totalPages") or 0)
            catalog_audit_counts["http_status_not_200"] += meta.get("status") != 200
            captured_path = OUT / meta.get("capture_file", "")
            capture_sha = hashlib.sha256(captured_path.read_bytes()).hexdigest() if captured_path.exists() else None
            catalog_audit_counts["capture_sha_mismatch"] += capture_sha != meta.get("sha256")
        except Exception:
            catalog_audit_counts["audit_parse_errors"] += 1
    duplicate_query_page_count = len(catalog_keys) - len(set(catalog_keys))
    manifest = {
        "source": "au PAY Market public catalog and item/options JSON APIs",
        "created_at_utc": now(), "output_dir": str(OUT), "stage": 2,
        "stage1_reference": str(STAGE1.relative_to(ROOT)), "stage1_immutable": True,
        "scope": {"catalog_shop_id": SHOP_WEIMALL, "known_nissen_shop_id": SHOP_NISSEN,
                  "known_ids_fetched_first": KNOWN_IDS},
        "limits": {"max_total_http_attempts": MAX_HTTP, "max_catalog_http_attempts": MAX_CATALOG_HTTP,
                   "max_captured_body_bytes": MAX_BODY_BYTES, "max_response_body_bytes": MAX_RESPONSE_BYTES,
                   "max_stage2_sku_products": MAX_STAGE2_SKU_PRODUCTS,
                   "combined_stage1_plus_stage2_sku_cap": 1000, "max_concurrent_http": 2,
                   "actual_concurrency": 1},
        "counts": {"stage1_sku_products": len(previous_ids), "stage2_sku_products": products_count,
                   "stage2_unique_item_ids": len(current_ids), "stage2_overlap_with_stage1": sorted(current_ids & previous_ids),
                   "combined_unique_sku_products": len(current_ids | previous_ids),
                   "new_sku_product_count_vs_stage1": len(current_ids - previous_ids),
                   "stage2_sku_cells": cells_count, "catalog_search_response_count": len(catalog_summaries),
                   "unique_shop_catalog_candidates": len(catalog_candidate_ids), "request_types": dict(request_type_counts),
                   "http_statuses": dict(result_status_counts), "candidate_states": dict(status_counts),
                   "captured_response_bytes": client.captured_bytes, "http_attempts_charged": client.charged_http,
                   "catalog_attempts_charged": client.catalog_http},
        "catalog_audit": {"saved_response_count": len(catalog_files),
                           "unique_requested_query_page_pairs": len(set(catalog_keys)),
                           "duplicate_query_page_response_count": duplicate_query_page_count,
                           "empty_result_page_count": empty_result_pages,
                           "checks": dict(catalog_audit_counts),
                           "candidate_pool_unprocessed_ids": len(catalog_candidate_ids - processed_ids - previous_ids),
                           "pagination": "Requested page is recorded in the actual URL, condition.page, and condition.queryParams.page; for nonempty results it matches pageInformation.currentPage and is within API totalPages. Six valid zero-result queries report currentPage=0,totalPages=0.",
                           "source_integrity": "Each catalog response is hash-linked to its captured HTTP body and request URL in the budget ledger."},
        "catalog_searches": catalog_summaries,
        "rakuten_title_search_term_sources": RAKUTEN_QUERY_SOURCES,
        "method": {"query_encoding": "Japanese keywords encoded as CP932 before URL encoding",
                   "condition_validation": "Catalog responses accepted only when condition.shopId=36356553 and condition.keyword equals the submitted keyword.",
                   "id_selection": "Only observed hitItems.lotNo values from shop-scoped catalog responses plus ten pre-existing known IDs; no numeric ID enumeration.",
                   "non_sku_policy": "Item API response and candidate record retained under raw/records with state non_sku; excluded from products.jsonl and skus.jsonl.",
                   "price_grain": "item-level itemInfo.currentPrice; no SKU-level price inferred",
                   "sku_cells": "Every source stockList cell is exported, including sold-out/unavailable cells; no option cross-products inferred.",
                   "options": "Raw itemOptions response and freeOptions/paidOptions preserved per SKU product.",
                   "merchant_code": "Catalog API observed hit records are preserved. Merchant code is emitted only if present in the source record."},
        "errors_file": "errors.jsonl", "ledger_file": "http-ledger-state.json",
        "raw_response_directory": "http-responses/", "raw_product_directory": "raw/",
    }
    atomic_json(OUT / "provenance.json", manifest)
    return manifest


def main() -> None:
    global OUT, MAX_HTTP, MAX_CATALOG_HTTP, MAX_BODY_BYTES, MAX_RESPONSE_BYTES, MAX_STAGE2_SKU_PRODUCTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--resume", action="store_true", help="Resume only when a matching durable ledger exists.")
    parser.add_argument("--max-http", type=int, default=MAX_HTTP)
    parser.add_argument("--max-catalog-http", type=int, default=MAX_CATALOG_HTTP)
    parser.add_argument("--max-body-bytes", type=int, default=MAX_BODY_BYTES)
    parser.add_argument("--max-response-bytes", type=int, default=MAX_RESPONSE_BYTES)
    parser.add_argument("--max-new-products", type=int, default=MAX_STAGE2_SKU_PRODUCTS)
    parser.add_argument("--known-limit", type=int, default=None,
                        help="Fetch at most this many pre-known IDs, then write a checkpoint and stop.")
    args = parser.parse_args()
    if args.max_http > MAX_HTTP or args.max_catalog_http > MAX_CATALOG_HTTP:
        parser.error(f"caps cannot exceed authorized limits {MAX_HTTP} HTTP / {MAX_CATALOG_HTTP} catalog attempts")
    if args.max_body_bytes > MAX_BODY_BYTES or args.max_response_bytes > MAX_RESPONSE_BYTES:
        parser.error("body caps cannot exceed 500 MiB total / 8 MiB per response")
    if args.max_new_products > MAX_STAGE2_SKU_PRODUCTS:
        parser.error(f"--max-new-products cannot exceed {MAX_STAGE2_SKU_PRODUCTS}")
    if args.known_limit is not None and not (1 <= args.known_limit <= len(KNOWN_IDS)):
        parser.error(f"--known-limit must be between 1 and {len(KNOWN_IDS)}")
    OUT = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    MAX_HTTP, MAX_CATALOG_HTTP = args.max_http, args.max_catalog_http
    MAX_BODY_BYTES, MAX_RESPONSE_BYTES = args.max_body_bytes, args.max_response_bytes
    MAX_STAGE2_SKU_PRODUCTS = args.max_new_products
    if args.resume and not (OUT / "http-ledger-state.json").exists():
        raise RuntimeError(f"--resume requires existing durable ledger: {OUT}")
    if not args.resume and OUT.exists() and any(OUT.iterdir()):
        raise RuntimeError(f"refusing existing output without --resume: {OUT}")
    if OUT.exists() and any(OUT.iterdir()) and not (OUT / "http-ledger-state.json").exists():
        raise RuntimeError(f"refusing non-empty stage2 output without a budget ledger: {OUT}")
    OUT.mkdir(parents=True, exist_ok=True)
    client = fetch_au.BudgetedClient(OUT, MAX_HTTP, MAX_CATALOG_HTTP, MAX_BODY_BYTES, MAX_RESPONSE_BYTES, retries=1)
    append_rakuten_grounded_search_terms()
    evidence = source_known_evidence()
    # Known target IDs are acquired first and are explicit prior observations.
    existing_stage1 = stage1_ids()
    current_records = {r["item_id"]: r for r in saved_records()}
    order = 0
    stopped_budget = False
    terminal_states = {"sku_product", "non_sku", "shop_mismatch", "item_http_error", "options_http_error"}
    states = Counter(r.get("state", "unknown") for r in current_records.values())
    ids_to_fetch = KNOWN_IDS[:args.known_limit] if args.known_limit is not None else KNOWN_IDS
    for item_id in ids_to_fetch:
        old_record = current_records.get(item_id, {})
        if item_id in existing_stage1 or old_record.get("state") in terminal_states:
            continue
        order += 1
        try:
            status = process_id(client, item_id, KNOWN_SHOP[item_id], evidence.get(item_id, []), order)
        except fetch_au.BudgetExceeded as exc:
            append_jsonl(OUT / "run-events.jsonl", {"event": "budget_stop", "phase": "known",
                                                     "item_id": item_id, "error": str(exc), "at": now()})
            stopped_budget = True
            break
        states[status] += 1
        products_count, cells_count = rebuild_exports()
        if order % 10 == 0 or item_id == KNOWN_IDS[-1]:
            print(json.dumps({"stage": 2, "phase": "known", "processed": order,
                              "item_id": item_id, "state": status,
                              "sku_products": products_count, "sku_cells": cells_count,
                              "http_attempts": client.charged_http}, ensure_ascii=False))
    if args.known_limit is not None:
        status_counts = Counter(r.get("state", "unknown") for r in saved_records())
        manifest = build_manifest(client, [], status_counts)
        atomic_bytes(OUT / "http-ledger.jsonl", "".join(json.dumps(a, ensure_ascii=False, separators=(",", ":")) + "\n"
                                                            for a in client.state["attempts"]).encode("utf-8"))
        print(json.dumps({"stage2_sku_products": manifest["counts"]["stage2_sku_products"],
                          "sku_cells": manifest["counts"]["stage2_sku_cells"],
                          "http_attempts": manifest["counts"]["http_attempts_charged"],
                          "captured_bytes": manifest["counts"]["captured_response_bytes"],
                          "output": str(OUT)}, ensure_ascii=False))
        return
    catalog_evidence, catalog_summaries = {}, []
    if not stopped_budget:
        # Recover any prior item whose raw response was captured before an interrupted normalize.
        for old_record in saved_records():
            if old_record.get("state") not in terminal_states:
                try:
                    recovered_state = process_id(client, old_record["item_id"],
                                                 str(old_record.get("shop_id_expected") or SHOP_WEIMALL),
                                                 old_record.get("catalog_evidence", []),
                                                 old_record.get("collection_order", 0))
                    states[recovered_state] += 1
                except fetch_au.BudgetExceeded as exc:
                    append_jsonl(OUT / "run-events.jsonl", {"event": "budget_stop", "phase": "recovery",
                                                             "item_id": old_record["item_id"], "error": str(exc), "at": now()})
                    stopped_budget = True
                    break
    # Build/reuse structured shop catalog responses; detail decides SKU status authoritatively.
    if not stopped_budget:
        try:
            catalog_evidence, catalog_summaries = query_catalog(client)
        except fetch_au.BudgetExceeded as exc:
            append_jsonl(OUT / "run-events.jsonl", {"event": "budget_stop", "phase": "catalog",
                                                     "error": str(exc), "at": now()})
            stopped_budget = True
    previous_ids = existing_stage1
    records = saved_records()
    # Per-ID atomic records are authoritative; JSONL may lag by up to one checkpoint.
    added_skus = sum(1 for r in records if r.get("state") == "sku_product" and r.get("item_id") not in previous_ids)
    done_ids = {r["item_id"] for r in records if r.get("state") in terminal_states} | previous_ids
    by_query: dict[str, list[str]] = {}
    for item_id, evs in catalog_evidence.items():
        for ev in evs:
            by_query.setdefault(ev["query"], [])
            if item_id not in by_query[ev["query"]]:
                by_query[ev["query"]].append(item_id)
    candidates = []
    seen = set(done_ids)
    for offset in range(max((len(v) for v in by_query.values()), default=0)):
        for query in SEARCH_TERMS:
            ids = by_query.get(query, [])
            if offset < len(ids) and ids[offset] not in seen:
                candidates.append(ids[offset])
                seen.add(ids[offset])
    candidates = rank_catalog_candidates(candidates, catalog_evidence)
    last_milestone = added_skus // 100 * 100
    for idx, item_id in (enumerate(candidates, 1) if not stopped_budget else []):
        if added_skus >= MAX_STAGE2_SKU_PRODUCTS:
            break
        if client.charged_http >= MAX_HTTP:
            break
        evs = catalog_evidence.get(item_id, [])
        try:
            status = process_id(client, item_id, SHOP_WEIMALL, evs, len(KNOWN_IDS) + idx)
        except fetch_au.BudgetExceeded as exc:
            append_jsonl(OUT / "run-events.jsonl", {"event": "budget_stop", "phase": "catalog_candidates",
                                                     "item_id": item_id, "error": str(exc), "at": now()})
            break
        states[status] += 1
        if status == "sku_product":
            added_skus += 1
        if added_skus // 100 * 100 > last_milestone:
            last_milestone = added_skus // 100 * 100
            products_count, cells_count = rebuild_exports()
            print(json.dumps({"stage": 2, "phase": "catalog_candidates", "sku_products": products_count,
                              "new_sku_products": added_skus, "sku_cells": cells_count,
                              "http_attempts": client.charged_http, "catalog_attempts": client.catalog_http}, ensure_ascii=False))
        if client.charged_http >= MAX_HTTP:
            break
    status_counts = Counter(r.get("state", "unknown") for r in saved_records())
    manifest = build_manifest(client, catalog_summaries, status_counts)
    # Export the append-only atomic ledger as JSONL for inspection; state JSON remains authoritative.
    atomic_bytes(OUT / "http-ledger.jsonl", "".join(json.dumps(a, ensure_ascii=False, separators=(",", ":")) + "\n"
                                                        for a in client.state["attempts"]).encode("utf-8"))
    print(json.dumps({"stage2_sku_products": manifest["counts"]["stage2_sku_products"],
                      "combined_unique_sku_products": manifest["counts"]["combined_unique_sku_products"],
                      "sku_cells": manifest["counts"]["stage2_sku_cells"],
                      "http_attempts": manifest["counts"]["http_attempts_charged"],
                      "catalog_attempts": manifest["counts"]["catalog_attempts_charged"],
                      "captured_bytes": manifest["counts"]["captured_response_bytes"],
                      "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
