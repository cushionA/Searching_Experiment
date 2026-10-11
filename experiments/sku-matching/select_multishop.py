#!/usr/bin/env python3
"""Fold same-pattern products of the multi-shop capture into a balanced selection (nothing is deleted).

Unit = one Rakuten product with all of its AU siblings (so sibling splits such as 1-leg / 4-leg sets stay together).
Pattern = (shop, Rakuten axis labels without a leading "<product number>/"). At most --per-pattern products are kept per pattern; products with more than
--max-sku SKU rows are folded as oversized. Every link gets a decision and a reason in links.jsonl; the kept products
and their SKU rows are copied (rows only, raw_file paths unchanged) into <out>/au and <out>/rakuten, a table pair the
pair builder reads. The category is a title-word guess (true_category), because the capture directories were searched
by keyword and are not category-clean.
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / ".lab-output/sku-independent-real-20261011"
CATS = ("apparel", "food", "beauty")
CAT_WORDS = {
    "apparel": r"ワンピース|Tシャツ|Ｔシャツ|パンツ|シャツ|スカート|ジャケット|コート|ニット|デニム|靴|スニーカー|シューズ|バッグ|鞄|帽子|キャップ|ブーツ|サンダル|靴下|ソックス|下着|ショーツ|ブラ|水着|パーカー|カーディガン|ブラウス|レギンス|ストッキング|タイツ|トップス|ドレス|レディース|メンズ|キッズ|ベスト|スーツ|ルームウェア|パジャマ|マフラー|手袋|ベルト|財布|サングラス|アクセサリー|ネックレス|ピアス|リング",
    "beauty": r"シャンプー|トリートメント|コンディショナー|化粧水|乳液|美容液|ファンデ|リップ|マスカラ|アイシャドウ|ヘアオイル|ヘアケア|ボディソープ|洗顔|日焼け止め|ネイル|香水|入浴剤|歯磨き|フェイスパック|フェイスマスク|クリーム|石鹸|コスメ|スキンケア|メイク|ヘアカラー|ボディケア|ハンドクリーム|美顔",
    "food": r"米|コーヒー|珈琲|紅茶|茶葉|お茶|ナッツ|プロテイン|ジュース|炭酸水|麺|パスタ|調味料|菓子|チョコ|海苔|味噌|醤油|食用油|冷凍|ドライフルーツ|ビール|ワイン|日本酒|焼酎|食品|スナック|ふりかけ|ソース|サプリ|ドリンク|グミ|飴|ゼリー|スープ|ミネラルウォーター|飲料|肉|魚|野菜|果物|はちみつ|砂糖|乳酸菌",
}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def true_category(text: str) -> str:
    scores = {c: len(re.findall(p, text)) for c, p in CAT_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def links() -> list[dict]:
    rows = []
    for cat in CATS:
        rk = {p["source_url"]: p for p in read_jsonl(RUN / cat / "rakuten/products.jsonl") if p.get("sku_count")}
        state_path = RUN / cat / "rakuten/ms-state.json"
        if not state_path.exists():
            continue
        items = json.loads(state_path.read_text(encoding="utf-8"))["items"]
        recs = {}
        for f in glob.glob(str(RUN / cat / "au/records/*.json")):
            r = json.loads(Path(f).read_text(encoding="utf-8", errors="replace"))
            recs[str(r["item_id"])] = r
        for au_id, v in items.items():
            url = v.get("url")
            if not url or url not in rk or recs.get(au_id, {}).get("state") != "sku_product":
                continue
            p = rk[url]
            title = str(p.get("title") or "")
            au_title = str(recs[au_id].get("item_title") or "")
            rows.append({"dir_category": cat, "au_item_id": au_id, "au_shop_id": str(recs[au_id].get("shop_id") or ""),
                         "rakuten_url": url, "shop": url.split("/")[3],
                         "rakuten_manage": url.rstrip("/").split("/")[-1], "sku_count": p.get("sku_count"),
                         "rakuten_axes": [x.get("axis_label") or x.get("label") or "" for x in (p.get("axes") or [])],
                         "rakuten_title": title, "au_title": au_title, "true_category": true_category(title + " " + au_title)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="output folder name under multishop/ (must not exist)")
    ap.add_argument("--per-pattern", type=int, default=3)
    ap.add_argument("--max-sku", type=int, default=60)
    ap.add_argument("--only-relaxed", action="store_true",
                    help="keep only shops confirmed by collect_multishop qualify2 (title-agreement alias), i.e. not the strict name rule")
    args = ap.parse_args()
    out = RUN / "multishop" / args.name
    if out.exists():
        raise SystemExit(f"{out} exists")
    rows = links()
    if args.only_relaxed:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import collect_multishop as ms
        relaxed = {t["shop_id"] for t in ms.ci.read_jsonl(ms.MS / "shop-tests2.jsonl") if t["hit"]}
        rows = [r for r in rows if r["au_shop_id"] in relaxed]
    by_url: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_url[r["rakuten_url"]].append(r)
    decision: dict[str, tuple[str, str]] = {}
    kept_per_pattern: dict[tuple, int] = collections.Counter()
    for url in sorted(by_url, key=lambda u: hashlib.sha1(u.encode()).hexdigest()):
        first = by_url[url][0]
        key = (first["shop"], tuple(a.split("/")[-1] for a in first["rakuten_axes"]))
        if first["sku_count"] > args.max_sku:
            decision[url] = ("folded", "oversized")
        elif kept_per_pattern[key] >= args.per_pattern:
            decision[url] = ("folded", "same_pattern")
        else:
            kept_per_pattern[key] += 1
            decision[url] = ("kept", "within_pattern_quota")
    for r in rows:
        r["decision"], r["reason"] = decision[r["rakuten_url"]]
    kept_urls = {u for u, d in decision.items() if d[0] == "kept"}
    kept_au = {r["au_item_id"] for r in rows if r["decision"] == "kept"}
    write_jsonl(out / "links.jsonl", rows)
    au_products, au_skus, rk_products, rk_skus = [], [], [], []
    for cat in CATS:
        au_products += [p for p in read_jsonl(RUN / cat / "au/products.jsonl") if str(p.get("item_id")) in kept_au]
        au_skus += [s for s in read_jsonl(RUN / cat / "au/skus.jsonl") if str(s.get("item_id")) in kept_au]
        rk_products += [p for p in read_jsonl(RUN / cat / "rakuten/products.jsonl") if p.get("source_url") in kept_urls]
        rk_skus += [s for s in read_jsonl(RUN / cat / "rakuten/skus.jsonl") if s.get("source_url") in kept_urls]
    write_jsonl(out / "au/products.jsonl", au_products)
    write_jsonl(out / "au/skus.jsonl", au_skus)
    write_jsonl(out / "rakuten/products.jsonl", rk_products)
    write_jsonl(out / "rakuten/skus.jsonl", rk_skus)
    kept = [r for r in rows if r["decision"] == "kept"]
    sku_of = {u: by_url[u][0]["sku_count"] for u in kept_urls}
    summary = {
        "params": {"per_pattern": args.per_pattern, "max_sku": args.max_sku},
        "links": len(rows), "rakuten_products": len(by_url), "kept_rakuten_products": len(kept_urls), "kept_au_items": len(kept_au),
        "kept_rakuten_sku_rows": sum(sku_of.values()), "kept_cases_sku_x_au": sum(sku_of[r["rakuten_url"]] for r in kept),
        "folded": dict(collections.Counter(r["reason"] for r in rows if r["decision"] == "folded")),
        "kept_by_true_category": dict(collections.Counter(by_url[u][0]["true_category"] for u in kept_urls)),
        "kept_by_shop": dict(collections.Counter(by_url[u][0]["shop"] for u in kept_urls).most_common()),
        "table_rows": {"au_products": len(au_products), "au_skus": len(au_skus), "rakuten_products": len(rk_products), "rakuten_skus": len(rk_skus)},
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
