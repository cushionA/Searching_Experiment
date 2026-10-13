#!/usr/bin/env python3
"""Local-only prepare/import/assembly for bounded GPU continuation.

This program never calls Kaggle, submits a job, loads model weights, reads a
grade, or chooses products using labels. Worker artifacts exclude source joins.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

import nextdata_exact_cache_resume_v1 as R

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT / ".lab-output/sku-nextdata-table-20261013-v1"
GPU_DIR = ROOT / ".lab-output/sku-nextdata-table-kaggle-20261013-v1"
SOURCE = ROOT / ".lab-output/sku-volume-unused-test-20261013-v1"


def catalog_args(args):
    return R.Catalog(args.input_file, args.root_freeze, args.sourceproof,
                     args.coverage_file, args.preflight_receipt)


def add_sources(parser):
    parser.add_argument("--input-file", type=Path, default=ORIGINAL / "prepared-v1/public-paired-inputs.jsonl")
    parser.add_argument("--root-freeze", type=Path, default=ORIGINAL / "root-public-population-freeze-v1.json")
    parser.add_argument("--sourceproof", type=Path, default=ORIGINAL / "prepared-v1/public-description-sourceproof.json")
    parser.add_argument("--coverage-file", type=Path, default=GPU_DIR / "tokenizer-preflight-v1/pair-token-coverage.jsonl")
    parser.add_argument("--preflight-receipt", type=Path, default=GPU_DIR / "tokenizer-preflight-v1/PREFLIGHT_RECEIPT.json")


def export_worker_state(args, index, plan, output):
    """Exact source-only local staging. No network or notebook submission."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(index, output / "index.sqlite")
    shutil.copyfile(args.coverage_file, output / "pair-token-coverage.jsonl")
    shutil.copyfile(args.preflight_receipt, output / "PREFLIGHT_RECEIPT.json")
    public_plan = {k: v for k, v in plan.items() if k != "local_product_receipts"}
    source_db = R.connect(index)
    try:
        origin = dict(source_db.execute("SELECT key,value FROM metadata"))
    finally:
        source_db.close()
    public_plan.update(original_input_sha256=R.PINNED_INPUT_SHA, model_identities={k: R.model_identity(k) for k in R.KINDS},
                       index_sha256=R.sha(output / "index.sqlite"), original_bundle_manifest_sha256=R.ORIGINAL_BUNDLE_SHA)
    public_plan["numeric_continuation_allowed"] = origin.get("v2_origin_status") == "frozen_terminal_v2_imported"
    R.write_json(output / "worker-plan.json", public_plan)
    for name in ("nextdata_exact_cache_resume_v1.py", "kaggle_nextdata_exact_resume_v1.py"):
        shutil.copyfile(Path(__file__).parent / name, output / name)
    inventory = {str(p.relative_to(output)): {"size_bytes": p.stat().st_size, "sha256": R.sha(p)}
                 for p in output.rglob("*") if p.is_file()}
    R.write_json(output / "BUNDLE_MANIFEST.json", {"schema_version": "sku-exact-resume-source-only-state-v1",
                 "labels_included": False, "private_source_joins_included": False, "files": inventory,
                 "original_bundle_manifest_sha256": R.ORIGINAL_BUNDLE_SHA,
                 "original_input_sha256": R.PINNED_INPUT_SHA})
    return {"manifest_sha256": R.sha(output / "BUNDLE_MANIFEST.json"), "file_count": len(inventory),
            "size_bytes": sum(e["size_bytes"] for e in inventory.values()), "labels_included": False}


def prepare(args):
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    catalog = catalog_args(args)
    print(R.canonical({"stage": "original-catalog-verified", "totals": catalog.totals}), flush=True)
    args.output_dir.mkdir(parents=True)
    index = args.output_dir / "index.sqlite"
    statistics = R.build_index(index, catalog)
    closure = R.product_closure(args.input_file, args.membership, args.row_plans, args.tasks, args.bindings)
    R.write_json(args.output_dir / "private-product-closure.json", closure)
    db = R.connect(index)
    try:
        R.verify_index_identity(db)
        imported = {}
        if args.prior_dir is not None:
            if args.prior_receipt is None:
                raise ValueError("ROOT terminal fixed-version recovery receipt required")
            snapshot = R.snapshot_original_v2(args.prior_dir, args.prior_receipt, args.output_dir / "original-v2-raw")
            imported = R.import_original(db, catalog, snapshot)
        plan = R.bounded_plan(db, closure, args.max_unique_requests)
    finally:
        db.close()
    R.write_json(args.output_dir / "LOCAL_PLAN.json", plan)
    worker = export_worker_state(args, index, plan, args.output_dir / "worker-state")
    receipt = {"schema_version": "sku-exact-resume-local-preparation-v1", "labels_read": False,
               "numeric_inference_performed": False, "network_calls_performed": False,
               "original_input_sha256": R.PINNED_INPUT_SHA, "original_root_freeze_sha256": R.PINNED_ROOT_SHA,
               "preflight_coverage_sha256": R.PINNED_COVERAGE_SHA,
               "index_sha256": R.sha(index), "model_identities": {k: R.model_identity(k) for k in R.KINDS},
               "statistics": statistics, "original_import": imported, "closure_totals": closure["totals"],
               "shard_count": len(plan["shards"]), "planned_unique_request_count": plan["planned_unique_request_count"],
               "max_unique_requests_per_shard": args.max_unique_requests, "worker_state": worker,
               "local_code_sha256": {name: R.sha(Path(__file__).parent / name) for name in
                                      (Path(__file__).name, "nextdata_exact_cache_resume_v1.py", "kaggle_nextdata_exact_resume_v1.py")},
               "scope_status": "unproved", "citation_semantics_supported": False,
               "production_adoption_allowed": False,
               "batch_grouping_note": "New unwritten keys retain batch16/eager/FP32; outer call grouping changes. Bitwise equality for never-written first results is not assumed. Original occurrences are preserved verbatim."}
    R.write_json(args.output_dir / "PREPARATION.json", receipt)
    print(R.canonical({"stage": "prepared-local-only", "statistics": statistics,
                       "closure_totals": closure["totals"], "shard_count": len(plan["shards"]),
                       "planned_unique_request_count": plan["planned_unique_request_count"]}), flush=True)


def import_new_cache(args):
    if args.output_index.exists():
        raise FileExistsError(args.output_index)
    catalog = catalog_args(args)
    state = R.GPU.verify_bundle(args.worker_state_dir)
    plan = json.loads((args.worker_state_dir / "worker-plan.json").read_text(encoding="utf-8"))
    state_index = args.worker_state_dir / "index.sqlite"
    if R.sha(state_index) != plan["index_sha256"]:
        raise ValueError("worker execution index differs from its exact frozen plan")
    for name in ("nextdata_exact_cache_resume_v1.py", "kaggle_nextdata_exact_resume_v1.py"):
        if R.sha(args.worker_state_dir / name) != R.sha(Path(__file__).parent / name):
            raise ValueError("worker cache executor differs from reviewed local code")
    freeze = json.loads(args.execution_freeze.read_text(encoding="utf-8"))
    R.validate_runtime(freeze["gpu_runtime"])
    for kind in R.KINDS:
        R.validate_pins(kind, freeze["model_pins"][kind])
    if freeze.get("original_input_sha256") != R.PINNED_INPUT_SHA or freeze.get("model_identities") != {k: R.model_identity(k) for k in R.KINDS}:
        raise ValueError("new score cache does not bind the original execution identity")
    allowed_keys = R.validate_worker_cache_origin(plan, freeze, state["manifest_sha256"])
    recovered = json.loads(args.recovery_receipt.read_text(encoding="utf-8"))
    if recovered.get("terminal_status") not in {"complete", "error", "timeout", "cancelled"}:
        raise ValueError("mutable running cache cannot be imported")
    if (R.sha(args.cache_file), args.cache_file.stat().st_size, R.sha(args.execution_freeze)) != (
            recovered.get("cache_file_sha256"), recovered.get("cache_file_size_bytes"), recovered.get("execution_freeze_sha256")):
        raise ValueError("ROOT recovered new cache bytes differ")
    if recovered.get("remote_ref") != freeze.get("remote_ref") or recovered.get("version") != freeze.get("version"):
        raise ValueError("new cache remote identity differs")
    data = args.cache_file.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("preserve partial tail; ROOT review required before importing")
    shutil.copyfile(args.input_index, args.output_index)
    db = R.connect(args.output_index)
    initial = R.connect(state_index)
    try:
        R.verify_index_identity(db)
        R.verify_index_identity(initial)
        R.validate_merge_base(initial, db)
        seen = set()
        for line_number, raw in enumerate(R.read_jsonl(args.cache_file), 1):
            kind, key = raw["kind"], raw["exact_key"]
            if (kind, key) in seen:
                raise ValueError("new bounded run repeats a first-input record")
            seen.add((kind, key))
            if (kind, key) not in allowed_keys or initial.execute("SELECT 1 FROM cache WHERE kind=? AND exact_key=?", (kind, key)).fetchone():
                raise ValueError("new cache scored a key outside the unwritten frozen shard")
            request, identity = R.checked_request(db, catalog, kind, key)
            if raw["legacy_key"] != identity["legacy_key"] or raw["model_text_sha256"] != identity["model_text_sha256"]:
                raise ValueError("new score text identity differs")
            R.validate_score(kind, raw["score"], request, identity)
            if db.execute("SELECT 1 FROM cache WHERE kind=? AND exact_key=?", (kind, key)).fetchone():
                raise ValueError("semantic retry of an already-written exact input is prohibited")
            R.put_cache(db, kind, key, raw["score"], {"type": "bounded_gpu_first_record", "remote_ref": recovered["remote_ref"],
                        "version": recovered["version"], "file_sha256": R.sha(args.cache_file), "line_number": line_number})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        initial.close()
    R.write_json(args.output_index.with_suffix(".receipt.json"), {"imported_unique_requests": len(seen),
                 "output_index_sha256": R.sha(args.output_index), "input_index_sha256": R.sha(args.input_index),
                 "source_cache_sha256": R.sha(args.cache_file), "original_records_overwritten": 0, "labels_read": False})


def replan(args):
    closure = json.loads(args.closure.read_text(encoding="utf-8"))
    R.validate_private_closure(closure)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    db = R.connect(args.input_index)
    try:
        R.verify_index_identity(db)
        plan = R.bounded_plan(db, closure, args.max_unique_requests)
    finally:
        db.close()
    R.write_json(args.output_dir / "LOCAL_PLAN.json", plan)
    export_worker_state(args, args.input_index, plan, args.output_dir / "worker-state")


def assemble(args):
    catalog = catalog_args(args)
    db = R.connect(args.input_index)
    R.verify_index_identity(db)
    closure = json.loads(args.closure.read_text(encoding="utf-8"))
    R.validate_private_closure(closure)
    cached = {(r["kind"], r["exact_key"]) for r in db.execute("SELECT kind,exact_key FROM cache")}
    completed_products, allowed_refs = [], set()
    for product in closure["products"]:
        required = set()
        for ref in product["pair_refs"]:
            required.update((r["kind"], r["exact_key"]) for r in db.execute(
                "SELECT DISTINCT kind,exact_key FROM occurrences WHERE pair_ref=?", (R.canonical(ref),)))
        complete_product = required <= cached
        completed_products.append({"product_id": product["product_id"], "complete": complete_product,
                                   "closure_sha256": product["closure_sha256"], "case_count": len(product["case_ids"]),
                                   "row_count": len(product["row_keys"]), "task_count": len(product["task_ids"]),
                                   "unwritten_unique_requests": len(required - cached)})
        if complete_product:
            allowed_refs.update(product["pair_refs"])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    totals, incomplete = Counter(), []
    names = ["option-predictions.jsonl", "resolution-provenance.jsonl", *[
        f"predictions-{kind}-{variant}.jsonl" for kind in R.KINDS for variant in R.TASK.VARIANTS]]
    handles = {name: (args.output_dir / name).open("x", encoding="utf-8") for name in names}
    def append(name, record):
        handles[name].write(R.canonical(record) + "\n")
    try:
        for row in catalog.rows:
            if row["pair_ref"] not in allowed_refs:
                incomplete.extend([[row["pair_ref"], v] for v in R.TASK.VARIANTS])
                continue
            for variant in R.TASK.VARIANTS:
                chunks, issue = catalog.chunks[row["pair_ref"], variant]
                decisions = {kind: [] for kind in R.KINDS}
                pair_complete = True
                options = []
                for option_index, option in enumerate(row["option_values"]):
                    states, failures, counts = {}, {}, {}
                    for kind in R.KINDS:
                        raw_states, failed = [], issue is not None
                        for chunk in chunks:
                            score, provenance = R.resolve_score(db, kind, (row["pair_ref"], variant, option_index, chunk["chunk_ref"]))
                            if score is None:
                                pair_complete = False
                                continue
                            raw_states.append(score["state"])
                            failed |= (score["status"] != "ok") if kind == "jev" else (
                                score["probabilities"] is None or score.get("error_type") is not None
                                or (score["token_count_untruncated"] or 513) > 512)
                            append("resolution-provenance.jsonl", {"kind": kind, "pair_ref": row["pair_ref"], "variant": variant,
                                   "option_index": option_index, "chunk_ref": chunk["chunk_ref"], **provenance})
                            totals[provenance["resolution"]] += 1
                        states[kind] = R.TASK.aggregate(raw_states, failed)
                        failures[kind], counts[kind] = failed, dict(Counter(raw_states))
                        decisions[kind].append({"option_index": option_index,
                            "state": "explicit_conflict" if states[kind] == "conflict" else states[kind],
                            "chunk_state_counts": counts[kind], "technical_failure": failed})
                    options.append({"pair_ref": row["pair_ref"], "variant": variant, "option_index": option_index,
                                    "whole_option": option, "states": states, "technical_failure": failures,
                                    "chunk_state_counts": counts, "input_issue": issue, "scope_status": "unproved",
                                    "citation_semantics_supported": False, "production_adoption_allowed": False})
                if not pair_complete:
                    incomplete.append([row["pair_ref"], variant])
                    continue
                for option in options:
                    append("option-predictions.jsonl", option)
                for kind in R.KINDS:
                    append(f"predictions-{kind}-{variant}.jsonl", {"pair_ref": row["pair_ref"], "decisions": decisions[kind],
                           "input_issue": issue, "scope_status": "unproved", "citation_semantics_supported": False,
                           "production_adoption_allowed": False})
                totals["paired_input_count"] += 1
                totals["option_prediction_count"] += len(options)
    finally:
        for handle in handles.values():
            handle.close()
        db.close()
    complete = not incomplete
    if complete and (totals["paired_input_count"], totals["option_prediction_count"]) != (1942, 8536):
        raise ValueError("assembled complete population differs")
    R.write_json(args.output_dir / "ASSEMBLY.json", {"complete": complete, "totals": dict(totals),
                 "incomplete_pair_variants": incomplete, "original_input_sha256": R.PINNED_INPUT_SHA,
                 "index_sha256": R.sha(args.input_index), "labels_read": False,
                 "private_source_closure_sha256": R.sha(args.closure),
                 "complete_product_count": sum(p["complete"] for p in completed_products),
                 "product_publication_requires_all_product_pairs_complete": True,
                 "scope_status": "unproved", "citation_semantics_supported": False,
                 "production_adoption_allowed": False, "files": {name: {"sha256": R.sha(args.output_dir / name),
                     "size_bytes": (args.output_dir / name).stat().st_size} for name in names}})
    R.write_json(args.output_dir / "private-product-completion.json", {"products": completed_products,
                 "labels_read": False, "worker_export_allowed": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    add_sources(prep)
    prep.add_argument("--membership", type=Path, default=SOURCE / "prepared-source-v1/membership.jsonl")
    prep.add_argument("--row-plans", type=Path, default=SOURCE / "prepared-source-v1/row-plans.jsonl")
    prep.add_argument("--tasks", type=Path, default=SOURCE / "prepared-source-v1/tasks.jsonl")
    prep.add_argument("--bindings", type=Path, default=SOURCE / "scoped-native-source-v3/validation-bindings.jsonl")
    prep.add_argument("--output-dir", type=Path, required=True)
    prep.add_argument("--max-unique-requests", type=int, default=20000)
    prep.add_argument("--prior-dir", type=Path)
    prep.add_argument("--prior-receipt", type=Path)
    prep.set_defaults(func=prepare)
    imp = commands.add_parser("import-new-cache")
    add_sources(imp)
    for name in ("input-index", "output-index", "cache-file", "execution-freeze", "recovery-receipt", "worker-state-dir"):
        imp.add_argument("--" + name, type=Path, required=True)
    imp.set_defaults(func=import_new_cache)
    repl = commands.add_parser("replan")
    add_sources(repl)
    for name in ("input-index", "closure", "output-dir"):
        repl.add_argument("--" + name, type=Path, required=True)
    repl.add_argument("--max-unique-requests", type=int, default=20000)
    repl.set_defaults(func=replan)
    ass = commands.add_parser("assemble")
    add_sources(ass)
    ass.add_argument("--input-index", type=Path, required=True)
    ass.add_argument("--output-dir", type=Path, required=True)
    ass.add_argument("--closure", type=Path, required=True)
    ass.set_defaults(func=assemble)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
