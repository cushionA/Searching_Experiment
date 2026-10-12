#!/usr/bin/env python3
"""Validate and score the frozen mixed development GPU replay by source stratum.

All prediction gates and their freeze record are written before any reference
file is opened. References must be explicitly routed to selected source cases;
independent-test records are rejected as out of scope.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import evaluate_scoped_html_trial_v1 as GATE
import evaluate_product_condition_share_v1 as SHARED
import trial_product_condition_models_v1 as PRODUCT

ROOT = Path(__file__).resolve().parents[2]
PREP_SCHEMA = "mixed-real-gpu-sku-inputs-v1"
STRATA = ("prior_errors", "independent_dev", "old_structural_control", "product_pair_diagnostic")
EXPECTED_COUNTS = {"prior_errors": 44, "independent_dev": 31,
                   "old_structural_control": 7, "product_pair_diagnostic": 1}
VARIANTS = ("baseline", "product-top1-jev", "product-top3-jev", "product-top3-verified")
PREDICTION_VARIANTS = VARIANTS[1:]
PREP_FILES = (*GATE.SOURCE_NAMES, "membership.jsonl", "gpu-cases.jsonl", "failures.jsonl")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def write_rows(path: Path, values):
    with path.open("x", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False) + "\n")


def load_mixed_prepared(prepared_dir: Path, expected_counts=EXPECTED_COUNTS):
    manifest = json.loads((prepared_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != PREP_SCHEMA or manifest.get("phase") != "prepare"
            or manifest.get("labels_read") is not False or manifest.get("labels_in_model_inputs") is not False
            or manifest.get("model_inference_run") is not False or manifest.get("development_only") is not True
            or manifest.get("errors_only") is not False
            or manifest.get("independent_test") is not False or manifest.get("synthetic") is not False
            or manifest.get("production_adoption_allowed") is not False):
        raise ValueError("unsupported mixed development preparation contract")
    for name in PREP_FILES:
        if manifest.get("output_sha256", {}).get(name) != sha(prepared_dir / name):
            raise ValueError("mixed prepared file SHA differs: " + name)
    requests = GATE.indexed(prepared_dir / "requests.jsonl", "request_id")
    documents = GATE.indexed(prepared_dir / "documents.jsonl", "document_id")
    tasks = GATE.indexed(prepared_dir / "tasks.jsonl", "task_id")
    cases = GATE.indexed(prepared_dir / "case_plans.jsonl", "case_id")
    membership = GATE.indexed(prepared_dir / "membership.jsonl", "case_id")
    gpu_cases = GATE.indexed(prepared_dir / "gpu-cases.jsonl", "case_id")
    failures = list(rows(prepared_dir / "failures.jsonl"))
    row_plans = list(rows(prepared_dir / "row_plans.jsonl"))
    if failures:
        raise ValueError("mixed preparation contains source-incomplete cases; full request coverage is impossible")
    if set(membership) != set(cases) or set(gpu_cases) != set(cases):
        raise ValueError("membership, host cases and case plans do not cover the same identities")
    if len(membership) != manifest.get("membership_case_count") or len(cases) != manifest.get("case_count"):
        raise ValueError("mixed manifest case counts differ")
    strata_counts = Counter()
    for identity, member in membership.items():
        strata = member.get("membership_strata")
        if not isinstance(strata, list) or len(strata) != 1 or strata[0] not in STRATA:
            raise ValueError("each mixed case must have exactly one declared source stratum")
        strata_counts[strata[0]] += 1
        case = gpu_cases[identity]
        if (case.get("case_id") != identity or case.get("memberships") != member.get("memberships")
                or case.get("source_strata") != strata):
            raise ValueError("GPU host case and selection membership differ: " + identity)
        selection_eligible = all(item.get("primary_score_eligible", False)
                                 for item in member.get("memberships", []))
        if case.get("selection_primary_score_eligible", case.get("primary_score_eligible")) is not selection_eligible:
            raise ValueError("membership and host selection eligibility differ: " + identity)
        if case.get("primary_score_eligible") is True and not selection_eligible:
            raise ValueError("host eligibility promotes a diagnostic selection: " + identity)
        if selection_eligible and case.get("primary_score_eligible") is not True and not case.get("source_flags"):
            raise ValueError("host eligibility excludes a selected primary case without source diagnosis: " + identity)
    if dict(strata_counts) != manifest.get("stratum_case_counts"):
        raise ValueError("membership strata differ from frozen preparation counts")
    if expected_counts is not None and dict(strata_counts) != expected_counts:
        raise ValueError("mixed cohort counts differ from the frozen 44/31/7/1 selection")
    if (manifest.get("request_count") != len(requests) or manifest.get("task_count") != len(tasks)
            or manifest.get("document_count") != len(documents) or manifest.get("row_count") != len(row_plans)):
        raise ValueError("mixed manifest request/task/document/row counts differ")
    row_keys = {(plan["case_id"], plan["au_row_key"]) for plan in row_plans}
    if len(row_keys) != len(row_plans) or any(identity not in cases for identity, _ in row_keys):
        raise ValueError("mixed row plan identity differs")
    bound_requests = set()
    for task in tasks.values():
        if (task.get("case_id") not in cases or task.get("request_id") not in requests
                or (task["case_id"], task.get("au_row_key")) not in row_keys):
            raise ValueError("task binds outside the frozen mixed cases/rows")
        bound_requests.add(task["request_id"])
    if bound_requests != set(requests):
        raise ValueError("mixed request set differs from task bindings")
    return manifest, requests, documents, tasks, cases, membership, gpu_cases


def verify_gpu_run(prepared_dir: Path, run_dir: Path, manifest: dict, requests: dict):
    """Check the full, fresh-baseline CUDA artifact before any references load."""
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    source_freeze = json.loads((run_dir / "gpu-source-freeze.json").read_text(encoding="utf-8"))
    execution_freeze = json.loads((run_dir / "execution-freeze.json").read_text(encoding="utf-8"))
    if (summary.get("schema_version") != "product-condition-gpu-v1/device-only-fp32"
            or summary.get("gpu_used") is not True
            or summary.get("stage_devices") != {"reranker": "cpu", "jev": "cuda", "nli": "cuda"}
            or summary.get("stage_precision") != {"reranker": "int8", "jev": "fp32", "nli": "fp32"}
            or summary.get("received_source_and_code_verified_after_inference") is not True
            or summary.get("saved_prediction_fanout_and_literal_references_verified") is not True):
        raise ValueError("GPU run lacks verified mixed CPU/CUDA execution metadata")
    runtime = summary.get("gpu_runtime", {})
    if (runtime.get("cuda_matmul_tf32") is not False or runtime.get("cudnn_tf32") is not False
            or runtime.get("float32_matmul_precision") != "highest"):
        raise ValueError("GPU run does not prove TF32-disabled FP32 execution")
    telemetry = summary.get("gpu_stage_telemetry", {})
    for stage in ("jev", "nli"):
        if (telemetry.get(stage, {}).get("model_load_count", 0) < 1
                or telemetry.get(stage, {}).get("forward_call_count", 0) < 1
                or telemetry.get(stage, {}).get("forwarded_pair_count", 0) < 1):
            raise ValueError("GPU run has no actual CUDA model forward for " + stage)
    for stage, module in (("jev", PRODUCT.JEV), ("nli", PRODUCT.NLI)):
        pin = summary.get("verified_" + stage + "_pin") or {}
        expected_model = module.MODEL_ID
        expected_revision = module.MODEL_REVISION
        if (pin.get("model_id") != expected_model or pin.get("revision") != expected_revision
                or pin.get("device") != "cuda" or pin.get("precision") != "fp32"):
            raise ValueError("verified CUDA model pin differs for " + stage)
        expected_files = module.JEV_FILE_SHA256 if stage == "jev" else module.NLI_FILE_SHA256
        if pin.get("file_sha256") != expected_files:
            raise ValueError("verified CUDA model file pins differ for " + stage)
    source_hashes = source_freeze.get("input_sha256", {})
    if set(source_hashes) != {"manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl",
                              "baseline-predictions.jsonl", "blocks.jsonl", "retrieval-requests.jsonl"}:
        raise ValueError("GPU source freeze inventory differs")
    for name, expected in source_hashes.items():
        if sha(prepared_dir / name) != expected:
            raise ValueError("GPU run source hash does not match prepared source: " + name)
    if (execution_freeze.get("input_sha256") != source_hashes
            or execution_freeze.get("refresh_baseline") is not True
            or summary.get("received_source_freeze") != source_freeze):
        raise ValueError("execution, result and source freeze records differ")
    code_hashes = source_freeze.get("code_sha256", {})
    for name, expected in code_hashes.items():
        path = run_dir / "code" / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("frozen GPU code file differs: " + name)
    reranker = run_dir / "code/manifests/reranker.json"
    if not reranker.is_file() or sha(reranker) != source_freeze.get("reranker_manifest_sha256"):
        raise ValueError("frozen reranker manifest differs")
    for relative, expected in summary.get("output_sha256", {}).items():
        path = run_dir / relative
        if not path.is_file() or sha(path) != expected:
            raise ValueError("GPU output hash differs: " + relative)
    if summary.get("baseline_mode") not in {"fresh-gpu-fusion", "fresh-gpu-jev", "fresh-gpu-nli"}:
        raise ValueError("mixed baseline was not regenerated by the pinned GPU scorer")
    refresh = summary.get("baseline_refresh_summary")
    if not isinstance(refresh, dict) or refresh.get("request_count") != len(requests):
        raise ValueError("fresh baseline does not cover every mixed request")
    products = run_dir / "product-model"
    refreshed_manifest_path = products / "source-manifest.json"
    refreshed_manifest = json.loads(refreshed_manifest_path.read_text(encoding="utf-8"))
    if (refreshed_manifest.get("baseline_predictions_are_unevaluated") is not False
            or refreshed_manifest.get("baseline_must_be_regenerated_for_all_requests") is not False
            or not refreshed_manifest.get("baseline_refresh")):
        raise ValueError("product output does not bind the refreshed baseline source")
    refreshed_dir = run_dir / "baseline-refresh/prepared"
    refreshed_contract = json.loads((products / "sharing-contract.json").read_text(encoding="utf-8"))
    refreshed_hashes = refreshed_contract.get("source_sha256", {}).get("prepared", {})
    if not refreshed_hashes:
        raise ValueError("fresh sharing contract lacks prepared-source hashes")
    for name, expected in refreshed_hashes.items():
        path = refreshed_dir / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("fresh baseline input differs from its sharing contract: " + name)
    return summary, source_freeze


def verify_cpu_run(prepared_dir: Path, run_dir: Path, manifest: dict, requests: dict):
    """Validate an explicit CPU fallback without inheriting GPU evidence claims."""
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    source_freeze = json.loads((run_dir / "cpu-source-freeze.json").read_text(encoding="utf-8"))
    execution_freeze = json.loads((run_dir / "execution-freeze.json").read_text(encoding="utf-8"))
    if (summary.get("schema_version") != "product-condition-cpu-fallback-v1/device-only-fp32"
            or summary.get("execution_mode") != "explicit_cpu_fallback_after_gpu_submission_unknown"
            or summary.get("gpu_used") is not False or summary.get("gpu_submitted") is not False
            or summary.get("stage_devices") != {"reranker": "cpu", "jev": "cpu", "nli": "cpu"}
            or summary.get("stage_precision") != {"reranker": "int8", "jev": "fp32", "nli": "fp32"}
            or summary.get("received_source_and_code_verified_after_inference") is not True
            or summary.get("saved_prediction_fanout_and_literal_references_verified") is not True
            or summary.get("labels_read") is not False or summary.get("independent_test") is not False):
        raise ValueError("CPU run lacks explicit, label-free FP32 fallback provenance")
    if (execution_freeze.get("input_sha256") != source_freeze.get("input_sha256")
            or execution_freeze.get("eligible_contract_sha256") != source_freeze.get("eligible_contract_sha256")
            or execution_freeze.get("refresh_baseline") is not True
            or execution_freeze.get("gpu_submitted") is not False):
        raise ValueError("CPU execution and received-source freezes differ")
    for name, expected in source_freeze.get("input_sha256", {}).items():
        if sha(prepared_dir / name) != expected:
            raise ValueError("CPU source input changed from received preparation: " + name)
    for name, expected in source_freeze.get("code_sha256", {}).items():
        relative = "manifests/reranker.json" if name == "reranker.json" else name
        path = run_dir / "code" / relative
        if not path.is_file() or sha(path) != expected:
            raise ValueError("CPU frozen source code/model manifest differs: " + name)
    for stage, module in (("jev", PRODUCT.JEV), ("nli", PRODUCT.NLI)):
        pin = summary.get("verified_" + stage + "_pin") or {}
        expected_files = module.JEV_FILE_SHA256 if stage == "jev" else module.NLI_FILE_SHA256
        if (pin.get("model_id") != module.MODEL_ID or pin.get("revision") != module.MODEL_REVISION
                or pin.get("device") != "cpu" or pin.get("precision") != "fp32"
                or pin.get("file_sha256", pin.get("files_sha256")) != expected_files):
            raise ValueError("verified CPU model pin differs for " + stage)
        runtime = summary.get(stage + "_runtime", {})
        # The pinned CPU scorers historically report positive inference time but
        # omit a runtime.device field. The verified pin and explicit stage map
        # bind device; the frozen scorer code independently checks its loaded
        # parameters/placement. Reject an explicit conflicting device value.
        if (runtime.get("inference_seconds", 0) <= 0
                or runtime.get("device", "cpu") != "cpu"):
            raise ValueError("CPU model lacks actual inference runtime for " + stage)
    reranker_identity = summary.get("model_identity", {}).get("reranker", {})
    reranker_manifest = run_dir / "code/manifests/reranker.json"
    expected_reranker = json.loads(reranker_manifest.read_text(encoding="utf-8"))
    if reranker_identity != expected_reranker:
        raise ValueError("CPU reranker model identity differs from the frozen INT8 manifest")
    if summary.get("request_count") != len(requests):
        raise ValueError("CPU product model request count differs from frozen inputs")
    refresh = summary.get("baseline_refresh_summary")
    if (not isinstance(refresh, dict) or refresh.get("request_count") != len(requests)
            or refresh.get("device") != "cpu" or refresh.get("precision") != "fp32"
            or summary.get("baseline_mode") not in {"fresh-cpu-fusion", "fresh-cpu-jev", "fresh-cpu-nli"}):
        raise ValueError("CPU fresh baseline does not cover every original request")
    products = run_dir / "product-model"
    refreshed_manifest = json.loads((products / "source-manifest.json").read_text(encoding="utf-8"))
    if (refreshed_manifest.get("baseline_predictions_are_unevaluated") is not False
            or refreshed_manifest.get("baseline_must_be_regenerated_for_all_requests") is not False
            or not refreshed_manifest.get("baseline_refresh")):
        raise ValueError("CPU product output does not bind its refreshed baseline")
    refreshed_dir = run_dir / "baseline-refresh/prepared"
    refreshed_contract = json.loads((products / "sharing-contract.json").read_text(encoding="utf-8"))
    refreshed_hashes = refreshed_contract.get("source_sha256", {}).get("prepared", {})
    if not refreshed_hashes:
        raise ValueError("CPU fresh sharing contract lacks prepared-source hashes")
    for name, expected in refreshed_hashes.items():
        if not (refreshed_dir / name).is_file() or sha(refreshed_dir / name) != expected:
            raise ValueError("CPU fresh baseline input differs from its sharing contract: " + name)
    for relative, expected in summary.get("output_sha256", {}).items():
        path = run_dir / relative
        if not path.is_file() or sha(path) != expected:
            raise ValueError("CPU output hash differs: " + relative)
    return summary, source_freeze


def check_prediction_coverage(prepared_dir: Path, run_dir: Path, requests: dict, documents: dict,
                              tasks: dict, summary: dict):
    products = run_dir / "product-model"
    baseline = GATE.indexed(products / "baseline-predictions.jsonl", "request_id")
    if set(baseline) != set(requests):
        raise ValueError("fresh baseline predictions do not cover all mixed requests")
    contract = json.loads((products / "sharing-contract.json").read_text(encoding="utf-8"))
    PRODUCT.SHARING._validate_contract(contract, requests)
    prepared_manifest = json.loads((prepared_dir / "manifest.json").read_text(encoding="utf-8"))
    if contract.get("sold_out_rows_excluded_upstream") is not False or contract.get("row_policy_audit") != prepared_manifest.get("row_policy_audit"):
        raise ValueError("shared contract differs from the mixed captured-row audit")
    eligible_list = contract.get("eligible_forward_request_ids")
    if not isinstance(eligible_list, list) or len(eligible_list) != len(set(eligible_list)):
        raise ValueError("shared request allowlist is invalid")
    eligible = set(eligible_list)
    if not eligible <= set(requests) or any(requests[key].get("direction") != "rakuten_to_au" for key in eligible):
        raise ValueError("shared request allowlist escapes original forward requests")
    prediction_maps = {"baseline": baseline}
    for variant in PREDICTION_VARIANTS:
        path = products / (variant + "-predictions.jsonl")
        prediction_maps[variant] = GATE.indexed(path, "request_id")
        if set(prediction_maps[variant]) != set(requests):
            raise ValueError("prediction set does not cover every original request: " + variant)
        SHARED.validate_shared_predictions(requests, documents, tasks, prediction_maps[variant],
                                           baseline, eligible, variant)
    if summary.get("request_count") != len(requests):
        raise ValueError("GPU run request count differs from the frozen mixed inputs")
    # Baseline generation covers every forward and reverse task; shared variants
    # are allowed to replace only explicitly eligible forward requests.
    for variant in PREDICTION_VARIANTS:
        for request_id in set(requests) - eligible:
            if prediction_maps[variant][request_id] != baseline[request_id]:
                raise ValueError("nonshared/reverse obligation did not preserve the fresh baseline: " + request_id)
    return baseline, prediction_maps, eligible


def eligibility_rows(membership: dict, gpu_cases: dict):
    result, source_flags = [], Counter()
    for identity, member in membership.items():
        case = gpu_cases[identity]
        flags = list(case.get("source_flags", []))
        source_flags.update(flags)
        stratum = member["membership_strata"][0]
        primary = bool(case.get("primary_score_eligible")) and stratum != "product_pair_diagnostic"
        reasons = []
        if stratum == "product_pair_diagnostic":
            reasons.append("diagnostic_case_separate_from_condition_score")
        if case.get("primary_score_eligible") is not True:
            reasons.append("primary_score_eligible_false")
        result.append({"case_id": identity, "stratum": stratum, "primary_score_eligible": primary,
                       "source_flags": flags, "optional_source_capture_incomplete": case.get("optional_source_capture_incomplete", False),
                       "scoring_exclusion_reasons": reasons})
    return result, dict(source_flags)


UNCERTAIN_LABELS = {"review", "uncertain", "unknown", "needs_review", "needs_confirmation"}


def read_reference_file(path: Path, allowed_ids: set[str], source_kind: str,
                        *, require_all: bool = False, require_original_route: bool = False):
    """Read a selected reference route after gates freeze; retain uncertainty separately."""
    checksum = sha(path)
    references = {}
    for line_number, reference in enumerate(rows(path), 1):
        identity = reference.get("case_id")
        if identity not in allowed_ids:
            raise ValueError(f"{source_kind} reference contains an out-of-scope case: {identity}")
        if identity in references:
            raise ValueError(f"duplicate {source_kind} reference: {identity}")
        source = reference.get("scoring_reference_source")
        if source is None:
            if require_original_route:
                raise ValueError(f"{source_kind} reference lacks its original source route: " + identity)
            source = {"source_path": str(path), "source_sha256": checksum, "jsonl_row": line_number,
                      "reference_kind": source_kind}
        else:
            if not isinstance(source, dict):
                raise ValueError("reference lacks a valid original label route: " + identity)
            if isinstance(source.get("source_path"), str):
                route_digest = source.get("source_sha256")
            elif isinstance(source.get("source_archive"), str) and isinstance(source.get("member"), str):
                route_digest = source.get("source_archive_sha256")
                member_digest = source.get("member_sha256")
                if not isinstance(member_digest, str) or len(member_digest) != 64:
                    raise ValueError("archived reference lacks a pinned member: " + identity)
                archive = Path(source["source_archive"])
                if sha(archive) != route_digest:
                    raise ValueError("archived reference source SHA differs: " + identity)
                from zipfile import ZipFile
                with ZipFile(archive) as zipped:
                    if hashlib.sha256(zipped.read(source["member"])).hexdigest() != member_digest:
                        raise ValueError("archived reference member SHA differs: " + identity)
            else:
                raise ValueError("reference lacks a valid original label route: " + identity)
            if not isinstance(route_digest, str) or len(route_digest) != 64:
                raise ValueError("reference route has an invalid SHA256: " + identity)
            try:
                int(route_digest, 16)
            except ValueError as error:
                raise ValueError("reference route has an invalid SHA256: " + identity) from error
        label = reference.get("label", reference.get("decision"))
        if label not in {"matched", "unmatched", *UNCERTAIN_LABELS}:
            raise ValueError(f"{source_kind} reference has unsupported label {label!r}: " + identity)
        if "label" in reference and "decision" in reference and reference["label"] != reference["decision"]:
            raise ValueError("reference label aliases conflict: " + identity)
        references[identity] = {**reference, "label": label,
                                "reference_uncertain": label in UNCERTAIN_LABELS,
                                "scoring_reference_source": source}
    if require_all and set(references) != allowed_ids:
        missing = sorted(allowed_ids - set(references))
        raise ValueError(f"{source_kind} reference coverage is incomplete; missing {len(missing)} selected cases")
    return references, checksum


def metrics_for(gates: dict, references: dict):
    counts = Counter()
    details = []
    for identity, reference in references.items():
        if reference.get("label", reference.get("decision")) in UNCERTAIN_LABELS:
            continue
        prediction = gates[identity]
        accepted, matched = prediction["decision"] == "accept", reference.get("label", reference.get("decision")) == "matched"
        cell = "tp" if accepted and matched else "fp" if accepted else "fn" if matched else "tn"
        counts[cell] += 1
        row_status = "not_accepted"
        expected = reference.get("matched_au_row_keys", reference.get("matching_au_row_keys", reference.get("au_row_keys")))
        if accepted and not matched:
            row_status = "false_positive_sku"
        elif accepted and matched:
            if expected is None:
                row_status = "accepted_matched_row_unscored"
            elif not isinstance(expected, list) or any(not isinstance(key, str) for key in expected):
                raise ValueError("matched reference row keys are invalid: " + identity)
            else:
                row_status = "correct_accepted_row" if prediction["top_row_key"] in expected else "wrong_accepted_row"
        if accepted:
            counts[row_status] += 1
        details.append({"case_id": identity, "label": reference.get("label", reference.get("decision")),
                        "decision": prediction["decision"], "binary_cell": cell,
                        "top_row_key": prediction.get("top_row_key"), "row_status": row_status,
                        "reference_route": reference["scoring_reference_source"]})
    total = sum(counts[k] for k in ("tp", "fp", "fn", "tn"))
    return {"tp": counts["tp"], "fp": counts["fp"], "fn": counts["fn"], "tn": counts["tn"],
            "scored_case_count": total,
            "accuracy": (counts["tp"] + counts["tn"]) / total if total else None,
            "precision": counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else None,
            "recall": counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else None,
            "row_outcomes": {key: counts[key] for key in ("correct_accepted_row", "wrong_accepted_row",
                                                            "accepted_matched_row_unscored", "false_positive_sku")},
            "case_results": details}


def run(prepared_dir: Path, run_dir: Path, prior_error_references: Path | None, output_dir: Path,
        control_references: Path | None = None, new_dev_references: Path | None = None,
        expected_counts=EXPECTED_COUNTS, execution_device="cuda", freeze_only=False,
        allow_unresolved_controls=False):
    prepared_dir, run_dir, output_dir = Path(prepared_dir), Path(run_dir), Path(output_dir)
    prior_error_references = Path(prior_error_references) if prior_error_references is not None else None
    control_references = Path(control_references) if control_references is not None else None
    new_dev_references = Path(new_dev_references) if new_dev_references is not None else None
    if output_dir.exists():
        raise FileExistsError(output_dir)
    if not freeze_only and prior_error_references is None:
        raise ValueError("prior-error references are required unless --freeze-only is used")
    manifest, requests, documents, tasks, cases, membership, gpu_cases = load_mixed_prepared(prepared_dir, expected_counts)
    if execution_device not in {"cuda", "cpu"}:
        raise ValueError("execution_device must be cuda or cpu")
    gpu_summary, source_freeze = (verify_gpu_run(prepared_dir, run_dir, manifest, requests)
                                  if execution_device == "cuda" else
                                  verify_cpu_run(prepared_dir, run_dir, manifest, requests))
    baseline, prediction_maps, eligible_shared = check_prediction_coverage(
        prepared_dir, run_dir, requests, documents, tasks, gpu_summary)
    eligible, source_flag_counts = eligibility_rows(membership, gpu_cases)
    eligibility = {row["case_id"]: row for row in eligible}
    eligible_scores = {identity for identity, row in eligibility.items() if row["primary_score_eligible"]}
    strata = {identity: membership[identity]["membership_strata"][0] for identity in membership}

    # Freeze every gate and provenance artifact before touching any reference file.
    output_dir.mkdir(parents=True, exist_ok=False)
    input_files = [prepared_dir / "manifest.json", *(prepared_dir / name for name in PREP_FILES),
                   run_dir / "summary.json", run_dir / ("gpu-source-freeze.json" if execution_device == "cuda" else "cpu-source-freeze.json"),
                   run_dir / "execution-freeze.json",
                   run_dir / "product-model/freeze.json", run_dir / "product-model/summary.json",
                   run_dir / "product-model/sharing-contract.json", Path(__file__), Path(SHARED.__file__),
                   Path(PRODUCT.__file__),
                   GATE.HERE / "integrate_claude_description_v1.py"]
    if execution_device == "cpu":
        input_files.append(Path(__file__).with_name("trial_mixed_cpu_sku_v1.py"))
    frozen = {str(path.resolve()): sha(path) for path in input_files}
    prediction_paths = {"baseline": run_dir / "product-model/baseline-predictions.jsonl"}
    prediction_paths.update({variant: run_dir / "product-model" / (variant + "-predictions.jsonl")
                             for variant in PREDICTION_VARIANTS})
    frozen.update({str(path.resolve()): sha(path) for path in prediction_paths.values()})
    gates, gate_manifests, gate_files = {}, {}, {}
    for variant in VARIANTS:
        gate_dir = output_dir / variant
        gate_files[variant] = gate_dir
        gate_manifests[variant] = GATE.finish(prepared_dir, prediction_paths[variant], gate_dir)
        gates[variant] = GATE.indexed(gate_dir / "decisions.jsonl", "case_id")
        if set(gates[variant]) != set(cases):
            raise ValueError("gate case coverage differs from the mixed membership: " + variant)
    gate_hashes = {str(path.resolve()): sha(path)
                   for gate_dir in gate_files.values() for path in gate_dir.rglob("*") if path.is_file()}
    write_rows(output_dir / "case-eligibility.jsonl", eligible)
    write_json(output_dir / "all-gates-frozen.json", {
        "labels_read": False, "all_predictions_and_gates_frozen": True,
        "prediction_request_count": len(requests), "case_count": len(cases),
        "gate_manifest_sha256": {variant: sha(gate_files[variant] / "manifest.json") for variant in VARIANTS}})
    write_json(output_dir / "freeze.json", {
        "schema_version": "mixed-gpu-sku-evaluation-v1", "labels_read": False,
        "labels_in_model_inputs": False, "independent_test": False, "synthetic_data": False,
        "production_adoption_allowed": False, "full_data_accuracy_measured": False,
        "source_stratum_case_counts": dict(Counter(strata.values())),
        "primary_score_eligible_case_count": len(eligible_scores),
        "source_flag_counts": source_flag_counts,
        "shared_request_count": len(eligible_shared), "request_count": len(requests), "case_count": len(cases),
        "input_sha256": frozen,
        "source_freeze": source_freeze, "execution_device": execution_device,
        "variants": list(VARIANTS), "gate_manifest_sha256": {variant: sha(gate_files[variant] / "manifest.json") for variant in VARIANTS},
        "case_eligibility_sha256": sha(output_dir / "case-eligibility.jsonl"),
        "labels_are_loaded_only_after_all_gates_frozen": True,
        "independent_dev_references_loaded": False})

    if freeze_only:
        return {"schema_version": "mixed-gpu-sku-freeze-only-v1", "freeze_only": True,
                "labels_read": False, "all_gates_frozen": True, "execution_device": execution_device,
                "request_count": len(requests), "case_count": len(cases),
                "source_stratum_case_counts": dict(Counter(strata.values())),
                "gate_manifest_sha256": {variant: sha(gate_files[variant] / "manifest.json") for variant in VARIANTS},
                "freeze_sha256": sha(output_dir / "freeze.json"),
                "case_eligibility_sha256": sha(output_dir / "case-eligibility.jsonl")}

    prior_ids = {identity for identity in membership if strata[identity] == "prior_errors"}
    dev_ids = {identity for identity in membership if strata[identity] == "independent_dev"}
    control_ids = {identity for identity in membership if strata[identity] == "old_structural_control"}
    prior_refs, prior_sha = read_reference_file(prior_error_references, prior_ids, "prior_errors", require_all=True)
    if dev_ids and new_dev_references is None:
        raise ValueError("selected independent-dev cases require an explicit routed reference file")
    dev_refs, dev_sha = ({}, None)
    if new_dev_references is not None:
        dev_refs, dev_sha = read_reference_file(new_dev_references, dev_ids, "independent_dev",
                                               require_all=True, require_original_route=True)
    if control_ids and control_references is None and not allow_unresolved_controls:
        raise ValueError("selected old structural controls require an explicit routed reference file")
    control_refs, control_sha = ({}, None)
    if control_references is not None:
        control_refs, control_sha = read_reference_file(control_references, control_ids,
                                                        "old_structural_control",
                                                        require_all=not allow_unresolved_controls)
    references = {**prior_refs, **dev_refs, **control_refs}
    if len(references) != len(prior_refs) + len(dev_refs) + len(control_refs):
        raise ValueError("reference source identities overlap")
    reference_routes = [{"case_id": identity, "stratum": strata[identity],
                         "source": reference["scoring_reference_source"]}
                        for identity, reference in references.items()]
    write_rows(output_dir / "references-for-scoring.jsonl", references.values())
    write_rows(output_dir / "reference-routes.jsonl", reference_routes)

    scores = {}
    result_rows = []
    for variant in VARIANTS:
        scores[variant] = {}
        for stratum in STRATA:
            selected = {identity: reference for identity, reference in references.items()
                        if strata[identity] == stratum and identity in eligible_scores
                        and not reference.get("reference_uncertain", False)}
            metrics = metrics_for(gates[variant], selected)
            scores[variant][stratum] = {key: value for key, value in metrics.items() if key != "case_results"}
            result_rows.extend({"variant": variant, "stratum": stratum, **row} for row in metrics["case_results"])
        all_refs = {identity: reference for identity, reference in references.items()
                    if identity in eligible_scores and not reference.get("reference_uncertain", False)}
        metrics = metrics_for(gates[variant], all_refs)
        scores[variant]["scored_primary_reference_union"] = {key: value for key, value in metrics.items() if key != "case_results"}
    write_rows(output_dir / "case-results.jsonl", result_rows)
    if any(sha(Path(path)) != checksum for path, checksum in frozen.items()):
        raise ValueError("frozen inputs, predictions or gates changed during evaluation")
    if any(sha(Path(path)) != checksum for path, checksum in gate_hashes.items()):
        raise ValueError("frozen gates changed during label scoring")
    label_sha = {str(prior_error_references.resolve()): prior_sha}
    if new_dev_references is not None:
        label_sha[str(new_dev_references.resolve())] = dev_sha
    if control_references is not None:
        label_sha[str(control_references.resolve())] = control_sha
    if any(sha(Path(path)) != checksum for path, checksum in label_sha.items()):
        raise ValueError("reference labels changed during scoring")
    summary = {"schema_version": "mixed-gpu-sku-evaluation-result-v1",
        "execution_device": execution_device,
        "evaluation_scope": "prior-error stress set, selected independent development cases, and old structural controls",
        "reference_kind": "separate existing machine labels; not human gold",
        "labels_read_after_all_gates_frozen": True, "independent_dev_references_loaded": bool(dev_ids),
        "independent_test": False, "synthetic_data": False, "full_data_accuracy_measured": False,
        "production_adoption_allowed": False,
        "source_stratum_case_counts": dict(Counter(strata.values())),
        "primary_score_eligible_case_count": len(eligible_scores),
        "primary_score_exclusion_count": len(eligibility) - len(eligible_scores),
        "source_flag_counts": source_flag_counts,
        "unscored_case_counts": {"independent_dev_uncertain": sum(stratum == "independent_dev" and identity in dev_refs and dev_refs[identity].get("reference_uncertain", False)
                                                                    for identity, stratum in strata.items()),
                                  "old_structural_control_without_reference": len(control_ids - set(control_refs)),
                                  "old_structural_control_uncertain": sum(stratum == "old_structural_control" and identity in control_refs and control_refs[identity].get("reference_uncertain", False)
                                                                          for identity, stratum in strata.items()),
                                  "diagnostic": sum(stratum == "product_pair_diagnostic" for stratum in strata.values())},
        "unscored_case_ids": {"old_structural_control_without_reference": sorted(control_ids - set(control_refs))},
        "request_count": len(requests), "case_count": len(cases), "shared_request_count": len(eligible_shared),
        "reference_case_counts": dict(Counter(strata[identity] for identity in references)),
        "reference_coverage": {"old_structural_control": {"selected": len(control_ids),
                            "routed_reference_count": len(control_refs), "complete": set(control_refs) == control_ids}},
        "reference_uncertain_case_counts": dict(Counter(strata[identity] for identity, reference in references.items()
                                                       if reference.get("reference_uncertain", False))),
        "reference_sha256": label_sha, "variants": scores,
        "original_inputs_and_predictions_verified_unchanged": True,
        "output_sha256": {str(path.relative_to(output_dir)): sha(path)
                          for path in output_dir.rglob("*") if path.is_file() and path.name != "summary.json"}}
    write_json(output_dir / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="GPU output root; gates and baseline are read from product-model/")
    parser.add_argument("--prior-error-references", type=Path,
                        help="existing frozen 44-case reference file, required for scoring")
    parser.add_argument("--control-references", type=Path,
                        help="selected references routed to old structural controls")
    parser.add_argument("--new-dev-references", type=Path,
                        help="explicit selected-ID references for independent development cases; never independent test")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execution-device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--freeze-only", action="store_true",
                        help="validate and freeze every gate before reference routing; read no references")
    parser.add_argument("--allow-unresolved-controls", action="store_true",
                        help="score only explicitly routed control references and list all remaining control IDs")
    args = vars(parser.parse_args(argv))
    result = run(**args)
    print(json.dumps(result if result.get("freeze_only") else
        {"case_count": result["case_count"], "request_count": result["request_count"],
         "reference_case_counts": result["reference_case_counts"], "variants": result["variants"]},
        ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
