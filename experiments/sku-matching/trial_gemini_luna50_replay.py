#!/usr/bin/env python3
"""Replay the latest fifty Luna SKU tasks on Vertex, once per request."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import sku_gemini_replay_stages as stages
import sku_gemini_replay_transport as transport

EXP = Path(__file__).resolve().parent
DRIVER = Path(__file__).resolve()
MODULES = [
    "trial_gemini_luna50_replay.py", "sku_gemini_replay_stages.py", "sku_gemini_replay_transport.py",
    "trial_luna_sku_matching.py", "trial_gemini_sku_smoke_v2.py", "sku_luna_inputs.py",
    "sku_luna_source_context.py", "sku_luna_dimension_guard.py", "sku_luna_task_policy.py",
    "sku_luna_output_shape.py", "sku_luna_curtain_dimension_alias.py", "sku_luna_curtain_task.py",
    "sku_luna_closed_contents_task.py", "sku_luna_compact_general_task.py", "sku_luna_current_row_dimension_task.py",
    "sku_luna_adjustable_dimension_task.py", "evaluate_frozen_sku_accuracy.py",
]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(root: Path, name: str, paths: list[Path], **notes):
    mapping = {str(p.resolve().relative_to(EXP)): sha(p) for p in sorted(set(paths))}
    transport.save_new(root / name, {"createdUTC": transport.utc(), "sha256": mapping, **notes})


def verify(root: Path, name: str):
    data = read(root / name)
    for rel, value in data["sha256"].items():
        if sha(EXP / rel) != value:
            raise ValueError("frozen file changed: " + rel)


def request_paths(root: Path, name: str) -> list[Path]:
    return [root / e["request"] for e in read(root / name)["entries"]]


def lineage_paths() -> list[Path]:
    """Bind the inherited composed guards and same-task source templates."""
    v11 = EXP / "results/20261010T204500Z-luna-adjustable-dimensions-v11"
    paths = [stages.V13 / "run.py", v11 / "run_v2.py", v11 / "run.py", v11 / "selection.json",
             stages.V9 / "run.py", stages.V9 / "matching-dispatch-manifest.json",
             stages.V13 / "matching-dispatch-manifest.json", stages.V13 / "matching-chunk-evaluation.json",
             stages.V13 / "derived/manifest.json", stages.V13 / "derived/signature-v3-manifest.json",
             stages.V13 / "review-dispatch-manifest.json"]
    for folder in (stages.V9, stages.V13):
        paths.extend(request_paths(folder, "matching-dispatch-manifest.json"))
    paths.extend(request_paths(stages.V13, "review-dispatch-manifest.json"))
    paths.extend(request_paths(stages.V13 / "derived", "signature-v3-manifest.json"))
    return paths


def prepare(root: Path):
    # Check ancestor freezes before any new inference, without importing gold.
    check = subprocess.run([sys.executable, "-B", str(stages.V13 / "verify_lineage_v2.py"), "--verify-only"],
                           capture_output=True, text=True)
    if check.returncode:
        raise ValueError("inherited frozen lineage verification failed")
    lineage = json.loads(check.stdout)
    result = stages.prepare(root)
    transport.save_new(root / "source-lineage-verification.json", {
        "checkedUTC": transport.utc(), "stage": "before new Gemini inference", **lineage})
    paths = [EXP / m for m in MODULES] + lineage_paths()
    paths += [root / "derived/inputs.json", root / "derived/source-contexts.json",
              root / "matching-dispatch-manifest.json", root / "source-lineage-verification.json"]
    paths += request_paths(root, "matching-dispatch-manifest.json")
    paths += request_paths(root / "derived", "manifest.json")
    transport.save_new(root / "initial-manifest.json", read(root / "derived/manifest.json"))
    paths += [root / "initial-manifest.json"]
    freeze(root, "frozen-protocol.json", paths, gold_read=False, task_input_reused=True,
           prior_predictions_sent=False, prior_metrics_observed=True, new_model_answers_required=True)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def auth(root: Path, secret: str) -> dict:
    directory = root / "auth"
    directory.mkdir(exist_ok=True)
    index = len(list(directory.glob("*.json"))) + 1
    result = transport.auth_check(secret)
    transport.save_new(directory / f"{index:02}.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def execute_stage(root: Path, stage: str, secret: str):
    verify(root, "frozen-protocol.json")
    dispatch_name = {"matching": "matching-dispatch-manifest.json", "signature": "signature-dispatch-manifest.json",
                     "review": "review-dispatch-manifest.json"}[stage]
    if stage != "matching":
        verify(root, stage + "-freeze.json")
    entries = read(root / dispatch_name)["entries"]
    for index, entry in enumerate(entries, 1):
        prefix = root / "provider" / stage / f"{index:03}"
        request_path = root / entry["request"]
        if sha(request_path) != entry["request_sha256"]:
            raise ValueError("dispatch request changed")
        request = read(request_path)
        body = request["body"] if stage != "review" else stages.gemini_review_body(request)
        if prefix.with_suffix(".attempt.json").exists():
            # An interrupted/failed attempt is never resubmitted automatically.
            if not prefix.with_suffix(".receipt.json").exists():
                raise ValueError("previous attempt lacks receipt; refuse automatic replay")
            transport.verify_attempt(root, stage, index, body, root / entry["answer"])
            continue
        result = transport.call_once(root, stage, index, body, root / entry["answer"], secret)
        print(json.dumps({"stage": stage, "task": index, "total": len(entries),
                          "http_status": result["http_status"], "parse_error": result["parse_error"],
                          "modelVersion": result.get("modelVersion"),
                          "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False), flush=True)
        if result["http_status"] != 200:
            raise ValueError("Vertex transport failure; remaining inference stopped")
        transport.verify_attempt(root, stage, index, body, root / entry["answer"])


def signature_prepare(root: Path):
    verify(root, "frozen-protocol.json")
    result = stages.signature_prepare(root)
    paths = [root / "derived/manifest.json", root / "derived/evaluation.json",
             root / "matching-chunk-evaluation.json", root / "derived/signature-v3-manifest.json",
             root / "signature-dispatch-manifest.json"]
    paths += request_paths(root / "derived", "signature-v3-manifest.json")
    paths += [root / "derived" / e["answer"] for e in read(root / "derived/manifest.json")["entries"]]
    paths += [p for p in (root / "provider/matching").glob("*.json")]
    paths += [p for p in (root / "matching-chunk-answers").glob("*.json")]
    paths += [p for p in (root / "derived/signature-v3-answers").glob("*.json")]
    freeze(root, "signature-freeze.json", paths, **result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def verify_provider_stage(root: Path, stage: str):
    manifest = {"matching": "matching-dispatch-manifest.json", "signature": "signature-dispatch-manifest.json",
                "review": "review-dispatch-manifest.json"}[stage]
    for index, entry in enumerate(read(root / manifest)["entries"], 1):
        request = read(root / entry["request"])
        body = stages.gemini_review_body(request) if stage == "review" else request["body"]
        transport.verify_attempt(root, stage, index, body, root / entry["answer"])


def reviews_prepare(root: Path):
    verify(root, "frozen-protocol.json")
    verify(root, "signature-freeze.json")
    result = stages.reviews_prepare(root)
    paths = [root / "derived/signature-v3-evaluation.json", root / "review-dispatch-manifest.json"]
    paths += request_paths(root, "review-dispatch-manifest.json")
    paths += list((root / "derived/signature-v3-answers").glob("*.json"))
    paths += list((root / "provider/signature").glob("*.json"))
    # Freeze exact adapter output, in addition to the source review request.
    bodies = root / "review-vertex-bodies"
    bodies.mkdir()
    for i, entry in enumerate(read(root / "review-dispatch-manifest.json")["entries"], 1):
        path = bodies / f"{i:02}.json"
        transport.save_new(path, stages.gemini_review_body(read(root / entry["request"])))
        paths.append(path)
    freeze(root, "review-freeze.json", paths, **result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def execution_summary(root: Path, state: str, elapsed: float | None = None):
    receipts = [read(p) for p in sorted((root / "provider").glob("*/*.receipt.json"))]
    usage = Counter()
    for result in receipts:
        usage.update({k: v for k, v in result.get("usageMetadata", {}).items()
                      if isinstance(v, int) and not isinstance(v, bool)})
    summary = {"createdUTC": transport.utc(), "state": state, "project": transport.PROJECT,
               "location": transport.LOCATION, "model_requested": transport.MODEL,
               "actual_generateContent_calls": len(receipts),
               "served_model_versions": dict(Counter(r.get("modelVersion", "unreported") for r in receipts)),
               "stage_calls": dict(Counter(r["stage"] for r in receipts)),
               "http_status_counts": dict(Counter(str(r["http_status"]) for r in receipts)),
               "parse_success": sum(r["parse_error"] is None for r in receipts),
               "usage_totals": dict(usage),
               "api_elapsed_seconds_sum": round(sum(r["elapsed_seconds"] for r in receipts), 6),
               "wall_elapsed_seconds": round(elapsed, 6) if elapsed is not None else None,
               "model_runtime_verified": bool(receipts) and all(r.get("modelVersion") for r in receipts),
               "gold_read_before_freeze": False}
    path = root / "execution-summary.json"
    transport.save_new(path, summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def run(root: Path):
    verify(root, "frozen-protocol.json")
    secret = transport.token()
    if not auth(root, secret)["authenticated"]:
        print("Authentication blocked; zero generateContent calls.", flush=True)
        return 2
    start = time.monotonic()
    try:
        execute_stage(root, "matching", secret)
        verify_provider_stage(root, "matching")
        if not (root / "matching-chunk-evaluation.json").exists():
            print(json.dumps(stages.collect_matching(root), ensure_ascii=False), flush=True)
        if not (root / "signature-freeze.json").exists():
            signature_prepare(root)
        execute_stage(root, "signature", secret)
        verify_provider_stage(root, "signature")
        if not (root / "review-freeze.json").exists():
            reviews_prepare(root)
        execute_stage(root, "review", secret)
        verify_provider_stage(root, "review")
        verify(root, "frozen-protocol.json")
        verify(root, "signature-freeze.json")
        verify(root, "review-freeze.json")
        print(json.dumps(stages.reviews_collect(root)["counts"], ensure_ascii=False), flush=True)
    except Exception as exc:
        # Never print exception messages from an execution path.
        transport.save_new(root / "execution-error.json", {"exception_type": type(exc).__name__,
                                                            "remaining_inference_stopped": True})
        execution_summary(root, "incomplete", time.monotonic() - start)
        return 2
    paths = [p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
             and p.name not in {"execution-summary.json", "grading-freeze.json"}]
    freeze(root, "grading-freeze.json", paths, gold_outside_checkout=True, inference_complete=True)
    execution_summary(root, "complete", time.monotonic() - start)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "auth", "run", "verify"))
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.run_dir.resolve()
    if not root.is_relative_to(EXP / "results"):
        parser.error("run directory must be inside experiments/sku-matching/results")
    if args.command == "prepare":
        prepare(root)
    elif args.command == "auth":
        return 0 if auth(root, transport.token())["authenticated"] else 2
    elif args.command == "run":
        return run(root)
    else:
        for name in ("frozen-protocol.json", "signature-freeze.json", "review-freeze.json", "grading-freeze.json"):
            if (root / name).exists():
                verify(root, name)
        print("All existing freezes verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
