#!/usr/bin/env python3
"""Reference-ID transport and deterministic renderer for frozen SKU NLI v2.

This module adds source-local aliases to the existing semantic payload. It does
not choose semantic states, scopes, target facts, or exclusivity assessments.
Malformed model transport fails closed to unknown for the affected whole option.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import sku_directional_choice_nli_v1 as NLI

PROTOCOL = "sku-choice-refids-v1"
OLD_PROTOCOL = NLI.PROTOCOL
STATES = {"support", "explicit_conflict", "unknown"}
SCOPES = {"same_condition", "different_condition", "uncertain"}
RELATIONS = {"support", "explicit_conflict", "uncertain"}
EXCLUSIVITY = {"source_family", "source_body", "model_assessed", "uncertain"}


def _clean_hypothesis(hypothesis: dict[str, Any]) -> dict[str, Any]:
    """Remove only task/key identity metadata; preserve all semantic/source fields."""
    value = deepcopy(hypothesis)
    value.pop("identity", None)
    for key in ("task_id", "case_id", "source_sku_key", "sku_key", "selected_wanted"):
        value.pop(key, None)
    return value


def build_refid_model_payload(pair: dict[str, Any], context: dict[str, Any],
                              source_docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the old semantic payload and add only short indexes/aliases."""
    base = deepcopy(NLI._build_model_payload(pair, context, source_docs))
    base["context"]["hypothesis"] = _clean_hypothesis(base["context"]["hypothesis"])
    facts = base["context"]["target_selected_facts"]
    for index, fact in enumerate(facts):
        fact["target_fact_index"] = index
        fact.pop("identity", None)
        for key in ("task_id", "case_id", "source_sku_key", "sku_key", "selected_wanted"):
            fact.pop(key, None)
    for index, member in enumerate(base["candidate_vector"]):
        member["option_index"] = index
    for body_index, body in enumerate(base["context"]["eligible_source_bodies"]):
        for index, block in enumerate(body["blocks"]):
            block["body_index"] = body_index
            block["block_index"] = index
    base["refid_protocol"] = PROTOCOL
    base["model_output_schema"] = {
        "pair_id": "copy exact pair_id", "cache_key": "copy exact cache_key",
        "decisions": [{"option_index": "each index once in original order",
            "whole_value_assessed": "true only after assessing the complete original raw option",
            "state": "support|explicit_conflict|unknown",
            "scope_assessment": "same_condition|different_condition|uncertain",
            "semantic_relation": "support|explicit_conflict|uncertain",
            "scope_and_coordinate_explanation": "required string",
            "option_relation_explanation": "required string",
            "exclusivity_assessment": "source_family|source_body|model_assessed|uncertain",
            "exclusivity_explanation": "required for explicit_conflict",
            "target_fact_indices": "indices into context.target_selected_facts; model-selected",
            "body_block_refs": [{"body_index": "local eligible_source_bodies index",
                                  "block_index": "local blocks index",
                                  "quote": "exact substring of support_text",
                                  "start": "character offset in support_text",
                                  "end": "exclusive character offset"}]}],
        "whole_options": "assess every complete candidate independently; no alternative-winner inference",
    }
    return base


def _unknown(index: int, reason: str) -> dict[str, Any]:
    return {"option_index": index, "state": "unknown", "reason": reason}


def _safe_span_id(span: Any) -> str | None:
    if not isinstance(span, dict):
        return None
    try:
        return NLI._span_id(span)
    except (KeyError, TypeError, ValueError):
        return None


def _target_fact_source_ok(fact: Any) -> bool:
    if not isinstance(fact, dict):
        return False
    axis, value = fact.get("axis_name"), fact.get("value")
    axis_span, value_span = fact.get("axis_name_span"), fact.get("value_span")
    return (isinstance(fact.get("fact_id"), str) and bool(fact["fact_id"])
            and isinstance(axis, str) and bool(axis) and isinstance(value, str) and bool(value)
            and isinstance(axis_span, dict) and axis_span.get("quote") == axis
            and isinstance(value_span, dict) and value_span.get("quote") == value
            and fact["fact_id"] == _safe_span_id(value_span)
            and _safe_span_id(axis_span) is not None)


def render_v2_response(pair: dict[str, Any], context: dict[str, Any],
                       source_docs: dict[str, dict[str, Any]], model_output: Any) -> dict[str, Any]:
    """Render model decisions into the frozen v2 response; never supply semantics."""
    options = pair.get("option_values")
    if not isinstance(options, list) or not options:
        raise ValueError("pair has no complete original option vector")
    decisions = model_output.get("decisions") if isinstance(model_output, dict) else None
    identity_ok = (isinstance(model_output, dict)
                   and model_output.get("pair_id") == pair.get("pair_id")
                   and model_output.get("cache_key") == pair.get("cache_key"))
    by_index: dict[int, dict[str, Any]] = {}
    candidate = pair.get("candidate_vector")
    hypothesis = context.get("hypothesis", {})
    hypothesis_members = hypothesis.get("family_members", [])
    family_by_value = {member.get("whole_option"): member for member in hypothesis_members
                       if isinstance(member, dict) and isinstance(member.get("whole_option"), str)}
    family_vector_ok = (hypothesis.get("family_values") == options
        and isinstance(hypothesis_members, list) and len(hypothesis_members) == len(options)
        and all(isinstance(member, dict) and type(member.get("index")) is int and member.get("index") == i
                and member.get("whole_option") == options[i]
                and isinstance(member.get("evidence_id"), str)
                and isinstance(member.get("span"), dict)
                and _safe_span_id(member["span"]) == member["evidence_id"]
                for i, member in enumerate(hypothesis_members)))
    candidate_ok = (isinstance(candidate, list) and len(candidate) == len(options)
        and family_vector_ok
        and all(isinstance(member, dict) and type(member.get("option_index")) is int and member.get("option_index") == i
                and member.get("whole_option") == options[i]
                and isinstance(member.get("option_evidence_id"), str)
                and bool(member["option_evidence_id"])
                and member["option_evidence_id"] == hypothesis_members[i].get("evidence_id")
                for i, member in enumerate(candidate))
        and len({member.get("option_evidence_id") for member in candidate
                 if isinstance(member, dict)}) == len(options))
    vector_ok = (candidate_ok and isinstance(decisions, list) and len(decisions) == len(options)
                 and all(isinstance(d, dict) and type(d.get("option_index")) is int
                         and 0 <= d["option_index"] < len(options) for d in decisions)
                 and [d["option_index"] for d in decisions if isinstance(d, dict)
                      and type(d.get("option_index")) is int] == list(range(len(options)))
                 and len({d["option_index"] for d in decisions if isinstance(d, dict)
                          and type(d.get("option_index")) is int}) == len(options))
    if vector_ok and identity_ok:
        by_index = {d["option_index"]: d for d in decisions}
    citation_table: list[dict[str, Any]] = []
    citation_ids: dict[tuple, str] = {}

    def cite(evidence_id: str, span: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
        citation = {"evidence_id": evidence_id, "source_sha256": span["sha256"],
                    "start": span["start"], "end": span["end"], "quote": span["quote"]}
        if extra:
            citation.update(extra)
        key = tuple(sorted(citation.items()))
        if key not in citation_ids:
            cid = f"c{len(citation_ids):04d}"
            citation_ids[key] = cid
            citation_table.append({"citation_id": cid, **citation})
        return citation_ids[key]

    judgments = []
    target_facts = context.get("target_selected_facts", [])
    candidate = candidate if isinstance(candidate, list) else []
    option_spans = {member.get("evidence_id"): member.get("span") for member in hypothesis_members}
    bodies = []
    for body_ref in context.get("source_body_refs", []):
        body = source_docs.get(body_ref.get("document_sha256"))
        if body is not None:
            bodies.append(body)

    for index, value in enumerate(options):
        d = by_index.get(index)
        # The renderer is intentionally strict: no partially inferred semantic fields.
        valid = isinstance(d, dict)
        if valid:
            valid = (d.get("whole_value_assessed") is True
                     and d.get("state") in STATES and d.get("scope_assessment") in SCOPES
                     and d.get("semantic_relation") in RELATIONS
                     and isinstance(d.get("scope_and_coordinate_explanation"), str)
                     and bool(d["scope_and_coordinate_explanation"].strip())
                     and isinstance(d.get("option_relation_explanation"), str)
                     and bool(d["option_relation_explanation"].strip())
                     and d.get("exclusivity_assessment") in EXCLUSIVITY
                     and (d.get("state") != "explicit_conflict" or
                          (isinstance(d.get("exclusivity_explanation"), str)
                           and bool(d["exclusivity_explanation"].strip())))
                     and isinstance(d.get("target_fact_indices"), list)
                     and all(type(x) is int and 0 <= x < len(target_facts)
                             for x in d["target_fact_indices"])
                     and all(_target_fact_source_ok(target_facts[x])
                             for x in d["target_fact_indices"] if type(x) is int
                             and 0 <= x < len(target_facts))
                     and len(d["target_fact_indices"]) == len(set(d["target_fact_indices"])))
            if valid and d["state"] in {"support", "explicit_conflict"}:
                valid = (d["scope_assessment"] == "same_condition"
                         and d["semantic_relation"] == d["state"]
                         and bool(d["target_fact_indices"])
                         and (d["state"] != "explicit_conflict"
                              or d["exclusivity_assessment"] != "uncertain"))
        member = candidate[index] if candidate_ok else family_by_value.get(value, {})
        output = {"option_index": index, "whole_option": value,
                  "option_evidence_id": member.get("option_evidence_id", member.get("evidence_id")),
                  "whole_value_assessed": True}
        if not valid:
            output.update({"state": "unknown", "scope_assessment": "uncertain",
                "semantic_relation": "uncertain",
                "scope_and_coordinate_explanation": "Malformed or incomplete reference-ID decision; abstaining.",
                "option_relation_explanation": "No semantic judgment was inferred by the renderer.",
                "exclusivity_assessment": "uncertain", "target_fact_ids": [], "citation_ids": []})
            judgments.append(output)
            continue
        picked = d["target_fact_indices"]
        selected = [target_facts[i] for i in picked]
        body_refs = d.get("body_block_refs", [])
        body_valid = isinstance(body_refs, list)
        body_citation_inputs = []
        if body_valid:
            for ref in body_refs:
                if not isinstance(ref, dict) or type(ref.get("body_index")) is not int or type(ref.get("block_index")) is not int:
                    body_valid = False; break
                bi, ki = ref["body_index"], ref["block_index"]
                if not (0 <= bi < len(bodies) and 0 <= ki < len(bodies[bi].get("blocks", []))):
                    body_valid = False; break
                block, quote = bodies[bi]["blocks"][ki], ref.get("quote")
                start, end = ref.get("start"), ref.get("end")
                support = block.get("support_text", "")
                citation_meta = next((c for c in bodies[bi].get("citations", [])
                                      if c.get("block_id") == block.get("block_id")), None)
                if (citation_meta is None or type(start) is not int or type(end) is not int
                        or not 0 <= start < end <= len(support) or support[start:end] != quote):
                    body_valid = False; break
                body_citation_inputs.append(("body-block:" + block["block_id"],
                    {"sha256": citation_meta["source_raw_sha256"], "start": start,
                     "end": end, "quote": quote}, {"document_sha256": citation_meta["document_sha256"]}))
        if not body_valid:
            # Body reference errors invalidate the whole option transport.
            output.update({"state": "unknown", "scope_assessment": "uncertain",
                "semantic_relation": "uncertain", "scope_and_coordinate_explanation": "Malformed body reference; abstaining.",
                "option_relation_explanation": "No semantic judgment was inferred by the renderer.",
                "exclusivity_assessment": "uncertain", "target_fact_ids": [], "citation_ids": []})
            judgments.append(output)
            continue
        ids = []
        if candidate_ok:
            option_span = option_spans.get(candidate[index].get("option_evidence_id"))
        else:
            option_span = member.get("span")
        if isinstance(option_span, dict):
            option_evidence_id = member.get("option_evidence_id", member.get("evidence_id"))
            if isinstance(option_evidence_id, str):
                ids.append(cite(option_evidence_id, option_span))
        for fact in selected:
            for key in ("axis_name_span", "value_span"):
                span = fact.get(key)
                if isinstance(span, dict):
                    ids.append(cite(NLI._span_id(span), span))
        ids.extend(cite(*item) for item in body_citation_inputs)
        output.update({key: d[key] for key in ("state", "scope_assessment", "semantic_relation",
            "scope_and_coordinate_explanation", "option_relation_explanation", "exclusivity_assessment")})
        if "exclusivity_explanation" in d:
            output["exclusivity_explanation"] = d["exclusivity_explanation"]
        output["target_fact_ids"] = [f["fact_id"] for f in selected]
        output["citation_ids"] = list(dict.fromkeys(ids))
        judgments.append(output)
    return {"protocol": OLD_PROTOCOL, "pair_id": pair.get("pair_id"),
        "cache_key": pair.get("cache_key"), "scope_status": "model_assessed_unproved",
        "sourceproof_certified": False, "production_adoption_allowed": False,
        "citation_table": citation_table, "judgments": judgments}


DEFAULT_PREPARED = Path(__file__).resolve().parents[2] / ".lab-output/sku-next-dev-coordinate-refids-20261013-v1/source-nli-v3"


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source_only_inputs(prepared_dir: Path = DEFAULT_PREPARED):
    """Load only frozen pair/context/document artifacts, never validator bindings."""
    prepared_dir = Path(prepared_dir)
    freeze_path = prepared_dir / "freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if (freeze.get("schema_version") != OLD_PROTOCOL
            or freeze.get("dataset") != "sku-next-dev-coordinate-refids-20261013-v1"
            or freeze.get("selected_split") != "dev"
            or freeze.get("independent_test_set") is not False
            or freeze.get("labels_read") is not False
            or freeze.get("test_split_read") is not False
            or freeze.get("inference_run") is not False
            or freeze.get("production_adoption_allowed") is not False):
        raise ValueError("prepared input is not a label-blind source-only frozen bundle")
    expected = freeze.get("output_sha256")
    if not isinstance(expected, dict):
        raise ValueError("source-only freeze has no output hash manifest")
    for name in ("model-pairs.jsonl", "paired-contexts.jsonl", "source-documents.jsonl",
                 "preparation-errors.jsonl"):
        if not isinstance(expected.get(name), str) or _sha_file(prepared_dir / name) != expected[name]:
            raise ValueError("frozen source-only input SHA mismatch: " + name)
    pairs = NLI.read_rows(prepared_dir / "model-pairs.jsonl")
    contexts_rows = NLI.read_rows(prepared_dir / "paired-contexts.jsonl")
    docs_rows = NLI.read_rows(prepared_dir / "source-documents.jsonl")
    pair_map = {row["pair_id"]: row for row in pairs}
    context_map = {row["context_id"]: row for row in contexts_rows}
    docs = {row["document_sha256"]: row for row in docs_rows}
    if len(pair_map) != len(pairs) or len(context_map) != len(contexts_rows) or len(docs) != len(docs_rows):
        raise ValueError("source-only input contains duplicate identities")
    if any(pair.get("preparation_status") != "ready" for pair in pairs):
        raise ValueError("source-only pair file must contain only ready semantic pairs")
    if any(pair.get("context_id") not in context_map for pair in pairs):
        raise ValueError("source-only pair is missing its semantic context")
    if (freeze.get("prepared_pair_count") != len(pairs)
            or freeze.get("ready_pair_count") != len(pairs)
            or freeze.get("paired_context_count") != len(context_map)):
        raise ValueError("source-only pair/context counts differ from frozen receipt")
    return freeze, pair_map, context_map, docs


def dispatch_payloads(pair_ids: list[str] | None = None,
                      prepared_dir: Path = DEFAULT_PREPARED) -> list[dict[str, Any]]:
    """Render source-only model payloads for all pairs or an explicit safe subset."""
    _freeze, pairs, contexts, docs = load_source_only_inputs(prepared_dir)
    selected = list(pairs) if pair_ids is None else pair_ids
    if len(selected) != len(set(selected)) or any(pid not in pairs for pid in selected):
        raise ValueError("pair-ID selection is duplicated or outside the frozen ready source set")
    return [build_refid_model_payload(pairs[pid], contexts[pairs[pid]["context_id"]], docs)
            for pid in selected]


def render_responses(model_outputs: list[dict[str, Any]],
                     prepared_dir: Path = DEFAULT_PREPARED) -> list[dict[str, Any]]:
    """Render one source-bound v2 response per requested model pair; no inference."""
    _freeze, pairs, contexts, docs = load_source_only_inputs(prepared_dir)
    if not isinstance(model_outputs, list):
        raise ValueError("model outputs must be a JSONL row list")
    seen = set()
    responses = []
    for model_output in model_outputs:
        pid = model_output.get("pair_id") if isinstance(model_output, dict) else None
        if pid not in pairs or pid in seen:
            raise ValueError("model output has a missing, foreign, or duplicate pair identity")
        seen.add(pid)
        pair = pairs[pid]
        context = contexts[pair["context_id"]]
        responses.append(render_v2_response(pair, context, docs, model_output))
    return responses


def _write_rows_new(path: Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(NLI.canonical(row).decode("utf-8") + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    dispatch = subs.add_parser("dispatch", help="write source-only ref-ID model payloads")
    dispatch.add_argument("--prepared-dir", type=Path, default=DEFAULT_PREPARED)
    dispatch.add_argument("--pair-ids", type=Path, help="optional newline-delimited pair ID allowlist")
    dispatch.add_argument("--output", type=Path, required=True)
    render = subs.add_parser("render", help="render model decisions into frozen v2 responses")
    render.add_argument("--prepared-dir", type=Path, default=DEFAULT_PREPARED)
    render.add_argument("--model-output", type=Path, required=True, help="newline-delimited ref-ID model decisions")
    render.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "dispatch":
        pair_ids = ([line.strip() for line in args.pair_ids.read_text(encoding="utf-8").splitlines()
                     if line.strip()] if args.pair_ids else None)
        rows = dispatch_payloads(pair_ids, args.prepared_dir)
    else:
        rows = render_responses(NLI.read_rows(args.model_output), args.prepared_dir)
    _write_rows_new(args.output, rows)
    print(json.dumps({"status": "complete", "command": args.command, "row_count": len(rows),
                      "classification_run": False, "labels_read": False,
                      "production_adoption_allowed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
