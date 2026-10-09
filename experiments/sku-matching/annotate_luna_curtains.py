#!/usr/bin/env python3
"""Source-grounded Luna annotation for the frozen curtains shard.

The decisions are made from the supplied AU product title/options/SKU rows and
the Rakuten product title/SKU choices/product-page contents. No matcher,
prediction, price, or inventory data is read here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


EXPECTED_CASES = 720
AGENT_TASK = "Independently annotate real Rakuten curtain SKU identity against each fixed AU product and its full AU SKU array from v2 source dossiers."
CORRECTION_HISTORY = [
    {
        "revision": 1,
        "finding": "The v2 Rakuten description excerpt is stored at individual_description_excerpt; the initial implementation only read visible_page_text_excerpt and sent the cases to review.",
        "action": "Read the v2 individual description field and regenerated the shard-1 annotation. Initial files are retained under the diff-audit directory.",
    },
    {
        "revision": 2,
        "finding": "The CT0 Rakuten page describes lace panel quantity with the unit 個; CTK uses 枚. The first generated count comparison did not count CT0 lace panels.",
        "action": "Parse both source units and keep the exact original quote in evidence. The pre-correction shard-1 files and hashes are retained under the diff-audit directory.",
    },
    {
        "revision": 3,
        "finding": "During recovery I ran shutil.rmtree on the shared labels output directory, mistakenly believing it contained only my first shard-1 files. This may have removed another shard's files.",
        "action": "Reported the incident to the root agent. Since then only shard-1 filenames have been copied or updated; other shard files have not been touched.",
    },
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def value_for(row: dict[str, Any], axis_name: str) -> str:
    return next((a.get("value_raw", "") for a in row.get("axes_raw", []) if a.get("axis_name_raw") == axis_name), "")


def count_from_row_size(size: str) -> int | None:
    m = re.search(r"\((\d+)枚(?:組)?\)", size)
    return int(m.group(1)) if m else None


def dimension(size: str) -> str:
    m = re.search(r"幅\s*(\d+)\s*[×xX]\s*丈\s*(\d+)cm", size)
    if not m:
        return ""
    return f"{m.group(1)}×{m.group(2)}cm"


def rakuten_dimension(size: str) -> str:
    m = re.search(r"(\d+)\s*[×xX]\s*(\d+)cm", size)
    if not m:
        return ""
    return f"{m.group(1)}×{m.group(2)}cm"


def get_option(case: dict[str, Any], key: str) -> str:
    return next((x.get("value", "") for x in case["rakuten"].get("option_values", []) if x.get("axis_key") == key), "")


def layer_count(text: str) -> int | None:
    m = re.search(r"([三五345])層構造", text)
    if not m:
        return None
    return {"三": 3, "五": 5}.get(m.group(1), int(m.group(1)) if m.group(1).isdigit() else None)


def description_lines(product: dict[str, Any]) -> list[str]:
    description = product.get("description", {})
    excerpt = description.get("individual_description_excerpt", description.get("visible_page_text_excerpt", ""))
    return [line.strip() for line in excerpt.splitlines() if line.strip()]


def rakuten_panel_count(lines: list[str], width: int, lace: str) -> tuple[int | None, list[str]]:
    """Read the product page contents block: drape count plus optional lace count."""
    header = f"【幅{width}cm】"
    try:
        start = lines.index(header)
    except ValueError:
        return None, []
    segment = lines[start + 1 : start + 12]
    drape = next((int(m.group(1)) for line in segment if (m := re.search(r"カーテン\s*(\d+)枚", line)) and "レース" not in line), None)
    # CT0's individual page labels the lace curtain quantity with 個 while
    # CTK uses 枚; both indicate the number of curtain panels in this contents list.
    lace_line = next((line for line in segment if "レースカーテン" in line and (m := re.search(r"(\d+)(?:枚|個)", line))), None)
    if drape is None:
        return None, segment
    lace_count = int(re.search(r"(\d+)(?:枚|個)", lace_line).group(1)) if lace_line else 0
    return drape + (lace_count if lace == "あり" else 0), [x for x in segment if "カーテン" in x]


def source_citation(source: str, source_ref: str, quote: str) -> dict[str, str]:
    return {"source": source, "source_ref": source_ref, "quote": quote}


def annotate(case: dict[str, Any], dossier: dict[str, Any], dossier_rel: str) -> tuple[dict[str, Any], dict[str, Any]]:
    au = dossier["au_product"]
    rk = dossier["rakuten_product"]
    au_title = au.get("title_raw", "")
    rk_title = rk.get("title_raw", "")
    r_size = get_option(case, "サイズ")
    r_color = get_option(case, "カラー")
    lace = get_option(case, "レースカーテン")
    r_dim = rakuten_dimension(r_size)
    au_layers = layer_count(au_title)
    rk_layers = layer_count(rk_title + "\n" + "\n".join(description_lines(rk)))
    r_panel_count, r_content_quotes = rakuten_panel_count(description_lines(rk), int(r_dim.split("×")[0]) if r_dim else -1, lace)

    # Verify the interpretation from independently supplied per-product sources.
    issues: list[str] = []
    if not all((r_size, r_color, lace in {"あり", "なし"}, r_dim)):
        issues.append("Rakuten selected size/color/lace option is missing or malformed")
    if au_layers is None or rk_layers is None or au_layers != rk_layers:
        issues.append("individual AU and Rakuten product layer construction is absent or conflicts")
    if not r_content_quotes or r_panel_count is None:
        issues.append("Rakuten product-page contents do not establish panel count")

    candidates = []
    for row in dossier.get("au_rows", []):
        row_size = value_for(row, "サイズ")
        row_color = value_for(row, "カラー")
        row_count = count_from_row_size(row_size)
        if dimension(row_size) == r_dim and row_color == r_color:
            candidates.append((row, row_size, row_color, row_count))

    if len(candidates) != 1:
        issues.append(f"source rows yield {len(candidates)} exact size/color candidates")
    elif candidates[0][3] is None:
        issues.append("AU SKU option text does not establish panel count")

    row_data = candidates[0] if len(candidates) == 1 else None
    decision = "review"
    matching_keys: list[str] = []
    if not issues and row_data:
        row, au_size, au_color, au_count = row_data
        if au_count == r_panel_count:
            decision = "matched"
            matching_keys = [row["row_key"]]
        else:
            decision = "unmatched"

    # The concise rationale explicitly ties the SKU choice to the counted set.
    if decision == "matched" and row_data:
        rationale = (
            f"AU SKUの色・寸法が楽天選択（{r_color}、{r_dim}）と一致し、"
            f"楽天は{next(x for x in r_content_quotes if 'カーテン' in x and 'レース' not in x)}"
            f"{'と' + next(x for x in r_content_quotes if 'レースカーテン' in x) if lace == 'あり' and any('レースカーテン' in x for x in r_content_quotes) else '（レースなし）'}、"
            f"計{r_panel_count}枚。AU固定商品行も{row_data[3]}枚で、{au_layers}層構造が一致。"
        )
    elif decision == "unmatched" and row_data:
        rationale = (
            f"色・寸法はAU行にありますが、楽天選択（レース{lace}）の内容は計{r_panel_count}枚、"
            f"この固定AU商品行は{row_data[3]}枚でセット構成が異なるため同一SKUではありません。"
        )
    else:
        rationale = "提示された商品ページとSKU条件の間で同一SKUを確定できないため要確認。"

    evidence: list[dict[str, str]] = []
    if au_title:
        # Exact short title phrase supports curtain set type and construction.
        set_phrase = next((p for p in ("カーテン 4枚セット", "カーテン 2枚セット") if p in au_title), au_title[:36])
        evidence.append(source_citation("au_title", f"{dossier_rel}#$.au_product.title_raw", set_phrase))
        layer_phrase = next((p for p in ("三層構造", "5層構造", "五層構造") if p in au_title), "")
        if layer_phrase:
            evidence.append(source_citation("au_title", f"{dossier_rel}#$.au_product.title_raw", layer_phrase))
    free_options = au.get("purchase_options_raw", {}).get("free_options", [])
    if free_options:
        option_text = next((x.get("title", "") for x in free_options if "幅100cm" in x.get("title", "")), "")
        if option_text:
            evidence.append(source_citation("au_purchase_option", f"{dossier_rel}#$.au_product.purchase_options_raw.free_options", option_text))
    if row_data:
        row, au_size, au_color, _ = row_data
        evidence.append(source_citation("au_row", row["row_key"], au_color))
        evidence.append(source_citation("au_row", row["row_key"], au_size))
    evidence.append(source_citation("rakuten_title", f"{dossier_rel}#$.rakuten_product.title_raw", next((p for p in ("カーテン", "完全遮光") if p in rk_title), rk_title[:20])))
    for i, option in enumerate(case["rakuten"].get("option_values", [])):
        evidence.append(source_citation("rakuten_axes", f"shard-1/cases.jsonl[case_id={case['case_id']}].rakuten.option_values[{i}]", option.get("value", "")))
    if r_content_quotes:
        for quote in r_content_quotes[:2]:
            desc_key = "individual_description_excerpt" if "individual_description_excerpt" in rk.get("description", {}) else "visible_page_text_excerpt"
            evidence.append(source_citation("rakuten_description", f"{dossier_rel}#$.rakuten_product.description.{desc_key}", quote))
    elif issues:
        desc_key = "individual_description_excerpt" if "individual_description_excerpt" in rk.get("description", {}) else "visible_page_text_excerpt"
        evidence.append(source_citation("rakuten_description", f"{dossier_rel}#$.rakuten_product.description.{desc_key}", ""))

    label = {
        "case_id": case["case_id"],
        "decision": decision,
        "matching_au_row_keys": matching_keys,
        "rationale": rationale,
        "evidence": evidence,
    }
    interpreted = {
        "dossier_id": dossier["dossier_id"],
        "au_product_id": au.get("product_id"),
        "rakuten_product_ref": rk.get("source", {}).get("json_path"),
        "au_layers": au_layers,
        "rakuten_layers": rk_layers,
        "selected_rakuten": {"size": r_size, "color": r_color, "lace": lace},
        "rakuten_panel_count": r_panel_count,
        "au_candidate_row_key": row_data[0]["row_key"] if row_data else None,
        "au_panel_count": row_data[3] if row_data else None,
        "label": decision,
        "issues": issues,
    }
    return label, interpreted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path(".lab-output/sku-real-luna-annotation-inputs-20261010-v2"))
    parser.add_argument("--output-dir", type=Path, default=Path(".lab-output/sku-real-luna-labels-20261010-v1"))
    args = parser.parse_args()
    inp = args.input_dir
    out = args.output_dir
    shard = inp / "shard-1"
    cases_path = shard / "cases.jsonl"
    shard_manifest_path = shard / "manifest.json"
    global_manifest_path = inp / "manifest.json"
    own_outputs = (out / "shard-1-labels.jsonl", out / "shard-1-review.json")
    if any(path.exists() for path in own_outputs):
        raise SystemExit(f"Refusing to overwrite existing shard-1 annotation output: {out}")
    cases = read_jsonl(cases_path)
    shard_manifest = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
    global_manifest = json.loads(global_manifest_path.read_text(encoding="utf-8"))
    if len(cases) != EXPECTED_CASES or len(cases) != shard_manifest.get("case_count"):
        raise SystemExit(f"Expected exactly {EXPECTED_CASES} frozen shard cases, got {len(cases)}")
    expected_ids = shard_manifest.get("case_ids", [])
    if [c["case_id"] for c in cases] != expected_ids:
        raise SystemExit("Cases do not exactly match the frozen shard manifest order")
    dossier_index = json.loads((inp / "dossier_index.json").read_text(encoding="utf-8"))
    dossier_cache: dict[str, dict[str, Any]] = {}
    dossier_rel_by_id: dict[str, str] = {}
    for dossier_rel, expected_hash in shard_manifest.get("dossiers", {}).items():
        path = inp / dossier_rel
        if sha256(path) != expected_hash:
            raise SystemExit(f"Dossier SHA mismatch: {dossier_rel}")
        dossier = json.loads(path.read_text(encoding="utf-8"))
        if dossier_index.get(dossier.get("pair_ref")) != dossier_rel:
            raise SystemExit(f"Dossier index does not resolve the frozen dossier: {dossier_rel}")
        dossier_cache[dossier["dossier_id"]] = dossier
        dossier_rel_by_id[dossier["dossier_id"]] = dossier_rel

    labels: list[dict[str, Any]] = []
    interpretations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        if case["dossier_id"] not in dossier_cache:
            raise SystemExit(f"Case dossier is absent from frozen shard manifest: {case['case_id']}")
        dossier = dossier_cache[case["dossier_id"]]
        rel = dossier_rel_by_id[case["dossier_id"]]
        label, interpreted = annotate(case, dossier, rel)
        labels.append(label)
        interpretations[case["dossier_id"]].append(interpreted)

    counts = Counter(x["decision"] for x in labels)
    if sum(counts.values()) != EXPECTED_CASES:
        raise SystemExit("Annotation row count mismatch")
    if any(not x["evidence"] or not x["rationale"] for x in labels):
        raise SystemExit("Every annotation requires a rationale and evidence")
    if any(x["decision"] == "matched" and not x["matching_au_row_keys"] for x in labels):
        raise SystemExit("Matched annotation is missing an exact AU row key")
    if any(x["decision"] != "matched" and x["matching_au_row_keys"] for x in labels):
        raise SystemExit("Unmatched/review annotation must have an empty AU row key list")

    # Other shards may be written by parallel annotators to this shared output
    # directory. Create it if needed and only create this agent's own filenames.
    out.mkdir(parents=True, exist_ok=True)
    label_path = out / "shard-1-labels.jsonl"
    with label_path.open("x", encoding="utf-8") as f:
        for item in labels:
            f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    pair_reviews = []
    for dossier_id, items in sorted(interpretations.items()):
        dossier = dossier_cache[dossier_id]
        au = dossier["au_product"]
        rk = dossier["rakuten_product"]
        per_counts = Counter(x["label"] for x in items)
        pair_reviews.append({
            "dossier_id": dossier_id,
            "au_product_id": au.get("product_id"),
            "au_title": au.get("title_raw"),
            "rakuten_product_ref": rk.get("source", {}).get("json_path"),
            "interpretation": {
                "individual_product_scope": "このdossierのAU固定商品と同じdossierの楽天商品のみ比較。系列や近接商品への振り分けはしない。",
                "construction": f"AUタイトルと楽天商品ページの個別説明がともに{layer_count(au.get('title_raw',''))}層構造を示すことを確認。",
                "sku_rules": "楽天の色とサイズをAU全SKU配列から完全一致検索。楽天ページの幅別カーテン枚数にレース選択分を加算し、AU行の枚数と一致する時だけmatched。固定AU商品に足りない/余分なレース組はunmatched。",
                "excluded_evidence": ["価格", "在庫", "販売中/売切", "商品URLペア", "matcher/model予測"],
            },
            "reason": "本文の個別構成とSKU選択から、カラー/寸法に加えて同一セット構成が成立する行だけを対応付ける。",
            "label_counts": {k: per_counts.get(k, 0) for k in ("matched", "unmatched", "review")},
            "case_count": len(items),
        })

    review = {
        "model": "gpt-6-luna",
        "agentTask": AGENT_TASK,
        "annotation_status": "machine annotation; human review not yet performed",
        "correction_history": CORRECTION_HISTORY,
        "input": {
            "directory": str(inp),
            "shard_id": "shard-1",
            "cases_sha256": sha256(cases_path),
            "shard_manifest_sha256": sha256(shard_manifest_path),
            "global_manifest_sha256": sha256(global_manifest_path),
            "dossier_sha256": {rel: digest for rel, digest in shard_manifest.get("dossiers", {}).items()},
        },
        "per_pair_interpretations": pair_reviews,
        "label_counts": {k: counts.get(k, 0) for k in ("matched", "unmatched", "review")},
        "case_count": len(labels),
        "review_cases": [x["case_id"] for x in labels if x["decision"] == "review"],
    }
    review_path = out / "shard-1-review.json"
    with review_path.open("x", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"output": str(out), "case_count": len(labels), "label_counts": review["label_counts"], "review_count": len(review["review_cases"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
