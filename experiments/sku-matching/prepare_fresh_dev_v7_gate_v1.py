#!/usr/bin/env python3
"""Prepare a bounded, source-bound fresh-v7 DEV handoff for the whole-SKU gate.

This adapter only freezes gate input plus untrusted v7 hints. It never loads
labels, runs inference, assigns a match result, or converts producer status into
proof/conflict. The current `--selection-only` mode can stage the original
full-condition gate inputs while a fresh producer handoff is being prepared.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
DEFAULT_BASE = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1"
DEFAULT_OUT = DEFAULT_BASE / "full-gate-inputs-v1"
DEFAULT_SOURCE_BUNDLE = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
DEFAULT_RUNTIME_DIR = ROOT / ".lab-output/sku-next-dev-v7-runtime-20261013-v1"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def write_rows(path: Path, values):
    with path.open("xb") as f:
        for value in values:
            f.write(canonical(value) + b"\n")


def unique(rows, key, label):
    result = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value or value in result:
            raise ValueError("invalid or duplicate " + label + " identity")
        result[value] = row
    return result


def load_selection(selection_dir: Path):
    plan = read_json(selection_dir / "selection-plan.json")
    members_path = selection_dir / "membership.jsonl"
    members = read_rows(members_path)
    if plan.get("schema_version") != "sku-next-dev-v7-subset-plan-v1":
        raise ValueError("unexpected bounded DEV selection schema")
    if plan.get("membership_sha256") != sha_file(members_path):
        raise ValueError("frozen DEV membership SHA differs")
    if (plan.get("selected_case_count") != 40 or plan.get("selected_product_count") != 20
            or plan.get("product_count") != 20 or plan.get("cases_per_product") != 2
            or plan.get("labels_read") is not False or plan.get("test_split_read") is not False
            or plan.get("predictions_read") is not False):
        raise ValueError("selection does not carry the required DEV-only frozen scope")
    if len(members) != 40 or len({x.get("case_id") for x in members}) != 40:
        raise ValueError("membership does not contain 40 unique case IDs")
    products = Counter(x.get("au_product_id") for x in members)
    if len(products) != 20 or set(products.values()) != {2}:
        raise ValueError("membership does not contain exactly two cases for each of 20 products")
    return plan, members


def load_source(source_dir: Path, source_bundle_dir: Path):
    manifest_path = source_dir / "manifest.json"
    manifest = read_json(manifest_path)
    root_manifest = read_json(source_dir.parent / "manifest.json")
    inventory = read_json(source_bundle_dir.parent / "inventory.json")
    if (root_manifest.get("schema_version") != "full-condition-adapter-v2"
            or root_manifest.get("missing_referenced_sources") != []
            or root_manifest.get("labels_read") is not False
            or root_manifest.get("model_inference_run") is not False
            or not isinstance(root_manifest.get("source_span_count_verified"), int)
            or inventory.get("status") != "source_bundle_ready"
            or inventory.get("source_provenance", {}).get("missing_referenced_sources")):
        raise ValueError("full-condition source bundle/span verification is not ready")
    if (manifest.get("schema_version") != "full-condition-inputs-v2"
            or manifest.get("split") != "dev" or manifest.get("independent_test") is not False
            or manifest.get("labels_read") is not False or manifest.get("model_inference_run") is not False
            or manifest.get("production_adoption_allowed") is not False):
        raise ValueError("source is not the declared label-blind full-condition DEV input")
    names = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl", "case-plans.jsonl")
    actual = {name: sha_file(source_dir / name) for name in names}
    if any(manifest.get("outputs_sha256", {}).get(name) != digest for name, digest in actual.items()):
        raise ValueError("full-condition DEV source output SHA differs from manifest")
    cases = unique(read_rows(source_dir / "case-plans.jsonl"), "case_id", "source case")
    rows = read_rows(source_dir / "row-plans.jsonl")
    tasks = unique(read_rows(source_dir / "tasks.jsonl"), "task_id", "source task")
    requests = unique(read_rows(source_dir / "requests.jsonl"), "request_id", "source request")
    documents = unique(read_rows(source_dir / "documents.jsonl"), "document_id", "source document")
    if len(cases) != 1370:
        raise ValueError("full DEV source case population is not 1,370")
    if len({str(c["au_product_id"]) for c in cases.values()}) != 107:
        raise ValueError("full DEV source product population is not 107")
    validate_full_coverage(cases, rows, tasks, requests)
    original_cases, original_case_line_sha, original_case_line_number, original_case_file_sha = load_original_cases(source_bundle_dir, manifest)
    if set(original_cases) != set(cases):
        raise ValueError("full condition case IDs differ from frozen original source inputs")
    return manifest, actual, cases, rows, tasks, requests, documents, original_cases, original_case_line_sha, original_case_line_number, original_case_file_sha, root_manifest


def load_original_cases(source_bundle_dir: Path, source_manifest):
    root = source_bundle_dir / ".lab-output/sku-independent-real-20261011"
    source_index_path = source_bundle_dir.parent / "source-index.json"
    indexed = {row["path"]: row["sha256"] for row in read_json(source_index_path)}
    cases, line_shas, source_line_numbers, source_file_hashes = {}, {}, {}, {}
    for version in ("v2", "v3"):
        base = f".lab-output/sku-independent-real-20261011/eval-{version}"
        rel = base + "/run-token/inputs/cases.jsonl"
        expected = source_manifest.get("source_inputs", {}).get(rel)
        path = root / f"eval-{version}/run-token/inputs/cases.jsonl"
        if not expected or sha_file(path) != expected:
            raise ValueError("frozen original source case input SHA differs")
        source_file_hashes[rel] = expected
        groups_path = root / f"eval-{version}/groups-token/groups.jsonl"
        groups_rel = base + "/groups-token/groups.jsonl"
        if indexed.get(groups_rel) != sha_file(groups_path):
            raise ValueError("frozen original DEV group-map SHA differs")
        group_map = {}
        for group_row in read_rows(groups_path):
            group = group_row.get("group", group_row)
            dossier = str(group.get("dossier_id", group_row.get("dossier_id", "")))
            if not dossier or dossier in group_map:
                raise ValueError("frozen DEV group map has duplicate/missing dossier identity")
            group_map[dossier] = group
        for line_number, line in enumerate(path.read_bytes().splitlines(), 1):
            if not line:
                continue
            row = json.loads(line)
            cid = row.get("case_id")
            group = group_map.get(str(row.get("dossier_id")))
            if not group or group.get("excluded") or group.get("split") != "dev":
                continue
            if not isinstance(cid, str) or cid in cases:
                raise ValueError("duplicate or malformed original source case identity")
            cases[cid] = row
            line_shas[cid] = sha_bytes(line)
            source_line_numbers[cid] = line_number
    return cases, line_shas, source_line_numbers, source_file_hashes


def validate_full_coverage(cases, row_plans, tasks, requests):
    row_ids = set()
    rows_by_case = defaultdict(list)
    task_case_ids = set()
    for task in tasks.values():
        task_case_ids.add(task.get("case_id"))
        if task.get("request_id") not in requests:
            raise ValueError("source task references missing full-condition request")
    for row in row_plans:
        pair = (row.get("case_id"), row.get("au_row_key"))
        if pair in row_ids or pair[0] not in cases:
            raise ValueError("duplicate or unbound source AU row plan")
        row_ids.add(pair)
        rows_by_case[pair[0]].append(row)
        bindings = row.get("bindings")
        if not isinstance(bindings, list):
            raise ValueError("source row lacks task bindings")
        bound_tasks = []
        for binding in bindings:
            tid = binding.get("task_id")
            task = tasks.get(tid)
            if not task or task.get("case_id") != pair[0] or task.get("au_row_key") != pair[1]:
                raise ValueError("source row binding does not identify its exact full-condition task")
            bound_tasks.append(task)
        rak = [x for x in bound_tasks if x.get("direction") == "rakuten_to_au"]
        au = [x for x in bound_tasks if x.get("direction") == "au_to_rakuten"]
        if (len(rak) != row.get("rakuten_axis_count") or len(au) != row.get("au_axis_count")
                or len({x.get("axis_key") for x in rak}) != len(rak)
                or len({x.get("axis_key") for x in au}) != len(au)):
            raise ValueError("source row does not cover each original axis in both directions")
        for task in bound_tasks:
            request = requests[task["request_id"]]
            raw = task.get("raw_condition_ref", {})
            if raw.get("value") not in request.get("option_values", []):
                raise ValueError("source selected whole value is absent from its complete options")
            if request.get("selected_value") != raw.get("value"):
                raise ValueError("source task and request selected values differ")
    if task_case_ids != set(cases):
        raise ValueError("source tasks do not cover exactly all full DEV cases")
    for cid, case in cases.items():
        case_rows = rows_by_case.get(cid, [])
        if len(case_rows) != case.get("total_au_rows") or {x["au_row_key"] for x in case_rows} != set(case.get("row_keys", [])):
            raise ValueError("source case does not retain every fixed AU row")
        if any(x.get("row_retained_regardless_of_stock") is not True for x in case_rows):
            raise ValueError("source case does not explicitly retain all AU stock states")


def filter_source(members, cases, row_plans, tasks, requests, documents):
    member_by_case = unique(members, "case_id", "membership")
    if not set(member_by_case).issubset(cases):
        raise ValueError("frozen membership contains a case outside the full DEV source")
    selected_cases = {cid: cases[cid] for cid in member_by_case}
    expected_products = {x["au_product_id"] for x in members}
    if any(str(cases[cid]["au_product_id"]) != row["au_product_id"]
           for cid, row in member_by_case.items()):
        raise ValueError("membership product binding differs from full DEV case plan")
    chosen_rows = [x for x in row_plans if x["case_id"] in selected_cases]
    selected_row_keys = {(x["case_id"], x["au_row_key"]) for x in chosen_rows}
    chosen_tasks = {tid: task for tid, task in tasks.items()
                    if (task.get("case_id"), task.get("au_row_key")) in selected_row_keys}
    expected_task_ids = {b["task_id"] for row in chosen_rows for b in row["bindings"]}
    if set(chosen_tasks) != expected_task_ids:
        raise ValueError("selected source rows do not retain their complete source task set")
    req_ids = {x["request_id"] for x in chosen_tasks.values()}
    chosen_requests = {rid: requests[rid] for rid in req_ids}
    doc_ids = {x["target_document_id"] for x in chosen_requests.values()}
    chosen_documents = {did: documents[did] for did in doc_ids}
    if set(chosen_documents) != doc_ids:
        raise ValueError("selected requests lack source documents")
    if {x["case_id"] for x in chosen_rows} != set(selected_cases):
        raise ValueError("selected rows do not cover every membership case")
    return selected_cases, chosen_rows, chosen_tasks, chosen_requests, chosen_documents


def validate_runtime_manifest(trace_manifest_path: Path, experiment_freeze_path: Path,
                              input_freeze_path: Path, run_receipt_path: Path,
                              source_manifest, source_bundle_dir: Path,
                              trace_path: Path, runtime_dir: Path):
    trace_manifest = read_json(trace_manifest_path)
    freeze = read_json(experiment_freeze_path)
    input_freeze = read_json(input_freeze_path)
    receipt = read_json(run_receipt_path)
    if (trace_manifest.get("schema_version") != "sku_v7_all_row_trace_manifest_v1"
            or trace_manifest.get("split") != "dev" or trace_manifest.get("independent_test") is not False
            or trace_manifest.get("labels_read") is not False or trace_manifest.get("prediction_inputs_read") is not False
            or trace_manifest.get("no_source_proof_or_gate_conflict_is_claimed") is not True):
        raise ValueError("fresh v7 trace manifest does not declare the expected blind DEV trace scope")
    if (freeze.get("split") != "dev" or freeze.get("independent_test") is not False
            or freeze.get("labels_read") is not False
            or freeze.get("device") != "cpu" or freeze.get("precision") != "fp32"):
        raise ValueError("fresh producer experiment freeze is not completed blind DEV CPU inference")
    if (input_freeze.get("case_count") != 1370 or input_freeze.get("product_count") != 107
            or input_freeze.get("labels_read") is not False or input_freeze.get("prediction_inputs_read") is not False):
        raise ValueError("fresh producer input freeze does not bind all 1,370 DEV cases / 107 products")
    if (receipt.get("split") != "dev" or receipt.get("independent_test") is not False
            or receipt.get("labels_read") is not False or receipt.get("similarity_stage") != "completed_once"
            or receipt.get("trace_stage") != "completed_once" or receipt.get("score_stage_run") is not False):
        raise ValueError("fresh producer run receipt is outside the one-pass similarity/trace phase")
    trace_sha = sha_file(trace_path)
    if (trace_sha != trace_manifest.get("trace_sha256")
            or trace_sha != trace_manifest.get("outputs_sha256", {}).get("all-row-trace.jsonl")):
        raise ValueError("fresh v7 all-row trace SHA differs from its manifest")
    if trace_manifest.get("runtime_freeze_sha256") != sha_file(experiment_freeze_path):
        raise ValueError("fresh v7 trace does not bind the experiment runtime freeze")
    pinned_files = (
        (runtime_dir / "producer/v7/align_sku_conditions.py", trace_manifest.get("producer_code_sha256"), "archived producer code"),
        (runtime_dir / "run_similarity.py", freeze.get("runtime_adapter_sha256"), "runtime adapter"),
        (runtime_dir / "trace_all_rows.py", trace_manifest.get("trace_generator_sha256"), "trace generator"),
        (runtime_dir / "model-metadata/model-manifest.json", freeze.get("model_manifest_sha256"), "model manifest"),
    )
    for path, expected_sha, description in pinned_files:
        if not isinstance(expected_sha, str) or sha_file(path) != expected_sha:
            raise ValueError("fresh v7 " + description + " file SHA differs from its freeze")
    runtime_inputs = runtime_dir / "run/inputs"
    for name in ("cases.jsonl", "products.jsonl"):
        if sha_file(runtime_inputs / name) != input_freeze.get("outputs_sha256", {}).get(name):
            raise ValueError("fresh v7 runtime input file differs from the input freeze")
    receipt_trace_sha = receipt.get("all_run_output_sha256", {}).get("out/trace/all-row-trace.jsonl")
    if receipt_trace_sha != trace_sha:
        raise ValueError("fresh v7 run receipt does not bind the all-row trace output")
    if freeze.get("input_freeze_sha256") != sha_file(input_freeze_path) or receipt.get("input_freeze_sha256") != sha_file(input_freeze_path):
        raise ValueError("fresh producer run does not bind the input freeze")
    if (trace_manifest.get("producer_code_sha256") != freeze.get("producer_code_sha256")
            or trace_manifest.get("archive_member_sha256") != freeze.get("producer_archive_member_sha256")
            or trace_manifest.get("runtime_freeze_sha256") != receipt.get("inference_pin_manifest_sha256")
            or trace_manifest.get("inference_pins", {}).get("device") != "cpu"
            or trace_manifest.get("inference_pins", {}).get("revision") != freeze.get("revision")
            or trace_manifest.get("inference_pins", {}).get("weights_sha256") != freeze.get("model_weights_sha256")):
        raise ValueError("fresh producer code/model/runtime pins differ across manifests")
    runtime_deps = freeze.get("runtime_dependencies")
    if not isinstance(runtime_deps, dict) or not runtime_deps:
        raise ValueError("fresh producer runtime dependency pins are missing")
    combined = input_freeze.get("outputs_sha256", {})
    if trace_manifest.get("inputs", {}).get("cases_sha256") != combined.get("cases.jsonl") or trace_manifest.get("inputs", {}).get("products_sha256") != combined.get("products.jsonl"):
        raise ValueError("fresh trace input file SHA differs from input freeze")
    if freeze.get("input_hashes") != {"cases.jsonl": combined.get("cases.jsonl"), "products.jsonl": combined.get("products.jsonl")}:
        raise ValueError("experiment runtime freeze input hashes differ")
    source_hashes = source_manifest.get("source_inputs", {})
    case_bindings = trace_manifest.get("source_case_record_line_bindings")
    if not isinstance(case_bindings, dict) or len(case_bindings) != 1370:
        raise ValueError("fresh v7 trace lacks all original case-record source bindings")
    for cid, binding in case_bindings.items():
        source_input = binding.get("source_input")
        if source_hashes.get(source_input) != binding.get("source_sha256"):
            raise ValueError("fresh v7 case binding references an unpinned source input")
    product_bindings = trace_manifest.get("source_product_record_line_bindings")
    if not isinstance(product_bindings, dict) or len(product_bindings) != 107:
        raise ValueError("fresh v7 trace lacks all original product-record source bindings")
    for binding in product_bindings.values():
        if source_hashes.get(binding.get("source_input")) != binding.get("source_sha256"):
            raise ValueError("fresh v7 product binding references an unpinned source input")
    return trace_manifest, freeze, input_freeze, receipt


def same_source_span(left, right):
    """Compare provenance spans; only canonicalize path separators mechanically."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    a, b = dict(left), dict(right)
    for obj in (a, b):
        if isinstance(obj.get("raw_file"), str):
            obj["raw_file"] = obj["raw_file"].replace("\\", "/")
    return a == b


def condition_index(handoff, source_rows, source_tasks, source_requests):
    """Validate one complete source handoff row and return status hints by task ID."""
    case_id, row_key = handoff.get("case_id"), handoff.get("au_row_key")
    row = next((x for x in source_rows if x.get("case_id") == case_id and x.get("au_row_key") == row_key), None)
    if row is None:
        raise ValueError("fresh handoff points to an unknown fixed source AU row")
    bound = [source_tasks[b["task_id"]] for b in row["bindings"]]
    rak_tasks = {x["axis_key"]: x for x in bound if x["direction"] == "rakuten_to_au"}
    au_tasks = {x["raw_condition_ref"]["axis_name"]: x for x in bound if x["direction"] == "au_to_rakuten"}
    if handoff.get("rakuten_axes") is None or handoff.get("au_row_axes") is None:
        raise ValueError("fresh handoff omits complete source-bound raw axes")
    seen_rak, seen_au = set(), set()
    rakuten_axes = list(handoff["rakuten_axes"].values()) if isinstance(handoff["rakuten_axes"], dict) else handoff["rakuten_axes"]
    for axis in rakuten_axes:
        key = axis.get("axis_key")
        task = rak_tasks.get(key)
        if task is None or key in seen_rak:
            raise ValueError("fresh Rakuten axis identity is unknown or duplicated")
        raw = task["raw_condition_ref"]
        request = source_requests[task["request_id"]]
        if (axis.get("axis_label") != raw.get("axis_label") or axis.get("value") != raw.get("value")
                or not same_source_span(axis.get("value_span"), raw.get("value_span"))
                or axis.get("family_values") != request.get("option_values")):
            raise ValueError("fresh Rakuten axis/value/family/source span differs from frozen whole source")
        seen_rak.add(key)
    if seen_rak != set(rak_tasks):
        raise ValueError("fresh handoff omitted a complete original Rakuten axis")
    for axis in handoff["au_row_axes"]:
        name = axis.get("axis_name")
        task = au_tasks.get(name)
        if task is None or name in seen_au:
            raise ValueError("fresh AU axis identity is unknown or duplicated")
        raw = task["raw_condition_ref"]
        if axis.get("value") != raw.get("value") or not same_source_span(axis.get("value_span"), raw.get("value_span")):
            raise ValueError("fresh AU axis/value/source span differs from frozen whole source")
        seen_au.add(name)
    if seen_au != set(au_tasks):
        raise ValueError("fresh handoff omitted a complete original AU row axis")
    options = handoff.get("au_axis_options")
    if not isinstance(options, dict):
        raise ValueError("fresh handoff omits complete AU axis option arrays")
    for name, task in au_tasks.items():
        request = source_requests[task["request_id"]]
        if options.get(name) != request.get("option_values"):
            raise ValueError("fresh AU whole-option list differs from frozen source task")
    conditions = handoff.get("conditions")
    if not isinstance(conditions, list):
        raise ValueError("fresh handoff conditions must be an array")
    represented = set()
    task_hints = defaultdict(list)
    for condition in conditions:
        kind = condition.get("kind")
        status = condition.get("status")
        if status not in {"one_sided", "aligned", "residual", "model_candidate", "pending", "contradiction", "symmetric_unresolved"}:
            raise ValueError("unsupported fresh v7 status")
        if kind == "rakuten":
            key = condition.get("axis_key")
            source_axis = next((x for x in rakuten_axes if x["axis_key"] == key), None)
            if not source_axis or key in represented or condition.get("value") != source_axis["value"]:
                raise ValueError("fresh condition does not bind exactly one original Rakuten whole axis")
            represented.add(key)
            target_name = condition.get("au_axis")
            if status == "one_sided":
                if target_name is not None or condition.get("au_value") is not None:
                    raise ValueError("one-sided fresh condition carries a mapped AU counterpart")
            else:
                if target_name not in au_tasks or condition.get("au_value") != au_tasks[target_name]["raw_condition_ref"]["value"]:
                    raise ValueError("fresh mapped condition does not bind an exact selected AU axis")
            hint = {"producer_condition": condition, "producer_status_is_proof": False,
                    "core_residual_are_untrusted_hints": True,
                    "automatic_conflict_allowed": False, "automatic_support_allowed": False}
            task_hints[rak_tasks[key]["task_id"]].append(hint)
            if target_name is not None:
                task_hints[au_tasks[target_name]["task_id"]].append(hint)
        elif kind == "au_only":
            name = condition.get("au_axis")
            if status != "one_sided" or name not in au_tasks or condition.get("au_value") != au_tasks[name]["raw_condition_ref"]["value"]:
                raise ValueError("fresh AU-only condition does not bind an original AU row axis")
            hint = {"producer_condition": condition, "producer_status_is_proof": False,
                    "core_residual_are_untrusted_hints": True,
                    "automatic_conflict_allowed": False, "automatic_support_allowed": False}
            task_hints[au_tasks[name]["task_id"]].append(hint)
        else:
            raise ValueError("unsupported fresh condition kind")
    if represented != set(rak_tasks):
        raise ValueError("fresh conditions do not account for every original Rakuten axis exactly once")
    return dict(task_hints)


def prepare(source_dir: Path, source_bundle_dir: Path, selection_dir: Path, output_dir: Path,
            trace_path: Path | None = None, trace_manifest_path: Path | None = None,
            experiment_freeze_path: Path | None = None, input_freeze_path: Path | None = None,
            run_receipt_path: Path | None = None, decisions_path: Path | None = None,
            runtime_dir: Path = DEFAULT_RUNTIME_DIR):
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite " + str(output_dir))
    selection_plan, members = load_selection(selection_dir)
    source_manifest, source_hashes, cases, row_plans, tasks, requests, documents, original_cases, original_case_line_sha, original_case_line_number, original_case_file_sha, root_source_manifest = load_source(source_dir, source_bundle_dir)
    for member in members:
        cid = member["case_id"]
        if (member.get("source_case_line_sha256") != original_case_line_sha.get(cid)
                or member.get("selected_rakuten_source_sha256") != cases[cid].get("raw_selected_sku_source_sha256")):
            raise ValueError("frozen membership source case/SKU SHA binding differs")
        version = cases[cid].get("source_version")
        rel = f".lab-output/sku-independent-real-20261011/eval-{version}/run-token/inputs/cases.jsonl"
        if member.get("source_case_input_sha256") != source_manifest.get("source_inputs", {}).get(rel):
            raise ValueError("frozen membership input file SHA differs from source manifest")
        original = original_cases[cid]
        if (str(original.get("dossier_id")) != str(cases[cid].get("dossier_id"))
                or str(original.get("au_product_id")) != str(cases[cid].get("au_product_id"))):
            raise ValueError("frozen case identity differs from original case row")
    selected_cases, selected_rows, selected_tasks, selected_requests, selected_documents = filter_source(
        members, cases, row_plans, tasks, requests, documents)
    trace_rows, decision_rows, hint_rows = [], [], []
    runtime_hash = None
    if any(x is not None for x in (trace_path, trace_manifest_path, experiment_freeze_path, input_freeze_path, run_receipt_path, decisions_path)):
        if not all(x is not None for x in (trace_path, trace_manifest_path, experiment_freeze_path, input_freeze_path, run_receipt_path)):
            raise ValueError("fresh trace, trace manifest, runtime/input freezes, and run receipt are an all-required set")
        trace_manifest, runtime_freeze, input_freeze, run_receipt = validate_runtime_manifest(
            trace_manifest_path, experiment_freeze_path, input_freeze_path, run_receipt_path,
            source_manifest, source_bundle_dir, trace_path, runtime_dir)
        runtime_hash = sha_file(experiment_freeze_path)
        case_bindings = trace_manifest["source_case_record_line_bindings"]
        for cid, case in cases.items():
            binding = case_bindings.get(cid)
            version = case.get("source_version")
            source_path = f".lab-output/sku-independent-real-20261011/eval-{version}/run-token/inputs/cases.jsonl"
            if (not binding or binding.get("source_input") != source_path
                    or binding.get("source_line") != original_case_line_number[cid]
                    or binding.get("source_sha256") != original_case_file_sha[source_path]):
                raise ValueError("producer case-line binding differs from frozen exact source record")
        decisions = unique(read_rows(decisions_path), "case_id", "fresh decision") if decisions_path else {}
        if decisions and set(decisions) != set(cases):
            raise ValueError("fresh decisions do not cover exactly the complete full DEV source population")
        handoffs = read_rows(trace_path)
        row_plan_map = {(row["case_id"], row["au_row_key"]): row for row in row_plans}
        expected = set(row_plan_map)
        handoff_map = {}
        case_decisions = {}
        for h in handoffs:
            key = (h.get("case_id"), h.get("au_row_key"))
            if key in handoff_map or key not in expected:
                raise ValueError("fresh trace has duplicate or out-of-scope case/row identity")
            handoff_map[key] = h
            cid = key[0]
            case = cases[cid]
            if h.get("dossier_id") != case.get("dossier_id") or str(h.get("au_product_id")) != str(case.get("au_product_id")):
                raise ValueError("fresh trace changes fixed case/product identity")
            decision = {"case_id": h.get("case_id"), "dossier_id": h.get("dossier_id"),
                        "au_product_id": h.get("au_product_id"), "decision": h.get("case_decision"),
                        "top_row_key": h.get("case_top_row_key"), "reason": h.get("case_reason")}
            if decision["decision"] not in {"matched", "unmatched"} or not isinstance(decision["reason"], str) or not decision["reason"]:
                raise ValueError("fresh trace lacks a concrete producer case decision and reason")
            prior = case_decisions.setdefault(cid, decision)
            if prior != decision:
                raise ValueError("fresh trace case decision/reason differs across AU rows")
        if set(handoff_map) != expected:
            raise ValueError("fresh trace does not cover every captured AU row")
        if decisions:
            for cid, row in case_decisions.items():
                if any(decisions[cid].get(k) != row.get(k) for k in ("dossier_id", "au_product_id", "decision", "reason")):
                    raise ValueError("fresh trace decision differs from decision file")
        for (cid, row_key), h in handoff_map.items():
            source_row = row_plan_map[(cid, row_key)]
            state = h.get("state")
            allowed_states = {"contradiction", "aligned", "pending_description_check", "symmetric_unresolved", "unknown", "unresolved"}
            if isinstance(state, dict):
                state = state.get("state")
            if state not in allowed_states:
                raise ValueError("fresh trace omits or misstates the explicit per-row producer state")
            trace_rows.append({"case_id": cid, "au_row_key": h["au_row_key"],
                "producer_row_state": state,
                "pending_description_check_candidate": h.get("pending_description_check_candidate"),
                "case_top_row_key": h.get("case_top_row_key"),
                "heuristic_status_is_untrusted_hint": h.get("heuristic_status_is_untrusted_hint") is True,
                "producer_row_state_is_untrusted": True})
        for cid, case in selected_cases.items():
            decision_rows.append({**case_decisions[cid],
                "producer_decision_is_untrusted": True,
                "whole_gate_decision": None})
            for source_row in (r for r in selected_rows if r["case_id"] == cid):
                h = handoff_map[(cid, source_row["au_row_key"])]
                by_task = condition_index(h, selected_rows, selected_tasks, selected_requests)
                for tid, hints in by_task.items():
                    for hint in hints:
                        hint_rows.append({"task_id": tid, "case_id": cid,
                            "au_row_key": source_row["au_row_key"], **hint})
    output_dir.mkdir(parents=True)
    # The standard gate input files remain the exact original full-condition
    # inputs. Producer hints live in a separate task-ID-joined sidecar.
    for name, content in (("documents.jsonl", (selected_documents[k] for k in sorted(selected_documents))),
                          ("requests.jsonl", (selected_requests[k] for k in sorted(selected_requests))),
                          ("tasks.jsonl", selected_tasks.values()),
                          ("row-plans.jsonl", selected_rows),
                          ("case-plans.jsonl", selected_cases.values())):
        write_rows(output_dir / name, content)
    write_rows(output_dir / "membership.jsonl", members)
    write_json(output_dir / "selection-plan.json", selection_plan)
    if runtime_hash:
        trace_rows.sort(key=lambda x: (x["case_id"], x["au_row_key"]))
        decision_rows.sort(key=lambda x: x["case_id"])
        hint_rows.sort(key=lambda x: (x["task_id"], canonical(x["producer_condition"])))
        write_rows(output_dir / "fresh-v7-row-trace.jsonl", trace_rows)
        write_rows(output_dir / "fresh-v7-decisions.jsonl", decision_rows)
        write_rows(output_dir / "fresh-v7-task-hints.jsonl", hint_rows)
    names = ["documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl", "case-plans.jsonl",
             "membership.jsonl", "selection-plan.json"]
    if runtime_hash:
        names += ["fresh-v7-row-trace.jsonl", "fresh-v7-decisions.jsonl", "fresh-v7-task-hints.jsonl"]
    manifest = {"schema_version": "fresh-dev-v7-whole-sku-gate-prepare-v1",
        "phase": "source_only_prepare_with_v7_hints" if runtime_hash else "source_only_prepare_waiting_for_v7_handoff",
        "full_dev_source_manifest_sha256": sha_file(source_dir / "manifest.json"),
        "full_condition_root_source_manifest_sha256": sha_file(source_dir.parent / "manifest.json"),
        "source_bundle_inventory_sha256": sha_file(source_bundle_dir.parent / "inventory.json"),
        "source_span_count_verified_in_full_prep": root_source_manifest.get("source_span_count_verified"),
        "full_dev_source_file_sha256": source_hashes,
        "selection_plan_sha256": sha_file(selection_dir / "selection-plan.json"),
        "membership_sha256": sha_file(selection_dir / "membership.jsonl"),
        "runtime_manifest_sha256": runtime_hash,
        "fresh_v7_trace_sha256": sha_file(trace_path) if runtime_hash else None,
        "fresh_v7_inference_pins": trace_manifest.get("inference_pins") if runtime_hash else None,
        "fresh_v7_producer_code_sha256": trace_manifest.get("producer_code_sha256") if runtime_hash else None,
        "trace_manifest_sha256": sha_file(trace_manifest_path) if runtime_hash else None,
        "input_freeze_sha256": sha_file(input_freeze_path) if runtime_hash else None,
        "run_receipt_sha256": sha_file(run_receipt_path) if runtime_hash else None,
        "selected_case_count": len(selected_cases), "selected_product_count": len({str(x["au_product_id"]) for x in selected_cases.values()}),
        "selected_row_count": len(selected_rows), "selected_task_count": len(selected_tasks),
        "selected_request_count": len(selected_requests), "selected_document_count": len(selected_documents),
        "source_case_population_count": len(cases), "source_au_row_population_count": len(row_plans),
        "outputs_sha256": {name: sha_file(output_dir / name) for name in names},
        "all_original_bidirectional_axis_tasks_preserved": True,
        "all_captured_au_rows_including_sold_out_preserved": True,
        "fresh_v7_status_is_untrusted_metadata_only": True,
        "whole_value_equality_shortcut_used": False,
        "normalization_core_residual_used_as_proof": False,
        "labels_read": False, "test_split_read": False, "adapter_model_inference_run": False,
        "fresh_v7_producer_similarity_inference_completed": bool(runtime_hash),
        "whole_gate_evaluation_run": False, "whole_gate_scores_created": False,
        "production_adoption_allowed": False,
        "adapter_code_sha256": sha_file(Path(__file__))}
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--source-bundle-dir", type=Path, default=DEFAULT_SOURCE_BUNDLE)
    ap.add_argument("--selection-dir", type=Path, default=DEFAULT_BASE)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--trace", type=Path)
    ap.add_argument("--trace-manifest", type=Path)
    ap.add_argument("--experiment-freeze", type=Path)
    ap.add_argument("--input-freeze", type=Path)
    ap.add_argument("--run-receipt", type=Path)
    ap.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    ap.add_argument("--decisions", type=Path)
    args = ap.parse_args(argv)
    result = prepare(args.source_dir.resolve(), args.source_bundle_dir.resolve(), args.selection_dir.resolve(), args.output_dir.resolve(),
                     args.trace.resolve() if args.trace else None,
                     args.trace_manifest.resolve() if args.trace_manifest else None,
                     args.experiment_freeze.resolve() if args.experiment_freeze else None,
                     args.input_freeze.resolve() if args.input_freeze else None,
                     args.run_receipt.resolve() if args.run_receipt else None,
                     args.decisions.resolve() if args.decisions else None,
                     args.runtime_dir.resolve())
    print(json.dumps({k: result[k] for k in ("schema_version", "phase", "selected_case_count",
        "selected_product_count", "selected_row_count", "selected_task_count", "labels_read",
        "adapter_model_inference_run", "whole_gate_evaluation_run", "whole_gate_scores_created")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
