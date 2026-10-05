"""Assemble strictly verified model outputs from separate GPU jobs.

Timed-out processes are rejected by default. The explicit recovery option only
accepts a completed MoE artifact set backed by an offline recovery receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys

from summarize_public_eval import EXPECTED_MODELS, IntegrityError, read_json, sha256_file, verify_model


def _receipt_files(job: Path, preferred_name: str | None = None) -> dict[str, dict]:
    identity = read_json(job / "job.json")
    if preferred_name:
        candidates = [job / preferred_name]
    else:
        candidates = [job / "terminal-recovery.json", job / "continuation-worker-result.json",
                     job / "continuation.json"]
    receipts = []
    for receipt_path in candidates:
        if not receipt_path.is_file():
            continue
        receipt = read_json(receipt_path)
        if receipt.get("ref") != identity.get("ref") or receipt.get("version") != identity.get("version"):
            continue
        if receipt.get("status") not in {"error", "complete", "success"}:
            continue
        if isinstance(receipt.get("files"), list):
            receipts.append((receipt_path, receipt))
    require(receipts, f"No matching downloaded-file receipt for job {job}")
    # Prefer the continuation worker's complete result if there are duplicate
    # snapshots; otherwise use terminal-recovery, then continuation.
    rank = {"continuation-worker-result.json": 0, "terminal-recovery.json": 1,
            "continuation.json": 2}
    receipt_path, receipt = min(receipts, key=lambda pair: rank.get(pair[0].name, 3))
    files = receipt["files"]
    result = {}
    for item in files:
        rel = item.get("path")
        require(isinstance(rel, str), "Invalid path in terminal receipt")
        posix = PurePosixPath(rel)
        require(not posix.is_absolute() and all(part not in {"", ".", ".."} for part in posix.parts),
                f"Unsafe path in terminal receipt: {rel!r}")
        require(rel not in result, f"Duplicate path in terminal receipt: {rel}")
        result[rel] = item
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def verify_receipted_files(job: Path, relative_paths: list[str], *, receipt_name: str | None = None) -> dict[str, dict]:
    files = _receipt_files(job, preferred_name=receipt_name)
    verified = {}
    for rel in relative_paths:
        require(rel in files, f"No terminal SHA receipt for {job.name}/{rel}")
        path = job / "artifacts" / rel
        require(path.is_file(), f"Missing receipted artifact: {path}")
        receipt = files[rel]
        digest = sha256_file(path)
        size = path.stat().st_size
        require(receipt.get("sha256") == digest and receipt.get("bytes") == size,
                f"Terminal SHA/size receipt mismatch: {path}")
        verified[rel] = {"bytes": size, "sha256": digest}
    return verified


def _model_paths(job: Path, model: str) -> list[str]:
    slug = model.lower().replace("-", "_")
    paths = ["environment.json", "public-eval-summary.json",
             f"public-eval-{slug}.log.txt"]
    folder = job / "artifacts" / f"public-eval-result-{slug}"
    require(folder.is_dir(), f"Missing model output folder: {folder}")
    paths.extend(str(path.relative_to(job / "artifacts").as_posix())
                 for path in sorted(folder.rglob("*")) if path.is_file())
    scripts = job / "artifacts" / "public-eval-scripts"
    require(scripts.is_dir(), f"Missing source script folder: {scripts}")
    paths.extend(str(path.relative_to(job / "artifacts").as_posix())
                 for path in sorted(scripts.rglob("*")) if path.is_file())
    return paths


def _verify_model_from_job(run: Path, job: Path, model: str, manifest: dict,
                           manifest_sha: str, labels: list[str]) -> dict:
    slug = model.lower().replace("-", "_")
    artifacts = job / "artifacts"
    return verify_model(artifacts, slug, model, manifest, manifest_sha, labels)


def _verify_recovery_receipt(receipt_path: Path, run: Path, repaired_job: Path,
                             manifest_sha: str) -> dict:
    receipt = read_json(receipt_path)
    identity = read_json(repaired_job / "job.json")
    require(receipt.get("verification_status") == "artifact_verification_passed_with_source_runtime_error",
            "Recovery receipt does not record verified artifacts with a source runtime error")
    require(receipt.get("model") == "MoE-ViE-B16", "Recovery receipt is not for the MoE output")
    require(receipt.get("ref") == identity.get("ref") and receipt.get("version") == identity.get("version"),
            "Recovery receipt does not identify the supplied source job")
    require(receipt.get("manifest_sha256") == manifest_sha, "Recovery receipt manifest SHA mismatch")
    require(receipt.get("run_dir") == str(run.resolve()), "Recovery receipt run path mismatch")
    proc = receipt.get("original_process_status", {})
    require(proc.get("returncode") is None and proc.get("timed_out") is True,
            "Recovery receipt does not preserve the timed-out process status")
    require(receipt.get("notebook_job_status") == "error", "Recovery receipt does not preserve notebook error")
    return receipt


def merge_jobs(run, previous_job, repaired_job, *, recover_completed_timeout=False,
               recovery_receipt=None):
    run, previous_job, repaired_job = Path(run).resolve(), Path(previous_job).resolve(), Path(repaired_job).resolve()
    target = run / "gpu-eval/combined-artifacts"
    if target.exists():
        raise FileExistsError(target)
    old = read_json(previous_job / "artifacts/public-eval-summary.json")
    new = read_json(repaired_job / "artifacts/public-eval-summary.json")
    require(old["input_manifest_sha256"] == new["input_manifest_sha256"],
            "Repair input manifest differs from the earlier run")
    manifest_path = run / "public_full.json"
    manifest = read_json(manifest_path)
    manifest_sha = sha256_file(manifest_path)
    require(manifest_sha == old["input_manifest_sha256"], "GPU artifacts do not match the run's pinned manifest")
    manifest["_run_dir"] = str(run)
    labels = [item["label"] for item in manifest["classes"]]
    selected = {"TinyCLIP-ViT-40M-32-Text-19M": previous_job,
                "MobileCLIP2-S0": previous_job, "MobileCLIP2-S2": previous_job,
                "MoE-ViE-B16": repaired_job}
    statuses, provenance, environments, verified_models = {}, {}, {}, {}
    recovery = None
    if recover_completed_timeout:
        if recovery_receipt is None:
            raise IntegrityError("--recover-completed-timeout requires --recovery-receipt")
        recovery = _verify_recovery_receipt(Path(recovery_receipt).resolve(), run, repaired_job, manifest_sha)
        terminal = read_json(repaired_job / "terminal-recovery.json")
        require(terminal.get("status") == "error" and terminal.get("ready_for_verification") is False,
                "Completed-timeout recovery requires an error-state terminal-recovery receipt")
        verify_receipted_files(repaired_job, _model_paths(repaired_job, "MoE-ViE-B16"),
                               receipt_name="terminal-recovery.json")
    for name, job in selected.items():
        summary = old if job == previous_job else new
        status = summary["models"][name]
        recovered = name == "MoE-ViE-B16" and job == repaired_job and recover_completed_timeout
        if recovered:
            require(status.get("returncode") is None and status.get("timed_out") is True,
                    "Opt-in timeout recovery applies only to an unresolved timed-out MoE process")
            require(summary.get("status") == "error", "Recovered source notebook error must remain recorded")
        elif status.get("returncode") != 0 or status.get("timed_out"):
            raise IntegrityError(f"Model did not finish successfully: {name}")
        identity = read_json(job / "job.json")
        env = read_json(job / "artifacts/environment.json")
        receipt_paths = _model_paths(job, name)
        hashes = verify_receipted_files(job, receipt_paths)
        model_result = _verify_model_from_job(run, job, name, manifest, manifest_sha, labels)
        if recovered:
            require(model_result["verified_sample_count"] == 4068 and
                    model_result["verified_board_count"] == 1000,
                    "Recovered MoE did not pass complete strict verification")
            require(model_result["model_metrics"].get("accuracy") == recovery.get("model_accuracy"),
                    "Recovery receipt accuracy differs from strict verification")
        environments[name] = env
        verified_models[name] = model_result
        provenance[name] = {
            "ref": identity["ref"], "version": identity["version"],
            "source_artifacts": str(job / "artifacts"),
            "source_signature": identity["source_signature"],
            "process_status": status,
            "artifact_verification": "passed",
            "source_runtime_error": bool(recovered),
            "terminal_sha_receipts_verified": hashes,
            "offline_recovery_receipt": str(Path(recovery_receipt).resolve()) if recovered else None,
        }
        # Preserve the original process status verbatim, including timed_out/returncode.
        statuses[name] = status
    hardware = ("gpu_name", "cuda_runtime", "torch")
    first = environments[next(iter(selected))]
    if any(any(env.get(key) != first.get(key) for key in hardware) for env in environments.values()):
        raise IntegrityError("Repaired run hardware/software differs; report separately")

    target.mkdir(parents=True)
    for name, job in selected.items():
        slug = name.lower().replace("-", "_")
        source = job / "artifacts"
        shutil.copytree(source / ("public-eval-result-" + slug), target / ("public-eval-result-" + slug))
        shutil.copyfile(source / ("public-eval-" + slug + ".log.txt"), target / ("public-eval-" + slug + ".log.txt"))
    for job in (previous_job, repaired_job):
        identity = read_json(job / "job.json")
        shutil.copytree(job / "artifacts/public-eval-scripts", target / f"scripts-version-{identity['version']}")
    runtime_errors = {}
    if recovery:
        runtime_errors["MoE-ViE-B16"] = {
            "job_status": "error",
            "reason": "MoE subprocess timed out after emitting model_complete and saving output artifacts",
            "returncode": None,
            "timed_out": True,
            "timeout_seconds": statuses["MoE-ViE-B16"].get("timeout_seconds"),
            "offline_artifact_recovery": True,
        }
    env = {**first, "model_run_provenance": provenance,
           "model_runtime_environments": environments, "separate_jobs": True,
           "source_job_runtime_errors": runtime_errors,
           "artifact_verification_status": "strict_source_checks_passed",
           "recovery_mode": "completed_timeout_artifact_verification" if recovery else None}
    (target / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    summary = {
        "status": "artifacts_assembled_with_source_runtime_error" if recovery else "success",
        "artifact_verification_status": "strict_source_checks_passed",
        "models": statuses, "input_manifest_sha256": old["input_manifest_sha256"],
        "assembled_from_separate_jobs": True, "model_run_provenance": provenance,
        "source_job_runtime_errors": runtime_errors,
        "offline_recovery_receipt": str(Path(recovery_receipt).resolve()) if recovery else None,
    }
    (target / "public-eval-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (target / "source-model-verification.json").write_text(json.dumps(verified_models, indent=2) + "\n")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--previous-job", type=Path, required=True)
    parser.add_argument("--repaired-job", type=Path, required=True)
    parser.add_argument("--recover-completed-timeout", action="store_true",
                        help="explicitly allow only a strictly verified completed MoE timeout")
    parser.add_argument("--recovery-receipt", type=Path,
                        help="offline recovery-verification.json created by recover_public_outputs.py")
    args = parser.parse_args()
    try:
        result = merge_jobs(args.run, args.previous_job, args.repaired_job,
                            recover_completed_timeout=args.recover_completed_timeout,
                            recovery_receipt=args.recovery_receipt)
    except (IntegrityError, FileExistsError) as exc:
        print(f"merge refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps({"artifact_dir": str(result), "status": "assembled_with_source_runtime_error"
                      if args.recover_completed_timeout else "success"}))


if __name__ == "__main__":
    main()
