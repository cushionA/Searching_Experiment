#!/usr/bin/env python3
"""Label-blind RapidFuzz ranking over installed-library text views.

The candidate population comes only from original case/product inputs. Each
case is ranked against every AU row in its product in source order. This emits
candidate rankings for offline analysis; it never accepts or rejects a match.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Callable, Iterable

from rapidfuzz import fuzz


SCHEMA = "sku-installed-library-ranking-v1"
VIEW_MODES = ("raw", "width_jaconv", "sudachi_norm", "spacy_norm")
SCORERS: tuple[tuple[str, Callable[[str, str], float]], ...] = (
    ("ratio", fuzz.ratio),
    ("WRatio", fuzz.WRatio),
    ("token_set_ratio", fuzz.token_set_ratio),
)
EXPECTED_CASE_COUNT = 1370
EXPECTED_PRODUCT_COUNT = 107
EXPECTED_CASE_ROW_COUNT = 32325


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            result.append(row)
    return result


def _value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def raw_axis_values(axes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project an ordered axis list to raw names and values without filtering."""
    if not isinstance(axes, list):
        raise TypeError("axes must be a list")
    projected = []
    for index, axis in enumerate(axes):
        if not isinstance(axis, dict):
            raise ValueError(f"axis {index} must be an object")
        name = axis.get("axis_name", axis.get("axis_label", axis.get("axis_key")))
        projected.append({"axis_name": name, "value": axis.get("value")})
    return projected


def axis_value_text(axes: list[dict[str, Any]]) -> str:
    """Join every axis value in original order, including empty values."""
    return " | ".join(_value_text(entry["value"]) for entry in raw_axis_values(axes))


def _view_text(view_lookup: dict[str, dict[str, str]], mode: str, value: Any) -> str:
    raw = _value_text(value)
    try:
        return view_lookup[mode][raw]
    except KeyError as exc:
        raise ValueError(f"missing {mode} view for original value {raw!r}") from exc


def _view_axis_text(view_lookup: dict[str, dict[str, str]], mode: str,
                    axes: list[dict[str, Any]]) -> str:
    return " | ".join(_view_text(view_lookup, mode, item["value"])
                      for item in raw_axis_values(axes))


def _ranked_candidates(query_text: str, candidates: list[dict[str, Any]],
                       scorer: Callable[[str, str], float],
                       mode: str) -> list[dict[str, Any]]:
    scored: list[tuple[float, int, dict[str, Any], str]] = []
    for source_order, candidate in enumerate(candidates):
        axes = candidate["axes"]
        text = _view_axis_text(candidate["view_lookup"], mode, axes)
        score = float(scorer(query_text, text))
        if not math.isfinite(score):
            raise ValueError("RapidFuzz returned a non-finite score")
        scored.append((score, source_order, candidate, text))
    scored.sort(key=lambda item: (-item[0], item[1]))
    tie_sizes = Counter(score for score, _, _, _ in scored)
    output = []
    prior_score: float | None = None
    prior_rank = 0
    for ordinal, (score, source_order, candidate, text) in enumerate(scored, 1):
        rank = prior_rank if prior_score == score else ordinal
        output.append({
            "source_order": source_order,
            "row_key": candidate["row_key"],
            "rank": rank,
            "tie_size": tie_sizes[score],
            "score": round(score, 6),
            "text": text,
            "raw_axis_values": raw_axis_values(candidate["axes"]),
        })
        prior_score, prior_rank = score, rank
    return output


def rank_case(case: dict[str, Any], candidates: list[dict[str, Any]],
              view_lookup: dict[str, dict[str, str]],
              modes: Iterable[str] = VIEW_MODES) -> list[dict[str, Any]]:
    """Return one score-ranked candidate list per view/scorer pair."""
    query_axes = case["rakuten_selected"]["axes"]
    query_raw_axes = raw_axis_values(query_axes)
    rankings = []
    for mode in modes:
        if mode not in VIEW_MODES:
            raise ValueError(f"unknown text view: {mode}")
        query_text = _view_axis_text(view_lookup, mode, query_axes)
        for scorer_name, scorer in SCORERS:
            rankings.append({
                "case_id": case["case_id"],
                "view": mode,
                "scorer": scorer_name,
                "query_text": query_text,
                "query_raw_axis_values": query_raw_axes,
                "candidates": _ranked_candidates(
                    query_text,
                    [{**candidate, "view_lookup": view_lookup} for candidate in candidates],
                    scorer,
                    mode,
                ),
            })
    return rankings


def _resolve_source(source: Path) -> tuple[Path, Path]:
    if (source / "cases.jsonl").is_file() and (source / "products.jsonl").is_file():
        return source / "cases.jsonl", source / "products.jsonl"
    raise FileNotFoundError("--source must contain cases.jsonl and products.jsonl")


def _build_candidates(cases: list[dict[str, Any]],
                      products: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_dossier: dict[str, dict[str, Any]] = {}
    for product in products:
        dossier_id = product.get("dossier_id")
        if not isinstance(dossier_id, str) or not dossier_id:
            raise ValueError("product has missing dossier_id")
        if dossier_id in by_dossier:
            raise ValueError(f"duplicate product dossier_id: {dossier_id}")
        by_dossier[dossier_id] = product

    result: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        case_id = case.get("case_id")
        dossier_id = case.get("dossier_id")
        if not isinstance(case_id, str) or not case_id or case_id in result:
            raise ValueError(f"invalid or duplicate case_id: {case_id!r}")
        product = by_dossier.get(dossier_id)
        if product is None:
            raise ValueError(f"no original product for dossier {dossier_id!r}")
        au_rows = product.get("au_rows")
        if not isinstance(au_rows, list) or not au_rows:
            raise ValueError(f"product {dossier_id!r} has no AU rows")
        candidates = []
        for row in au_rows:
            axes = row.get("axes")
            row_key = row.get("row_key")
            if not isinstance(axes, list) or not isinstance(row_key, str) or not row_key:
                raise ValueError(f"malformed original AU row in dossier {dossier_id!r}")
            candidates.append({"row_key": row_key, "axes": axes})
        result[case_id] = candidates
    return result


def _collect_raw_values(cases: list[dict[str, Any]],
                        products: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    def add(axes: list[dict[str, Any]]) -> None:
        for item in raw_axis_values(axes):
            raw = _value_text(item["value"])
            if raw not in seen:
                seen.add(raw)
                values.append(raw)
    for case in cases:
        add(case["rakuten_selected"]["axes"])
    for product in products:
        for row in product["au_rows"]:
            add(row["axes"])
    return values


def _load_or_build_views(views_path: Path | None,
                         raw_values: list[str]) -> tuple[dict[str, dict[str, str]], dict[str, Any], str]:
    if views_path is None:
        module_dir = Path(__file__).resolve().parent
        if str(module_dir) not in sys.path:
            sys.path.insert(0, str(module_dir))
        from installed_library_views_v1 import build_views
        payload = build_views(raw_values)
        payload_bytes = _json_bytes(payload)
        source_digest = _sha256_bytes(payload_bytes)
    else:
        payload_bytes = views_path.read_bytes()
        payload = json.loads(payload_bytes)
        source_digest = _sha256_bytes(payload_bytes)
    if not isinstance(payload, dict) or not isinstance(payload.get("views"), dict):
        raise ValueError("view payload must contain a views map")
    lookup = payload["views"]
    for mode in VIEW_MODES:
        view_map = lookup.get(mode)
        if not isinstance(view_map, dict):
            raise ValueError(f"view payload is missing mode {mode!r}")
        for value in raw_values:
            if not isinstance(view_map.get(value), str):
                raise ValueError(f"view payload has no string {mode} view for {value!r}")
        if any(not isinstance(value, str) for value in view_map.values()):
            raise ValueError(f"view map {mode!r} contains a non-string output")
    if any(lookup["raw"][value] != value for value in raw_values):
        raise ValueError("raw view must preserve each source value exactly")
    return lookup, payload, source_digest


def _runtime_versions(view_payload: dict[str, Any]) -> dict[str, Any]:
    versions = dict(view_payload.get("versions") or {})
    versions.setdefault("RapidFuzz", importlib.metadata.version("rapidfuzz"))
    return versions


def run(source: Path, output: Path, views_path: Path | None = None,
        *, enforce_population: bool = True, view_modes: Iterable[str] = VIEW_MODES) -> dict[str, Any]:
    selected_modes = tuple(view_modes)
    if not selected_modes or len(set(selected_modes)) != len(selected_modes) or any(m not in VIEW_MODES for m in selected_modes):
        raise ValueError('invalid feature view modes')
    cases_path, products_path = _resolve_source(source)
    cases = _load_jsonl(cases_path)
    products = _load_jsonl(products_path)
    if enforce_population and (len(cases) != EXPECTED_CASE_COUNT or len(products) != EXPECTED_PRODUCT_COUNT):
        raise ValueError(f"unexpected source population: cases={len(cases)}, products={len(products)}")
    candidate_map = _build_candidates(cases, products)
    total_case_rows = sum(len(candidate_map[case["case_id"]]) for case in cases)
    if enforce_population and total_case_rows != EXPECTED_CASE_ROW_COUNT:
        raise ValueError(f"unexpected case-row population: {total_case_rows}")
    raw_values = _collect_raw_values(cases, products)
    view_lookup, view_payload, view_payload_sha = _load_or_build_views(views_path, raw_values)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    start = time.perf_counter()
    ranking_path = output / "rankings.jsonl"
    row_count = 0
    with ranking_path.open("xb") as stream:
        for case in cases:
            rankings = rank_case(case, candidate_map[case["case_id"]], view_lookup, modes=selected_modes)
            for record in rankings:
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
                row_count += 1
        stream.flush()
    elapsed = time.perf_counter() - start

    view_module_path = Path(__file__).resolve().with_name("installed_library_views_v1.py")
    view_code_sha = _sha256_file(view_module_path) if view_module_path.is_file() else None
    ranking_code_sha = _sha256_file(Path(__file__).resolve())
    input_freeze = {
        "schema": "sku-installed-library-ranking-input-freeze-v1",
        "source_split": "dev",
        "labels_read": False,
        "annotations_read": False,
        "independent_test_read": False,
        "trained_models_loaded": [],
        "candidate_decisions_emitted": False,
        "source_paths": {"cases": str(cases_path), "products": str(products_path),
                         "views": str(views_path) if views_path else "installed_library_views_v1.build_views"},
        "source_sha256": {"cases": _sha256_file(cases_path), "products": _sha256_file(products_path),
                          "views_payload": view_payload_sha,
                          "views_code": view_payload.get("code_sha256") or view_code_sha,
                          "raw_values_source": view_payload.get("input_sha256"),
                          "ranking_code": ranking_code_sha},
        "untrusted_trace_sample_inspected": True,
        "untrusted_trace_features_used": False,
        "counts": {"cases": len(cases), "products": len(products),
                   "unique_au_rows": sum(len(product["au_rows"]) for product in products),
                   "case_row_candidates": total_case_rows,
                   "unique_raw_values": len(raw_values)},
        "view_modes": list(selected_modes),
        "scorers": [name for name, _ in SCORERS],
        "runtime_versions": _runtime_versions(view_payload),
    }
    input_bytes = _json_bytes(input_freeze)
    with (output / "INPUT_FREEZE.json").open("xb") as stream:
        stream.write(input_bytes)
    output_freeze = {
        "schema": "sku-installed-library-ranking-output-freeze-v1",
        "candidate_rankings_only": True,
        "accept_or_unmatched_decision": False,
        "scores_frozen_before_label_evaluation": True,
        "case_count": len(cases),
        "ranking_record_count": row_count,
        "expected_ranking_record_count": len(cases) * len(selected_modes) * len(SCORERS),
        "case_row_candidate_count": total_case_rows,
        "candidate_score_count": total_case_rows * len(selected_modes) * len(SCORERS),
        "ranking_code_sha256": ranking_code_sha,
        "elapsed_seconds": round(elapsed, 6),
        "output_files": {"rankings.jsonl": {"bytes": ranking_path.stat().st_size,
                                             "sha256": _sha256_file(ranking_path)},
                         "INPUT_FREEZE.json": {"bytes": len(input_bytes),
                                               "sha256": _sha256_bytes(input_bytes)}},
    }
    with (output / "OUTPUT_FREEZE.json").open("xb") as stream:
        stream.write(_json_bytes(output_freeze))
    return output_freeze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="directory containing original cases.jsonl and products.jsonl")
    parser.add_argument("--views", type=Path,
                        help="optional JSON payload from installed_library_views_v1.build_views")
    parser.add_argument("--output", type=Path, required=True,
                        help="new output directory; existing directories are refused")
    parser.add_argument("--view-modes", nargs='+', choices=VIEW_MODES, default=VIEW_MODES,
                        help="feature views to rank; the raw provenance view is always checked")
    args = parser.parse_args(argv)
    result = run(args.source, args.output, args.views, view_modes=args.view_modes)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
