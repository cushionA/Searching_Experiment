#!/usr/bin/env python3
"""Build price-free views by masking only installed-library currency spans."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CASES = [
    "【1000円オトク】12枚セット", "グランドシート付（+780円）",
    "サイズ指定：別途+3980円",
    "トレーンタイプ：別途4280円",
    "ワインレッド(ロング丈)+1980円",
]
BASE = Path(".lab-output/sku-installed-library-matching-20261015-v1")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None

def norm_to_source(text: str, tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapped, cursor = [], 0
    for token in tokens:
        norm = token["norm"]
        start, end = cursor, cursor + len(norm)
        mapped.append({"norm_start": start, "norm_end": end,
                       "source_start": token["start"], "source_end": token["end"],
                       "norm": norm, "surface": token["surface"]})
        cursor = end + 1
    rebuilt = " ".join(token["norm"] for token in tokens)
    if rebuilt != text:
        raise ValueError("frozen spaCy norm text does not match its token offsets")
    return mapped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(".lab-output/sku-installed-library-matching-20261015-v1"))
    args = parser.parse_args()
    base, outdir = BASE.resolve(), args.output_dir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    raw_path, old_views_path = base / "raw-values.json", base / "views.json"
    raw_values = json.loads(raw_path.read_text(encoding="utf-8"))
    old_views = json.loads(old_views_path.read_text(encoding="utf-8"))
    if not isinstance(raw_values, list) or len(raw_values) != old_views["input_count"]:
        raise ValueError("frozen raw-values/views count mismatch")
    if old_views.get("trained_components") != []:
        raise ValueError("frozen views indicate trained components; refusing adapter")

    from quantulum3 import parser as quantulum_parser
    import quantulum3.classifier as classifier_api

    prior_flag = classifier_api.USE_CLF
    classifier_api.USE_CLF = False
    audit: dict[str, Any] = {}
    price_free: dict[str, str] = {}
    currency_hits = 0
    yen_masks = 0
    map_failures = 0
    try:
        for raw in raw_values:
            norm_text = old_views["views"]["spacy_norm"][raw]
            tokens = old_views["observations"]["spacy_norm"][raw]["tokens"]
            charmap = norm_to_source(norm_text, tokens)
            entities = []
            mask_spans = []
            for q in quantulum_parser.parse(norm_text, lang="en_US", classifier_path=None):
                unit = getattr(q, "unit", None)
                entity = getattr(getattr(unit, "entity", None), "name", None)
                span = getattr(q, "span", None)
                if span is None:
                    continue
                ns, ne = int(span[0]), int(span[1])
                touched = [m for m in charmap if m["norm_start"] < ne and ns < m["norm_end"]]
                mapped = None
                if entity == "currency" and touched:
                    mapped = [min(m["source_start"] for m in touched),
                              max(m["source_end"] for m in touched)]
                    currency_hits += 1
                    # Quantulum also labels pack suffixes such as "2P" as penny.
                    # Only its explicit Japanese-yen unit is a price span here.
                    if getattr(unit, "name", None) == "japanese yen":
                        mask_spans.append(mapped)
                        yen_masks += 1
                elif entity == "currency":
                    map_failures += 1
                entities.append({
                    "entity": entity, "surface": getattr(q, "surface", None),
                    "value": getattr(q, "value", None),
                    "uncertainty": getattr(q, "uncertainty", None),
                    "unit": getattr(unit, "name", None), "norm_span": [ns, ne],
                    "source_span": mapped,
                    "source_quote": raw[mapped[0]:mapped[1]] if mapped else None,
                    "masked_as_price": bool(mapped and getattr(unit, "name", None) == "japanese yen"),
                })
            merged = []
            for start, end in sorted(mask_spans):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            text = raw
            for start, end in reversed(merged):
                text = text[:start] + text[end:]
            price_free[raw] = text
            audit[raw] = {
                "original_raw": raw, "price_free_raw": text,
                "currency_entities": [e for e in entities if e["entity"] == "currency"],
                "other_quantity_entities": [e for e in entities if e["entity"] != "currency"],
                "mask_source_spans": merged,
                "original_source_spans": {
                    view: old_views["observations"][view][raw]["tokens"]
                    for view in old_views["observations"]
                },
            }
    finally:
        classifier_api.USE_CLF = prior_flag

    # Re-run only the existing normalization/tokenizer adapter on the modified strings.
    from installed_library_views_v1 import build_views
    rebuilt = build_views(list(price_free.values()))
    names = ("raw", "width_jaconv", "sudachi_norm", "spacy_norm")
    views = {name: {} for name in names}
    observations = {name: {} for name in names}
    for raw, modified in price_free.items():
        for name in names:
            views[name][raw] = rebuilt["views"][name][modified]
            observations[name][raw] = rebuilt["observations"][name][modified]

    case_results = {}
    for case in CASES:
        info = audit.get(case)
        case_results[case] = {
            "present_in_frozen_values": info is not None,
            "currency_entity_count": len(info["currency_entities"]) if info else 0,
            "currency_source_quotes": [x["source_quote"] for x in info["currency_entities"]] if info else [],
            "price_free_raw": info["price_free_raw"] if info else None,
        }
    geometry = audit.get("楕円形")
    original_outside_masks_ok = all(
        info["price_free_raw"] == "".join(
            info["original_raw"][end:next_start]
            for end, next_start in zip(
                [0] + [span[1] for span in info["mask_source_spans"]],
                [span[0] for span in info["mask_source_spans"]] + [len(info["original_raw"])],
            )
        ) for info in audit.values()
    )
    metadata = {
        "schema": "installed-library-price-free-views-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_sha256": {"raw_values": sha(raw_path), "views": sha(old_views_path)},
        "input_code_sha256": {
            "installed_library_views_v1.py": sha(Path("experiments/sku-matching/installed_library_views_v1.py")),
            "prepare_library_price_free_views_v1.py": sha(Path(__file__).resolve()),
        },
        "versions": {"quantulum3": version("quantulum3"), **rebuilt["versions"]},
        "trained_components": [], "neural_model_loaded": False,
        "classifier_use_clf_forced_false": True,
        "classifier_flag_restored": classifier_api.USE_CLF == prior_flag,
        "input_count": len(raw_values), "unique_input_count": len(set(raw_values)),
        "currency_entity_hits": currency_hits, "japanese_yen_spans_masked": yen_masks,
        "currency_span_map_failures": map_failures,
        "raw_values_with_currency_hit": sum(bool(v["currency_entities"]) for v in audit.values()),
        "verification_cases": case_results,
        "geometry_guard": {"quote": "楕円形", "present": geometry is not None,
                           "masked": bool(geometry and geometry["mask_source_spans"])},
        "all_nonmasked_original_characters_preserved": original_outside_masks_ok,
        "unsupported_if_no_currency_hits": currency_hits == 0,
        "timings_seconds": rebuilt["timings_seconds"],
    }
    view_payload = {**metadata, "views": views, "observations": observations}
    (outdir / "views-price-free.json").write_text(
        json.dumps(view_payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    (outdir / "price-exclusion-audit.json").write_text(
        json.dumps({**metadata, "per_original_raw_key": audit}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({"views": str(outdir / "views-price-free.json"),
                      "audit": str(outdir / "price-exclusion-audit.json"),
                      "currency_entity_hits": currency_hits, "japanese_yen_spans_masked": yen_masks,
                      "currency_span_map_failures": map_failures,
                      "cases": case_results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
