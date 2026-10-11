#!/usr/bin/env python3
"""Bind Claude's fixed-page alignment to generic bidirectional description tasks.

The prepare step reads no labels and runs no model. The finish step freezes a
binary experimental accept/drop result; it never enables production adoption.
Raw whole values, all alternatives, source spans and fixed SKU/row references
survive. An alternative model winner is unknown, never a contradiction proof.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
import unicodedata

from import_claude_alignment_handoff_v2 import read_archive

ROOT = Path(__file__).resolve().parents[2]
COHORTS = ("legacy", "family")
VERSION = "claude-description-integration-v1"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def norm(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def index(values, key, location):
    result = {}
    for value in values:
        identity = value[key]
        if identity in result:
            raise ValueError(f"duplicate {key}: {location}:{identity}")
        result[identity] = value
    return result


def write_rows(path: Path, values):
    with path.open("xb") as stream:
        for value in values:
            stream.write(encoded(value) + b"\n")


def verify_outputs(directory: Path, manifest: dict, names):
    for name in names:
        if manifest.get("output_sha256", {}).get(name) != sha((directory / name).read_bytes()):
            raise ValueError(f"frozen output SHA differs: {directory / name}")


def axis_text(axes: list[dict]) -> str:
    return "\n".join(f"{axis['axis_name']}：{axis['value']}" for axis in axes)


def document(product: dict, side: str, product_ref: dict) -> dict:
    source = product[side]
    joined, segments, offset = [], [], 0
    for number, description in enumerate(source.get("descriptions", [])):
        text = description["text"]
        if not isinstance(text, str):
            raise ValueError("description must be a string")
        if number:
            offset += 1
        refs = copy.deepcopy(description.get("source_refs", []))
        if description.get("source_ref") is not None:
            refs.append(copy.deepcopy(description["source_ref"]))
        segments.append({"description_index": number, "start": offset, "end": offset + len(text),
                         "source_refs": refs, "input_ref": {**product_ref, "json_path": f"$.{side}.descriptions[{number}].text"}})
        joined.append(text)
        offset += len(text)
    result = {"document_id": f"fixed-{side}:{product['dossier_id']}", "dossier_id": product["dossier_id"],
              "side": side, "fixed_page": True, "product_id": source.get("product_id"), "url": source.get("url"),
              "title": {"text": source.get("title") or "", "source_refs": [copy.deepcopy(source["title_source"])]
                        if source.get("title_source") else []},
              "description": {"text": "\n".join(joined), "segments": segments,
                              "offset_unit": "unicode_characters", "complete": True},
              "input_ref": product_ref, "scope_applicability_proved": False}
    if not isinstance(result["title"]["text"], str):
        raise ValueError("title must be a string")
    result["document_sha256"] = sha(encoded(result))
    return result


def prepare(input_dir: Path, handoff_dir: Path, archive: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    generic_manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    imported_manifest = json.loads((handoff_dir / "manifest.json").read_text(encoding="utf-8"))
    generic_names = ("cases.jsonl", "products.jsonl")
    imported_names = ("cards.jsonl", "upstream_context.jsonl", "reverse_conditions.jsonl", "original_handoff.jsonl")
    verify_outputs(input_dir, generic_manifest, generic_names)
    verify_outputs(handoff_dir, imported_manifest, imported_names)
    paths = [input_dir / name for name in (*generic_names, "manifest.json")] + [handoff_dir / name for name in (*imported_names, "manifest.json")] + [archive]
    input_shas = {str(path.resolve()): sha(path.read_bytes()) for path in paths}
    for name in generic_names:
        if imported_manifest.get("input_sha256", {}).get(name) != sha((input_dir / name).read_bytes()):
            raise ValueError("imported handoff is bound to different generic inputs")
    cases = index(rows(input_dir / "cases.jsonl"), "case_id", "cases")
    product_values = rows(input_dir / "products.jsonl")
    products = index(product_values, "dossier_id", "products")
    product_lines = {product["dossier_id"]: number for number, product in enumerate(product_values, 1)}
    contexts = rows(handoff_dir / "upstream_context.jsonl")
    cards, reverse = rows(handoff_dir / "cards.jsonl"), rows(handoff_dir / "reverse_conditions.jsonl")
    source_members = [source["member"] for source in imported_manifest["selected_handoff_sources"]]
    _, _, payload = read_archive(archive, imported_manifest["archive_sha256"], source_members)
    decisions, tables = {}, {}
    for source in imported_manifest["selected_handoff_sources"]:
        parent = str(Path(source["member"]).parent)
        cohort = Path(parent).parent.name
        if cohort not in COHORTS or cohort in tables:
            raise ValueError("unexpected or duplicate handoff cohort")
        cohort_rows = [json.loads(line) for line in payload[parent + "/decisions.jsonl"].splitlines()]
        cohort_decisions = index(cohort_rows, "case_id", cohort + ":decisions")
        expected = {key for key, case in cases.items() if case.get("cohort") == cohort}
        if set(cohort_decisions) != expected:
            raise ValueError(f"upstream decisions do not cover exactly the frozen cohort: {cohort}")
        if dict(Counter(row["decision"] for row in cohort_rows)) != source["producer_summary"]["decisions"]:
            raise ValueError("upstream declared decision counts differ")
        decisions.update(cohort_decisions)
        tables[cohort] = {"axes": index([json.loads(line) for line in payload[parent + "/axes.jsonl"].splitlines()], "dossier_id", cohort + ":axes"),
                          "pairs": [json.loads(line) for line in payload[parent + "/value_pairs.jsonl"].splitlines()],
                          "source_parent": parent}
    if set(tables) != set(COHORTS):
        raise ValueError("both legacy and family handoffs are required")
    if (handoff_dir / "original_handoff.jsonl").read_bytes() != b"".join(payload[member] for member in source_members):
        raise ValueError("preserved imported handoff differs from the pinned archive")
    by_context = {}
    for context in contexts:
        key = context["case_id"], context["au_row_key"]
        if key in by_context:
            raise ValueError("duplicate fixed case/row context")
        by_context[key] = context
    condition_cards = defaultdict(list)
    for direction, values, name in (("rakuten_to_au", cards, "cards.jsonl"), ("au_to_rakuten", reverse, "reverse_conditions.jsonl")):
        for number, card in enumerate(values, 1):
            key = card["case_id"], card["au_row_key"]
            if key not in by_context or card["direction"] != direction:
                raise ValueError("condition lacks a matching fixed row/direction")
            condition_cards[key].append((card, {"path": str((handoff_dir / name).resolve()),
                                               "sha256": input_shas[str((handoff_dir / name).resolve())], "line": number}))
    if imported_manifest["handoff_row_count"] != len(contexts):
        raise ValueError("imported handoff row count differs")
    documents, requests, tasks, row_plans, case_plans = {}, {}, [], [], []
    counts = Counter()
    pair_lookup = defaultdict(list)
    for cohort, table in tables.items():
        for pair in table["pairs"]:
            pair_lookup[(cohort, pair["dossier_id"], pair["axis_key"], pair["value"])].append(pair)

    def get_document(product, side):
        key = product["dossier_id"], side
        if key not in documents:
            product_ref = {"path": str((input_dir / "products.jsonl").resolve()),
                           "sha256": input_shas[str((input_dir / "products.jsonl").resolve())], "line": product_lines[product["dossier_id"]]}
            documents[key] = document(product, side, product_ref)
        return documents[key]

    def add_task(case, product, row, direction, raw_axis, options, origin, source_ref):
        side = "au" if direction == "rakuten_to_au" else "rakuten"
        axis_name = raw_axis["axis_label"] if direction == "rakuten_to_au" else raw_axis["axis_name"]
        if not isinstance(options, list) or any(not isinstance(value, str) for value in options) or raw_axis["value"] not in options:
            raise ValueError("complete original alternatives must contain the whole selected value")
        target_axes = [{"axis_name": axis["axis_name"], "value": axis["value"]} for axis in row["axes"]] if side == "au" else [
            {"axis_name": axis["axis_label"], "value": axis["value"]} for axis in case["rakuten"]["axes"]]
        doc = get_document(product, side)
        semantic = {"direction": direction, "axis_name": axis_name, "selected_value": raw_axis["value"],
                    "option_values": copy.deepcopy(options), "target_document_id": doc["document_id"],
                    "target_document_sha256": doc["document_sha256"], "target_selected_axes": target_axes,
                    "target_selected_axes_text": axis_text(target_axes)}
        digest = sha(encoded(semantic))
        request_id = "generic-description:" + digest
        request = {"request_id": request_id, "request_sha256": digest, **semantic,
                   "selected_value_is_binding_only": True, "applicability_required": True,
                   "production_adoption_allowed": False}
        requests.setdefault(request_id, request)
        identity = {"case_id": case["case_id"], "au_row_key": row["row_key"], "direction": direction,
                    "axis_key": raw_axis.get("axis_key", raw_axis.get("axis_name"))}
        task_id = "condition:" + sha(encoded(identity))
        tasks.append({"task_id": task_id, **identity, "request_id": request_id, "dossier_id": case["dossier_id"],
                      "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
                      "raw_condition": copy.deepcopy(raw_axis), "option_values": copy.deepcopy(options),
                      "origin": origin, "source_ref": source_ref, "selected_au_row_ref": {
                          "path": str((input_dir / "products.jsonl").resolve()), "sha256": input_shas[str((input_dir / "products.jsonl").resolve())],
                          "line": product_lines[product["dossier_id"]], "json_path": f"$.au.rows[{product['au']['rows'].index(row)}]"},
                      "selected_rakuten_sku_ref": {"path": str((input_dir / "cases.jsonl").resolve()),
                                                   "sha256": input_shas[str((input_dir / "cases.jsonl").resolve())],
                                                   "case_id": case["case_id"], "json_path": "$.rakuten"},
                      "status": "not_evaluated", "verification_required": True, "production_adoption_allowed": False})
        counts["task:" + direction] += 1
        counts["origin:" + origin] += 1
        return {"kind": "model", "task_id": task_id, "request_id": request_id, "direction": direction,
                "axis_key": identity["axis_key"]}

    for case_id, decision in decisions.items():
        case = cases[case_id]
        product = products[case["dossier_id"]]
        if decision["dossier_id"] != case["dossier_id"] or product["au"]["product_id"] != case["au_product_id"]:
            raise ValueError("upstream decision fixed product differs")
        fixed_rows = index(product["au"]["rows"], "row_key", case_id + ":rows")
        state_counts = decision["row_states"]
        if (not isinstance(state_counts, dict) or any(key not in {"contradiction", "aligned", "pending_description_check", "symmetric_unresolved"}
                or type(value) is not int or value < 0 for key, value in state_counts.items()) or sum(state_counts.values()) != len(fixed_rows)):
            raise ValueError("upstream row states do not cover the fixed AU row array")
        pending_keys = [key[1] for key in by_context if key[0] == case_id]
        reason = decision["reason"]
        if reason == "pending_description_check":
            if decision["decision"] != "unmatched" or len(pending_keys) != state_counts.get("pending_description_check", 0) or not pending_keys:
                raise ValueError("upstream pending count differs from imported handoff")
            selected_keys = pending_keys
        elif reason == "all_conditions_aligned_on_one_row":
            if (decision["decision"] != "matched" or pending_keys or state_counts.get("aligned") != 1
                    or state_counts.get("contradiction", 0) != len(fixed_rows) - 1 or decision.get("top_row_key") not in fixed_rows):
                raise ValueError("upstream direct match is not a unique fixed aligned row")
            selected_keys = [decision["top_row_key"]]
        elif reason == "symmetric_condition_unresolved":
            if decision["decision"] != "unmatched" or pending_keys:
                raise ValueError("upstream omitted case unexpectedly has a handoff")
            selected_keys = []
        else:
            raise ValueError("unsupported upstream decision reason")
        plan = {"case_id": case_id, "dossier_id": case["dossier_id"], "cohort": case["cohort"],
                "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
                "upstream_decision": copy.deepcopy(decision), "candidate_row_keys": selected_keys,
                "total_fixed_au_row_count": len(fixed_rows), "upstream_contradiction_row_count": state_counts.get("contradiction", 0),
                "unresolved_omitted_row_count": state_counts.get("symmetric_unresolved", 0),
                "all_pending_rows_received": True, "production_adoption_allowed": False}
        case_plans.append(plan)
        for row_key in selected_keys:
            row = fixed_rows[row_key]
            rak_axes = index(case["rakuten"]["axes"], "axis_key", case_id + ":rak axes")
            au_axes = index(row["axes"], "axis_name", case_id + ":au axes")
            represented_rak, represented_au, bindings = set(), set(), []
            context = by_context.get((case_id, row_key))
            if context:
                if context["source_sku_key"] != case["rakuten"]["source_sku_key"] or context["au_product_id"] != case["au_product_id"]:
                    raise ValueError("context fixed SKU/product differs")
                aligned = context["aligned_conditions"]
            else:
                aligned = []
                symmetric = tables[case["cohort"]]["axes"][case["dossier_id"]]["symmetric"]
                for key, axis in rak_axes.items():
                    eligible = [pair for pair in pair_lookup[(case["cohort"], case["dossier_id"], key, axis["value"])]
                                if pair.get("paired") is True and pair.get("matched_by") == "identical"
                                and pair.get("au_axis") == symmetric.get(key) and pair.get("au_axis") in au_axes
                                and pair.get("au_value") == au_axes[pair["au_axis"]]["value"]
                                and norm(axis["value"]) == norm(pair["au_value"])]
                    if len(eligible) == 1:
                        aligned.append({"raw_condition": axis, "mapped_au_condition": au_axes[eligible[0]["au_axis"]],
                                        "producer_condition": eligible[0]})
            for correspondence in aligned:
                rak_axis, au_axis = correspondence["raw_condition"], correspondence["mapped_au_condition"]
                key, name = rak_axis["axis_key"], au_axis["axis_name"]
                if (rak_axes.get(key) != rak_axis or au_axes.get(name) != au_axis or key in represented_rak or name in represented_au
                        or norm(rak_axis["value"]) != norm(au_axis["value"])):
                    raise ValueError("aligned whole-value correspondence does not bind uniquely to the fixed inputs")
                represented_rak.add(key)
                represented_au.add(name)
                bindings.append({"kind": "upstream_identical", "rakuten_axis_key": key, "au_axis_name": name,
                                 "raw_condition": copy.deepcopy(rak_axis), "mapped_au_condition": copy.deepcopy(au_axis),
                                 "independent_semantic_gold": False})
                counts["upstream_identical_links"] += 1
            for card, card_ref in condition_cards[(case_id, row_key)]:
                direction = card["direction"]
                if direction == "rakuten_to_au":
                    raw_axis = card["raw_condition"]
                    key = raw_axis["axis_key"]
                    if rak_axes.get(key) != raw_axis or key in represented_rak or card["option_values"] != raw_axis["family_values"]:
                        raise ValueError("forward card changed/partially consumed a whole Rakuten axis")
                    represented_rak.add(key)
                else:
                    raw_axis = card["raw_condition"]
                    key = raw_axis["axis_name"]
                    expected_options = list(dict.fromkeys(axis["value"] for candidate in product["au"]["rows"]
                                                        for axis in candidate["axes"] if axis["axis_name"] == key))
                    if au_axes.get(key) != raw_axis or key in represented_au or card["option_values"] != expected_options:
                        raise ValueError("reverse card changed/partially consumed a whole AU axis")
                    represented_au.add(key)
                if card["selected_value"] != raw_axis["value"] or card["source_sku_key"] != case["rakuten"]["source_sku_key"]:
                    raise ValueError("whole card value or fixed SKU differs")
                bindings.append(add_task(case, product, row, direction, raw_axis, card["option_values"],
                                         card.get("producer_status") or card.get("origin") or "upstream_residual", card_ref))
            for key in rak_axes.keys() - represented_rak:
                if context:
                    raise ValueError("pending handoff lost an original Rakuten condition")
                bindings.append(add_task(case, product, row, "rakuten_to_au", rak_axes[key], rak_axes[key]["family_values"],
                                         "upstream_direct_unresolved", {"archive": str(archive.resolve()), "member": tables[case["cohort"]]["source_parent"] + "/value_pairs.jsonl"}))
                represented_rak.add(key)
            for name in au_axes.keys() - represented_au:
                raw_axis = au_axes[name]
                alternatives = list(dict.fromkeys(axis["value"] for candidate in product["au"]["rows"]
                                                for axis in candidate["axes"] if axis["axis_name"] == name))
                bindings.append(add_task(case, product, row, "au_to_rakuten", raw_axis, alternatives, "unrepresented_au_axis",
                                         {"path": str((input_dir / "products.jsonl").resolve()), "line": product_lines[product["dossier_id"]],
                                          "sha256": input_shas[str((input_dir / "products.jsonl").resolve())]}))
                represented_au.add(name)
                counts["unrepresented_singleton_au_axes" if len(alternatives) == 1 else "unrepresented_multiple_au_axes"] += 1
                if not context:
                    counts["upstream_direct_missing_au_axis"] += 1
            row_plans.append({"case_id": case_id, "au_row_key": row_key, "upstream_state": "pending_description_check" if context else "aligned",
                              "condition_bindings": bindings, "rakuten_axis_count": len(rak_axes), "au_axis_count": len(au_axes),
                              "all_original_axes_covered": represented_rak == set(rak_axes) and represented_au == set(au_axes),
                              "production_adoption_allowed": False})
    if len({task["task_id"] for task in tasks}) != len(tasks):
        raise ValueError("duplicate whole-condition task")
    if any(sha(path.read_bytes()) != input_shas[str(path.resolve())] for path in paths):
        raise ValueError("frozen source changed during preparation")
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {"documents.jsonl": list(documents.values()), "requests.jsonl": list(requests.values()), "tasks.jsonl": tasks,
               "row_plans.jsonl": row_plans, "case_plans.jsonl": case_plans}
    for name, values in outputs.items():
        write_rows(output_dir / name, values)
    manifest = {"schema_version": VERSION, "phase": "prepare", "upstream_head": imported_manifest["upstream_head"],
                "input_sha256": input_shas, "code_sha256": sha(Path(__file__).read_bytes()), "labels_read": False,
                "model_inference_run": False, "production_adoption_allowed": False, "scope_applicability_proved": False,
                "whole_values_and_alternatives_preserved": True, "candidate_url_routing": False,
                "upstream_row_state_contract": "aggregate counts and top_row_key only; per-row contradiction identities are not independently supplied",
                "case_count": len(case_plans), "cohort_case_counts": dict(Counter(plan["cohort"] for plan in case_plans)),
                "input_case_count": len(cases), "input_cases_outside_received_cohorts": len(set(cases) - set(decisions)),
                "unreceived_cohort_cases_are_not_processed_or_adopted": True,
                "baseline_matched_count": sum(plan["upstream_decision"]["decision"] == "matched" for plan in case_plans),
                "row_count": len(row_plans), "task_count": len(tasks), "request_count": len(requests), "document_count": len(documents),
                "counts": dict(counts), "model_input_contract": "selected_value is binding only; classify unchanged option_values using axis_name, target title/full description and target_selected_axes_text",
                "source_raw_sha256": generic_manifest.get("source_raw_sha256", {}),
                "output_sha256": {name: sha((output_dir / name).read_bytes()) for name in outputs}}
    (output_dir / "manifest.json").write_bytes(encoded(manifest) + b"\n")
    return manifest


def verify_quote(quote: dict, request: dict, documents: dict) -> bool:
    if not isinstance(quote, dict) or quote.get("document_id") != request["target_document_id"]:
        return False
    field = quote.get("field")
    if field == "selected_axes":
        text = request["target_selected_axes_text"]
    elif field in ("title", "description"):
        text = documents[request["target_document_id"]][field]["text"]
    else:
        return False
    start, end, literal = quote.get("start"), quote.get("end"), quote.get("quote")
    return (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
            and isinstance(literal, str) and literal == text[start:end])


def resolve_prediction(prediction: dict | None, request: dict, documents: dict):
    if prediction is None:
        return "unknown", "missing_prediction", []
    if prediction.get("request_sha256") != request["request_sha256"] or prediction.get("selected_value") != request["selected_value"]:
        raise ValueError("prediction condition/hash binding differs")
    state = prediction.get("state")
    if state not in {"support", "conflict", "unknown"} or prediction.get("scope_status") not in {"fixed_target", "unproved"}:
        raise ValueError("unsupported prediction state/scope")
    winner = prediction.get("winning_value")
    if winner is not None and winner not in request["option_values"]:
        raise ValueError("prediction winner is not a whole original alternative")
    if winner is not None and winner != request["selected_value"]:
        return "unknown", "alternative_winner_is_not_conflict_proof", []
    if state == "unknown":
        return state, "model_unknown", []
    quotes = prediction.get("quote_refs", [])
    if not isinstance(quotes, list) or not quotes or not all(verify_quote(quote, request, documents) for quote in quotes):
        return "unknown", "missing_or_unverified_literal_quote", []
    if state == "support" and winner != request["selected_value"]:
        return "unknown", "support_did_not_select_whole_original_value", []
    return state, "experimental_quoted_model_" + state, quotes


def finish(prepared_dir: Path, predictions: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    manifest = json.loads((prepared_dir / "manifest.json").read_text(encoding="utf-8"))
    names = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row_plans.jsonl", "case_plans.jsonl")
    verify_outputs(prepared_dir, manifest, names)
    before = {str((prepared_dir / name).resolve()): sha((prepared_dir / name).read_bytes()) for name in (*names, "manifest.json")}
    before[str(predictions.resolve())] = sha(predictions.read_bytes())
    documents = index(rows(prepared_dir / "documents.jsonl"), "document_id", "documents")
    requests = index(rows(prepared_dir / "requests.jsonl"), "request_id", "requests")
    predicted = index(rows(predictions), "request_id", "predictions")
    if set(predicted) - set(requests):
        raise ValueError("prediction contains an unprepared request")
    resolved = {}
    for key, request in requests.items():
        state, reason, quotes = resolve_prediction(predicted.get(key), request, documents)
        resolved[key] = {"request_id": key, "state": state, "reason": reason, "verified_quote_refs": quotes,
                         "quote_applicability_independently_proved": False, "production_adoption_allowed": False}
    row_results = {}
    for plan in rows(prepared_dir / "row_plans.jsonl"):
        states = ["support" if binding["kind"] == "upstream_identical" else resolved[binding["request_id"]]["state"]
                  for binding in plan["condition_bindings"]]
        state = ("unknown" if not plan["all_original_axes_covered"] else "conflict" if "conflict" in states else
                 "unknown" if "unknown" in states else "support")
        key = plan["case_id"], plan["au_row_key"]
        row_results[key] = {"case_id": key[0], "au_row_key": key[1], "state": state,
                            "condition_states": dict(Counter(states)), "upstream_state": plan["upstream_state"],
                            "all_original_axes_covered": plan["all_original_axes_covered"], "production_adoption_allowed": False}
    decisions, diffs = [], []
    for plan in rows(prepared_dir / "case_plans.jsonl"):
        states = [row_results[(plan["case_id"], key)] for key in plan["candidate_row_keys"]]
        supported = [result["au_row_key"] for result in states if result["state"] == "support"]
        all_others_conflict = all(result["state"] == "conflict" or result["au_row_key"] in supported for result in states)
        complete = (plan["all_pending_rows_received"] and not plan["unresolved_omitted_row_count"]
                    and len(states) + plan["upstream_contradiction_row_count"] == plan["total_fixed_au_row_count"])
        accepted = len(supported) == 1 and all_others_conflict and complete
        reason = ("unique_fully_supported_fixed_row" if accepted else "omitted_upstream_unresolved_rows" if plan["unresolved_omitted_row_count"] else
                  "multiple_supported_rows" if len(supported) > 1 else "unknown_competing_row" if supported else
                  "no_fully_supported_row")
        result = {"case_id": plan["case_id"], "dossier_id": plan["dossier_id"], "cohort": plan["cohort"],
                  "au_product_id": plan["au_product_id"], "source_sku_key": plan["source_sku_key"],
                  "decision": "accept" if accepted else "drop", "top_row_key": supported[0] if accepted else None,
                  "reason": reason, "experimental_only": True, "production_adoption_allowed": False,
                  "row_states": dict(Counter(result["state"] for result in states)),
                  "upstream_contradiction_row_count": plan["upstream_contradiction_row_count"],
                  "upstream_decision": plan["upstream_decision"]["decision"], "upstream_top_row_key": plan["upstream_decision"].get("top_row_key")}
        decisions.append(result)
        baseline = "accept" if result["upstream_decision"] == "matched" else "drop"
        if baseline != result["decision"] or result["upstream_top_row_key"] != result["top_row_key"]:
            diffs.append(result)
    if any(sha(Path(path).read_bytes()) != digest for path, digest in before.items()):
        raise ValueError("frozen prepared inputs/predictions changed during gating")
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {"decisions.jsonl": decisions, "row_results.jsonl": list(row_results.values()),
               "condition_results.jsonl": list(resolved.values()), "diff_vs_upstream.jsonl": diffs}
    for name, values in outputs.items():
        write_rows(output_dir / name, values)
    result_manifest = {"schema_version": VERSION, "phase": "finish", "upstream_head": manifest["upstream_head"],
                       "input_sha256": before, "code_sha256": sha(Path(__file__).read_bytes()), "labels_read": False,
                       "case_count": len(decisions), "cohort_case_counts": dict(Counter(row["cohort"] for row in decisions)),
                       "decision_counts": dict(Counter(row["decision"] for row in decisions)),
                       "condition_states": dict(Counter(row["state"] for row in resolved.values())),
                       "condition_reasons": dict(Counter(row["reason"] for row in resolved.values())),
                       "baseline_matched_count": manifest["baseline_matched_count"], "changed_case_count": len(diffs),
                       "input_cases_outside_received_cohorts": manifest.get("input_cases_outside_received_cohorts", 0),
                       "missing_prediction_count": len(requests) - len(predicted), "production_adoption_allowed": False,
                       "scope_applicability_independently_proved": False, "no_human_review_lane": True,
                       "diagnostic_scope_policy": "quoted predictions may affect experimental decisions even when applicability is unproved; these decisions must not be adopted in production",
                       "prediction_scope_status_counts": dict(Counter(value["scope_status"] for value in predicted.values())),
                       "unproved_scope_supported_request_count": sum(value["state"] == "support" and predicted.get(key, {}).get("scope_status") == "unproved" for key, value in resolved.items()),
                       "upstream_row_state_contract": manifest.get("upstream_row_state_contract", "aggregate counts and top_row_key only; per-row contradiction identities are not independently supplied"),
                       "whole_sku_accuracy_measured": False,
                       "output_sha256": {name: sha((output_dir / name).read_bytes()) for name in outputs}}
    (output_dir / "manifest.json").write_bytes(encoded(result_manifest) + b"\n")
    return result_manifest


def score(finished_dir: Path, labels: Path, output_file: Path) -> dict:
    """Read separate machine reference labels only after predictions are frozen.

    This does not update decisions or train/choose model thresholds. Review
    reference labels are excluded from scoring, not emitted as a system lane.
    """
    if output_file.exists():
        raise FileExistsError(f"refusing to overwrite {output_file}")
    manifest = json.loads((finished_dir / "manifest.json").read_text(encoding="utf-8"))
    verify_outputs(finished_dir, manifest, ("decisions.jsonl",))
    source_paths = (finished_dir / "decisions.jsonl", finished_dir / "manifest.json", labels)
    before = {str(path.resolve()): sha(path.read_bytes()) for path in source_paths}
    decisions = index(rows(finished_dir / "decisions.jsonl"), "case_id", "decisions")
    label_values = rows(labels)
    references = index(label_values, "case_id", "labels")
    counts, cohort_counts, row_counts = Counter(), defaultdict(Counter), Counter()
    excluded, differences = [], []
    for identity, prediction in decisions.items():
        reference = references.get(identity)
        if reference is None:
            excluded.append({"case_id": identity, "reason": "missing_reference"})
            continue
        if "label" in reference and "decision" in reference and reference["label"] != reference["decision"]:
            raise ValueError("reference label/decision aliases conflict")
        if ("matched_au_row_keys" in reference and "matching_au_row_keys" in reference
                and reference["matched_au_row_keys"] != reference["matching_au_row_keys"]):
            raise ValueError("reference matched/matching AU row aliases conflict")
        label = reference.get("label", reference.get("decision"))
        if label not in {"matched", "unmatched"}:
            if label not in {"review", "needs_review", "unknown"}:
                raise ValueError("unsupported scoring label; use the separate declared label contract")
            excluded.append({"case_id": identity, "reason": "unresolved_reference"})
            continue
        accepted = prediction["decision"] == "accept"
        matched = label == "matched"
        cell = "tp" if accepted and matched else "fp" if accepted else "fn" if matched else "tn"
        counts[cell] += 1
        cohort_counts[prediction["cohort"]][cell] += 1
        expected_rows = reference.get("matched_au_row_keys", reference.get("matching_au_row_keys"))
        if expected_rows is not None:
            if not isinstance(expected_rows, list) or any(not isinstance(value, str) for value in expected_rows):
                raise ValueError("matched_au_row_keys must be an array of complete row keys")
            if matched and not expected_rows:
                raise ValueError("matched reference with row evidence must have at least one row key")
            if accepted and matched:
                row_counts["correct_accepted_row" if prediction["top_row_key"] in expected_rows else "wrong_accepted_row"] += 1
        elif accepted and matched:
            row_counts["accepted_matched_row_unscored"] += 1
        if cell in {"fp", "fn"} or (accepted and matched and expected_rows is not None and prediction["top_row_key"] not in expected_rows):
            differences.append({"case_id": identity, "label": label, "decision": prediction["decision"],
                                "top_row_key": prediction["top_row_key"], "matched_au_row_keys": expected_rows})

    def metrics(counter):
        cells = {key: counter[key] for key in ("tp", "fp", "fn", "tn")}
        total = sum(cells.values())
        return {**cells, "scored_case_count": total,
                "binary_accuracy": (counter["tp"] + counter["tn"]) / total if total else None,
                "precision": counter["tp"] / (counter["tp"] + counter["fp"]) if counter["tp"] + counter["fp"] else None,
                "recall": counter["tp"] / (counter["tp"] + counter["fn"]) if counter["tp"] + counter["fn"] else None}
    if any(sha(path.read_bytes()) != before[str(path.resolve())] for path in source_paths):
        raise ValueError("frozen decisions/references changed during scoring")
    result = {"schema_version": VERSION, "phase": "score", "reference_kind": "separate_machine_labels_not_human_gold",
              "input_sha256": before, "code_sha256": sha(Path(__file__).read_bytes()), "labels_read_after_decisions_frozen": True,
              "decisions_changed": False, "thresholds_selected": False, "production_adoption_allowed": False,
              "metrics": metrics(counts), "cohort_metrics": {cohort: metrics(counter) for cohort, counter in cohort_counts.items()},
              "row_metrics": dict(row_counts), "excluded": excluded, "differences": differences,
              "references_outside_received_decisions": len(set(references) - set(decisions)),
              "whole_sku_correctness_established": False}
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("xb") as stream:
        stream.write(encoded(result) + b"\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--input-dir", type=Path, default=ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2")
    prep.add_argument("--handoff-dir", type=Path, required=True)
    prep.add_argument("--archive", type=Path, required=True)
    prep.add_argument("--output-dir", type=Path, required=True)
    gate = commands.add_parser("finish")
    gate.add_argument("--prepared-dir", type=Path, required=True)
    gate.add_argument("--predictions", type=Path, required=True)
    gate.add_argument("--output-dir", type=Path, required=True)
    grading = commands.add_parser("score")
    grading.add_argument("--finished-dir", type=Path, required=True)
    grading.add_argument("--labels", type=Path, required=True)
    grading.add_argument("--output-file", type=Path, required=True)
    options = vars(parser.parse_args(argv))
    command = options.pop("command")
    result = {"prepare": prepare, "finish": finish, "score": score}[command](**options)
    print(json.dumps({key: value for key, value in result.items() if key not in {"input_sha256", "output_sha256", "source_raw_sha256"}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
