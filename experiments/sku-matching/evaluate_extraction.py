#!/usr/bin/env python3
"""Run a bounded attribute-extraction diagnostic on synthetic SKU controls.

This is a diagnostic runner, not a production evaluation: the source controls
provide attribute values but have no span-annotated evidence gold.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import resource
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT / ".lab-output" / "sku-synthetic-controls-20261009" / "evaluation.json"
DEFAULT_OUT = HERE / "results" / "gliner-extraction-diagnostic.json"
FIELDS = ("width_cm", "height_cm", "color", "lace", "pieces")
SCENARIO_ORDER = (
    "positive_format_variation",
    "negative_height_near_miss",
    "negative_near_color",
    "negative_lace_presence",
    "negative_width_height_swap",
    "negative_width_piece_count",
    "negative_missing_lace_evidence",
)


def _numeric_values(field: str, text: str) -> list[float]:
    import re

    normalized = unicodedata.normalize("NFKC", text).replace(",", "")
    pair_pattern = r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:cm|センチ)?\s*[×x＊*]\s*(\d+(?:\.\d+)?)\s*(?:cm|センチ)?"
    pairs = list(re.finditer(pair_pattern, normalized, flags=re.I))
    if pairs and field in ("width_cm", "height_cm"):
        index = 0 if field == "width_cm" else 1
        return [float(match.group(index + 1)) for match in pairs]

    axis_pattern = {
        "width_cm": r"(?:幅|横幅|width|\bW)\s*[:：]?\s*(\d+(?:\.\d+)?)",
        "height_cm": r"(?:丈|高さ|長さ|drop|height|\bH)\s*[:：]?\s*(\d+(?:\.\d+)?)",
    }[field]
    axis_values = [float(value) for value in re.findall(axis_pattern, normalized, flags=re.I)]
    if axis_values:
        return axis_values
    bare_values = [float(value) for value in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:cm|センチ)", normalized, flags=re.I)]
    if not bare_values:
        bare_values = [float(value) for value in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", normalized)]
    return bare_values


def _normalize_prediction_details(field: str, spans: list[dict[str, Any]]) -> tuple[Any, bool]:
    import re

    if not spans:
        return None, False
    pieces = [str(span.get("source_text", span.get("text", ""))) for span in spans]
    normalized_pieces = [unicodedata.normalize("NFKC", part).strip() for part in pieces if part.strip()]
    if field in ("width_cm", "height_cm"):
        values = [value for part in normalized_pieces for value in _numeric_values(field, part)]
        distinct = list(dict.fromkeys(values))
        if len(distinct) > 1:
            return None, True
        return (distinct[0], False) if distinct else (None, False)
    if field == "pieces":
        values = [int(value) for part in normalized_pieces for value in re.findall(
            r"(\d+)\s*(?:枚(?:組)?|組|点|個|本|set|pieces?|p)", part, flags=re.I
        )]
        if not values:
            values = [int(value) for part in normalized_pieces for value in re.findall(r"(?<!\d)(\d+)(?!\d)", part)]
        distinct = list(dict.fromkeys(values))
        if len(values) > 1:
            return None, True
        return (distinct[0], False) if distinct else (None, False)
    if field == "lace":
        states = []
        for part in normalized_pieces:
            compact = re.sub(r"\s+", "", part).casefold()
            if "有無" in compact:
                continue
            negative_tokens = ("付属なし", "付属無し", "未付属", "非付属", "notincluded",
                               "without", "nolace", "レースなし", "レース無し", "レース無", "なし", "無し")
            if any(token in compact for token in negative_tokens):
                states.append(False)
            positive_text = compact
            for token in negative_tokens:
                positive_text = positive_text.replace(token, "")
            if any(token in positive_text for token in ("レースあり", "レース有", "あり", "有り", "付属", "付き", "included", "withlace")):
                states.append(True)
        distinct = list(dict.fromkeys(states))
        if len(distinct) > 1:
            return None, True
        return (distinct[0], False) if distinct else (None, False)
    if field == "color":
        values = [re.sub(r"\s+", "", part).strip("/・,、:：").casefold() for part in normalized_pieces]
        values = [value for value in values if value]
        distinct = list(dict.fromkeys(values))
        if len(distinct) > 1:
            return None, True
        return (distinct[0], False) if distinct else (None, False)
    raw = " / ".join(normalized_pieces)
    return raw or None, False


def _normalize_prediction(field: str, spans: list[dict[str, Any]]) -> Any:
    """Return a normalized field, abstaining when evidence conflicts."""
    return _normalize_prediction_details(field, spans)[0]


def _expected(item: dict[str, Any], field: str) -> Any:
    value = item.get("attributes", {}).get(field)
    if field in ("width_cm", "height_cm") and value is not None:
        return float(value)
    if field == "color" and value is not None:
        return unicodedata.normalize("NFKC", str(value)).replace(" ", "").casefold()
    return value


def _same(field: str, expected: Any, predicted: Any) -> bool:
    if field in ("width_cm", "height_cm") and expected is not None and predicted is not None:
        return math.isclose(float(expected), float(predicted), rel_tol=0.0, abs_tol=1e-6)
    if field == "color" and expected is not None and predicted is not None:
        return str(expected).casefold() == str(predicted).casefold()
    return expected == predicted


def _literal_sku_label_visibility(item: dict[str, Any], field: str, reference: Any) -> str:
    """Use SKU-variant label evidence; page titles can describe a whole series."""
    import re

    if reference is None:
        return "reference_missing"
    label = unicodedata.normalize("NFKC", item["sku_label"])
    compact = re.sub(r"\s+", "", label)
    if field in ("width_cm", "height_cm"):
        dimensions = []
        for match in re.finditer(
            r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:cm|センチ)?\s*[×x＊*]\s*(\d+(?:\.\d+)?)\s*(?:cm|センチ)?",
            compact,
            flags=re.I,
        ):
            dimensions.extend((float(match.group(1)), float(match.group(2))))
        if not dimensions:
            width = re.search(r"幅\s*(\d+(?:\.\d+)?)", compact)
            height = re.search(r"(?:丈|高さ|長さ)\s*(\d+(?:\.\d+)?)", compact)
            dimensions = [float(width.group(1)) if width else math.nan,
                          float(height.group(1)) if height else math.nan]
        index = 0 if field == "width_cm" else 1
        if len(dimensions) > index and math.isclose(dimensions[index], float(reference), abs_tol=1e-6):
            return "visible"
        return "not_in_literal_sku_label"
    if field == "pieces":
        matches = re.findall(r"(\d+)\s*(?:枚(?:組)?|組|点|個|本|pieces?|sets?)", compact, flags=re.I)
        if any(int(match) == int(reference) for match in matches):
            return "visible"
        return "not_in_literal_sku_label"
    if field == "color":
        expected = unicodedata.normalize("NFKC", str(reference)).replace(" ", "").casefold()
        return "visible" if expected and expected in compact.casefold() else "not_in_literal_sku_label"
    if field == "lace":
        tail = compact.split("/")[-1].strip().casefold()
        if reference is True:
            visible = any(token in compact.casefold() for token in ("レースあり", "レース有", "laceあり", "laceincluded")) or tail in {"あり", "有", "レース有", "laceあり", "lace有"}
        else:
            visible = any(token in compact.casefold() for token in ("レースなし", "レース無し", "レース無", "laceなし", "lacenotincluded")) or tail in {"なし", "無し", "無", "レースなし", "レース無し", "レース無"}
        return "visible" if visible else "not_in_literal_sku_label"
    return "not_in_literal_sku_label"


def select_cases(cases: list[dict[str, Any]], count: int = 48) -> list[dict[str, Any]]:
    if count <= 0 or count > 48:
        raise ValueError("case count must be between 1 and 48 (bounded diagnostic)")
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_scenario[case["scenario"]].append(case)
    missing = set(SCENARIO_ORDER) - set(by_scenario)
    if missing:
        raise ValueError(f"Expected synthetic-control scenarios missing: {sorted(missing)}")

    # A 48-case selection has 6 positive controls and 7 examples from each
    # negative scenario. General smaller limits use round-robin stratification.
    if count == 48:
        chosen = []
        for scenario in SCENARIO_ORDER:
            take = 6 if scenario == "positive_format_variation" else 7
            chosen.extend(by_scenario[scenario][:take])
        return chosen

    chosen = []
    cursor = 0
    while len(chosen) < count:
        scenario = SCENARIO_ORDER[cursor % len(SCENARIO_ORDER)]
        index = cursor // len(SCENARIO_ORDER)
        if index < len(by_scenario[scenario]):
            chosen.append(by_scenario[scenario][index])
        cursor += 1
    return chosen


def _build_jobs(cases: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    jobs = []
    for case in cases:
        for side in ("au", "rakuten"):
            item = case[side]
            text = item["sku_label"] if mode == "sku" else f"{item['product_title']} / {item['sku_label']}"
            jobs.append({
                "case_id": case["case_id"],
                "scenario": case["scenario"],
                "expected_decision": case["expected_decision"],
                "expected_match": case["expected_match"],
                "label_basis": case["label_basis"],
                "side": side,
                "text": text,
                "reference": item["attributes"],
                "reference_visibility": {
                    field: _literal_sku_label_visibility(item, field, item["attributes"].get(field))
                    for field in FIELDS
                },
            })
    return jobs


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] if lo == hi else ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def _pair_decision_diagnostics(records: list[dict[str, Any]], input_mode: str, label_style: str) -> dict[str, Any]:
    """Decide from extracted fields only; never use the reference attributes."""
    from compare_models import classification_metrics

    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        grouped[record["case_id"]][record["side"]] = record
    decisions = []
    expected = []
    predicted = []
    for case_id, sides in grouped.items():
        au, rakuten = sides["au"], sides["rakuten"]
        a_attrs = au["normalized_attributes"]
        r_attrs = rakuten["normalized_attributes"]
        contradictions = []
        for field in FIELDS:
            left, right = a_attrs[field], r_attrs[field]
            if left is not None and right is not None and not _same(field, left, right):
                contradictions.append({"field": field, "au": left, "rakuten": right})
        if contradictions:
            decision = "unmatched"
            reasons = [f"{item['field']}_conflict" for item in contradictions]
        else:
            unknowns = []
            for side_name, record in (("au", au), ("rakuten", rakuten)):
                for field in FIELDS:
                    field_result = record["field_results"][field]
                    if field_result["ambiguous_evidence"]:
                        unknowns.append(f"{side_name}.{field}=ambiguous")
                    elif record["normalized_attributes"][field] is None:
                        unknowns.append(f"{side_name}.{field}=missing")
            if unknowns:
                decision = "review"
                reasons = unknowns
            else:
                decision = "matched"
                reasons = ["all_five_extracted_attributes_equal"]
        target = au["expected_decision"]
        expected.append(target)
        predicted.append(decision)
        decisions.append({
            "case_id": case_id,
            "scenario": au["scenario"],
            "input_mode": input_mode,
            "label_style": label_style,
            "expected_decision": target,
            "expected_match": au["expected_match"],
            "label_basis": au["label_basis"],
            "predicted_decision": decision,
            "decision_reasons": reasons,
            "known_contradictions": contradictions,
            "au_attributes": a_attrs,
            "rakuten_attributes": r_attrs,
        })
    return {
        "decision_source": "normalized GLiNER extracted attributes only; no provided reference attributes are filled into predictions",
        "metrics": classification_metrics(expected, predicted),
        "cases": decisions,
    }


def run_configuration(
    jobs: list[dict[str, Any]], *, input_mode: str, label_style: str, batch_size: int, threads: int, model: Any,
) -> dict[str, Any]:
    import torch

    torch.set_num_threads(threads)
    from backend_gliner_extract import ENTITY_DESCRIPTIONS, GLiNERAttributeExtractor

    extractor = GLiNERAttributeExtractor(label_style=label_style, model=model)
    outputs: list[dict[str, Any]] = []
    batch_latencies_ms = []
    amortized_latencies_ms = []
    for offset in range(0, len(jobs), batch_size):
        batch = jobs[offset : offset + batch_size]
        started = time.perf_counter()
        predictions = extractor.extract([job["text"] for job in batch], batch_size=batch_size)
        elapsed_ms = (time.perf_counter() - started) * 1000
        batch_latencies_ms.append(elapsed_ms)
        amortized_latencies_ms.append(elapsed_ms / len(batch))
        outputs.extend(predictions)

    records = []
    counts: Counter[str] = Counter()
    per_scenario: dict[str, Counter[str]] = defaultdict(Counter)
    exact_known = []
    exact_visible = []
    field_totals: dict[str, Counter[str]] = {field: Counter() for field in FIELDS}
    for job, prediction in zip(jobs, outputs, strict=True):
        normalized_details = {
            field: _normalize_prediction_details(field, prediction["evidence"].get(field, []))
            for field in FIELDS
        }
        normalized = {field: detail[0] for field, detail in normalized_details.items()}
        ambiguities = {field: detail[1] for field, detail in normalized_details.items()}
        field_results = {}
        known_matches = []
        visible_matches = []
        for field in FIELDS:
            reference = job["reference"].get(field)
            predicted = normalized[field]
            availability = job["reference_visibility"][field]
            is_ambiguous = ambiguities[field]
            if is_ambiguous:
                status = "ambiguous_evidence"
                counts[status] += 1
                per_scenario[job["scenario"]][status] += 1
                field_totals[field][status] += 1
                if reference is not None:
                    known_matches.append(False)
                    if availability == "visible":
                        visible_matches.append(False)
            elif reference is None:
                status = "missing_evidence" if predicted is None else "hallucinated_missing_evidence"
                counts[status] += 1
                per_scenario[job["scenario"]][status] += 1
                field_totals[field][status] += 1
            else:
                match = _same(field, reference, predicted)
                known_matches.append(match)
                if availability == "visible":
                    status = "exact_match" if match else ("missed_visible_reference" if predicted is None else "mismatch_visible_reference")
                    visible_matches.append(match)
                elif match:
                    status = "unavailable_reference_match"
                else:
                    status = "unavailable_reference_not_extracted" if predicted is None else "unavailable_reference_different_prediction"
                counts[status] += 1
                per_scenario[job["scenario"]][status] += 1
                field_totals[field][status] += 1
            field_results[field] = {
                "reference": reference,
                "reference_visibility": availability,
                "prediction": predicted,
                "ambiguous_evidence": is_ambiguous,
                "status": status,
            }
        exact_known.append(bool(known_matches) and all(known_matches))
        if visible_matches:
            exact_visible.append(all(visible_matches))
        records.append({
            "case_id": job["case_id"],
            "scenario": job["scenario"],
            "expected_decision": job["expected_decision"],
            "expected_match": job["expected_match"],
            "label_basis": job["label_basis"],
            "side": job["side"],
            "input_text": job["text"],
            "normalized_attributes": normalized,
            "field_results": field_results,
            "evidence": prediction["evidence"],
            "raw_output": prediction["raw_output"],
        })

    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "label_style": label_style,
        "batch_size": batch_size,
        "threads": threads,
        "case_side_count": len(jobs),
        "cold_load_seconds": None,
        "batch_call_latency_ms": {
            "count": len(batch_latencies_ms),
            "p50": statistics.median(batch_latencies_ms) if batch_latencies_ms else None,
            "p95": _percentile(batch_latencies_ms, 0.95),
            "per_text_amortized_p50": statistics.median(amortized_latencies_ms) if amortized_latencies_ms else None,
            "per_text_amortized_p95": _percentile(amortized_latencies_ms, 0.95),
        },
        "process_peak_rss_bytes": maxrss * (1024 if sys.platform != "darwin" else 1),
        "counts": dict(counts),
        "provided_reference_exact_match": {
            "count": sum(exact_known),
            "denominator": len(exact_known),
            "rate": sum(exact_known) / len(exact_known) if exact_known else None,
        },
        "visible_reference_exact_match": {
            "count": sum(exact_visible),
            "denominator": len(exact_visible),
            "rate": sum(exact_visible) / len(exact_visible) if exact_visible else None,
            "definition": "All provided non-missing attributes with their value visibly present in the SKU label match; title-only family metadata does not count as selected-SKU evidence.",
        },
        "field_counts": {field: dict(summary) for field, summary in field_totals.items()},
        "scenario_counts": {scenario: dict(summary) for scenario, summary in per_scenario.items()},
        "lace_hallucinations_on_missing_reference": field_totals["lace"]["hallucinated_missing_evidence"],
        "pair_decision_diagnostics": _pair_decision_diagnostics(records, input_mode, label_style),
        "records": records,
        "evidence_scope_note": "No span-annotated gold exists; evidence text/spans are recorded for review, but span precision or recall cannot be claimed. Attributes absent from the literal SKU label are separated from visible-span misses and are not required for visible-reference accuracy.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--input-mode", choices=("sku", "title-sku", "both"), default="both")
    parser.add_argument("--label-style", choices=("ja", "en", "both"), default="both")
    parser.add_argument("--cases", type=int, default=48)
    parser.add_argument("--batch-size", type=int, choices=(1, 8), default=8)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.threads <= 0:
        parser.error("--threads must be positive")
    source = json.loads(args.source.read_text(encoding="utf-8"))
    cases = select_cases(source["cases"], args.cases)
    modes = ("sku", "title-sku") if args.input_mode == "both" else (args.input_mode,)
    styles = ("ja", "en") if args.label_style == "both" else (args.label_style,)

    import torch
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    from backend_gliner_extract import ENTITY_DESCRIPTIONS, GLiNERAttributeExtractor

    cold_load_started = time.perf_counter()
    shared_extractor = GLiNERAttributeExtractor(label_style=styles[0])
    cold_load_seconds = time.perf_counter() - cold_load_started
    print(f"GLiNER extraction loaded in {cold_load_seconds:.2f}s; batch={args.batch_size}", flush=True)
    measured_at_utc = datetime.now(timezone.utc).isoformat()
    manifest_path = HERE / "manifests" / "gliner-extract.json"
    source_bytes = args.source.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    model_parameter = next(shared_extractor.model.parameters())

    report = {
        "model": "fastino/gliner2.5-multi-v1",
        "model_revision": "cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3",
        "source": str(args.source),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "manifest": str(manifest_path.relative_to(ROOT)),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "measured_at_utc": measured_at_utc,
        "source_note": source.get("note"),
        "case_count": len(cases),
        "scenario_counts": dict(Counter(case["scenario"] for case in cases)),
        "input_modes": {},
        "gliner2_version": importlib.metadata.version("gliner2"),
        "torch_version": importlib.metadata.version("torch"),
        "transformers_version": importlib.metadata.version("transformers"),
        "schema_labels": {style: dict(ENTITY_DESCRIPTIONS) for style in styles},
        "actual_runtime": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "torch_threads": torch.get_num_threads(),
            "torch_interop_threads": torch.get_num_interop_threads(),
            "model_class": type(shared_extractor.model).__name__,
            "parameter_dtype": str(model_parameter.dtype),
            "parameter_device": str(model_parameter.device),
        },
        "limitations": [
            "Synthetic controls are not live catalog truth or a production accuracy estimate.",
            "No span-annotated gold exists, so span precision/recall are not measured.",
        ],
    }
    if args.cases == 48 and any(n != expected for n, expected in zip(report["scenario_counts"].values(), [6, 7, 7, 7, 7, 7, 7])):
        raise RuntimeError("48-case stratification did not balance the expected scenarios")
    for mode in modes:
        jobs = _build_jobs(cases, mode)
        report["input_modes"][mode] = {}
        for style in styles:
            result = run_configuration(
                jobs, input_mode=mode, label_style=style, batch_size=args.batch_size, threads=args.threads,
                model=shared_extractor.model,
            )
            result["cold_load_seconds"] = cold_load_seconds
            report["input_modes"][mode][style] = result
            print(f"GLiNER {mode} schema={style} done", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(json.dumps({"output": str(args.output), "case_count": len(cases), "input_modes": modes, "label_styles": styles}, ensure_ascii=False))


if __name__ == "__main__":
    main()
