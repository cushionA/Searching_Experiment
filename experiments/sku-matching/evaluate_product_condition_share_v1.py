#!/usr/bin/env python3
"""Freeze shared-condition gates, then score only prior real SKU errors.

Product-global sharing is an explicit input contract. Original SKU and AU row
bindings survive; citations prove literal existence, not semantic correctness.
No threshold search, label mutation, full-data accuracy or independent test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import evaluate_scoped_html_trial_v1 as E

VARIANTS = ("baseline", "product-top1-jev", "product-top3-jev", "product-top3-verified")


def validate_shared_predictions(requests, documents, tasks, predictions, baseline, eligible, variant):
    """Recheck fanout provenance and prevent product-level row citations."""
    literals = E.validate_predictions(requests, documents, tasks, predictions, baseline, variant)
    for identity, prediction in predictions.items():
        if variant == "baseline" or identity not in eligible:
            if prediction != baseline[identity]:
                raise ValueError("unshared request must remain the exact baseline object")
        elif any(ref.get("field") == "selected_axes" for ref in prediction.get("quote_refs", [])):
            raise ValueError("shared condition cannot cite a varying AU selected row")
    return literals


def run(prepared_dir, run_dir, legacy_labels, novel_labels, output_dir):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest, requests, documents, tasks, cases = E.load_prepared(prepared_dir)
    contract_path = run_dir / "sharing-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if (contract.get("basis") != "user_asserted"
            or contract.get("sold_out_rows_excluded_upstream") is not True):
        raise ValueError("requires the explicit product-global invariant contract")
    raw_ids = contract.get("eligible_forward_request_ids")
    if not isinstance(raw_ids, list) or len(set(raw_ids)) != len(raw_ids):
        raise ValueError("invalid eligible request identities")
    eligible = set(raw_ids)
    if not eligible <= set(requests) or any(requests[k]["direction"] != "rakuten_to_au" for k in eligible):
        raise ValueError("shared identity must be an original forward condition")
    paths = [prepared_dir / name for name in ("manifest.json", *E.SOURCE_NAMES)]
    paths += [run_dir / (variant + "-predictions.jsonl") for variant in VARIANTS]
    paths += [contract_path, run_dir / "freeze.json", run_dir / "summary.json", Path(__file__), E.HERE / "integrate_claude_description_v1.py"]
    frozen_sha = {str(path): E.sha(path) for path in paths}
    predictions = {variant: E.indexed(run_dir / (variant + "-predictions.jsonl"), "request_id") for variant in VARIANTS}
    baseline = predictions["baseline"]
    literals = {variant: validate_shared_predictions(requests, documents, tasks, values, baseline, eligible, variant)
                for variant, values in predictions.items()}
    output_dir.mkdir(parents=True, exist_ok=False)
    E.write_json(output_dir / "freeze.json", {
        "schema_version": "product-condition-share-evaluation-v1", "labels_read": False,
        "model_inference_run": False, "threshold_search": False, "independent_test": False,
        "production_adoption_allowed": False, "full_dataset_accuracy_measured": False,
        "selection_policy": manifest["selection_policy"], "selected_error_cells": manifest["selected_error_cells"],
        "selected_error_counts": manifest["selected_error_counts"], "input_sha256": frozen_sha,
        "variants": list(VARIANTS), "eligible_request_count": len(eligible),
        "literal_quote_checks": literals, "literal_existence_is_not_scope_proof": True})
    gates, gate_manifests = {}, {}
    for variant in VARIANTS:
        destination = output_dir / variant
        gate_manifests[variant] = E.finish(prepared_dir, run_dir / (variant + "-predictions.jsonl"), destination)
        gates[variant] = E.indexed(destination / "decisions.jsonl", "case_id")
        if set(gates[variant]) != set(cases):
            raise ValueError("case coverage differs after shared-condition gate")
    E.write_json(output_dir / "all-gates-frozen.json", {
        "labels_read": False, "all_predictions_and_gates_frozen": True,
        "gate_manifest_sha256": {variant: E.sha(output_dir / variant / "manifest.json") for variant in VARIANTS}})
    # First reference access occurs after all variants and gates are frozen.
    label_sha = {str(path): E.sha(path) for path in (legacy_labels, novel_labels)}
    references, routed_counts = E.route_references(set(cases), legacy_labels, novel_labels, output_dir)
    results = {}
    for variant in VARIANTS:
        scoring = E.score(output_dir / variant, output_dir / "references-for-scoring.jsonl", output_dir / variant / "score-machine.json")
        comparison = E.compare_variant(gates["baseline"], gates[variant], references, manifest["selected_error_cells"])
        if comparison["binary_confusion_on_selected_previous_errors"] != {k: scoring["metrics"][k] for k in ("tp", "fp", "fn", "tn")}:
            raise ValueError("independent error comparison differs from the fixed scorer")
        results[variant] = {k: v for k, v in comparison.items() if k not in {"per_product", "case_results"}}
        results[variant].update(gated_decision_counts=gate_manifests[variant]["decision_counts"],
                                reference_kind="machine_labels_not_human_gold")
        E.write_rows(output_dir / variant / "case-results.jsonl", comparison["case_results"])
        E.write_json(output_dir / variant / "per-product-errors.json", comparison["per_product"])
    if any(E.sha(Path(path)) != checksum for path, checksum in {**frozen_sha, **label_sha}.items()):
        raise ValueError("source, prediction, code or reference changed during evaluation")
    summary = {"schema_version": "product-condition-share-evaluation-result-v1",
        "evaluation_scope": "capped previously incorrect real cases; not full-data or independent accuracy",
        "full_dataset_accuracy_measured": False, "independent_test": False,
        "production_adoption_allowed": False, "synthetic_data": False,
        "case_count": len(cases), "eligible_request_count": len(eligible),
        "variants": results, "reference_cohort_counts": routed_counts,
        "reference_sha256": label_sha, "original_inputs_verified_unchanged": True,
        "output_sha256": {p.name: E.sha(p) for p in output_dir.iterdir() if p.is_file()}}
    E.write_json(output_dir / "summary.json", summary)
    print(json.dumps({"variants": results, "case_count": len(cases)}, ensure_ascii=False), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prepared-dir", "run-dir", "legacy-labels", "novel-labels", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    run(**vars(parser.parse_args()))
