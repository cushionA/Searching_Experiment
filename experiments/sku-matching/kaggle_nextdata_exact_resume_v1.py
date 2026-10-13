#!/usr/bin/env python3
"""CUDA first-score executor for ONE frozen bounded numeric request shard.

The original private source bundle is reused without changes. A separate
source-only state bundle contains the exact request index and old chunk ranges.
No remote orchestration, source joins, grades, quotation or scope authoring.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time

import nextdata_exact_cache_resume_v1 as R


def thin(kind, record):
    if kind == "jev":
        state, _ = R.TASK.jev_state(record)
        fields = ("status", "logits", "probabilities", "token_counts", "cache_key", "error_type")
    else:
        state = record["relation"]
        fields = ("probabilities", "predicted_class", "predicted_confidence", "token_count_untruncated", "cache_key", "error_type")
    return {"state": state, **{field: record[field] for field in fields}}


def execute_pending(db, catalog, keys, scorer, append, max_seconds=6000, clock=time.monotonic):
    """Single first record per key. Technical unknowns are saved and never retried."""
    started = clock()
    totals = Counter()
    pending = []
    for kind, key in keys:
        if db.execute("SELECT 1 FROM cache WHERE kind=? AND exact_key=?", (kind, key)).fetchone():
            totals["already_written_skipped"] += 1
        else:
            request, identity = R.checked_request(db, catalog, kind, key)
            pending.append((kind, key, request, identity))
    # Keep the label-free product-plan order, and advance both checkpoints in
    # each bounded group. Small complete products are available before a large
    # shard finishes; publication still requires every original request.
    # The original numeric driver retains its inner batches of 16.
    for offset in range(0, len(pending), 256):
        for kind in R.KINDS:
            if clock() - started >= max_seconds:
                return {**dict(totals), "bounded_time_reached": True, "complete": False}
            group = [(key, request, identity) for model_kind, key, request, identity
                     in pending[offset:offset + 256] if model_kind == kind]
            if not group:
                continue
            requests = [request for _, request, _ in group]
            result = scorer.run_choices(requests, threads=4, batch_size=16) if kind == "jev" else scorer.run_pairs(requests, threads=4, batch_size=16)
            if len(result["records"]) != len(group):
                raise ValueError("numeric driver returned a partial batch")
            for (key, request, identity), record in zip(group, result["records"], strict=True):
                score = thin(kind, record)
                R.validate_score(kind, score, request, identity)
                out = {"kind": kind, "exact_key": key, "legacy_key": identity["legacy_key"],
                       "model_text_sha256": identity["model_text_sha256"], "score": score}
                # Publish/flush the first score before any later group starts.
                append(out)
                R.put_cache(db, kind, key, score, {"type": "bounded_gpu_first_record"})
                totals[f"{kind}_new_unique_records"] += 1
                totals["technical_first_records"] += ((score["status"] != "ok") if kind == "jev" else
                    (score["probabilities"] is None or score["error_type"] is not None or (score["token_count_untruncated"] or 513) > 512))
            db.commit()
            print(R.canonical({"stage": "bounded-group-complete", "kind": kind, "totals": dict(totals),
                               "elapsed_seconds": clock() - started}), flush=True)
    return {**dict(totals), "bounded_time_reached": False, "complete": True}


def run(args):
    base = R.GPU.verify_bundle(args.original_bundle_dir)
    state = R.GPU.verify_bundle(args.state_bundle_dir)
    if base["manifest_sha256"] != R.ORIGINAL_BUNDLE_SHA:
        raise ValueError("original unmodified source-only bundle required")
    plan = json.loads((args.state_bundle_dir / "worker-plan.json").read_text(encoding="utf-8"))
    if (plan.get("original_input_sha256"), plan.get("original_bundle_manifest_sha256"), plan.get("model_identities")) != (
            R.PINNED_INPUT_SHA, R.ORIGINAL_BUNDLE_SHA, {k: R.model_identity(k) for k in R.KINDS}):
        raise ValueError("worker plan original input/model identity differs")
    if "local_product_receipts" in plan or plan.get("labels_read") is not False:
        raise ValueError("worker plan must exclude private joins and labels")
    if plan.get("numeric_continuation_allowed") is not True:
        raise ValueError("planning-only shards cannot repeat the ongoing original first pass")
    shard = next((s for s in plan["shards"] if s["shard_id"] == args.shard_id), None)
    if shard is None or shard["unique_request_count"] > plan["max_unique_requests_per_shard"]:
        raise ValueError("unknown or overbudget shard")
    keys = shard["request_keys"]
    if len(keys) != shard["unique_request_count"] or len({tuple(k) for k in keys}) != len(keys):
        raise ValueError("shard repeats or omits an exact request")
    index = args.state_bundle_dir / "index.sqlite"
    if R.sha(index) != plan["index_sha256"]:
        raise ValueError("index differs from frozen shard plan")
    catalog = R.Catalog(args.original_bundle_dir / "input/paired-public-inputs.jsonl",
                        args.original_bundle_dir / "provenance/root-population-freeze.json",
                        args.original_bundle_dir / "provenance/description-sourceproof.json",
                        args.state_bundle_dir / "pair-token-coverage.jsonl",
                        args.state_bundle_dir / "PREFLIGHT_RECEIPT.json")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    db = R.connect(index)
    # Immutable source DB is read only: this scratch DB lives only in memory.
    memory = R.connect(":memory:")
    db.backup(memory)
    db.close()
    R.verify_index_identity(memory)
    R.verify_resume_ready(memory)
    roots = R.GPU.acquire_weights(args.weights_dir, args.original_bundle_dir / "provenance/jev-provenance.json",
                                  download=not args.no_download)
    scorer = R.GPU.CudaScorer(roots["jev"], roots["nli"])
    for kind in R.KINDS:
        scorer.load(kind)
        R.validate_pins(kind, scorer.loaded[kind][2])
    R.validate_runtime(scorer.runtime)
    freeze = {"schema_version": "sku-nextdata-bounded-exact-input-gpu-v1", "remote_ref": args.remote_ref,
              "version": args.version, "shard_id": args.shard_id, "max_seconds": args.max_seconds,
              "original_input_sha256": R.PINNED_INPUT_SHA, "original_bundle_manifest_sha256": R.ORIGINAL_BUNDLE_SHA,
              "state_bundle_manifest_sha256": state["manifest_sha256"], "index_sha256": plan["index_sha256"],
              "model_identities": {k: R.model_identity(k) for k in R.KINDS}, "gpu_runtime": scorer.runtime,
              "model_pins": {k: scorer.loaded[k][2] for k in R.KINDS}, "labels_read": False,
              "batch_grouping": "outer256 combined keys in label-free product-plan order, both models advance per group; original numeric driver inner16; FP32/eager/CUDA unchanged",
              "no_bitwise_equality_assumed_for_never_written_requests": True,
              "scope_status": "unproved", "citation_semantics_supported": False, "production_adoption_allowed": False}
    R.write_json(args.output_dir / "EXECUTION_FREEZE.json", freeze)
    output = args.output_dir / "new-first-scores.jsonl"
    with output.open("x", encoding="utf-8") as handle:
        def append(record):
            handle.write(R.canonical(record) + "\n")
            handle.flush()
        totals = execute_pending(memory, catalog, keys, scorer, append, args.max_seconds)
    memory.close()
    if R.GPU.verify_bundle(args.original_bundle_dir)["manifest_sha256"] != base["manifest_sha256"] or R.GPU.verify_bundle(args.state_bundle_dir)["manifest_sha256"] != state["manifest_sha256"]:
        raise ValueError("a frozen source bundle changed during execution")
    R.write_json(args.output_dir / "COMPLETION.json", {**freeze, "totals": totals,
                 "new_first_scores_sha256": R.sha(output), "new_first_scores_size_bytes": output.stat().st_size,
                 "semantic_retry_performed": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("original-bundle-dir", "state-bundle-dir", "output-dir", "weights-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--shard-id", type=int, required=True)
    parser.add_argument("--remote-ref", required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--max-seconds", type=int, default=6000)
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()
    if not 0 < args.max_seconds <= 6000 or args.version < 1:
        raise ValueError("bounded first-score runtime must be <=6000s with a fixed remote version")
    run(args)


if __name__ == "__main__":
    main()
