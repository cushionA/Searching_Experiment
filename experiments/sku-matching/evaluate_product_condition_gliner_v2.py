#!/usr/bin/env python3
"""Bind comparison inputs to completed model manifests before reference access."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import evaluate_product_condition_gliner_v1 as DRIVER

VARIANTS = DRIVER.VARIANTS
E = DRIVER.E.E


def verify_completed_run(run_dir):
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    freeze = json.loads((run_dir / "freeze.json").read_text(encoding="utf-8"))
    if (summary.get("schema_version") != "product-condition-gliner-v1"
            or summary.get("labels_read") is not False
            or summary.get("input_and_code_sha256_verified_after_inference") is not True
            or tuple(summary.get("variants", ())) != VARIANTS[1:]):
        raise ValueError("requires the completed fixed GLiNER model comparison")
    if any(summary.get(key) != value for key, value in freeze.items()):
        raise ValueError("completed model summary differs from pre-inference freeze")
    output_hashes = summary.get("output_sha256")
    required = {"freeze.json", "sharing-plan.json", "sharing-contract.json", "model-requests.jsonl"}
    required.update(variant + "-predictions.jsonl" for variant in VARIANTS)
    required.update(variant + "-group-results.jsonl" for variant in VARIANTS[1:])
    if not isinstance(output_hashes, dict) or not required <= set(output_hashes):
        raise ValueError("completed output manifest is missing required comparison inputs")
    for name, checksum in output_hashes.items():
        if Path(name).name != name or E.sha(run_dir / name) != checksum:
            raise ValueError("completed model output SHA256 differs: " + name)
    if freeze.get("model_requests_sha256") != E.sha(run_dir / "model-requests.jsonl"):
        raise ValueError("model request inputs differ from the pre-inference freeze")
    if freeze.get("new_sharing_plan_sha256") != E.sha(run_dir / "sharing-plan.json"):
        raise ValueError("sharing plan differs from the pre-inference freeze")
    for name, checksum in freeze.get("code_sha256", {}).items():
        if Path(name).name != name or E.sha(run_dir / "code" / name) != checksum:
            raise ValueError("frozen model code snapshot differs: " + name)
    return {"summary_sha256": E.sha(run_dir / "summary.json"),
            "freeze_sha256": E.sha(run_dir / "freeze.json"),
            "output_manifest_sha256_verified": True,
            "verified_output_file_count": len(output_hashes),
            "labels_read": False}


def run(prepared_dir, run_dir, legacy_labels, novel_labels, output_dir):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    verified = verify_completed_run(run_dir)
    code_sha = E.sha(Path(__file__))
    output_dir.mkdir(parents=True, exist_ok=False)
    E.write_json(output_dir / "completed-run-binding-freeze.json", {
        **verified, "schema_version": "product-condition-gliner-bound-evaluation-v2",
        "driver_code_sha256": code_sha, "variants": list(VARIANTS)})
    (output_dir / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    result = DRIVER.run(prepared_dir, run_dir, legacy_labels, novel_labels,
                        output_dir / "bound-evaluation")
    if verify_completed_run(run_dir) != verified or E.sha(Path(__file__)) != code_sha:
        raise ValueError("completed model run or bound driver changed during scoring")
    summary = {**result, "schema_version": "product-condition-gliner-bound-evaluation-result-v2",
        "completed_run_binding": verified,
        "fixed_gate_directory": "bound-evaluation/fixed-gates",
        "output_sha256": {str(p.relative_to(output_dir)): E.sha(p)
                          for p in sorted(output_dir.rglob("*")) if p.is_file()}}
    E.write_json(output_dir / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prepared-dir", "run-dir", "legacy-labels", "novel-labels", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    run(**vars(parser.parse_args()))
