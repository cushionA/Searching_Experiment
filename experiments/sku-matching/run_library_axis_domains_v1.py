#!/usr/bin/env python3
"""Label-blind fixed-view graph alignment over complete native option domains."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

import spacy

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from library_axis_domain_alignment_v1 import MODES, POLICIES, apply_policy_edges, build_product_graph
from replay_dev_nli_policies_v1 import assemble

ROOT = Path(__file__).resolve().parents[2]
POP = ROOT / ".lab-output/sku-classical-parser-20261015-v1"
SOURCE = POP / "population-inputs"
ORIGINAL = POP / "source-dev/population/run/inputs"
BASELINE = ROOT / ".lab-output/sku-installed-library-matching-20261015-v1/price-free/selection-v1"
VIEWS = ROOT / ".lab-output/sku-installed-library-matching-20261015-v1/views-price-free.json"
COMPONENT = ROOT / ".lab-output/sku-library-components-20261015-v1/run-v3"
EXPECTED = {"requests": 30169, "rows": 32325, "cases": 1370, "tasks": 132136,
            "au_documents": 107, "rakuten_documents": 107, "products": 107}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            result.append(item)
    return result


def load_population_inputs(source_dir: Path, original_dir: Path | None = None) -> tuple[dict[str, Any], list, list, list, list, list]:
    """Load and pin-check the manifest plus the five population JSONL files."""
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    filenames = ("requests.jsonl", "documents.jsonl", "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl")
    loaded = [read_jsonl(source_dir / name) for name in filenames]
    requests, documents, rows, cases, tasks = loaded
    counts = {"unique_request_count": len(requests), "document_count": len(documents),
              "row_plan_count": len(rows), "case_count": len(cases), "task_binding_count": len(tasks)}
    if any(manifest.get(key) != value for key, value in counts.items()):
        raise ValueError("population manifest counts differ from source inputs")
    for filename, digest in manifest.get("output_sha256", {}).items():
        path = source_dir / filename
        if not path.is_file() or sha(path) != digest:
            raise ValueError("population manifest source-file pin mismatch: " + filename)
    if original_dir is not None:
        for filename, digest in manifest.get("source_inputs", {}).items():
            path = original_dir / filename
            if not path.is_file() or sha(path) != digest:
                raise ValueError("population manifest original-input pin mismatch: " + filename)
    return manifest, requests, documents, rows, cases, tasks


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def input_family_side(direction: str) -> str:
    if direction == "au_to_rakuten":
        return "au"
    if direction == "rakuten_to_au":
        return "rakuten"
    raise ValueError(f"unknown direction {direction!r}")


def collect_families(requests: list[dict[str, Any]], docs: dict[str, dict[str, Any]]) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for req in requests:
        doc = docs[req["target_document_id"]]
        dossier, side, axis_name = doc["dossier_id"], input_family_side(req["direction"]), req.get("axis_name")
        if not isinstance(dossier, str) or not dossier or not isinstance(axis_name, str) or not axis_name:
            raise ValueError("request has an invalid product or axis identifier")
        key = (dossier, side, axis_name)
        item = grouped.setdefault(key, {"vectors": [], "statuses": [], "request_ids": []})
        vector = req.get("option_values")
        vector_entry = next((entry for entry in item["vectors"] if entry["option_values"] == vector), None)
        if vector_entry is None:
            vector_entry = {"option_values": vector, "request_ids": []}
            item["vectors"].append(vector_entry)
        vector_entry["request_ids"].append(req.get("request_id"))
        item["statuses"].append(req.get("source_family_status"))
        item["request_ids"].append(req["request_id"])

    result: dict[str, dict[str, dict[str, dict[str, Any]]]] = defaultdict(lambda: {"au": {}, "rakuten": {}})
    for (dossier, side, axis_name), item in grouped.items():
        vector_items = item["vectors"]
        reason = None
        if any(status != "complete" for status in item["statuses"]):
            reason = "source_family_status_not_complete"
        elif len(vector_items) != 1:
            reason = "inconsistent_native_vectors_for_family"
        raw_vector = vector_items[0]["option_values"] if len(vector_items) == 1 else None
        values = list(raw_vector) if isinstance(raw_vector, list) else []
        if not reason and (not values or any(not isinstance(value, str) or not value for value in values)):
            reason = "empty_or_nonstring_native_option"
        if not reason and len(values) != len(set(values)):
            reason = "duplicate_raw_native_option"
        vectors = [{"option_values": entry["option_values"], "request_ids": entry["request_ids"]}
                   for entry in sorted(item["vectors"], key=lambda entry: repr(entry["option_values"]))]
        result[dossier][side][axis_name] = {"axis_name": axis_name, "raw_values": values,
            "request_ids": item["request_ids"], "vectors": vectors,
            "valid": reason is None, "invalid_reason": reason}
    return dict(result)


def validate_native_catalog_coverage(families_by_dossier: dict[str, Any], products: list[dict[str, Any]]) -> None:
    """Require request-derived graph nodes to cover each product's native axes.

    Native catalogs come from the immutable source product records: AU SKU rows
    and Rakuten selector families. A missing or mismatched request family is
    retained as an invalid graph node, so a competitor axis cannot disappear
    and make another edge appear forced.
    """
    for product in products:
        dossier = product["dossier_id"]
        native: dict[str, dict[str, list[str]]] = {"au": {}, "rakuten": {}}
        native_errors: dict[str, dict[str, str]] = {"au": {}, "rakuten": {}}
        for row in product.get("au_rows", []):
            for axis in row.get("axes", []):
                name, value = axis.get("axis_name"), axis.get("value")
                if isinstance(name, str) and name and isinstance(value, str) and value:
                    values = native["au"].setdefault(name, [])
                    if value not in values:
                        values.append(value)
                else:
                    sentinel = f"__invalid_native_axis__:{len(native_errors['au'])}"
                    native[sentinel_side := "au"][sentinel] = []
                    native_errors[sentinel_side][sentinel] = "invalid_native_axis_or_option"
        for index, family in enumerate(product.get("rakuten_product", {}).get("selector_families", [])):
            name, values = family.get("label"), family.get("values")
            if not isinstance(name, str) or not name or not isinstance(values, list) or not values:
                name = f"__invalid_native_axis__:{index}"
                native["rakuten"][name] = [value for value in values if isinstance(value, str)] if isinstance(values, list) else []
                native_errors["rakuten"][name] = "invalid_native_selector_family"
                continue
            if name in native["rakuten"]:
                native_errors["rakuten"][name] = "duplicate_native_axis_identifier"
                native["rakuten"][name].extend(value for value in values
                                                if isinstance(value, str) and value not in native["rakuten"][name])
            else:
                native["rakuten"][name] = [value for value in values if isinstance(value, str)]
            if any(not isinstance(value, str) or not value for value in values):
                native_errors["rakuten"][name] = "invalid_native_option"
            valid_values = [value for value in values if isinstance(value, str)]
            if len(valid_values) != len(set(valid_values)):
                native_errors["rakuten"][name] = "duplicate_native_option"
        dossier_families = families_by_dossier.setdefault(dossier, {"au": {}, "rakuten": {}})
        for side in ("au", "rakuten"):
            requested = dossier_families[side]
            for name in sorted(set(native[side]) | set(requested)):
                actual_values = native[side].get(name)
                family = requested.get(name)
                if family is None:
                    values = actual_values or []
                    requested[name] = {"axis_name": name, "raw_values": values,
                        "request_ids": [], "vectors": [], "valid": False,
                        "invalid_reason": native_errors[side].get(name, "native_axis_missing_from_request_catalog")}
                elif actual_values is None:
                    family["valid"] = False
                    family["invalid_reason"] = "request_axis_missing_from_native_catalog"
                elif (not isinstance(family.get("raw_values"), list)
                      or any(not isinstance(value, str) for value in family["raw_values"])):
                    family["valid"] = False
                    family["invalid_reason"] = "invalid_request_native_option_vector"
                elif set(family["raw_values"]) != set(actual_values):
                    family["valid"] = False
                    family["invalid_reason"] = "request_option_domain_differs_from_native_catalog"
                family = requested[name]
                if name in native_errors[side]:
                    family["valid"] = False
                    family["invalid_reason"] = native_errors[side][name]
                family["native_catalog_values"] = actual_values


def validate_document_catalog_hashes(documents: list[dict[str, Any]], products: list[dict[str, Any]]) -> None:
    """Bind fixed source document identities to the native product catalogs."""
    product_by_dossier = {product["dossier_id"]: product for product in products}
    if len(product_by_dossier) != len(products):
        raise ValueError("duplicate dossier in native product catalog")
    for document in documents:
        product = product_by_dossier.get(document.get("dossier_id"))
        if product is None:
            raise ValueError("source document has no native product catalog")
        side = document.get("side")
        source_product = product.get("au_product" if side == "au" else "rakuten_product")
        if side not in {"au", "rakuten"} or not isinstance(source_product, dict):
            raise ValueError("source document has invalid native product side")
        if document.get("raw_sha256") != source_product.get("sha256"):
            raise ValueError("document raw source SHA differs from native product catalog")


def validate_source_and_baseline(requests: list[dict[str, Any]], documents: list[dict[str, Any]],
                                 rows: list[dict[str, Any]], cases: list[dict[str, Any]],
                                 tasks: list[dict[str, Any]], baseline_requests: list[dict[str, Any]],
                                 baseline_rows: list[dict[str, Any]], baseline_cases: list[dict[str, Any]],
                                 baseline_freeze: dict[str, Any], view_payload: dict[str, Any],
                                 source_pins: dict[str, str]) -> tuple[dict, dict, dict]:
    if (len(requests), len(rows), len(cases), len(tasks)) != (
            EXPECTED["requests"], EXPECTED["rows"], EXPECTED["cases"], EXPECTED["tasks"]):
        raise ValueError("original population request/row/case/task counts differ")
    if [r["request_id"] for r in requests] != [r["request_id"] for r in baseline_requests]:
        raise ValueError("baseline request order differs from source")
    if (len(baseline_rows), len(baseline_cases)) != (len(rows), len(cases)):
        raise ValueError("baseline row/case coverage differs")
    docs = {d["document_id"]: d for d in documents}
    if len(docs) != len(documents) or len(documents) != EXPECTED["au_documents"] + EXPECTED["rakuten_documents"]:
        raise ValueError("document identity/coverage mismatch")
    side_counts = Counter(d["side"] for d in documents)
    if side_counts != {"au": EXPECTED["au_documents"], "rakuten": EXPECTED["rakuten_documents"]}:
        raise ValueError("document side counts differ")
    required_baseline_inputs = {"requests.jsonl", "documents.jsonl", "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl"}
    if not required_baseline_inputs.issubset(baseline_freeze["input_pins"]):
        raise ValueError("baseline freeze lacks required source pins")
    for name in ("requests.jsonl", "documents.jsonl", "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl"):
        actual = source_pins[str((SOURCE / name).relative_to(ROOT).as_posix())]
        if baseline_freeze["input_pins"][name] != actual:
            raise ValueError("baseline source pin mismatch: " + name)
    if baseline_freeze.get("view_sha256") != sha(VIEWS):
        raise ValueError("baseline price-free views pin mismatch")
    for filename, digest in baseline_freeze["output_pins"].items():
        path = BASELINE / filename
        if not path.is_file() or sha(path) != digest:
            raise ValueError("baseline output pin mismatch: " + filename)

    raw_view = view_payload["views"]["raw"]
    nlp = spacy.blank("ja")
    norm_view = view_payload["views"]["spacy_norm"]
    expected_values = {v for request in requests for v in request["option_values"]} | {
        axis["value"] for request in requests for axis in request["target_selected_axes"]}
    if not expected_values.issubset(raw_view) or not expected_values.issubset(norm_view):
        raise ValueError("fixed view does not cover every input value")

    request_by_id = {r["request_id"]: r for r in requests}
    case_by_id = {c["case_id"]: c for c in cases}
    task_by_id = {}
    if len(request_by_id) != len(requests) or len(case_by_id) != len(cases):
        raise ValueError("duplicate request/case ID")
    for task in tasks:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in task_by_id:
            raise ValueError("missing or duplicate task ID")
        task_by_id[task_id] = task
    used_tasks = []
    row_keys_by_case: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        cid, row_key = row.get("case_id"), row.get("au_row_key")
        if cid not in case_by_id or row_key in row_keys_by_case[cid]:
            raise ValueError("foreign case or duplicate row key")
        if row["dossier_id"] != case_by_id[cid]["dossier_id"]:
            raise ValueError("row/case dossier mismatch")
        row_keys_by_case[cid].append(row_key)
        for binding in row["bindings"]:
            task = task_by_id.get(binding.get("task_id"))
            req = request_by_id.get(binding.get("request_id"))
            if task is None or req is None:
                raise ValueError("foreign task or request binding")
            if (task.get("case_id"), task.get("au_row_key"), task.get("request_id"), task.get("direction")) != (
                    cid, row_key, binding["request_id"], req["direction"]):
                raise ValueError("task case/row/request/direction envelope mismatch")
            if task.get("axis_key") != binding.get("axis_key"):
                raise ValueError("task/binding axis key mismatch")
            ref = task.get("raw_condition_ref", {})
            if (ref.get("axis_name"), ref.get("value")) != (req.get("axis_name"), req.get("selected_value")):
                raise ValueError("task raw condition/request mismatch")
            used_tasks.append(task["task_id"])
    if len(used_tasks) != EXPECTED["tasks"] or len(set(used_tasks)) != EXPECTED["tasks"] or set(used_tasks) != set(task_by_id):
        raise ValueError("task IDs do not bind one-to-one to original row conditions")
    for case in cases:
        if row_keys_by_case[case["case_id"]] != case["row_keys"]:
            raise ValueError("case row order/coverage differs")

    base_states = {}
    base_reasons = {}
    for original, baseline in zip(requests, baseline_requests):
        if baseline.get("request_id") != original["request_id"]:
            raise ValueError("baseline request IDs differ")
        if (baseline.get("raw_selected_value"), baseline.get("view_selected_value")) != (
                original["selected_value"], raw_view[original["selected_value"]]):
            raise ValueError("baseline request content differs from source/view")
        state = baseline.get("state")
        if state not in {"unknown", "support", "conflict"}:
            raise ValueError("invalid raw baseline state")
        base_states[original["request_id"]] = state
        base_reasons[original["request_id"]] = baseline.get("reason")
    expected_rows, expected_cases = assemble(rows, requests, base_states)
    raw_case_rows: dict[str, list[str]] = defaultdict(list)
    for actual, expected in zip(baseline_rows, expected_rows):
        if (actual.get("case_id"), actual.get("au_row_key"), actual.get("state"), actual.get("condition_count")) != (
                expected["case_id"], expected["au_row_key"], expected["state"], expected["condition_count"]):
            raise ValueError("baseline rows do not reassemble from immutable request states")
        raw_case_rows[actual["case_id"]].append(actual["state"])
    for actual, expected in zip(baseline_cases, expected_cases):
        disposition = ("accept" if expected["decision"] == "accept" else
                       "reject" if all(s == "conflict" for s in raw_case_rows[expected["case_id"]]) else "review")
        if (actual.get("case_id"), actual.get("decision"), actual.get("au_row_key"), actual.get("disposition")) != (
                expected["case_id"], expected["decision"], expected["au_row_key"], disposition):
            raise ValueError("baseline cases do not reassemble from immutable request states")
    return docs, base_states, base_reasons


def validate_component_hypotheses(requests: list[dict[str, Any]], base_states: dict[str, str],
                                  component_rows: list[dict[str, Any]], component_freeze: dict[str, Any],
                                  source_pins: dict[str, str]) -> dict[str, dict[str, Any]]:
    expected_code = component_freeze["code_pins"]
    component_script = HERE / "run_library_components_v1.py"
    component_adapter = HERE / "library_local_phrase_components_v1.py"
    for name, path in ((component_script.name, component_script), (component_adapter.name, component_adapter)):
        if sha(path) != expected_code[name]:
            raise ValueError("previous component code pin mismatch: " + name)
    component_paths = [COMPONENT / "native_punctuation-requests.jsonl", COMPONENT / "OUTPUT_FREEZE.json"]
    if (len(component_rows) != len(requests)
            or component_freeze.get("request_count") != len(requests)
            or component_freeze.get("condition_binding_count") != EXPECTED["tasks"]):
        raise ValueError("previous component output coverage mismatch")
    for name, digest in component_freeze["output_pins"].items():
        path = COMPONENT / name
        if not path.is_file() or sha(path) != digest:
            raise ValueError("previous component output pin mismatch: " + name)
    if component_freeze["input_pins"].get(str((SOURCE / "requests.jsonl").relative_to(ROOT).as_posix())) != source_pins[str((SOURCE / "requests.jsonl").relative_to(ROOT).as_posix())]:
        raise ValueError("previous component request input pin mismatch")
    for name, digest in component_freeze.get("input_pins", {}).items():
        if name in source_pins and source_pins[name] != digest:
            raise ValueError("previous component input pin mismatch: " + name)
    result = {}
    for req, row in zip(requests, component_rows):
        rid = req["request_id"]
        if row.get("request_id") != rid or row.get("raw_baseline_state") != base_states[rid]:
            raise ValueError("previous component row/request/raw-state identity mismatch")
        state = row.get("hypothesis_state")
        if state not in {"unknown", "support", "conflict"}:
            raise ValueError("invalid previous component hypothesis")
        if not row.get("punctuation_reading_aid"):
            raise ValueError("previous component variant is not the required punctuation diagnostic")
        result[rid] = {"state": state, "reason": row.get("hypothesis_reason"),
                       "component_request_id": rid}
    return result


def graph_state(req: dict[str, Any], dossier: str, graph_record: dict[str, Any], policy: str,
                mode: str, families: dict[str, dict[str, dict[str, Any]]], raw_view: dict[str, str],
                norm_view: dict[str, str]) -> dict[str, Any]:
    au_to_rakuten = apply_policy_edges(graph_record, policy)
    reason = None
    source_axis = req["axis_name"]
    target_names = set(families["rakuten" if req["direction"] == "au_to_rakuten" else "au"])
    target_side = "rakuten" if req["direction"] == "au_to_rakuten" else "au"
    source_side = input_family_side(req["direction"])
    mapping = (au_to_rakuten if req["direction"] == "au_to_rakuten" else
               {target: source for source, target in au_to_rakuten.items()})
    source_family = families[source_side].get(source_axis)
    if graph_record["status"] != "ready":
        return {"state": "unknown", "reason": "product_graph_held", "graph_id": graph_record["graph_id"],
                "mapping": mapping, "production_adoption_allowed": False}
    if source_family is None or not source_family["valid"]:
        reason = "source_native_axis_missing_or_invalid"
    elif req.get("source_family_status") != "complete":
        reason = "source_family_status_not_complete"
    elif source_family["raw_values"] != req["option_values"]:
        reason = "request_source_vector_differs_from_graph_family"
    elif req["selected_value"] not in source_family["raw_values"]:
        reason = "selected_source_value_not_unique_native_member"
    elif source_family["raw_values"].count(req["selected_value"]) != 1:
        reason = "selected_source_value_not_unique_native_member"
    else:
        target_axis = mapping.get(source_axis)
        if target_axis is None:
            reason = "source_axis_not_in_policy_mapping"
        elif source_axis in target_names and target_axis != source_axis:
            reason = "same_raw_axis_name_exists_but_mapping_points_elsewhere"
        else:
            target_family = families[target_side].get(target_axis)
            if target_family is None or not target_family["valid"]:
                reason = "mapped_target_family_missing_or_invalid"
            else:
                view = raw_view if mode == "raw" else norm_view
                source_key = view.get(req["selected_value"])
                source_keys = [view[value] for value in source_family["raw_values"]]
                target_keys = [view[value] for value in target_family["raw_values"]]
                if source_key is None or source_keys.count(source_key) != 1:
                    reason = "selected_source_view_key_not_unique"
                elif target_keys.count(source_key) != 1:
                    reason = "selected_source_key_not_unique_target_domain_member"
                else:
                    selected_axes = [axis for axis in req.get("target_selected_axes", [])
                                     if axis.get("axis_name") == target_axis]
                    if len(selected_axes) != 1:
                        reason = "mapped_target_selected_axis_missing_or_ambiguous"
                    else:
                        actual = selected_axes[0].get("value")
                        if target_family["raw_values"].count(actual) != 1:
                            reason = "actual_target_value_not_unique_native_member"
                        else:
                            actual_key = view.get(actual)
                            if actual_key is None or target_keys.count(actual_key) != 1:
                                reason = "actual_target_view_key_not_unique"
                            else:
                                return {"state": "support" if source_key == actual_key else "conflict",
                                    "reason": "selected_native_domain_keys_equal" if source_key == actual_key else "different_values_under_structural_axis_mapping",
                                    "graph_id": graph_record["graph_id"], "source_axis_name": source_axis,
                                    "target_axis_name": target_axis,
                                    "selected_source_raw_value": req["selected_value"],
                                    "actual_target_raw_value": actual,
                                    "selected_view_key": source_key, "actual_view_key": actual_key,
                                    "policy": policy, "view": mode,
                                    "production_adoption_allowed": False}
    return {"state": "unknown", "reason": reason or "not_eligible",
            "graph_id": graph_record["graph_id"], "mapping": mapping,
            "source_axis_name": source_axis, "policy": policy, "view": mode,
            "production_adoption_allowed": False}


def combine_hypotheses(raw_state: str, graph: dict[str, Any], component: dict[str, Any] | None) -> tuple[str, str]:
    if raw_state != "unknown":
        return raw_state, "immutable_raw_baseline_known_state"
    candidates = [x for x in (graph.get("state"), component.get("state") if component else "unknown")
                  if x in {"support", "conflict"}]
    if not candidates:
        return "unknown", "no_eligible_domain_or_component_hypothesis"
    if len(set(candidates)) > 1:
        return "unknown", "eligible_domain_and_component_hypotheses_disagree"
    names = []
    if graph.get("state") in {"support", "conflict"}:
        names.append("axis_domain")
    if component and component.get("state") in {"support", "conflict"}:
        names.append("prior_component")
    return candidates[0], "agreeing_eligible_hypotheses:" + "+".join(names)


def assemble_mode(rows: list[dict[str, Any]], requests: list[dict[str, Any]], states: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    row_results, decisions = assemble(rows, requests, states)
    row_states: dict[str, list[str]] = defaultdict(list)
    for row in row_results:
        row["production_adoption_allowed"] = False
        row_states[row["case_id"]].append(row["state"])
    for decision in decisions:
        decision["disposition"] = ("accept" if decision["decision"] == "accept" else
            "reject" if all(s == "conflict" for s in row_states[decision["case_id"]]) else "review")
        decision["production_adoption_allowed"] = False
        decision["axis_mapping_is_semantic_proof"] = False
    return row_results, decisions


def run(output: Path, preflight_only: bool = False) -> None:
    if output.exists():
        raise FileExistsError(output)
    manifest_path = SOURCE / "manifest.json"
    input_paths = [SOURCE / n for n in ("requests.jsonl", "documents.jsonl", "row-plans.jsonl",
                                        "case-plans.jsonl", "tasks.jsonl")]
    original_paths = [ORIGINAL / "cases.jsonl", ORIGINAL / "products.jsonl"]
    baseline_paths = [BASELINE / n for n in ("raw-requests.jsonl", "raw-rows.jsonl", "raw-cases.jsonl", "OUTPUT_FREEZE.json")]
    component_paths = [COMPONENT / "native_punctuation-requests.jsonl", COMPONENT / "OUTPUT_FREEZE.json"]
    paths = [manifest_path] + input_paths + original_paths + baseline_paths + component_paths + [VIEWS]
    input_pins = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    code_paths = [Path(__file__), HERE / "library_axis_domain_alignment_v1.py", HERE / "replay_dev_nli_policies_v1.py"]
    code_pins = {p.name: sha(p) for p in code_paths}
    manifest, requests, documents, rows, cases, tasks = load_population_inputs(SOURCE, ORIGINAL)
    original_cases, original_products = [read_jsonl(p) for p in original_paths]
    if manifest.get("product_count") != len(original_products) or manifest.get("case_count") != len(original_cases):
        raise ValueError("population manifest original case/product counts differ")
    baseline_requests, baseline_rows, baseline_cases = [read_jsonl(p) for p in baseline_paths[:3]]
    baseline_freeze = json.loads(baseline_paths[3].read_text(encoding="utf-8"))
    component_rows = read_jsonl(component_paths[0])
    component_freeze = json.loads(component_paths[1].read_text(encoding="utf-8"))
    view_payload = json.loads(VIEWS.read_text(encoding="utf-8"))
    raw_view, norm_view = view_payload["views"]["raw"], view_payload["views"]["spacy_norm"]
    if (len(original_cases), len(original_products)) != (EXPECTED["cases"], EXPECTED["products"]):
        raise ValueError("original source-dev cases/products coverage mismatch")
    if len({p["dossier_id"] for p in original_products}) != EXPECTED["products"]:
        raise ValueError("original product dossier IDs are not unique")
    docs, base_states, base_reasons = validate_source_and_baseline(
        requests, documents, rows, cases, tasks, baseline_requests, baseline_rows, baseline_cases,
        baseline_freeze, view_payload, input_pins)
    component = validate_component_hypotheses(requests, base_states, component_rows, component_freeze, input_pins)

    families_by_dossier = collect_families(requests, docs)
    dossier_ids = sorted({d["dossier_id"] for d in documents})
    if set(dossier_ids) != {p["dossier_id"] for p in original_products}:
        raise ValueError("document/product dossier coverage differs")
    graph_records: dict[tuple[str, str], dict[str, Any]] = {}
    validate_document_catalog_hashes(documents, original_products)
    validate_native_catalog_coverage(families_by_dossier, original_products)
    if preflight_only:
        print(json.dumps({"preflight": "passed", "requests": len(requests),
                          "documents": len(documents), "products": len(original_products),
                          "rows": len(rows), "cases": len(cases), "tasks": len(tasks),
                          "native_catalogs_validated": True,
                          "labels_read": False, "grades_read": False,
                          "traces_read": False, "output_created": False}, ensure_ascii=False))
        return
    graph_evidence: list[dict[str, Any]] = []
    graph_policies: dict[tuple[str, str, str], dict[str, str]] = {}
    started = time.perf_counter()
    for dossier in dossier_ids:
        families = families_by_dossier.get(dossier, {"au": {}, "rakuten": {}})
        for mode in MODES:
            record = build_product_graph(dossier, families, raw_view, norm_view, mode)
            record["policy_axis_maps"] = {policy: apply_policy_edges(record, policy) for policy in POLICIES}
            graph_records[dossier, mode] = record
            graph_evidence.append(record)
            for policy in POLICIES:
                graph_policies[dossier, mode, policy] = record["policy_axis_maps"][policy]

    observations: list[dict[str, Any]] = []
    outputs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    variants = [(f"{policy}_{'raw' if mode == 'raw' else 'spacy'}", policy, mode, False)
                for policy in POLICIES for mode in MODES]
    variants += [(name + "_components", policy, mode, True)
                 for name, policy, mode, _ in list(variants)]
    for index, request in enumerate(requests, 1):
        doc = docs[request["target_document_id"]]
        dossier = doc["dossier_id"]
        request_results = {}
        for name, policy, mode, with_component in variants:
            graph = graph_records[dossier, mode]
            hypothesis = graph_state(request, dossier, graph, policy,
                                     "spacy_norm" if mode == "spacy_norm" else "raw",
                                     families_by_dossier[dossier], raw_view, norm_view)
            raw_state = base_states[request["request_id"]]
            comp_hyp = component[request["request_id"]] if with_component else None
            if with_component:
                state, reason = combine_hypotheses(raw_state, hypothesis, comp_hyp)
            elif raw_state != "unknown":
                state, reason = raw_state, "immutable_raw_baseline_known_state"
            else:
                state, reason = hypothesis["state"], hypothesis["reason"]
            request_results[name] = {"request_id": request["request_id"], "state": state,
                "reason": reason, "raw_baseline_state": raw_state,
                "axis_domain_hypothesis": hypothesis["state"],
                "axis_domain_reason": hypothesis["reason"],
                "component_hypothesis": comp_hyp["state"] if comp_hyp else None,
                "component_reason": comp_hyp["reason"] if comp_hyp else None,
                "graph_id": graph["graph_id"], "policy": policy, "view": mode,
                "production_adoption_allowed": False,
                "axis_correspondence_is_semantic_proof": False}
            outputs[name].append(request_results[name])
        observations.append({"request_id": request["request_id"], "dossier_id": dossier,
            "direction": request["direction"], "source_axis_name": request["axis_name"],
            "source_family_status": request["source_family_status"],
            "source_option_values": request["option_values"], "selected_value": request["selected_value"],
            "target_document_id": request["target_document_id"],
            "target_selected_axes": request["target_selected_axes"],
            "raw_baseline_state": base_states[request["request_id"]],
            "raw_baseline_reason": base_reasons[request["request_id"]],
            "variants": request_results, "all_original_bindings_retained": True})
        if index % 1000 == 0:
            print(f"requests {index}/{len(requests)}", flush=True)

    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "graph-evidence.jsonl", graph_evidence)
    write_jsonl(output / "request-observations.jsonl", observations)
    counts = {}
    for name, _, _, _ in variants:
        request_rows = outputs[name]
        states = {r["request_id"]: r["state"] for r in request_rows}
        row_results, decisions = assemble_mode(rows, requests, states)
        for suffix, records in (("requests", request_rows), ("rows", row_results), ("cases", decisions)):
            write_jsonl(output / f"{name}-{suffix}.jsonl", records)
        counts[name] = {"requests": dict(Counter(r["state"] for r in request_rows)),
            "rows": dict(Counter(r["state"] for r in row_results)),
            "cases": dict(Counter(r["disposition"] for r in decisions)),
            "axis_domain_hypotheses": dict(Counter(r["axis_domain_hypothesis"] for r in request_rows)),
            "component_hypotheses": dict(Counter(r["component_hypothesis"] for r in request_rows
                                                  if r["component_hypothesis"] is not None)),
            "raw_known_preserved": all(r["state"] == r["raw_baseline_state"]
                                        for r in request_rows if r["raw_baseline_state"] != "unknown")}
    graph_counts = {mode: dict(Counter(g["status"] for g in graph_evidence if g["view"] == mode)) for mode in MODES}
    counts_payload = {"variants": counts, "graphs_by_view": graph_counts,
                      "graph_count": len(graph_evidence), "request_observation_count": len(observations)}
    output_files = {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
                    for p in sorted(output.iterdir()) if p.is_file()}
    if any(sha(path) != digest for path, digest in ((p, input_pins[p.relative_to(ROOT).as_posix()]) for p in paths)):
        raise ValueError("input changed during experiment")
    if any(sha(p) != code_pins[p.name] for p in code_paths):
        raise ValueError("experiment code changed during run")
    freeze = {"schema": "sku-library-axis-domains-output-freeze-v1",
        "source_path": str(SOURCE.resolve()), "input_pins": input_pins,
        "code_pins": code_pins,
        "previous_component_freeze_sha256": sha(COMPONENT / "OUTPUT_FREEZE.json"),
        "previous_component_code_pins": component_freeze["code_pins"],
        "baseline_freeze_sha256": sha(BASELINE / "OUTPUT_FREEZE.json"),
        "price_free_views_sha256": sha(VIEWS), "output_files": output_files,
        "output_pins": {name: data["sha256"] for name, data in output_files.items()},
        "counts": counts_payload, "elapsed_seconds": time.perf_counter() - started,
        "networkx_version": __import__("networkx").__version__, "spacy_version": spacy.__version__,
        "views": ["raw", "spacy_norm"], "axis_policies": list(POLICIES),
        "labels_read": False, "grades_read": False, "traces_read": False,
        "model_calls": 0, "models_loaded": [], "custom_sku_grammar_used": False,
        "hand_maintained_alias_dictionary_used": False, "human_registration_or_feedback_used": False,
        "fuzzy_similarity_threshold_used": False,
        "one_to_one_axis_mapping_assumption": True,
        "forced_maximum_edge_is_semantic_proof": False,
        "component_overlay_is_diagnostic_only": True,
        "all_original_requests_rows_cases_bindings_retained": True,
        "raw_baseline_known_states_unchanged": True,
        "scope_status": "unproved", "semantic_correctness_proved": False,
        "citation_semantics_supported": False, "production_adoption_allowed": False}
    write_json(output / "OUTPUT_FREEZE.json", freeze)
    print(json.dumps({"output": str(output), "elapsed_seconds": freeze["elapsed_seconds"],
                      "counts": counts_payload}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".lab-output/sku-library-axis-domains-20261015-v1/run-v1")
    parser.add_argument("--preflight-only", action="store_true",
                        help="validate all pinned inputs and bindings without creating output or hypotheses")
    args = parser.parse_args()
    run(args.output, preflight_only=args.preflight_only)
