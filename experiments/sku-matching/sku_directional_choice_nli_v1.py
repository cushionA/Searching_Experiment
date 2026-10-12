#!/usr/bin/env python3
"""Prepare and validate source-bound whole-option directional NLI diagnostics.

This module is a DEV-only source preparation/response validator. It does not
run inference, score labels, or alter any existing SKU gate. Model judgments
remain experimental semantic evidence; only the existing strict literal proof
can provide deterministic source proof.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUT = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8"
DEFAULT_SOURCE_ROOT = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-next-dev-choice-nli-20261013-v2"
PROTOCOL = "sku-directional-choice-nli-v2"
STATES = {"support", "explicit_conflict", "unknown"}
RELATIONS = {"same_condition", "different_condition", "uncertain"}
EXCLUSIVITY = {"source_family", "source_body", "model_assessed", "uncertain"}
SEMANTIC_RELATIONS = {"support", "explicit_conflict", "uncertain"}

sys.path.insert(0, str(HERE / "claude_v9_gate"))
import sku_gate_sources as SOURCES  # noqa: E402
from build_scoped_html_blocks_v1 import extract_blocks, verify_blocks  # noqa: E402
from prepare_scoped_html_trial_v1 import render_passage  # noqa: E402


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes())


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return sha_bytes(canonical(value))


def read_rows(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_rows(path: Path, values: list[dict[str, Any]]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(canonical(value).decode("utf-8") + "\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _verify_span(store: SOURCES.RawStore, span: Any, expected: str, label: str) -> None:
    if not isinstance(span, dict) or span.get("quote") != expected:
        raise ValueError(label + ": missing or mismatched quote")
    normalized = dict(span)
    raw_file = normalized.get("raw_file")
    if isinstance(raw_file, str):
        normalized["raw_file"] = raw_file.replace("\\", "/")
    if not SOURCES.verify_span(store, normalized):
        raise ValueError(label + ": source SHA/span verification failed")


def _locator_path(ref: dict[str, Any]) -> str | None:
    locator = ref.get("locator")
    if isinstance(locator, dict):
        return locator.get("json_path")
    return locator if isinstance(locator, str) and locator.startswith("$") else None


def _unique(items: list[Any], label: str) -> Any:
    if len(items) != 1:
        raise ValueError(label + f": expected one source coordinate, found {len(items)}")
    return items[0]


def _rakuten_family(store: SOURCES.RawStore, fact: dict[str, Any]) -> dict[str, Any]:
    ref = fact["value_span"]
    raw_file = str(ref["raw_file"]).replace("\\", "/")
    encoding = (ref.get("locator") or {}).get("encoding")
    if not isinstance(encoding, str) or not encoding:
        raise ValueError("Rakuten selected value has no source encoding")
    selectors = SOURCES.rakuten_variant_selectors(store, raw_file, encoding)
    label = fact["axis_name"]
    selector = _unique([item for item in selectors if item.get("label") == label],
                       "Rakuten selector label")
    label_span = selector.get("label_span")
    _verify_span(store, label_span, label, "Rakuten selector label")
    values = selector.get("values")
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
        raise ValueError("Rakuten source option family is malformed")
    if len(values) != len(set(values)):
        raise ValueError("duplicate Rakuten source option values")
    selector_index = selectors.index(selector)
    members = []
    for index, value in enumerate(values):
        span = SOURCES.rakuten_selector_value_span(store, raw_file, encoding, selector_index,
                                                   selector["key"], value)
        _verify_span(store, span, value, "Rakuten whole option member")
        members.append({"index": index, "value": value,
                        "role": "selected" if value == fact["value"] else "unselected",
                        "evidence_id": _span_id(span), "span": span})
    if values.count(fact["value"]) != 1:
        raise ValueError("selected Rakuten value is not a unique family member")
    return {"axis_name": label, "axis_name_span": label_span,
            "family_values": values, "members": members,
            "family_source": {"raw_file": raw_file, "sha256": label_span["sha256"],
                              "locator": label_span["locator"]}}


def _au_axis(store: SOURCES.RawStore, raw_file: str, axis_name: str) -> tuple[str, list[str], dict[str, Any], list[dict[str, Any]]]:
    raw_file = raw_file.replace("\\", "/")
    source = store.json(raw_file)
    sku = (source.get("itemInfo") or {}).get("skuInfo") or {}
    names = sku.get("optionName") or {}
    matches = [(kind, value) for kind, value in names.items() if value == axis_name]
    kind, _ = _unique(matches, "AU raw axis name")
    value_key = kind + "Names"
    family = sku.get(value_key)
    if not isinstance(family, list) or not family or any(not isinstance(v, str) or not v for v in family):
        raise ValueError("AU source option family is malformed")
    if len(family) != len(set(family)):
        raise ValueError("duplicate AU source option values")
    name_span = SOURCES.json_leaf_span(store, raw_file,
                                       f"$.itemInfo.skuInfo.optionName.{kind}", axis_name)
    _verify_span(store, name_span, axis_name, "AU raw axis name")
    members = []
    for index, value in enumerate(family):
        span = SOURCES.json_leaf_span(store, raw_file,
                                      f"$.itemInfo.skuInfo.{value_key}[{index}]", value)
        _verify_span(store, span, value, "AU whole option member")
        members.append({"index": index, "value": value,
                        "evidence_id": _span_id(span), "span": span})
    return kind, family, name_span, members


def _au_family(store: SOURCES.RawStore, fact: dict[str, Any]) -> dict[str, Any]:
    span = fact["value_span"]
    raw_file = str(span["raw_file"]).replace("\\", "/")
    kind, values, name_span, members = _au_axis(store, raw_file, fact["axis_name"])
    selected_values = [m for m in members if m["value"] == fact["value"]]
    selected = _unique(selected_values, "AU selected family member")
    for member in members:
        member["role"] = "selected" if member is selected else "unselected"
    _verify_span(store, span, fact["value"], "AU selected row fact")
    return {"axis_name": fact["axis_name"], "axis_name_span": name_span,
            "family_values": values, "members": members,
            "family_source": {"raw_file": raw_file, "sha256": name_span["sha256"],
                              "locator": name_span["locator"]}, "axis_kind": kind}


def _span_id(span: dict[str, Any]) -> str:
    return "raw-span:" + digest({key: span.get(key) for key in
                                  ("raw_file", "sha256", "locator", "start", "end", "quote")})


def _fact_from_task(store: SOURCES.RawStore, task: dict[str, Any]) -> dict[str, Any]:
    ref = task.get("raw_condition_ref") or {}
    axis = ref.get("axis_label") or ref.get("axis_name")
    value = ref.get("value")
    value_span = ref.get("value_span")
    if not isinstance(axis, str) or not axis or not isinstance(value, str) or not value:
        raise ValueError("task source condition lacks exact axis/value")
    _verify_span(store, value_span, value, "task selected source value")
    label_span = ref.get("axis_label_span")
    if label_span is not None:
        _verify_span(store, label_span, axis, "task selected source axis label")
    return {"side": "rakuten" if task["direction"] == "rakuten_to_au" else "au",
            "axis_name": axis, "value": value,
            "axis_name_span": label_span, "value_span": value_span,
            "fact_id": _span_id(value_span),
            "identity": {key: task.get(key) for key in
                         ("case_id", "dossier_id", "au_product_id", "au_row_key", "source_sku_key")}}


def _document_blocks(store: SOURCES.RawStore, document: dict[str, Any]) -> dict[str, Any]:
    """Return only blocks produced by the existing anchor-excluded AU helper."""
    if document.get("side") != "au":
        return {"document_sha256": document["document_sha256"],
                "status": "no_existing_eligible_block_route", "blocks": [], "citations": []}
    doc_text = document.get("description", {}).get("text", "")
    by_route: dict[tuple[str, str, str], dict[str, Any]] = {}
    for segment in document.get("description", {}).get("segments", []):
        for ref in segment.get("source_refs", []):
            path = _locator_path(ref)
            if not path or path not in {"$.itemInfo.itemComment", "$.itemInfo.extraItemComment",
                                        "$.itemInfo.extraItemCommentWithVideo"}:
                continue
            raw_file, expected = ref.get("raw_file"), ref.get("sha256")
            if not isinstance(raw_file, str) or not isinstance(expected, str):
                continue
            by_route[(raw_file.replace("\\", "/"), expected, path)] = ref
    blocks, citations = [], []
    for (raw_file, expected, path), original_ref in sorted(by_route.items()):
        store.raw(raw_file, expected)
        html = SOURCES.resolve_json_path(store.json(raw_file), path)
        if not isinstance(html, str) or not html:
            continue
        source_ref = {"raw_file": raw_file, "sha256": expected, "json_path": path}
        extracted = extract_blocks(html, source_ref)
        verify_blocks(html, extracted)
        for block in extracted:
            if not block.get("support_text"):
                continue
            block_record = {"block_id": block["block_id"], "field": path,
                            "passage": render_passage(block),
                            "support_text": block["support_text"],
                            "source_ref": block["source_ref"],
                            "eligible_source_runs": [{"text": run.get("text"), "kind": run.get("kind"),
                                                      "source_ref": run.get("source_ref")}
                                                     for run in block.get("support_text_runs", [])
                                                     if run.get("source_ref")]}
            blocks.append(block_record)
            citations.append({"evidence_id": "body-block:" + block["block_id"],
                              "document_sha256": document["document_sha256"],
                              "block_id": block["block_id"], "field": path,
                              "source_raw_file": raw_file, "source_raw_sha256": expected,
                              "source_json_path": path})
    return {"document_sha256": document["document_sha256"],
            "status": "eligible_blocks" if blocks else "no_eligible_blocks",
            "blocks": blocks, "citations": citations}


def _source_family(store: SOURCES.RawStore, fact: dict[str, Any]) -> dict[str, Any]:
    return _rakuten_family(store, fact) if fact["side"] == "rakuten" else _au_family(store, fact)


def _fact_record(store: SOURCES.RawStore, task: dict[str, Any]) -> dict[str, Any]:
    fact = _fact_from_task(store, task)
    family = _source_family(store, fact)
    return {**fact, "family": family}


def _verify_document_payload(docs: list[dict[str, Any]], manifest: dict[str, Any], input_dir: Path) -> None:
    expected_files = manifest.get("outputs_sha256", {})
    for name in ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl", "case-plans.jsonl"):
        if name in expected_files and sha_file(input_dir / name) != expected_files[name]:
            raise ValueError("frozen input SHA mismatch: " + name)
    if len({d["document_id"] for d in docs}) != len(docs):
        raise ValueError("duplicate source document id")


def _load_manifest(input_dir: Path) -> dict[str, Any]:
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("labels_read") is not False or manifest.get("test_split_read") is not False:
        raise ValueError("input bundle is not label-blind DEV source preparation")
    if manifest.get("selected_case_count") != 40 or manifest.get("selected_row_count") != 504 or manifest.get("selected_task_count") != 1924:
        raise ValueError("input bundle is not the frozen DEV40 handoff")
    return manifest


def prepare(input_dir: Path = DEFAULT_INPUT, source_root: Path = DEFAULT_SOURCE_ROOT,
            output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    input_dir, source_root, output_dir = map(lambda p: Path(p).resolve(), (input_dir, source_root, output_dir))
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite " + str(output_dir))
    manifest = _load_manifest(input_dir)
    tasks = read_rows(input_dir / "tasks.jsonl")
    requests = read_rows(input_dir / "requests.jsonl")
    documents = read_rows(input_dir / "documents.jsonl")
    cases = read_rows(input_dir / "case-plans.jsonl")
    rows = read_rows(input_dir / "row-plans.jsonl")
    _verify_document_payload(documents, manifest, input_dir)
    task_by_id = {x["task_id"]: x for x in tasks}
    request_by_id = {x["request_id"]: x for x in requests}
    docs_by_id = {x["document_id"]: x for x in documents}
    case_by_id = {x["case_id"]: x for x in cases}
    row_by_key = {(x["case_id"], x["au_row_key"]): x for x in rows}
    if len(task_by_id) != len(tasks) or len(request_by_id) != len(requests) or len(row_by_key) != len(rows):
        raise ValueError("duplicate task/request/row identity in frozen handoff")
    if any(task.get("request_id") not in request_by_id for task in tasks):
        raise ValueError("task references missing request")
    if any(request.get("target_document_id") not in docs_by_id for request in requests):
        raise ValueError("request references missing document")

    store = SOURCES.RawStore(source_root)
    fact_errors: dict[str, str] = {}
    facts: dict[str, dict[str, Any]] = {}
    # One verified source fact per original directional task; do not rekey on label or value.
    for task in tasks:
        try:
            facts[task["task_id"]] = _fact_record(store, task)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            fact_errors[task["task_id"]] = str(exc)

    tasks_by_case_side: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    tasks_by_row: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if task["direction"] == "rakuten_to_au":
            tasks_by_case_side[(task["case_id"], "rakuten")].append(task)
        else:
            tasks_by_row[(task["case_id"], task["au_row_key"])].append(task)
    body_cache: dict[str, dict[str, Any]] = {}
    source_docs = []
    for doc in documents:
        try:
            body_cache[doc["document_id"]] = _document_blocks(store, doc)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            body_cache[doc["document_id"]] = {"document_id": doc["document_id"],
                "document_sha256": doc["document_sha256"], "status": "source_validation_failed",
                "blocks": [], "citations": [], "error": str(exc)}
        source_docs.append(body_cache[doc["document_id"]])

    semantic_pairs: dict[str, dict[str, Any]] = {}
    errors, validation_bindings = [], []
    used_contexts = {}
    for task in tasks:
        request = request_by_id[task["request_id"]]
        row = row_by_key.get((task["case_id"], task["au_row_key"]))
        case = case_by_id.get(task["case_id"])
        pair_errors = []
        binding_base = {"task_binding": {key: task.get(key) for key in
                            ("task_id", "request_id", "case_id", "dossier_id", "au_product_id",
                             "au_row_key", "source_sku_key", "axis_key", "direction")},
                        "request_binding": {"request_id": request.get("request_id"),
                            "request_sha256": request.get("request_sha256"),
                            "target_document_id": request.get("target_document_id"),
                            "target_document_sha256": request.get("target_document_sha256")}}
        try:
            if not row or not case:
                raise ValueError("task case/row binding is absent")
            if request.get("direction") != task.get("direction"):
                raise ValueError("request/task direction differs")
            for key in ("case_id", "dossier_id", "au_product_id", "au_row_key", "source_sku_key"):
                if task.get(key) != row.get(key, task.get(key)) and key != "source_sku_key":
                    raise ValueError("task/row binding differs: " + key)
                if key in case and task.get(key) != case.get(key):
                    raise ValueError("task/case binding differs: " + key)
            options = request.get("option_values")
            if not isinstance(options, list) or not options or any(not isinstance(v, str) or not v for v in options):
                raise ValueError("full original option family missing or malformed")
            if len(options) != len(set(options)):
                raise ValueError("duplicate whole options require abstention")
            if task["task_id"] in fact_errors:
                raise ValueError("selected task source fact invalid: " + fact_errors[task["task_id"]])
            target_doc = docs_by_id[request["target_document_id"]]
            if request.get("target_document_sha256") != target_doc.get("document_sha256"):
                raise ValueError("target document SHA binding differs")
            if target_doc.get("side") != ("au" if task["direction"] == "rakuten_to_au" else "rakuten"):
                raise ValueError("direction/target document side differs")
            target_tasks = (tasks_by_row.get((task["case_id"], task["au_row_key"]), []) if target_doc["side"] == "au"
                            else tasks_by_case_side.get((task["case_id"], "rakuten"), []))
            task_fact = facts[task["task_id"]]
            candidate_family = task_fact["family"]
            if candidate_family["family_values"] != options:
                raise ValueError("request family differs from complete raw source family")
            target_facts_all = _dedupe_facts([facts[t["task_id"]] for t in target_tasks if t["task_id"] in facts])
            expected_axes = request.get("target_selected_axes")
            if not isinstance(expected_axes, list) or not expected_axes:
                raise ValueError("target selected axes are absent")
            bound_target_facts = []
            for axis in expected_axes:
                name, value = axis.get("axis_name"), axis.get("value")
                matches = [f for f in target_facts_all if f["axis_name"] == name and f["value"] == value]
                bound_target_facts.append(_unique(matches, "exact target selected axis fact"))
            target_ids = {f["fact_id"] for f in bound_target_facts}
            if len(target_ids) != len(bound_target_facts):
                raise ValueError("duplicate target selected fact bindings")
            target_facts = _dedupe_facts(bound_target_facts)
            source_doc = _paired_document(documents, task, source_side=task_fact["side"])
            source_body = body_cache[source_doc["document_id"]]
            target_body = body_cache[target_doc["document_id"]]
            target_facts_payload = [_fact_payload(f) for f in target_facts]
            hypothesis = _hypothesis_payload(task_fact, candidate_family)
            body_refs = [{"document_sha256": b["document_sha256"], "status": b["status"],
                          "block_ids": [x["block_id"] for x in b["blocks"]],
                          "raw_source_shas": sorted({x["source_ref"]["sha256"] for x in b["blocks"]})}
                         for b in (source_body, target_body)]
            context_obj = {"direction": task["direction"], "hypothesis": hypothesis,
                           "target_document_sha256": target_doc["document_sha256"],
                           "target_selected_facts": target_facts_payload,
                           "source_document_sha256": source_doc["document_sha256"],
                           "source_body_refs": body_refs}
            context_key = digest(context_obj)
            context_id = "choice-context:" + context_key
            used_contexts.setdefault(context_key, {"context_id": context_id, **context_obj})
            pair_identity = {"protocol": PROTOCOL, "context": context_obj,
                             "full_ordered_hypothesis_family": hypothesis["family_values"]}
            cache_key = digest(pair_identity)
            pair_id = "choice-pair:" + cache_key
            candidate_members = {m["value"]: m for m in candidate_family["members"]}
            if len(candidate_members) != len(options) or list(candidate_family["family_values"]) != options:
                raise ValueError("whole option vector does not bijectively bind to raw source family")
            pair = {"pair_id": pair_id, "cache_key": cache_key, "protocol": PROTOCOL,
                    "evaluation_instruction": (
                        "For every whole_option in candidate_vector, independently return support, explicit_conflict, or unknown for the exact paired context. "
                        "Assess the complete raw condition value, never a substring or stripped core. Cite the exact raw source family member and, for each decisive target fact, cite its raw axis label and selected value as separate spans. "
                        "You may use eligible body blocks to interpret the hypothesis meaning, but body text alone cannot certify a target match. "
                        "For each option give a concise scope/coordinate reason; give an exclusivity reason for any explicit conflict. "
                        "A different option being more plausible, a different winner, missing text, raw inequality, or assumed cross-source exclusivity is unknown. "
                        "Scope and relation are model assessments, not source proof; unresolved scope or relation is unknown."
                    ),
                    "context_id": context_id,
                    "option_values": list(options),
                    "candidate_vector": [{"option_index": i, "whole_option": value,
                                          "option_evidence_id": candidate_members[value]["evidence_id"]}
                                         for i, value in enumerate(options)],
                    "candidate_vector_order": "exact original raw family order",
                    "direction": task["direction"],
                    "preparation_status": "ready", "scope_status": "model_assessed_unproved",
                    "sourceproof_certified": False, "production_adoption_allowed": False}
            prior = semantic_pairs.get(pair_id)
            if prior is not None and prior != pair:
                raise ValueError("semantic pair ID collision")
            semantic_pairs.setdefault(pair_id, pair)
            exact_name_targets = [f["fact_id"] for f in target_facts if f["axis_name"] == request["axis_name"]]
            source_fact = {"axis_name": task_fact["axis_name"], "value": task_fact["value"],
                           "axis_name_span": task_fact["axis_name_span"], "value_span": task_fact["value_span"],
                           "fact_id": task_fact["fact_id"]}
            validation_bindings.append({"pair_id": pair_id, **binding_base,
                                        "source_selected_fact": source_fact,
                                        "target_fact_ids": [f["fact_id"] for f in target_facts],
                                        "exact_name_target_fact_ids": exact_name_targets,
                                        "exact_name_binding_status": "unique" if len(exact_name_targets) == 1 else
                                            ("none" if not exact_name_targets else "ambiguous"),
                                        "preparation_status": "ready"})
        except (KeyError, TypeError, ValueError, OSError) as exc:
            pair_errors.append(str(exc))
            errors.append({"task_id": task["task_id"], "request_id": request.get("request_id"), "error": str(exc)})
            validation_bindings.append({"pair_id": None, **binding_base, "source_selected_fact": None,
                                        "preparation_status": "unknown_invalid_source_context",
                                        "preparation_errors": pair_errors})

    output_dir.mkdir(parents=True, exist_ok=False)
    pairs = list(semantic_pairs.values())
    write_rows(output_dir / "model-pairs.jsonl", pairs)
    write_rows(output_dir / "paired-contexts.jsonl", list(used_contexts.values()))
    write_rows(output_dir / "source-documents.jsonl", source_docs)
    write_rows(output_dir / "preparation-errors.jsonl", errors)
    write_rows(output_dir / "validation-bindings.jsonl", validation_bindings)
    freeze = {"schema_version": PROTOCOL, "builder_sha256": sha_file(Path(__file__)),
              "artifact_type": "source_bound_choice_nli_preparation",
              "dataset": "frozen DEV40 source handoff only", "input_dir": str(input_dir),
              "input_manifest_sha256": sha_file(input_dir / "manifest.json"),
              "source_root": str(source_root), "selected_case_count": manifest["selected_case_count"],
              "selected_row_count": manifest["selected_row_count"],
              "selected_task_count": manifest["selected_task_count"],
              "prepared_pair_count": len(pairs), "ready_pair_count": sum(p["preparation_status"] == "ready" for p in pairs),
              "abstention_task_count": sum(b["preparation_status"] != "ready" for b in validation_bindings),
              "paired_context_count": len(used_contexts), "source_document_count": len(source_docs),
              "source_fact_errors": len(fact_errors), "labels_read": False, "test_split_read": False,
              "gold_or_worker_responses_read": False, "inference_run": False,
              "scores_or_gate_outputs_created": False, "prior_predictions_or_scores_in_payload": False,
              "original_task_binding_count": len(validation_bindings),
              "all_original_tasks_retained": len(validation_bindings) == len(tasks),
              "all_original_option_members_ordered": True,
              "body_policy": "only existing anchor-excluded AU source blocks with exact document/source binding; unsupported body routes are explicit unavailable context",
              "selected_fact_policy": "literal raw selected axis facts with exact raw field spans; axis label and value cited separately, never fabricated as a contiguous quote",
              "family_policy": "complete raw family rederived per source; whole composite/value strings retained; duplicate family members force abstention",
              "cross_source_semantics": "model judgment only; complete families do not establish cross-store identity; explicit model coordinate/exclusivity assessment required",
              "different_winner_policy": "an alternate option or relative winner alone is unknown, never conflict",
              "strict_literal_precedence": "existing strict literal-family proof remains protected and outside this diagnostic",
              "cache_key_policy": "SHA256 binds direction, complete unmarked source hypothesis family, full opposite-target selected facts/families, exact source/target document SHAs and body references; excludes task/request/case/row/source-SKU identities and hidden source-selected fact; only byte-identical semantic contexts deduplicate",
              "model_visibility_policy": "model payload excludes source-side selected-value record, selected marker, selected-value span, task/request/case/row identifiers and source SKU key; source selected facts exist only in validator bindings",
              "model_payload_excludes": ["desired answer field", "prior predictions", "scores", "producer hints/status", "labels", "test data"],
              "response_validator_limits": "validates protocol, complete option vector, source identity, fanout binding, citation offsets/SHA/spans, and required model-assessment fields; it cannot validate semantic truth of an LLM explanation",
              "production_adoption_allowed": False, "sourceproof_certified": False,
              "output_sha256": {p.name: sha_file(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir / "freeze.json", freeze)
    write_json(output_dir / "summary.json", {**freeze,
        "ready_pairs_by_direction": _counts(pairs, "direction"),
        "ready_pairs_by_status": _counts(pairs, "preparation_status")})
    return {**freeze, "output_dir": str(output_dir)}


def _dedupe_facts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for fact in values:
        key = (fact["side"], fact["axis_name"], fact["value"], fact["fact_id"])
        if key not in seen:
            seen.add(key)
            out.append(fact)
    return out


def _fact_key(fact: dict[str, Any]) -> dict[str, Any]:
    return {"side": fact["side"], "axis_name": fact["axis_name"],
            "value": fact["value"], "fact_id": fact["fact_id"]}


def _fact_payload(fact: dict[str, Any]) -> dict[str, Any]:
    return {"fact_id": fact["fact_id"], "side": fact["side"], "axis_name": fact["axis_name"],
            "value": fact["value"], "axis_name_span": fact["family"]["axis_name_span"],
            "value_span": fact["value_span"], "family": fact["family"]}


def _hypothesis_payload(task_fact: dict[str, Any], family: dict[str, Any]) -> dict[str, Any]:
    """Expose the source coordinate and full unmarked family, never its selected member."""
    members = [{"index": member["index"], "whole_option": member["value"],
                "evidence_id": member["evidence_id"], "span": member["span"]}
               for member in family["members"]]
    return {"source_side": task_fact["side"], "axis_name": task_fact["axis_name"],
            "axis_name_span": family["axis_name_span"],
            "family_values": list(family["family_values"]), "family_members": members,
            "family_source": family["family_source"]}


def _paired_document(documents: list[dict[str, Any]], task: dict[str, Any], source_side: str) -> dict[str, Any]:
    matches = [d for d in documents if d.get("dossier_id") == task["dossier_id"]
               and d.get("side") == source_side]
    return _unique(matches, "paired source document")


def _counts(pairs: list[dict[str, Any]], field: str) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for pair in pairs:
        result[str(pair.get(field))] += 1
    return dict(sorted(result.items()))


def _load_prepared(prepared_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]],
                                              dict[str, dict[str, Any]], dict[str, dict[str, Any]],
                                              dict[str, list[dict[str, Any]]]]:
    freeze = json.loads((prepared_dir / "freeze.json").read_text(encoding="utf-8"))
    if freeze.get("schema_version") != PROTOCOL or freeze.get("production_adoption_allowed") is not False:
        raise ValueError("prepared input is not the frozen DEV experimental protocol")
    for name, expected_sha in freeze.get("output_sha256", {}).items():
        if sha_file(prepared_dir / name) != expected_sha:
            raise ValueError("prepared source artifact SHA differs: " + name)
    pairs = read_rows(prepared_dir / "model-pairs.jsonl")
    contexts = {x["context_id"]: x for x in read_rows(prepared_dir / "paired-contexts.jsonl")}
    source_docs = {x["document_sha256"]: x for x in read_rows(prepared_dir / "source-documents.jsonl")}
    validation_bindings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in read_rows(prepared_dir / "validation-bindings.jsonl"):
        if binding.get("pair_id"):
            validation_bindings[binding["pair_id"]].append(binding)
    if len({p["pair_id"] for p in pairs}) != len(pairs):
        raise ValueError("duplicate expected pair identity")
    return freeze, {p["pair_id"]: p for p in pairs}, contexts, source_docs, validation_bindings


def build_model_payload(prepared_dir: Path, pair_id: str) -> dict[str, Any]:
    """Join a compact semantic pair with exactly its eligible, SHA-bound body blocks."""
    _freeze, pairs, contexts, source_docs, _bindings = _load_prepared(Path(prepared_dir))
    pair = pairs.get(pair_id)
    if not pair or pair.get("preparation_status") != "ready":
        raise ValueError("model payload requires one ready semantic pair")
    return _build_model_payload(pair, contexts[pair["context_id"]], source_docs)


def build_model_payloads(prepared_dir: Path, pair_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Batch-render payloads after verifying a prepared bundle once."""
    _freeze, pairs, contexts, source_docs, _bindings = _load_prepared(Path(prepared_dir))
    selected_ids = pair_ids if pair_ids is not None else list(pairs)
    return [_build_model_payload(pairs[pid], contexts[pairs[pid]["context_id"]], source_docs)
            for pid in selected_ids]


def _build_model_payload(pair: dict[str, Any], context: dict[str, Any],
                         source_docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if pair.get("preparation_status") != "ready":
        raise ValueError("model payload requires one ready semantic pair")
    bodies = []
    for body_ref in context.get("source_body_refs", []):
        body = source_docs.get(body_ref.get("document_sha256"))
        if body is None:
            raise ValueError("paired eligible body document is missing")
        citations_by_block = {c["block_id"]: c for c in body.get("citations", [])}
        blocks = []
        for block in body.get("blocks", []):
            citation = citations_by_block.get(block["block_id"])
            if not citation:
                raise ValueError("eligible body block lacks an exact source citation binding")
            blocks.append({"evidence_id": citation["evidence_id"], "block_id": block["block_id"],
                           "field": block["field"], "passage": block["passage"],
                           "support_text": block["support_text"],
                           "document_sha256": body["document_sha256"],
                           "source_sha256": citation["source_raw_sha256"]})
        bodies.append({"document_sha256": body["document_sha256"], "status": body["status"],
                       "blocks": blocks})
    return {"protocol": pair["protocol"], "pair_id": pair["pair_id"],
            "cache_key": pair["cache_key"],
            "evaluation_instruction": pair["evaluation_instruction"],
            "context": {"direction": context["direction"],
                        "hypothesis": context["hypothesis"],
                        "target_selected_facts": context["target_selected_facts"],
                        "target_document_sha256": context["target_document_sha256"],
                        "eligible_source_bodies": bodies},
            "candidate_vector": pair["candidate_vector"],
            "candidate_vector_order": pair["candidate_vector_order"],
            "scope_status": pair["scope_status"],
            "sourceproof_certified": False, "production_adoption_allowed": False}


def _context_raw_spans(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    spans: dict[str, dict[str, Any]] = {}
    hypothesis = context.get("hypothesis", {})
    for member in hypothesis.get("family_members", []):
        span = member.get("span")
        if isinstance(span, dict):
            spans[member.get("evidence_id")] = span
    for fact in context.get("target_selected_facts", []):
        for span_key in ("axis_name_span", "value_span"):
            span = fact.get(span_key)
            if isinstance(span, dict):
                spans[_span_id(span)] = span
        for member in fact.get("family", {}).get("members", []):
            span = member.get("span")
            if isinstance(span, dict):
                spans[member.get("evidence_id")] = span
    return spans


def _citation_is_valid(citation: Any, context: dict[str, Any], source_docs: dict[str, dict[str, Any]]) -> bool:
    if not isinstance(citation, dict):
        return False
    evidence_id = citation.get("evidence_id")
    if not isinstance(evidence_id, str):
        return False
    span = _context_raw_spans(context).get(evidence_id)
    if span is not None:
        return (citation.get("quote") == span.get("quote") and citation.get("start") == span.get("start")
                and citation.get("end") == span.get("end") and citation.get("source_sha256") == span.get("sha256"))
    for body_ref in context.get("source_body_refs", []):
        body = source_docs.get(body_ref.get("document_sha256"), {})
        for ref in body.get("citations", []):
            if ref.get("evidence_id") == evidence_id:
                block = next((b for b in body.get("blocks", []) if b.get("block_id") == ref.get("block_id")), None)
                start, end = citation.get("start"), citation.get("end")
                if not block:
                    return False
                source_ref = block.get("source_ref", {})
                return (type(start) is int and type(end) is int and 0 <= start < end <= len(block.get("support_text", ""))
                        and citation.get("quote") == block["support_text"][start:end]
                        and citation.get("source_sha256") == ref.get("source_raw_sha256")
                        and citation.get("document_sha256") == ref.get("document_sha256")
                        and source_ref.get("raw_file") == ref.get("source_raw_file")
                        and source_ref.get("sha256") == ref.get("source_raw_sha256")
                        and source_ref.get("locator", {}).get("json_path") == ref.get("source_json_path"))
    return False

def _verify_body_block(store: SOURCES.RawStore, block: dict[str, Any]) -> None:
    ref = block.get("source_ref") or {}
    raw_file = str(ref.get("raw_file", "")).replace("\\", "/")
    locator = ref.get("locator") or {}
    path = locator.get("json_path") if isinstance(locator, dict) else None
    if not raw_file or not path or not isinstance(ref.get("sha256"), str):
        raise ValueError("body block source identity is incomplete")
    store.raw(raw_file, ref["sha256"])
    html = SOURCES.resolve_json_path(store.json(raw_file), path)
    if not isinstance(html, str):
        raise ValueError("body block source field is not a string")
    block_ref = {"raw_file": raw_file, "sha256": ref["sha256"], "json_path": path}
    extracted = extract_blocks(html, block_ref)
    verify_blocks(html, extracted)
    matches = [item for item in extracted if item.get("block_id") == block.get("block_id")]
    if len(matches) != 1 or matches[0].get("support_text") != block.get("support_text"):
        raise ValueError("body block ID/text does not rederive from exact pinned raw source")


def validate_response(prepared_dir: Path, response: dict[str, Any],
                      source_root: Path = DEFAULT_SOURCE_ROOT) -> dict[str, Any]:
    """Validate source identity, ordered vectors, and evidence citations, not semantic truth."""
    prepared_dir, source_root = Path(prepared_dir).resolve(), Path(source_root).resolve()
    freeze, expected, contexts, source_docs, validation_bindings = _load_prepared(prepared_dir)
    raw_store = SOURCES.RawStore(source_root)
    verified_body_blocks: set[str] = set()
    pair_id = response.get("pair_id")
    if pair_id not in expected:
        raise ValueError("response pair id is absent from the prepared DEV source set")
    pair = expected[pair_id]
    bindings = validation_bindings.get(pair_id)
    if not bindings:
        raise ValueError("pair has no source-derived validator binding")
    if pair["preparation_status"] != "ready":
        raise ValueError("invalid source context is abstention-only")
    if response.get("cache_key") != pair.get("cache_key"):
        raise ValueError("response exact paired-context cache key differs")
    if response.get("scope_status") != "model_assessed_unproved" or response.get("sourceproof_certified") is not False or response.get("production_adoption_allowed") is not False:
        raise ValueError("response must remain experimental, semantic, and non-adoptable")
    if response.get("protocol") != PROTOCOL:
        raise ValueError("response protocol/version differs")
    judgments = response.get("judgments")
    if not isinstance(judgments, list) or len(judgments) != len(pair["option_values"]):
        raise ValueError("response must cover every original whole option exactly once")
    context = contexts.get(pair.get("context_id"))
    if not context:
        raise ValueError("paired source context missing")
    target_fact_ids = {f["fact_id"] for f in context.get("target_selected_facts", [])}
    # Every original task that fans out to this semantic pair must agree on its hidden
    # validator-only selected fact and exact-name axis binding, and those raw spans reverify.
    binding_signatures = set()
    for binding in bindings:
        if binding.get("preparation_status") != "ready":
            raise ValueError("semantic pair is joined to an invalid original task")
        hidden = binding.get("source_selected_fact")
        if not isinstance(hidden, dict) or not isinstance(hidden.get("value"), str):
            raise ValueError("validator-only source selected fact is missing")
        _verify_span(raw_store, hidden.get("value_span"), hidden["value"], "validator source selected fact")
        if hidden.get("axis_name_span") is not None:
            _verify_span(raw_store, hidden["axis_name_span"], hidden["axis_name"], "validator source axis name")
        if hidden.get("fact_id") != _span_id(hidden["value_span"]):
            raise ValueError("validator source selected fact identity differs")
        binding_signatures.add((tuple(binding.get("target_fact_ids", [])),
                                tuple(binding.get("exact_name_target_fact_ids", [])),
                                binding.get("exact_name_binding_status")))
    if len(binding_signatures) != 1:
        raise ValueError("deduplicated semantic pair has conflicting task-side validator bindings")
    target_binding_ids, exact_name_ids, exact_status = next(iter(binding_signatures))
    if set(target_binding_ids) != target_fact_ids:
        raise ValueError("fanout target facts differ from the semantic context")
    validated = []
    seen_indexes, seen_values = set(), set()
    raw_spans = _context_raw_spans(context)
    for judgment in judgments:
        if not isinstance(judgment, dict):
            raise ValueError("each option judgment must be an object")
        index, value = judgment.get("option_index"), judgment.get("whole_option")
        if type(index) is not int or not 0 <= index < len(pair["option_values"]):
            raise ValueError("option index is outside the original full family")
        if value != pair["option_values"][index] or judgment.get("whole_value_assessed") is not True:
            raise ValueError("judgment does not assess the exact complete original option")
        expected_member = pair["candidate_vector"][index]
        if judgment.get("option_evidence_id") != expected_member.get("option_evidence_id"):
            raise ValueError("judgment option member does not bind the complete raw source family")
        if index in seen_indexes or value in seen_values:
            raise ValueError("duplicate or rekeyed whole option judgment")
        seen_indexes.add(index); seen_values.add(value)
        state = judgment.get("state")
        if state not in STATES:
            raise ValueError("unsupported relation state")
        scope = judgment.get("scope_assessment")
        relation = judgment.get("semantic_relation")
        explanations = (judgment.get("scope_and_coordinate_explanation"),
                       judgment.get("option_relation_explanation"))
        exclusivity_kind = judgment.get("exclusivity_assessment")
        if scope not in RELATIONS or exclusivity_kind not in EXCLUSIVITY:
            raise ValueError("model scope/relation assessment is missing or malformed")
        if any(not isinstance(x, str) or not x.strip() for x in explanations):
            raise ValueError("model must concisely explain scope/coordinate and whole-option assessment")
        if state == "explicit_conflict" and (not isinstance(judgment.get("exclusivity_explanation"), str)
                                               or not judgment["exclusivity_explanation"].strip()):
            raise ValueError("explicit conflict requires an exclusivity explanation")
        if relation not in SEMANTIC_RELATIONS:
            raise ValueError("unsupported model semantic relation")
        cited_facts = judgment.get("target_fact_ids")
        if not isinstance(cited_facts, list) or any(x not in target_fact_ids for x in cited_facts):
            raise ValueError("judgment cites a target fact outside the exact paired source context")
        citation_table = response.get("citation_table")
        if not isinstance(citation_table, list):
            raise ValueError("response citation table is missing")
        citation_map = {c.get("citation_id"): c for c in citation_table if isinstance(c, dict)}
        if len(citation_map) != len(citation_table) or None in citation_map:
            raise ValueError("response citation table has missing or duplicate IDs")
        citation_ids = judgment.get("citation_ids")
        if not isinstance(citation_ids, list) or len(citation_ids) != len(set(citation_ids)) or any(x not in citation_map for x in citation_ids):
            raise ValueError("judgment citation references are missing or duplicated")
        citations = [citation_map[x] for x in citation_ids]
        if any(not _citation_is_valid(c, context, source_docs) for c in citations):
            raise ValueError("one or more citations fail source ID/SHA/offset verification")
        for citation in citations:
            evidence_id = citation.get("evidence_id", "")
            if evidence_id in raw_spans:
                _verify_span(raw_store, raw_spans[evidence_id], raw_spans[evidence_id]["quote"], "response raw citation")
            elif str(evidence_id).startswith("body-block:"):
                body = next((source_docs[ref["document_sha256"]] for ref in context.get("source_body_refs", [])
                             if ref.get("document_sha256") in source_docs and any(
                                 c.get("evidence_id") == evidence_id
                                 for c in source_docs[ref["document_sha256"]].get("citations", []))), None)
                if body is None:
                    raise ValueError("body citation is outside paired source documents")
                block = next(b for b in body["blocks"] if "body-block:" + b["block_id"] == evidence_id)
                source_ref = dict(block["source_ref"])
                source_ref["raw_file"] = str(source_ref["raw_file"]).replace("\\", "/")
                if not SOURCES.verify_span(raw_store, source_ref):
                    raise ValueError("body block raw source span failed verification")
                if block["block_id"] not in verified_body_blocks:
                    _verify_body_block(raw_store, block)
                    verified_body_blocks.add(block["block_id"])
        if state in {"support", "explicit_conflict"}:
            if scope != "same_condition" or relation != state or not cited_facts:
                raise ValueError("decisive state lacks explicit model same-condition assessment and selected target fact")
            evidence_ids = {c["evidence_id"] for c in citations}
            if expected_member["option_evidence_id"] not in evidence_ids:
                raise ValueError("decisive judgment does not cite the exact whole option family member")
            if not any(fid in evidence_ids for fid in cited_facts):
                raise ValueError("decisive judgment target fact has no direct source citation")
            target_by_id = {f["fact_id"]: f for f in context.get("target_selected_facts", [])}
            for fact_id in cited_facts:
                fact = target_by_id[fact_id]
                required_spans = {_span_id(fact["axis_name_span"]), _span_id(fact["value_span"])}
                if not required_spans.issubset(evidence_ids):
                    raise ValueError("decisive judgment must cite target axis label and selected value separately")
            if state == "explicit_conflict" and exclusivity_kind == "uncertain":
                raise ValueError("explicit conflict requires an explained exclusivity assessment")
        elif scope != "same_condition" and state != "unknown":
            raise ValueError("unresolved/different condition scope must remain unknown")
        validated.append({"option_index": index, "whole_option": value, "state": state,
                          "scope_assessment": scope, "semantic_relation": relation,
                          "citations_verified": len({c["evidence_id"] for c in citations}),
                          "semantic_truth_verified": False})
    if seen_indexes != set(range(len(pair["option_values"]))):
        raise ValueError("response omits one or more original option indexes")
    return {"pair_id": pair_id, "cache_key": pair["cache_key"],
            "fanout_task_count": len(bindings), "judgment_count": len(validated),
            "judgments": validated, "source_citations_verified": True,
            "semantic_truth_verified": False, "scope_status": "model_assessed_unproved",
            "sourceproof_certified": False, "production_adoption_allowed": False}

def validate_file(prepared_dir: Path, responses_path: Path, output_path: Path,
                  source_root: Path = DEFAULT_SOURCE_ROOT) -> dict[str, Any]:
    responses = read_rows(responses_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    results = [validate_response(prepared_dir, response, source_root) for response in responses]
    _freeze, expected, _, _, _ = _load_prepared(Path(prepared_dir))
    seen = [r["pair_id"] for r in results]
    if len(seen) != len(set(seen)):
        raise ValueError("duplicate response pair")
    if set(seen) != {pid for pid, pair in expected.items() if pair["preparation_status"] == "ready"}:
        raise ValueError("response set must exactly cover all ready prepared pairs")
    write_rows(output_path, results)
    return {"schema_version": PROTOCOL + "/validated", "response_count": len(results),
            "source_citations_verified": True, "semantic_truth_verified": False,
            "sourceproof_certified": False, "production_adoption_allowed": False,
            "validated_output_sha256": sha_file(output_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    prep.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    prep.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    valid = sub.add_parser("validate")
    valid.add_argument("--prepared-dir", type=Path, required=True)
    valid.add_argument("--responses", type=Path, required=True)
    valid.add_argument("--output", type=Path, required=True)
    valid.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.input_dir, args.source_root, args.output_dir)
    else:
        result = validate_file(args.prepared_dir, args.responses, args.output, args.source_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
