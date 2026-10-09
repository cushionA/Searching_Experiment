#!/usr/bin/env python3
"""Extract direct WEIMALL product links from already captured item pages."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict, deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse


ROOT = Path(__file__).resolve().parents[2]


def raw_path_key(raw_file: str) -> Path:
    path = Path(raw_file)
    return (path if path.is_absolute() else ROOT / path).resolve()


class ProductLinks(HTMLParser):
    def __init__(self, page_url: str):
        super().__init__()
        self.page_url = page_url
        self.rows: list[dict] = []
        self.current: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            href = attrs.get("href") or ""
            url = urljoin(self.page_url, href)
            p = urlparse(url)
            if p.scheme == "https" and p.netloc == "item.rakuten.co.jp" and p.path.startswith("/weimall/"):
                segments = p.path.strip("/").split("/")
                if len(segments) == 2 and segments[1] != "c":
                    canonical = urlunparse((p.scheme, p.netloc, p.path.rstrip("/") + "/", "", "", ""))
                    self.current = {"url": canonical, "observed_href": href, "anchor_text": "", "image_alt": []}
                    self.rows.append(self.current)
        elif self.current and tag in ("img", "input"):
            alt = attrs.get("alt") or attrs.get("title")
            if alt:
                self.current["image_alt"].append(alt)

    def handle_data(self, data):
        if self.current:
            self.current["anchor_text"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None


class StoreCategories(HTMLParser):
    def __init__(self, page_url: str):
        super().__init__()
        self.page_url = page_url
        self.rows: list[dict] = []
        self.current: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            href = attrs.get("href") or ""
            url = urljoin(self.page_url, href)
            p = urlparse(url)
            segments = p.path.strip("/").split("/")
            if (p.scheme == "https" and p.netloc == "item.rakuten.co.jp"
                    and len(segments) >= 2 and segments[0] == "weimall" and segments[1] == "c"):
                canonical = urlunparse((p.scheme, p.netloc, p.path.rstrip("/") + "/", "", "", ""))
                self.current = {"url": canonical, "observed_href": href, "anchor_text": "", "image_alt": []}
                self.rows.append(self.current)
        elif self.current and tag in ("img", "input"):
            alt = attrs.get("alt") or attrs.get("title")
            if alt:
                self.current["image_alt"].append(alt)

    def handle_data(self, data):
        if self.current:
            self.current["anchor_text"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None


def extract_categories_main():
    ap = argparse.ArgumentParser(description="Extract only observed WEIMALL /c/ category links from captured HTML")
    ap.add_argument("--source-dir", type=Path, action="append", required=True)
    ap.add_argument("--au-products", type=Path, required=True, help="real catalog titles used only to prioritize observed category links")
    ap.add_argument("--exclude-urls", type=Path, action="append", default=[])
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--max-selected", type=int, default=40)
    args = ap.parse_args()
    if not 1 <= args.max_selected <= 100:
        raise SystemExit("max-selected must be 1..100; category pages share the stage2 HTTP budget")
    au_titles = [json.loads(x).get("item_title", "") for x in args.au_products.read_text(encoding="utf-8").splitlines() if x.strip()]
    au_titles = [x for x in au_titles if x]
    excluded = set()
    for path in args.exclude_urls:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                excluded.add(row.get("url") or row.get("category_url") or "")
    meta_by_raw = {}
    for source_dir in args.source_dir:
        for name in ("products.jsonl", "catalog_manifest.jsonl"):
            for meta_path in source_dir.rglob(name):
                for line in meta_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    raw_file = row.get("raw_file")
                    source_url = row.get("source_url") or row.get("category_url")
                    if raw_file and source_url:
                        meta_by_raw[raw_path_key(raw_file)] = row
    found = {}
    for source_dir in args.source_dir:
        for html_path in source_dir.rglob("*.html"):
            raw = html_path.read_bytes()
            meta = meta_by_raw.get(html_path.resolve())
            if not meta:
                continue
            rel_path = html_path.resolve().relative_to(ROOT.resolve()).as_posix() if html_path.resolve().is_relative_to(ROOT.resolve()) else str(html_path.resolve())
            source_url = meta.get("source_url") or meta.get("category_url")
            html = raw.decode("euc_jp", "replace")
            parser = StoreCategories(source_url)
            parser.feed(html)
            for row in parser.rows:
                if row["url"] in excluded:
                    continue
                row["anchor_text"] = " ".join(row["anchor_text"].split())
                row["image_alt"] = list(dict.fromkeys(x.strip() for x in row["image_alt"] if x.strip()))
                link_text = " ".join([row["anchor_text"], " ".join(row["image_alt"])])
                link_score = max((_overlap(link_text, title) for title in au_titles), default=0.0)
                source_title = meta.get("title") or ""
                source_score = max((_overlap(source_title, title) for title in au_titles), default=0.0)
                candidate = {**row,
                    "evidence_source_url": source_url,
                    "evidence_raw_file": rel_path,
                    "evidence_html_sha256": hashlib.sha256(raw).hexdigest(),
                    "evidence_kind": "observed_direct_same_store_category_anchor_in_fresh_html",
                    "au_title_link_similarity": round(link_score, 4),
                    "au_title_source_similarity": round(source_score, 4)}
                prev = found.get(row["url"])
                # The visible category anchor is stronger evidence of the
                # category itself. The source product title only breaks ties
                # when anchor labels are weak or absent.
                rank = link_score + source_score * 0.05
                if not prev or rank > prev[0]:
                    found[row["url"]] = (rank, candidate)
    candidates = [x[1] for x in sorted(found.values(), key=lambda x: -x[0])]
    selected = candidates[:args.max_selected]
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "all_observed_category_links.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in candidates), encoding="utf-8")
    (out / "selected_urls.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in selected), encoding="utf-8")
    print(json.dumps({"unique_observed_category_urls": len(candidates), "selected": len(selected),
                      "output": str(out)}, ensure_ascii=False))


def _overlap(left: str, right: str) -> float:
    import re
    def grams(value):
        value = re.sub(r"[^一-龥ぁ-んァ-ヴーa-z0-9]", "", (value or "").lower())
        return {value[i:i+2] for i in range(max(0, len(value)-1))}
    a, b = grams(left), grams(right)
    return len(a & b) / max(1, len(a | b))


def main():
    import sys
    if "--extract-categories" in sys.argv:
        sys.argv.remove("--extract-categories")
        extract_categories_main()
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--known-products", type=Path, required=True)
    ap.add_argument("--exclude-urls", type=Path)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--max-selected", type=int, default=80)
    args = ap.parse_args()
    if not 0 <= args.max_selected <= 80:
        raise SystemExit("max-selected must be <= 80 (remaining cumulative page budget)")
    known_rows = [json.loads(line) for line in args.known_products.read_text(encoding="utf-8").splitlines() if line.strip()]
    known = {row["source_url"] for row in known_rows if row.get("source_url")}
    meta_by_raw = {raw_path_key(row["raw_file"]): row for row in known_rows if row.get("raw_file") and row.get("source_url")}
    if args.exclude_urls:
        for line in args.exclude_urls.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            url = row.get("url") or row.get("source_url") or ""
            if url:
                known.add(url)
    by_source: dict[str, list[dict]] = defaultdict(list)
    for html_path in sorted(args.source_dir.glob("*.html")):
        raw = html_path.read_bytes()
        # Captured Rakuten pages declare EUC-JP; each candidate URL is traced to
        # this fresh response and its digest, without making another request.
        html = raw.decode("euc_jp", "replace")
        meta = meta_by_raw.get(html_path.resolve())
        if not meta:
            continue
        page_url = meta.get("source_url")
        parser = ProductLinks(page_url)
        parser.feed(html)
        seen = set()
        for row in parser.rows:
            if row["url"] in known or row["url"] in seen:
                continue
            seen.add(row["url"])
            row["anchor_text"] = " ".join(row["anchor_text"].split())
            row["image_alt"] = list(dict.fromkeys(x.strip() for x in row["image_alt"] if x.strip()))
            by_source[page_url].append({
                **row,
                "evidence_source_url": page_url,
                "evidence_raw_file": html_path.resolve().relative_to(ROOT.resolve()).as_posix() if html_path.resolve().is_relative_to(ROOT.resolve()) else str(html_path.resolve()),
                "evidence_html_sha256": hashlib.sha256(raw).hexdigest(),
                "evidence_kind": "observed_direct_same_store_anchor_in_fresh_item_html",
            })
    # Round-robin across the observed item pages so a single page's very long
    # recommendation list cannot consume the whole remaining request budget.
    queues = {k: deque(v) for k, v in sorted(by_source.items())}
    selected = []
    selected_urls = set()
    while queues and len(selected) < args.max_selected:
        for key in list(queues):
            while queues[key] and len(selected) < args.max_selected:
                candidate = queues[key].popleft()
                if candidate["url"] not in selected_urls:
                    selected.append(candidate)
                    selected_urls.add(candidate["url"])
                    break
            if not queues[key]:
                del queues[key]
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "all_observed_same_store_links.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for key in sorted(by_source) for row in by_source[key]), encoding="utf-8")
    (out / "selected_urls.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected), encoding="utf-8")
    print(json.dumps({"observed_link_edges": sum(map(len, by_source.values())),
                      "unique_candidate_urls": len({r["url"] for rows in by_source.values() for r in rows}),
                      "selected": len(selected),
                      "source_pages": len(by_source), "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
