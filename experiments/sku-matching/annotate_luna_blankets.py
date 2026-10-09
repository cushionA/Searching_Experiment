#!/usr/bin/env python3
"""Create the independent Luna labels for the frozen blanket SKU shard.

This script reads only the blind annotation input bundle (cases, dossiers, and
manifest).  It deliberately does not import or call any matcher, model, or
curated-pair builder.  The SKU identity rules below encode the source-page
interpretation documented in shard-2-review.json: the fixed AU blanket type,
its page-specific size (using the stated dimensions where labels differ), and
an exact color present in the AU SKU array must all agree with the selected
Rakuten variant.  Duvet covers are treated as a separate product type.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v2"
DEFAULT_OUTPUT = REPO_ROOT / ".lab-output/sku-real-luna-labels-20261010-v1"
SHARD_ID = "shard-2"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def matching_block(blocks: list[dict[str, Any]], pattern: str) -> dict[str, Any] | None:
    rx = re.compile(pattern)
    return next((b for b in blocks if rx.search(str(b.get("text", "")))), None)


def dimensions(text: str) -> tuple[int, int] | None:
    """Read width x length in cm from source text, allowing Japanese x marks."""
    s = text.replace("×", "x").replace("＊", "x").replace("*", "x")
    # Source prose can write either ``幅70cm×長さ100cm`` or
    # ``幅100×長さ160cm``; allow the labels and the first cm marker.
    m = re.search(
        r"(\d+)\s*(?:cm|ｃｍ)?\s*x\s*(?:(?:長さ|横幅|幅)\s*)?(\d+)\s*(?:cm|ｃｍ)",
        s,
        flags=re.I,
    )
    return (int(m.group(1)), int(m.group(2))) if m else None


def axes(case: dict[str, Any]) -> dict[str, str]:
    return {
        str(o.get("axis_key") or o.get("axis_name") or ""): str(o.get("value", ""))
        for o in case.get("rakuten", {}).get("option_values", [])
    }


def cite(source: str, source_ref: str, quote: str) -> dict[str, str]:
    return {"source": source, "source_ref": source_ref, "quote": quote}


def au_row_color(row: dict[str, Any]) -> str | None:
    for axis in row.get("axes_raw", []):
        if axis.get("value_raw"):
            return str(axis["value_raw"])
    return None


def blanket_target_size(au: dict[str, Any]) -> tuple[str, tuple[int, int], dict[str, Any]]:
    """Resolve the selected AU blanket size from its own detail text."""
    blocks = au.get("description", {}).get("blocks", [])
    selector = matching_block(blocks, r"こちらのページは【?(XS|QT|S|SD|D)サイズ】?です")
    if not selector:
        raise ValueError(f"AU blanket page size selector missing for {au.get('product_id')}")
    selected = re.search(r"(XS|QT|S|SD|D)サイズ", selector["text"])
    assert selected
    token = selected.group(1)
    # The detail page supplies the chosen size dimensions; these also resolve
    # XS on AU to Rakuten's 100x160 cm half blanket.
    dim_block = matching_block(
        blocks,
        rf"(?:^|\b){re.escape(token)}：.*?(?:\d+\s*[×x]\s*\d+\s*(?:cm|ｃｍ))",
    )
    if not dim_block:
        dim_block = matching_block(blocks, rf"{re.escape(token)}：（約）")
    if not dim_block:
        raise ValueError(f"AU dimension line missing for {au.get('product_id')} / {token}")
    dim = dimensions(dim_block["text"])
    if not dim:
        raise ValueError(f"Cannot parse AU dimensions for {au.get('product_id')}: {dim_block['text']}")
    return token, dim, dim_block


def cover_target_size(au: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    blocks = au.get("description", {}).get("blocks", [])
    selector = matching_block(blocks, r"こちらのページは(シングル|セミダブル|ダブル)サイズです")
    if not selector:
        raise ValueError(f"AU cover page size selector missing for {au.get('product_id')}")
    m = re.search(r"(シングル|セミダブル|ダブル)サイズ", selector["text"])
    assert m
    return m.group(1), selector


def product_family(au: dict[str, Any]) -> str:
    text = au["title_raw"]
    # The cover's detail page explicitly calls this 掛け布団カバー; the other
    # assigned products are the two-layer もことろん blanket SKU family.
    if "掛け布団カバー" in text or "布団カバー 2WAY" in text:
        return "duvet_cover"
    if "毛布" in text and "もことろん" in text:
        return "blanket"
    raise ValueError(f"Unknown AU product type in title: {text}")


def target_au_colors(au_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    colors: dict[str, dict[str, Any]] = {}
    for row in au_rows:
        color = au_row_color(row)
        if color:
            colors[color] = row
    return colors


def title_quote(title: str, family: str) -> str:
    needles = ("掛け布団カバー", "布団カバー") if family == "duvet_cover" else ("もことろん", "毛布")
    for needle in needles:
        pos = title.find(needle)
        if pos >= 0:
            return title[max(0, pos - 8):min(len(title), pos + len(needle) + 14)]
    return title[:80]


def annotate_case(case: dict[str, Any], dossier: dict[str, Any]) -> dict[str, Any]:
    au = dossier["au_product"]
    rak = dossier["rakuten_product"]
    au_rows = dossier["au_rows"]
    family = product_family(au)
    selected = axes(case)
    rsku_ref = case["rakuten"]["source"]["source_sku_key"]
    au_title_ref = "$.itemInfo.itemName"
    au_desc_path = au["description"]["source"]["json_paths"]
    r_title_ref = rak["source"]["json_path"]
    r_desc_ref = rak["description"]["source"]["json_path"]
    au_colors = target_au_colors(au_rows)
    r_color = selected.get("カラー", "")
    color_row = au_colors.get(r_color)
    r_size = selected.get("サイズ", "")
    r_type = selected.get("タイプ", "")
    evidence: list[dict[str, str]] = [
        cite("au_title", au_title_ref, title_quote(au["title_raw"], family)),
        cite("rakuten_title", r_title_ref,
             "掛け布団カバー" if family == "duvet_cover" else "ふわもこ毛布"),
    ]

    if family == "duvet_cover":
        au_size, au_selector = cover_target_size(au)
        au_size_match = r_size == au_size
        # Product-page text confirms that size names carry the dimensions
        # listed in the Rakuten detail; cite the exact source lines for either
        # the matching or the competing selected size.
        evidence.append(cite("au_description", str(au_selector["source_field"]), au_selector["text"]))
        r_detail_lines = rak["description"]["individual_description_excerpt"].splitlines()
        r_size_line = next((line for line in r_detail_lines if line.startswith(r_size + "：")), None)
        if r_size_line:
            evidence.append(cite("rakuten_description", r_desc_ref, r_size_line))
        evidence.append(cite("rakuten_sku", rsku_ref, r_size))
        if r_color:
            evidence.append(cite("rakuten_sku", rsku_ref, r_color))
    else:
        au_size_token, au_dim, au_size_block = blanket_target_size(au)
        r_type_matches = r_type == "もことろん毛布"
        r_dim = dimensions(r_size)
        # For blanket SKU axes, the real dimensions determine the same size
        # even where Rakuten calls the AU XS option ハーフ.
        au_size_matches = r_type_matches and r_dim == au_dim
        evidence.append(cite("au_description", str(au_selector_field(au)),
                             selected_au_size_quote(au, au_size_token)))
        evidence.append(cite("au_description", str(au_size_block["source_field"]), au_size_block["text"]))
        type_labels = {str(a.get("key", "")) for a in case["rakuten"].get("axes_labels", [])}
        if "タイプ" in selected:
            evidence.append(cite("rakuten_sku", rsku_ref, r_type))
        r_detail_lines = rak["description"]["individual_description_excerpt"].splitlines()
        r_size_line = next((line for line in r_detail_lines
                            if dimensions(line) == r_dim and any(k in line for k in ["QT", "H(", "S(", "SD(", "D("])) , None)
        if r_size_line:
            evidence.append(cite("rakuten_description", r_desc_ref, r_size_line))
        evidence.append(cite("rakuten_sku", rsku_ref, r_size))
        if r_color:
            evidence.append(cite("rakuten_sku", rsku_ref, r_color))

    if color_row:
        evidence.append(cite("au_row", color_row["row_key"], r_color))
    else:
        # Show the complete captured AU color array when the Rakuten color is
        # absent. Similar shades such as ブラウン and カフェオレブラウン
        # remain distinct labels.
        for row in au_rows:
            color = au_row_color(row)
            if color:
                evidence.append(cite("au_row", row["row_key"], color))

    if family == "duvet_cover":
        matched = au_size_match and color_row is not None
        if matched:
            decision = "matched"
            matching = [color_row["row_key"]]
            rationale = f"掛け布団カバーの選択サイズ「{r_size}」がAU個別ページのサイズと一致し、色「{r_color}」もAU SKU行に完全一致します。"
        else:
            decision = "unmatched"
            matching = []
            if not au_size_match:
                rationale = f"楽天SKUのサイズ「{r_size}」はAU固定商品の「{au_size}」と異なります。色が一致しても別サイズです。"
            else:
                rationale = f"楽天の色「{r_color}」に完全一致するAU SKU行がありません。近い色名は同一視していません。"
    else:
        au_size_token, au_dim, _ = blanket_target_size(au)
        correct_type = r_type == "もことろん毛布"
        correct_size = dimensions(r_size) == au_dim
        matched = correct_type and correct_size and color_row is not None
        if matched:
            decision = "matched"
            matching = [color_row["row_key"]]
            rationale = f"選択肢は通常のもことろん毛布で、寸法{au_dim[0]}×{au_dim[1]}cmと色「{r_color}」がAU固定ページのSKUに一致します。"
        elif correct_type and correct_size and r_color == "ブラウン":
            # The Rakuten product page says ブラウン while the fixed AU page
            # offers カフェオレブラウン among its browns. Without image
            # evidence or an explicit textual alias, the identity cannot be
            # settled from text alone, so reserve this exact-size case for review.
            decision = "review"
            matching = []
            rationale = "同じタイプ・サイズですが、楽天の「ブラウン」がAUの「カフェオレブラウン」と同色の別名かは、テキスト資料だけでは確認できません。"
        else:
            decision = "unmatched"
            matching = []
            if not correct_type:
                rationale = f"楽天SKUは「{r_type}」ですが、AU固定商品は通常のもことろん毛布です。電気毛布・ストライプ毛布・クッションカバーは別商品です。"
            elif not correct_size:
                r_dim_text = f"{dimensions(r_size)[0]}×{dimensions(r_size)[1]}cm" if dimensions(r_size) else r_size
                rationale = f"同じ毛布タイプでも、楽天SKUの寸法「{r_dim_text}」はAU固定ページの{au_dim[0]}×{au_dim[1]}cmと異なります。"
            else:
                rationale = f"楽天の色「{r_color}」に完全一致するAU SKU行がありません。近い色名は同一視していません。"

    # Stable de-duplication preserves the most direct citations first.
    unique_evidence = []
    seen = set()
    for e in evidence:
        key = (e["source"], e["source_ref"], e["quote"])
        if key not in seen:
            unique_evidence.append(e)
            seen.add(key)
    return {
        "case_id": case["case_id"],
        "decision": decision,
        "matching_au_row_keys": matching,
        "rationale": rationale,
        "evidence": unique_evidence,
    }


def au_selector_field(au: dict[str, Any]) -> str:
    blocks = au.get("description", {}).get("blocks", [])
    b = matching_block(blocks, r"こちらのページは【?(XS|QT|S|SD|D)サイズ】?です")
    if not b:
        raise ValueError(f"AU selector missing for {au.get('product_id')}")
    return str(b["source_field"])


def selected_au_size_quote(au: dict[str, Any], token: str) -> str:
    blocks = au.get("description", {}).get("blocks", [])
    b = matching_block(blocks, rf"こちらのページは【?{re.escape(token)}サイズ】?です")
    if not b:
        raise ValueError(f"AU selector missing for {au.get('product_id')} / {token}")
    return str(b["text"])


def run(input_root: Path, output_root: Path) -> None:
    manifest_path = input_root / "manifest.json"
    cases_path = input_root / SHARD_ID / "cases.jsonl"
    manifest = read_json(manifest_path)
    shard_manifest = read_json(input_root / SHARD_ID / "manifest.json")
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(cases) != shard_manifest["case_count"]:
        raise ValueError(f"case count mismatch: {len(cases)} != {shard_manifest['case_count']}")
    actual_cases_sha = sha256_file(cases_path)
    if actual_cases_sha != shard_manifest["cases_sha256"]:
        raise ValueError("shard cases SHA-256 differs from the frozen manifest")
    if output_root.exists():
        raise FileExistsError(f"Refusing to overwrite existing annotation output: {output_root}")

    dossiers: dict[str, dict[str, Any]] = {}
    dossier_dir = input_root / "dossiers"
    for case in cases:
        dossier_id = case["dossier_id"]
        if dossier_id not in dossiers:
            p = dossier_dir / f"{dossier_id}.json"
            expected_sha = shard_manifest["dossiers"][f"dossiers/{dossier_id}.json"]
            if sha256_file(p) != expected_sha:
                raise ValueError(f"dossier SHA-256 differs from frozen manifest: {dossier_id}")
            dossiers[dossier_id] = read_json(p)

    labels = [annotate_case(c, dossiers[c["dossier_id"]]) for c in cases]
    counts = Counter(x["decision"] for x in labels)
    if sum(counts.values()) != len(cases):
        raise AssertionError("not every case received exactly one decision")
    for label in labels:
        if label["decision"] == "matched" and not label["matching_au_row_keys"]:
            raise AssertionError(f"matched label lacks AU row key: {label['case_id']}")
        if label["decision"] != "matched" and label["matching_au_row_keys"]:
            raise AssertionError(f"non-matched label has an AU row key: {label['case_id']}")

    output_root.mkdir(parents=True, exist_ok=False)
    labels_path = output_root / f"{SHARD_ID}-labels.jsonl"
    with labels_path.open("x", encoding="utf-8") as f:
        for label in labels:
            f.write(json.dumps(label, ensure_ascii=False, separators=(",", ":")) + "\n")

    review = {
        "task": "AU Pay Market / Rakuten real SKU identity annotation",
        "shard_id": SHARD_ID,
        "model": "gpt-6-luna",
        "input_directory": str(input_root.relative_to(REPO_ROOT)),
        "input_sha256": {
            "shard_cases_jsonl": actual_cases_sha,
            "manifest_json": sha256_file(manifest_path),
            "shard_manifest_json": sha256_file(input_root / SHARD_ID / "manifest.json"),
            "dossiers": {
                f"dossiers/{dossier_id}.json": shard_manifest["dossiers"][f"dossiers/{dossier_id}.json"]
                for dossier_id in sorted(dossiers)
            },
        },
        "case_count": len(labels),
        "label_counts": {k: counts.get(k, 0) for k in ("matched", "unmatched", "review")},
        "interpretation": [
            "Each case is evaluated against its fixed AU product and every AU SKU row in that dossier.",
            "For blanket SKUs, the AU page-specific type and stated dimensions must agree with the Rakuten selected type/size. AU XS at 100×160 cm is the same size as Rakuten ハーフ(100×160cm).",
            "For 2WAY duvet-cover SKUs, the product type and page-specific Japanese size selector must agree with the selected Rakuten size.",
            "Color matching is exact across the full AU SKU array; near color names such as ブラウン and カフェオレブラウン are kept distinct.",
            "The same-size plain ブラウン Rakuten option remains review because the supplied text does not establish whether it aliases AU カフェオレブラウン.",
            "Availability, inventory, and price were not read or used as semantic evidence. The script reads only this blind input bundle, not matcher/model outputs.",
        ],
        "human_reviewed": False,
        "label_file": labels_path.name,
        "label_file_sha256": sha256_file(labels_path),
    }
    review_path = output_root / f"{SHARD_ID}-review.json"
    with review_path.open("x", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"cases": len(labels), "label_counts": review["label_counts"],
                      "labels": str(labels_path), "review": str(review_path)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run(args.input.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
