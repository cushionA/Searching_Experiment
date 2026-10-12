#!/usr/bin/env python3
"""Normalize only uniquely source-bound citation evidence IDs in NLI responses.

The normalization phase is source-only metadata assembly. It does not read
source-side selected-value validator bindings, change semantic judgments, invent
citations, repair quote/offset/SHA data, or run inference. Its optional validation
wrapper delegates to the frozen validator, which reads its validator-only bindings.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import FunctionType
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_PREPARED = ROOT / ".lab-output/sku-next-dev-choice-nli-20261013-v2"
DEFAULT_SOURCE_ROOT = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
PROTOCOL = "sku-directional-choice-nli-v2"

# Reuse only deterministic source-span primitives; this module never calls the
# parent preparation or response validator and never reads validation-bindings.
sys.path.insert(0, str(HERE))
import sku_directional_choice_nli_v1 as NLI  # noqa: E402


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def read_rows(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_rows(path: Path, values: list[dict[str, Any]]) -> None:
    with Path(path).open("x", encoding="utf-8") as f:
        for row in values:
            f.write(canonical(row).decode("utf-8") + "\n")


def load_source_contexts(prepared_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load only model-pair/context files; never open validator-only bindings."""
    prepared_dir = Path(prepared_dir).resolve()
    freeze = json.loads((prepared_dir / "freeze.json").read_text(encoding="utf-8"))
    if (freeze.get("schema_version") != PROTOCOL or freeze.get("production_adoption_allowed") is not False
            or freeze.get("sourceproof_certified") is not False):
        raise ValueError("prepared data is not the frozen experimental v2 protocol")
    for name in ("model-pairs.jsonl", "paired-contexts.jsonl"):
        expected = freeze.get("output_sha256", {}).get(name)
        if not isinstance(expected, str) or sha_file(prepared_dir / name) != expected:
            raise ValueError("prepared source artifact SHA differs: " + name)
    pairs = read_rows(prepared_dir / "model-pairs.jsonl")
    contexts = read_rows(prepared_dir / "paired-contexts.jsonl")
    pair_map = {p["pair_id"]: p for p in pairs}
    context_map = {c["context_id"]: c for c in contexts}
    if len(pair_map) != len(pairs) or len(context_map) != len(contexts):
        raise ValueError("duplicate semantic pair/context ID")
    if any(p.get("protocol") != PROTOCOL for p in pairs):
        raise ValueError("unexpected model pair protocol")
    return freeze, pair_map, context_map


def _source_tuple_matches(citation: dict[str, Any], span: dict[str, Any]) -> bool:
    return (citation.get("source_sha256") == span.get("sha256")
            and type(citation.get("start")) is int and citation.get("start") == span.get("start")
            and type(citation.get("end")) is int and citation.get("end") == span.get("end")
            and isinstance(citation.get("quote"), str) and citation.get("quote") == span.get("quote"))


def _label_candidates(context: dict[str, Any], judgment: dict[str, Any], citation: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """Return exact prepared label spans in the response's declared scope only."""
    candidates: dict[str, tuple[dict[str, Any], str]] = {}
    fact_ids = judgment.get("target_fact_ids")
    if isinstance(fact_ids, list):
        fact_by_id = {f.get("fact_id"): f for f in context.get("target_selected_facts", [])}
        for fact_id in fact_ids:
            fact = fact_by_id.get(fact_id)
            span = fact.get("axis_name_span") if isinstance(fact, dict) else None
            if isinstance(span, dict) and _source_tuple_matches(citation, span):
                evidence_id = NLI._span_id(span)
                candidates[evidence_id] = (span, "target_axis_label")
    hypothesis = context.get("hypothesis", {})
    hyp_span = hypothesis.get("axis_name_span") if isinstance(hypothesis, dict) else None
    if isinstance(hyp_span, dict) and _source_tuple_matches(citation, hyp_span):
        evidence_id = NLI._span_id(hyp_span)
        candidates[evidence_id] = (hyp_span, "hypothesis_axis_label")
    return [(eid, span, role) for eid, (span, role) in candidates.items()]


def _semantic_projection(response: dict[str, Any]) -> bytes:
    """Project all non-evidence-ID fields for a preservation assertion."""
    projected = copy.deepcopy(response)
    for citation in projected.get("citation_table", []):
        if isinstance(citation, dict):
            citation.pop("evidence_id", None)
    return canonical(projected)


def normalize_response(response: dict[str, Any], pair_map: dict[str, dict[str, Any]],
                       context_map: dict[str, dict[str, Any]], source_store: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Change evidence_id only, and only for a unique exact raw label span."""
    if not isinstance(response, dict):
        raise ValueError("response must be an object")
    result = copy.deepcopy(response)
    pair = pair_map.get(response.get("pair_id"))
    if not pair or pair.get("preparation_status") != "ready":
        return result, [{"pair_id": response.get("pair_id"), "status": "unmatched_pair"}]
    if response.get("cache_key") != pair.get("cache_key"):
        return result, [{"pair_id": pair["pair_id"], "status": "cache_key_mismatch"}]
    context = context_map.get(pair.get("context_id"))
    if not context:
        return result, [{"pair_id": pair["pair_id"], "status": "missing_context"}]
    before = _semantic_projection(response)
    citations = result.get("citation_table")
    judgments = result.get("judgments")
    if not isinstance(citations, list) or not isinstance(judgments, list):
        return result, [{"pair_id": pair["pair_id"], "status": "malformed_citation_or_judgment_table"}]
    citations_by_id = {c.get("citation_id"): c for c in citations if isinstance(c, dict)}
    if len(citations_by_id) != len(citations) or None in citations_by_id:
        return result, [{"pair_id": pair["pair_id"], "status": "duplicate_or_missing_citation_id"}]
    citing_judgments: dict[str, list[dict[str, Any]]] = {}
    for judgment in judgments:
        if not isinstance(judgment, dict):
            continue
        for citation_id in judgment.get("citation_ids", []) if isinstance(judgment.get("citation_ids"), list) else []:
            citing_judgments.setdefault(citation_id, []).append(judgment)
    report: list[dict[str, Any]] = []
    for citation_id, citation in citations_by_id.items():
        if not isinstance(citation.get("evidence_id"), str):
            report.append({"pair_id": pair["pair_id"], "citation_id": citation_id,
                           "status": "missing_evidence_id"})
            continue
        scoped = citing_judgments.get(citation_id, [])
        candidates: dict[str, tuple[dict[str, Any], str]] = {}
        for judgment in scoped:
            for evidence_id, span, role in _label_candidates(context, judgment, citation):
                candidates[evidence_id] = (span, role)
        if len(candidates) != 1:
            report.append({"pair_id": pair["pair_id"], "citation_id": citation_id,
                           "status": "no_exact_source_match" if not candidates else "ambiguous_exact_source_match",
                           "candidate_evidence_ids": sorted(candidates)})
            continue
        evidence_id, (span, role) = next(iter(candidates.items()))
        try:
            NLI._verify_span(source_store, span, span["quote"], "citation normalization raw label")
        except (KeyError, TypeError, ValueError, OSError) as exc:
            report.append({"pair_id": pair["pair_id"], "citation_id": citation_id,
                           "status": "raw_source_verification_failed", "error": str(exc)})
            continue
        previous = citation["evidence_id"]
        if previous == evidence_id:
            report.append({"pair_id": pair["pair_id"], "citation_id": citation_id,
                           "status": "already_normalized", "evidence_id": evidence_id, "span_role": role})
            continue
        citation["evidence_id"] = evidence_id
        report.append({"pair_id": pair["pair_id"], "citation_id": citation_id,
                       "status": "normalized_exact_label_id", "previous_evidence_id": previous,
                       "evidence_id": evidence_id, "span_role": role})
    if _semantic_projection(result) != before:
        raise AssertionError("citation normalizer changed fields outside evidence_id")
    return result, report


def normalize_file(prepared_dir: Path, responses_path: Path, output_dir: Path,
                   source_root: Path = DEFAULT_SOURCE_ROOT) -> dict[str, Any]:
    prepared_dir, responses_path, output_dir, source_root = map(
        lambda p: Path(p).resolve(), (prepared_dir, responses_path, output_dir, source_root))
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite separate normalization output: " + str(output_dir))
    freeze, pairs, contexts = load_source_contexts(prepared_dir)
    responses = read_rows(responses_path)
    store = NLI.SOURCES.RawStore(source_root)
    normalized, reports = [], []
    for response in responses:
        updated, report = normalize_response(response, pairs, contexts, store)
        normalized.append(updated)
        reports.extend(report)
    output_dir.mkdir(parents=True)
    write_rows(output_dir / "normalized-responses.jsonl", normalized)
    write_rows(output_dir / "normalization-report.jsonl", reports)
    receipt = {"schema_version": "sku-source-nli-citation-normalization-v1",
               "artifact_type": "citation_evidence_id_metadata_normalization",
               "normalizer_sha256": sha_file(Path(__file__)),
               "prepared_protocol": PROTOCOL,
               "prepared_input_manifest_sha256": freeze["input_manifest_sha256"],
               "prepared_pair_file_sha256": sha_file(prepared_dir / "model-pairs.jsonl"),
               "prepared_context_file_sha256": sha_file(prepared_dir / "paired-contexts.jsonl"),
               "input_response_sha256": sha_file(responses_path),
               "input_response_count": len(responses),
               "output_response_count": len(normalized),
               "output_response_sha256": sha_file(output_dir / "normalized-responses.jsonl"),
               "normalization_report_sha256": sha_file(output_dir / "normalization-report.jsonl"),
               "normalized_citation_count": sum(r["status"] == "normalized_exact_label_id" for r in reports),
               "already_normalized_citation_count": sum(r["status"] == "already_normalized" for r in reports),
               "unresolved_citation_count": sum(r["status"] in {"no_exact_source_match", "ambiguous_exact_source_match",
                                                                  "raw_source_verification_failed"} for r in reports),
               "semantic_fields_changed": False, "quotes_or_offsets_changed": False,
               "citations_added_or_removed": False, "judgments_added_or_removed": False,
               "source_side_selected_value_bindings_read": False,
               "inference_run": False, "labels_read": False, "test_split_read": False,
               "scores_or_gates_created": False, "production_adoption_allowed": False,
               "unresolved_handling": "leave citation unchanged; downstream frozen validator decides validity; never rewrite semantic state"}
    with (output_dir / "normalization-receipt.json").open("x", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    return {**receipt, "output_dir": str(output_dir)}


def validate_normalized_response(prepared_dir: Path, response: dict[str, Any],
                                 source_root: Path = DEFAULT_SOURCE_ROOT) -> dict[str, Any]:
    """Run the frozen validator with one verified hypothesis-label span registered.

    The registry only adds the exact prepared `hypothesis.axis_name_span` to the
    frozen validator's source evidence lookup. Validation state logic is copied
    unchanged from the frozen validator function and its citation helper.
    """
    prepared_dir, source_root = Path(prepared_dir).resolve(), Path(source_root).resolve()
    _freeze, pairs, contexts = load_source_contexts(prepared_dir)
    pair = pairs.get(response.get("pair_id"))
    if not pair or response.get("cache_key") != pair.get("cache_key"):
        raise ValueError("response pair/cache identity is not in the prepared source set")
    context = contexts.get(pair.get("context_id"))
    if not context:
        raise ValueError("paired source context is missing")
    hypothesis = context.get("hypothesis", {})
    hyp_span = hypothesis.get("axis_name_span") if isinstance(hypothesis, dict) else None
    axis_name = hypothesis.get("axis_name") if isinstance(hypothesis, dict) else None
    if not isinstance(hyp_span, dict) or not isinstance(axis_name, str) or not axis_name:
        raise ValueError("prepared hypothesis axis-label span is unavailable")
    raw_store = NLI.SOURCES.RawStore(source_root)
    NLI._verify_span(raw_store, hyp_span, axis_name, "registered hypothesis axis label")
    evidence_id = NLI._span_id(hyp_span)
    context_id = context["context_id"]

    def augmented_context_raw_spans(candidate_context: dict[str, Any]) -> dict[str, dict[str, Any]]:
        spans = NLI._context_raw_spans(candidate_context)
        if candidate_context.get("context_id") == context_id:
            spans[evidence_id] = hyp_span
        return spans

    validator_globals = NLI.validate_response.__globals__.copy()
    citation_globals = NLI._citation_is_valid.__globals__.copy()
    citation_globals["_context_raw_spans"] = augmented_context_raw_spans
    citation_validator = FunctionType(NLI._citation_is_valid.__code__, citation_globals,
                                      NLI._citation_is_valid.__name__)
    validator_globals["_context_raw_spans"] = augmented_context_raw_spans
    validator_globals["_citation_is_valid"] = citation_validator
    validator = FunctionType(NLI.validate_response.__code__, validator_globals,
                             NLI.validate_response.__name__)
    return validator(prepared_dir, response, source_root)


def validate_normalized_responses(prepared_dir: Path, responses: list[dict[str, Any]],
                                  source_root: Path = DEFAULT_SOURCE_ROOT) -> list[dict[str, Any]]:
    """Fail closed per pair; invalid responses are source-unknown, not relabeled."""
    results = []
    seen: set[str] = set()
    for response in responses:
        if not isinstance(response, dict):
            results.append({"pair_id": None, "validation_status": "invalid_unknown",
                            "semantic_state_preserved": True, "error": "response is not an object"})
            continue
        pair_id = response.get("pair_id")
        if pair_id in seen:
            results.append({"pair_id": pair_id, "validation_status": "invalid_unknown",
                            "error": "duplicate semantic pair response"})
            continue
        if isinstance(pair_id, str):
            seen.add(pair_id)
        try:
            result = validate_normalized_response(prepared_dir, response, source_root)
            results.append({"pair_id": pair_id, "validation_status": "valid_source_metadata",
                            "semantic_truth_verified": result["semantic_truth_verified"],
                            "source_citations_verified": result["source_citations_verified"],
                            "judgment_count": result["judgment_count"]})
        except (KeyError, TypeError, ValueError, OSError) as exc:
            results.append({"pair_id": pair_id, "validation_status": "invalid_unknown",
                            "semantic_state_preserved": True, "error": str(exc)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, default=DEFAULT_PREPARED)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    args = parser.parse_args()
    print(json.dumps(normalize_file(args.prepared_dir, args.responses, args.output_dir, args.source_root),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
