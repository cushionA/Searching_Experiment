#!/usr/bin/env python3
"""Re-run four previously incorrect SKU cases with explicit thinking settings.

This is a selected-error development experiment, not a new holdout benchmark.
All inference is fresh. Existing frozen experiments are read-only.
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import sku_gemini_replay_stages as stages
import sku_gemini_replay_transport as transport
import trial_gemini_luna50_replay as base
import trial_gemini_luna50_replay_v2 as decoder

EXP = base.EXP
SOURCE = EXP / "results/20261011T002300Z-gemini-luna50-replay-v2"
MODULE = Path(__file__).resolve()
CASE_IDS = (
    "case-49c7d5a7b36f3f21641f",
    "case-04a68396ffb8ec49fae2",
    "case-74f7cb07e510ea6c8310",
    "case-8c12c6dc7a575b9ac8b3",
)
THINKING_CONFIG = {"thinkingLevel": "MINIMAL", "includeThoughts": False}
MODES = {
    "minimal": ("gemini-3.5-flash-lite", {"thinkingLevel": "MINIMAL", "includeThoughts": False}),
    "low": ("gemini-3.5-flash-lite", {"thinkingLevel": "LOW", "includeThoughts": False}),
    "off-2.5": ("gemini-2.5-flash-lite", {"thinkingBudget": 0, "includeThoughts": False}),
}


def configure(mode):
    global THINKING_CONFIG
    model, config = MODES[mode]
    THINKING_CONFIG = copy.deepcopy(config)
    transport.MODEL = model
    transport.ENDPOINT = (f"https://aiplatform.googleapis.com/v1/projects/{transport.PROJECT}"
                          f"/locations/{transport.LOCATION}/publishers/google/models/{model}:generateContent")


def thinking_off_body(body):
    """Change only thinkingConfig; preserve prompt, decoder and other parameters."""
    original = copy.deepcopy(body)
    if "thinkingConfig" in original.get("generationConfig", {}):
        raise ValueError("control body already has an explicit thinking configuration")
    result = copy.deepcopy(original)
    result["generationConfig"]["thinkingConfig"] = copy.deepcopy(THINKING_CONFIG)
    restored = copy.deepcopy(result)
    restored["generationConfig"].pop("thinkingConfig")
    if restored != original:
        raise ValueError("thinking adapter changed the underlying task")
    return result


def adapt_file(root, entry):
    path = root / entry["request"]
    request = base.read(path)
    entry["control_request_sha256"] = base.sha(path)
    request["body"] = thinking_off_body(request["body"])
    stages.core.save(path, request)
    entry["request_sha256"] = base.sha(path)
    entry["request_body_sha256"] = stages._body_sha(path.read_bytes())


def prepare(root, mode):
    configure(mode)
    if root.exists() and any(not (p.name in {"auth-check.json", "auth", "thinking-specification.json"}
                                 or p.name.startswith("auth-manual-")) for p in root.iterdir()):
        raise ValueError("prepare requires a new directory with auth checks only")
    root.mkdir(parents=True, exist_ok=True)
    base.verify(SOURCE, "grading-freeze.json")
    cases_by_id = {c["case_id"]: c for c in base.read(SOURCE / "derived/inputs.json")}
    cases = [cases_by_id[cid] for cid in CASE_IDS]
    old_contexts = base.read(SOURCE / "derived/source-contexts.json")
    ddir = root / "derived"
    for name in ("requests", "answers"):
        (ddir / name).mkdir(parents=True)
    for name in ("matching-chunk-requests", "matching-chunk-answers"):
        (root / name).mkdir()
    stages.core.save(ddir / "inputs.json", cases)
    stages.core.save(ddir / "source-contexts.json", {cid: old_contexts[cid] for cid in CASE_IDS})
    original = base.read(SOURCE / "initial-manifest.json")
    old_entries = {e["case_id"]: e for e in original["entries"]}
    entries = []
    for i, cid in enumerate(CASE_IDS, 1):
        old = old_entries[cid]
        request_rel = f"requests/{i:02}.json"
        (ddir / request_rel).write_bytes((SOURCE / "derived" / old["request"]).read_bytes())
        entry = {"case_id": cid, "request": request_rel, "answer": f"answers/{i:02}.json"}
        adapt_file(ddir, entry)
        entries.append(entry)
    manifest = {k: copy.deepcopy(v) for k, v in original.items()
                if k not in {"entries", "selection", "routing_evidence"}}
    manifest.update({"entries": entries, "created_utc": transport.utc(), "planned_calls": 4,
                     "model_requested": transport.MODEL,
                     "selection": {"selected_case_ids": list(CASE_IDS),
                                   "rule": "previous known false accept, positive abstention, negative unevaluated, negative pending"},
                     "inputs_sha256": base.sha(ddir / "inputs.json"),
                     "source_contexts_sha256": base.sha(ddir / "source-contexts.json"),
                     "labels_read": False, "prior_label_derived_results_used_for_selection": True})
    stages.core.save(ddir / "manifest.json", manifest)
    transport.save_new(root / "initial-manifest.json", manifest)
    dispatch = []
    for old in base.read(SOURCE / "matching-dispatch-manifest.json")["entries"]:
        if old["original_case_id"] not in CASE_IDS:
            continue
        i = len(dispatch) + 1
        rel = f"matching-chunk-requests/{i:03}.json"
        (root / rel).write_bytes((SOURCE / old["request"]).read_bytes())
        entry = {k: copy.deepcopy(old[k]) for k in
                 ("case_id", "original_case_id", "row_start", "row_count")}
        entry.update(request=rel, answer=f"matching-chunk-answers/{i:03}.json",
                     control_request=str((SOURCE / old["request"]).relative_to(EXP)))
        adapt_file(root, entry)
        dispatch.append(entry)
    if len(dispatch) != 9 or sum(e["row_count"] for e in dispatch) != 190:
        raise ValueError("selected four-case coverage differs from the frozen source")
    transport.save_new(root / "matching-dispatch-manifest.json", {
        "planned_calls": len(dispatch), "distinct_case_count": len(cases), "entries": dispatch,
        "candidate_rows_per_task_max": 27, "prior_answers_reused": 0})
    transport.save_new(root / "replay-plan.json", {
        "source_run": str(SOURCE.relative_to(EXP)), "case_ids": list(CASE_IDS),
        "case_count": 4, "candidate_rows": 190, "matching_calls": 9, "review_calls": 4,
        "dimension_calls_max": 4, "maximum_generateContent_calls": 17,
        "thinking_mode": mode, "model_requested": transport.MODEL,
        "thinking_config_requested": THINKING_CONFIG,
        "provider_acceptance": "unverified until actual generateContent",
        "internal_thinking_disabled_verified": False,
        "complete_off_supported_for_requested_model": mode == "off-2.5",
        "previous_3_5_default_thinking_level": "MINIMAL",
        "same_selected_values_and_sources": True, "same_matching_prompts_and_decoders": True,
        "prior_answers_reused": 0, "automatic_retry": False,
        "previous_label_derived_errors_observed": True, "holdout": False,
        "comparison": "historical default setting versus explicit selected setting, once per task",
        "human_verified_labels": False})
    paths = [EXP / name for name in base.MODULES] + [decoder.MODULE, MODULE]
    paths += base.lineage_paths() + [SOURCE / "grading-freeze.json"]
    # The collector will enrich manifest.json with output provenance. Freeze
    # its immutable initial copy, not that mutable working manifest.
    paths += [p for p in root.rglob("*") if p.is_file() and p != ddir / "manifest.json"]
    base.freeze(root, "frozen-protocol.json", paths, raw_labels_read=False,
                prior_label_derived_errors_observed=True, selected_error_subset=True,
                prior_model_answers_sent_to_matching=False)
    print(json.dumps(base.read(root / "replay-plan.json"), ensure_ascii=False), flush=True)


def signature_prepare(root):
    base.verify(root, "frozen-protocol.json")
    result = stages.signature_prepare(root)
    if result["planned_calls"] > 4:
        raise ValueError("dimension stage exceeds the selected four-case bound")
    manifest = base.read(root / "derived/signature-v3-manifest.json")
    old = {(e["case_id"], e["row_key"]): e for e in
           base.read(SOURCE / "derived/signature-v3-manifest.json")["entries"]}
    for entry in manifest["entries"]:
        control = old.get((entry["case_id"], entry["row_key"]))
        if control and control["targets"] == entry["targets"]:
            (root / "derived" / entry["request"]).write_bytes(
                (SOURCE / "derived" / control["request"]).read_bytes())
            entry["control_origin"] = "exact previous Gemini dimension body"
        else:
            decoder.adapt_request(root / "derived", entry)
            entry["control_origin"] = "same frozen dimension builder, bound to new candidate"
        adapt_file(root / "derived", entry)
    stages.core.save(root / "derived/signature-v3-manifest.json", manifest)
    by_id = {e["case_id"]: e for e in manifest["entries"]}
    dispatch = base.read(root / "signature-dispatch-manifest.json")
    dispatch["entries"] = [{**by_id[e["case_id"]],
                            "request": "derived/" + by_id[e["case_id"]]["request"],
                            "answer": "derived/" + by_id[e["case_id"]]["answer"]}
                           for e in dispatch["entries"]]
    stages.core.save(root / "signature-dispatch-manifest.json", dispatch)
    paths = [p for p in (root / "derived").rglob("*") if p.is_file()]
    paths += [root / "matching-chunk-evaluation.json", root / "signature-dispatch-manifest.json"]
    paths += list((root / "provider/matching").glob("*.json"))
    paths += list((root / "matching-chunk-answers").glob("*.json"))
    base.freeze(root, "signature-freeze.json", paths, **result)


def reviews_prepare(root):
    base.verify(root, "signature-freeze.json")
    stages._v13_guard_for(root)
    ddir = root / "derived"
    cases = {c["case_id"]: c for c in base.read(ddir / "inputs.json")}
    ev = {c["case_id"]: c for c in stages.core.evaluate(ddir)["cases"]}
    sig = stages.core.signature_results(ddir, "signature-v3")
    old = {e["case_id"]: e for e in base.read(SOURCE / "review-dispatch-manifest.json")["entries"]}
    for name in ("review-requests", "review-answers", "review-vertex-bodies"):
        (root / name).mkdir()
    entries = []
    for i, cid in enumerate(CASE_IDS, 1):
        request = base.read(SOURCE / old[cid]["request"])
        control = copy.deepcopy(request)
        dynamic = stages._dynamic_review_fields(cases[cid], ev[cid], sig.get(cid))
        request["cases"][0].update(dynamic)
        restored = copy.deepcopy(request)
        for key in dynamic:
            restored["cases"][0][key] = control["cases"][0][key]
        if restored != control:
            raise ValueError("review task changed beyond fresh model predictions")
        rel, answer = f"review-requests/{i:02}.json", f"review-answers/{i:02}.json"
        transport.save_new(root / rel, request)
        body_rel = f"review-vertex-bodies/{i:02}.json"
        transport.save_new(root / body_rel, thinking_off_body(stages.gemini_review_body(request)))
        entries.append({"case_id": cid, "request": rel, "answer": answer,
                        "request_sha256": base.sha(root / rel), "body_request": body_rel,
                        "body_request_sha256": base.sha(root / body_rel)})
    transport.save_new(root / "review-dispatch-manifest.json", {"planned_calls": 4, "entries": entries})
    paths = [p for p in ddir.rglob("*") if p.is_file()]
    paths += base.request_paths(root, "review-dispatch-manifest.json")
    paths += [root / e["body_request"] for e in entries]
    paths += [root / "review-dispatch-manifest.json"] + list((root / "provider/signature").glob("*.json"))
    base.freeze(root, "review-freeze.json", paths, prior_review_answers_reused=0)


def body_for(root, stage, entry):
    if base.sha(root / entry["request"]) != entry["request_sha256"]:
        raise ValueError("dispatch request changed")
    if stage == "review":
        path = root / entry["body_request"]
        if base.sha(path) != entry["body_request_sha256"]:
            raise ValueError("review provider body changed")
        body = base.read(path)
    else:
        body = base.read(root / entry["request"])["body"]
    if body.get("generationConfig", {}).get("thinkingConfig") != THINKING_CONFIG:
        raise ValueError("request thinking configuration differs from the selected condition")
    return body


def execute_stage(root, stage, secret):
    base.verify(root, "frozen-protocol.json")
    if stage != "matching":
        base.verify(root, stage + "-freeze.json")
    entries = base.read(root / (stage + "-dispatch-manifest.json"))["entries"]
    for i, entry in enumerate(entries, 1):
        body = body_for(root, stage, entry)
        prefix = root / "provider" / stage / f"{i:03}"
        if prefix.with_suffix(".attempt.json").exists():
            transport.verify_attempt(root, stage, i, body, root / entry["answer"])
            continue
        result = transport.call_once(root, stage, i, body, root / entry["answer"], secret)
        print(json.dumps({"stage": stage, "task": i, "http_status": result["http_status"],
                          "parse_error": result["parse_error"], "modelVersion": result.get("modelVersion"),
                          "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False), flush=True)
        if result["http_status"] != 200:
            raise ValueError("provider did not accept the request; remaining inference stopped")
        transport.verify_attempt(root, stage, i, body, root / entry["answer"])


def run(root):
    base.verify(root, "frozen-protocol.json")
    configure(base.read(root / "replay-plan.json")["thinking_mode"])
    secret = transport.token()
    if not base.auth(root, secret)["authenticated"]:
        return 2
    started = time.monotonic()
    try:
        execute_stage(root, "matching", secret)
        if not (root / "matching-chunk-evaluation.json").exists():
            stages.collect_matching(root)
        if not (root / "signature-freeze.json").exists():
            signature_prepare(root)
        execute_stage(root, "signature", secret)
        if not (root / "review-freeze.json").exists():
            reviews_prepare(root)
        execute_stage(root, "review", secret)
        stages.reviews_collect(root)
    except Exception as exc:
        transport.save_new(root / "execution-error.json", {
            "exception_type": type(exc).__name__, "remaining_inference_stopped": True})
        base.execution_summary(root, "incomplete", time.monotonic() - started)
        return 2
    paths = [p for p in root.rglob("*") if p.is_file()]
    base.freeze(root, "grading-freeze.json", paths, inference_complete=True,
                selected_error_subset=True, internal_thinking_disabled_verified=False)
    base.execution_summary(root, "complete", time.monotonic() - started)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--thinking-mode", choices=tuple(MODES))
    args = parser.parse_args()
    root = args.run_dir.resolve()
    if not root.is_relative_to(EXP / "results"):
        parser.error("output must be inside experiments/sku-matching/results")
    if args.command == "prepare":
        if args.thinking_mode is None:
            parser.error("prepare requires an explicit --thinking-mode")
        prepare(root, args.thinking_mode)
    elif args.command == "run":
        return run(root)
    else:
        for name in ("frozen-protocol.json", "signature-freeze.json", "review-freeze.json", "grading-freeze.json"):
            if (root / name).exists():
                base.verify(root, name)
        print("All existing freezes verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
