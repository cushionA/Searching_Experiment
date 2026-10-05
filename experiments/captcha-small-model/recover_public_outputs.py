#!/usr/bin/env python3
"""Offline recovery of a completed, saved model output from a timed-out job.

The source job remains errored and the process remains marked timed out. This
tool only verifies saved artifacts, combines them with strictly successful
outputs from an earlier job, and creates an explicitly qualified report.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

from merge_public_gpu import merge_jobs
from summarize_public_eval import IntegrityError, read_json, sha256_file, summarize, verify_model


MODEL = "MoE-ViE-B16"
SLUG = "moe_vie_b16"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def verify_terminal_recovery(job: Path) -> dict:
    job = job.resolve()
    identity = read_json(job / "job.json")
    receipt = read_json(job / "terminal-recovery.json")
    require(receipt.get("ref") == identity.get("ref") and receipt.get("version") == identity.get("version"),
            "terminal-recovery.json does not match job ref/version")
    require(receipt.get("status") == "error" and receipt.get("ready_for_verification") is False,
            "Expected source job to remain terminal error, not ready/success")
    files = receipt.get("files")
    require(isinstance(files, list) and files, "terminal-recovery.json has no downloaded file receipts")
    seen = set()
    verified = []
    for item in files:
        rel = item.get("path")
        require(isinstance(rel, str), "Invalid file path in terminal-recovery.json")
        posix = PurePosixPath(rel)
        require(not posix.is_absolute() and all(part not in {"", ".", ".."} for part in posix.parts),
                f"Unsafe terminal receipt path: {rel!r}")
        require(rel not in seen, f"Duplicate terminal file receipt: {rel}")
        seen.add(rel)
        path = job / "artifacts" / Path(*posix.parts)
        require(path.is_file(), f"Downloaded artifact is missing: {path}")
        actual_size = path.stat().st_size
        actual_sha = sha256_file(path)
        require(item.get("bytes") == actual_size and item.get("sha256") == actual_sha,
                f"Downloaded file does not match terminal SHA receipt: {rel}")
        verified.append({"path": rel, "bytes": actual_size, "sha256": actual_sha})
    required = {
        "environment.json",
        "public-eval-summary.json",
        f"public-eval-{SLUG}.log.txt",
        f"public-eval-result-{SLUG}/{MODEL}.jsonl",
        f"public-eval-result-{SLUG}/aggregate.json",
        f"public-eval-result-{SLUG}/run_protocol.json",
    }
    require(required.issubset(seen), f"Terminal receipts omit required MoE artifacts: {sorted(required - seen)}")
    for script in (job / "artifacts/public-eval-scripts").rglob("*"):
        if script.is_file():
            rel = script.relative_to(job / "artifacts").as_posix()
            require(rel in seen, f"Terminal receipts omit source script: {rel}")
    return {"file_count": len(verified), "files": verified,
            "terminal_status": receipt["status"], "ready_for_verification": receipt["ready_for_verification"]}


def parse_complete_log(job: Path) -> tuple[dict, dict]:
    summary = read_json(job / "artifacts/public-eval-summary.json")
    model_status = summary.get("models", {}).get(MODEL, {})
    require(summary.get("status") == "error", "Source notebook summary must retain error status")
    require(model_status.get("returncode") is None and model_status.get("timed_out") is True,
            "Source model process is not recorded as the expected timeout")
    require(model_status.get("timeout_seconds") == 1200,
            "Unexpected source subprocess timeout; recovery is pinned to the recorded 1200-second run")
    log_path = job / "artifacts" / f"public-eval-{SLUG}.log.txt"
    complete_events, last_batch_events = [], []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("status") == "model_complete":
            complete_events.append(event)
        if isinstance(event, dict) and event.get("model") == MODEL and "batch" in event:
            last_batch_events.append(event)
    require(len(complete_events) == 1, f"Expected exactly one structured {MODEL} model_complete record")
    complete = complete_events[0]
    require(set(complete) == {"status", "model", "accuracy"} and
            complete["status"] == "model_complete" and complete["model"] == MODEL,
            "model_complete record has an unexpected structure or model")
    require(isinstance(complete["accuracy"], (int, float)) and not isinstance(complete["accuracy"], bool),
            "model_complete record has invalid accuracy")
    require(last_batch_events and last_batch_events[-1] == {
        "model": MODEL, "batch": 482, "batches": 482, "unique_images_done": 15420
    }, "MoE log does not end at the expected complete 482/482 batch progress record")
    tail = model_status.get("log_tail", "")
    require("TIMEOUT after 1200 seconds" in tail, "Original timeout marker is missing from the saved job summary")
    return complete, model_status


def recover(run: Path, previous_job: Path, timed_out_job: Path) -> dict:
    run, previous_job, timed_out_job = run.resolve(), previous_job.resolve(), timed_out_job.resolve()
    gpu_eval = run / "gpu-eval"
    recovery_dir = gpu_eval / "completed-timeout-recovery"
    merged_dir = gpu_eval / "combined-artifacts"
    summary_dir = gpu_eval / "summary-verified"
    require(not recovery_dir.exists(), f"Refusing to overwrite {recovery_dir}")
    require(not merged_dir.exists(), f"Refusing to overwrite {merged_dir}")
    require(not summary_dir.exists(), f"Refusing to overwrite {summary_dir}")
    manifest_path = run / "public_full.json"
    manifest = read_json(manifest_path)
    manifest_sha = sha256_file(manifest_path)
    require(len(manifest.get("samples", [])) == 4068 and len(manifest.get("cases", [])) == 1000,
            "Pinned source manifest counts do not match the public evaluation contract")

    file_receipts = verify_terminal_recovery(timed_out_job)
    complete_event, original_process_status = parse_complete_log(timed_out_job)
    strict_model = verify_model(timed_out_job / "artifacts", SLUG, MODEL,
                                {**manifest, "_run_dir": str(run)}, manifest_sha,
                                [item["label"] for item in manifest["classes"]])
    require(strict_model["verified_sample_count"] == 4068 and strict_model["verified_board_count"] == 1000,
            "Strict MoE verifier did not validate all samples and boards")
    accuracy = strict_model["model_metrics"].get("accuracy")
    require(accuracy == complete_event["accuracy"],
            "Strict aggregate accuracy disagrees with exact model_complete log record")
    environment = read_json(timed_out_job / "artifacts/environment.json")
    require(environment.get("manifest_sha256") == manifest_sha,
            "MoE environment manifest SHA differs from local pinned manifest")
    expected_env = read_json(previous_job / "artifacts/environment.json")
    for key in ("gpu_name", "cuda_runtime", "torch"):
        require(environment.get(key) == expected_env.get(key),
                f"MoE and earlier successful outputs differ in {key}")

    recovery_dir.mkdir(parents=True)
    recovery_path = recovery_dir / "recovery-verification.json"
    recovery_receipt = {
        "verification_status": "artifact_verification_passed_with_source_runtime_error",
        "recovery_mode": "offline_artifact_verification_only",
        "run_dir": str(run), "run_name": run.name,
        "manifest_sha256": manifest_sha,
        "ref": read_json(timed_out_job / "job.json")["ref"],
        "version": read_json(timed_out_job / "job.json")["version"],
        "model": MODEL,
        "notebook_job_status": "error",
        "original_process_status": original_process_status,
        "process_status_changed": False,
        "model_complete_record": complete_event,
        "last_batch_progress": {"batch": 482, "batches": 482, "unique_images_done": 15420},
        "model_accuracy": accuracy,
        "verified_sample_count": strict_model["verified_sample_count"],
        "verified_board_count": strict_model["verified_board_count"],
        "strict_model_metrics": strict_model,
        "terminal_recovery_receipts": file_receipts,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    recovery_path.write_text(json.dumps(recovery_receipt, indent=2) + "\n", encoding="utf-8")

    combined = merge_jobs(run, previous_job, timed_out_job,
                          recover_completed_timeout=True, recovery_receipt=recovery_path)
    report = summarize(run, combined, summary_dir)
    require(report["verified_model_count"] == 4, "Combined verifier did not validate exactly four models")
    require(report["artifact_verification_status"] == "all_four_models_verified_with_source_runtime_error",
            "Final summary does not preserve source runtime error qualification")
    return {"recovery_receipt": str(recovery_path), "combined_artifacts": str(combined),
            "summary_verified": str(summary_dir), "verification_status": report["artifact_verification_status"],
            "models": {name: {"accuracy": item["sample_metrics"]["accuracy"],
                              "correct": item["sample_metrics"]["correct"],
                              "samples": item["sample_metrics"]["count"],
                              "board_exact_count": item["board_metrics"]["board_exact_count"],
                              "boards": item["board_metrics"]["count"]}
                       for name, item in report["models"].items()},
            "source_job_runtime_error": report["source_job_runtime_errors"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--previous-job", type=Path, required=True,
                        help="terminal job containing the three successful models")
    parser.add_argument("--timed-out-job", type=Path, required=True,
                        help="terminal job with a saved completed MoE output and timeout")
    args = parser.parse_args()
    try:
        result = recover(args.run, args.previous_job, args.timed_out_job)
    except (IntegrityError, FileExistsError) as exc:
        print(f"recovery refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
