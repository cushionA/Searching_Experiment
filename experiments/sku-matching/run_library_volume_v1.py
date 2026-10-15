#!/usr/bin/env python3
"""Run the fixed raw, component, and axis-domain policies on a new population.

This is a population adapter/orchestrator only. Decision helpers are imported
from the frozen experiments; no SKU grammar, thresholds, or alias rules live
here. It never reads labels, grades, or prior model outputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import resource
import sys
import time
from typing import Any

import spacy

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Fixed existing policies and pure functions; their source files are pinned in
# RUN_FREEZE.json and are never edited by this adapter.
from library_axis_domain_alignment_v1 import apply_policy_edges, build_product_graph
from library_local_phrase_components_v1 import LocalPhraseCatalog
from replay_dev_nli_policies_v1 import assemble
from run_installed_library_selection_v1 import catalogs as raw_catalogs
from run_installed_library_selection_v1 import selected_state
from run_library_axis_domains_v1 import (assemble_mode, collect_families,
    combine_hypotheses, graph_state, input_family_side, validate_document_catalog_hashes,
    validate_native_catalog_coverage)
from run_library_components_v1 import evaluate_request, make_catalog_index


METHODS = ("baseline_raw", "previous_components", "mutual_unique_raw_components")
PINNED_CODE = (
    "run_library_volume_v1.py",
    "prepare_classical_population_v1.py",
    "run_installed_library_selection_v1.py",
    "run_library_components_v1.py",
    "library_local_phrase_components_v1.py",
    "run_library_axis_domains_v1.py",
    "library_axis_domain_alignment_v1.py",
    "replay_dev_nli_policies_v1.py",
    "installed_library_views_v1.py",
    "prepare_library_price_free_views_v1.py",
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            values.append(value)
    return values


def validate_price_free_views(views: dict[str, Any], views_path: Path) -> Path:
    """Verify the frozen Yen-masked view contract and its source audit."""
    if views.get("schema") != "installed-library-price-free-views-v1":
        raise ValueError("views are not the frozen price-free schema")
    if (views.get("trained_components") != [] or views.get("neural_model_loaded") is not False
            or views.get("classifier_use_clf_forced_false") is not True
            or views.get("classifier_flag_restored") is not True
            or views.get("all_nonmasked_original_characters_preserved") is not True
            or views.get("currency_span_map_failures") != 0):
        raise ValueError("price-free view preparation guards failed or are missing")
    raw_view = views.get("views", {}).get("raw")
    if not isinstance(raw_view, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                              for key, value in raw_view.items()):
        raise ValueError("price-free raw view must map original strings to strings")
    audit_path = views_path.with_name("price-exclusion-audit.json")
    audit = read_json(audit_path)
    audit_raw = audit.get("per_original_raw_key")
    if (audit.get("schema") != "installed-library-price-free-views-v1"
            or not isinstance(audit_raw, dict) or set(audit_raw) != set(raw_view)
            or audit.get("trained_components") != []
            or audit.get("neural_model_loaded") is not False
            or audit.get("classifier_use_clf_forced_false") is not True
            or audit.get("classifier_flag_restored") is not True
            or audit.get("all_nonmasked_original_characters_preserved") is not True
            or audit.get("currency_span_map_failures") != 0):
        raise ValueError("price-exclusion audit is missing, mismatched, or failed guards")
    for original, entry in audit_raw.items():
        if (not isinstance(entry, dict) or entry.get("original_raw") != original
                or entry.get("price_free_raw") != raw_view[original]):
            raise ValueError("price-exclusion audit does not reconstruct the frozen raw view")
        spans = entry.get("mask_source_spans")
        if not isinstance(spans, list):
            raise ValueError("price-exclusion audit lacks original-coordinate mask spans")
        previous_end = 0
        chunks = []
        for span in spans:
            if (not isinstance(span, list) or len(span) != 2
                    or any(not isinstance(offset, int) or isinstance(offset, bool) for offset in span)):
                raise ValueError("invalid price mask source span")
            start, end = span
            if start < previous_end or start < 0 or end <= start or end > len(original):
                raise ValueError("overlapping or out-of-range price mask source span")
            chunks.append(original[previous_end:start])
            previous_end = end
        chunks.append(original[previous_end:])
        if "".join(chunks) != raw_view[original]:
            raise ValueError("price-free raw view differs outside audited mask spans")
    expected_code_pins = {
        "installed_library_views_v1.py": sha(HERE / "installed_library_views_v1.py"),
        "prepare_library_price_free_views_v1.py": sha(HERE / "prepare_library_price_free_views_v1.py"),
    }
    if views.get("input_code_sha256") != expected_code_pins or audit.get("input_code_sha256") != expected_code_pins:
        raise ValueError("price-free view preparation code pins differ")
    for field in ("input_sha256", "versions", "input_count", "unique_input_count",
                  "currency_entity_hits", "japanese_yen_spans_masked"):
        if audit.get(field) != views.get(field):
            raise ValueError("price-free view and audit metadata differ: " + field)
    return audit_path


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def load_population(source: Path, original: Path) -> tuple[
        dict[str, Any], list[dict], list[dict], list[dict], list[dict], list[dict],
        list[dict], list[dict]]:
    """Read and manifest-check the standard population input bundle."""
    manifest = read_json(source / "manifest.json")
    names = ("requests.jsonl", "documents.jsonl", "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl")
    requests, documents, rows, cases, tasks = [read_jsonl(source / name) for name in names]
    original_cases = read_jsonl(original / "cases.jsonl")
    original_products = read_jsonl(original / "products.jsonl")
    counts = {"unique_request_count": len(requests), "document_count": len(documents),
              "row_plan_count": len(rows), "case_count": len(cases),
              "task_binding_count": len(tasks), "product_count": len(original_products)}
    if any(manifest.get(name) != count for name, count in counts.items()):
        raise ValueError("population manifest counts do not match inputs")
    if manifest.get("source_inputs") != {
            "cases.jsonl": sha(original / "cases.jsonl"),
            "products.jsonl": sha(original / "products.jsonl")}:
        raise ValueError("original cases/products SHA differs from population manifest")
    output_hashes = manifest.get("output_sha256")
    if not isinstance(output_hashes, dict):
        raise ValueError("population manifest lacks output_sha256 map")
    for name in names:
        path = source / name
        if not path.is_file() or sha(path) != output_hashes.get(name):
            raise ValueError("population manifest output SHA mismatch: " + name)
    return manifest, requests, documents, rows, cases, tasks, original_cases, original_products


def validate_population(manifest: dict[str, Any], requests: list[dict[str, Any]],
                        documents: list[dict[str, Any]], rows: list[dict[str, Any]],
                        cases: list[dict[str, Any]], tasks: list[dict[str, Any]],
                        original_cases: list[dict[str, Any]], original_products: list[dict[str, Any]],
                        views: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Validate identities/bindings and retain every original native family."""
    if any(not isinstance(r.get("request_id"), str) or not r["request_id"] for r in requests):
        raise ValueError("invalid request ID")
    if len({r["request_id"] for r in requests}) != len(requests):
        raise ValueError("duplicate request ID")
    if any(not isinstance(d.get("document_id"), str) or not d["document_id"] for d in documents):
        raise ValueError("invalid document ID")
    if len({d["document_id"] for d in documents}) != len(documents):
        raise ValueError("duplicate document ID")
    if any(not isinstance(c.get("case_id"), str) or not c["case_id"] for c in cases):
        raise ValueError("invalid case ID")
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("duplicate case ID")
    products_by_dossier = {p["dossier_id"]: p for p in original_products}
    if len(products_by_dossier) != len(original_products):
        raise ValueError("duplicate native product dossier")
    cases_by_id = {c["case_id"]: c for c in cases}
    original_cases_by_id = {c["case_id"]: c for c in original_cases}
    if len(original_cases_by_id) != len(original_cases):
        raise ValueError("duplicate original case ID")
    if set(cases_by_id) != set(original_cases_by_id):
        raise ValueError("prepared and original case IDs differ")
    for case_id, case in cases_by_id.items():
        original_case = original_cases_by_id[case_id]
        if case.get("dossier_id") != original_case.get("dossier_id"):
            raise ValueError("prepared/original case dossier differs")
        if case.get("dossier_id") not in products_by_dossier:
            raise ValueError("case refers to missing product")
        native_product = products_by_dossier[case["dossier_id"]]
        native_row_keys = [row.get("row_key") for row in native_product.get("au_rows", [])]
        if case.get("row_keys") != native_row_keys:
            raise ValueError("prepared case does not retain every native AU row in source order")

    docs = {d["document_id"]: d for d in documents}
    if len(docs) != len(documents):
        raise ValueError("duplicate document identity")
    product_dossiers = set(products_by_dossier)
    document_pairs = [(d.get("dossier_id"), d.get("side")) for d in documents]
    expected_document_pairs = {(dossier, side) for dossier in product_dossiers for side in ("au", "rakuten")}
    if len(document_pairs) != len(set(document_pairs)) or set(document_pairs) != expected_document_pairs:
        raise ValueError("documents do not cover exactly one AU and Rakuten document per native product")
    validate_document_catalog_hashes(documents, original_products)

    for req in requests:
        if req.get("direction") not in {"au_to_rakuten", "rakuten_to_au"}:
            raise ValueError("request has invalid direction")
        if not isinstance(req.get("axis_name"), str):
            raise ValueError("request axis name must be preserved as a string")
        options = req.get("option_values")
        if not isinstance(options, list):
            raise ValueError("request option_values must remain a list")
        target_axes = req.get("target_selected_axes")
        if not isinstance(target_axes, list) or any(not isinstance(axis, dict) for axis in target_axes):
            raise ValueError("request has invalid target selected axes")
    request_by_id = {r["request_id"]: r for r in requests}
    task_by_id = {}
    for task in tasks:
        tid = task.get("task_id")
        if not isinstance(tid, str) or not tid or tid in task_by_id:
            raise ValueError("missing or duplicate task ID")
        task_by_id[tid] = task
    row_keys_by_case: dict[str, list[str]] = defaultdict(list)
    bound_tasks = []
    for row in rows:
        case_id, row_key = row.get("case_id"), row.get("au_row_key")
        if case_id not in cases_by_id or row_key in row_keys_by_case[case_id]:
            raise ValueError("foreign case or duplicate row key")
        if row.get("dossier_id") != cases_by_id[case_id].get("dossier_id"):
            raise ValueError("row/case dossier mismatch")
        row_keys_by_case[case_id].append(row_key)
        row_index = row.get("au_row_index")
        native_rows = products_by_dossier[row["dossier_id"]].get("au_rows", [])
        if (not isinstance(row_index, int) or isinstance(row_index, bool)
                or row_index < 0 or row_index >= len(native_rows)
                or native_rows[row_index].get("row_key") != row_key):
            raise ValueError("prepared row does not bind to the native AU row inventory")
        for binding in row.get("bindings", []):
            task = task_by_id.get(binding.get("task_id"))
            req = request_by_id.get(binding.get("request_id"))
            if task is None or req is None:
                raise ValueError("foreign task/request binding")
            if (task.get("case_id"), task.get("au_row_key"), task.get("request_id"), task.get("direction")) != (
                    case_id, row_key, binding["request_id"], req["direction"]):
                raise ValueError("task case/row/request/direction envelope mismatch")
            if task.get("axis_key") != binding.get("axis_key"):
                raise ValueError("task/binding axis key mismatch")
            condition = task.get("raw_condition_ref", {})
            if (condition.get("axis_name"), condition.get("value")) != (
                    req.get("axis_name"), req.get("selected_value")):
                raise ValueError("task condition/request mismatch")
            bound_tasks.append(task["task_id"])
    bound_request_ids = {task["request_id"] for task in tasks}
    if (len(bound_tasks) != len(tasks) or len(set(bound_tasks)) != len(tasks)
            or set(bound_tasks) != set(task_by_id)):
        raise ValueError("task bindings are not one-to-one")
    if bound_request_ids != set(request_by_id):
        raise ValueError("every request must be bound to at least one original task")
    for case in cases:
        if row_keys_by_case[case["case_id"]] != case.get("row_keys"):
            raise ValueError("case row order/coverage differs")
    expanded_native_rows = sum(len(products_by_dossier[case["dossier_id"]].get("au_rows", []))
                               for case in original_cases)
    if len(rows) != expanded_native_rows:
        raise ValueError("prepared row count differs from full case-expanded native AU row inventory")

    raw_view = views.get("views", {}).get("raw")
    if not isinstance(raw_view, dict):
        raise ValueError("price-free views lack raw mapping")
    expected_values = {v for req in requests for v in req.get("option_values", []) if isinstance(v, str)}
    expected_values.update(axis.get("value") for req in requests
                           for axis in req.get("target_selected_axes", [])
                           if isinstance(axis.get("value"), str))
    if not expected_values.issubset(raw_view):
        raise ValueError("price-free raw view misses a request/catalog value")
    if any(not isinstance(raw_view[value], str) for value in expected_values):
        raise ValueError("price-free raw view has a non-string mapped value")
    # Full schema and audit validation happens before any fixed policy runs.

    valid_axis_requests = [req for req in requests if req.get("axis_name")]
    families_by_dossier: dict[str, Any] = collect_families(valid_axis_requests, docs)
    for req in requests:
        if req.get("axis_name"):
            continue
        doc = docs[req["target_document_id"]]
        dossier, side, name = doc["dossier_id"], input_family_side(req["direction"]), req["axis_name"]
        entry = families_by_dossier.setdefault(dossier, {"au": {}, "rakuten": {}})[side].setdefault(
            name, {"axis_name": name, "raw_values": [], "request_ids": [], "vectors": [],
                  "valid": False, "invalid_reason": "empty_native_axis_name"})
        entry["request_ids"].append(req["request_id"])
        entry["vectors"].append({"option_values": req.get("option_values"), "request_ids": [req["request_id"]]})
    validate_native_catalog_coverage(families_by_dossier, original_products)
    return docs, families_by_dossier


def build_component_context(requests: list[dict[str, Any]], docs: dict[str, dict[str, Any]],
                            raw_view: dict[str, str]) -> tuple[Any, dict, dict, dict, dict, dict]:
    """Build the frozen PhraseMatcher contexts using the existing policy API."""
    nlp = spacy.blank("ja")
    families, catalog_values, collisions = make_catalog_index(requests, docs, raw_view, nlp)
    analyzers: dict[tuple[str, str], LocalPhraseCatalog] = {}
    catalog_records = []
    for (dossier, side), catalog in sorted(catalog_values.items()):
        analyzer = LocalPhraseCatalog(nlp, catalog)
        analyzer.metadata["catalog_id"] = f"{dossier}:{side}"
        analyzers[(dossier, side)] = analyzer
        catalog_records.append({"catalog_id": f"{dossier}:{side}", "dossier_id": dossier,
            "side": side, "axes": catalog, "axis_collision_exclusions": collisions.get((dossier, side), []),
            "adapter_metadata": analyzer.metadata, "production_adoption_allowed": False})
    return nlp, families, analyzers, catalog_records, collisions, {}


def component_hypotheses(requests: list[dict[str, Any]], docs: dict[str, dict[str, Any]],
                         families: dict, analyzers: dict, raw_view: dict[str, str], nlp: Any
                         ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    cache: dict[tuple[str, str, bool], dict[str, Any]] = {}
    analysis_rows: dict[str, dict[str, Any]] = {}
    cache_stats = {"lookups": 0, "hits": 0, "misses": 0}
    results: dict[str, dict[str, Any]] = {}
    evidence = []
    start = time.perf_counter()
    for index, req in enumerate(requests, 1):
        doc = docs[req["target_document_id"]]
        result = evaluate_request(req, doc, families, analyzers, raw_view, nlp,
                                  cache, analysis_rows, cache_stats, True)
        results[req["request_id"]] = {"state": result["hypothesis_state"],
                                       "reason": result["reason"],
                                       "component_request_id": req["request_id"]}
        evidence.append({"request_id": req["request_id"], "hypothesis_state": result["hypothesis_state"],
                         "hypothesis_reason": result["reason"], "applicable_routes": result["applicable_routes"],
                         "analysis_refs": result["analysis_refs"], "punctuation_reading_aid": True,
                         "production_adoption_allowed": False})
        if index % 1000 == 0:
            print(f"component hypotheses {index}/{len(requests)}", flush=True)
    return results, evidence, list(analysis_rows.values()), {**cache_stats,
        "cache_entries": len(cache), "elapsed_seconds": time.perf_counter() - start}


def emit_method(output: Path, name: str, requests: list[dict[str, Any]], rows: list[dict[str, Any]],
                states: dict[str, str], request_records: list[dict[str, Any]]) -> dict[str, Any]:
    row_results, case_results = assemble_mode(rows, requests, states)
    for suffix, records in (("requests", request_records), ("rows", row_results), ("cases", case_results)):
        write_jsonl(output / f"{name}-{suffix}.jsonl", records)
    return {"requests": dict(Counter(item["state"] for item in request_records)),
            "rows": dict(Counter(item["state"] for item in row_results)),
            "cases": dict(Counter(item["disposition"] for item in case_results)),
            "raw_known_preserved": all(item["state"] == item["raw_baseline_state"]
                                        for item in request_records
                                        if item["raw_baseline_state"] != "unknown")}


def run(source: Path, original: Path, views_path: Path, output: Path,
        preflight_only: bool = False) -> None:
    if output.exists():
        raise FileExistsError(f"refusing existing output directory: {output}")
    input_paths = [source / name for name in ("manifest.json", "requests.jsonl", "documents.jsonl",
                    "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl")]
    original_paths = [original / "cases.jsonl", original / "products.jsonl"]
    all_input_paths = input_paths + original_paths + [views_path]
    input_pins = {path.resolve().as_posix(): sha(path) for path in all_input_paths}
    code_paths = [HERE / name for name in PINNED_CODE]
    code_pins = {path.name: sha(path) for path in code_paths}
    timings: dict[str, float] = {}
    rss: dict[str, int] = {"start": peak_rss_bytes()}
    total_start = time.perf_counter()

    phase = time.perf_counter()
    manifest, requests, documents, rows, cases, tasks, original_cases, original_products = load_population(source, original)
    view_payload = read_json(views_path)
    audit_path = validate_price_free_views(view_payload, views_path)
    input_pins[audit_path.resolve().as_posix()] = sha(audit_path)
    timings["load"] = time.perf_counter() - phase
    rss["after_load"] = peak_rss_bytes()

    phase = time.perf_counter()
    docs, families_by_dossier = validate_population(manifest, requests, documents, rows, cases, tasks,
        original_cases, original_products, view_payload)
    timings["validation"] = time.perf_counter() - phase
    rss["after_validation"] = peak_rss_bytes()
    if preflight_only:
        print(json.dumps({"preflight": "passed", "counts": {"requests": len(requests),
            "documents": len(documents), "products": len(original_products), "rows": len(rows),
            "cases": len(cases), "tasks": len(tasks)}, "labels_read": False, "grades_read": False,
            "traces_read": False, "predictions_run": False, "output_created": False}, ensure_ascii=False))
        return

    raw_view = view_payload["views"]["raw"]
    # Stage 1: the original selected-value lookup policy on raw, price-free text.
    phase = time.perf_counter()
    raw_docs, raw_families = raw_catalogs(requests, documents)
    raw_states: dict[str, str] = {}
    raw_reasons: dict[str, str] = {}
    raw_records = []
    for index, req in enumerate(requests, 1):
        state, reason = selected_state(req, raw_docs, raw_families, raw_view)
        raw_states[req["request_id"]] = state
        raw_reasons[req["request_id"]] = reason
        raw_records.append({"request_id": req["request_id"], "state": state, "reason": reason,
            "raw_selected_value": req["selected_value"],
            "view_selected_value": raw_view.get(req.get("selected_value"))
                if isinstance(req.get("selected_value"), str) else None,
            "raw_baseline_state": state, "production_adoption_allowed": False})
        if index % 1000 == 0:
            print(f"raw baseline {index}/{len(requests)}", flush=True)
    timings["baseline_raw"] = time.perf_counter() - phase
    rss["after_baseline_raw"] = peak_rss_bytes()

    # Stage 2: unchanged component policy, punctuation-aware hypothesis only.
    phase = time.perf_counter()
    nlp, component_families, analyzers, component_catalogs, collisions, _ = build_component_context(
        requests, docs, raw_view)
    component, component_records, component_analyses, component_stats = component_hypotheses(
        requests, docs, component_families, analyzers, raw_view, nlp)
    timings["components"] = time.perf_counter() - phase
    rss["after_components"] = peak_rss_bytes()

    # Stage 3: unchanged raw-view mutual-unique native-domain graph.
    phase = time.perf_counter()
    dossiers = sorted({document["dossier_id"] for document in documents})
    graph_records = []
    graphs: dict[str, dict[str, Any]] = {}
    for dossier in dossiers:
        graph = build_product_graph(dossier, families_by_dossier[dossier], raw_view, {}, "raw")
        graph["policy_axis_maps"] = {"mutual_unique": apply_policy_edges(graph, "mutual_unique")}
        graphs[dossier] = graph
        graph_records.append(graph)
    graph_states: dict[str, dict[str, Any]] = {}
    for req in requests:
        dossier = docs[req["target_document_id"]]["dossier_id"]
        graph_states[req["request_id"]] = graph_state(req, dossier, graphs[dossier], "mutual_unique",
            "raw", families_by_dossier[dossier], raw_view, {})
    timings["axis_domain"] = time.perf_counter() - phase
    rss["after_axis_domain"] = peak_rss_bytes()

    # Known raw states are immutable in both overlays. Unknown-to-known is
    # permitted only where the unchanged hypotheses agree.
    component_states: dict[str, str] = {}
    combined_states: dict[str, str] = {}
    component_request_records = []
    combined_request_records = []
    observations = []
    for req in requests:
        rid = req["request_id"]
        baseline = raw_states[rid]
        comp = component[rid]
        graph = graph_states[rid]
        comp_state, comp_reason = combine_hypotheses(baseline,
            {"state": "unknown", "reason": "no_axis_domain_hypothesis"}, comp)
        combined_state, combined_reason = combine_hypotheses(baseline, graph, comp)
        component_states[rid] = comp_state
        combined_states[rid] = combined_state
        component_request_records.append({"request_id": rid, "state": comp_state,
            "reason": comp_reason, "raw_baseline_state": baseline,
            "hypothesis_state": comp["state"], "hypothesis_reason": comp["reason"],
            "punctuation_reading_aid": True, "production_adoption_allowed": False})
        combined_request_records.append({"request_id": rid, "state": combined_state,
            "reason": combined_reason, "raw_baseline_state": baseline,
            "component_hypothesis_state": comp["state"], "component_hypothesis_reason": comp["reason"],
            "axis_domain_hypothesis_state": graph["state"], "axis_domain_hypothesis_reason": graph["reason"],
            "graph_id": graph["graph_id"], "axis_policy": "mutual_unique", "axis_view": "raw",
            "production_adoption_allowed": False})
        observations.append({"request_id": rid, "dossier_id": docs[req["target_document_id"]]["dossier_id"],
            "direction": req["direction"], "axis_name": req["axis_name"],
            "option_values": req["option_values"], "selected_value": req["selected_value"],
            "target_document_id": req["target_document_id"],
            "target_selected_axes": req["target_selected_axes"],
            "raw_baseline_state": baseline, "raw_baseline_reason": raw_reasons[rid],
            "component_hypothesis": comp, "component_only_state": comp_state,
            "axis_domain_hypothesis": graph, "combined_state": combined_state,
            "all_original_conditions_retained": True})
    timings["hypothesis_assembly"] = time.perf_counter() - phase
    rss["after_hypothesis_assembly"] = peak_rss_bytes()

    # All expensive validation and scoring is complete before creating the
    # immutable output directory. Failures during writing leave partial history.
    output.mkdir(parents=True, exist_ok=False)
    phase = time.perf_counter()
    write_jsonl(output / "component-catalogs.jsonl", component_catalogs)
    write_jsonl(output / "component-analyses.jsonl", component_analyses)
    write_jsonl(output / "component-evidence.jsonl", component_records)
    native_catalog_records = []
    for dossier, sides in sorted(families_by_dossier.items()):
        for side, families in sorted(sides.items()):
            for axis_name, family in sorted(families.items(), key=lambda pair: repr(pair[0])):
                native_catalog_records.append({"dossier_id": dossier, "side": side,
                    "axis_name": axis_name, "request_option_values": family.get("raw_values"),
                    "native_catalog_values": family.get("native_catalog_values"),
                    "request_vectors": family.get("vectors"), "request_ids": family.get("request_ids"),
                    "valid": family.get("valid"), "invalid_reason": family.get("invalid_reason"),
                    "all_native_axes_retained": True, "production_adoption_allowed": False})
    write_jsonl(output / "native-catalogs.jsonl", native_catalog_records)
    write_jsonl(output / "graph-evidence.jsonl", graph_records)
    write_jsonl(output / "request-observations.jsonl", observations)
    counts = {
        "baseline_raw": emit_method(output, "baseline_raw", requests, rows, raw_states, raw_records),
        "previous_components": emit_method(output, "previous_components", requests, rows,
                                            component_states, component_request_records),
        "mutual_unique_raw_components": emit_method(output, "mutual_unique_raw_components", requests,
                                                     rows, combined_states, combined_request_records),
    }
    timings["write_outputs"] = time.perf_counter() - phase
    rss["after_outputs"] = peak_rss_bytes()

    # Pin-check everything again before writing the freeze manifest.
    if any(sha(Path(path)) != digest for path, digest in input_pins.items()):
        raise ValueError("an input changed during volume run")
    if any(sha(HERE / name) != digest for name, digest in code_pins.items()):
        raise ValueError("a pinned policy/code module changed during volume run")
    output_files = {path.name: {"bytes": path.stat().st_size, "sha256": sha(path)}
                    for path in sorted(output.iterdir()) if path.is_file()}
    timings["total"] = time.perf_counter() - total_start
    rss["final"] = peak_rss_bytes()
    native_counts = {side: sum(1 for product_families in families_by_dossier.values()
                               for family in product_families.get(side, {}).values()
                               if family.get("valid"))
                     for side in ("au", "rakuten")}
    freeze = {"schema": "sku-library-volume-output-freeze-v1", "source_path": str(source.resolve()),
        "original_path": str(original.resolve()), "views_path": str(views_path.resolve()),
        "input_pins": input_pins, "code_pins": code_pins,
        "output_files": output_files, "output_pins": {name: data["sha256"] for name, data in output_files.items()},
        "counts": {"requests": len(requests), "documents": len(documents),
                   "products": len(original_products), "rows": len(rows), "cases": len(cases),
                   "tasks": len(tasks), "native_families_by_side": native_counts,
                   "valid_component_families": sum(bool(fam["valid"]) for fam in component_families.values()),
                   "component_catalog_count": len(component_catalogs), "component_analysis_count": len(component_analyses),
                   "component_cache": component_stats,
                   "graphs": len(graph_records), "graph_status_by_view": dict(Counter(g["status"] for g in graph_records)),
                   "methods": counts},
        "phase_timings_seconds": timings, "peak_rss_bytes_by_phase": rss,
        "native_library_versions": {"spacy": spacy.__version__,
            "networkx": importlib.metadata.version("networkx"),
            "quantulum3": importlib.metadata.version("quantulum3"),
            "SudachiPy": importlib.metadata.version("SudachiPy"),
            "jaconv": importlib.metadata.version("jaconv")},
        "population_manifest_schema": manifest.get("schema_version"),
        "source_split": manifest.get("split", "unknown"),
        "historical_source_status": "historical_population; independent_test_claim=false",
        "independent_test_claim": False, "labels_read": False, "grades_read": False,
        "previous_model_outputs_read": False, "traces_read": False,
        "model_calls": 0, "models_loaded": [], "custom_sku_grammar_used": False,
        "custom_alias_dictionary_used": False, "new_decision_thresholds_used": False,
        "all_native_axes_rows_conditions_retained": True,
        "raw_baseline_known_states_immutable": True,
        "component_mode": "existing evaluate_request(punctuation=True)",
        "axis_mode": "existing build_product_graph(mode=raw) + mutual_unique",
        "component_overlay_disagreement_holds_unknown": True,
        "scope_status": "unproved", "semantic_correctness_proved": False,
        "citation_semantics_supported": False, "production_adoption_allowed": False}
    write_json(output / "RUN_FREEZE.json", freeze)
    print(json.dumps({"output": str(output), "counts": freeze["counts"],
                      "phase_timings_seconds": timings, "peak_rss_bytes_by_phase": rss}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="prepared population inputs directory")
    parser.add_argument("--original", required=True, type=Path, help="directory containing cases.jsonl/products.jsonl")
    parser.add_argument("--views", required=True, type=Path, help="frozen price-free views JSON")
    parser.add_argument("--output", required=True, type=Path, help="new immutable run directory")
    parser.add_argument("--preflight-only", action="store_true",
                        help="validate all inputs/bindings/catalogs without computing predictions or writing output")
    args = parser.parse_args()
    run(args.source, args.original, args.views, args.output, preflight_only=args.preflight_only)


if __name__ == "__main__":
    main()
