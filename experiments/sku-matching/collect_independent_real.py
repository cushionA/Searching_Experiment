#!/usr/bin/env python3
"""Category-partitioned collector for the independent real SKU evaluation data (2026-10-11).

One process per category and side; several categories run at the same time:
  au        WEIMALL (shopId 36356553) catalog search on wowma.jp, then item detail + options through the
            stage-2 collector's own `process_id` (same records/raw/products/skus layout).
  rakuten   WEIMALL item pages on item.rakuten.co.jp, only URLs observed as anchors in captured HTML
            (no guessed slugs), parsed by `fetch_rakuten.parse_page`.
  status    counts per category.

Every HTTP attempt, failed or redirected, is in the side's ledger. Items, search pages and URLs are claimed
with an exclusive file before the request, so two categories never fetch the same thing, and items captured
before this run are never fetched again. A cross-process throttle spaces requests per host.
Each invocation stops after --max-attempts or --max-seconds and resumes from its files on the next call.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import types
from urllib.error import HTTPError, URLError
from urllib.parse import quote_from_bytes, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
if os.name == "nt":
    # fetch_rakuten locks its stage-2 ledger with fcntl; this collector never uses that ledger.
    sys.modules.setdefault("fcntl", types.SimpleNamespace(flock=lambda *a, **k: None, LOCK_EX=2, LOCK_UN=8))
import collect_au_samples_expanded as base  # noqa: E402
import extract_rakuten_links as links  # noqa: E402
import fetch_au  # noqa: E402
import fetch_rakuten  # noqa: E402

ROOT = HERE.parents[1]
RUN = ROOT / ".lab-output/sku-independent-real-20261011"
SHOP = "36356553"
AU_INTERVAL, RAKUTEN_INTERVAL = 0.5, 1.0
AU_MAX_HTTP, AU_MAX_CATALOG, RAKUTEN_MAX_HTTP = 1500, 300, 1000
AU_CAPS = {"sweep": (5000, 500), "sweepall": (4000, 120)}
MAX_PAGES_PER_TERM = 60
CATEGORIES = {
    "curtain": ["カーテン", "レースカーテン", "遮光カーテン", "1級遮光", "ドレープ", "カーテンセット", "のれん",
                "ロールスクリーン", "ブラインド"],
    "bedding": ["マットレス", "敷布団", "布団", "毛布", "ブランケット", "布団カバー", "シーツ", "敷きパッド",
                "枕", "ベッドパッド", "掛け布団", "ベッド"],
    "storage": ["ラック", "スチールラック", "収納", "チェスト", "カラーボックス", "棚", "ワゴン", "レンジラック",
                "本棚", "シェルフ", "ハンガーラック", "おもちゃ収納"],
    "seating": ["座椅子", "ソファ", "ソファー", "ソファーベッド", "チェア", "椅子", "クッション",
                "ビーズクッション", "スツール", "オットマン"],
    "table": ["テーブル", "デスク", "ダイニング", "ローテーブル", "サイドテーブル", "パソコンデスク", "ドレッサー",
              "こたつ"],
    "pet": ["ペット", "犬", "猫", "ケージ", "キャットタワー", "ペットカート", "ペットベッド", "ペットフェンス",
            "サークル", "キャリー"],
    "household": ["タオル", "脚立", "踏み台", "ステップ台", "はしご", "ランドリー", "ラグ", "ジョイントマット",
                  "カーペット", "ベルト", "ゴミ箱", "洗車"],
    # Broad shop-wide terms; claims keep it from refetching what a category already took.
    "sweep": ["送料無料", "おしゃれ", "北欧", "新生活", "日本製", "折りたたみ", "ホワイト", "ブラック", "グレー",
              "ブラウン", "ナチュラル", "ベージュ", "ネイビー", "セット", "サイズ", "カラー"],
    # One shop-wide term walked through every page (sweep spread its catalog cap over page 1 of many terms).
    "sweepall": ["送料無料"],
}
OLD_AU = [ROOT / ".lab-output/sku-real-au-20261009", ROOT / ".lab-output/sku-real-au-20261010",
          ROOT / ".lab-output/sku-real-au-20261010-expanded"]
OLD_RAKUTEN_GLOB = ".lab-output/sku-real-rakuten-2026*"
STOP = {"楽天1位", "送料無料", "クーポン", "配布中", "新商品", "公式", "おしゃれ", "人気", "おすすめ", "セット",
        "対応", "用", "大容量", "洗える", "収納", "OFF", "off"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def norm_url(url: str) -> str:
    path, _, query = url.split("#")[0].partition("?")
    path = path.rstrip("/") + "/"
    page = re.search(r"(?:^|&)p=(\d+)", query)
    # Category pagination is a different page; every other query is dropped.
    return path + (f"?p={page.group(1)}" if page and "/weimall/c/" in path and page.group(1) != "1" else "")


PAGINATION = re.compile(r'href="(https://item\.rakuten\.co\.jp/weimall/c/\d+/?\?[^"#]*\bp=\d+[^"#]*)')
INDEX_VERSION = 2


class Throttle:
    """Space requests to one host across processes: a locked file holds the last request time."""

    def __init__(self, host: str, interval: float):
        self.path, self.interval = RUN / f"throttle-{host}.lock", interval

    def wait(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as fh:
            while True:
                try:
                    _lock(fh)
                    break
                except OSError:
                    time.sleep(0.2)
            try:
                fh.seek(0)
                text = fh.read().decode() or "0"
                last = float(text.split()[-1]) if text.split() else 0.0
                delay = last + self.interval - time.time()
                if delay > 0:
                    time.sleep(delay)
                fh.seek(0)
                fh.truncate()
                fh.write(f"{time.time():.3f}".encode())
                fh.flush()
            finally:
                _unlock(fh)


def _lock(fh):
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)


def _unlock(fh):
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def claim(key: str, category: str) -> bool:
    """True when this category owns the key (first claim wins, own claims stay valid on resume)."""
    path = RUN / "claims" / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("category") == category
        except (OSError, json.JSONDecodeError):
            return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": key, "category": category, "at": now()}, ensure_ascii=False))
    return True


def tokens(title: str) -> list[str]:
    clean = re.sub(r"【[^】]*】|［[^］]*］|\[[^\]]*\]|＼[^／]*／", " ", title)
    out = re.findall(r"[一-龯ぁ-んァ-ヶーA-Za-z0-9]+", clean)
    return [t for t in out if len(t) >= 2 and t not in STOP and not t.isdigit()]


def rakuten_titles() -> list[str]:
    titles = []
    for path in sorted(glob.glob(str(ROOT / OLD_RAKUTEN_GLOB / "**/products.jsonl"), recursive=True)) + \
            sorted(glob.glob(str(RUN / "*/rakuten/products.jsonl"))):
        titles += [str(r.get("title") or "") for r in read_jsonl(Path(path))]
    return [t for t in titles if t]


def category_terms(category: str) -> list[str]:
    seeds = CATEGORIES[category]
    terms = list(seeds)
    for title in rakuten_titles():
        if not any(k in title for k in seeds):
            continue
        tk = tokens(title)
        for term in tk[:4] + [f"{tk[i]} {tk[i + 1]}" for i in range(min(4, len(tk) - 1))]:
            if any(k in term for k in seeds) and term not in terms:
                terms.append(term)
    return terms


def old_au_ids() -> set[str]:
    ids = set()
    for d in OLD_AU:
        ids |= {str(r.get("item_id")) for r in read_jsonl(d / "products.jsonl")}
        ids |= {p.stem for p in (d / "records").glob("*.json")} if (d / "records").exists() else set()
        ids |= {p.name.split("-")[0] for p in d.glob("*-item.json")}
    return ids


def stage2_catalog() -> dict[tuple[str, int], Path]:
    found = {}
    for path in (OLD_AU[2] / "catalog").glob("search-*.json"):
        if path.name.endswith("meta.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cond = data.get("condition", {})
            page = int(data.get("pageInformation", {}).get("currentPage") or cond.get("page") or 1)
            found[(str(cond.get("keyword", "")), page)] = path
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return found


def parse_catalog(raw: bytes, term: str) -> tuple[int, list[dict]] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Observed: status 200 with an empty body. Logged as a validation failure by the caller.
        return None
    cond = data.get("condition", {})
    if str(cond.get("shopId")) != SHOP or str(cond.get("keyword")) != term:
        return None
    total = int(data.get("pageInformation", {}).get("totalPages") or 1)
    hits = [h for h in data.get("hitItems", []) if str(h.get("lotNo", "")).isdigit() and str(h.get("shopId", "")) == SHOP]
    return total, hits


def au(args) -> None:
    out = RUN / args.category / "au"
    base.OUT = out
    max_http, max_catalog = AU_CAPS.get(args.category, (AU_MAX_HTTP, AU_MAX_CATALOG))
    ledger_state = out / "http-ledger-state.json"
    if ledger_state.exists():
        # A raised cap keeps every charged attempt; only the configured ceiling changes, and the change is logged.
        st = json.loads(ledger_state.read_text(encoding="utf-8"))
        if (st["max_http"], st["max_catalog_http"]) != (max_http, max_catalog):
            append_jsonl(out / "cap-changes.jsonl", {"from": [st["max_http"], st["max_catalog_http"]], "to": [max_http, max_catalog],
                                                     "attempts_kept": len(st["attempts"]), "at": now()})
            st["max_http"], st["max_catalog_http"] = max_http, max_catalog
            fetch_au.atomic_write(ledger_state, json.dumps(st, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    client = fetch_au.BudgetedClient(out, max_http, max_catalog, 500 * 1024 * 1024, 8 * 1024 * 1024, retries=1)
    throttle, raw_get = Throttle("wowma.jp", AU_INTERVAL), client.get

    def get(*a, **k):
        throttle.wait()
        return raw_get(*a, **k)
    client.get = get
    (out / "catalog").mkdir(parents=True, exist_ok=True)
    state_path = out / "search-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"total_pages": {}, "pages": {}}
    state.setdefault("failed_items", [])
    terms = category_terms(args.category)
    old_ids, reuse = old_au_ids(), stage2_catalog()
    evidence: dict[str, list[dict]] = {}
    for key, rel in state["pages"].items():
        term, page = key.rsplit("\t", 1)
        parsed = parse_catalog((ROOT / rel).read_bytes(), term) if rel else None
        for hit in (parsed[1] if parsed else []):
            evidence.setdefault(str(hit["lotNo"]), []).append({"query": term, "page": int(page), "raw_file": rel,
                                                                "item_name": hit.get("itemName"), "shop_id": SHOP})
    done = {r["item_id"] for r in base.saved_records() if r.get("state") in
            {"sku_product", "non_sku", "shop_mismatch", "item_http_error", "options_http_error"}} | set(state["failed_items"])
    start, attempts0, order = time.time(), client.charged_http, len(done)
    stop_reason = "chunk_limit"

    def next_page():
        for page in range(1, MAX_PAGES_PER_TERM + 1):
            for term in terms:
                key = f"{term}\t{page}"
                if key in state["pages"] or page > state["total_pages"].get(term, 1 if page == 1 else 0):
                    continue
                return term, page, key
        return None

    while client.charged_http - attempts0 < args.max_attempts and time.time() - start < args.max_seconds:
        pending = [i for i in evidence if i not in old_ids and i not in done]
        try:
            if pending:
                item_id = pending[0]
                if not claim(f"au:item:{item_id}", args.category):
                    done.add(item_id)
                    continue
                order += 1
                try:
                    base.process_id(client, item_id, SHOP, evidence[item_id], order)
                except fetch_au.BudgetExceeded:
                    raise
                except Exception as exc:
                    append_jsonl(out / "errors.jsonl", {"request_type": "item_process", "item_id": item_id,
                                                        "error": f"{type(exc).__name__}: {exc}", "at": now()})
                    state["failed_items"].append(item_id)
                done.add(item_id)
                continue
            nxt = next_page()
            if nxt is None:
                stop_reason = "search_exhausted"
                break
            term, page, key = nxt
            if (term, page) in reuse:
                rel = reuse[(term, page)].relative_to(ROOT).as_posix()
            elif not claim(f"au:search:{term}:{page}", args.category):
                # Another category owns this page: read its saved response so the term's page count is known.
                rel = next((st["pages"][key] for st in (json.loads(p.read_text(encoding="utf-8"))
                                                         for p in RUN.glob("*/au/search-state.json") if p.parent != out)
                            if st.get("pages", {}).get(key)), None)
                if rel is None:
                    state["pages"][key] = None
                    continue
            else:
                try:
                    encoded = quote_from_bytes(term.encode("cp932"))
                except UnicodeEncodeError as exc:
                    append_jsonl(out / "errors.jsonl", {"request_type": "catalog_encoding", "query": term, "error": str(exc), "at": now()})
                    state["pages"][key] = None
                    continue
                url = f"https://wowma.jp/catalog/api/search/items?user={SHOP}&keyword={encoded}&ipp=40&page={page}"
                meta, raw = client.get(url, {"X-Catalog-API-Version": "1.0.0", "Accept": "application/json",
                                             "User-Agent": "Mozilla/5.0 (compatible; read-only product observation)"},
                                       "catalog", "catalog_search", logical_request_id=f"catalog:{term}:{page}")
                digest = hashlib.sha1(term.encode("utf-8")).hexdigest()[:10]
                path = out / "catalog" / f"search-{digest}-p{page:02d}.json"
                path.write_bytes(raw)
                (out / "catalog" / f"search-{digest}-p{page:02d}.meta.json").write_text(
                    json.dumps({**meta, "query": term, "page": page}, ensure_ascii=False, indent=1), encoding="utf-8")
                rel = path.relative_to(ROOT).as_posix()
                if meta.get("status") != 200:
                    append_jsonl(out / "errors.jsonl", {"request_type": "catalog_http_status", "query": term, "page": page, "meta": meta, "at": now()})
                    state["pages"][key] = None
                    continue
            parsed = parse_catalog((ROOT / rel).read_bytes(), term)
            if parsed is None:
                append_jsonl(out / "errors.jsonl", {"request_type": "catalog_validation", "query": term, "page": page, "raw_file": rel, "at": now()})
                state["pages"][key] = None
                continue
            state["total_pages"][term], state["pages"][key] = parsed[0], rel
            for hit in parsed[1]:
                evidence.setdefault(str(hit["lotNo"]), []).append({"query": term, "page": page, "raw_file": rel,
                                                                    "item_name": hit.get("itemName"), "shop_id": SHOP})
        except fetch_au.BudgetExceeded as exc:
            stop_reason = f"budget: {exc}"
            break
        finally:
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    products, cells = base.rebuild_exports()
    states = Counter(r.get("state") for r in base.saved_records())
    print(json.dumps({"category": args.category, "side": "au", "stop": stop_reason, "http": client.charged_http,
                      "catalog_http": client.catalog_http, "this_call": client.charged_http - attempts0,
                      "states": dict(states), "sku_products": products, "sku_cells": cells,
                      "pending_hits": len([i for i in evidence if i not in old_ids and i not in done])}, ensure_ascii=False))


class SameStore(HTTPRedirectHandler):
    def __init__(self, hops: list):
        super().__init__()
        self.hops = hops

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urlparse(newurl)
        if p.scheme != "https" or p.netloc != "item.rakuten.co.jp":
            raise RuntimeError(f"redirect outside item.rakuten.co.jp: {newurl}")
        self.hops.append({"from": req.full_url, "to": newurl, "status": code})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def source_urls() -> dict[Path, str]:
    urls = {}
    for pattern, key in (("**/products.jsonl", "source_url"), ("**/catalog_manifest.jsonl", "category_url")):
        for root in [ROOT / OLD_RAKUTEN_GLOB, RUN / "*" / "rakuten"]:
            for path in glob.glob(str(root / pattern), recursive=True):
                for r in read_jsonl(Path(path)):
                    if r.get("raw_file") and r.get(key):
                        urls[(ROOT / r["raw_file"]).resolve()] = r[key]
    return urls


def au_basenames(category: str) -> list[str]:
    names, seeds = [], CATEGORIES[category]
    for path in list((RUN / category / "au" / "raw").glob("*-item.json")) + \
            [p for d in OLD_AU for p in list(d.glob("*-item.json")) + list((d / "raw").glob("*-item.json"))]:
        try:
            info = json.loads(path.read_text(encoding="utf-8")).get("itemInfo", {})
        except (OSError, json.JSONDecodeError):
            continue
        own = path.is_relative_to(RUN)
        if not own and not any(k in str(info.get("itemTitle") or info.get("itemName") or "") for k in seeds):
            continue
        images = info.get("productImageUrls") or []
        if images:
            names.append(urlparse(str(images[0])).path.rsplit("/", 1)[-1].casefold())
    return names


def rakuten(args) -> None:
    out = RUN / args.category / "rakuten"
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "categories").mkdir(parents=True, exist_ok=True)
    seeds = CATEGORIES[args.category]
    ledger_path = out / "http-ledger.jsonl"
    ledger = read_jsonl(ledger_path)
    old = json.loads((ROOT / ".lab-output/sku-real-rakuten-20261010-expanded/request-ledger.json").read_text(encoding="utf-8"))
    tried = {norm_url(u) for u in old["product_urls_attempted"] + old["catalog_urls_attempted"]}
    for path in glob.glob(str(ROOT / OLD_RAKUTEN_GLOB / "**/products.jsonl"), recursive=True):
        for r in read_jsonl(Path(path)):
            tried |= {norm_url(r[k]) for k in ("source_url", "requested_url") if r.get(k)}
    tried |= {norm_url(a["url"]) for a in ledger}
    index_path = out / "link-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    if index.pop("__version__", None) != INDEX_VERSION:
        index = {}
    page_urls = source_urls()
    html_files = [Path(p) for p in glob.glob(str(ROOT / OLD_RAKUTEN_GLOB / "**/*.html"), recursive=True)] + \
                 [Path(p) for p in glob.glob(str(RUN / "*" / "rakuten" / "*" / "*.html"))]
    for path in html_files:
        key = path.resolve().relative_to(ROOT.resolve()).as_posix()
        if key in index:
            continue
        page_url = page_urls.get(path.resolve(), "https://item.rakuten.co.jp/weimall/")
        html = path.read_bytes().decode("euc_jp", "replace")
        found = []
        for kind, parser in (("product", links.ProductLinks(page_url)), ("category", links.StoreCategories(page_url))):
            parser.feed(html)
            found += [{"kind": kind, "url": r["url"], "text": " ".join((" ".join(r["anchor_text"].split()) if isinstance(r["anchor_text"], str) else "",
                                                                        *r["image_alt"])).strip(),
                       "evidence_source_url": page_url} for r in parser.rows]
        found += [{"kind": "category", "url": m.replace("&amp;", "&"), "text": "", "evidence_source_url": page_url}
                  for m in dict.fromkeys(PAGINATION.findall(html))]
        index[key] = found
    index_path.write_text(json.dumps({"__version__": INDEX_VERSION, **index}, ensure_ascii=False), encoding="utf-8")
    names = au_basenames(args.category)
    all_seeds = [k for ks in CATEGORIES.values() for k in ks]
    candidates = {}
    for evidence_file, rows in index.items():
        for r in rows:
            url = norm_url(r["url"])
            if url in tried or url in candidates:
                continue
            valid = fetch_rakuten.valid_item_url(url) if r["kind"] == "product" else fetch_rakuten.valid_category_url(url)
            if not valid:
                continue
            slug = urlparse(url).path.strip("/").split("/")[-1].casefold()
            token = r["kind"] == "product" and any(re.search(r"(?<![a-z0-9])" + re.escape(slug) + r"(?:[-_.]|$)", n) for n in names)
            # The category only orders the queue: own links first, then links naming no category, then other
            # categories' links, so a link is never orphaned when its category finishes. Claims prevent refetching.
            keyword = any(k in r["text"] for k in seeds)
            group = 0 if keyword else 2 if any(k in r["text"] for k in all_seeds) else 1
            rank = 0 if token else 1 + group + (0 if r["kind"] == "product" else 3)
            candidates[url] = (rank, {**r, "url": url, "request_url": r["url"].split("#")[0], "evidence_raw_file": evidence_file,
                                      "evidence_kind": "observed_direct_same_store_anchor_in_captured_html",
                                      "priority": ["au_primary_image_token", "own_keyword_product", "unnamed_product",
                                                   "other_keyword_product", "own_keyword_category_page",
                                                   "unnamed_category_page", "other_keyword_category_page"][rank]})
    queue = [c for _, c in sorted(candidates.values(), key=lambda x: (x[0], x[1]["url"]))]
    throttle, start, calls, consecutive, stop_reason = Throttle("item.rakuten.co.jp", RAKUTEN_INTERVAL), time.time(), 0, 0, "chunk_limit"
    while queue and calls < args.max_attempts and time.time() - start < args.max_seconds:
        if len(ledger) >= RAKUTEN_MAX_HTTP:
            stop_reason = "category_http_cap"
            break
        cand = queue.pop(0)
        url = cand["url"]
        if not claim(f"rakuten:url:{url}", args.category):
            continue
        hops: list = []
        opener = build_opener(SameStore(hops))
        throttle.wait()
        started = now()
        attempt = {"attempt_id": f"r{len(ledger) + 1:05d}", "kind": cand["kind"], "url": url, "request_url": cand["request_url"], "started_at_utc": started,
                   "candidate_priority": cand["priority"]}
        status, raw, final_url, ctype, error = None, b"", url, "", None
        try:
            with opener.open(Request(cand["request_url"], headers={"User-Agent": fetch_rakuten.USER_AGENT, "Accept": "text/html"}), timeout=30) as resp:
                raw = resp.read(fetch_rakuten.MAX_BODY_BYTES + 1)
                status, final_url, ctype = resp.status, resp.geturl(), resp.headers.get("Content-Type", "")
                if len(raw) > fetch_rakuten.MAX_BODY_BYTES:
                    error, raw = "response exceeded 8 MiB capture cap", b""
        except HTTPError as exc:
            status, error = exc.code, f"HTTPError: {exc.code}"
        except (URLError, TimeoutError, RuntimeError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        calls += 1
        for hop in hops:
            ledger.append({"attempt_id": f"r{len(ledger) + 1:05d}", "kind": "redirect", "url": hop["from"], "final_url": hop["to"],
                           "http_status": hop["status"], "started_at_utc": started, "finished_at_utc": now()})
            append_jsonl(ledger_path, ledger[-1])
        attempt.update({"final_url": final_url, "http_status": status, "error": error, "finished_at_utc": now(),
                        "response_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest() if raw else None})
        if raw and not error and status == 200:
            if cand["kind"] == "product" and fetch_rakuten.valid_item_url(final_url):
                slug = urlparse(final_url).path.strip("/").split("/")[-1]
                dest = out / "raw" / f"{slug}-{hashlib.sha256(final_url.encode()).hexdigest()[:8]}.html"
                dest.write_bytes(raw)
                rel = dest.relative_to(ROOT).as_posix()
                record = {"requested_url": cand["request_url"], "url": final_url, "retrieved_at_utc": started, "raw_file": rel,
                          "status": status, "content_type": ctype, "candidate_evidence": cand}
                product, sku_rows = fetch_rakuten.parse_page(record, raw)
                append_jsonl(out / "products.jsonl", product)
                for row in sku_rows:
                    append_jsonl(out / "skus.jsonl", row)
                attempt["raw_file"] = rel
            elif cand["kind"] == "category" and fetch_rakuten.valid_category_url(final_url):
                dest = out / "categories" / f"{hashlib.sha256(final_url.encode()).hexdigest()[:12]}.html"
                dest.write_bytes(raw)
                rel = dest.relative_to(ROOT).as_posix()
                append_jsonl(out / "catalog_manifest.jsonl", {"category_url": final_url, "requested_url": cand["request_url"], "raw_file": rel,
                                                              "sha256": attempt["sha256"], "retrieved_at_utc": started,
                                                              "http_status": status, "candidate_evidence": cand})
                attempt["raw_file"] = rel
            else:
                attempt["error"] = f"final URL outside expected {cand['kind']} scope: {final_url}"
        ledger.append(attempt)
        append_jsonl(ledger_path, attempt)
        if attempt.get("error") or status != 200:
            append_jsonl(out / "errors.jsonl", attempt)
            consecutive += 1
            if status in (403, 429):
                stop_reason = f"blocked_status_{status}"
                break
            if consecutive >= 10:
                stop_reason = "ten_consecutive_failures"
                break
        else:
            consecutive = 0
    if not queue:
        stop_reason = "candidates_exhausted"
    products = read_jsonl(out / "products.jsonl")
    print(json.dumps({"category": args.category, "side": "rakuten", "stop": stop_reason, "http": len(ledger), "this_call": calls,
                      "product_pages": len(products), "with_skus": sum(1 for p in products if p.get("sku_count")),
                      "sku_rows": len(read_jsonl(out / "skus.jsonl")), "queue_left": len(queue)}, ensure_ascii=False))


def status(args) -> None:
    rows = {}
    for category in CATEGORIES:
        au_dir, rk_dir = RUN / category / "au", RUN / category / "rakuten"
        au_states = Counter(json.loads(p.read_text(encoding="utf-8")).get("state") for p in (au_dir / "records").glob("*.json")) \
            if (au_dir / "records").exists() else Counter()
        au_http = len(json.loads((au_dir / "http-ledger-state.json").read_text(encoding="utf-8"))["attempts"]) \
            if (au_dir / "http-ledger-state.json").exists() else 0
        products = read_jsonl(rk_dir / "products.jsonl")
        rows[category] = {"au_http": au_http, "au_states": dict(au_states), "rakuten_http": len(read_jsonl(rk_dir / "http-ledger.jsonl")),
                          "rakuten_pages": len(products), "rakuten_with_skus": sum(1 for p in products if p.get("sku_count")),
                          "rakuten_sku_rows": len(read_jsonl(rk_dir / "skus.jsonl"))}
    print(json.dumps(rows, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("side", choices=["au", "rakuten", "status"])
    parser.add_argument("--category", choices=sorted(CATEGORIES))
    parser.add_argument("--max-attempts", type=int, default=300)
    parser.add_argument("--max-seconds", type=int, default=480)
    args = parser.parse_args()
    if args.side != "status" and not args.category:
        parser.error("--category is required")
    {"au": au, "rakuten": rakuten, "status": status}[args.side](args)


if __name__ == "__main__":
    main()
