#!/usr/bin/env python3
"""Score the frozen, selected independent-product DEV20 cohort after explicit root GO.

This is a development evaluation, never an independent-test score. It uses
the established answer-envelope parser only after it verifies that the exact
20-case DEV membership, all predictions, gates, source inputs, proofs, code,
and runtime/model pins are frozen. Only selected case references are emitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MEMBERSHIP_SHA256 = "436d9c078f944a0265ebc6bedbaae42712c958f079a492387259f149385e2f32"
CASE_COUNT = 20
PRODUCT_COUNT = 10
SEED = "sku-next-dev-coordinate-refids-20261013-v1"
V2_ARCHIVE = ROOT / ".lab-output/sku-claude-additional-review-20261012-v1/20261011-sku-independent-real-eval-v2.zip"
TEST_CASE_PLANS = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/test/case-plans.jsonl"
PRIOR_DEV_MEMBERSHIP = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/membership.jsonl"
V3_ARCHIVE = ROOT / ".lab-output/sku-claude-additional-review-20261012-v1/20261012-sku-independent-real-eval-v3.zip"

if str(ROOT / "experiments/sku-matching") not in sys.path:
    sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
from grade_final_test_subset_v1 import (  # noqa: E402
    V2_LABEL_ARCHIVE_SHA256,
    V3_LABEL_ARCHIVE_SHA256,
    read_jsonl,
    read_label_archives,
    score_variant,
    sha,
    unique_index,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_repo_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"freeze path must be repo-relative and traversal-free: {value}")
    target = ROOT / path
    if not target.is_file() or target.is_symlink():
        raise FileNotFoundError(f"frozen file missing or unsafe: {value}")
    return target


def validate_dev_membership(
    membership_path: Path,
    case_plans_path: Path,
    row_plans_path: Path,
    selection_plan_path: Path,
    test_case_plans_path: Path = TEST_CASE_PLANS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, set[str]]]:
    """Check the frozen pre-classification DEV selection and all identities."""
    if sha_file(membership_path) != MEMBERSHIP_SHA256:
        raise ValueError("selected DEV membership SHA differs from the frozen 20-case plan")
    membership = read_jsonl(membership_path)
    cases = read_jsonl(case_plans_path)
    rows = read_jsonl(row_plans_path)
    plan = read_json(selection_plan_path)
    if sha_file(selection_plan_path) != "d658eeb40d3fa3eee3b09b0809059c2e6194bed3e4db2ed42c8b7693a6eda8e2":
        raise ValueError("frozen label-blind selection-plan bytes changed")
    source_root = ROOT / ".lab-output/sku-next-dev-coordinate-refids-20261013-v1"
    source_manifest = source_root / "manifest.json"
    source_policy = source_root / "selection-policy.json"
    source_report_path = source_root / "source-only-report.json"
    source_report = read_json(source_report_path)
    if (sha_file(source_manifest) != "b786a75e0ec6f8439beb8869bbc9e6482b76356d9dd9119503847b06bbfdf909"
            or sha_file(source_report_path) != "3f750caad3e699c310258f7c69926c79849b786f90bebacd55a001fd93aed43c"
            or sha_file(source_policy) != "f97050926ec4c289c57b6f08bc1e4bb5513ad15b0d74cc00c09e85c94f369579"
            or plan.get("selection_policy_sha256") != sha_file(source_policy)):
        raise ValueError("frozen source-only cohort input/report pin changed")
    if (source_report.get("schema_version") != "sku-next-dev-coordinate-refids-source-only-report-v1"
            or source_report.get("selected_products") != 10 or source_report.get("selected_cases") != 20
            or source_report.get("selected_rows") != 320 or source_report.get("original_bidirectional_tasks") != 1220
            or source_report.get("requests") != 773 or source_report.get("prior_dev20_product_overlap") != 0
            or source_report.get("labels_read") is not False or source_report.get("test_split_read") is not False
            or source_report.get("inference_run") is not False or source_report.get("scores_created") is not False
            or source_report.get("production_adoption_allowed") is not False):
        raise ValueError("source-only report does not attest the frozen independent DEV20 selection")
    if len(membership) != CASE_COUNT:
        raise ValueError("DEV membership must contain exactly 20 selected cases")
    members = unique_index(membership, "case_id", "DEV membership")
    case_index = unique_index(cases, "case_id", "selected DEV case plans")
    if set(case_index) != set(members):
        raise ValueError("selected DEV case plans do not exactly cover the frozen membership")
    test_cases = read_jsonl(test_case_plans_path)
    if len(test_cases) != 1292 or len({str(x.get("au_product_id")) for x in test_cases}) != 106:
        raise ValueError("original full-source TEST case-plan provenance is not the pinned 106-product cohort")
    test_case_ids = {str(x.get("case_id")) for x in test_cases}
    test_products = {str(x.get("au_product_id")) for x in test_cases}
    prior_membership = read_jsonl(PRIOR_DEV_MEMBERSHIP)
    if (sha_file(PRIOR_DEV_MEMBERSHIP) != "ab90d19dc4065be564acb8fe7d57d966d9c82f8018b1482de515083ab39f3a58"
            or len(prior_membership) != 40
            or len({str(x.get("au_product_id")) for x in prior_membership}) != 20):
        raise ValueError("original prior DEV20 identity membership source is not the pinned 20-product cohort")
    prior_products = {str(x.get("au_product_id")) for x in prior_membership}
    products = Counter()
    for case_id, member in members.items():
        if case_id in test_case_ids:
            raise ValueError(f"selected case overlaps original full-source TEST: {case_id}")
        if member.get("split", "dev") != "dev":
            raise ValueError(f"selected membership is not DEV: {case_id}")
        # This cohort's membership is identity-only. The case plan must retain
        # the source SKU key and digest of the original selected-SKU source line.
        case = case_index[case_id]
        for key in ("case_id", "au_product_id"):
            if case.get(key) != member.get(key):
                raise ValueError(f"membership/case-plan identity mismatch ({key}): {case_id}")
        if case.get("split") != "dev":
            raise ValueError(f"case plan source split is not DEV: {case_id}")
        if case.get("source_version") not in {"v2", "v3"} or not isinstance(case.get("raw_selected_sku_source_sha256"), str) or len(case["raw_selected_sku_source_sha256"]) != 64:
            raise ValueError(f"case plan lacks original source-line provenance: {case_id}")
        if not isinstance(case.get("source_sku_key"), str) or not case["source_sku_key"]:
            raise ValueError(f"case plan lacks exact source SKU pointer: {case_id}")
        products[str(member.get("au_product_id"))] += 1
    if set(products) & test_products:
        raise ValueError("selected DEV product overlaps original full-source 106-product TEST cohort")
    if set(products) & prior_products:
        raise ValueError("selected DEV products overlap the prior frozen DEV20")
    if len(products) != PRODUCT_COUNT or set(products.values()) != {2}:
        raise ValueError("frozen DEV selection must contain two cases from each of exactly 10 products")
    if (plan.get("schema_version") != "sku-next-dev-coordinate-refids-selection-v1"
            or plan.get("dataset") != SEED
            or plan.get("selected_case_count") != CASE_COUNT
            or plan.get("selected_product_count") != PRODUCT_COUNT
            or plan.get("membership_sha256") != MEMBERSHIP_SHA256
            or plan.get("prior_dev_membership_sha256") != "ab90d19dc4065be564acb8fe7d57d966d9c82f8018b1482de515083ab39f3a58"
            or plan.get("remaining_product_count") != 87
            or plan.get("excluded_prior_product_count") != 20
            or plan.get("labels_read") is not False
            or plan.get("test_split_read") is not False
            or plan.get("predictions_read") is not False
            or plan.get("hash_domain") != "IDs only"
            or plan.get("independent_test") is not False):
        raise ValueError("DEV selection plan is not the frozen label-blind DEV cohort")
    row_keys: dict[str, set[str]] = {identity: set() for identity in members}
    for row in rows:
        case_id = row.get("case_id")
        if case_id not in members:
            continue
        key = row.get("au_row_key", row.get("row_key"))
        if not isinstance(key, str) or not key:
            raise ValueError(f"selected row plan has a missing AU row key: {case_id}")
        row_keys[case_id].add(key)
    total_rows = 0
    for case_id, case in case_index.items():
        declared = case.get("row_keys")
        if not isinstance(declared, list) or not declared or set(declared) != row_keys[case_id]:
            raise ValueError(f"selected case row plans do not match their frozen complete row-key set: {case_id}")
        if case.get("all_original_au_rows_covered") is not True:
            raise ValueError(f"selected case omits an original AU row: {case_id}")
        total_rows += len(declared)
    if total_rows != 320:
        raise ValueError(f"selected cohort must cover exactly 320 original AU rows, got {total_rows}")
    return membership, cases, row_keys


def validate_dev_freeze_receipt(
    receipt_path: Path,
    *,
    membership_path: Path,
    case_plans_path: Path,
    row_plans_path: Path,
    selection_plan_path: Path,
    expected_variants: set[str] | None = None,
) -> dict[str, Any]:
    """Verify every receipt pin before reading any source-reference archive."""
    receipt = read_json(receipt_path)
    if receipt.get("schema_version") != "sku-next-dev-coordinate-refids-grading-freeze-v1":
        raise ValueError("unsupported DEV grading-freeze schema")
    required_true = ("all_predictions_frozen", "all_gates_frozen")
    if receipt.get("freeze_status") != "all_dev_predictions_and_gates_frozen" or any(receipt.get(k) is not True for k in required_true):
        raise ValueError("DEV predictions and every gate are not confirmed frozen")
    if receipt.get("source_split") != "dev" or receipt.get("independent_test") is not False or receipt.get("production_adoption_allowed") is not False:
        raise ValueError("scoring source must be explicitly DEV and independent_test=false")
    if (receipt.get("labels_read") is not False or receipt.get("test_split_read") is not False
            or receipt.get("test_identity_membership_read_for_disjointness") is not True
            or receipt.get("test_identity_use") != "disjointness_only"
            or receipt.get("test_labels_or_details_read_for_classification_or_selection") is not False
            or receipt.get("test_split_read_scope") != "original full-source TEST case-plan identities read solely for case/product disjointness; no TEST details, labels, or outcomes read"):

            raise ValueError("freeze receipt must distinguish identity-only TEST disjointness from unread labels/outcomes")
    if receipt.get("membership_sha256") != MEMBERSHIP_SHA256:
        raise ValueError("freeze receipt has a different DEV membership SHA")
    if receipt.get("policy_or_threshold_changed") is not False or receipt.get("classifier_outputs_changed_after_freeze") is not False:
        raise ValueError("policy or classifier outputs changed after freeze")
    variants = receipt.get("variants")
    if not isinstance(variants, dict) or set(variants) != {"coordinate-refids-choice-nli", "literal-only"}:
        raise ValueError("freeze receipt must contain both primary coordinate-refids and genuine literal-only variants")
    if expected_variants is not None and set(variants) != expected_variants:
        raise ValueError("frozen DEV variant set differs from the requested variants")
    gate_files = receipt.get("gate_files")
    if not isinstance(gate_files, list) or not gate_files or any(not isinstance(p, str) for p in gate_files):
        raise ValueError("frozen DEV gate file list is missing")
    input_files = receipt.get("input_files")
    expected_inputs = {
        "membership": membership_path,
        "case_plans": case_plans_path,
        "row_plans": row_plans_path,
        "selection_plan": selection_plan_path,
    }
    if not isinstance(input_files, dict):
        raise ValueError("frozen DEV source input map is missing")
    for key, target in expected_inputs.items():
        rel = target.resolve().relative_to(ROOT).as_posix()
        if input_files.get(key) != rel:
            raise ValueError(f"DEV freeze receipt does not bind exact {key} path")

    pins = receipt.get("frozen_files")
    if not isinstance(pins, dict) or not pins:
        raise ValueError("DEV freeze receipt lacks complete frozen-file SHA/byte pins")
    needed = {p for p in input_files.values() if isinstance(p, str)} | set(gate_files)
    for name, entry in variants.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("predictions"), str):
            raise ValueError(f"variant prediction path is missing: {name}")
        needed.add(entry["predictions"])
        variant_gates = entry.get("gate_files", [])
        if not isinstance(variant_gates, list) or not variant_gates:
            raise ValueError(f"variant gate list is malformed: {name}")
        if not set(variant_gates) <= set(gate_files):
            raise ValueError(f"variant gates are not in the frozen gate inventory: {name}")
        basenames = [Path(item).name.lower().replace("-", "_") for item in variant_gates]
        if not any("decision" in item for item in basenames):
            raise ValueError(f"variant gate freeze lacks case decisions: {name}")
        if not any("row" in item and "result" in item for item in basenames):
            raise ValueError(f"variant gate freeze lacks all-row result output: {name}")
    source_artifacts = receipt.get("prepared_source_artifacts")
    source_keys = {"source_only_report", "source_manifest", "nli_freeze", "model_pairs", "paired_contexts", "source_documents", "validation_bindings", "preparation_errors"}
    if not isinstance(source_artifacts, dict) or set(source_artifacts) != source_keys or any(not isinstance(x, str) for x in source_artifacts.values()):
        raise ValueError("freeze receipt must bind the canonical prepared source-NLI artifact set")
    cohort_rel = ".lab-output/sku-next-dev-coordinate-refids-20261013-v1"
    if (source_artifacts["source_only_report"] != f"{cohort_rel}/source-only-report.json"
            or source_artifacts["source_manifest"] != f"{cohort_rel}/manifest.json"):
        raise ValueError("freeze receipt source-only artifacts do not bind this exact cohort")
    nli_names = {"nli_freeze": "freeze.json", "model_pairs": "model-pairs.jsonl",
                 "paired_contexts": "paired-contexts.jsonl", "source_documents": "source-documents.jsonl",
                 "validation_bindings": "validation-bindings.jsonl", "preparation_errors": "preparation-errors.jsonl"}
    nli_dirs = set()
    for key, basename in nli_names.items():
        path = Path(source_artifacts[key])
        if path.name != basename or not path.parent.name.startswith("source-nli-") or path.parent.name == "source-nli-v1":
            raise ValueError("freeze receipt must use the new canonical prepared source-NLI directory")
        nli_dirs.add(path.parent.as_posix())
    if len(nli_dirs) != 1:
        raise ValueError("prepared source-NLI artifact set spans multiple directories")
    for rel in source_artifacts.values():
        needed.add(rel)
    test_identity_rel = TEST_CASE_PLANS.resolve().relative_to(ROOT).as_posix()
    prior_identity_rel = PRIOR_DEV_MEMBERSHIP.resolve().relative_to(ROOT).as_posix()
    if (receipt.get("full_test_identity_case_plans") != test_identity_rel
            or receipt.get("prior_dev20_identity_membership") != prior_identity_rel):
        raise ValueError("freeze receipt must name the exact identity-only disjointness provenance files")
    needed.update({test_identity_rel, prior_identity_rel,
                   f"{cohort_rel}/manifest.json", f"{cohort_rel}/source-only-report.json",
                   f"{cohort_rel}/selection-policy.json",
                   ".lab-output/sku-next-dev-coordinate-execution-20261013-v1/assemble_grade_freeze.py"})
    groups = receipt.get("frozen_artifact_groups")
    group_keys = {"model_outputs", "runtime", "proofs", "code"}
    if not isinstance(groups, dict) or set(groups) != group_keys or any(
            not isinstance(groups.get(k), list) or not groups[k] or any(not isinstance(x, str) for x in groups[k])
            for k in group_keys):
        raise ValueError("DEV freeze must separately enumerate frozen model outputs, runtime, proofs, and code")
    required_artifacts = receipt.get("required_artifact_paths")
    expected_required = set().union(*(set(groups[k]) for k in group_keys))
    if not isinstance(required_artifacts, list) or set(required_artifacts) != expected_required:
        raise ValueError("DEV freeze artifact list must exactly equal its model/runtime/proof/code groups")
    needed.update(required_artifacts)
    for rel in needed:
        if rel not in pins:
            raise ValueError(f"frozen receipt lacks an input/prediction/gate pin: {rel}")
    grader_rel = Path(__file__).resolve().relative_to(ROOT).as_posix()
    helper_rel = "experiments/sku-matching/route_mixed_sku_references_v1.py"
    score_helper_rel = "experiments/sku-matching/grade_final_test_subset_v1.py"
    tests_rel = "tests/test_lab_next_dev_coordinate_refids_grade.py"
    assembler_rel = ".lab-output/sku-next-dev-coordinate-execution-20261013-v1/assemble_grade_freeze.py"
    for rel in (grader_rel, helper_rel, score_helper_rel, tests_rel, assembler_rel):
        if rel not in pins:
            raise ValueError(f"frozen receipt does not pin scoring code: {rel}")
    if receipt.get("grader_code_sha256") != sha_file(ROOT / grader_rel):
        raise ValueError("DEV grader code SHA differs from freeze receipt")
    if receipt.get("reference_parser_helper_sha256") != sha_file(ROOT / helper_rel):
        raise ValueError("shared exact source parser SHA differs from freeze receipt")
    if receipt.get("score_helper_sha256") != sha_file(ROOT / score_helper_rel):
        raise ValueError("shared score-contract helper SHA differs from freeze receipt")
    if receipt.get("grader_tests_sha256") != sha_file(ROOT / tests_rel):
        raise ValueError("synthetic grader test SHA differs from freeze receipt")
    if receipt.get("freeze_assembler_sha256") != sha_file(ROOT / assembler_rel):
        raise ValueError("grade freeze assembler SHA differs from freeze receipt")

    actual_pins: dict[str, str] = {}
    for rel, pin in pins.items():
        path = safe_repo_path(rel)
        lower_parts = {part.lower() for part in Path(rel).parts}
        if (path.resolve() in {V2_ARCHIVE.resolve(), V3_ARCHIVE.resolve()}
                or ("test" in lower_parts and rel != TEST_CASE_PLANS.resolve().relative_to(ROOT).as_posix())
                or lower_parts & {"label", "labels", "gold", "truth", "answers", "answer-shards", "labels-token"}
                or Path(rel).name.lower() in {"resolved.jsonl", "label-manifest.json", "answers.jsonl"}):
            raise ValueError("label-bearing archives/files must remain external to the pre-grade frozen-file inventory")
        if any(part.lower() in {"labels-token", "gold", "answer-shards"} for part in Path(rel).parts):
            raise ValueError(f"label-bearing path must not be included in the pre-grade freeze: {rel}")
        if not isinstance(pin, dict) or type(pin.get("bytes")) is not int or not isinstance(pin.get("sha256"), str):
            raise ValueError(f"invalid bytes/SHA pin: {rel}")
        if path.stat().st_size != pin["bytes"] or sha_file(path) != pin["sha256"]:
            raise ValueError(f"frozen file pin mismatch: {rel}")
        actual_pins[rel] = pin["sha256"]
    if any(rel not in pins for rel in gate_files):
        raise ValueError("a final gate artifact is outside the frozen file inventory")
    return {"receipt_sha256": sha_file(receipt_path), "membership_sha256": MEMBERSHIP_SHA256,
            "frozen_file_sha256": actual_pins, "variant_names": sorted(variants),
            "gate_files": list(gate_files), "freeze_status": receipt["freeze_status"]}


def validate_frozen_output_coverage(
    membership: list[dict[str, Any]],
    row_keys: dict[str, set[str]],
    prediction_rows: dict[str, list[dict[str, Any]]],
    gate_contents: list[tuple[str, list[dict[str, Any]]]],
) -> None:
    """Require full frozen case/row outputs before opening selected references."""
    expected_case_ids = {row["case_id"] for row in membership}
    expected_row_pairs = {(case_id, key) for case_id, keys in row_keys.items() for key in keys}
    for name, rows in prediction_rows.items():
        if set(unique_index(rows, "case_id", f"{name} frozen predictions")) != expected_case_ids:
            raise ValueError(f"frozen predictions do not exactly cover all 20 selected DEV cases: {name}")
    for rel, rows in gate_contents:
        basename = Path(rel).name.lower().replace("-", "_")
        if "decision" in basename and set(unique_index(rows, "case_id", f"{rel} decisions")) != expected_case_ids:
            raise ValueError(f"frozen case decisions do not exactly cover all 20 selected DEV cases: {rel}")
        if "row" in basename and "result" in basename:
            actual_pairs = set()
            for row in rows:
                pair = (row.get("case_id"), row.get("au_row_key", row.get("row_key")))
                if pair in actual_pairs:
                    raise ValueError(f"duplicate frozen all-row result: {rel}")
                actual_pairs.add(pair)
            if actual_pairs != expected_row_pairs:
                raise ValueError(f"frozen all-row results do not exactly cover the 320 selected AU rows: {rel}")


def grade_from_files(
    *,
    root_go: bool,
    membership_path: Path,
    case_plans_path: Path,
    row_plans_path: Path,
    selection_plan_path: Path,
    receipt_path: Path,
    archive_v2: Path = V2_ARCHIVE,
    archive_v3: Path = V3_ARCHIVE,
    expected_variants: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if root_go is not True:
        raise PermissionError("DEV scoring requires explicit --root-go after all gates are frozen")
    # Freeze checks, selected DEV membership and selected source plans all run
    # before either source answer archive is opened.
    receipt_info = validate_dev_freeze_receipt(
        receipt_path, membership_path=membership_path, case_plans_path=case_plans_path,
        row_plans_path=row_plans_path, selection_plan_path=selection_plan_path,
        expected_variants=expected_variants)
    membership, case_plans, row_keys = validate_dev_membership(
        membership_path, case_plans_path, row_plans_path, selection_plan_path)
    case_index = unique_index(case_plans, "case_id", "selected DEV case plans")
    graded_membership = [{**member, **{k: case_index[member["case_id"]][k]
                                       for k in ("dossier_id", "source_version", "split")}}
                         for member in membership]
    receipt = read_json(receipt_path)
    variants = receipt["variants"]
    prediction_rows = {}
    for name, entry in variants.items():
        path = safe_repo_path(entry["predictions"])
        prediction_rows[name] = read_jsonl(path)
    gate_contents = []
    gate_rows = []
    for rel in receipt["gate_files"]:
        gate_path = safe_repo_path(rel)
        # Read only already-frozen outputs and enforce full coverage before
        # opening the selected DEV reference archives.
        gate_contents.append((rel, read_jsonl(gate_path)))
        gate_rows.append({"path": rel, "sha256": sha_file(gate_path), "bytes": gate_path.stat().st_size})
    validate_frozen_output_coverage(membership, row_keys, prediction_rows, gate_contents)

    # The existing parser validates archive/member/answer-row pins, keeps every
    # nonselected resolved row opaque, and checks exact source AU row keys.
    references = read_label_archives(archive_v2, archive_v3, graded_membership, row_plans_path)
    expected_ids = {row["case_id"] for row in graded_membership}
    if set(references) != expected_ids or len(references) != CASE_COUNT:
        raise ValueError("selected DEV reference rows do not exactly cover the frozen 20-case membership")
    results = {name: score_variant(graded_membership, rows, references)
               for name, rows in sorted(prediction_rows.items())}
    score = {
        "schema_version": "sku-next-dev-coordinate-refids-gate-score-v1",
        "phase": "dev_score",
        "source_split": "dev",
        "independent_test": False,
        "membership_sha256": MEMBERSHIP_SHA256,
        "selected_case_count": CASE_COUNT,
        "selected_product_count": PRODUCT_COUNT,
        "selected_row_count": 320,
        "reference_kind": "claude-haiku-5-5 machine annotation; not human gold",
        "labels_read_after_all_predictions_and_gates_frozen": True,
        "predictions_changed": False,
        "gates_changed": False,
        "thresholds_selected": False,
        "production_adoption_allowed": False,
        "new_dev_code": True,
        "same_membership_for_all_variants": True,
        "freeze_receipt": receipt_info,
        "reference_archive_sha256": {"v2": sha_file(archive_v2), "v3": sha_file(archive_v3)},
        "gate_file_sha256": gate_rows,
        "variants": results,
        "selected_reference_count": len(references),
    }
    selected_rows = [references[case_id] for case_id in sorted(references)]
    return score, selected_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-go", action="store_true", help="required after all DEV policies/predictions/gates freeze")
    parser.add_argument("--membership", type=Path, default=ROOT / ".lab-output/sku-next-dev-coordinate-refids-20261013-v1/membership.jsonl")
    parser.add_argument("--case-plans", type=Path, default=ROOT / ".lab-output/sku-next-dev-coordinate-refids-20261013-v1/case-plans.jsonl")
    parser.add_argument("--row-plans", type=Path, default=ROOT / ".lab-output/sku-next-dev-coordinate-refids-20261013-v1/row-plans.jsonl")
    parser.add_argument("--selection-plan", type=Path, default=ROOT / ".lab-output/sku-next-dev-coordinate-refids-20261013-v1/selection-plan.json")
    parser.add_argument("--freeze-receipt", type=Path, required=True)
    parser.add_argument("--expected-variant", action="append", default=[])
    parser.add_argument("--v2-label-archive", type=Path, default=V2_ARCHIVE)
    parser.add_argument("--v3-label-archive", type=Path, default=V3_ARCHIVE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--references-output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() == args.references_output.resolve():
        raise ValueError("score and selected-reference outputs must be different files")
    if args.output.exists() or args.references_output.exists():
        raise FileExistsError("refusing to overwrite a prior DEV score or reference artifact")
    expected = set(args.expected_variant) if args.expected_variant else None
    score, references = grade_from_files(
        root_go=args.root_go, membership_path=args.membership, case_plans_path=args.case_plans,
        row_plans_path=args.row_plans, selection_plan_path=args.selection_plan,
        receipt_path=args.freeze_receipt, archive_v2=args.v2_label_archive,
        archive_v3=args.v3_label_archive, expected_variants=expected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.references_output.parent.mkdir(parents=True, exist_ok=True)
    with args.references_output.open("xb") as stream:
        for row in references:
            stream.write((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    with args.output.open("xb") as stream:
        stream.write((json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"score": str(args.output), "selected_references": str(args.references_output),
                      "selected_reference_count": len(references), "source_split": "dev",
                      "independent_test": False, "labels_read_after_freeze": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
