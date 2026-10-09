"""Curtain SKU matching experiment. Raw data is never deleted or mutated."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import re
import unicodedata


ATTRIBUTES = ("width_cm", "height_cm", "color", "lace", "pieces")
SIZE = re.compile(r"(?:幅\s*)?(\d+(?:\.\d+)?)\s*[×xX*]\s*(?:丈\s*)?(\d+(?:\.\d+)?)\s*cm", re.I)
PIECES = re.compile(r"(\d+)\s*枚\s*(?:組|セット)?")
LACE = {"あり": True, "なし": False, "レースあり": True, "レースなし": False,
        "レースカーテンあり": True, "レースカーテンなし": False}
CSV_FIELDS = ("pair_id", "rakuten_product_id", "rakuten_sku_id", "rakuten_product_name",
              "rakuten_price", "rakuten_price_basis", "rakuten_url", "au_product_id", "au_sku_id",
              "au_product_name", "au_price", "au_price_basis", "au_url")


def normalize(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def color_key(value, aliases):
    key = normalize(value).casefold()
    return aliases.get(key, key)


def normalize_sku(record, aliases=None):
    """Only known formatting is removed; unknown options require review.

    Product attributes (e.g. lace supplied on an au page) must be copied into
    record.attributes by the collector, with evidence retained at collection.
    Four pieces never implicitly means lace=True.
    """
    aliases = aliases or {}
    for field in ("sku_id", "product_id", "product_title", "sku_label", "url"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"SKU requires nonempty {field}")
    attrs = dict.fromkeys(ATTRIBUTES)
    issues = []

    def put(field, value):
        if field in ("width_cm", "height_cm", "pieces") and value <= 0:
            issues.append(f"invalid_{field}")
            return
        if attrs[field] is not None and attrs[field] != value:
            issues.append(f"conflicting_{field}")
        else:
            attrs[field] = value

    explicit = record.get("attributes", {})
    if not isinstance(explicit, dict) or set(explicit) - set(ATTRIBUTES):
        raise ValueError("Unknown attribute: this experiment supports curtain attributes only")
    for field, value in explicit.items():
        if value is None:
            continue
        if field in ("width_cm", "height_cm"):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid {field}")
            value = float(value)
        elif field == "pieces":
            if type(value) is not int or value <= 0:
                raise ValueError("Invalid pieces")
        elif field == "lace":
            if type(value) is not bool:
                raise ValueError("lace must be a boolean")
        elif field == "color":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Invalid color")
            value = color_key(value, aliases)
        put(field, value)

    label = normalize(record["sku_label"])
    parts = [part.strip() for part in label.split("/")]
    size_found = False
    color_found = False
    for part in parts:
        size = SIZE.search(part)
        if size:
            size_found = True
            put("width_cm", float(size[1]))
            put("height_cm", float(size[2]))
            remainder = SIZE.sub("", part)
            for count in PIECES.findall(remainder):
                put("pieces", int(count))
            remainder = PIECES.sub("", remainder).strip(" ()")
            if remainder:
                issues.append("unknown_size_suffix")
        elif part in LACE:
            put("lace", LACE[part])
        elif PIECES.fullmatch(part.strip("()")):
            put("pieces", int(PIECES.fullmatch(part.strip("()"))[1]))
        elif size_found and not color_found and part:
            put("color", color_key(part, aliases))
            color_found = True
        else:
            issues.append("unknown_option")

    # Titles may describe an entire series ("4枚セット" also covering 150cm
    # 2-piece SKUs). Counts must come from this SKU or verified attributes.
    result = dict(record)
    result["canonical"] = attrs
    result["issues"] = sorted(set(issues))
    result["missing"] = [field for field, value in attrs.items() if value is None]
    result["single_price"] = type(record.get("price")) is int and record["price"] > 0
    return result


def canonical_key(sku):
    return tuple(sku["canonical"][field] for field in ATTRIBUTES)


def model_text(sku, mode="title-sku"):
    return (f"{sku['product_title']} / {sku['sku_label']}" if mode == "title-sku" else sku["sku_label"])


def conflicts(a, b):
    return [field for field in ATTRIBUTES
            if a["canonical"][field] is not None and b["canonical"][field] is not None
            and a["canonical"][field] != b["canonical"][field]]


def match_pair(pair, model=None, input_mode="title-sku", batch_size=32):
    """Match within one already-verified product family, never across families.

    Embeddings are suggestions only; incomplete attributes and ambiguity are
    kept in the audit but excluded from the final comparison CSV.
    """
    if not isinstance(pair.get("pair_id"), str) or not pair["pair_id"]:
        raise ValueError("pair_id is required")
    aliases = {normalize(k).casefold(): normalize(v).casefold()
               for k, v in pair.get("color_aliases", {}).items()}
    au = [normalize_sku(row, aliases) for row in pair["au"]]
    rakuten = [normalize_sku(row, aliases) for row in pair["rakuten"]]
    for rows in (au, rakuten):
        identities = [(r["product_id"], r["sku_id"]) for r in rows]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate SKU ID within a product")

    index = defaultdict(list)
    sizes = defaultdict(list)
    unknown_sizes = []
    for a in au:
        size = (a["canonical"]["width_cm"], a["canonical"]["height_cm"])
        if None in size:
            unknown_sizes.append(a)
        else:
            sizes[size].append(a)
        if not a["issues"] and not a["missing"]:
            index[canonical_key(a)].append(a)

    # One embedding batch per family. encoder caches repeated title+SKU text.
    vectors = None
    if model is not None and au and rakuten:
        texts = [model_text(s, input_mode) for s in au + rakuten]
        vectors = model.encode(texts, batch_size=batch_size)
    au_positions = {id(s): i for i, s in enumerate(au)}
    rakuten_keys = Counter(canonical_key(s) for s in rakuten if not s["issues"] and not s["missing"])
    results = []
    matched_rows = []
    used_au = set()
    for pos, r in enumerate(rakuten):
        decision = {"pair_id": pair["pair_id"], "site": "rakuten",
                    "product_id": r["product_id"], "sku_id": r["sku_id"],
                    "product_title": r["product_title"], "sku_label": r["sku_label"],
                    "canonical": r["canonical"], "status": "review", "reason": ""}
        matches = index.get(canonical_key(r), []) if not r["missing"] else []
        if r.get("available") is False:
            decision.update(status="excluded", reason="unavailable")
        elif r.get("available") is not True:
            decision["reason"] = "availability_unknown"
        elif not r["single_price"]:
            decision["reason"] = "price_not_single_integer"
        elif r["issues"]:
            decision["reason"] = ",".join(r["issues"])
        elif r["missing"]:
            decision["reason"] = "missing_attributes:" + ",".join(r["missing"])
        elif rakuten_keys[canonical_key(r)] > 1:
            decision["reason"] = "ambiguous_rakuten_skus"
        elif len(matches) > 1:
            decision["reason"] = "ambiguous_au_skus"
        elif len(matches) == 1:
            a = matches[0]
            if a.get("available") is False:
                decision.update(status="excluded", reason="au_unavailable")
            elif a.get("available") is not True:
                decision["reason"] = "au_availability_unknown"
            elif not a["single_price"]:
                decision["reason"] = "au_price_not_single_integer"
            else:
                decision.update(status="matched", reason="canonical_attributes_equal",
                                au_product_id=a["product_id"], au_sku_id=a["sku_id"])
                used_au.add((a["product_id"], a["sku_id"]))
                matched_rows.append({"pair_id": pair["pair_id"],
                    "rakuten_product_id": r["product_id"], "rakuten_sku_id": r["sku_id"],
                    "rakuten_product_name": model_text(r), "rakuten_price": r["price"],
                    "rakuten_price_basis": r.get("price_basis", "provided_single_item_price"), "rakuten_url": r["url"],
                    "au_product_id": a["product_id"], "au_sku_id": a["sku_id"],
                    "au_product_name": model_text(a), "au_price": a["price"],
                    "au_price_basis": a.get("price_basis", "provided_single_item_price"), "au_url": a["url"]})
        else:
            size = (r["canonical"]["width_cm"], r["canonical"]["height_cm"])
            incomplete = [a for a in sizes.get(size, []) + unknown_sizes
                          if not conflicts(a, r) and (a["missing"] or a["issues"])]
            decision.update(status="review" if incomplete else "unmatched",
                            reason="au_attributes_incomplete" if incomplete else "no_equal_au_sku")

        if vectors is not None and decision["status"] != "matched":
            # Known contradictions cannot be overturned by a high similarity.
            size = (r["canonical"]["width_cm"], r["canonical"]["height_cm"])
            candidates = au if None in size else sizes.get(size, []) + unknown_sizes
            candidates = [a for a in candidates if not conflicts(a, r)]
            scored = [(float(vectors[au_positions[id(a)]] @ vectors[len(au) + pos]), a)
                      for a in candidates]
            scored.sort(key=lambda item: item[0], reverse=True)
            decision["model_candidates"] = [{"au_product_id": a["product_id"], "au_sku_id": a["sku_id"],
                                             "similarity": round(score, 6)} for score, a in scored[:3]]
        results.append(decision)

    # Keep au rows with no accepted partner visible in the audit as well.
    for a in au:
        if (a["product_id"], a["sku_id"]) not in used_au:
            status = "unmatched"
            if a.get("available") is False:
                status = "excluded"
            elif a.get("available") is not True or not a["single_price"] or a["missing"] or a["issues"]:
                status = "review"
            results.append({"pair_id": pair["pair_id"], "site": "au", "product_id": a["product_id"],
                            "sku_id": a["sku_id"], "sku_label": a["sku_label"], "canonical": a["canonical"],
                            "status": status,
                            "reason": "not_in_accepted_pair", "issues": a["issues"], "missing": a["missing"]})
    return matched_rows, results


def export_matches(input_path, output_dir, model=None, input_mode="title-sku", batch_size=32):
    output_dir.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    pair_ids = set()
    with input_path.open(encoding="utf-8") as source, \
         (output_dir / "matched.csv").open("w", encoding="utf-8-sig", newline="") as target, \
         (output_dir / "audit.jsonl").open("w", encoding="utf-8") as audit:
        writer = csv.DictWriter(target, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                pair = json.loads(line)
                if pair["pair_id"] in pair_ids:
                    raise ValueError("Duplicate pair_id")
                pair_ids.add(pair["pair_id"])
                rows, decisions = match_pair(pair, model, input_mode, batch_size)
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"Input line {number}: {exc}") from exc
            writer.writerows(rows)
            counts["output_rows"] += len(rows)
            counts["product_pairs"] += 1
            for decision in decisions:
                counts[f"{decision['site']}_{decision['status']}"] += 1
                audit.write(json.dumps(decision, ensure_ascii=False) + "\n")
    summary = {"counts": dict(counts), "input_mode": input_mode,
               "model_enabled": model is not None, "acceptance": "complete_equal_attributes_only",
               "source_mutated": False}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description="取得済みカーテンSKUを突合し、一致した販売中SKUだけCSVへ出力")
    parser.add_argument("--input", type=Path, required=True, help="検証済み商品ペアごとのJSONL")
    parser.add_argument("--output", type=Path, required=True, help="未作成の出力ディレクトリ")
    parser.add_argument("--model-dir", type=Path, help="任意のCPU ONNXモデル")
    parser.add_argument("--input-mode", choices=("title-sku", "sku"), default="title-sku")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    model = None
    if args.model_dir:
        from embeddings import EmbeddingModel
        model = EmbeddingModel(args.model_dir)
    print(json.dumps(export_matches(args.input, args.output, model, args.input_mode, args.batch_size), ensure_ascii=False))


if __name__ == "__main__":
    main()
