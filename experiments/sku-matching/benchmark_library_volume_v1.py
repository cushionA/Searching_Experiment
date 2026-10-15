#!/usr/bin/env python3
"""Bounded, label-blind replay of frozen request states at larger volumes.

Repeated cases are only a throughput/memory load test, never a larger accuracy
population. Each product is replayed whole, and assemble() is reused unchanged.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
POP = ROOT / ".lab-output/sku-classical-parser-20261015-v1/population-inputs"
AXRUN = ROOT / ".lab-output/sku-library-axis-domains-20261015-v1/run-v1"
COMP = ROOT / ".lab-output/sku-library-components-20261015-v1/run-v3"
BASE = ROOT / ".lab-output/sku-installed-library-matching-20261015-v1/price-free/selection-v1"
EXPECTED = {"requests": 30169, "documents": 214, "cases": 1370,
            "rows": 32325, "bindings": 132136, "products": 107}
VARIANTS = {
    "price_free_raw": (BASE, "raw"),
    "compound_helper": (COMP, "native_punctuation"),
    "axis_compound_main": (AXRUN, "mutual_unique_raw_components"),
}
REPLAY_SOURCE = ROOT / "experiments/sku-matching/replay_dev_nli_policies_v1.py"


def frozen_assemble():
    """Execute the unmodified assemble/row_state AST from the pinned source file.

    The replay module imports unrelated model tooling. Extracting only these exact
    function nodes avoids initializing those dependencies while preserving the
    original gate implementation byte-for-byte at the AST level.
    """
    source = REPLAY_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(REPLAY_SOURCE))
    wanted = [node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name in {"row_state", "assemble"}]
    if {node.name for node in wanted} != {"row_state", "assemble"}:
        raise ValueError("replay source does not expose expected assembly functions")
    namespace = {"defaultdict": defaultdict}
    code = compile(ast.Module(body=wanted, type_ignores=[]), str(REPLAY_SOURCE), "exec")
    exec(code, namespace)
    return namespace["assemble"]


assemble = frozen_assemble()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def peak_rss_observed_bytes() -> int:
    return max(rss_bytes(), current_rss_bytes() or 0)


def current_rss_bytes() -> int | None:
    statm = Path("/proc/self/statm")
    if not statm.is_file():
        return None
    pages = int(statm.read_text().split()[1])
    return pages * os.sysconf("SC_PAGE_SIZE")


def frozen_variant(name: str) -> dict[str, Any]:
    directory, stem = VARIANTS[name]
    request_path = directory / f"{stem}-requests.jsonl"
    row_path = directory / f"{stem}-rows.jsonl"
    case_path = directory / f"{stem}-cases.jsonl"
    expected = directory / "OUTPUT_FREEZE.json"
    if not expected.is_file():
        raise FileNotFoundError(expected)
    freeze = json.loads(expected.read_text(encoding="utf-8"))
    pins = freeze.get("output_pins", {})
    for path in (request_path, row_path, case_path):
        if path.name not in pins or sha(path) != pins[path.name]:
            raise ValueError(f"frozen output pin missing/mismatched: {path}")
    states: dict[str, str] = {}
    for r in iter_jsonl(request_path):
        if r["request_id"] in states:
            raise ValueError(f"duplicate frozen request state: {name}")
        states[r["request_id"]] = r["state"]
    if any(s not in {"support", "conflict", "unknown"} for s in states.values()):
        raise ValueError(f"invalid/duplicate frozen request states in {name}")
    rows = {}
    for r in iter_jsonl(row_path):
        key = (r["case_id"], r["au_row_key"])
        if key in rows:
            raise ValueError(f"duplicate frozen row state in {name}")
        rows[key] = (r["state"], r["condition_count"])
    cases = {}
    for r in iter_jsonl(case_path):
        if r["case_id"] in cases:
            raise ValueError(f"duplicate frozen case state in {name}")
        cases[r["case_id"]] = (r["decision"], r.get("au_row_key"))
    return {"states": states, "rows": rows, "cases": cases, "freeze": freeze,
            "full_generation_seconds": freeze["elapsed_seconds"]}


def load_sources() -> dict[str, Any]:
    started = time.perf_counter()
    manifest = json.loads((POP / "manifest.json").read_text(encoding="utf-8"))
    names = ("requests.jsonl", "documents.jsonl", "row-plans.jsonl", "case-plans.jsonl", "tasks.jsonl")
    sources = {n: POP / n for n in names}
    for n, digest in manifest["output_sha256"].items():
        p = POP / n
        if not p.is_file() or sha(p) != digest:
            raise ValueError(f"frozen population input hash mismatch: {p}")
    for run_dir in (AXRUN, COMP):
        freeze = json.loads((run_dir / "OUTPUT_FREEZE.json").read_text(encoding="utf-8"))
        for name, digest in freeze["input_pins"].items():
            p = ROOT / name
            if not p.is_file() or sha(p) != digest:
                raise ValueError(f"frozen run input pin mismatch: {p}")
    docs_by_id, request_ids, task_ids = {}, set(), set()
    documents_count = requests_count = task_count = 0
    for d in iter_jsonl(sources["documents.jsonl"]):
        documents_count += 1
        if d["document_id"] in docs_by_id:
            raise ValueError("duplicate original document IDs")
        docs_by_id[d["document_id"]] = d["dossier_id"]
    cases_count = 0
    cases_by_product: dict[str, list[str]] = defaultdict(list)
    case_to_product = {}
    for case in iter_jsonl(sources["case-plans.jsonl"]):
        cases_count += 1
        product = case["dossier_id"]
        if case["case_id"] in case_to_product:
            raise ValueError("duplicate original case IDs")
        cases_by_product[product].append(case["case_id"])
        case_to_product[case["case_id"]] = product
    rows_count = bindings_count = 0
    rows_by_product: dict[str, list[dict]] = defaultdict(list)
    bound_task_ids = set()
    bound_request_ids = set()
    for source_row in iter_jsonl(sources["row-plans.jsonl"]):
        rows_count += 1
        row = {"case_id": source_row["case_id"], "dossier_id": source_row["dossier_id"],
               "au_row_key": source_row["au_row_key"],
               "bindings": [{"request_id": b["request_id"]} for b in source_row["bindings"]]}
        bindings_count += len(row["bindings"])
        product = row["dossier_id"]
        if case_to_product.get(row["case_id"]) != product:
            raise ValueError("row plan detached from original product/case plan")
        for binding in source_row["bindings"]:
            tid, rid = binding["task_id"], binding["request_id"]
            if tid in bound_task_ids:
                raise ValueError("original task bound more than once")
            bound_task_ids.add(tid)
            bound_request_ids.add(rid)
        rows_by_product[product].append(row)
    reqs_by_product: dict[str, list[dict]] = defaultdict(list)
    for request in iter_jsonl(sources["requests.jsonl"]):
        requests_count += 1
        rid = request["request_id"]
        if rid in request_ids:
            raise ValueError("duplicate original request IDs")
        request_ids.add(rid)
        product = docs_by_id[request["target_document_id"]]
        reqs_by_product[product].append({"request_id": rid})
    for task in iter_jsonl(sources["tasks.jsonl"]):
        task_count += 1
        tid = task["task_id"]
        if tid in task_ids:
            raise ValueError("duplicate original task IDs")
        task_ids.add(tid)
    counts = {"requests": requests_count, "documents": documents_count, "cases": cases_count,
              "rows": rows_count, "bindings": bindings_count, "products": len(cases_by_product)}
    if counts != EXPECTED or task_count != EXPECTED["bindings"]:
        raise ValueError(f"frozen population counts differ: {counts}, tasks={task_count}")
    if bound_task_ids != task_ids or bound_request_ids != request_ids:
        raise ValueError("original rows/tasks/requests are not fully bound")
    products = sorted(cases_by_product)
    if set(products) != set(rows_by_product) or set(products) != set(reqs_by_product):
        raise ValueError("population does not cover complete product groups")
    return {"manifest": manifest, "request_ids": request_ids, "cases_by_product": cases_by_product,
            "rows_by_product": rows_by_product, "reqs_by_product": reqs_by_product,
            "products": products, "counts": counts, "load_seconds": time.perf_counter() - started}


def checked_freeze(name: str) -> dict:
    directory, stem = VARIANTS[name]
    freeze_path = directory / "OUTPUT_FREEZE.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if name == "price_free_raw":
        for filename, digest in freeze["input_pins"].items():
            path = POP / filename
            if not path.is_file() or sha(path) != digest:
                raise ValueError(f"baseline input pin mismatch: {path}")
        for filename, digest in freeze["output_pins"].items():
            path = directory / filename
            if not path.is_file() or sha(path) != digest:
                raise ValueError(f"baseline output pin mismatch: {path}")
    return freeze


def run(args: argparse.Namespace) -> None:
    output: Path = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    if not 1 <= args.max_seconds <= 60:
        raise ValueError("--max-seconds must be in [1, 60]")
    if not 1 <= args.max_repeats <= 40:
        raise ValueError("--max-repeats must be in [1, 40]")
    if not 1 <= args.products_per_segment <= 107:
        raise ValueError("--products-per-segment must be in [1, 107]")
    if any(n < 1 or n > 50000 for n in args.targets):
        raise ValueError("targets must be in [1, 50000]")
    output.mkdir(parents=True, exist_ok=False)
    benchmark_start = time.perf_counter()
    deadline = benchmark_start + args.max_seconds
    sources = load_sources()
    variants = args.variants or list(VARIANTS)
    freezes: dict[str, dict] = {}
    loaded: dict[str, dict] = {}
    expected_rows_by_product: dict[str, dict[str, dict]] = {}
    expected_cases_by_product: dict[str, dict[str, dict]] = {}
    case_to_product = {cid: product for product, cids in sources["cases_by_product"].items() for cid in cids}
    for name in variants:
        freezes[name] = checked_freeze(name)
        loaded[name] = frozen_variant(name)
        states, rows, cases = loaded[name]["states"], loaded[name]["rows"], loaded[name]["cases"]
        if len(states) != EXPECTED["requests"] or set(states) != sources["request_ids"]:
            raise ValueError(f"request state coverage differs for {name}")
        if len(rows) != EXPECTED["rows"] or len(cases) != EXPECTED["cases"]:
            raise ValueError(f"frozen row/case outputs are incomplete for {name}")
        expected_rows_by_product[name] = defaultdict(dict)
        for (case_id, row_key), value in rows.items():
            product = case_to_product[case_id]
            expected_rows_by_product[name][product][(case_id, row_key)] = value
        expected_cases_by_product[name] = defaultdict(dict)
        for case_id, value in cases.items():
            product = case_to_product[case_id]
            expected_cases_by_product[name][product][case_id] = value
    replay_pin = freezes["axis_compound_main"]["code_pins"].get("replay_dev_nli_policies_v1.py")
    if replay_pin is None or sha(REPLAY_SOURCE) != replay_pin:
        raise ValueError("assemble source hash does not match the frozen axis-run code pin")

    # A single append-only checkpoint stream survives an interrupted run and bounds
    # each measured segment to complete products only.
    checkpoint = output / "segments.jsonl"
    results: dict[str, dict] = {}
    stop_reason = None
    checkpoint_stream = checkpoint.open("x", encoding="utf-8")
    try:
        for name in variants:
            states = loaded[name]["states"]
            for target in sorted(set(args.targets)):
                requested_repeats = (target + sources["counts"]["cases"] - 1) // sources["counts"]["cases"]
                planned_repeats = min(requested_repeats, args.max_repeats)
                per_product_rows = sources["rows_by_product"]
                per_product_reqs = sources["reqs_by_product"]
                repeats_done = 0
                cases_done = rows_done = bindings_done = 0
                wall_total = cpu_total = 0.0
                max_sampled_rss = 0
                state_equal = True
                segment_index = 0
                key = f"{name}:{target}"
                for repeat in range(planned_repeats):
                    pass_cases = pass_rows = pass_bindings = 0
                    for offset in range(0, len(sources["products"]), args.products_per_segment):
                        product_batch = sources["products"][offset:offset + args.products_per_segment]
                        seg_wall0, seg_cpu0 = time.perf_counter(), time.process_time()
                        seg_cases = seg_rows = seg_bindings = 0
                        for product in product_batch:
                            product_rows = per_product_rows[product]
                            product_reqs = per_product_reqs[product]
                            row_out, case_out = assemble(product_rows, product_reqs, states)
                            row_proj = {(r["case_id"], r["au_row_key"]): (r["state"], r["condition_count"]) for r in row_out}
                            case_proj = {r["case_id"]: (r["decision"], r.get("au_row_key")) for r in case_out}
                            expected_r = expected_rows_by_product[name][product]
                            expected_c = expected_cases_by_product[name][product]
                            same = row_proj == expected_r and case_proj == expected_c
                            state_equal = state_equal and same
                            if not same:
                                raise ValueError(f"reassembled predictions differ from frozen states: {name}/{product}")
                            seg_cases += len(case_out)
                            seg_rows += len(row_out)
                            seg_bindings += sum(r["condition_count"] for r in row_out)
                        seg_wall = time.perf_counter() - seg_wall0
                        seg_cpu = time.process_time() - seg_cpu0
                        segment_index += 1
                        max_sampled_rss = max(max_sampled_rss, current_rss_bytes() or 0)
                        record = {"variant": name, "target_case_count": target, "repeat_index": repeat + 1,
                            "segment_index": segment_index, "product_ids": product_batch,
                            "cases": seg_cases, "rows": seg_rows, "condition_bindings": seg_bindings,
                            "elapsed_wall_seconds": seg_wall, "cpu_seconds": seg_cpu,
                            "same_frozen_prediction_states": True,
                            "segment_over_30_seconds": seg_wall > 30,
                            "process_peak_rss_bytes": peak_rss_observed_bytes(), "sampled_current_rss_bytes": current_rss_bytes()}
                        checkpoint_stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                        checkpoint_stream.flush()
                        wall_total += seg_wall; cpu_total += seg_cpu
                        pass_cases += seg_cases; pass_rows += seg_rows; pass_bindings += seg_bindings
                        if seg_wall > 30:
                            stop_reason = "one_product_safe_segment_exceeded_30_seconds; checkpoint saved"
                            break
                        if time.perf_counter() >= deadline:
                            stop_reason = "max_seconds reached; checkpoint saved"
                            break
                    cases_done += pass_cases; rows_done += pass_rows; bindings_done += pass_bindings
                    if pass_cases == sources["counts"]["cases"]:
                        if (pass_rows, pass_bindings) != (sources["counts"]["rows"], sources["counts"]["bindings"]):
                            raise ValueError("a complete population pass omitted original rows or condition bindings")
                        repeats_done += 1
                    if stop_reason:
                        break
                results[key] = {"variant": name, "requested_case_count": target,
                    "planned_full_population_repeats": requested_repeats,
                    "max_repeats_cap": args.max_repeats,
                    "completed_full_population_repeats": repeats_done,
                    "actual_replayed_cases": cases_done, "actual_replayed_rows": rows_done,
                    "actual_replayed_condition_bindings": bindings_done,
                    "requested_target_reached": cases_done >= target,
                    "same_prediction_states_as_frozen": state_equal,
                    "gate_reassembly_wall_seconds": wall_total, "gate_reassembly_cpu_seconds": cpu_total,
                    "cases_per_gate_wall_second": cases_done / wall_total if wall_total else None,
                    "rows_per_gate_wall_second": rows_done / wall_total if wall_total else None,
                    "condition_bindings_per_gate_wall_second": bindings_done / wall_total if wall_total else None,
                    "max_sampled_current_rss_bytes": max_sampled_rss,
                    "process_peak_rss_bytes": peak_rss_observed_bytes(), "status": "partial" if stop_reason else "complete",
                    "segment_count": segment_index}
                if stop_reason:
                    break
            if stop_reason:
                break
    finally:
        checkpoint_stream.close()

    full_run_reference = {}
    for name in variants:
        full_run_reference[name] = {"native_population_cases": EXPECTED["cases"],
            "full_run_seconds_in_frozen_freeze": loaded[name]["full_generation_seconds"],
            "basis": {"price_free_raw": "frozen normalization/selection run; no graph/phrase parsing",
                      "compound_helper": "frozen phrase-component run; fixed native DEV population",
                      "axis_compound_main": "frozen axis-graph run with diagnostic component overlay; fixed native DEV population"}[name],
            "not_replayed_at_volume_targets": True}
    summary = {"schema": "sku-library-volume-benchmark-v1", "kind": "throughput_load_test_only",
        "accuracy_population": False, "duplicated_replays_are_not_new_examples": True,
        "labels_or_grade_read": False, "model_calls": 0,
        "population": {**sources["counts"], "manifest_sha256": sha(POP / "manifest.json"),
                       "source_load_wall_seconds": sources["load_seconds"]},
        "variants": results, "full_run_generation_reference": full_run_reference,
        "method": {"reassembly_function": "replay_dev_nli_policies_v1.assemble (unchanged)",
            "source_input": "frozen population requests, documents, case-plans, row-plans, tasks",
            "frozen_request_state_sources": {n: str(VARIANTS[n][0].relative_to(ROOT)) + "/" + VARIANTS[n][1] + "-requests.jsonl" for n in variants},
            "whole_product_groups_per_segment": args.products_per_segment,
            "row_and_condition_coverage_preserved": True,
            "prediction_equality_projection": "each replayed product's row state/condition_count and case decision/row key equals its frozen output",
            "max_seconds": args.max_seconds, "max_repeats": args.max_repeats,
            "segment_over_30s_behavior": "checkpoint after product-safe segment and stop"},
        "stop_reason": stop_reason, "elapsed_wall_seconds_total": time.perf_counter() - benchmark_start,
        "process_peak_rss_bytes": peak_rss_observed_bytes(), "checkpoint_file": str(checkpoint.relative_to(ROOT)),
        "reassembly_source": {"file": str(REPLAY_SOURCE.relative_to(ROOT)), "sha256": sha(REPLAY_SOURCE),
            "loaded_functions": ["row_state", "assemble"], "ast_nodes_unmodified": True,
            "unrelated_model_imports_initialized": False}}
    with (output / "benchmark.json").open("x", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, allow_nan=False); f.write("\n")
    print(json.dumps({"output": str(output.relative_to(ROOT)), "stop_reason": stop_reason,
        "variants": results, "full_run_generation_reference": full_run_reference,
        "process_peak_rss_bytes": peak_rss_observed_bytes()}, ensure_ascii=False))


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--targets", nargs="+", type=int, default=[5000, 10000, 50000])
    parser.add_argument("--variants", nargs="+", choices=tuple(VARIANTS), default=None)
    parser.add_argument("--max-seconds", type=int, default=60)
    parser.add_argument("--max-repeats", type=int, default=37)
    parser.add_argument("--products-per-segment", type=int, default=10)
    return parser.parse_args()


if __name__ == "__main__":
    run(arguments())
