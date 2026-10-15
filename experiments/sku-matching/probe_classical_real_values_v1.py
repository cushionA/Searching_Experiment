#!/usr/bin/env python3
"""Run bounded, direct classical-library observations over frozen raw value text.

No labels, grades, predictions, or annotations are read.  Quantulum3's optional
classifier is forcibly disabled during its single-threaded parse calls.  spaCy
uses only blank Japanese tokenization and the fixed hand-written Matcher rules
also used by probe_classical_libraries_v1.py; no trained pipeline is loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path(
    ".lab-output/sku-classical-parser-20261015-v1/population-v1/parsed-values.jsonl"
)
DEFAULT_OUTPUT = Path(
    ".lab-output/sku-classical-parser-20261015-v1/real-library-probes-v1"
)
MAX_RUNTIME_SECONDS = 58.0
UNIT_SURFACES = ["cm", "mm", "kg", "g", "m"]
RANGE_SEPARATORS = ["±", "〜", "～", "~", "-", "–"]


def version_of(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    return str(value)


def read_raw_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("raw_text"), str):
                raise ValueError(f"row {line_number} lacks a string raw_text")
            records.append(row)
    return records


def physical_spans(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Read existing parser's explicit physical tokens; do not infer new ones."""
    spans = []
    for token in record.get("tokens", []):
        if (
            token.get("kind") == "quantity"
            and token.get("quantity_kind") == "physical"
            and isinstance(token.get("span"), list)
            and len(token["span"]) == 2
        ):
            spans.append(
                {
                    "quote": token.get("quote", token.get("text", "")),
                    "start": token["span"][0],
                    "end": token["span"][1],
                    "unit_raw": token.get("unit_raw"),
                    "value_raw": token.get("value_raw"),
                }
            )
    return spans


def uncovered_spans(text: str, spans: list[tuple[int, int]]) -> list[dict[str, Any]]:
    occupied = [False] * len(text)
    for start, end in spans:
        lo, hi = max(0, int(start)), min(len(text), int(end))
        for index in range(lo, hi):
            occupied[index] = True
    result = []
    index = 0
    while index < len(text):
        if occupied[index]:
            index += 1
            continue
        start = index
        while index < len(text) and not occupied[index]:
            index += 1
        result.append({"start": start, "end": index, "quote": text[start:index]})
    return result


def overlap_stats(
    physical: list[dict[str, Any]], quantities: list[dict[str, Any]]
) -> dict[str, Any]:
    pairs = []
    phys_hit = set()
    quantity_hit = set()
    for p_index, p in enumerate(physical):
        for q_index, q in enumerate(quantities):
            if p.get("start") is None or p.get("end") is None:
                continue
            if q.get("start") is None or q.get("end") is None:
                continue
            start = max(int(p["start"]), int(q["start"]))
            end = min(int(p["end"]), int(q["end"]))
            if start < end:
                phys_hit.add(p_index)
                quantity_hit.add(q_index)
                pairs.append(
                    {
                        "physical_index": p_index,
                        "quantity_index": q_index,
                        "overlap_start": start,
                        "overlap_end": end,
                    }
                )
    return {
        "physical_token_count": len(physical),
        "quantulum_quantity_count": len(quantities),
        "physical_tokens_with_span_overlap": len(phys_hit),
        "quantulum_quantities_with_span_overlap": len(quantity_hit),
        "overlapping_span_pair_count": len(pairs),
        "overlap_pairs": pairs,
        "interpretation_limit": "Character-span overlap only; not semantic agreement or correctness.",
    }


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    cli.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = cli.parse_args()
    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_bytes = input_path.read_bytes()
    input_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    records = read_raw_records(input_path)
    results_path = output_dir / "library-observations.jsonl"
    summary_path = output_dir / "summary.json"

    # Initialize direct APIs once.  Missing packages are recorded rather than
    # replaced by hand-written parsers or fake API outputs.
    quantulum_api: dict[str, Any] = {"available": False}
    quantulum_classifier_api = None
    try:
        from quantulum3 import parser as quantulum_parser
        import quantulum3.classifier as quantulum_classifier_api

        quantulum_api = {
            "available": True,
            "parser": quantulum_parser,
            "classifier_control": quantulum_classifier_api,
        }
    except Exception as exc:
        quantulum_api = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    sudachi_api: dict[str, Any] = {"available": False}
    try:
        from sudachipy import dictionary as sudachi_dictionary
        from sudachipy import tokenizer as sudachi_tokenizer

        sudachi_api = {
            "available": True,
            "tokenizer": sudachi_dictionary.Dictionary().tokenizer(),
            "mode": sudachi_tokenizer.Tokenizer.SplitMode.C,
        }
    except Exception as exc:
        sudachi_api = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    spacy_api: dict[str, Any] = {"available": False}
    try:
        import spacy
        from spacy.matcher import Matcher

        nlp = spacy.blank("ja")
        matcher = Matcher(nlp.vocab)
        matcher.add(
            "NUMBER_WITH_UNIT",
            [[{"LIKE_NUM": True}, {"LOWER": {"IN": UNIT_SURFACES}}]],
        )
        matcher.add(
            "RANGE_WITH_UNIT",
            [[
                {"LIKE_NUM": True},
                {"TEXT": {"IN": RANGE_SEPARATORS}},
                {"LIKE_NUM": True},
                {"LOWER": {"IN": UNIT_SURFACES}},
            ]],
        )
        spacy_api = {"available": True, "nlp": nlp, "matcher": matcher}
    except Exception as exc:
        spacy_api = {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    libraries = {
        "quantulum3": {
            "version": version_of("quantulum3"),
            "available": quantulum_api["available"],
        },
        "sudachi": {
            "SudachiPy": version_of("SudachiPy"),
            "SudachiDict-core": version_of("SudachiDict-core"),
            "available": sudachi_api["available"],
        },
        "spacy": {
            "version": version_of("spacy"),
            "available": spacy_api["available"],
            "language": "ja",
            "trained_components": spacy_api.get("nlp").pipe_names if spacy_api["available"] else [],
        },
    }

    start_time = time.perf_counter()
    library_seconds = {"quantulum3": 0.0, "sudachi": 0.0, "spacy_blank_ja": 0.0}
    error_counts = {"quantulum3": 0, "sudachi": 0, "spacy_blank_ja": 0}
    status_counts = {"quantulum3": 0, "sudachi": 0, "spacy_blank_ja": 0}
    physical_total = 0
    overlap_physical_hits = 0
    quantulum_total = 0
    overlap_quantity_hits = 0
    processed = 0
    timed_out = False

    previous_classifier_flag = None
    if quantulum_api["available"] and hasattr(quantulum_classifier_api, "USE_CLF"):
        previous_classifier_flag = quantulum_classifier_api.USE_CLF
        quantulum_classifier_api.USE_CLF = False

    try:
        with results_path.open("w", encoding="utf-8") as out:
            for record_index, record in enumerate(records):
                if time.perf_counter() - start_time >= MAX_RUNTIME_SECONDS:
                    timed_out = True
                    break
                raw_text = record["raw_text"]
                physical = physical_spans(record)
                physical_total += len(physical)
                row: dict[str, Any] = {
                    "record_index": record_index,
                    "raw_text": raw_text,
                    "existing_custom_physical_spans": physical,
                }

                quantulum_items = []
                q_start = time.perf_counter()
                if quantulum_api["available"]:
                    try:
                        quantities = quantulum_api["parser"].parse(
                            raw_text, lang="en_US", classifier_path=None
                        )
                        for quantity in quantities:
                            unit = getattr(quantity, "unit", None)
                            span = getattr(quantity, "span", None)
                            if span is None:
                                start, end = None, None
                            else:
                                start, end = int(span[0]), int(span[1])
                            quantulum_items.append(
                                {
                                    "surface": getattr(quantity, "surface", None),
                                    "value": getattr(quantity, "value", None),
                                    "uncertainty": getattr(quantity, "uncertainty", None),
                                    "unit": getattr(unit, "name", None),
                                    "entity": getattr(getattr(unit, "entity", None), "name", None),
                                    "start": start,
                                    "end": end,
                                }
                            )
                        row["quantulum3"] = {
                            "status": "ok",
                            "classifier_enabled": False,
                            "quantities": quantulum_items,
                            "uncovered_raw_spans": uncovered_spans(
                                raw_text,
                                [
                                    (q["start"], q["end"])
                                    for q in quantulum_items
                                    if q["start"] is not None and q["end"] is not None
                                ],
                            ),
                            "custom_physical_span_overlap": overlap_stats(physical, quantulum_items),
                        }
                        quantulum_total += len(quantulum_items)
                        overlap_physical_hits += row["quantulum3"]["custom_physical_span_overlap"]["physical_tokens_with_span_overlap"]
                        overlap_quantity_hits += row["quantulum3"]["custom_physical_span_overlap"]["quantulum_quantities_with_span_overlap"]
                        status_counts["quantulum3"] += 1
                    except Exception as exc:
                        error_counts["quantulum3"] += 1
                        row["quantulum3"] = {
                            "status": "error",
                            "classifier_enabled": False,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "uncovered_raw_spans": [{"start": 0, "end": len(raw_text), "quote": raw_text}],
                        }
                else:
                    row["quantulum3"] = {
                        "status": "unavailable",
                        "error": quantulum_api.get("error", "quantulum3 unavailable"),
                        "uncovered_raw_spans": [{"start": 0, "end": len(raw_text), "quote": raw_text}],
                    }
                    error_counts["quantulum3"] += 1
                library_seconds["quantulum3"] += time.perf_counter() - q_start

                s_start = time.perf_counter()
                if sudachi_api["available"]:
                    try:
                        row["sudachi"] = {
                            "status": "ok",
                            "split_mode": "C",
                            "tokens": [
                                {
                                    "surface": morpheme.surface(),
                                    "normalized_form_candidate": morpheme.normalized_form(),
                                    "dictionary_form": morpheme.dictionary_form(),
                                    "start": morpheme.begin(),
                                    "end": morpheme.end(),
                                    "part_of_speech": list(morpheme.part_of_speech()),
                                }
                                for morpheme in sudachi_api["tokenizer"].tokenize(
                                    raw_text, sudachi_api["mode"]
                                )
                            ],
                            "normalization_is_semantic_truth": False,
                        }
                        status_counts["sudachi"] += 1
                    except Exception as exc:
                        error_counts["sudachi"] += 1
                        row["sudachi"] = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
                else:
                    error_counts["sudachi"] += 1
                    row["sudachi"] = {"status": "unavailable", "error": sudachi_api.get("error")}
                library_seconds["sudachi"] += time.perf_counter() - s_start

                sp_start = time.perf_counter()
                if spacy_api["available"]:
                    try:
                        doc = spacy_api["nlp"](raw_text)
                        matcher = spacy_api["matcher"]
                        matches = [
                            {
                                "rule": spacy_api["nlp"].vocab.strings[match_id],
                                "start_token": begin,
                                "end_token": end,
                                "start": doc[begin].idx,
                                "end": doc[end - 1].idx + len(doc[end - 1]),
                                "quote": doc[begin:end].text,
                            }
                            for match_id, begin, end in matcher(doc)
                        ]
                        row["spacy_blank_ja"] = {
                            "status": "ok",
                            "trained_components": list(spacy_api["nlp"].pipe_names),
                            "tokens": [
                                {
                                    "text": token.text,
                                    "start": token.idx,
                                    "end": token.idx + len(token),
                                    "like_num": token.like_num,
                                }
                                for token in doc
                            ],
                            "matches": matches,
                        }
                        status_counts["spacy_blank_ja"] += 1
                    except Exception as exc:
                        error_counts["spacy_blank_ja"] += 1
                        row["spacy_blank_ja"] = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
                else:
                    error_counts["spacy_blank_ja"] += 1
                    row["spacy_blank_ja"] = {"status": "unavailable", "error": spacy_api.get("error")}
                library_seconds["spacy_blank_ja"] += time.perf_counter() - sp_start

                out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                processed += 1
                if processed % 20 == 0:
                    out.flush()
            out.flush()
    finally:
        if previous_classifier_flag is not None:
            quantulum_classifier_api.USE_CLF = previous_classifier_flag

    elapsed = time.perf_counter() - start_time
    summary = {
        "schema_version": "classical-real-value-observations-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "input_sha256": input_sha256,
        "input_row_count": len(records),
        "processed_row_count": processed,
        "run_status": "partial_time_budget" if timed_out else "complete",
        "elapsed_seconds": round(elapsed, 3),
        "single_worker": True,
        "no_network_or_package_install": True,
        "neural_model_loaded": False,
        "trained_spacy_pipeline_loaded": False,
        "quantulum_classifier": {
            "api_signature": "parse(text, lang='en_US', verbose=False, classifier_path=None)",
            "USE_CLF_forced_false_during_parse": bool(quantulum_api["available"]),
            "previous_flag_restored": previous_classifier_flag is None or quantulum_classifier_api.USE_CLF == previous_classifier_flag,
        },
        "python": sys.version,
        "libraries": libraries,
        "per_library": {
            name: {
                "elapsed_seconds": round(library_seconds[name], 3),
                "successful_rows": status_counts[name],
                "error_or_unavailable_rows": error_counts[name],
            }
            for name in library_seconds
        },
        "existing_custom_physical_token_count_in_processed_rows": physical_total,
        "quantulum_quantity_count_in_successful_rows": quantulum_total,
        "existing_custom_physical_tokens_with_any_quantulum_span_overlap": overlap_physical_hits,
        "quantulum_quantities_with_any_existing_custom_physical_span_overlap": overlap_quantity_hits,
        "coverage_metric_limit": (
            "Counts are character-span overlap with existing parsed-values.jsonl physical tokens; "
            "they do not measure semantic correctness, extraction recall, SKU matching, or final decisions."
        ),
        "spacy_matcher_rules": [
            "LIKE_NUM followed by cm/mm/kg/g/m token",
            "LIKE_NUM separator LIKE_NUM followed by cm/mm/kg/g/m token",
        ],
        "sudachi_normalized_form_limit": "Token normalization is recorded as candidate metadata, not truth.",
        "per_row_output": str(results_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(summary_path)
    return 0 if not timed_out else 2


if __name__ == "__main__":
    raise SystemExit(main())
