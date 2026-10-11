#!/usr/bin/env python3
"""Vertex-compatible decoder for the same frozen SKU tasks; retain prior success."""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import trial_gemini_luna50_replay as base
import sku_gemini_replay_stages as stages
import sku_gemini_replay_transport as transport

EXP = base.EXP
FIRST = EXP / "results/20261011T001500Z-gemini-luna50-replay"
MODULE = Path(__file__).resolve()


def compatible_schema(schema):
    """Move input-dependent array counts and evidence-ID membership to local validation."""
    if isinstance(schema, list):
        return [compatible_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    result = {k: compatible_schema(v) for k, v in schema.items() if k not in {"minItems", "maxItems"}}
    if result.get("type") == "STRING" and result.get("enum") != ["support", "contradiction", "unknown"]:
        result.pop("enum", None)
    return result


def adapt_request(root, entry):
    path = root / entry["request"]
    request = base.read(path)
    original = copy.deepcopy(request["body"])
    request["body"]["generationConfig"]["responseSchema"] = compatible_schema(original["generationConfig"]["responseSchema"])
    # Explicitly prove that only the decoder schema changed.
    restored = copy.deepcopy(request["body"])
    restored["generationConfig"]["responseSchema"] = original["generationConfig"]["responseSchema"]
    if restored != original:
        raise ValueError("decoder adaptation changed task")
    entry["original_request_sha256"] = base.sha(path)
    stages.core.save(path, request)
    entry["request_sha256"] = base.sha(path)
    entry["request_body_sha256"] = stages._body_sha(path.read_bytes())
    entry["decoder_adapter"] = "input-dependent decoder constraints validated locally"


def prepare(root):
    base.verify(FIRST, "frozen-protocol.json")
    check = subprocess.run([sys.executable, "-B", str(stages.V13 / "verify_lineage_v2.py"), "--verify-only"], capture_output=True, text=True)
    if check.returncode:
        raise ValueError("inherited lineage verification failed")
    result = stages.prepare(root)
    transport.save_new(root / "source-lineage-verification.json", {"stage": "before Gemini v2 inference", **json.loads(check.stdout)})
    manifest = base.read(root / "matching-dispatch-manifest.json")
    prior = base.read(FIRST / "matching-dispatch-manifest.json")
    reuse = []
    for index, entry in enumerate(manifest["entries"], 1):
        if index <= 3:
            if (root / entry["request"]).read_bytes() != (FIRST / prior["entries"][index-1]["request"]).read_bytes():
                raise ValueError("prior successful task differs")
            request = base.read(root / entry["request"])
            transport.verify_attempt(FIRST, "matching", index, request["body"], FIRST / entry["answer"])
            for suffix in ("attempt", "response", "receipt"):
                source = FIRST / "provider/matching" / f"{index:03}.{suffix}.json"
                dest = root / "provider/matching" / source.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(source.read_bytes())
            (root / entry["answer"]).write_bytes((FIRST / entry["answer"]).read_bytes())
            entry["answer_origin"] = {"method": "unchanged prior Gemini successful task", "run": str(FIRST.relative_to(EXP)),
                                      "same_request_bytes": True, "new_inference": False}
            reuse.append(entry["case_id"])
        else:
            adapt_request(root, entry)
    manifest["decoder_adaptation"] = {"unchanged_task_text_and_selected_values": True,
        "status_source_ids_contract_unchanged": True, "strict_local_counts_and_reference_validation": True,
        "fresh_calls_planned": 67, "prior_Gemini_success_reused": 3,
        "prior_FAILED_http400_retained": str(FIRST.relative_to(EXP)),
        "no_Luna_answers_reused": True}
    stages.core.save(root / "matching-dispatch-manifest.json", manifest)
    transport.save_new(root / "replay-plan.json", {**result, **manifest["decoder_adaptation"], "reused_task_ids": reuse})
    transport.save_new(root / "initial-manifest.json", base.read(root / "derived/manifest.json"))
    paths = [EXP/m for m in base.MODULES] + [MODULE] + base.lineage_paths()
    paths += [root / "derived/inputs.json", root / "derived/source-contexts.json", root / "matching-dispatch-manifest.json",
              root / "initial-manifest.json", root / "source-lineage-verification.json", root / "replay-plan.json", FIRST / "frozen-protocol.json"]
    paths += base.request_paths(root, "matching-dispatch-manifest.json") + base.request_paths(root / "derived", "manifest.json")
    paths += list((root / "provider/matching").glob("*.json")) + list((root / "matching-chunk-answers").glob("*.json"))
    paths += list((FIRST / "provider/matching").glob("*.json"))
    base.freeze(root, "frozen-protocol.json", paths, gold_read=False, same_semantic_task=True, new_matching_calls_planned=67,
                prior_Gemini_success_reused=3, prior_Luna_answers_reused=0, local_validation_unchanged=True)
    print(json.dumps(base.read(root / "replay-plan.json"), ensure_ascii=False), flush=True)


def signature_prepare(root):
    base.verify(root, "frozen-protocol.json")
    result = stages.signature_prepare(root)
    manifest = base.read(root / "derived/signature-v3-manifest.json")
    for entry in manifest["entries"]:
        adapt_request(root / "derived", entry)
    stages.core.save(root / "derived/signature-v3-manifest.json", manifest)
    dispatch = base.read(root / "signature-dispatch-manifest.json")
    by_case = {e["case_id"]: e for e in manifest["entries"]}
    dispatch["entries"] = [{**by_case[e["case_id"]], "request": "derived/"+by_case[e["case_id"]]["request"],
                           "answer": "derived/"+by_case[e["case_id"]]["answer"]} for e in dispatch["entries"]]
    stages.core.save(root / "signature-dispatch-manifest.json", dispatch)
    paths = [root / "derived/manifest.json", root / "derived/evaluation.json", root / "matching-chunk-evaluation.json",
             root / "derived/signature-v3-manifest.json", root / "signature-dispatch-manifest.json"]
    paths += base.request_paths(root / "derived", "signature-v3-manifest.json")
    paths += [root / "derived" / e["answer"] for e in base.read(root / "derived/manifest.json")["entries"]]
    paths += list((root / "provider/matching").glob("*.json")) + list((root / "matching-chunk-answers").glob("*.json"))
    paths += list((root / "derived/signature-v3-answers").glob("*.json"))
    base.freeze(root, "signature-freeze.json", paths, decoder_only_adapted=True, **result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def execution_summary(root, state, elapsed=None):
    receipts = [base.read(p) for p in sorted((root / "provider").glob("*/*.receipt.json"))]
    reused = min(3, sum(r["stage"] == "matching" for r in receipts))
    usage = Counter()
    for r in receipts:
        usage.update({k:v for k,v in r.get("usageMetadata",{}).items() if isinstance(v,int) and not isinstance(v,bool)})
    summary = {"createdUTC": transport.utc(), "state": state, "project": transport.PROJECT, "location": transport.LOCATION,
               "model_requested": transport.MODEL, "successful_task_receipts": len(receipts),
               "actual_generateContent_calls_this_run": len(receipts)-reused, "prior_Gemini_success_reused": reused,
               "actual_generateContent_calls_including_prior_failed_protocol": len(receipts)+1,
               "served_model_versions": dict(Counter(r.get("modelVersion","unreported") for r in receipts)),
               "stage_calls_including_reused": dict(Counter(r["stage"] for r in receipts)),
               "http_status_counts": dict(Counter(str(r["http_status"]) for r in receipts)),
               "parse_success": sum(r["parse_error"] is None for r in receipts), "usage_totals_including_reused_once": dict(usage),
               "api_elapsed_seconds_sum_including_reused_once": round(sum(r["elapsed_seconds"] for r in receipts),6),
               "wall_elapsed_seconds_this_run": round(elapsed,6) if elapsed is not None else None,
               "model_runtime_verified": bool(receipts) and all(r.get("modelVersion") for r in receipts),
               "prior_http400_extra_seconds": 0.78747, "gold_read_before_freeze": False}
    transport.save_new(root / "execution-summary.json", summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return summary


def run(root):
    base.signature_prepare = signature_prepare
    base.execution_summary = execution_summary
    return base.run(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare","run","verify"))
    parser.add_argument("--run-dir",type=Path,required=True)
    args=parser.parse_args();root=args.run_dir.resolve()
    if not root.is_relative_to(EXP/"results"):
        parser.error("run directory must be inside results")
    if args.command=="prepare":prepare(root)
    elif args.command=="run":return run(root)
    else:
        for name in ("frozen-protocol.json","signature-freeze.json","review-freeze.json","grading-freeze.json"):
            if (root/name).exists():base.verify(root,name)
        print("All existing freezes verified.")
    return 0


if __name__=="__main__":raise SystemExit(main())
