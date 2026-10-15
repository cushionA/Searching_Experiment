#!/usr/bin/env python3
"""Label-blind native catalog phrase-component experiment.

This uses spaCy's native PhraseMatcher only to expose exact catalog phrases in
other native option values. The small tuple checks below require complete
native domains and observed 2x2 coverage; they add no string grammar, aliases,
category rules, or learned decision policy.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import itertools
import json
from pathlib import Path
import sys
import time
from typing import Any

import spacy

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from library_local_phrase_components_v1 import LocalPhraseCatalog
from replay_dev_nli_policies_v1 import assemble

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".lab-output/sku-classical-parser-20261015-v1/population-inputs"
BASELINE = ROOT / ".lab-output/sku-installed-library-matching-20261015-v1/price-free/selection-v1"
VIEWS = ROOT / ".lab-output/sku-installed-library-matching-20261015-v1/views-price-free.json"
EXPECTED = {"requests": 30169, "rows": 32325, "cases": 1370, "bindings": 132136}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected object")
            result.append(value)
    return result


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def source_side(direction: str) -> str:
    if direction == "au_to_rakuten":
        return "au"
    if direction == "rakuten_to_au":
        return "rakuten"
    raise ValueError(f"unknown direction: {direction!r}")


def view_diff(original: str, viewed: str) -> list[dict[str, Any]]:
    """Audit differences as separate original/view Unicode-codepoint spans."""
    result = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=original, b=viewed, autojunk=False).get_opcodes():
        if tag != "equal":
            result.append({"operation": tag,
                           "original_span": [i1, i2], "view_span": [j1, j2],
                           "original_text": original[i1:i2], "view_text": viewed[j1:j2]})
    return result


def normalized_key(nlp: Any, text: str) -> tuple[str, ...]:
    return tuple(token.norm_ for token in nlp.make_doc(text))


def make_catalog_index(requests: list[dict[str, Any]], docs: dict[str, dict[str, Any]],
                       raw_view: dict[str, str], nlp: Any) -> tuple[dict, dict, dict]:
    """Return full source-side family vectors, family status, and side lexicons."""
    collected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for req in requests:
        doc = docs[req["target_document_id"]]
        side = source_side(req["direction"])
        key = (doc["dossier_id"], side, req["axis_name"])
        entry = collected.setdefault(key, {"vectors": {}, "statuses": set(), "request_ids": []})
        vector = tuple(req["option_values"])
        entry["vectors"].setdefault(vector, []).append(req["request_id"])
        entry["statuses"].add(req.get("source_family_status"))
        entry["request_ids"].append(req["request_id"])

    families: dict[tuple[str, str, str], dict[str, Any]] = {}
    lexicons: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(dict)
    for key, entry in collected.items():
        dossier, side, axis = key
        vector_items = list(entry["vectors"].items())
        reason = None
        if entry["statuses"] != {"complete"}:
            reason = "source_family_status_not_complete"
        elif len(vector_items) != 1:
            reason = "multiple_native_vectors_for_family"
        else:
            values = list(vector_items[0][0])
            if not values or any(not isinstance(v, str) or not v for v in values):
                reason = "empty_or_nonstring_native_option"
            elif len(set(values)) != len(values):
                reason = "duplicate_native_option"
            elif any(v not in raw_view for v in values):
                reason = "missing_price_free_raw_view"
            elif len({raw_view[v] for v in values}) != len(values):
                reason = "price_free_view_collision_within_family"
            elif len({normalized_key(nlp, raw_view[v]) for v in values}) != len(values):
                reason = "native_norm_collision_within_family"
        vector = list(vector_items[0][0]) if len(vector_items) == 1 else []
        families[key] = {"dossier_id": dossier, "side": side, "axis_name": axis,
                         "raw_values": vector, "view_values": [raw_view[v] for v in vector],
                         "request_ids": entry["request_ids"], "valid": reason is None,
                         "invalid_reason": reason}
        if reason is None:
            lexicons[(dossier, side)][axis] = [raw_view[v] for v in vector]

    # A raw view or NORM key shared by distinct native axis/value identities is
    # unsafe to use as a component. Keep all catalogs, but mark implicated axes.
    axis_collisions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (dossier, side), axes in lexicons.items():
        raw_entries: dict[str, set[tuple[str, str]]] = defaultdict(set)
        norm_entries: dict[tuple[str, ...], set[tuple[str, str]]] = defaultdict(set)
        for axis, values in axes.items():
            for value in values:
                raw_entries[value].add((axis, value))
                norm_entries[normalized_key(nlp, value)].add((axis, value))
        for identities in raw_entries.values():
            if len(identities) > 1:
                for axis, _ in identities:
                    axis_collisions[dossier, side].add(axis)
        for identities in norm_entries.values():
            if len(identities) > 1:
                for axis, _ in identities:
                    axis_collisions[dossier, side].add(axis)
    for (dossier, side), axes in lexicons.items():
        for axis in axis_collisions[dossier, side]:
            families[dossier, side, axis]["valid"] = False
            families[dossier, side, axis]["invalid_reason"] = "native_or_price_free_norm_collision_across_catalog"
        for axis in axis_collisions[dossier, side]:
            axes.pop(axis, None)
    return families, dict(lexicons), {k: sorted(v) for k, v in axis_collisions.items()}


def tuple_domain(values: list[str], analyzer: LocalPhraseCatalog, raw_view: dict[str, str],
                 originals: dict[str, str], punctuation: bool,
                 cache: dict[tuple[str, str, bool], dict[str, Any]],
                 analysis_rows: dict[str, dict[str, Any]],
                 cache_stats: dict[str, int]) -> tuple[list[tuple[str, ...]] | None, list[str], list[str]]:
    """Strictly decompose every value into one unique atom per stable axis."""
    axis_order: tuple[str, ...] | None = None
    tuples: list[tuple[str, ...]] = []
    refs: list[str] = []
    reasons: list[str] = []
    for original in values:
        view_text = raw_view.get(original)
        if view_text is None:
            reasons.append("missing_price_free_raw_view")
            continue
        key = (analyzer.metadata["catalog_id"], view_text, punctuation)
        if key not in cache:
            cache_stats["misses"] += 1
            result = analyzer.analyze(view_text)
            analysis_id = hashlib.sha256((key[0] + "\0" + view_text + "\0" + str(punctuation)).encode()).hexdigest()
            selected = (result["unique_full_compound_ignoring_native_punctuation"] if punctuation
                        else result["unique_full_compound"])
            record = {"analysis_id": analysis_id, "catalog_id": analyzer.metadata["catalog_id"],
                      "original_raw_value": original, "analyzed_view_text": view_text,
                      "coordinate_space": "price_free_view_unicode_codepoints",
                      "original_to_view_changes": view_diff(original, view_text),
                      "punctuation_reading_aid": punctuation,
                      "unique_full_compound": selected,
                      "raw_tokens": result["raw_tokens"],
                      "ambiguity_reasons": result["ambiguity_reasons"],
                      "matches": result["matches"], "unmatched_substantive_spans": result["unmatched_substantive_spans"],
                      "ignored_native_punctuation_spans": result["ignored_native_punctuation_spans"],
                      "overlapping_matches": result["overlapping_matches"],
                      "normalized_catalog_collisions": result["normalized_catalog_collisions"],
                      "production_adoption_allowed": False}
            atoms: dict[str, set[str]] = defaultdict(set)
            for hit in result["matches"]:
                atoms[hit["axis_name"]].add(hit["raw_option_value"])
            record["decomposed_atoms"] = {a: sorted(v) for a, v in sorted(atoms.items())}
            cache[key] = {"record": record, "tuple": None}
            analysis_rows[analysis_id] = record
        else:
            cache_stats["hits"] += 1
        cache_stats["lookups"] += 1
        record = cache[key]["record"]
        refs.append(record["analysis_id"])
        if not record["unique_full_compound"]:
            reasons.extend(record["ambiguity_reasons"] or ["not_unique_full_compound"])
            continue
        atom_map = record["decomposed_atoms"]
        if any(len(v) != 1 for v in atom_map.values()):
            reasons.append("multiple_atom_values_on_same_axis")
            continue
        axes = tuple(sorted(atom_map))
        if len(axes) < 2:
            reasons.append("fewer_than_two_atoms")
            continue
        if axis_order is None:
            axis_order = axes
        elif axis_order != axes:
            reasons.append("compound_domain_axis_set_varies")
            continue
        tuples.append(tuple(atom_map[a][0] for a in axes))
    if reasons or len(tuples) != len(values) or len(set(tuples)) != len(tuples):
        if len(set(tuples)) != len(tuples):
            reasons.append("duplicate_component_tuple")
        return None, sorted(set(reasons)), refs
    return tuples, [], refs


def has_observed_2x2(tuples: list[tuple[str, ...]]) -> dict[str, Any] | None:
    """Require an observed 2x2 on two axes while every other atom is fixed."""
    if not tuples:
        return None
    width = len(tuples[0])
    axes = tuple(range(width))
    observed = set(tuples)
    for left, right in itertools.combinations(axes, 2):
        left_values = sorted({row[left] for row in tuples})
        right_values = sorted({row[right] for row in tuples})
        for fixed in itertools.product(*(sorted({row[i] for row in tuples}) for i in axes if i not in (left, right))):
            for lv in itertools.combinations(left_values, 2):
                for rv in itertools.combinations(right_values, 2):
                    expected = set()
                    for a, b in itertools.product(lv, rv):
                        row = [None] * width
                        row[left], row[right] = a, b
                        cursor = 0
                        for i in axes:
                            if i not in (left, right):
                                row[i] = fixed[cursor]
                                cursor += 1
                        expected.add(tuple(row))
                    if expected.issubset(observed):
                        fixed_indices = [i for i in axes if i not in (left, right)]
                        return {"varying_axis_indices": [left, right],
                                "other_atoms_fixed": {str(i): v for i, v in zip(fixed_indices, fixed)},
                                "observed_2x2": [list(row) for row in sorted(expected)]}
    return None


def family_domain(families: dict, dossier: str, side: str, axis: str) -> dict[str, Any] | None:
    value = families.get((dossier, side, axis))
    return value if value and value["valid"] else None


def resolve_view_atom(family: dict[str, Any], view_value: str,
                      raw_view: dict[str, str]) -> str | None:
    candidates = [raw for raw in family["raw_values"] if raw_view.get(raw) == view_value]
    return candidates[0] if len(candidates) == 1 else None


def norm_compare(nlp: Any, value: str, expected: str, family: dict[str, Any], raw_view: dict[str, str]) -> str:
    """Compare two atoms only when both are unambiguous members of a native family."""
    if value not in family["raw_values"] or expected not in family["raw_values"]:
        return "unknown"
    v = raw_view[value]
    e = raw_view[expected]
    keys = [normalized_key(nlp, raw_view[x]) for x in family["raw_values"]]
    if len(set(keys)) != len(keys):
        return "unknown"
    return "support" if normalized_key(nlp, v) == normalized_key(nlp, e) else "conflict"


def evaluate_request(req: dict[str, Any], doc: dict[str, Any], families: dict,
                     analyzers: dict, raw_view: dict[str, str], nlp: Any,
                     cache: dict, analysis_rows: dict, cache_stats: dict[str, int],
                     punctuation: bool) -> dict[str, Any]:
    dossier = doc["dossier_id"]
    src = source_side(req["direction"])
    dst = "rakuten" if src == "au" else "au"
    route_results = []
    route_refs = []

    # Route A: one source-axis native domain contains compound values. Decompose
    # the full source domain against the opposite side's complete atomic catalog.
    source_family = family_domain(families, dossier, src, req["axis_name"])
    target_axes = req.get("target_selected_axes", [])
    if source_family and req.get("source_family_status") == "complete":
        analyzer = analyzers.get((dossier, dst))
        if analyzer is not None and source_family["raw_values"] == req["option_values"]:
            tuples, why, refs = tuple_domain(source_family["raw_values"], analyzer, raw_view,
                                             raw_view, punctuation, cache, analysis_rows, cache_stats)
            route_refs.extend(refs)
            if tuples is not None:
                # Component axis names come from matched native catalog families.
                first = cache[(analyzer.metadata["catalog_id"], raw_view[source_family["raw_values"][0]], punctuation)]["record"]["decomposed_atoms"]
                atom_axes = tuple(sorted(first))
                repeat = has_observed_2x2(tuples)
                if repeat is None:
                    why = ["native_domain_lacks_observed_2x2"]
                else:
                    selected = req["selected_value"]
                    source_index = source_family["raw_values"].index(selected) if selected in source_family["raw_values"] else None
                    actual_by_axis: dict[str, list[str]] = defaultdict(list)
                    for axis in atom_axes:
                        target_family = family_domain(families, dossier, dst, axis)
                        if target_family is None:
                            why.append("opposite_axis_catalog_missing_or_ambiguous")
                            break
                        for entry in target_axes:
                            if entry.get("axis_name") == axis:
                                actual_by_axis[axis].append(entry.get("value"))
                    selected_names = [entry.get("axis_name") for entry in target_axes]
                    if (not why and source_index is not None
                            and len(selected_names) == len(set(selected_names))
                            and set(atom_axes).issubset(set(selected_names))
                            and all(len(actual_by_axis[a]) == 1 for a in atom_axes)):
                        expected_tuple = tuples[source_index]
                        comparisons = []
                        resolved_expected = []
                        for axis, expected_view in zip(atom_axes, expected_tuple):
                            target_family = family_domain(families, dossier, dst, axis)
                            expected = resolve_view_atom(target_family, expected_view, raw_view)
                            if expected is None:
                                comparisons.append("unknown")
                                continue
                            resolved_expected.append(expected)
                            comp = norm_compare(nlp, actual_by_axis[axis][0], expected,
                                                target_family, raw_view)
                            comparisons.append(comp)
                        if all(x == "support" for x in comparisons):
                            route_results.append({"route": "source_compound_to_target_atomic",
                                                  "state": "support", "component_axes": list(atom_axes),
                                                  "repeat_witness": repeat, "analysis_refs": refs})
                        elif "unknown" in comparisons:
                            why = ["component_value_not_unambiguous_native_member"]
                        else:
                            route_results.append({"route": "source_compound_to_target_atomic",
                                                  "state": "conflict", "component_axes": list(atom_axes),
                                                  "repeat_witness": repeat, "analysis_refs": refs})
                    elif not why:
                        why = ["target_selected_axes_missing_or_duplicate_component_axis_or_source_selection_missing"]
            else:
                why = why or ["source_compound_domain_not_full_unambiguous"]
        else:
            why = ["source_or_opposite_lexicon_missing_or_vector_mismatch"]
    else:
        why = ["source_native_family_missing_or_incomplete"]
    route_refs.extend([])

    # Route B: exactly one selected target-native family is compound; decompose
    # its entire native domain against source-side catalogs, then compare the
    # source request's named axis atom to selected_value.
    b_reason = ["no_single_target_compound_domain"]
    b_eligible_fields = []
    analyzer = analyzers.get((dossier, src))
    if analyzer is not None:
        for target_entry in target_axes:
            target_axis = target_entry.get("axis_name")
            target_family = family_domain(families, dossier, dst, target_axis)
            if target_family is None:
                continue
            tuples, why_b, refs = tuple_domain(target_family["raw_values"], analyzer,
                                               raw_view, raw_view, punctuation, cache, analysis_rows, cache_stats)
            route_refs.extend(refs)
            if tuples is None:
                continue
            first = cache[(analyzer.metadata["catalog_id"], raw_view[target_family["raw_values"][0]], punctuation)]["record"]["decomposed_atoms"]
            atom_axes = tuple(sorted(first))
            if req["axis_name"] not in atom_axes:
                continue
            repeat = has_observed_2x2(tuples)
            if repeat is None or target_entry.get("value") not in target_family["raw_values"]:
                continue
            if source_family is None or req["selected_value"] not in source_family["raw_values"]:
                continue
            idx = target_family["raw_values"].index(target_entry["value"])
            expected_view = tuples[idx][atom_axes.index(req["axis_name"])]
            expected = resolve_view_atom(family_domain(families, dossier, src, req["axis_name"]),
                                         expected_view, raw_view)
            if expected is None:
                continue
            comp = norm_compare(nlp, req["selected_value"], expected,
                                family_domain(families, dossier, src, req["axis_name"]), raw_view)
            if comp in ("support", "conflict"):
                b_eligible_fields.append({"route": "target_compound_to_source_atomic",
                    "state": comp, "target_axis_name": target_axis,
                    "component_axes": list(atom_axes), "repeat_witness": repeat,
                    "analysis_refs": refs})
    if len(b_eligible_fields) == 1:
        route_results.append(b_eligible_fields[0])
    elif len(b_eligible_fields) > 1:
        b_reason = ["multiple_target_compound_fields_include_request_axis"]
    elif not b_eligible_fields:
        b_reason = ["no_unique_repeat_supported_target_compound_field"]

    if len(b_eligible_fields) > 1:
        result_state, reason = "unknown", "multiple_target_compound_fields_include_request_axis"
    elif len(route_results) == 0:
        result_state, reason = "unknown", ";".join(sorted(set(why + b_reason)))
    elif len({r["state"] for r in route_results}) > 1:
        result_state, reason = "unknown", "applicable_routes_disagree"
    else:
        result_state = route_results[0]["state"]
        reason = "all_applicable_component_routes_agree"
    return {"hypothesis_state": result_state, "reason": reason,
            "applicable_routes": route_results, "analysis_refs": sorted(set(route_refs)),
            "punctuation_reading_aid": punctuation,
            "production_adoption_allowed": False}


def run(output: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    input_paths = [SOURCE / n for n in ("requests.jsonl", "documents.jsonl", "row-plans.jsonl",
                                        "case-plans.jsonl", "tasks.jsonl")]
    baseline_paths = [BASELINE / n for n in ("raw-requests.jsonl", "raw-rows.jsonl", "raw-cases.jsonl",
                                               "OUTPUT_FREEZE.json")]
    paths = input_paths + baseline_paths + [VIEWS]
    input_pins = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    code_pins = {p.name: sha(p) for p in (Path(__file__), HERE / "library_local_phrase_components_v1.py",
                                           HERE / "replay_dev_nli_policies_v1.py")}
    requests, documents, rows, cases, tasks = [read_jsonl(p) for p in input_paths]
    base_requests, base_rows, base_cases = [read_jsonl(p) for p in baseline_paths[:3]]
    base_freeze = json.loads(baseline_paths[3].read_text(encoding="utf-8"))
    view_payload = json.loads(VIEWS.read_text(encoding="utf-8"))
    raw_view = view_payload["views"]["raw"]
    if (len(requests), len(rows), len(cases), sum(len(r["bindings"]) for r in rows)) != (
            EXPECTED["requests"], EXPECTED["rows"], EXPECTED["cases"], EXPECTED["bindings"]):
        raise ValueError("full source coverage mismatch")
    if len(tasks) != EXPECTED["bindings"]:
        raise ValueError("full task coverage mismatch")
    if len(base_requests) != len(requests) or len(base_rows) != len(rows) or len(base_cases) != len(cases):
        raise ValueError("baseline coverage mismatch")
    if [r["request_id"] for r in requests] != [r["request_id"] for r in base_requests]:
        raise ValueError("baseline request order differs")
    base_state = {r["request_id"]: r["state"] for r in base_requests}
    base_reason = {r["request_id"]: r["reason"] for r in base_requests}
    if set(base_state.values()) - {"unknown", "support", "conflict"}:
        raise ValueError("invalid baseline request state")
    docs = {d["document_id"]: d for d in documents}
    if len(docs) != len(documents):
        raise ValueError("duplicate document ID")
    expected_views = {v for r in requests for v in r["option_values"]} | {
        a["value"] for r in requests for a in r["target_selected_axes"]}
    if not expected_views.issubset(raw_view):
        raise ValueError("price-free raw view lacks input values")

    # Require exact source pins from the baseline, including the task and row
    # envelopes used for the unchanged all-condition replay.
    source_names = ("requests.jsonl", "documents.jsonl", "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl")
    for name, path in zip(source_names, input_paths):
        if base_freeze["input_pins"].get(name) != sha(path):
            raise ValueError("baseline source input pin differs: " + name)
    if base_freeze.get("view_sha256") != sha(VIEWS):
        raise ValueError("baseline price-free view pin differs")
    for filename, digest in base_freeze["output_pins"].items():
        artifact = BASELINE / filename
        if not artifact.is_file() or sha(artifact) != digest:
            raise ValueError("baseline selected artifact pin differs: " + filename)

    request_by_id = {r["request_id"]: r for r in requests}
    case_by_id = {c["case_id"]: c for c in cases}
    if len(request_by_id) != len(requests) or len(case_by_id) != len(cases):
        raise ValueError("duplicate request/case identity")
    task_by_id: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in task_by_id:
            raise ValueError("missing or duplicate task ID")
        task_by_id[task_id] = task
    used_task_ids: list[str] = []
    row_keys_by_case: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        case_id, row_key = row.get("case_id"), row.get("au_row_key")
        if case_id not in case_by_id or row_key in row_keys_by_case[case_id]:
            raise ValueError("foreign case or duplicate AU row key")
        if row.get("dossier_id") != case_by_id[case_id].get("dossier_id"):
            raise ValueError("row/case dossier mismatch")
        row_keys_by_case[case_id].append(row_key)
        for binding in row["bindings"]:
            task_id = binding.get("task_id")
            task = task_by_id.get(task_id)
            request = request_by_id.get(binding.get("request_id"))
            if task is None or request is None:
                raise ValueError("foreign task/request binding")
            if (task.get("case_id"), task.get("au_row_key"), task.get("request_id"), task.get("direction")) != (
                    case_id, row_key, binding["request_id"], request["direction"]):
                raise ValueError("task case/row/request/direction envelope differs")
            raw_ref = task.get("raw_condition_ref", {})
            if task.get("axis_key") != binding.get("axis_key"):
                raise ValueError("task/binding native axis key differs")
            if (raw_ref.get("axis_name"), raw_ref.get("value")) != (
                    request.get("axis_name"), request.get("selected_value")):
                raise ValueError("task raw condition does not bind to source request")
            used_task_ids.append(task_id)
    if (len(used_task_ids) != EXPECTED["bindings"] or len(set(used_task_ids)) != EXPECTED["bindings"]
            or set(used_task_ids) != set(task_by_id)):
        raise ValueError("every original task must map one-to-one to a row binding")
    for case in cases:
        if row_keys_by_case[case["case_id"]] != case["row_keys"]:
            raise ValueError("case row order/coverage differs")

    expected_base_rows, expected_base_cases = assemble(rows, requests, base_state)
    if (len(base_rows), len(base_cases)) != (len(expected_base_rows), len(expected_base_cases)):
        raise ValueError("baseline row/case coverage differs")
    for actual, expected in zip(base_rows, expected_base_rows):
        if (actual.get("case_id"), actual.get("au_row_key"), actual.get("state"), actual.get("condition_count")) != (
                expected["case_id"], expected["au_row_key"], expected["state"], expected["condition_count"]):
            raise ValueError("baseline raw row state or binding differs")
    raw_states_by_case: dict[str, list[str]] = defaultdict(list)
    for row in base_rows:
        raw_states_by_case[row["case_id"]].append(row["state"])
    for actual, expected in zip(base_cases, expected_base_cases):
        disposition = ("accept" if expected["decision"] == "accept" else
                       "reject" if all(s == "conflict" for s in raw_states_by_case[expected["case_id"]]) else "review")
        if (actual.get("case_id"), actual.get("decision"), actual.get("au_row_key"), actual.get("disposition")) != (
                expected["case_id"], expected["decision"], expected["au_row_key"], disposition):
            raise ValueError("baseline raw case decision differs")
    for actual, request in zip(base_requests, requests):
        if (actual.get("raw_selected_value"), actual.get("view_selected_value")) != (
                request["selected_value"], raw_view[request["selected_value"]]):
            raise ValueError("baseline raw request binding differs")

    nlp = spacy.blank("ja")
    families, catalog_values, collisions = make_catalog_index(requests, docs, raw_view, nlp)
    analyzers: dict[tuple[str, str], LocalPhraseCatalog] = {}
    lexicon_rows = []
    for (dossier, side), catalog in sorted(catalog_values.items()):
        analyzer = LocalPhraseCatalog(nlp, catalog)
        analyzer.metadata["catalog_id"] = f"{dossier}:{side}"
        analyzers[(dossier, side)] = analyzer
        lexicon_rows.append({"catalog_id": f"{dossier}:{side}", "dossier_id": dossier,
            "side": side, "axes": catalog,
            "axis_collision_exclusions": collisions.get((dossier, side), []),
            "adapter_metadata": analyzer.metadata,
            "raw_input_strings_preserved": True, "production_adoption_allowed": False})

    analyses_cache: dict[tuple[str, str, bool], dict[str, Any]] = {}
    analysis_rows: dict[str, dict[str, Any]] = {}
    cache_stats = {"lookups": 0, "hits": 0, "misses": 0}
    observations = []
    mode_results: dict[str, list[dict[str, Any]]] = {"strict": [], "native_punctuation": []}
    start = time.perf_counter()
    for index, req in enumerate(requests, 1):
        doc = docs[req["target_document_id"]]
        selected_source_view = raw_view.get(req["selected_value"])
        selected_refs = {}
        for mode, punctuation in (("strict", False), ("native_punctuation", True)):
            result = evaluate_request(req, doc, families, analyzers, raw_view, nlp,
                                      analyses_cache, analysis_rows, cache_stats, punctuation)
            mode_results[mode].append(result)
            selected_refs[mode] = result["analysis_refs"]
        observations.append({"request_id": req["request_id"],
            "dossier_id": doc["dossier_id"], "source_side": source_side(req["direction"]),
            "source_axis_name": req["axis_name"], "source_family_status": req.get("source_family_status"),
            "raw_selected_value": req["selected_value"], "price_free_view_value": selected_source_view,
            "original_to_view_changes": view_diff(req["selected_value"], selected_source_view or ""),
            "strict_analysis_refs": selected_refs["strict"],
            "native_punctuation_analysis_refs": selected_refs["native_punctuation"],
            "strict_hypothesis": mode_results["strict"][-1],
            "native_punctuation_hypothesis": mode_results["native_punctuation"][-1],
            "raw_baseline_state": base_state[req["request_id"]],
            "raw_baseline_reason": base_reason[req["request_id"]],
            "raw_baseline_preserved": True, "production_adoption_allowed": False})
        if index % 1000 == 0:
            print(f"requests {index}/{len(requests)}", flush=True)

    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "lexicons.jsonl", lexicon_rows)
    write_jsonl(output / "analyses.jsonl", list(analysis_rows.values()))
    write_jsonl(output / "request-observations.jsonl", observations)
    summary = {"schema": "sku-library-components-summary-v1", "scope_status": "unproved",
        "citation_semantics_supported": False, "production_adoption_allowed": False,
        "source_family_count": len(families), "valid_source_family_count": sum(x["valid"] for x in families.values()),
        "invalid_source_family_reasons": dict(Counter(x["invalid_reason"] for x in families.values() if not x["valid"])),
        "catalog_side_count": len(analyzers), "catalog_collision_axes": sum(len(v) for v in collisions.values()),
        "analysis_count": len(analysis_rows), "analysis_cache_entries": len(analyses_cache),
        "analysis_cache": cache_stats,
        "adapter": "spaCy PhraseMatcher attr=NORM; no trained components",
        "experimental_only": True, "production_adoption_allowed": False}
    write_json(output / "ANALYSIS_SUMMARY.json", summary)

    counts = {}
    for mode in ("strict", "native_punctuation"):
        punctuation = mode == "native_punctuation"
        hypotheses = mode_results[mode]
        states = {}
        request_rows = []
        for req, hypothesis in zip(requests, hypotheses):
            rid = req["request_id"]
            # Existing known baseline states are immutable; hypotheses only fill unknowns.
            state = base_state[rid]
            reason = base_reason[rid]
            overlaid = False
            if state == "unknown" and hypothesis["hypothesis_state"] in ("support", "conflict"):
                state = hypothesis["hypothesis_state"]
                reason = "library_component_hypothesis:" + hypothesis["reason"]
                overlaid = True
            states[rid] = state
            request_rows.append({"request_id": rid, "state": state, "reason": reason,
                "raw_baseline_state": base_state[rid], "hypothesis_state": hypothesis["hypothesis_state"],
                "hypothesis_reason": hypothesis["reason"], "overlay_applied_to_unknown_only": overlaid,
                "punctuation_reading_aid": punctuation,
                "production_adoption_allowed": False})
        row_outputs, decisions = assemble(rows, requests, states)
        row_groups: dict[str, list[str]] = defaultdict(list)
        for row in row_outputs:
            row_groups[row["case_id"]].append(row["state"])
        for decision in decisions:
            decision["disposition"] = ("accept" if decision["decision"] == "accept" else
                "reject" if all(s == "conflict" for s in row_groups[decision["case_id"]]) else "review")
            decision["production_adoption_allowed"] = False
        for kind, records in (("requests", request_rows), ("rows", row_outputs), ("cases", decisions)):
            write_jsonl(output / f"{mode}-{kind}.jsonl", records)
        counts[mode] = {
            "requests": dict(Counter(r["state"] for r in request_rows)),
            "rows": dict(Counter(r["state"] for r in row_outputs)),
            "cases": dict(Counter(r["disposition"] for r in decisions)),
            "hypotheses": dict(Counter(r["hypothesis_state"] for r in request_rows)),
            "overlays": sum(r["overlay_applied_to_unknown_only"] for r in request_rows),
            "raw_known_preserved": all(r["state"] == r["raw_baseline_state"]
                                        for r in request_rows if r["raw_baseline_state"] != "unknown")}

    output_files = {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
                    for p in sorted(output.iterdir()) if p.is_file()}
    if any(sha(p) != input_pins[p.relative_to(ROOT).as_posix()] for p in paths):
        raise ValueError("an input changed during run")
    if any(sha(p) != code_pins[p.name] for p in (Path(__file__), HERE / "library_local_phrase_components_v1.py",
                                                  HERE / "replay_dev_nli_policies_v1.py")):
        raise ValueError("code changed during run")
    freeze = {"schema": "sku-library-components-output-freeze-v1",
        "source_path": str(SOURCE.resolve()), "input_pins": input_pins,
        "baseline_output_freeze_sha256": sha(BASELINE / "OUTPUT_FREEZE.json"),
        "price_free_views_sha256": sha(VIEWS), "code_pins": code_pins,
        "output_pins": {name: item["sha256"] for name, item in output_files.items()},
        "output_files": output_files, "counts": counts,
        "analysis_summary": summary, "elapsed_seconds": time.perf_counter() - start,
        "spacy_version": spacy.__version__, "spacy_pipeline": "blank:ja",
        "native_library_versions": {"spacy": spacy.__version__},
        "labels_read": False, "grades_read": False, "traces_read": False,
        "models_loaded": [], "model_inference_count": 0,
        "request_count": len(requests), "row_count": len(rows), "case_count": len(cases),
        "condition_binding_count": sum(len(row["bindings"]) for row in rows),
        "input_catalog_rebuilt_mechanically_from_requests": True,
        "human_feedback_or_answer_registration_used": False,
        "custom_sku_grammar_used": False, "custom_alias_dictionary_used": False,
        "category_rules_used": False, "labels_or_grade_used_for_catalog_changes": False,
        "native_punctuation_mode_is_diagnostic_only": True,
        "raw_baseline_known_states_preserved": True,
        "all_original_conditions_and_rows_retained": True,
        "scope_status": "unproved", "citation_semantics_supported": False,
        "production_adoption_allowed": False}
    write_json(output / "OUTPUT_FREEZE.json", freeze)
    print(json.dumps({"output": str(output), "elapsed_seconds": freeze["elapsed_seconds"],
                      "counts": counts, "analysis_summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".lab-output/sku-library-components-20261015-v1/run-v1")
    run(parser.parse_args().output)
