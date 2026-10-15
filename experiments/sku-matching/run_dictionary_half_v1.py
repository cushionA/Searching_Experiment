#!/usr/bin/env python3
"""Orchestrate the frozen dictionary-half trial.

This adapter consumes a pre-frozen product/source-group split, trains two
TRAIN-only dictionaries, then applies them to the held-out EVAL population.
The existing exact lookup, component, graph, and case assembly functions are
imported unchanged. It never reads grades or model outputs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from library_train_dictionary_v1 import build_dictionary, dictionary_request_state
from replay_dev_nli_policies_v1 import assemble
from run_installed_library_selection_v1 import catalogs as make_catalogs
from run_installed_library_selection_v1 import selected_state
from run_library_axis_domains_v1 import assemble_mode

SCHEMA = "sku-dictionary-half-output-freeze-v1"
PINNED_CODE = (
    "run_dictionary_half_v1.py", "library_train_dictionary_v1.py",
    "run_installed_library_selection_v1.py", "run_library_axis_domains_v1.py",
    "library_axis_domain_alignment_v1.py", "replay_dev_nli_policies_v1.py",
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
    result = []
    with path.open(encoding="utf-8") as stream:
        for n, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{n}: expected JSON object")
            result.append(value)
    return result


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for n, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{n}: expected JSON object")
            yield value


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_jsonl(path: Path, values) -> None:
    with path.open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def validate_split(split_dir: Path) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    """Return dossier->side and dossier->independent-group maps from frozen split."""
    freeze_path = split_dir / "SPLIT_FREEZE.json"
    membership_path = split_dir / "membership.jsonl"
    freeze = read_json(freeze_path)
    if freeze.get("schema_version") != "sku_dictionary_half_split_v1":
        raise ValueError("unsupported split freeze schema")
    expected_membership = freeze.get("artifact_sha256", {}).get("membership.jsonl")
    if not expected_membership or sha(membership_path) != expected_membership:
        raise ValueError("split membership hash differs from SPLIT_FREEZE")
    for name, expected in freeze.get("artifact_sha256", {}).items():
        path = split_dir / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("split artifact pin mismatch: " + name)
    sides: dict[str, str] = {}
    groups: dict[str, str] = {}
    group_sides: dict[str, set[str]] = {}
    for item in iter_jsonl(membership_path):
        dossier = item.get("dossier_id")
        side = {"TRAINdict": "train", "EVALtest": "eval"}.get(item.get("split"))
        group = item.get("source_group_id", item.get("group_id"))
        if not isinstance(dossier, str) or not dossier or side not in {"train", "eval"}:
            raise ValueError("invalid split membership row")
        if not isinstance(group, str) or not group:
            raise ValueError("membership row lacks stable source_group_id")
        if dossier in sides and (sides[dossier] != side or groups[dossier] != group):
            raise ValueError("one dossier has inconsistent split/source-group membership")
        sides[dossier], groups[dossier] = side, group
        group_sides.setdefault(group, set()).add(side)
    if not sides:
        raise ValueError("empty split membership")
    if any(len(side_set) != 1 for side_set in group_sides.values()):
        raise ValueError("a connected source group crosses TRAIN/EVAL")
    return sides, groups, freeze


def validate_plan(plan_path: Path, split_dir: Path) -> dict[str, Any]:
    plan = read_json(plan_path)
    expected_variants = ["baseline_raw", "current_pipeline", "exact_dictionary1", "exact_dictionary2",
                         "exact_dictionary1_current", "exact_dictionary2_current",
                         "exact_current_dictionary1", "exact_current_dictionary2"]
    if (plan.get("schema") != "sku-dictionary-half-plan-v1"
            or plan.get("primary") != "exact_dictionary2_current"
            or plan.get("variants") != expected_variants
            or plan.get("split_freeze_sha256") != sha(split_dir / "SPLIT_FREEZE.json")
            or plan.get("dictionary_frozen_before_eval_predictions") is not True
            or plan.get("eval_predictions_frozen_before_eval_reference_read") is not True
            or plan.get("post_grade_tuning_allowed") is not False
            or plan.get("model_calls") != 0):
        raise ValueError("experiment plan does not match the frozen dictionary-half protocol")
    return plan


def load_side_corpora(manifest_path: Path, split_dir: Path, sides: dict[str, str], wanted: str):
    """Validate each complete frozen corpus, then retain only its assigned cases."""
    corpus_manifest = read_json(manifest_path)
    if corpus_manifest.get("schema") != "sku-dictionary-half-corpus-v1":
        raise ValueError("unsupported corpus manifest")
    membership = {m["case_id"]: m for m in iter_jsonl(split_dir / "membership.jsonl")}
    records: dict[str, list] = {k: [] for k in ("requests", "documents", "rows", "cases", "originals", "products")}
    raw_views: dict[str, str] = {}
    used_paths: list[Path] = [manifest_path, split_dir / "membership.jsonl", split_dir / "SPLIT_FREEZE.json"]
    fixed_by_corpus = {}
    selected_source_case_ids: set[str] = set()
    for corpus in corpus_manifest.get("sources", []):
        name = corpus.get("name")
        required = ("source", "original", "views", "fixed_run")
        if not isinstance(name, str) or any(k not in corpus for k in required):
            raise ValueError("corpus manifest source is incomplete")
        paths = {key: Path(corpus[key]) for key in required}
        repo_root = HERE.parents[1]
        paths = {key: (path if path.is_absolute() else repo_root / path) for key, path in paths.items()}
        source, original, views_path, fixed = (paths[k] for k in required)
        fixed_freeze = validate_fixed_run(fixed, source, views_path)
        manifest = read_json(source / "manifest.json")
        input_names = ("requests.jsonl", "documents.jsonl", "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl")
        for file_name in input_names:
            if sha(source / file_name) != manifest.get("output_sha256", {}).get(file_name):
                raise ValueError(f"{name} prepared input pin mismatch: {file_name}")
        source_cases = read_jsonl(original / "cases.jsonl")
        source_products = read_jsonl(original / "products.jsonl")
        if sha(original / "cases.jsonl") != manifest.get("source_inputs", {}).get("cases.jsonl") or \
                sha(original / "products.jsonl") != manifest.get("source_inputs", {}).get("products.jsonl"):
            raise ValueError(f"{name} original source pin mismatch")
        source_case_ids = {c["case_id"] for c in source_cases}
        assigned = {cid for cid, m in membership.items() if m.get("source_name") == name and
                    m.get("split") == ("TRAINdict" if wanted == "train" else "EVALtest")}
        if not assigned.issubset(source_case_ids):
            raise ValueError(f"{name} split membership refers to missing source cases")
        source_dossiers = {m["dossier_id"] for cid, m in membership.items() if cid in assigned}
        selected_source_case_ids.update(assigned)
        original_cases_by_id = {c["case_id"]: c for c in source_cases}
        original_products_by_id = {p["dossier_id"]: p for p in source_products}
        if not source_dossiers.issubset(original_products_by_id):
            raise ValueError(f"{name} split membership refers to missing products")
        prepared_requests = read_jsonl(source / "requests.jsonl")
        prepared_docs = read_jsonl(source / "documents.jsonl")
        prepared_rows = read_jsonl(source / "row-plans.jsonl")
        prepared_cases = read_jsonl(source / "case-plans.jsonl")
        docs_full = {d["document_id"]: d for d in prepared_docs}
        if len(docs_full) != len(prepared_docs):
            raise ValueError(f"{name} has duplicate documents")
        requests_full = {r["request_id"]: r for r in prepared_requests}
        if len(requests_full) != len(prepared_requests):
            raise ValueError(f"{name} has duplicate requests")
        if {c["case_id"] for c in prepared_cases} != source_case_ids:
            raise ValueError(f"{name} prepared/original case coverage differs")
        bindings = {}
        for row in prepared_rows:
            if row.get("case_id") not in source_case_ids:
                raise ValueError(f"{name} row refers to a foreign case")
            for binding in row.get("bindings", []):
                tid = binding.get("task_id")
                if not isinstance(tid, str) or tid in bindings:
                    raise ValueError(f"{name} has duplicate/missing task binding")
                bindings[tid] = (row, binding)
        seen_tasks = set()
        for task in iter_jsonl(source / "tasks.jsonl"):
            tid = task.get("task_id")
            if not isinstance(tid, str) or tid in seen_tasks:
                raise ValueError(f"{name} task ledger has duplicate/missing task ID")
            seen_tasks.add(tid)
            pair = bindings.get(tid)
            if pair is None:
                raise ValueError(f"{name} task ledger has an unbound task")
            row, binding = pair
            req = requests_full.get(binding.get("request_id"))
            condition = task.get("raw_condition_ref", {})
            if (req is None or (task.get("case_id"), task.get("au_row_key"), task.get("request_id"),
                    task.get("direction"), task.get("axis_key")) != (
                    row.get("case_id"), row.get("au_row_key"), binding.get("request_id"),
                    req.get("direction"), binding.get("axis_key"))
                    or (condition.get("axis_name"), condition.get("value")) != (
                    req.get("axis_name"), req.get("selected_value"))):
                raise ValueError(f"{name} task/binding mismatch")
        if seen_tasks != set(bindings):
            raise ValueError(f"{name} task ledger omitted bindings")
        selected_rows = [r for r in prepared_rows if r.get("case_id") in assigned]
        selected_req_ids = {b["request_id"] for row in selected_rows for b in row.get("bindings", [])}
        selected_requests = [r for r in prepared_requests if r["request_id"] in selected_req_ids]
        if len(selected_requests) != len(selected_req_ids):
            raise ValueError(f"{name} selected rows refer to missing requests")
        selected_docs_ids = {r["target_document_id"] for r in selected_requests}
        selected_docs = [d for d in prepared_docs if d["document_id"] in selected_docs_ids]
        if len(selected_docs) != len(selected_docs_ids):
            raise ValueError(f"{name} selected requests refer to missing documents")
        req_by_id = {r["request_id"]: r for r in selected_requests}
        current_reqs = {r["request_id"]: r for r in iter_jsonl(fixed / "baseline_raw-requests.jsonl")}
        current_alt = {r["request_id"]: r for r in iter_jsonl(fixed / "mutual_unique_raw_components-requests.jsonl")}
        for rid in selected_req_ids:
            if rid not in current_reqs or rid not in current_alt:
                raise ValueError(f"{name} fixed output omits selected request")
            if current_reqs[rid].get("raw_baseline_state") != current_reqs[rid].get("state"):
                raise ValueError(f"{name} raw baseline changed")
            if current_reqs[rid]["state"] != "unknown" and current_alt[rid].get("state") != current_reqs[rid]["state"]:
                raise ValueError(f"{name} current pipeline changed known raw state")
        selected_request_rows = [r for r in prepared_requests if r["request_id"] in selected_req_ids]
        for policy_name, states_by_id, fixed_prefix in (
                ("baseline_raw", {rid: current_reqs[rid]["state"] for rid in selected_req_ids}, "baseline_raw"),
                ("mutual_unique_raw_components", {rid: current_alt[rid]["state"] for rid in selected_req_ids},
                 "mutual_unique_raw_components")):
            projected_rows, projected_cases = assemble_mode(selected_rows, selected_request_rows, states_by_id)
            fixed_rows = [r for r in iter_jsonl(fixed / f"{fixed_prefix}-rows.jsonl")
                          if r.get("case_id") in assigned]
            fixed_cases = [r for r in iter_jsonl(fixed / f"{fixed_prefix}-cases.jsonl")
                           if r.get("case_id") in assigned]
            if projected_rows != fixed_rows or projected_cases != fixed_cases:
                raise ValueError(f"{name} {policy_name} TRAIN/EVAL projection differs from frozen output")
        selected_cases = [original_cases_by_id[cid] for cid in sorted(assigned)]
        selected_prepared_cases = [c for c in prepared_cases if c["case_id"] in assigned]
        selected_products = [original_products_by_id[d] for d in sorted(source_dossiers)]
        for key, values in (("requests", selected_requests), ("documents", selected_docs),
                            ("rows", selected_rows), ("cases", selected_prepared_cases),
                            ("originals", selected_cases), ("products", selected_products)):
            records[key].extend(values)
        view_payload = read_json(views_path)
        view = view_payload.get("views", {}).get("raw")
        if not isinstance(view, dict):
            raise ValueError(f"{name} missing raw price-free view")
        used_raw = set()
        for req in selected_requests:
            used_raw.update(v for v in req.get("option_values", []) if isinstance(v, str))
            if isinstance(req.get("selected_value"), str):
                used_raw.add(req["selected_value"])
            for axis in req.get("target_selected_axes", []):
                if isinstance(axis.get("value"), str):
                    used_raw.add(axis["value"])
        for product in selected_products:
            for row in product.get("au_rows", []):
                for axis in row.get("axes", []):
                    if isinstance(axis.get("value"), str):
                        used_raw.add(axis["value"])
            for family in product.get("rakuten_product", {}).get("selector_families", []):
                used_raw.update(v for v in family.get("values", []) if isinstance(v, str))
        for case in selected_cases:
            for axis in case.get("rakuten_selected", {}).get("axes", []):
                if isinstance(axis.get("value"), str):
                    used_raw.add(axis["value"])
        missing_view_values = used_raw.difference(view)
        if missing_view_values:
            raise ValueError(f"{name} price-free view misses selected split values")
        for raw in used_raw:
            mapped = view[raw]
            if raw in raw_views and raw_views[raw] != mapped:
                raise ValueError("price-free raw view mapping differs across corpora")
            raw_views[raw] = mapped
        used_paths.extend([source / "manifest.json", *(source / n for n in input_names),
                           original / "cases.jsonl", original / "products.jsonl", views_path,
                           fixed / "RUN_FREEZE.json", fixed / "baseline_raw-requests.jsonl",
                           fixed / "baseline_raw-rows.jsonl", fixed / "baseline_raw-cases.jsonl",
                           fixed / "mutual_unique_raw_components-requests.jsonl",
                           fixed / "mutual_unique_raw_components-rows.jsonl",
                           fixed / "mutual_unique_raw_components-cases.jsonl"])
        fixed_by_corpus[name] = {"path": fixed, "freeze": fixed_freeze,
                                 "baseline": current_reqs, "current": current_alt,
                                 "selected_ids": selected_req_ids, "selected_case_ids": assigned}
    if selected_source_case_ids != {cid for cid, m in membership.items()
                                    if m.get("split") == ("TRAINdict" if wanted == "train" else "EVALtest")}:
        raise ValueError("split membership source routing failed")
    all_req_ids = [r["request_id"] for r in records["requests"]]
    if len(all_req_ids) != len(set(all_req_ids)):
        raise ValueError("request IDs collide across corpus sources")
    return records, raw_views, fixed_by_corpus, used_paths


def validate_population(source: Path, original: Path, views_path: Path,
                        selected_side: str, sides: dict[str, str]):
    """Load selected split data; stream the potentially huge task ledger.

    The source directory and original cases/products directory must already be
    split-specific. A side mismatch fails before any training or prediction.
    """
    manifest = read_json(source / "manifest.json")
    requests = read_jsonl(source / "requests.jsonl")
    documents = read_jsonl(source / "documents.jsonl")
    rows = read_jsonl(source / "row-plans.jsonl")
    cases = read_jsonl(source / "case-plans.jsonl")
    original_cases = read_jsonl(original / "cases.jsonl")
    products = read_jsonl(original / "products.jsonl")
    expected_dossiers = {d for d, side in sides.items() if side == selected_side}
    product_ids = [p.get("dossier_id") for p in products]
    if len(set(product_ids)) != len(product_ids) or set(product_ids) != expected_dossiers:
        raise ValueError(f"{selected_side} products do not exactly match frozen membership")
    case_ids = {c.get("case_id") for c in cases}
    if case_ids != {c.get("case_id") for c in original_cases}:
        raise ValueError("prepared/original split case IDs differ")
    products_by_id = {p["dossier_id"]: p for p in products}
    original_by_id = {c["case_id"]: c for c in original_cases}
    cases_by_id = {c["case_id"]: c for c in cases}
    for cid, case in cases_by_id.items():
        if case.get("dossier_id") != original_by_id[cid].get("dossier_id"):
            raise ValueError("prepared/original case dossier mismatch")
        dossier = case.get("dossier_id")
        if dossier not in expected_dossiers:
            raise ValueError("foreign case appears in split population")
        native_keys = [row.get("row_key") for row in products_by_id[dossier].get("au_rows", [])]
        if case.get("row_keys") != native_keys:
            raise ValueError("prepared case does not preserve all native AU rows")
    docs = {d.get("document_id"): d for d in documents}
    if len(docs) != len(documents) or {d.get("dossier_id") for d in documents} != expected_dossiers:
        raise ValueError("split documents do not exactly cover split products")
    if len(documents) != 2 * len(expected_dossiers):
        raise ValueError("expected one AU and one Rakuten document per product")
    if any(d.get("side") not in {"au", "rakuten"} for d in documents):
        raise ValueError("invalid document side")
    reqs = {r.get("request_id"): r for r in requests}
    if None in reqs or len(reqs) != len(requests):
        raise ValueError("missing or duplicate request ID")
    if any(r.get("target_document_id") not in docs for r in requests):
        raise ValueError("request refers to a foreign document")
    if any(docs[r["target_document_id"]]["dossier_id"] not in expected_dossiers for r in requests):
        raise ValueError("request leaks across split")
    if len({r.get("case_id") for r in rows}) == 0 and rows:
        raise ValueError("invalid rows")
    task_ids = set()
    expected_bindings = {}
    for row in rows:
        case = cases_by_id.get(row.get("case_id"))
        if not case or row.get("dossier_id") != case.get("dossier_id"):
            raise ValueError("foreign/mismatched row plan")
        for binding in row.get("bindings", []):
            tid = binding.get("task_id")
            if not isinstance(tid, str) or tid in expected_bindings:
                raise ValueError("missing or duplicate row task binding")
            expected_bindings[tid] = (row, binding)
            if binding.get("request_id") not in reqs:
                raise ValueError("row binding refers to foreign request")
    # Full streaming verification avoids materializing a multi-gigabyte tasks.jsonl.
    task_path = source / "tasks.jsonl"
    for task in iter_jsonl(task_path):
        tid = task.get("task_id")
        if not isinstance(tid, str) or tid in task_ids:
            raise ValueError("missing or duplicate task ID in streamed ledger")
        task_ids.add(tid)
        pair = expected_bindings.get(tid)
        if pair is None:
            raise ValueError("task ledger contains an unbound task")
        row, binding = pair
        req = reqs[binding["request_id"]]
        if (task.get("case_id"), task.get("au_row_key"), task.get("request_id"), task.get("direction")) != (
                row.get("case_id"), row.get("au_row_key"), binding.get("request_id"), req.get("direction")):
            raise ValueError("task/binding envelope mismatch")
    if task_ids != set(expected_bindings):
        raise ValueError("streamed task ledger omitted a row binding")
    for case in cases:
        row_keys = [r.get("au_row_key") for r in rows if r.get("case_id") == case["case_id"]]
        if row_keys != case.get("row_keys"):
            raise ValueError("case row order/coverage differs")
    if manifest.get("unique_request_count") != len(requests) or manifest.get("document_count") != len(documents):
        raise ValueError("split population manifest counts differ")
    expected_hashes = manifest.get("output_sha256", {})
    for name in ("requests.jsonl", "documents.jsonl", "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl"):
        if sha(source / name) != expected_hashes.get(name):
            raise ValueError("split population manifest output hash mismatch: " + name)
    views = read_json(views_path)
    raw_view = views.get("views", {}).get("raw")
    if not isinstance(raw_view, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                               for k, v in raw_view.items()):
        raise ValueError("invalid raw price-free view")
    # Only the selected split's strings are handed to the dictionary builder.
    needed = {v for r in requests for v in r.get("option_values", []) if isinstance(v, str)}
    needed.update(a.get("value") for r in requests for a in r.get("target_selected_axes", [])
                  if isinstance(a.get("value"), str))
    if not needed.issubset(raw_view):
        raise ValueError("price-free view misses split request values")
    return manifest, requests, documents, rows, cases, original_cases, products, raw_view, views


def validate_fixed_run(fixed: Path, source: Path, view_path: Path):
    freeze_path = fixed / "RUN_FREEZE.json"
    freeze = read_json(freeze_path)
    if freeze.get("schema") != "sku-library-volume-output-freeze-v1":
        raise ValueError("unsupported fixed classical output freeze")
    # Verify every input and output named by the fixed run's freeze.
    for path_text, expected in freeze.get("input_pins", {}).items():
        path = Path(path_text)
        if not path.is_file() or sha(path) != expected:
            raise ValueError("fixed run source/view pin mismatch: " + path_text)
    for name, expected in freeze.get("output_pins", {}).items():
        path = fixed / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("fixed run output pin mismatch: " + name)
    code_pins = freeze.get("code_pins")
    if not isinstance(code_pins, dict) or not code_pins:
        raise ValueError("fixed run freeze lacks transitive code pins")
    for name, expected in code_pins.items():
        path = HERE / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("fixed run code pin mismatch: " + name)
    return freeze


def selected_records(fixed: Path, side: str, sides: dict[str, str], docs: list[dict[str, Any]],
                     cases: list[dict[str, Any]], rows: list[dict[str, Any]], requests: list[dict[str, Any]]):
    """Select already-frozen baseline/current states after checking full run pins."""
    doc_by_id = {d["document_id"]: d for d in docs}
    request_ids = {r["request_id"] for r in requests}
    case_ids = {c["case_id"] for c in cases}
    req_rows = {}
    baseline_recs = {r["request_id"]: r for r in iter_jsonl(fixed / "baseline_raw-requests.jsonl")}
    current_recs = {r["request_id"]: r for r in iter_jsonl(fixed / "mutual_unique_raw_components-requests.jsonl")}
    # The fixed source can be larger than this split; request IDs are stable and
    # unique, while dossier membership determines which contexts are selected.
    expected_ids = {r["request_id"] for r in requests}
    if not expected_ids.issubset(baseline_recs) or not expected_ids.issubset(current_recs):
        raise ValueError("fixed classical output omits split requests")
    base_states = {rid: baseline_recs[rid]["state"] for rid in expected_ids}
    current_states = {rid: current_recs[rid]["state"] for rid in expected_ids}
    for rid in expected_ids:
        if baseline_recs[rid].get("raw_baseline_state") != baseline_recs[rid].get("state"):
            raise ValueError("baseline state is not its own immutable raw state")
        if baseline_recs[rid].get("state") != "unknown" and current_recs[rid].get("state") != baseline_recs[rid].get("state"):
            raise ValueError("current known state changed frozen raw baseline")
    chosen_rows = [r for r in rows if r.get("case_id") in case_ids]
    for row in chosen_rows:
        if any(b.get("request_id") not in expected_ids for b in row.get("bindings", [])):
            raise ValueError("split row references request outside split")
    base_row_records = {r["case_id"] + "\0" + str(r["au_row_key"]): r
                        for r in iter_jsonl(fixed / "baseline_raw-rows.jsonl") if r.get("case_id") in case_ids}
    current_row_records = {r["case_id"] + "\0" + str(r["au_row_key"]): r
                           for r in iter_jsonl(fixed / "mutual_unique_raw_components-rows.jsonl")
                           if r.get("case_id") in case_ids}
    expected_row_keys = {r["case_id"] + "\0" + str(r["au_row_key"]) for r in chosen_rows}
    if set(base_row_records) != expected_row_keys or set(current_row_records) != expected_row_keys:
        raise ValueError("fixed classical row projection differs from split row bindings")
    for key in expected_row_keys:
        if base_row_records[key].get("state") not in {"support", "conflict", "unknown"}:
            raise ValueError("invalid baseline row state")
        if base_row_records[key].get("state") != "unknown" and current_row_records[key].get("state") != base_row_records[key].get("state"):
            raise ValueError("current row projection changed known baseline state")
    return base_states, current_states, baseline_recs, current_recs, chosen_rows


def run_build(args) -> None:
    sides, groups, split_freeze = validate_split(args.split)
    plan_payload = validate_plan(args.plan, args.split) if args.plan is not None else None
    records, raw, _, source_paths = load_side_corpora(args.corpus_manifest, args.split, sides, "train")
    requests, documents, cases, originals, products = (records[k] for k in
        ("requests", "documents", "cases", "originals", "products"))
    reference_envelope = read_json(args.train_refs)
    train_refs = reference_envelope.get("references")
    if not isinstance(train_refs, dict):
        raise ValueError("TRAIN reference file must contain a references object")
    train_case_ids = {c["case_id"] for c in originals}
    train_dossiers = {p["dossier_id"] for p in products}
    if set(train_refs) != train_case_ids:
        raise ValueError("TRAIN reference case IDs must exactly cover TRAIN cases")
    if (reference_envelope.get("labels_subset") != "train_only"
            or reference_envelope.get("split_freeze_sha256") != sha(args.split / "SPLIT_FREEZE.json")):
        raise ValueError("TRAIN references are not pinned to this frozen split")
    if args.plan is not None and reference_envelope.get("plan_sha256") != sha(args.plan):
        raise ValueError("TRAIN references are not pinned to the supplied experiment plan")
    if set(reference_envelope.get("training_case_ids", [])) != train_case_ids:
        raise ValueError("reference envelope training_case_ids do not exactly equal TRAIN cases")
    if any(not isinstance(ref, dict) for ref in train_refs.values()):
        raise ValueError("invalid TRAIN reference record")
    if args.output.exists():
        raise FileExistsError(f"refusing existing dictionary directory: {args.output}")
    inputs = source_paths + [args.corpus_manifest, args.train_refs]
    if args.plan is not None:
        inputs.append(args.plan)
    input_pins = {p.resolve().as_posix(): sha(p) for p in inputs}
    code_pins = {name: sha(HERE / name) for name in PINNED_CODE}
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    group_map = {d: groups[d] for d in train_dossiers}
    for support in (1, 2):
        dictionary = build_dictionary(products, originals, requests, documents, raw, train_refs, group_map, support)
        write_json(output / f"dict{support}.json", dictionary)
    if any(sha(Path(path)) != digest for path, digest in input_pins.items()):
        raise ValueError("TRAIN input changed during dictionary build")
    if any(sha(HERE / name) != digest for name, digest in code_pins.items()):
        raise ValueError("dictionary runner/builder code changed during dictionary build")
    outputs = {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
               for p in sorted(output.iterdir()) if p.is_file()}
    freeze = {"schema": "sku-train-dictionary-freeze-v1", "split_freeze_sha256": sha(args.split / "SPLIT_FREEZE.json"),
              "membership_sha256": sha(args.split / "membership.jsonl"), "train_refs_sha256": sha(args.train_refs),
              "input_pins": input_pins, "code_pins": code_pins, "output_files": outputs,
              "experiment_plan_sha256": sha(args.plan) if args.plan is not None else None,
              "primary_variant": plan_payload.get("primary") if plan_payload else None,
              "counts": {"train_products": len(products), "train_cases": len(cases),
                         "train_requests": len(requests), "train_documents": len(documents),
                         "train_refs": len(train_refs)}, "min_support_values": [1, 2],
              "eval_labels_read": False, "validation_reads_full_source_metadata": True,
              "dictionary_builder_only_train_records": True, "grades_read": False,
              "models_loaded": [], "model_calls": 0, "scope": "historical_not_fresh"}
    write_json(output / "DICTIONARY_FREEZE.json", freeze)
    print(json.dumps({"phase": "build", "output": str(output), "counts": freeze["counts"],
                      "elapsed_seconds": time.perf_counter() - start}, ensure_ascii=False))


def run_predict(args) -> None:
    sides, _, _ = validate_split(args.split)
    plan_payload = validate_plan(args.plan, args.split) if args.plan is not None else None
    dict_freeze_path = args.dictionaries / "DICTIONARY_FREEZE.json"
    dict_freeze = read_json(dict_freeze_path)
    if dict_freeze.get("schema") != "sku-train-dictionary-freeze-v1":
        raise ValueError("dictionary freeze is missing or has wrong schema")
    if dict_freeze.get("experiment_plan_sha256"):
        if args.plan is None or sha(args.plan) != dict_freeze["experiment_plan_sha256"]:
            raise ValueError("predict phase must use the exact plan pinned during dictionary build")
    for path_text, expected in dict_freeze.get("input_pins", {}).items():
        path = Path(path_text)
        if not path.is_file() or sha(path) != expected:
            raise ValueError("dictionary TRAIN input pin mismatch: " + path_text)
    for name, item in dict_freeze.get("output_files", {}).items():
        path = args.dictionaries / name
        if not path.is_file() or sha(path) != item.get("sha256"):
            raise ValueError("dictionary frozen output pin mismatch: " + name)
    records, raw, fixed_by_corpus, source_paths = load_side_corpora(
        args.corpus_manifest, args.split, sides, "eval")
    requests, documents, rows, cases, originals, products = (records[k] for k in
        ("requests", "documents", "rows", "cases", "originals", "products"))
    base_states, current_states = {}, {}
    base_reasons, current_reasons = {}, {}
    for info in fixed_by_corpus.values():
        for rid in info["selected_ids"]:
            base_states[rid] = info["baseline"][rid]["state"]
            base_reasons[rid] = info["baseline"][rid].get("reason")
            current_states[rid] = info["current"][rid]["state"]
            current_reasons[rid] = info["current"][rid].get("reason")
    docs, catalogs = make_catalogs(requests, documents)
    dictionaries = {n: read_json(args.dictionaries / f"dict{n}.json") for n in (1, 2)}
    dict_states = {n: {} for n in (1, 2)}
    dict_traces = {n: [] for n in (1, 2)}
    dict_trace_by_id = {n: {} for n in (1, 2)}
    start = time.perf_counter()
    for req in requests:
        rid = req["request_id"]
        for n in (1, 2):
            state, reason, trace = dictionary_request_state(req, docs, catalogs, dictionaries[n], raw)
            dict_states[n][rid] = state
            record = {"request_id": rid, "state": state, "reason": reason,
                      "trace": trace, "dictionary_min_support": n,
                      "production_adoption_allowed": False}
            dict_traces[n].append(record)
            dict_trace_by_id[n][rid] = record
    states_by_variant = {
        "baseline_raw": base_states,
        "current_pipeline": current_states,
        "exact_dictionary1": {r["request_id"]: dict_states[1][r["request_id"]]
                               if base_states[r["request_id"]] == "unknown" else base_states[r["request_id"]] for r in requests},
        "exact_dictionary2": {r["request_id"]: dict_states[2][r["request_id"]]
                               if base_states[r["request_id"]] == "unknown" else base_states[r["request_id"]] for r in requests},
        "exact_dictionary1_current": {r["request_id"]: (base_states[r["request_id"]]
            if base_states[r["request_id"]] != "unknown" else dict_states[1][r["request_id"]]
            if dict_states[1][r["request_id"]] != "unknown" else current_states[r["request_id"]]) for r in requests},
        "exact_dictionary2_current": {r["request_id"]: (base_states[r["request_id"]]
            if base_states[r["request_id"]] != "unknown" else dict_states[2][r["request_id"]]
            if dict_states[2][r["request_id"]] != "unknown" else current_states[r["request_id"]]) for r in requests},
        "exact_current_dictionary1": {r["request_id"]: (base_states[r["request_id"]]
            if base_states[r["request_id"]] != "unknown" else current_states[r["request_id"]]
            if current_states[r["request_id"]] != "unknown" else dict_states[1][r["request_id"]]) for r in requests},
        "exact_current_dictionary2": {r["request_id"]: (base_states[r["request_id"]]
            if base_states[r["request_id"]] != "unknown" else current_states[r["request_id"]]
            if current_states[r["request_id"]] != "unknown" else dict_states[2][r["request_id"]]) for r in requests},
    }
    if args.output.exists():
        raise FileExistsError(f"refusing existing EVAL output directory: {args.output}")
    input_paths = source_paths + [args.corpus_manifest, args.dictionaries / "DICTIONARY_FREEZE.json",
                                  args.dictionaries / "dict1.json", args.dictionaries / "dict2.json"]
    if args.plan is not None:
        input_paths.append(args.plan)
    input_pins = {p.resolve().as_posix(): sha(p) for p in input_paths}
    code_pins = {name: sha(HERE / name) for name in PINNED_CODE}
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    records_by_variant: dict[str, list[dict[str, Any]]] = {}
    row_counts, case_counts = {}, {}
    for variant, states in states_by_variant.items():
        request_records = []
        for req in requests:
            rid = req["request_id"]
            if base_states[rid] != "unknown":
                decision_source = "immutable_exact_baseline"
                reason = base_reasons[rid]
            elif variant == "baseline_raw":
                decision_source, reason = "exact_baseline_unknown", base_reasons[rid]
            elif variant == "current_pipeline":
                decision_source, reason = "frozen_current_pipeline", current_reasons[rid]
            elif "dictionary" in variant:
                n = 2 if variant.endswith("2") or variant.endswith("2_current") else 1
                dstate = dict_trace_by_id[n][rid]["state"]
                dreason = dict_trace_by_id[n][rid]["reason"]
                if variant.startswith("exact_dictionary"):
                    if variant.endswith("_current"):
                        if dstate != "unknown":
                            decision_source, reason = "dictionary", dreason
                        else:
                            decision_source, reason = "frozen_current_pipeline", current_reasons[rid]
                    else:
                        decision_source, reason = (("dictionary" if dstate != "unknown" else "dictionary_unknown"), dreason)
                elif variant.startswith("exact_current"):
                    if current_states[rid] != "unknown":
                        decision_source, reason = "frozen_current_pipeline", current_reasons[rid]
                    else:
                        decision_source, reason = (("dictionary" if dstate != "unknown" else "dictionary_unknown"), dreason)
                else:
                    if dstate != "unknown":
                        decision_source, reason = "dictionary", dreason
                    else:
                        decision_source, reason = "frozen_current_pipeline", current_reasons[rid]
            else:
                decision_source, reason = "frozen_current_pipeline", current_reasons[rid]
            item = {"request_id": rid, "state": states[rid], "raw_baseline_state": base_states[rid],
                    "raw_baseline_reason": base_reasons[rid], "current_pipeline_state": current_states[rid],
                    "current_pipeline_reason": current_reasons[rid], "decision_source": decision_source,
                    "reason": reason, "production_adoption_allowed": False}
            if variant.startswith("exact_dictionary") or variant.startswith("exact_current_dictionary"):
                n = 2 if variant.endswith("2") or variant.endswith("2_current") else 1
                item.update({"dictionary_state": dict_states[n][rid], "dictionary_min_support": n})
            request_records.append(item)
        row_results, case_results = assemble_mode(rows, requests, states)
        records_by_variant[variant] = request_records
        write_jsonl(output / f"{variant}-requests.jsonl", request_records)
        write_jsonl(output / f"{variant}-rows.jsonl", row_results)
        write_jsonl(output / f"{variant}-cases.jsonl", case_results)
        row_counts[variant] = dict(Counter(r["state"] for r in row_results))
        case_counts[variant] = dict(Counter(c["disposition"] for c in case_results))
    for n in (1, 2):
        write_jsonl(output / f"dictionary{n}-traces.jsonl", dict_traces[n])
    write_jsonl(output / "eval-requests.jsonl", requests)
    write_jsonl(output / "eval-documents.jsonl", documents)
    write_jsonl(output / "eval-row-plans.jsonl", rows)
    write_jsonl(output / "eval-case-plans.jsonl", cases)
    # Baseline/current case projections must match each corpus's frozen run.
    for variant, fixed_name in (("baseline_raw", "baseline_raw"), ("current_pipeline", "mutual_unique_raw_components")):
        actual = {c["case_id"]: c for c in read_jsonl(output / f"{variant}-cases.jsonl")}
        for info in fixed_by_corpus.values():
            fixed_cases = {c["case_id"]: c for c in iter_jsonl(info["path"] / f"{fixed_name}-cases.jsonl")
                           if c.get("case_id") in info["selected_case_ids"]}
            for cid, expected_case in fixed_cases.items():
                if actual.get(cid) != expected_case:
                    raise ValueError(f"{variant} EVAL case projection differs from fixed output: {cid}")
    if any(sha(Path(path)) != digest for path, digest in input_pins.items()):
        raise ValueError("an EVAL, dictionary, or fixed state input changed during prediction")
    if any(sha(HERE / name) != digest for name, digest in code_pins.items()):
        raise ValueError("runner/builder/frozen policy code changed during prediction")
    output_files = {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
                    for p in sorted(output.iterdir()) if p.is_file()}
    freeze = {"schema": SCHEMA, "scope_status": "historical_not_fresh", "split": "eval",
              "input_pins": input_pins, "code_pins": code_pins,
              "fixed_run_freeze_sha256_by_corpus": {name: sha(info["path"] / "RUN_FREEZE.json")
                                                     for name, info in fixed_by_corpus.items()},
              "dictionary_freeze_sha256": sha(dict_freeze_path), "output_files": output_files,
              "experiment_plan_sha256": sha(args.plan) if args.plan is not None else None,
              "primary_variant": plan_payload.get("primary") if plan_payload else None,
              "counts": {"eval_products": len(products), "eval_cases": len(cases),
                         "eval_requests": len(requests), "eval_rows": len(rows),
                         "eval_documents": len(documents), "rows_by_variant": row_counts,
                         "cases_by_variant": case_counts},
              "variants": list(states_by_variant), "primary_variant": "exact_dictionary2_current",
              "baseline_and_current_projection_equal_fixed_outputs": True,
              "eval_labels_read": False, "validation_reads_full_source_metadata": True,
              "grades_read": False, "models_loaded": [], "model_calls": 0,
              "dictionary_training_on_eval": False, "case_assembly": "existing assemble_mode",
              "unknown_competitors_cannot_certify_unique": True, "production_adoption_allowed": False,
              "elapsed_seconds": time.perf_counter() - start}
    write_json(output / "EVAL_OUTPUT_FREEZE.json", freeze)
    print(json.dumps({"phase": "predict", "output": str(output), "counts": freeze["counts"]}, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("build", "predict"), required=True)
    p.add_argument("--split", "--split-dir", dest="split", required=True, type=Path, help="frozen split root")
    p.add_argument("--corpus-manifest", required=True, type=Path,
                   help="manifest of immutable source bundles and fixed output runs")
    p.add_argument("--output", required=True, type=Path, help="new immutable output directory")
    p.add_argument("--train-refs", type=Path, help="TRAIN-only reference rows (build phase)")
    p.add_argument("--dictionaries", type=Path, help="frozen dictionary directory (predict phase)")
    p.add_argument("--plan", type=Path, help="optional frozen experiment plan to pin by SHA256")
    return p


def main() -> None:
    args = parser().parse_args()
    if args.phase == "build":
        if args.train_refs is None:
            raise SystemExit("--train-refs is required for build")
        run_build(args)
    else:
        if args.dictionaries is None:
            raise SystemExit("--dictionaries is required for predict")
        run_predict(args)


if __name__ == "__main__":
    main()
