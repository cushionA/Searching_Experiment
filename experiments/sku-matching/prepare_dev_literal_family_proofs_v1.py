#!/usr/bin/env python3
"""Prepare and build exact raw-family proofs for a frozen DEV SKU subset.

The source adapter tasks remain byte-for-byte the proof inputs. This wrapper
only selects their original source records, pins the DEV input manifests, calls
the unchanged literal-family proof builder, and aggregates unanimous proofs by
original request. No labels, model predictions, or v7 hints are read.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ADAPTER = HERE / "prepare_fresh_dev_v7_gate_v1.py"
BUILDER = HERE / "build_sku_literal_family_proofs_v1.py"
DEFAULT_GATE_INPUTS = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8"
DEFAULT_SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
DEFAULT_SOURCE_BUNDLE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-next-dev-literal-family-proofs-20261013-v1"
DEFAULT_NLI_V2 = ROOT / ".lab-output/sku-next-dev-choice-nli-20261013-v2"


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        f.write("\n")


def write_rows(path: Path, rows):
    with path.open("xb") as f:
        for row in rows:
            f.write(canonical(row) + b"\n")


def replace_rows(path: Path, rows):
    temporary = path.with_name("." + path.name + ".pending")
    if temporary.exists():
        raise FileExistsError("refusing to overwrite pending file " + str(temporary))
    write_rows(temporary, rows)
    os.replace(temporary, path)


def indexed(rows, key, label):
    result = {}
    for row in rows:
        identity = row.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError("missing or duplicate " + label + " identity")
        result[identity] = row
    return result


def load_original_records(source_bundle: Path, source_manifest: dict, selected_cases: dict):
    case_by_id, product_by_dossier = {}, {}
    source_pins = {}
    selected_manifest = []
    for version in ("v2", "v3"):
        prefix = f".lab-output/sku-independent-real-20261011/eval-{version}/run-token/inputs/"
        case_rel, product_rel = prefix + "cases.jsonl", prefix + "products.jsonl"
        expected_cases = source_manifest.get("source_inputs", {}).get(case_rel)
        expected_products = source_manifest.get("source_inputs", {}).get(product_rel)
        cases_path = source_bundle / case_rel
        products_path = source_bundle / product_rel
        if (not expected_cases or not expected_products
                or sha_file(cases_path) != expected_cases or sha_file(products_path) != expected_products):
            raise ValueError("original DEV source input does not match the full-condition source manifest")
        source_pins[version] = {"cases_sha256": expected_cases, "products_sha256": expected_products}
        raw_cases, raw_products = read_rows(cases_path), read_rows(products_path)
        case_lines = {}
        for line_no, raw in enumerate(cases_path.read_bytes().splitlines(), 1):
            if raw:
                obj = json.loads(raw)
                case_lines[obj["case_id"]] = {"line": line_no,
                    "line_sha256": hashlib.sha256(raw).hexdigest()}
        cases_here = {row["case_id"]: row for row in raw_cases}
        products_here = {row["dossier_id"]: row for row in raw_products}
        if len(cases_here) != len(raw_cases) or len(products_here) != len(raw_products):
            raise ValueError("duplicate original DEV source case or dossier")
        for case_id, plan in selected_cases.items():
            if plan.get("source_version") != version:
                continue
            case = cases_here.get(case_id)
            product = products_here.get(plan["dossier_id"])
            if (case is None or product is None or case.get("dossier_id") != plan["dossier_id"]
                    or str(case.get("au_product_id")) != str(plan["au_product_id"])
                    or product.get("dossier_id") != plan["dossier_id"]
                    or str((product.get("au_product") or {}).get("product_id")) != str(plan["au_product_id"])):
                raise ValueError("selected case/product does not bind to its exact original DEV records")
            case_by_id[case_id] = {"record": case}
            product_by_dossier[plan["dossier_id"]] = {"record": product}
            selected_manifest.append({
                "case_id": case_id, "dossier_id": plan["dossier_id"],
                "au_product_id": str(plan["au_product_id"]), "source_version": version,
                "case_source_input": case_rel, "case_source_sha256": expected_cases,
                "case_source_line": case_lines[case_id]["line"],
                "case_source_line_sha256": case_lines[case_id]["line_sha256"],
                "product_source_input": product_rel, "product_source_sha256": expected_products,
            })
    if set(case_by_id) != set(selected_cases):
        raise ValueError("original source records do not cover exact frozen case selection")
    needed_dossiers = {case["dossier_id"] for case in selected_cases.values()}
    if set(product_by_dossier) != needed_dossiers:
        raise ValueError("original source products do not cover exact selected dossier population")
    return case_by_id, product_by_dossier, source_pins, selected_manifest


def prepare_inputs(gate_inputs: Path, source_bundle: Path, output_root: Path) -> dict:
    if output_root.exists():
        raise FileExistsError("refusing to overwrite " + str(output_root))
    spec = importlib.util.spec_from_file_location("fresh_dev_v7_adapter", ADAPTER)
    adapter = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = adapter
    spec.loader.exec_module(adapter)

    gate_manifest = read_json(gate_inputs / "manifest.json")
    if (gate_manifest.get("schema_version") != "fresh-dev-v7-whole-sku-gate-prepare-v1"
            or gate_manifest.get("phase") != "source_only_prepare_with_v7_hints"
            or gate_manifest.get("labels_read") is not False
            or gate_manifest.get("test_split_read") is not False
            or gate_manifest.get("whole_gate_evaluation_run") is not False
            or gate_manifest.get("whole_gate_scores_created") is not False):
        raise ValueError("canonical input is not the frozen source-only DEV gate preparation")
    for name, digest in gate_manifest.get("outputs_sha256", {}).items():
        if sha_file(gate_inputs / name) != digest:
            raise ValueError("canonical DEV gate input output SHA differs: " + name)
    base = gate_inputs.parent
    members = read_rows(base / "membership.jsonl")
    member_plan, members = adapter.load_selection(base)
    source_manifest, _, cases, source_rows, source_tasks, source_requests, _, *_ = adapter.load_source(
        DEFAULT_SOURCE, source_bundle)
    selected_cases, selected_rows, selected_tasks, selected_requests, _ = adapter.filter_source(
        members, cases, source_rows, source_tasks, source_requests,
        indexed(read_rows(gate_inputs / "documents.jsonl"), "document_id", "source document"))
    if (len(selected_cases) != 40 or len(selected_rows) != 504 or len(selected_tasks) != 1924
            or len(selected_requests) != 1045):
        raise ValueError("canonical selected DEV source population changed")

    row_map = {(row["case_id"], row["au_row_key"]): row for row in selected_rows}
    task_ids_by_row = defaultdict(list)
    for task in selected_tasks.values():
        key = (task["case_id"], task["au_row_key"])
        if key not in row_map:
            raise ValueError("selected proof task is outside selected fixed row plan")
        task_ids_by_row[key].append(task["task_id"])
    for key, row in row_map.items():
        bindings = {binding["task_id"] for binding in row["bindings"]}
        if bindings != set(task_ids_by_row.get(key, [])):
            raise ValueError("selected proof task coverage differs from complete bidirectional row bindings")
        row_tasks = [selected_tasks[task_id] for task_id in bindings]
        forward = [task for task in row_tasks if task["direction"] == "rakuten_to_au"]
        reverse = [task for task in row_tasks if task["direction"] == "au_to_rakuten"]
        if len(forward) != row["rakuten_axis_count"] or len(reverse) != row["au_axis_count"]:
            raise ValueError("selected proof tasks do not cover every original axis in both directions")

    source_cases, source_products, source_pins, source_manifest_rows = load_original_records(
        source_bundle, source_manifest, selected_cases)
    output_root.mkdir(parents=True)
    inputs = output_root / "inputs"
    inputs.mkdir()
    write_rows(inputs / "tasks.jsonl", (selected_tasks[key] for key in sorted(selected_tasks)))
    write_rows(inputs / "source-cases.jsonl", (source_cases[key] for key in sorted(source_cases)))
    write_rows(inputs / "source-products.jsonl", (source_products[key] for key in sorted(source_products)))
    write_rows(inputs / "row-plans.jsonl", sorted(selected_rows, key=lambda row: (row["case_id"], row["au_row_key"])))
    write_rows(inputs / "selected-source-manifest.jsonl", sorted(source_manifest_rows, key=lambda x: x["case_id"]))
    write_json(inputs / "source-input-manifests.json", source_pins)
    data_names = ("tasks.jsonl", "source-cases.jsonl", "source-products.jsonl", "row-plans.jsonl",
                  "selected-source-manifest.jsonl", "source-input-manifests.json")
    output_sha = {name: sha_file(inputs / name) for name in data_names}
    write_json(inputs / "manifest.json", {
        "schema_version": "sku-literal-family-dev-input-v1",
        "source_gate_artifact": str(gate_inputs.relative_to(ROOT)),
        "source_gate_manifest_sha256": sha_file(gate_inputs / "manifest.json"),
        "adapter_code_sha256": sha_file(ADAPTER),
        "original_proof_code_sha256": sha_file(HERE / "sku_literal_family_proof_v1.py"),
        "original_builder_code_sha256": sha_file(BUILDER),
        "source_full_condition_manifest_sha256": sha_file(DEFAULT_SOURCE / "manifest.json"),
        "source_bundle_inventory_sha256": sha_file(source_bundle.parent / "inventory.json"),
        "source_bundle_index_sha256": sha_file(source_bundle.parent / "source-index.json"),
        "selection_plan_sha256": sha_file(base / "selection-plan.json"),
        "membership_sha256": sha_file(base / "membership.jsonl"),
        "selected_case_count": len(selected_cases), "selected_product_count": len({str(c["au_product_id"]) for c in selected_cases.values()}),
        "row_plan_count": len(selected_rows), "task_count": len(selected_tasks),
        "request_count": len(selected_requests), "proof_pair_coverage_required": True,
        "source_input_pins": source_pins, "output_sha256": output_sha,
        "labels_read": False, "test_split_read": False, "predictions_read": False,
        "v7_hints_read": False, "model_inference_run": False,
    })
    # The old proof builder expects its hashes under output_sha256. The manifest
    # itself is pinned separately to avoid a self-referential digest.
    return {"input_dir": inputs, "selected_cases": selected_cases, "selected_rows": selected_rows,
            "selected_tasks": selected_tasks, "selected_requests": selected_requests,
            "source_cases": source_cases, "source_products": source_products, "source_pins": source_pins,
            "member_plan": member_plan, "adapter": adapter}


def aggregate_request_statuses(output_root: Path, prepared: dict, proofs_path: Path) -> dict:
    tasks = prepared["selected_tasks"]
    requests = prepared["selected_requests"]
    proof_rows = read_rows(proofs_path)
    by_task = defaultdict(list)
    for proof_row in proof_rows:
        for task_id in (proof_row.get("forward_task_id"), proof_row.get("reverse_task_id")):
            if task_id:
                by_task[task_id].append(proof_row)
    request_tasks = defaultdict(list)
    for task in tasks.values():
        request_tasks[task["request_id"]].append(task)
    if set(request_tasks) != set(requests):
        raise ValueError("literal proof source tasks do not cover every selected request")

    request_statuses = []
    statuses_by_id = {}
    eligible_context_rows = defaultdict(list)
    task_statuses = []
    for task_id, task in sorted(tasks.items()):
        matches = by_task.get(task_id, [])
        receipt = next((row for row in prepared.get("task_source_receipts", [])
                        if row.get("task_id") == task_id), None)
        proof_row = matches[0] if len(matches) == 1 else None
        partner_id = None
        if proof_row:
            partner_id = (proof_row.get("reverse_task_id") if task["direction"] == "rakuten_to_au"
                          else proof_row.get("forward_task_id"))
        proof_state = proof_row.get("status") if proof_row else "unknown"
        if (proof_state not in {"support", "conflict"} or not receipt
                or receipt.get("status") != "verified_complete_ordered_raw_family"
                or proof_row.get("ordered_family") != receipt.get("ordered_family")
                or task.get("raw_condition_ref", {}).get("value") not in proof_row.get("ordered_family", [])):
            proof_state = "unknown"
        task_statuses.append({"task_id": task_id, "request_id": task["request_id"],
            "case_id": task["case_id"], "au_row_key": task["au_row_key"],
            "direction": task["direction"], "axis_name": task["raw_condition_ref"]["axis_name"],
            "selected_value": task["raw_condition_ref"]["value"],
            "ordered_family": receipt.get("ordered_family") if receipt else None,
            "status": proof_state, "proof_pair_id": (f"{proof_row.get('forward_task_id')}|{proof_row.get('reverse_task_id')}"
                                                           if proof_row else None),
            "counterpart_task_id": partner_id if proof_state != "unknown" else None,
            "raw_source_family_verified": bool(receipt and receipt.get("status") == "verified_complete_ordered_raw_family"),
            "literal_proof_verified": proof_state in {"support", "conflict"},
            "production_adoption_allowed": False})
    task_state_by_id = {row["task_id"]: row for row in task_statuses}
    for request_id, request in sorted(requests.items()):
        bound = sorted(request_tasks[request_id], key=lambda task: task["task_id"])
        bound_proofs, observed_states = [], []
        structurally_complete = True
        for task in bound:
            if (task.get("direction") != request.get("direction")
                    or task.get("raw_condition_ref", {}).get("value") != request.get("selected_value")
                    or task.get("request_id") != request_id):
                raise ValueError("request/task source selected-value or direction fanout differs")
            task_state = task_state_by_id[task["task_id"]]
            matches = by_task.get(task["task_id"], [])
            if len(matches) != 1 or task_state["status"] == "unknown":
                structurally_complete = False
                continue
            proof_row = matches[0]
            partner_id = (proof_row.get("reverse_task_id") if task["direction"] == "rakuten_to_au"
                          else proof_row.get("forward_task_id"))
            partner = tasks.get(partner_id)
            if (partner is None or partner.get("case_id") != task.get("case_id")
                    or partner.get("au_row_key") != task.get("au_row_key")
                    or partner.get("raw_condition_ref", {}).get("axis_name") != task.get("raw_condition_ref", {}).get("axis_name")
                    or proof_row.get("axis_name") != request.get("axis_name")
                    or proof_row.get("ordered_family") != request.get("option_values")
                    or request.get("selected_value") not in proof_row.get("ordered_family", [])):
                structurally_complete = False
                continue
            bound_proofs.append(proof_row)
            observed_states.append(proof_row.get("status"))
        unique_states = set(observed_states)
        eligible = (structurally_complete and len(bound_proofs) == len(bound)
                    and len(unique_states) == 1 and next(iter(unique_states), None) in {"support", "conflict"})
        state = next(iter(unique_states)) if eligible else "unknown"
        row = {"request_id": request_id, "request_sha256": request["request_sha256"],
               "model_input_id": request["model_input_id"], "direction": request["direction"],
               "axis_name": request["axis_name"], "selected_value": request["selected_value"],
               "option_values": request["option_values"], "target_document_id": request["target_document_id"],
               "target_document_sha256": request["target_document_sha256"],
               "target_selected_axes": request["target_selected_axes"],
               "source_task_ids": [task["task_id"] for task in bound],
               "proof_pair_ids": sorted({f"{proof_row.get('forward_task_id')}|{proof_row.get('reverse_task_id')}"
                                         for proof_row in bound_proofs}),
               "proof_pair_count": len(bound_proofs), "source_task_count": len(bound),
               "status": state, "eligible": eligible,
               "winning_value": request["selected_value"] if state == "support" else None,
               "reason": "unanimous_verified_complete_raw_family" if eligible else "incomplete_or_nonunanimous_raw_family_proofs",
               "evidence_type": "verified_exact_complete_ordered_raw_family",
               "raw_source_proof_ids": sorted({f"{x.get('case_id')}|{x.get('au_row_key')}|{x.get('axis_name')}"
                                                for x in bound_proofs}),
               "production_adoption_allowed": False}
        request_statuses.append(row)
        statuses_by_id[request_id] = row
        if eligible:
            eligible_context_rows[request["model_input_id"]].append(row)

    # A cache context is protected only if every request sharing its model input
    # is eligible and agrees on the same exact source claim.
    all_by_context = defaultdict(list)
    for request_id, request in requests.items():
        all_by_context[request["model_input_id"]].append(statuses_by_id[request_id])
    protected_contexts = []
    for context_id, rows in sorted(all_by_context.items()):
        scope_signatures = {(row["direction"], row["axis_name"], row["target_document_id"],
                             row["target_document_sha256"], tuple(row["option_values"]),
                             canonical(row["target_selected_axes"])) for row in rows}
        if (len(rows) == len(eligible_context_rows.get(context_id, []))
                and len(scope_signatures) == 1 and rows[0]["eligible"]):
            protected_contexts.append({"model_input_id": context_id,
                "request_ids": sorted(row["request_id"] for row in rows),
                "request_states": [{"request_id": row["request_id"], "status": row["status"],
                                    "selected_value": row["selected_value"]}
                                   for row in sorted(rows, key=lambda item: item["request_id"])],
                "option_values": rows[0]["option_values"], "target_selected_axes": rows[0]["target_selected_axes"],
                "target_document_id": rows[0]["target_document_id"],
                "target_document_sha256": rows[0]["target_document_sha256"],
                "direction": rows[0]["direction"], "axis_name": rows[0]["axis_name"],
                "request_count": len(rows),
                "all_requests_have_verified_nonunknown_literal_state": True,
                "statuses_may_differ_across_desired_bindings": True,
                "skip_nli_context_if_frozen_inference_map_matches": True,
                "production_adoption_allowed": False})
    replace_rows(output_root / "request-proof-statuses.jsonl", request_statuses)
    replace_rows(output_root / "protected-requests.jsonl", (row for row in request_statuses if row["eligible"]))
    replace_rows(output_root / "task-proof-states.jsonl", task_statuses)
    replace_rows(output_root / "protected-nli-contexts.jsonl", protected_contexts)
    return {"request_status_count": len(request_statuses),
            "eligible_request_status_counts": dict(Counter(row["status"] for row in request_statuses if row["eligible"])),
            "unknown_request_status_count": sum(not row["eligible"] for row in request_statuses),
            "protected_nli_context_count": len(protected_contexts),
            "task_proof_state_counts": dict(Counter(row["status"] for row in task_statuses)),
            "protected_request_count": sum(row["eligible"] for row in request_statuses),
            "proof_pair_count": len(proof_rows), "task_proof_binding_count": sum(map(len, by_task.values()))}


def verify_all_task_source_families(output_root: Path, source_bundle: Path) -> dict:
    """Verify each original task's own complete ordered raw family, even if no
    opposite axis name exists for a bidirectional paired proof.
    """
    sys.path.insert(0, str(HERE / "claude_v9_gate"))
    import sku_gate_sources as sources
    import sku_literal_family_proof_v1 as proof

    inputs = output_root / "inputs"
    input_manifest = read_json(inputs / "manifest.json")
    tasks = read_rows(inputs / "tasks.jsonl")
    cases = {row["record"]["case_id"]: row["record"] for row in read_rows(inputs / "source-cases.jsonl")}
    products = {row["record"]["dossier_id"]: row["record"] for row in read_rows(inputs / "source-products.jsonl")}
    source_pins = read_json(inputs / "source-input-manifests.json")
    store = sources.RawStore(source_bundle)
    receipts = []
    raw_file_digests = {}
    for task in tasks:
        row = {"task_id": task.get("task_id"), "case_id": task.get("case_id"),
               "au_row_key": task.get("au_row_key"), "direction": task.get("direction"),
               "request_id": task.get("request_id"), "status": "unknown"}
        try:
            case = cases.get(task.get("case_id"))
            product = products.get(task.get("dossier_id"))
            if not case or not product:
                raise ValueError("task source case or product record is missing")
            if (task.get("dossier_id") != case.get("dossier_id")
                    or task.get("dossier_id") != product.get("dossier_id")
                    or str(task.get("au_product_id")) != str(case.get("au_product_id"))
                    or str(task.get("au_product_id")) != str((product.get("au_product") or {}).get("product_id"))):
                raise ValueError("task fixed case/dossier/AU product binding differs")
            provenance = task.get("provenance") or {}
            version = provenance.get("source_version")
            if (version not in source_pins
                    or provenance.get("case_source_sha256") != source_pins[version].get("cases_sha256")
                    or provenance.get("product_source_sha256") != source_pins[version].get("products_sha256")):
                raise ValueError("task original source-input SHA pin differs")
            row_key = task.get("au_row_key")
            if provenance.get("row_key") != row_key:
                raise ValueError("task original selected row key differs from source provenance")
            ref = task.get("raw_condition_ref") or {}
            family_spans = []
            if task.get("direction") == "rakuten_to_au":
                selected = case.get("rakuten_selected") or {}
                axis = proof._one(selected.get("axes") or [],
                                  lambda item: item.get("axis_label") == ref.get("axis_name"),
                                  "unique selected Rakuten source axis")
                if (axis.get("value") != ref.get("value")
                        or proof._normalized_span(axis.get("value_span")) != proof._normalized_span(ref.get("value_span"))
                        or ref.get("axis_label") != ref.get("axis_name")):
                    raise ValueError("Rakuten raw task selected value/span differs from original selected source")
                proof._verified_span(store, ref.get("axis_label_span"), ref["axis_name"], "Rakuten task source axis label")
                proof._verified_span(store, ref.get("value_span"), ref["value"], "Rakuten task selected value")
                family, family_spans, axis_span, selected_span = proof._rakuten_family(store, selected, axis)
                if proof._normalized_span(ref.get("axis_label_span")) != proof._normalized_span(axis_span):
                    raise ValueError("Rakuten task label span differs from raw complete-family selector")
                row.update({"raw_file": selected.get("raw_file"), "selected_value": ref["value"],
                            "axis_name": ref["axis_name"], "ordered_family": family,
                            "axis_label_span": proof._normalized_span(axis_span),
                            "selected_value_span": proof._normalized_span(selected_span)})
            elif task.get("direction") == "au_to_rakuten":
                au_row = proof._one(product.get("au_rows") or [], lambda item: item.get("row_key") == row_key,
                                    "unique original AU source row")
                axis = proof._one(au_row.get("axes") or [],
                                  lambda item: item.get("axis_name") == ref.get("axis_name"),
                                  "unique original AU source axis")
                if (axis.get("value") != ref.get("value")
                        or proof._normalized_span(axis.get("value_span")) != proof._normalized_span(ref.get("value_span"))):
                    raise ValueError("AU raw task selected value/span differs from original selected row")
                family, family_spans, axis_span, selected_span = proof._au_family(
                    store, product, ref["axis_name"], ref["value"], ref["value_span"])
                proof._verified_span(store, axis.get("axis_name_span"), ref["axis_name"], "AU task source axis label")
                if proof._normalized_span(axis.get("axis_name_span")) != proof._normalized_span(axis_span):
                    raise ValueError("AU task label span differs from raw complete-family axis")
                row.update({"raw_file": (product.get("au_product") or {}).get("raw_file"),
                            "selected_value": ref["value"], "axis_name": ref["axis_name"],
                            "ordered_family": family, "axis_label_span": proof._normalized_span(axis_span),
                            "selected_value_span": proof._normalized_span(selected_span)})
            else:
                raise ValueError("unsupported original bidirectional source task direction")
            row["status"] = "verified_complete_ordered_raw_family"
            row["family_value_span_count"] = len(family_spans)
            row["raw_span_receipts"] = [proof._normalized_span(span) for span in family_spans]
            row["source_pins"] = {"source_version": version,
                                   "case_source_sha256": provenance["case_source_sha256"],
                                   "product_source_sha256": provenance["product_source_sha256"]}
            row["reason"] = "exact_source_value_and_complete_family_spans_verified"
            for span in [row["axis_label_span"], row["selected_value_span"], *row["raw_span_receipts"]]:
                raw_file = span.get("raw_file")
                digest = span.get("sha256")
                if raw_file and digest:
                    if store.sha(raw_file) != digest:
                        raise ValueError("raw family receipt hash differs")
                    raw_file_digests[raw_file] = digest
        except (KeyError, TypeError, ValueError, OSError, UnicodeDecodeError) as exc:
            row["reason"] = str(exc)
        receipts.append(row)
    if len(receipts) != len(tasks) or len({row["task_id"] for row in receipts}) != len(tasks):
        raise ValueError("per-task raw source verification did not cover unique original task IDs")
    path = output_root / "task-source-verification.jsonl"
    pending_path = output_root / ".task-source-verification.pending.jsonl"
    if pending_path.exists():
        raise FileExistsError("refusing to overwrite pending task-source verification")
    write_rows(pending_path, receipts)
    os.replace(pending_path, path)
    verification_counts = Counter(row["status"] for row in receipts)
    manifest_path = output_root / "manifest.json"
    run_manifest = read_json(manifest_path)
    run_manifest.update({"task_source_verification_sha256": sha_file(path),
        "task_source_verification_count": len(receipts),
        "task_source_verification_status_counts": dict(verification_counts),
        "task_source_verification_raw_file_count": len(raw_file_digests),
        "task_source_verification_receipts": "all selected task labels, selected values, complete ordered families, and raw spans are independently pinned",
        "all_original_task_raw_families_verified": verification_counts.get("verified_complete_ordered_raw_family", 0) == len(tasks),
        "task_source_verification_uses_model_or_v7_hint": False})
    if manifest_path.exists():
        manifest_path.write_text(json.dumps(run_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                                 encoding="utf-8")
    return {"task_source_verification_count": len(receipts),
            "task_source_verification_status_counts": dict(verification_counts),
            "task_source_verification_raw_file_count": len(raw_file_digests)}


def attach_nli_v2_protection_map(output_root: Path, nli_dir: Path) -> dict:
    """Join source-only literal states to prepared semantic contexts, never responses."""
    nli_freeze = read_json(nli_dir / "freeze.json")
    nli_summary = read_json(nli_dir / "summary.json")
    proof_manifest = read_json(output_root / "manifest.json")
    proof_inputs = read_json(output_root / "inputs/manifest.json")
    if (nli_freeze.get("schema_version") != "sku-directional-choice-nli-v2"
            or nli_summary.get("inference_run") is not False
            or nli_summary.get("scores_or_gate_outputs_created") is not False
            or nli_summary.get("labels_read") is not False
            or nli_summary.get("gold_or_worker_responses_read") is not False
            or nli_summary.get("input_manifest_sha256") != proof_inputs.get("source_gate_manifest_sha256")
            or nli_summary.get("selected_task_count") != proof_manifest.get("input_task_count")):
        raise ValueError("choice-NLI v2 preparation is not the matching blind DEV-only source freeze")
    for name, digest in nli_summary.get("output_sha256", {}).items():
        if sha_file(nli_dir / name) != digest:
            raise ValueError("choice-NLI v2 source preparation SHA differs: " + name)

    task_states = indexed(read_rows(output_root / "task-proof-states.jsonl"), "task_id", "literal task proof state")
    source_receipts = indexed(read_rows(output_root / "task-source-verification.jsonl"), "task_id", "raw task source receipt")
    proof_records = read_rows(output_root / "proof-bundle/proofs.jsonl")
    proof_by_pair = {f"{row.get('forward_task_id')}|{row.get('reverse_task_id')}": row
                     for row in proof_records}
    source_gate = ROOT / proof_inputs["source_gate_artifact"]
    source_requests = indexed(read_rows(source_gate / "requests.jsonl"), "request_id", "original source request")
    model_pairs = indexed(read_rows(nli_dir / "model-pairs.jsonl"), "pair_id", "NLI semantic pair")
    bindings = read_rows(nli_dir / "validation-bindings.jsonl")
    bindings_by_pair = defaultdict(list)
    seen_tasks = set()
    pair_task_states = defaultdict(list)
    nli_task_rows = []
    for binding in bindings:
        pair_id = binding.get("pair_id")
        task_binding = binding.get("task_binding") or {}
        task_id = task_binding.get("task_id")
        pair = model_pairs.get(pair_id)
        state = task_states.get(task_id)
        receipt = source_receipts.get(task_id)
        if pair is None or state is None or task_id in seen_tasks:
            raise ValueError("choice-NLI source binding has a missing/duplicate pair or task foreign key")
        seen_tasks.add(task_id)
        vector = [member.get("whole_option") for member in pair.get("candidate_vector", [])]
        facts = binding.get("source_selected_fact") or {}
        request = source_requests.get(task_binding.get("request_id"))
        if request is None or receipt is None:
            raise ValueError("choice-NLI binding lacks original source request or raw-source receipt")
        if (binding.get("request_binding", {}).get("target_document_id") != request.get("target_document_id")
                or binding.get("request_binding", {}).get("target_document_sha256") != request.get("target_document_sha256")
                or facts.get("value_span") != receipt.get("selected_value_span")
                or facts.get("value") != receipt.get("selected_value")
                or (state.get("status") in {"support", "conflict"}
                    and binding.get("exact_name_binding_status") != "unique")):
            raise ValueError("choice-NLI target source scope or selected raw fact differs from verified task receipt")
        if (task_binding.get("request_id") != state.get("request_id")
                or task_binding.get("case_id") != state.get("case_id")
                or task_binding.get("au_row_key") != state.get("au_row_key")
                or task_binding.get("direction") != state.get("direction")
                or facts.get("axis_name") != state.get("axis_name")
                or facts.get("value") != state.get("selected_value")
                or vector != state.get("ordered_family")
                or pair.get("direction") != state.get("direction")
                or pair.get("option_values") != state.get("ordered_family")):
            raise ValueError("NLI context candidate vector or source task/fact binding differs from raw proof")
        if state.get("status") in {"support", "conflict"}:
            proof_pair = proof_by_pair.get(state.get("proof_pair_id"))
            if proof_pair is None:
                raise ValueError("protected NLI task has no unchanged strict literal paired proof")
            target_span_key = ("au_selected_value" if state["direction"] == "rakuten_to_au"
                               else "rakuten_selected_value")
            target_span = ((proof_pair.get("raw_span_receipts") or {}).get(target_span_key) or {})
            target_id = "raw-span:" + hashlib.sha256(canonical({key: target_span.get(key) for key in
                ("raw_file", "sha256", "locator", "start", "end", "quote")})).hexdigest()
            if (binding.get("exact_name_binding_status") != "unique"
                    or binding.get("exact_name_target_fact_ids") != [target_id]
                    or target_id not in binding.get("target_fact_ids", [])):
                raise ValueError("protected literal proof target selected raw member differs from NLI source binding")
        bindings_by_pair[pair_id].append(binding)
        pair_task_states[pair_id].append(state)
        nli_task_rows.append({"pair_id": pair_id, "context_id": pair.get("context_id"),
            "cache_key": pair.get("cache_key"), "task_id": task_id,
            "request_id": task_binding.get("request_id"), "case_id": task_binding.get("case_id"),
            "au_row_key": task_binding.get("au_row_key"), "direction": state["direction"],
            "axis_name": state["axis_name"], "selected_value": state["selected_value"],
            "option_values": state["ordered_family"], "literal_status": state["status"],
            "literal_proof_verified": state["literal_proof_verified"],
            "skip_task_classification": state["status"] in {"support", "conflict"},
            "production_adoption_allowed": False})
    if seen_tasks != set(task_states):
        raise ValueError("choice-NLI preparation does not retain every original source task exactly once")
    pair_protections = []
    for pair_id, pair in sorted(model_pairs.items()):
        pair_bindings = bindings_by_pair.get(pair_id, [])
        states = pair_task_states.get(pair_id, [])
        if not pair_bindings or len(pair_bindings) != len(states):
            raise ValueError("choice-NLI pair lacks complete task bindings")
        # All tasks must be individually proven for this semantic cache entry to
        # skip inference. Different wanted members may have different states.
        protected = all(state.get("status") in {"support", "conflict"}
                        and state.get("literal_proof_verified") is True
                        for state in states)
        pair_protections.append({"pair_id": pair_id, "context_id": pair.get("context_id"),
            "cache_key": pair.get("cache_key"), "task_ids": sorted(state["task_id"] for state in states),
            "request_ids": sorted({state["request_id"] for state in states}),
            "task_count": len(states), "task_statuses": sorted({state["status"] for state in states}),
            "all_bound_tasks_have_verified_literal_state": protected,
            "statuses_may_differ_across_desired_bindings": True,
            "skip_classification": protected,
            "production_adoption_allowed": False})
    replace_rows(output_root / "nli-v2-task-protection.jsonl", nli_task_rows)
    replace_rows(output_root / "nli-v2-context-protection.jsonl", pair_protections)
    nli_pins = {name: sha_file(nli_dir / name) for name in ("freeze.json", "summary.json", "model-pairs.jsonl",
                "paired-contexts.jsonl", "validation-bindings.jsonl", "source-documents.jsonl", "preparation-errors.jsonl")}
    manifest_path = output_root / "manifest.json"
    manifest = read_json(manifest_path)
    manifest.update({"nli_v2_preparation_pins": nli_pins,
        "nli_v2_task_protection_sha256": sha_file(output_root / "nli-v2-task-protection.jsonl"),
        "nli_v2_context_protection_sha256": sha_file(output_root / "nli-v2-context-protection.jsonl"),
        "nli_v2_ready_pair_count": len(model_pairs),
        "nli_v2_source_task_binding_count": len(bindings),
        "nli_v2_context_skip_count": sum(row["skip_classification"] for row in pair_protections),
        "nli_v2_context_unprotected_count": sum(not row["skip_classification"] for row in pair_protections),
        "nli_v2_inference_run": False, "nli_v2_responses_read": False})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                             encoding="utf-8")
    return {"nli_v2_ready_pair_count": len(model_pairs),
            "nli_v2_source_task_binding_count": len(bindings),
            "nli_v2_context_skip_count": sum(row["skip_classification"] for row in pair_protections),
            "nli_v2_context_unprotected_count": sum(not row["skip_classification"] for row in pair_protections)}


def finalize_output_hash_map(output_root: Path) -> dict:
    names = ("inputs/manifest.json", "proof-bundle/manifest.json", "proof-bundle/proofs.jsonl",
             "task-source-verification.jsonl", "request-proof-statuses.jsonl", "protected-requests.jsonl",
             "task-proof-states.jsonl", "protected-nli-contexts.jsonl",
             "nli-v2-task-protection.jsonl", "nli-v2-context-protection.jsonl", "gate-policy.json")
    current = {name: {"sha256": sha_file(output_root / name), "bytes": (output_root / name).stat().st_size}
               for name in names}
    path = output_root / "manifest.json"
    manifest = read_json(path)
    historical = {
        "request_statuses_sha256": {"digest": manifest.get("request_statuses_sha256"),
            "state": "superseded_by_canonical_output_file", "canonical_path": "request-proof-statuses.jsonl"},
        "protected_nli_contexts_sha256": {"digest": manifest.get("protected_nli_contexts_sha256"),
            "state": "superseded_by_canonical_output_file", "canonical_path": "protected-nli-contexts.jsonl"},
    }
    manifest.update({"canonical_output_hashes": current,
        "canonical_output_hashes_finalized": True,
        "source_code_pins": {
            "experiments/sku-matching/prepare_dev_literal_family_proofs_v1.py": sha_file(HERE / "prepare_dev_literal_family_proofs_v1.py"),
            "experiments/sku-matching/validate_dev_literal_family_proofs_v1.py": sha_file(HERE / "validate_dev_literal_family_proofs_v1.py"),
            "experiments/sku-matching/evaluate_dev_source_bound_whole_sku_gate_v1.py": sha_file(HERE / "evaluate_dev_source_bound_whole_sku_gate_v1.py"),
            "experiments/sku-matching/sku_literal_family_proof_v1.py": sha_file(HERE / "sku_literal_family_proof_v1.py"),
            "experiments/sku-matching/build_sku_literal_family_proofs_v1.py": sha_file(HERE / "build_sku_literal_family_proofs_v1.py")},
        "historical_hash_alias_lifecycle": historical,
        "output_manifest_rule": "Use canonical_output_hashes path/bytes/SHA; legacy aliases may describe superseded intermediate files."})
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")
    return {"canonical_output_file_count": len(current),
            "canonical_output_hashes_finalized": True,
            "historical_hash_alias_count": len(historical)}


def write_gate_policy(output_root: Path) -> dict:
    path = output_root / "gate-policy.json"
    if path.exists():
        raise FileExistsError("refusing to overwrite reviewed gate policy draft")
    policy = {"schema_version": "dev-source-bound-whole-sku-policy-v1",
        "review_state": "root_review_required_before_any_model_or_baseline_evaluation",
        "evaluation_scope": "frozen DEV40; all 504 captured AU rows; all 1,924 original bidirectional axis tasks",
        "variants": {
            "choice_nli_full": {
                "description": "full replacement using source-bound direct-option NLI per original task, with strict literal task proofs protected",
                "task_projection": "Use only the judgment for the exact original source-selected whole option. A decisive judgment must have passed the unchanged source-bound NLI v2 validator, including exact raw source family-member citation and opposite-target raw axis-label and selected-value citations. Support and explicit conflict remain experimental model evidence; semantic truth is not certified.",
                "axis_name_rule": "Do not require equal cross-store axis-name strings for semantic support/conflict. Exact-name matching is required only inside strict literal-family proof generation.",
                "unknown_rule": "Unresolved scope, unknown relation, missing exact selected member, missing/invalid citations, or absent response remains unknown. Alternate option, reranker winner, or raw inequality alone never becomes conflict.",
                "protected_literal_rule": "For a task with a unique source-verified strict literal-family support/conflict result, use that exact result and skip model classification only when every task bound to its prepared semantic context is similarly protected. Different desired members may have different literal states."},
            "baseline_literal_overlay": {
                "description": "frozen CPU full-condition baseline with unanimous request-level strict literal proofs overlaid",
                "overlay_rule": "Override the baseline request state only when every original task bound to that request has a verified strict literal-family proof and all those proof states agree. Otherwise keep the validated baseline request result.",
                "baseline_rule": "Require the unchanged whole-condition resolver's request/hash binding, original-option winner, and literal quote validation. A different winning option is unknown, never conflict."}},
        "request_aggregation": "Per request, strict proof override is eligible only for complete unanimous support or conflict across all bound tasks; mixed/missing/unknown proof remains unknown for overlay.",
        "row_aggregation": "Use every original forward Rakuten-to-AU and reverse AU-to-Rakuten task for the fixed AU row. Any validated conflict makes the row conflict; otherwise any unknown makes it unknown; support requires every original condition in both directions to support.",
        "case_finish": "Accept only exactly one supported fixed AU row and explicit conflict for every other captured row. Preserve all rows regardless of stock; any unknown competitor, omission, multiple support, or incomplete row/task coverage drops the case.",
        "source_scope": "No v7 decisions, contradictions, core/residual hints, inferred axis mappings, normalization shortcuts, labels, test split, or prior predictions enter source proof or gate structure.",
        "citation_handling": "Keep citations as validator-verified typed raw-source/body evidence references. Never fabricate target-body offsets for raw selected-axis fields.",
        "comparison_policy": "Compare full choice-NLI replacement plus protected literal task proofs against baseline plus unanimous literal request overlay; do not tune fallback or guardrails on DEV outcomes.",
        "production_adoption_allowed": False, "labels_read": False, "test_split_read": False,
        "model_inference_run": False, "whole_gate_evaluation_run": False}
    with path.open("x", encoding="utf-8") as f:
        json.dump(policy, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
    return {"gate_policy_sha256": sha_file(path), "review_state": policy["review_state"]}


def run(gate_inputs: Path = DEFAULT_GATE_INPUTS, source_bundle: Path = DEFAULT_SOURCE_BUNDLE,
        output_root: Path = DEFAULT_OUTPUT) -> dict:
    prepared = prepare_inputs(gate_inputs, source_bundle, output_root)
    build_spec = importlib.util.spec_from_file_location("sku_literal_family_builder", BUILDER)
    builder = importlib.util.module_from_spec(build_spec)
    assert build_spec and build_spec.loader
    sys.modules[build_spec.name] = builder
    build_spec.loader.exec_module(builder)
    bundle_dir = output_root / "proof-bundle"
    manifest = builder.build(output_root, source_bundle, bundle_dir)
    task_verification = verify_all_task_source_families(output_root, source_bundle)
    prepared["task_source_receipts"] = read_rows(output_root / "task-source-verification.jsonl")
    status_summary = aggregate_request_statuses(output_root, prepared, bundle_dir / "proofs.jsonl")
    run_manifest = {"schema_version": "sku-next-dev-literal-family-run-v1",
        "phase": "source_only_raw_span_proofs_complete",
        "inputs_manifest_sha256": sha_file(prepared["input_dir"] / "manifest.json"),
        "proof_bundle_manifest_sha256": sha_file(bundle_dir / "manifest.json"),
        "proofs_sha256": sha_file(bundle_dir / "proofs.jsonl"),
        "request_statuses_sha256": sha_file(output_root / "request-proof-statuses.jsonl"),
        "protected_nli_contexts_sha256": sha_file(output_root / "protected-nli-contexts.jsonl"),
        "input_case_count": manifest["input_subset_case_count"],
        "input_task_count": manifest["task_count"], "input_row_count": manifest["row_plan_count"],
        "input_request_count": len(prepared["selected_requests"]),
        "literal_proof_status_counts": manifest["proof_status_counts"],
        **task_verification,
        **status_summary,
        "labels_read": False, "test_split_read": False, "predictions_read": False,
        "model_inference_run": False, "whole_gate_evaluation_run": False,
        "v7_hints_used_as_proof": False, "production_adoption_allowed": False}
    write_json(output_root / "manifest.json", run_manifest)
    return read_json(output_root / "manifest.json")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-inputs", type=Path, default=DEFAULT_GATE_INPUTS)
    parser.add_argument("--source-bundle", type=Path, default=DEFAULT_SOURCE_BUNDLE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--add-task-source-verification", action="store_true",
                        help="augment an already-built source-only bundle with per-task raw-family receipts")
    parser.add_argument("--regenerate-request-aggregates", action="store_true",
                        help="rebuild request/task protection sidecars after source receipts are finalized")
    parser.add_argument("--attach-nli-v2", action="store_true",
                        help="join literal task states to frozen source-only NLI v2 pair identities")
    parser.add_argument("--finalize-output-hashes", action="store_true",
                        help="record the canonical current output path/bytes/SHA map and superseded alias history")
    parser.add_argument("--write-gate-policy", action="store_true",
                        help="write a source-only policy draft for root review before evaluation")
    args = parser.parse_args()
    if args.attach_nli_v2:
        print(json.dumps(attach_nli_v2_protection_map(args.output.resolve(), DEFAULT_NLI_V2),
                         ensure_ascii=False, sort_keys=True))
        raise SystemExit(0)
    if args.finalize_output_hashes:
        print(json.dumps(finalize_output_hash_map(args.output.resolve()), ensure_ascii=False, sort_keys=True))
        raise SystemExit(0)
    if args.write_gate_policy:
        print(json.dumps(write_gate_policy(args.output.resolve()), ensure_ascii=False, sort_keys=True))
        raise SystemExit(0)
    if args.regenerate_request_aggregates:
        tasks = {row["task_id"]: row for row in read_rows(args.output / "inputs/tasks.jsonl")}
        requests = {row["request_id"]: row for row in read_rows(DEFAULT_GATE_INPUTS / "requests.jsonl")}
        prepared = {"selected_tasks": tasks, "selected_requests": requests,
                    "task_source_receipts": read_rows(args.output / "task-source-verification.jsonl")}
        result = aggregate_request_statuses(args.output.resolve(), prepared,
                                            args.output.resolve() / "proof-bundle/proofs.jsonl")
        manifest_path = args.output.resolve() / "manifest.json"
        run_manifest = read_json(manifest_path)
        run_manifest.update(result)
        for name in ("request-proof-statuses.jsonl", "protected-requests.jsonl",
                     "task-proof-states.jsonl", "protected-nli-contexts.jsonl"):
            run_manifest[name.replace(".jsonl", "_sha256")] = sha_file(args.output.resolve() / name)
        manifest_path.write_text(json.dumps(run_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                                 encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        raise SystemExit(0)
    if args.add_task_source_verification:
        print(json.dumps(verify_all_task_source_families(args.output.resolve(), args.source_bundle.resolve()),
                         ensure_ascii=False, sort_keys=True))
        raise SystemExit(0)
    print(json.dumps(run(args.gate_inputs.resolve(), args.source_bundle.resolve(), args.output.resolve()),
                     ensure_ascii=False, sort_keys=True))
