#!/usr/bin/env python3
"""Blind, dossier-based semantic labels for shard-3 real AU/Rakuten SKU cases.

This deliberately does not import or consult the project matcher/model/labels.
Every decision is based on the frozen Luna dossier and the selected Rakuten SKU.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v2"
SHARD = INPUT / "shard-3"
OUT_DIR = ROOT / ".lab-output/sku-real-luna-labels-20261010-v1"
OUT = OUT_DIR / "shard-3-labels.jsonl"
REVIEW = OUT_DIR / "shard-3-review.json"


INTERPRETATIONS = {
    "pair-f7168991efbedd25": "AU商品詳細は3段タイプと明記。Rakutenの段数は2/3/4/5段を個別判定し、3段のみ対象。色はAU SKU行の同色を使う。",
    "pair-d52308682f40f75e": "AU個別ページはシングル・三つ折り・10cm・190N・フラットタイプ。Rakutenもシングルかつフラットのみ。AU SKU行はパイル/メッシュを分けるため、生地表記がないRakuten色はパイルと解釈し、メッシュ表記はメッシュ行に限定。",
    "pair-9b6887406ebac79f": "AU個別ページはキング・三つ折り・10cm・190N・フラットタイプ。Rakutenもキングかつフラットのみ。AU SKU行に生地別行がないのでメッシュ指定は対象外とし、無指定色は掲載色行に照合。",
    "pair-568fa1288eeb395d": "AU個別ページはダブル敷きパッド。Rakutenのサイズがダブルで、色がAU SKU行と一致する場合のみ該当。追加の毛布セットは敷きパッド単品と区別。",
    "pair-e4ce6dafcff3b3c1": "AU個別ページは50×50cm・13枚（パネル12枚とドア1枚）のペットフェンス。Rakutenもサイズ・セット内容・透明/半透明の3仕様を照合。",
    "pair-2731f3cede6c0af8": "AU個別ページは幅78×高さ50cmの洗車台で、天板は約78×30cm。高さ・天板寸法の両方が一致し、色もAU SKU行にある場合のみ該当。",
    "pair-acad8f7195cf2571": "AU個別ページは4段・グレーのおもちゃ収納ラック（約64×35×90cm）。Rakutenのスリム仕様は寸法差を原文から確定できないためreview。天板付き/本棚付きは個別構造が異なる。",
    "pair-bb5b9f3f3bd988f6": "AU個別ページは直径80cmの丸テーブル。Rakutenは色と直径を照合し、60cmは同シリーズでも別寸法なので対象外。",
    "pair-27f740543096bdc8": "AU商品は厚さ24cmの肘掛け付き4WAY座椅子。Rakutenの肘掛けなし/回転タイプは別構造。4WAYタイプは色およびマイヤー生地の表示をAU SKU行に照合。",
    "pair-cf985ab4f6713acb": "AU個別ページは直径60cmの丸テーブル。Rakutenは色と直径を照合し、80cmは別寸法なので対象外。",
    "pair-7ef19fdb139adda3": "AU個別ページは15kg耐荷重の分離型3WAYペットカート。色をAU SKU行に照合し、Rakuten商品説明の同じ15kg仕様を確認。",
    "pair-dbfc4eee30fecdb9": "AU個別ページは2026プレミアム分離型3WAYペットカート、耐荷重20kg。色をAU SKU行に照合し、Rakuten説明の20kg仕様を確認。",
    "pair-f67866cde828705b": "AU個別ページは360度回転・肘掛け付き・14段ギアの座椅子。色をAU SKU行に照合。",
    "pair-4cfc3bf55d3e2256": "AU個別ページは幅80×奥行40×高さ183cm、2WAY・最大5段のスチールラック。色がAU SKU行に一致。",
    "pair-4da15ed2be5f6397": "AU個別ページは幅80×奥行40×高さ183cm、2WAY・最大5段のスチールラック。色がAU SKU行に一致。",
    "pair-94632c8e98bf7c29": "AU個別ページは幅50×奥行30×高さ80cm・3段・ポール径19mmのスチールラック。色がAU SKU行に一致。",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def exact_quote(text: str, max_len: int = 110) -> str:
    """Return a contiguous exact excerpt, keeping citations readable."""
    text = str(text)
    if len(text) <= max_len:
        return text
    return text[:max_len]


def option_values(case):
    return [str(x.get("value", "")) for x in case["rakuten"].get("option_values", [])]


def au_row_color(row):
    return next((str(a.get("value_raw", "")) for a in row.get("axes_raw", []) if a.get("value_raw")), "")


def choose_au_quote(dossier, case, decision):
    did = dossier["dossier_id"]
    au = dossier["au_product"]
    text = au["title_raw"]
    if did == "pair-f7168991efbedd25":
        text = next((x["text"] for x in au["description"]["blocks"] if x["text"] == "・3段タイプ"), "3段")
    elif did in ("pair-d52308682f40f75e", "pair-9b6887406ebac79f"):
        keys = ("こちらのページは", "タイプ", "厚みたっぷり")
        text = next((x["text"] for x in au["description"]["blocks"] if x["text"].startswith(keys[0])), au["title_raw"])
    elif did == "pair-e4ce6dafcff3b3c1":
        text = next(x["text"] for x in au["description"]["blocks"] if x["text"].startswith("こちらのページは"))
    elif did == "pair-2731f3cede6c0af8":
        text = next(x["text"] for x in au["description"]["blocks"] if x["text"].startswith("こちらのページは"))
    elif did in ("pair-bb5b9f3f3bd988f6", "pair-cf985ab4f6713acb"):
        text = next(x["text"] for x in au["description"]["blocks"] if "直径" in x["text"] and "高さ74cm" in x["text"])
    elif did == "pair-acad8f7195cf2571":
        text = next(x["text"] for x in au["description"]["blocks"] if x["text"].startswith("本体："))
    elif did in ("pair-27f740543096bdc8", "pair-f67866cde828705b"):
        text = next(x["text"] for x in au["description"]["blocks"] if "商 品 詳 細" in x["text"] or "肘掛け付き回転座椅子" in x["text"])
    elif did in ("pair-4cfc3bf55d3e2256", "pair-4da15ed2be5f6397", "pair-94632c8e98bf7c29"):
        text = next(x["text"] for x in au["description"]["blocks"] if "（約）幅" in x["text"] and "高さ" in x["text"])
    elif did in ("pair-7ef19fdb139adda3", "pair-dbfc4eee30fecdb9"):
        text = next(x["text"] for x in au["description"]["blocks"] if "耐荷重" in x["text"] and "kg" in x["text"])
    elif did == "pair-568fa1288eeb395d":
        text = next((x["text"] for x in au["description"]["blocks"] if "ダブル" in x["text"]), au["title_raw"])
    return text


def decide(case, dossier):
    did = dossier["dossier_id"]
    vals = option_values(case)
    rows = dossier["au_rows"]
    colors = {au_row_color(r): r["row_key"] for r in rows}
    matched_key = None
    decision = "unmatched"
    rationale = "Rakutenの選択仕様がAU個別商品ページの仕様と一致しません。"

    if did == "pair-f7168991efbedd25":
        step, color = vals[0], vals[1]
        if step == "3段" and color in colors:
            decision, matched_key = "matched", colors[color]
            rationale = f"AU個別ページは3段タイプで、色{color}のSKU行があります。Rakuten選択は3段・{color}です。"
        else:
            rationale = f"AU個別ページ詳細は3段タイプですが、Rakuten選択は{step}・{color}です。"
    elif did in ("pair-d52308682f40f75e", "pair-9b6887406ebac79f"):
        typ, size, color = vals
        target_size = "シングル" if did == "pair-d52308682f40f75e" else "キング"
        # This AU page exposes both pile and mesh rows; a Rakuten color without a
        # fabric suffix denotes the dossier's pile-color option, while mesh is explicit.
        au_colors = {au_row_color(r): r["row_key"] for r in rows}
        color_candidates = []
        if "(メッシュ)" in color or "（メッシュ）" in color:
            base = color.replace("(メッシュ)", "").replace("（メッシュ）", "")
            for suffix in ("（メッシュ）", "(メッシュ)"):
                color_candidates.append(base + suffix)
        elif "(" not in color and "（" not in color:
            color_candidates.append(color + "（パイル）" if did == "pair-d52308682f40f75e" else color)
        if typ == "フラットタイプ" and size == target_size:
            for candidate in color_candidates:
                if candidate in au_colors:
                    decision, matched_key = "matched", au_colors[candidate]
                    fabric = "メッシュ" if "メッシュ" in candidate else "パイル" if "パイル" in candidate else "無指定"
                    rationale = f"AU個別ページの{target_size}・フラットタイプと一致し、色・生地{candidate}に対応するAU SKU行があります。"
                    break
        if decision != "matched":
            rationale = f"AU個別ページは{target_size}・フラットタイプですが、Rakuten選択は{typ}・{size}・{color}です。サイズ/タイプ/掲載生地のいずれかが異なります。"
    elif did == "pair-568fa1288eeb395d":
        size, color, extra = vals
        # This page is a double-size heated mattress pad. A bundled blanket is a
        # different sellable combination from the AU single pad SKU.
        if size == "ダブル" and extra == "なし" and color in colors:
            decision, matched_key = "matched", colors[color]
            rationale = f"AU個別ページはダブル敷きパッドで、色{color}の行があります。Rakuten選択もダブル・{color}・敷きパッド単品です。"
        else:
            rationale = f"AU個別ページはダブル敷きパッドですが、Rakuten選択は{size}・{color}・{extra}です。"
    elif did == "pair-e4ce6dafcff3b3c1":
        bundle, size, color = vals
        au_size = "50×50cm"
        au_bundle = "13枚セット(パネル12枚＋ドア1枚)"
        if bundle == au_bundle and size == au_size and color in colors:
            decision, matched_key = "matched", colors[color]
            rationale = f"AU個別ページの13枚セット・{au_size}・{color}と選択仕様が一致します。"
        else:
            rationale = f"AU個別ページは{au_bundle}・{au_size}・{color if color in colors else '掲載色'}です。Rakuten選択は{bundle}・{size}・{color}です。"
    elif did == "pair-2731f3cede6c0af8":
        height, board, color = vals
        if height == "50cm" and board == "78cm×30cm" and color in colors:
            decision, matched_key = "matched", colors[color]
            rationale = f"AU個別ページの高さ50cm・天板78×30cm・{color}に一致します。"
        else:
            rationale = f"AU個別ページは高さ50cm・天板78×30cmです。Rakuten選択は高さ{height}・天板{board}・{color}です。"
    elif did == "pair-acad8f7195cf2571":
        typ, color = vals[0].split(" / ", 1)
        if typ == "スリム" and color == "グレー":
            decision = "review"
            rationale = "AUページはグレーの4段ラックですが、Rakutenのスリム仕様の寸法・棚構成が引用テキストでは確認できず、同一性を確定できません。"
        else:
            rationale = f"AU個別ページはグレーの4段収納ラックです。Rakutenの「{typ} / {color}」は天板付きまたは本棚付きの木製構成で、AU掲載SKUの仕様と異なります。"
    elif did in ("pair-bb5b9f3f3bd988f6", "pair-cf985ab4f6713acb"):
        color, diameter = vals
        target = "80cm(直径)" if did == "pair-bb5b9f3f3bd988f6" else "60cm(直径)"
        if diameter == target and color in colors:
            decision, matched_key = "matched", colors[color]
            rationale = f"AU個別ページの直径{target[:2]}cmと色{color}に一致します。"
        else:
            rationale = f"AU個別ページは直径{target[:2]}cm・{color if color in colors else '掲載色'}ですが、Rakuten選択は{diameter}・{color}です。"
    elif did == "pair-27f740543096bdc8":
        typ, color = vals
        if typ == "4WAYタイプ" and color in colors:
            decision, matched_key = "matched", colors[color]
            rationale = f"AU個別ページの肘掛け付き4WAY座椅子で、色/生地{color}のSKU行に一致します。"
        else:
            rationale = f"AU個別ページは肘掛け付き4WAY座椅子ですが、Rakuten選択{typ}・{color}は回転型/肘掛けなし、またはAU SKUにない色・生地です。"
    elif did in ("pair-7ef19fdb139adda3", "pair-dbfc4eee30fecdb9"):
        color = vals[0]
        if color in colors:
            decision, matched_key = "matched", colors[color]
            kg = "15kg" if did == "pair-7ef19fdb139adda3" else "20kg"
            rationale = f"AU個別ページとRakuten説明は同じ分離型3WAYペットカート（耐荷重{kg}）で、色{color}のAU SKU行があります。"
        else:
            rationale = f"Rakuten選択色{color}に対応するAU SKU行がありません。"
    elif did == "pair-f67866cde828705b":
        color = vals[0]
        if color in colors:
            decision, matched_key = "matched", colors[color]
            rationale = f"双方とも360度回転・肘掛け付きの14段階ギア座椅子で、色{color}に対応します。"
        else:
            rationale = f"Rakuten選択色{color}に対応するAU SKU行がありません。"
    elif did in ("pair-4cfc3bf55d3e2256", "pair-4da15ed2be5f6397", "pair-94632c8e98bf7c29"):
        color = vals[0]
        if color in colors:
            decision, matched_key = "matched", colors[color]
            rationale = f"AU個別ページのサイズ/段数/構造とRakuten商品説明のラック仕様が一致し、色{color}のAU SKU行があります。"
        else:
            rationale = f"Rakuten選択色{color}に対応するAU SKU行がありません。"

    return decision, ([matched_key] if decision == "matched" else []), rationale


def make_label(case, dossier):
    decision, row_keys, rationale = decide(case, dossier)
    au = dossier["au_product"]
    rak = dossier["rakuten_product"]
    row_map = {r["row_key"]: r for r in dossier["au_rows"]}
    values = option_values(case)
    au_quote = choose_au_quote(dossier, case, decision)
    au_ref = "$.itemInfo.itemTitle"
    au_source = "au_title"
    for block in au.get("description", {}).get("blocks", []):
        if block.get("text") == au_quote:
            au_ref = block.get("source_field", au_ref)
            au_source = "au_description"
            break
    # The selected row and combination below are exact strings from the assigned
    # sources, giving every case traceable evidence from both marketplaces.
    ev = [
        {"source": au_source,
         "source_ref": au_ref,
         "quote": exact_quote(au_quote)},
        {"source": "rakuten_title",
         "source_ref": rak.get("source", {}).get("json_path", "products.jsonl"),
         "quote": exact_quote(rak["title_raw"])},
    ]
    for option in case["rakuten"].get("option_values", []):
        ev.append({"source": "rakuten_axes",
                   "source_ref": case["rakuten"]["source"]["source_row_key"],
                   "quote": exact_quote(option.get("value", ""))})
    if dossier["dossier_id"] in ("pair-d52308682f40f75e", "pair-9b6887406ebac79f"):
        type_block = next((x for x in au["description"]["blocks"] if x["text"] == "フラットタイプ"), None)
        if type_block:
            ev.append({"source": "au_description", "source_ref": type_block.get("source_field", "$.itemInfo.extraItemComment"), "quote": type_block["text"]})
    if row_keys:
        row = row_map[row_keys[0]]
        row_value = au_row_color(row)
        ev.append({"source": "au_row", "source_ref": row["row_key"], "quote": exact_quote(row_value)})
    else:
        # Add AU individual SKU context to support a transparent negative/review.
        if dossier["au_rows"]:
            row = dossier["au_rows"][0]
            ev.append({"source": "au_row", "source_ref": row["row_key"], "quote": exact_quote(au_row_color(row))})
    return {
        "case_id": case["case_id"],
        "decision": decision,
        "matching_au_row_keys": row_keys,
        "rationale": rationale,
        "evidence": ev,
    }


def main():
    if OUT.exists() or REVIEW.exists():
        raise SystemExit(f"refusing to overwrite existing labels/review: {OUT_DIR}")
    cases_path = SHARD / "cases.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line]
    dossiers = {p.stem: read_json(p) for p in (INPUT / "dossiers").glob("*.json")}
    if len(cases) != 321:
        raise SystemExit(f"expected 321 shard-3 cases; saw {len(cases)}")
    labels = []
    pair_stats = {}
    pair_cases = collections.defaultdict(list)
    for case in cases:
        pair_cases[case["dossier_id"]].append(case)
        dossier = dossiers[case["dossier_id"]]
        labels.append(make_label(case, dossier))
    counts = collections.Counter(x["decision"] for x in labels)
    label_by_id = {x["case_id"]: x for x in labels}
    for did, members in pair_cases.items():
        sub_counts = collections.Counter(label_by_id[c["case_id"]]["decision"] for c in members)
        pair_stats[did] = {
            "au_item_id": dossiers[did]["au_product"]["product_id"],
            "pair_ref": dossiers[did]["pair_ref"],
            "case_count": len(members),
            "label_counts": {k: sub_counts.get(k, 0) for k in ("matched", "unmatched", "review")},
            "spec_interpretation": INTERPRETATIONS[did],
        }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT.open("x", encoding="utf-8") as f:
        f.write("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in labels))
    manifest_path = INPUT / "manifest.json"
    shard_manifest_path = SHARD / "manifest.json"
    report = {
        "schema_version": "luna-sku-annotation-review-v1",
        "annotator_model": "gpt-6-luna",
        "task": "independent semantic identity review of real AU Pay Market and Rakuten SKU combinations",
        "shard_id": "shard-3",
        "case_count": len(labels),
        "input_sha256": {
            "cases_jsonl": sha256(cases_path),
            "input_manifest_json": sha256(manifest_path),
            "shard_manifest_json": sha256(shard_manifest_path),
        },
        "label_counts": {k: counts.get(k, 0) for k in ("matched", "unmatched", "review")},
        "pairs": pair_stats,
        "human_review_status": "not_reviewed",
        "synthetic_data_mixed": False,
        "overwrite_history": [],
        "evidence_policy": "Direct exact quotes from the assigned AU/Rakuten dossier, SKU rows and selected Rakuten option; no price, availability, inventory, image inference, matcher output or prior labels used.",
        "labels_path": str(OUT.relative_to(ROOT)),
        "labels_sha256": sha256(OUT),
    }
    with REVIEW.open("x", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"labels": str(OUT), "review": str(REVIEW), "label_counts": report["label_counts"], "input_sha256": report["input_sha256"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
