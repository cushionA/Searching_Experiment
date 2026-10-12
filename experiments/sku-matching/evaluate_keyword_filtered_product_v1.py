#!/usr/bin/env python3
"""Evaluate the frozen keyword-filtered product model on the mixed CPU cohort.

The candidate is an exploratory development experiment informed by its already
read development references. It reuses the original CPU baseline, gates all
83 cases before opening the original references, and never reads independent
test labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import evaluate_mixed_gpu_sku_v1 as E
import trial_keyword_filtered_product_v1 as CANDIDATE

ROOT = Path(__file__).resolve().parents[2]
PREP = Path(".lab-output/sku-mixed-gpu-prep-20261012-v5")
CPU_RUN = Path(".lab-output/sku-mixed-cpu-run-20261012-v1")
CANDIDATE_RUN = Path(".lab-output/sku-keyword-filter-cpu-20261012-v2")
ORIGINAL_EVAL = Path(".lab-output/sku-mixed-cpu-evaluation-20261012-v4")
ORIGINAL_ROUTES = Path(".lab-output/sku-mixed-cpu-reference-routes-20261012-v2")
OUTPUT = Path(".lab-output/sku-keyword-filter-evaluation-20261012-v2")
VARIANT = "product-top3-jev"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path: Path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def index(path: Path, key: str):
    result = {}
    for row in rows(path):
        identity = row.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError(f"missing or duplicate {key} in {path.name}")
        result[identity] = row
    return result


def verify_output_hashes(directory: Path, hashes: dict, *, skip=()):
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("artifact has no output SHA inventory")
    for relative, expected in hashes.items():
        if relative in skip:
            continue
        path = Path(directory) / relative
        if not path.is_file() or sha(path) != expected:
            raise ValueError("artifact output SHA mismatch: " + relative)


def verify_archive_reference(archive: Path, source: dict, case_id: str):
    """Verify one selected label line and its pinned original ZIP member."""
    archive = Path(archive)
    if not archive.is_file() or sha(archive) != source.get("source_archive_sha256"):
        raise ValueError("selected reference archive SHA differs")
    with ZipFile(archive) as zipped:
        member = zipped.read(source["member"])
        if hashlib.sha256(member).hexdigest() != source.get("member_sha256"):
            raise ValueError("selected reference archive member SHA differs")
        if source.get("source_case_member"):
            case_member = zipped.read(source["source_case_member"])
            if hashlib.sha256(case_member).hexdigest() != source.get("source_case_member_sha256"):
                raise ValueError("selected source case member SHA differs")
    offset = source.get("byte_offset")
    if type(offset) is not int or offset < 0:
        raise ValueError("selected reference line has no valid byte offset")
    line = member[offset:].splitlines()[0]
    record = json.loads(line)
    if (record.get("case_id") != case_id
            or hashlib.sha256(line).hexdigest() != source.get("record_sha256")):
        raise ValueError("selected original reference bytes differ from routed record")
    return record


def verify_path_reference(source: dict, case_id: str):
    path = Path(source.get("source_path", ""))
    if not path.is_file() or sha(path) != source.get("source_sha256"):
        raise ValueError("selected reference source path SHA differs")
    payload = path.read_bytes()
    offset = source.get("byte_offset")
    if type(offset) is not int or offset < 0:
        raise ValueError("selected reference line has no valid byte offset")
    line = payload[offset:].splitlines()[0]
    record = json.loads(line)
    if (record.get("case_id") != case_id
            or hashlib.sha256(line).hexdigest() != source.get("record_sha256")):
        raise ValueError("selected original reference bytes differ from routed record")
    return record


def verify_legacy_source_route(original_route: dict, archive: Path, member_name: str,
                               case_id: str, label: str):
    """Follow the prior-error reference back to its original legacy labels."""
    source_path = Path(original_route.get("source_path", ""))
    expected_sha = original_route.get("source_sha256")
    if source_path.is_file():
        if sha(source_path) != expected_sha:
            raise ValueError("legacy label source path SHA differs")
        payload = source_path.read_bytes()
    else:
        with ZipFile(archive) as zipped:
            payload = zipped.read(member_name)
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise ValueError("legacy label archive/member SHA differs")
    line_number = original_route.get("jsonl_row")
    if type(line_number) is not int or line_number < 1:
        raise ValueError("legacy label route has no valid JSONL line")
    source_rows = payload.splitlines()
    if line_number > len(source_rows):
        raise ValueError("legacy label route line is outside its source")
    record = json.loads(source_rows[line_number - 1])
    if (record.get("case_id") != case_id
            or record.get("label", record.get("decision")) != label):
        raise ValueError("legacy original label line differs from scored reference")


def verify_original_sources(prepared_dir: Path, original_eval_dir: Path,
                            route_dir: Path):
    """Verify v4 scores, routes, and selected source members before reuse."""
    summary = read_json(original_eval_dir / "summary.json")
    if (summary.get("schema_version") != "mixed-gpu-sku-evaluation-result-v1"
            or summary.get("execution_device") != "cpu"
            or summary.get("labels_read_after_all_gates_frozen") is not True
            or summary.get("independent_test") is not False
            or summary.get("source_stratum_case_counts") != E.EXPECTED_COUNTS
            or summary.get("primary_score_eligible_case_count") != 82
            or summary.get("unscored_case_counts", {}).get("independent_dev_uncertain") != 5
            or summary.get("unscored_case_counts", {}).get("old_structural_control_without_reference") != 4
            or summary.get("unscored_case_counts", {}).get("diagnostic") != 1):
        raise ValueError("original evaluation is not the frozen mixed CPU v4 scoring artifact")
    verify_output_hashes(original_eval_dir, summary.get("output_sha256", {}))
    eval_freeze = read_json(original_eval_dir / "freeze.json")
    if sha(original_eval_dir / "freeze.json") != summary["output_sha256"].get("freeze.json"):
        raise ValueError("original evaluation freeze SHA mismatch")
    if eval_freeze.get("labels_read") is not False or eval_freeze.get("execution_device") != "cpu":
        raise ValueError("original v4 gate freeze metadata differs")
    prep_manifest = read_json(prepared_dir / "manifest.json")
    if eval_freeze.get("input_sha256", {}).get(str((prepared_dir / "manifest.json").resolve())) != sha(prepared_dir / "manifest.json"):
        raise ValueError("original v4 evaluation is bound to another prepared input")

    route_manifest = read_json(route_dir / "manifest.json")
    if (route_manifest.get("schema_version") != "mixed-sku-reference-routes-v1"
            or route_manifest.get("prediction_and_gate_freeze_sha256") != sha(original_eval_dir / "freeze.json")
            or route_manifest.get("prepared_manifest_sha256") != sha(prepared_dir / "manifest.json")
            or route_manifest.get("membership_sha256") != sha(prepared_dir / "membership.jsonl")
            or route_manifest.get("diagnostic_labels_read") is not False
            or route_manifest.get("independent_test_labels_read") is not False
            or route_manifest.get("routed_case_counts") != {
                "prior_errors": 44, "independent_dev": 31, "old_structural_control": 3,
                "old_structural_control_unresolved": 4, "product_pair_diagnostic": 0}):
        raise ValueError("original reference-route manifest differs from v2")
    for name, expected in route_manifest.get("output_sha256", {}).items():
        path = route_dir / name
        if not path.is_file() or sha(path) != expected:
            raise ValueError("original routed source SHA mismatch: " + name)
    source_files = route_manifest.get("source_reference_files", {})
    prior_path = Path(source_files.get("prior_errors", ""))
    controls_archive = Path(source_files.get("controls_archive", ""))
    if not prior_path.is_file() or sha(prior_path) != source_files.get("prior_errors_sha256"):
        raise ValueError("pinned prior-error reference source changed")
    if not controls_archive.is_file() or sha(controls_archive) != source_files.get("controls_archive_sha256"):
        raise ValueError("pinned controls archive changed")
    with ZipFile(controls_archive) as zipped:
        payload = zipped.read(source_files["controls_member"])
    route_records = {row["case_id"]: row for row in rows(route_dir / "prior-errors.jsonl")}
    route_records.update({row["case_id"]: row for row in rows(route_dir / "independent-dev.jsonl")})
    route_records.update({row["case_id"]: row for row in rows(route_dir / "old-structural-controls.jsonl")})
    references_path = original_eval_dir / "references-for-scoring.jsonl"
    refs = index(references_path, "case_id")
    if sha(references_path) != summary.get("output_sha256", {}).get("references-for-scoring.jsonl"):
        raise ValueError("original v4 reference file SHA differs from scoring summary")
    if set(refs) != set(route_records):
        raise ValueError("original scored references differ from frozen routed membership")
    if any(ref.get("scoring_reference_source", {}).get("reference_kind") == "product_pair_diagnostic"
           for ref in refs.values()):
        raise ValueError("diagnostic label entered original scoring references")

    # Prior-error records route to a pinned existing reference JSONL. Dev
    # records route to exact selected lines in the pinned v2/v3 ZIP members.
    for identity, ref in refs.items():
        source = ref.get("scoring_reference_source", {})
        kind = source.get("reference_kind")
        routed = route_records[identity]
        if ref.get("label", ref.get("decision")) != routed.get("label", routed.get("decision")):
            raise ValueError("original scored label differs from selected route: " + identity)
        if kind == "prior_errors":
            if source.get("source_sha256") != source_files.get("prior_errors_sha256"):
                raise ValueError("prior-error label route differs from its pinned source")
            raw_record = verify_path_reference(source, identity)
            if raw_record.get("label", raw_record.get("decision")) != ref.get("label", ref.get("decision")):
                raise ValueError("prior-error source label differs from scored reference")
            original_route = source.get("original_route")
            if not isinstance(original_route, dict) or raw_record.get("scoring_reference_source") != original_route:
                raise ValueError("prior-error original route differs from the routed source record")
            verify_legacy_source_route(original_route, controls_archive,
                                      source_files["controls_member"], identity,
                                      ref.get("label", ref.get("decision")))
        elif kind == "old_structural_control":
            if (source.get("source_archive_sha256") != source_files.get("controls_archive_sha256")
                    or source.get("member") != source_files.get("controls_member")
                    or hashlib.sha256(payload).hexdigest() != source.get("member_sha256")):
                raise ValueError("old control reference archive/member route differs")
            raw_record = verify_archive_reference(controls_archive, source, identity)
            if raw_record.get("label", raw_record.get("decision")) != ref.get("label", ref.get("decision")):
                raise ValueError("control source label differs from scored reference")
        elif kind == "independent_dev":
            if source.get("source_case_split") == "test" or source.get("eligible_group_split") != "dev":
                raise ValueError("selected dev reference source is not in the dev group")
            archive = Path(source.get("source_archive", ""))
            raw_record = verify_archive_reference(archive, source, identity)
            if raw_record.get("expected") not in {"accept", "drop", None, "review", "uncertain", "unknown",
                                                   "needs_review", "needs_confirmation"}:
                raise ValueError("selected dev source record has an unsupported expected label")
            expected_label = ("matched" if raw_record.get("expected") == "accept" else
                              "unmatched" if raw_record.get("expected") == "drop" else
                              "review" if raw_record.get("expected") is None else raw_record.get("expected"))
            if (ref.get("source_label_record") != raw_record
                    or ref.get("label", ref.get("decision")) != expected_label
                    or ref.get("au_row_keys") != raw_record.get("au_row_keys")):
                raise ValueError("selected dev source label/row keys differ from routed scored record")
        else:
            raise ValueError("original reference has an unsupported source stratum: " + identity)

    old_case_results = [r for r in rows(original_eval_dir / "case-results.jsonl")
                        if r.get("variant") == VARIANT]
    old_case_index = index_rows_from(old_case_results, "case_id")
    if len(old_case_index) != 73:
        raise ValueError("original top3 case results do not cover the 73 scored cases")
    return summary, eval_freeze, route_manifest, refs, old_case_index


def index_rows_from(values, key):
    out = {}
    for row in values:
        identity = row.get(key)
        if not isinstance(identity, str) or identity in out:
            raise ValueError("missing or duplicate " + key + " in frozen case results")
        out[identity] = row
    return out


def validate_candidate_predictions(candidate_dir: Path, requests: dict, documents: dict,
                                   tasks: dict, baseline: dict, eligible: set[str]):
    predictions = index(candidate_dir / "product-top3-jev-predictions.jsonl", "request_id")
    if set(predictions) != set(requests):
        raise ValueError("keyword candidate must cover every original request")
    if not eligible <= set(requests) or any(requests[r].get("direction") != "rakuten_to_au" for r in eligible):
        raise ValueError("keyword candidate share set escapes forward request membership")
    E.SHARED.validate_shared_predictions(requests, documents, tasks, predictions,
                                         baseline, eligible, VARIANT)
    passthrough = set(requests) - eligible
    if any(predictions[r] != baseline[r] for r in passthrough):
        raise ValueError("keyword candidate changed a nonshared/reverse baseline request")
    return predictions, passthrough


def verify_candidate(candidate_dir: Path, source_run_dir: Path, prepared_dir: Path,
                     requests: dict, documents: dict, tasks: dict,
                     baseline: dict, eligible: set[str]):
    candidate_dir, product_dir = Path(candidate_dir), Path(candidate_dir)
    freeze = read_json(candidate_dir / "freeze.json")
    summary = read_json(candidate_dir / "summary.json")
    if (freeze.get("schema_version") != "keyword-filtered-product-cpu-v1"
            or freeze.get("experiment_status") != "exploratory_dev_informed"
            or freeze.get("labels_read") is not False or freeze.get("labels_in_model_inputs") is not False
            or freeze.get("independent_test") is not False or freeze.get("production_adoption_allowed") is not False
            or summary.get("experiment_status") != "exploratory_dev_informed"
            or summary.get("freeze_sha256") != sha(candidate_dir / "freeze.json")):
        raise ValueError("keyword candidate lacks a label-free exploratory prediction freeze")
    verify_output_hashes(candidate_dir, freeze.get("output_sha256", {}))
    verify_output_hashes(candidate_dir, summary.get("output_sha256", {}), skip=("summary.json",))
    if summary.get("output_sha256", {}).get("freeze.json") != sha(candidate_dir / "freeze.json"):
        raise ValueError("candidate summary does not bind its prediction freeze")
    if (freeze.get("request_count") != len(requests) or summary.get("request_count") != len(requests)
            or freeze.get("group_count") != 29 or summary.get("group_count") != 29
            or freeze.get("source_run_dir") != str(Path(source_run_dir).resolve())
            or freeze.get("source_product_dir") != str((Path(source_run_dir) / "product-model").resolve())):
        raise ValueError("keyword candidate population/source binding differs")
    policy = freeze.get("policy", {})
    if (policy.get("baseline_refresh") is not False
            or policy.get("nonshared_baseline_preserved") is not True
            or policy.get("reranker_rerun") is not False
            or policy.get("top_k_after_filter") != 3):
        raise ValueError("keyword candidate policy differs from the frozen filter-only experiment")
    original_product = Path(source_run_dir) / "product-model"
    source_summary = read_json(original_product / "summary.json")
    source_freeze = read_json(original_product / "freeze.json")
    received_freeze = read_json(Path(source_run_dir) / "cpu-source-freeze.json")
    if (freeze.get("source_summary_sha256") != sha(original_product / "summary.json")
            or freeze.get("source_freeze_sha256") != sha(original_product / "freeze.json")
            or freeze.get("source_output_sha256") != source_summary.get("output_sha256")
            or freeze.get("source_code_sha256") != source_summary.get("code_sha256")
            or freeze.get("source_sharing_plan_sha256") != source_summary.get("sharing_plan_sha256")
            or freeze.get("source_fresh_baseline_sha256") != sha(original_product / "baseline-predictions.jsonl")):
        raise ValueError("keyword candidate is not bound to the original frozen CPU product run")
    verify_output_hashes(original_product, source_summary.get("output_sha256", {}))
    verify_output_hashes(candidate_dir / "source", freeze.get("source_artifact_sha256", {}))
    if freeze.get("source_artifact_sha256") != {name: sha(original_product / name)
                                                for name in CANDIDATE.SOURCE_ARTIFACTS}:
        raise ValueError("keyword candidate source artifact copy differs from original run")
    copied_codes = freeze.get("copied_source_code_sha256", {})
    if copied_codes != source_summary.get("code_sha256"):
        raise ValueError("keyword candidate copied source code inventory differs")
    for name, expected in copied_codes.items():
        path = candidate_dir / "source/code" / ("reranker.json" if name == "reranker.json" else name)
        if not path.is_file() or sha(path) != expected:
            raise ValueError("keyword candidate copied source code SHA differs: " + name)
        current = (CANDIDATE.V1.HERE / "manifests/reranker.json" if name == "reranker.json"
                   else Path(CANDIDATE.PRODUCT.__file__).with_name(name))
        if not current.is_file() or sha(current) != expected:
            raise ValueError("current original model code differs from frozen source code: " + name)
    own_name = Path(CANDIDATE.__file__).name
    own_hash = sha(Path(CANDIDATE.__file__))
    if (freeze.get("new_experiment_code_sha256", {}).get(own_name) != own_hash
            or sha(candidate_dir / "code" / own_name) != own_hash):
        raise ValueError("current keyword evaluator code differs from its candidate freeze")
    if sha(prepared_dir / "manifest.json") != received_freeze.get("input_sha256", {}).get("manifest.json"):
        raise ValueError("candidate CPU run is bound to another original preparation")
    refreshed_dir = Path(source_run_dir) / "baseline-refresh/prepared"
    refreshed_hashes = source_freeze.get("input_sha256", {})
    for name, expected in refreshed_hashes.items():
        if not (refreshed_dir / name).is_file() or sha(refreshed_dir / name) != expected:
            raise ValueError("CPU refreshed product input differs from model freeze: " + name)
    for name in ("requests.jsonl", "documents.jsonl", "tasks.jsonl", "blocks.jsonl", "retrieval-requests.jsonl"):
        if received_freeze.get("input_sha256", {}).get(name) != refreshed_hashes.get(name):
            raise ValueError("CPU product run changed a non-baseline prepared source: " + name)
    if freeze.get("source_manifest_errors_only") is not False:
        raise ValueError("candidate source was an error-only subset")
    jev_pin = freeze.get("model_pins", {}).get("jev")
    if (not CANDIDATE.JEV._same_pins(jev_pin)
            or not CANDIDATE.JEV._same_pins(summary.get("model_pins", {}).get("jev"))):
        raise ValueError("candidate JEV model revision/file pins differ from the current pinned scorer")
    recorded_model = read_json(candidate_dir / "models/jev-pin.json")
    if (recorded_model.get("pin") != jev_pin or recorded_model.get("device") != "cpu"
            or recorded_model.get("precision") != "fp32"):
        raise ValueError("candidate model artifact pin/device/precision differs")
    new_scores = list(rows(candidate_dir / "new-jev-inference-scores.jsonl"))
    new_score_keys = {row.get("cache_key") for row in new_scores}
    if (None in new_score_keys or len(new_score_keys) != freeze.get("actual_new_jev_inference_count")
            or any(row.get("cache_reused") is not False
                   or not CANDIDATE.JEV._same_pins(row.get("pins")) for row in new_scores)):
        raise ValueError("new candidate inference rows differ from pinned JEV execution")
    # There is one emitted row per choice, so fanout can repeat cache keys.
    # Inference counts are unique-key counts; repeated keys must carry the
    # identical scored payload while retaining their per-choice IDs.
    score_payload_by_key = {}
    for row in new_scores:
        key = row["cache_key"]
        payload = {name: value for name, value in row.items() if name != "id"}
        if key in score_payload_by_key and score_payload_by_key[key] != payload:
            raise ValueError("duplicate candidate inference cache key has conflicting score payloads")
        score_payload_by_key[key] = payload
    execution_freeze_path = candidate_dir / "execution-freeze.json"
    if (not execution_freeze_path.is_file()
            or sha(execution_freeze_path) != freeze.get("execution_freeze_sha256")):
        raise ValueError("candidate execution freeze is missing or differs from its pinned SHA")
    execution_freeze = read_json(execution_freeze_path)
    runtime = freeze.get("jev_runtime", {})
    inferred_count = freeze.get("actual_new_jev_inference_count")
    if (runtime.get("inferred_unique_request_count") != inferred_count
            or runtime.get("cached_unique_request_count") != freeze.get("old_exact_jev_cache_hit_count")
            or inferred_count + runtime.get("cached_unique_request_count", -1)
            != freeze.get("unique_filtered_jev_key_count")
            or summary.get("jev_runtime") != runtime
            or summary.get("execution_freeze_sha256") != sha(execution_freeze_path)):
        raise ValueError("candidate inference runtime counts do not reconcile across freeze and summary")
    if execution_freeze.get("labels_read") is not False or execution_freeze.get("labels_in_model_inputs") is not False:
        raise ValueError("candidate execution freeze does not establish label-blind inference")
    if summary.get("after_inference_sha_verified") is False:
        raise ValueError("candidate reports an execution input SHA change after inference")
    if (freeze.get("old_exact_jev_cache_hit_count", 0)
            + freeze.get("actual_new_jev_inference_count", 0)
            != freeze.get("unique_filtered_jev_key_count")):
        raise ValueError("candidate exact-key cache/inference counts do not reconcile")
    predictions, passthrough = validate_candidate_predictions(
        candidate_dir, requests, documents, tasks, baseline, eligible)
    if len(passthrough) != 54:
        raise ValueError("candidate passthrough request count differs from original plan")
    return freeze, summary, predictions, passthrough


def score_changes(candidate_gates: dict, old_results: dict, references: dict):
    candidate_metrics = E.metrics_for(candidate_gates, references)
    new_by_id = index_rows_from(candidate_metrics["case_results"], "case_id")
    transitions = Counter()
    recovered = []
    regressed = []
    changed = []
    for identity, new in new_by_id.items():
        old = old_results[identity]
        old_cell, new_cell = old["binary_cell"], new["binary_cell"]
        transitions[f"{old_cell}->{new_cell}"] += 1
        if old_cell != new_cell:
            changed.append({"case_id": identity, "from": old_cell, "to": new_cell,
                            "old_decision": old["decision"], "new_decision": new["decision"],
                            "label": new["label"], "old_row_status": old.get("row_status"),
                            "new_row_status": new.get("row_status")})
        if old_cell == "fn" and new_cell == "tp":
            recovered.append(identity)
        if old_cell == "tp" and new_cell == "fn":
            regressed.append(identity)
    return candidate_metrics, {"case_change_count": len(changed), "binary_cell_transitions": dict(transitions),
                               "fn_to_tp_recovered_case_ids": recovered,
                               "tp_to_fn_regressed_case_ids": regressed, "case_changes": changed}


def run(prepared_dir: Path, source_run_dir: Path, candidate_dir: Path,
        original_eval_dir: Path, original_routes_dir: Path, output_dir: Path,
        *, expected_scored_counts=None):
    prepared_dir, source_run_dir, candidate_dir, original_eval_dir, original_routes_dir, output_dir = (
        Path(path).resolve() for path in
        (prepared_dir, source_run_dir, candidate_dir, original_eval_dir, original_routes_dir, output_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest, requests, documents, tasks, cases, membership, hosts = E.load_mixed_prepared(prepared_dir)
    cpu_summary, source_freeze = E.verify_cpu_run(prepared_dir, source_run_dir, manifest, requests)
    baseline, original_predictions, eligible = E.check_prediction_coverage(
        prepared_dir, source_run_dir, requests, documents, tasks, cpu_summary)
    candidate_freeze, candidate_summary, predictions, passthrough = verify_candidate(
        candidate_dir, source_run_dir, prepared_dir, requests, documents, tasks, baseline, eligible)
    strata = {identity: member["membership_strata"][0] for identity, member in membership.items()}
    # Candidate predictions and source runs have been validated; original
    # reference files and archives are deliberately not opened until after the
    # candidate gates and freeze below are durable.
    output_dir.mkdir(parents=True, exist_ok=False)
    candidate_prediction_path = candidate_dir / "product-top3-jev-predictions.jsonl"
    gate_dir = output_dir / VARIANT
    gate_manifest = E.GATE.finish(prepared_dir, candidate_prediction_path, gate_dir)
    gates = E.GATE.indexed(gate_dir / "decisions.jsonl", "case_id")
    if set(gates) != set(cases):
        raise ValueError("candidate gates do not cover all 83 frozen cases")
    gate_hashes = {str(path.resolve()): sha(path) for path in gate_dir.rglob("*") if path.is_file()}
    input_hashes = {str(path.resolve()): sha(path) for path in (
        prepared_dir / "manifest.json", prepared_dir / "requests.jsonl", prepared_dir / "documents.jsonl",
        prepared_dir / "tasks.jsonl", prepared_dir / "case_plans.jsonl", prepared_dir / "row_plans.jsonl",
        prepared_dir / "membership.jsonl", prepared_dir / "gpu-cases.jsonl", prepared_dir / "failures.jsonl",
        source_run_dir / "summary.json", source_run_dir / "cpu-source-freeze.json",
        source_run_dir / "execution-freeze.json", source_run_dir / "product-model/freeze.json",
        source_run_dir / "product-model/summary.json", candidate_dir / "freeze.json",
        candidate_dir / "summary.json", candidate_prediction_path, Path(__file__),
        Path(E.__file__), Path(CANDIDATE.__file__))}
    write_json(output_dir / "all-gates-frozen.json", {
        "labels_read": False, "all_predictions_and_gates_frozen": True,
        "case_count": len(gates), "prediction_request_count": len(requests),
        "gate_manifest_sha256": {VARIANT: sha(gate_dir / "manifest.json")}})
    write_json(output_dir / "freeze.json", {
        "schema_version": "keyword-filtered-product-evaluation-v1",
        "experiment_status": "exploratory_dev_informed", "labels_read": False,
        "independent_test": False, "production_adoption_allowed": False,
        "candidate_prediction_sha256": sha(candidate_prediction_path),
        "candidate_freeze_sha256": sha(candidate_dir / "freeze.json"),
        "candidate_summary_sha256": sha(candidate_dir / "summary.json"),
        "prepared_case_count": len(cases), "request_count": len(requests),
        "passthrough_request_count": len(passthrough), "gate_manifest_sha256": {VARIANT: sha(gate_dir / "manifest.json")},
        "gate_output_sha256": {str(Path(path).relative_to(gate_dir)): digest for path, digest in gate_hashes.items()},
        "input_sha256": input_hashes, "labels_are_loaded_only_after_candidate_gates_frozen": True})

    # Revalidate frozen original references and source members only after the
    # candidate gate freeze has become durable.
    original_summary, original_freeze, route_manifest, references, old_results = verify_original_sources(
        prepared_dir, original_eval_dir, original_routes_dir)
    if set(references) - set(membership):
        raise ValueError("original references contain out-of-cohort cases")
    if any(strata[identity] == "product_pair_diagnostic" for identity in references):
        raise ValueError("diagnostic case entered the scoring references")
    label_paths = {original_eval_dir / "summary.json", original_eval_dir / "freeze.json",
                   original_eval_dir / "references-for-scoring.jsonl",
                   original_routes_dir / "manifest.json"}
    route_manifest = read_json(original_routes_dir / "manifest.json")
    label_paths.update(original_routes_dir / name for name in route_manifest.get("output_sha256", {}))
    for ref in references.values():
        source = ref.get("scoring_reference_source", {})
        if source.get("source_archive"):
            label_paths.add(Path(source["source_archive"]))
        if source.get("source_path"):
            label_paths.add(Path(source["source_path"]))
        original_route = source.get("original_route", {})
        if original_route.get("source_path"):
            label_paths.add(Path(original_route["source_path"]))
    label_hashes = {str(path.resolve()): sha(path) for path in label_paths if path.is_file()}
    eligible_rows, _ = E.eligibility_rows(membership, hosts)
    score_ids = {r["case_id"] for r in eligible_rows if r["primary_score_eligible"]}
    if expected_scored_counts is None:
        expected_scored_counts = {"prior_errors": 44, "independent_dev": 26,
                                  "old_structural_control": 3}
    strata_metrics, all_details = {}, []
    for stratum in E.STRATA:
        selected = {identity: ref for identity, ref in references.items()
                    if strata.get(identity) == stratum and identity in score_ids
                    and not ref.get("reference_uncertain", False)}
        metrics = E.metrics_for(gates, selected)
        strata_metrics[stratum] = {k: v for k, v in metrics.items() if k != "case_results"}
        all_details.extend({"stratum": stratum, **row} for row in metrics["case_results"])
    scored_refs = {identity: ref for identity, ref in references.items()
                   if identity in score_ids and not ref.get("reference_uncertain", False)}
    metrics, changes = score_changes(gates, old_results, scored_refs)
    details_by_id = index_rows_from(metrics["case_results"], "case_id")
    transitions = changes["binary_cell_transitions"]
    reference_uncertain_counts = Counter(strata[identity] for identity, ref in references.items()
                                         if ref.get("reference_uncertain", False))
    missing_reference_counts = Counter(strata[identity] for identity in set(strata) - set(references))
    summary = {
        "schema_version": "keyword-filtered-product-evaluation-result-v1",
        "experiment_status": "exploratory_dev_informed",
        "evaluation_scope": "73 scored prior-error, selected dev, and labeled control cases",
        "labels_read_after_candidate_gates_frozen": True,
        "independent_test": False, "full_data_accuracy_measured": False,
        "production_adoption_allowed": False,
        "source_stratum_case_counts": dict(Counter(strata.values())),
        "scored_case_counts": {k: v["scored_case_count"] for k, v in strata_metrics.items() if v["scored_case_count"]},
        "unscored_case_counts": {"independent_dev_uncertain": reference_uncertain_counts["independent_dev"],
                                  "old_structural_control_without_reference": missing_reference_counts["old_structural_control"],
                                  "diagnostic": sum(value == "product_pair_diagnostic" for value in strata.values())},
        "request_count": len(requests), "passthrough_request_count": len(passthrough),
        "candidate_prediction_sha256": sha(candidate_prediction_path),
        "candidate_metrics_by_stratum": strata_metrics,
        "candidate_scored_primary_reference_union": {k: v for k, v in metrics.items() if k != "case_results"},
        "original_top3_metrics_by_stratum": original_summary["variants"][VARIANT],
        "case_change_count": changes["case_change_count"],
        "binary_cell_transitions_vs_original_top3": transitions,
        "fn_to_tp_recovered_case_ids": changes["fn_to_tp_recovered_case_ids"],
        "tp_to_fn_regressed_case_ids": changes["tp_to_fn_regressed_case_ids"],
        "case_changes": changes["case_changes"],
        "reference_sha256": sha(original_eval_dir / "references-for-scoring.jsonl"),
        "reference_route_manifest_sha256": sha(original_routes_dir / "manifest.json"),
        "reference_case_count": len(references),
        "scored_case_count": len(scored_refs),
        "case_results": [details_by_id[key] for key in sorted(details_by_id)],
        "original_inputs_and_candidate_predictions_verified_unchanged": True,
        "output_sha256": {str(p.relative_to(output_dir)): sha(p) for p in output_dir.rglob("*")
                          if p.is_file() and p.name != "summary.json"}}
    expected_total = sum(expected_scored_counts.values())
    if (summary["scored_case_count"] != expected_total
            or summary["scored_case_counts"] != expected_scored_counts):
        raise ValueError("keyword evaluation scored population differs from the original 73-case cohort")
    write_rows(output_dir / "case-results.jsonl", all_details)
    if any(sha(Path(path)) != digest for path, digest in input_hashes.items()):
        raise ValueError("frozen prepared/CPU/candidate sources changed during scoring")
    if any(sha(Path(path)) != digest for path, digest in gate_hashes.items()):
        raise ValueError("candidate gates changed during scoring")
    if any(sha(Path(path)) != digest for path, digest in label_hashes.items()):
        raise ValueError("original references or source archives changed during scoring")
    summary["output_sha256"]["case-results.jsonl"] = sha(output_dir / "case-results.jsonl")
    # Recompute after writing the final detail file and bind every result.
    summary["output_sha256"] = {str(p.relative_to(output_dir)): sha(p) for p in output_dir.rglob("*")
                                 if p.is_file() and p.name != "summary.json"}
    write_json(output_dir / "summary.json", summary)
    return summary


def write_json(path: Path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def write_rows(path: Path, values):
    with Path(path).open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, default=PREP)
    parser.add_argument("--source-run-dir", type=Path, default=CPU_RUN)
    parser.add_argument("--candidate-dir", type=Path, default=CANDIDATE_RUN)
    parser.add_argument("--original-evaluation-dir", dest="original_eval_dir", type=Path, default=ORIGINAL_EVAL)
    parser.add_argument("--original-routes-dir", type=Path, default=ORIGINAL_ROUTES)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    result = run(**vars(parser.parse_args(argv)))
    print(json.dumps({"scored_case_count": result["scored_case_count"],
                      "candidate_metrics": result["candidate_scored_primary_reference_union"],
                      "case_change_count": result["case_change_count"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
