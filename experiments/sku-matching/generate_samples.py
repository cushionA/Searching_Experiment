"""Generate clearly synthetic curtain SKU workloads from the observed page schema.

The source pages establish option names and size/color vocabulary, not the
generated SKU inventory, Rakuten option choices, price, or availability.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
OBSERVATION = HERE / "product-observation.json"
COLORS = ("ベージュ", "アイスグレー", "ライトグレー", "スモークグレー",
          "サンドベージュ", "ユーカリグリーン", "ノクターンネイビー",
          "コールブラック", "カフェブラウン")
SIZES = tuple((100, h) for h in (80, 90, 105, 110, 120, 135, 150, 178, 185, 190, 200, 210, 220, 230)) + tuple(
    (150, h) for h in (135, 178, 200))
ALIASES = {"ベージュ": "ベージュ", "アイスグレー": "アイスグレー",
           "ライトグレー": "ライトグレー", "スモークグレー": "スモークグレー",
           "サンドベージュ": "サンドベージュ", "ユーカリグリーン": "ユーカリグリーン",
           "ノクターンネイビー": "ノクターンネイビー", "コールブラック": "コールブラック",
           "カフェブラウン": "カフェブラウン"}
TITLE_PERTURBATIONS = (
    "カーテン 遮光 レースカーテンセット 洗える 無地",
    "遮光カーテン レース付 遮熱 省エネ ウォッシャブル",
    "カーテンセット 遮光 形状記憶 UVカット おしゃれ",
    "遮光カーテン カーテン レースカーテン 断熱 洗濯可",
    "レースセット 遮光カーテン 無地 厚手 省エネ",
    "カーテン 1級遮光 レースカーテンセット タッセル付き",
)


def _read_observation(path: Path = OBSERVATION) -> tuple[str, str, str, str]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    by_url = {row["url"]: row for row in obj["observations"]}
    rakuten = by_url["https://item.rakuten.co.jp/weimall/ct0/"]
    au = by_url["https://wowma.jp/item/704502086"]
    return (obj["retrieved_date_utc"], au["exact_product_title"], rakuten["exact_product_title"],
            json.dumps({"au": au["url"], "rakuten": rakuten["url"]}, ensure_ascii=False))


def _sku(site: str, family: str, color: str, width: int, height: int, lace: bool,
         title: str, url: str, idx: int, rng: random.Random, *, generated_label: str | None = None) -> dict:
    pieces = (4 if width == 100 else 2) if lace else (2 if width == 100 else 1)
    label = generated_label or f"幅{width}×丈{height}cm({pieces}枚組) / {color} / {'あり' if lace else 'なし'}"
    # Prices and stock are sampled control data; these values are never live quotes.
    price = rng.randrange(198, 1211) * 10 + (width == 150) * 700
    available = rng.random() >= 0.035
    product_id = f"synthetic-{site}-{family}"
    return {
        "sku_id": f"{product_id}-sku-{idx:04d}", "product_id": product_id,
        "product_title": title, "sku_label": label, "url": url,
        "price": int(price), "available": available,
        "attributes": {"width_cm": width, "height_cm": height, "color": color,
                       "lace": lace, "pieces": pieces},
        "provenance": {"record": "SYNTHETIC_FAMILY_INSTANCE",
                       "sku_label": ("OBSERVED_OPTION_FORMAT_RECONSTRUCTED_COMBINATIONS"
                                     if site == "au" else "SYNTHETIC_RECONSTRUCTION"),
                       "price": "SYNTHETIC", "availability": "SYNTHETIC",
                       "product_title": "OBSERVED_PAGE_TITLE" if title in (AU_TITLE, RAKUTEN_TITLE) else "SYNTHETIC_PERTURBATION"},
    }


# Initialized from the checked-in read-only observation; no network access.
OBSERVED_DATE, AU_TITLE, RAKUTEN_TITLE, _URL_JSON = _read_observation()
OBSERVED_URLS = json.loads(_URL_JSON)


def make_family(family_number: int, rng: random.Random) -> dict:
    family = f"{family_number:06d}"
    au_url, rakuten_url = OBSERVED_URLS["au"], OBSERVED_URLS["rakuten"]
    au: list[dict] = []
    rakuten: list[dict] = []
    idx = 0
    for color in COLORS:
        for width, height in SIZES:
            idx += 1
            au.append(_sku("au", family, color, width, height, True, AU_TITLE, au_url,
                           idx, rng, generated_label=f"幅{width}×丈{height}cm({4 if width == 100 else 2}枚組) / {color}"))
            for lace in (True, False):
                rakuten.append(_sku("rakuten", family, color, width, height, lace, RAKUTEN_TITLE,
                                    rakuten_url, idx * 2 - int(not lace), rng))
    # One controlled decision-boundary defect in selected families. This keeps
    # the inventory cardinality fixed while exercising the final exclusion gate.
    anomaly = (family_number - 1) % 6
    control = rakuten[0]
    if anomaly == 0:
        control["available"] = False
        control["scenario"] = "unavailable"
    elif anomaly == 1:
        control["available"] = True
        control["price"] = "3,980円〜"
        control["scenario"] = "range_price"
    elif anomaly == 2:
        control["available"] = True
        control["sku_label"] += " / 遮光ライナー別売"
        control["scenario"] = "unknown_paid_option"
    elif anomaly == 3:
        control["available"] = True
        control["attributes"]["lace"] = None
        control["sku_label"] = control["sku_label"].rsplit(" / ", 1)[0]
        control["scenario"] = "missing_lace_attribute"
    elif anomaly == 4:
        control["available"] = True
        duplicate = dict(rakuten[1])
        duplicate["attributes"] = dict(rakuten[0]["attributes"])
        duplicate["available"] = True
        duplicate["sku_label"] = rakuten[0]["sku_label"]
        duplicate["scenario"] = "duplicate_semantic_attributes"
        rakuten[1] = duplicate
        control["scenario"] = "duplicate_semantic_attributes"
    else:
        control["scenario"] = "ordinary_control"
    return {
        "pair_id": f"synthetic-product-pair-{family}", "au": au, "rakuten": rakuten,
        "color_aliases": ALIASES,
        "dataset_provenance": "SYNTHETIC_WORKLOAD_FROM_OBSERVED_OPTION_SCHEMA",
        "observed_source_date_utc": OBSERVED_DATE,
        "synthetic_control_scenario": ("unavailable", "range_price", "unknown_paid_option",
                                        "missing_lace_attribute", "duplicate_semantic_attributes",
                                        "ordinary_control")[anomaly],
        "source_notes": "AU labels follow observed color/size options; reconstructed Rakuten lace options, SKU rows, prices, and availability are synthetic. The captured Rakuten SKU selector was empty.",
    }


def _case_row(site: str, case_id: str, color: str, width: int, height: int, lace: bool,
              title: str, url: str, label_style: int) -> dict:
    pieces = (4 if width == 100 else 2) if lace else (2 if width == 100 else 1)
    if label_style == 0:
        label = f"幅{width}×丈{height}cm({pieces}枚組) / {color} / {'あり' if lace else 'なし'}"
    elif label_style == 1:
        label = f"{width}×{height}cm / {color} / レース{'あり' if lace else 'なし'}"
    else:
        label = f"幅{width}×丈{height}cm({pieces}枚組) / {color} / {'あり' if lace else 'なし'}"
    ptitle = title
    return {"sku_id": f"{case_id}-{site}", "product_id": f"{case_id}-{site}-product",
            "product_title": ptitle, "sku_label": label, "url": url,
            "price": 5000, "available": True,
            "attributes": {"width_cm": width, "height_cm": height, "color": color,
                           "lace": lace, "pieces": pieces},
            "provenance": {"record": "SYNTHETIC_EVALUATION_CASE",
                           "product_title": "OBSERVED_OR_CONTROLLED_SYNTHETIC_VARIANT",
                           "sku_label": "SYNTHETIC_CONTROLLED_FORMAT"}}


def make_evaluation_case(index: int, rng: random.Random) -> dict:
    """Independent single-candidate semantic cases, balanced across hard negatives."""
    case_id = f"eval-{index:06d}"
    color = rng.choice(COLORS)
    width, height = rng.choice(SIZES)
    lace = bool(rng.getrandbits(1))
    same = index % 2 == 0
    scenario = "positive_format_variation"
    other_color, other_width, other_height, other_lace = color, width, height, lace
    if not same:
        family = (index // 2) % 6
        if family == 0:
            other_height = 180 if height != 180 else 80
            scenario = "negative_height_near_miss"
        elif family == 1:
            other_color = "サンドベージュ" if color == "ベージュ" else "ベージュ"
            scenario = "negative_near_color"
        elif family == 2:
            other_lace = not lace
            scenario = "negative_lace_presence"
        elif family == 3:
            other_width, other_height = height, width
            scenario = "negative_width_height_swap"
        elif family == 4:
            other_width = 150 if width == 100 else 100
            scenario = "negative_width_piece_count"
        else:
            scenario = "negative_missing_lace_evidence"
    # Rotate controlled title rewrites so evaluation measures both contextual and SKU-only signals.
    title_a = AU_TITLE if index % 3 else TITLE_PERTURBATIONS[(index // 3) % len(TITLE_PERTURBATIONS)]
    title_r = RAKUTEN_TITLE if index % 4 else TITLE_PERTURBATIONS[(index // 4 + 2) % len(TITLE_PERTURBATIONS)]
    a = _case_row("au", case_id, color, width, height, lace, title_a,
                  OBSERVED_URLS["au"], (index // 7) % 3)
    r = _case_row("rakuten", case_id, other_color, other_width, other_height, other_lace, title_r,
                  OBSERVED_URLS["rakuten"], (index // 5 + 1) % 3)
    if scenario == "negative_missing_lace_evidence":
        # A deliberately incomplete SKU record remains review-worthy; it is not labeled equivalent.
        r["attributes"]["lace"] = None
        r["sku_label"] = f"{other_color} / 幅{other_width}×丈{other_height}cm({(4 if other_width == 100 else 2)}枚組)"
    expected_decision = ("matched" if same else
                        "review" if scenario == "negative_missing_lace_evidence" else "unmatched")
    return {"case_id": case_id, "scenario": scenario, "expected_match": same,
            "expected_decision": expected_decision,
            "au": a, "rakuten": r,
            "label_basis": "CONTROLLED_SYNTHETIC_CANONICAL_ATTRIBUTES",
            "input_modes": ["title-sku", "sku"]}


def generate(output: Path, pairs: int = 1000, seed: int = 20261009) -> None:
    if pairs < 1:
        raise ValueError("pairs must be positive")
    output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(seed)
    dataset_path = output / "dataset.jsonl"
    with dataset_path.open("x", encoding="utf-8") as stream:
        for i in range(1, pairs + 1):
            stream.write(json.dumps(make_family(i, rng), ensure_ascii=False, separators=(",", ":")) + "\n")
    # Always emit at least 1,000 independent examples, even for small smoke runs.
    evaluation_count = max(1000, pairs)
    evaluation = {"schema_version": 1, "provenance": "SYNTHETIC_EVALUATION_ONLY",
                  "observed_source_date_utc": OBSERVED_DATE,
                  "count": evaluation_count,
                  "input_modes": ["title-sku", "sku"],
                  "note": "Controlled synthetic semantic pairs; not live Rakuten SKU truth or a production accuracy estimate.",
                  "cases": [make_evaluation_case(i, rng) for i in range(evaluation_count)]}
    with (output / "evaluation.json").open("x", encoding="utf-8") as stream:
        json.dump(evaluation, stream, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")
    manifest = {"dataset_file": "dataset.jsonl", "evaluation_file": "evaluation.json",
                "pair_count": pairs, "au_skus_per_pair": 153, "rakuten_skus_per_pair": 306,
                "evaluation_cases": evaluation_count, "seed": seed,
                "data_status": "SYNTHETIC_WORKLOAD; generated Rakuten labels, prices, availability, and SKU matrix are not observed live data.",
                "observed_titles_and_option_vocabulary_from": "experiments/sku-matching/product-observation.json"}
    with (output / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new output directory; existing paths are refused")
    parser.add_argument("--pairs", type=int, default=1000, help="synthetic product families (default: 1000)")
    parser.add_argument("--seed", type=int, default=20261009)
    args = parser.parse_args(argv)
    try:
        generate(args.output, args.pairs, args.seed)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "pairs": args.pairs,
                      "evaluation_cases": max(1000, args.pairs), "status": "synthetic"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
