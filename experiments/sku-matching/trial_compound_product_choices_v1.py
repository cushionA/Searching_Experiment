#!/usr/bin/env python3
"""Compare whole source options using fixed product-global CPU JEV evidence.

This is an exploratory, dev-informed wording trial. Existing inference, source
captures, and retrieval rankings are read-only. New predictions and all gates
are frozen before the existing machine references are opened for scoring.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import time

import compound_product_choices_v1 as COMPOUND
import evaluate_mixed_gpu_sku_v1 as E
import evaluate_keyword_filtered_product_v1 as REFERENCES
import product_condition_sharing_v1 as SHARING
import trial_keyword_filtered_product_v1 as VERIFY
import trial_product_condition_models_v1 as PRODUCT

ROOT = PRODUCT.ROOT
CONTENT_VERSION = "compound-product-choices-v1/whole-axis+option/actual-specs/top3-min2"
VARIANT = "product-top3-jev"
DEFAULT_PREP = ROOT / ".lab-output/sku-mixed-gpu-prep-20261012-v5"
DEFAULT_CPU = ROOT / ".lab-output/sku-mixed-cpu-run-20261012-v1"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-compound-product-choices-20261012-v1"
DEFAULT_OLD_EVAL = ROOT / ".lab-output/sku-mixed-cpu-evaluation-20261012-v4"
DEFAULT_ROUTES = ROOT / ".lab-output/sku-mixed-cpu-reference-routes-20261012-v2"
DEFAULT_COMPARISON = ROOT / ".lab-output/sku-baseline-ablation-20261012-v2"
DEFAULT_MODEL = ROOT / ".deps/sku-fixed-models/jev"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def index(path, key):
    return E.GATE.indexed(Path(path), key)


def run(prepared_dir, cpu_dir, output_dir, jev_model_dir, old_evaluation_dir,
        reference_routes_dir, comparison_dir):
    prepared_dir, cpu_dir, output_dir, jev_model_dir, old_evaluation_dir, reference_routes_dir, comparison_dir = (
        Path(p).resolve() for p in (prepared_dir, cpu_dir, output_dir, jev_model_dir,
                                   old_evaluation_dir, reference_routes_dir, comparison_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    started = time.perf_counter()
    source_dir = cpu_dir / "product-model"
    source = VERIFY.verify_source(source_dir)
    manifest, requests, documents, tasks, cases, membership, hosts = E.load_mixed_prepared(prepared_dir)
    cpu_summary, _ = E.verify_cpu_run(prepared_dir, cpu_dir, manifest, requests)
    fusion, _, eligible = E.check_prediction_coverage(prepared_dir, cpu_dir, requests, documents, tasks, cpu_summary)
    bindings = {rid: p["source_task_bindings"] for rid, p in fusion.items()}
    baseline_dir = cpu_dir / "baseline-refresh/baseline-model"
    baseline_summary = read_json(baseline_dir / "summary.json")
    for name, want in baseline_summary["output_sha256"].items():
        assert E.sha(baseline_dir / name) == want, name
    saved = {v: index(baseline_dir / (v + "-predictions.jsonl"), "request_id") for v in ("jev", "nli")}
    baseline = {rid: saved["nli" if r["direction"] == "rakuten_to_au" else "jev"][rid]
                for rid, r in requests.items()}
    assert len(baseline) == 134 and len(set(requests) - eligible) == 54
    for rid, pred in baseline.items():
        assert pred["source_request"] == requests[rid]
        assert pred["source_task_bindings"] == bindings[rid]
    E.GATE.validate_predictions(requests, documents, tasks, baseline, baseline, "baseline")

    identity = deepcopy(source["summary"]["model_identity"])
    identity.update(model_id="sku-compound-product-global-jev", revision=CONTENT_VERSION,
                    prompt_builder_sha256=E.sha(Path(COMPOUND.__file__)), variants=[VARIANT],
                    verification="two distinct original-ranked blocks choose the same whole raw option; JEV only",
                    nonshared_policy="saved NLI forward / saved JEV reverse")
    plan = SHARING.build_groups(requests, documents, bindings, model_identity=identity,
                                content_version=CONTENT_VERSION, invariance_contract=source["contract"])
    groups = plan["groups"]
    assert len(groups) == 29 and set(plan["request_to_group"]) == eligible
    old_by_requests = {tuple(sorted(g["request_ids"])): g for g in source["groups"]}
    assert len(old_by_requests) == len(groups)
    rankings, mappings = {}, []
    unchanged_fields = ("axis_name", "option_values", "canonical_au_url", "target_document_id",
                        "target_document_sha256", "title", "description")
    for group in groups:
        old = old_by_requests[tuple(sorted(group["request_ids"]))]
        assert all(group[k] == old[k] for k in unchanged_fields)
        old_ranking = source["selections"][old["group_id"]]
        ranking = deepcopy(old_ranking)
        ranking["request_id"] = group["group_id"]
        rankings[group["group_id"]] = ranking
        mappings.append({"group_id": group["group_id"], "source_group_id": old["group_id"],
                         "request_ids": group["request_ids"],
                         "ordered_block_ids": deepcopy(old_ranking["ordered_block_ids"])})
    choices = COMPOUND.make_classification_requests(groups, source["blocks"], rankings)
    original_choices = PRODUCT.classification_requests(source["groups"], source["blocks"], source["selections"])
    original_by_block = {(r["group_id"], r["block_id"]): r for r in original_choices}
    mapping = {r["group_id"]: r["source_group_id"] for r in mappings}
    for request in choices:
        old = original_by_block[mapping[request["group_id"]], request["block_id"]]
        assert all(request[k] == old[k] for k in ("block_id", "block_rank", "state", "all_original_option_values"))
        assert [c["label"] for c in request["choices"]] == [c["label"] for c in old["choices"]]
        assert PRODUCT.JEV.request_key(request["question"], request["state"], request["choices"]) != PRODUCT.JEV.request_key(old["question"], old["state"], old["choices"])
    assert len(choices) == len(original_choices) == 83
    output_dir.mkdir(parents=True, exist_ok=False)
    E.write_json(output_dir / "sharing-plan.json", plan)
    E.write_json(output_dir / "sharing-contract.json", source["contract"])
    E.write_rows(output_dir / "product-groups.jsonl", groups)
    E.write_rows(output_dir / "source-group-mapping.jsonl", mappings)
    E.write_rows(output_dir / "retrieval-selections.jsonl", rankings.values())
    E.write_rows(output_dir / "baseline-predictions.jsonl", baseline.values())
    E.write_rows(output_dir / "model-requests.jsonl", choices)
    code_sources = {name: PRODUCT.HERE / ("manifests/reranker.json" if name == "reranker.json" else name)
                    for name in source["summary"]["code_sha256"]}
    for module in (E, REFERENCES, VERIFY, COMPOUND):
        code_sources[Path(module.__file__).name] = Path(module.__file__)
    code_sources[Path(__file__).name] = Path(__file__)
    code_dir = output_dir / "code"
    code_dir.mkdir()
    for name, path in code_sources.items():
        shutil.copyfile(path, code_dir / name)
    paths = [prepared_dir / "manifest.json", *(prepared_dir / n for n in E.PREP_FILES),
             cpu_dir / "summary.json", cpu_dir / "cpu-source-freeze.json", cpu_dir / "execution-freeze.json",
             *(source_dir / n for n in VERIFY.SOURCE_ARTIFACTS),
             source_dir / "product-top3-jev-group-results.jsonl", baseline_dir / "summary.json",
             *(baseline_dir / (v + "-predictions.jsonl") for v in ("jev", "nli")),
             *code_sources.values(), *(output_dir / n for n in ("sharing-plan.json", "sharing-contract.json",
              "product-groups.jsonl", "source-group-mapping.jsonl", "retrieval-selections.jsonl",
              "baseline-predictions.jsonl", "model-requests.jsonl")), *code_dir.iterdir()]
    snapshot = VERIFY.snapshot_paths({str(p): p for p in paths})
    E.write_json(output_dir / "execution-freeze.json", {
        "schema_version": "sku-compound-product-choices-v1", "experiment_status": "exploratory_dev_informed",
        "labels_read": False, "independent_test": False, "production_adoption_allowed": False,
        "errors_only": False, "source_summary_legacy_errors_only_flag": source["summary"]["errors_only"],
        "scope_authority": "source-manifest.errors_only=false; source legacy flag not edited",
        "content_version": CONTENT_VERSION, "model_identity": identity, "source_snapshot": snapshot,
        "case_count": 83, "request_count": 134, "group_count": 29, "eligible_request_count": 80,
        "passthrough_request_count": 54, "new_models": False, "cache_reused_from_old_wording": False,
        "selected_axes_model_input": False, "wanted_value_separately_injected": False,
        "all_raw_original_options_preserved": True, "unknown_control_retained": True,
        "top_k": 3, "minimum_votes": 2, "threads": 4, "batch_size": 16,
        "device": "cpu", "jev_precision": "fp32", "new_input": "question and choice descriptions only",
        "fixed_inputs": "state, complete original options/order, original retrieval ranking, majority and gate",
        "nonshared_policy": "saved NLI forward / saved JEV reverse",
        "new_reranker_inference": False, "new_nli_inference": False})
    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    with PRODUCT.FULL.Progress(output_dir) as progress:
        progress.emit(stage="compound-choices-start", classification_request_count=len(choices))
        records, runtime, pins = PRODUCT.score_jev(choices, PRODUCT.JEV.run_choices, jev_model_dir,
                                                  4, 16, output_dir, progress)
        assert PRODUCT.JEV._same_pins(pins)
        assert pins["device"] == "cpu" and pins["precision"] == "fp32"
        VERIFY.verify_snapshot(snapshot, phase="after inference")
        results = PRODUCT.group_results(groups, records, [], documents, source["blocks"], VARIANT)
        E.write_rows(output_dir / "group-results.jsonl", results.values())
        predictions = SHARING.fan_out(plan, results, baseline)
        predicted = {p["request_id"]: p for p in predictions}
        E.SHARED.validate_shared_predictions(requests, documents, tasks, predicted, baseline, eligible, VARIANT)
        assert all(predicted[k] == baseline[k] for k in set(requests) - eligible)
        E.write_rows(output_dir / "predictions.jsonl", predictions)
        E.GATE.finish(prepared_dir, output_dir / "predictions.jsonl", output_dir / "gates")
        gates = index(output_dir / "gates/decisions.jsonl", "case_id")
        assert set(gates) == set(cases)
        progress.emit(stage="all-gates-complete", case_count=len(gates))
    model_and_gate_files = [output_dir / n for n in ("group-results.jsonl", "predictions.jsonl",
                            "jev-unique-scores.jsonl", "jev-classification-options.jsonl", "progress.json")]
    model_and_gate_files.extend(p for p in (output_dir / "gates").rglob("*") if p.is_file())
    gate_snapshot = VERIFY.snapshot_paths({str(p): p for p in model_and_gate_files})
    E.write_json(output_dir / "all-gates-frozen.json", {"labels_read": False, "all_gates_frozen": True,
                 "case_count": 83, "request_count": 134, "model_and_gate_snapshot": gate_snapshot})
    _, _, _, references, _ = REFERENCES.verify_original_sources(prepared_dir, old_evaluation_dir, reference_routes_dir)
    scored_ids = {r["case_id"] for r in E.eligibility_rows(membership, hosts)[0] if r["primary_score_eligible"]}
    scored = {k: v for k, v in references.items() if k in scored_ids and not v.get("reference_uncertain", False)}
    assert len(scored) == 73
    comparison_summary = read_json(comparison_dir / "summary.json")
    for name, want in comparison_summary["output_sha256"].items():
        assert E.sha(comparison_dir / name) == want, name
    current_gates = index(comparison_dir / "forward-nli-reverse-jev/gates/decisions.jsonl", "case_id")
    metrics, detail = {}, []
    for stratum in E.STRATA:
        subset = {k: v for k, v in scored.items() if membership[k]["membership_strata"] == [stratum]}
        value = E.metrics_for(gates, subset)
        metrics[stratum] = {k: v for k, v in value.items() if k != "case_results"}
        detail.extend({"stratum": stratum, **r} for r in value["case_results"])
    value = E.metrics_for(gates, scored)
    metrics["scored_primary_reference_union"] = {k: v for k, v in value.items() if k != "case_results"}
    before = {r["case_id"]: r for r in E.metrics_for(current_gates, scored)["case_results"]}
    after = {r["case_id"]: r for r in value["case_results"]}
    changes = [{"case_id": k, "before": before[k], "after": after[k]} for k in sorted(after) if before[k] != after[k]]
    decision_changes = [{"case_id": k, "before": current_gates[k], "after": gates[k]} for k in sorted(gates)
                        if current_gates[k]["decision"] != gates[k]["decision"] or current_gates[k].get("top_row_key") != gates[k].get("top_row_key")]
    E.write_rows(output_dir / "case-results.jsonl", detail)
    E.write_rows(output_dir / "changes-vs-current-candidate.jsonl", changes)
    E.write_rows(output_dir / "decision-changes-all-cases.jsonl", decision_changes)
    VERIFY.verify_snapshot(snapshot, phase="after scoring")
    VERIFY.verify_snapshot(gate_snapshot, phase="after scoring")
    E.verify_cpu_run(prepared_dir, cpu_dir, manifest, requests)
    VERIFY.verify_source(source_dir)
    summary = {"schema_version": "sku-compound-product-choices-result-v1", "experiment_status": "exploratory_dev_informed",
               "independent_test": False, "production_adoption_allowed": False, "reference_kind": "existing machine labels",
               "scored_case_count": 73, "all_case_count": 83, "labels_read_after_all_gates_frozen": True,
               "source_hashes_verified_after_inference_and_scoring": True, "verified_jev_pin": pins,
               "jev_runtime": runtime, "total_seconds": time.perf_counter() - started,
               "new_reranker_inference": False, "new_nli_inference": False,
               "comparison_policy": "original shared top3 JEV plus saved nonshared NLI forward/JEV reverse",
               "metrics": metrics, "changed_scored_case_count": len(changes), "changed_all_case_count": len(decision_changes),
               "before_union": comparison_summary["variants"]["forward-nli-reverse-jev"]["scored_primary_reference_union"],
               "output_sha256": {str(p.relative_to(output_dir)): E.sha(p) for p in output_dir.rglob("*") if p.is_file()}}
    E.write_json(output_dir / "summary.json", summary)
    print(json.dumps({"stage": "scored", "metrics": metrics["scored_primary_reference_union"],
                      "changed_cases": len(changes)}, ensure_ascii=False), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (("prepared-dir", DEFAULT_PREP), ("cpu-dir", DEFAULT_CPU),
                          ("output-dir", DEFAULT_OUTPUT), ("jev-model-dir", DEFAULT_MODEL),
                          ("old-evaluation-dir", DEFAULT_OLD_EVAL), ("reference-routes-dir", DEFAULT_ROUTES),
                          ("comparison-dir", DEFAULT_COMPARISON)):
        parser.add_argument("--" + name, type=Path, default=default)
    run(**vars(parser.parse_args()))
