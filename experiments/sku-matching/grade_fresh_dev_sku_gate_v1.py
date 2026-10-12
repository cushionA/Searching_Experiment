#!/usr/bin/env python3
"""Score a frozen, selected DEV cohort after an explicit root GO.

This is a development evaluation, never an independent-test score. It uses
the established answer-envelope parser only after it verifies that the exact
40-case DEV membership, all predictions, gates, source inputs, proofs, code,
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
MEMBERSHIP_SHA256 = "ab90d19dc4065be564acb8fe7d57d966d9c82f8018b1482de515083ab39f3a58"
CASE_COUNT = 40
PRODUCT_COUNT = 20
SEED = "next-dev-v7-fullconditions-20261013"
V2_ARCHIVE = ROOT / ".lab-output/sku-claude-additional-review-20261012-v1/20261011-sku-independent-real-eval-v2.zip"
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, set[str]]]:
    """Check the frozen pre-classification DEV selection and all identities."""
    if sha_file(membership_path) != MEMBERSHIP_SHA256:
        raise ValueError("selected DEV membership SHA differs from the frozen 40-case plan")
    membership = read_jsonl(membership_path)
    cases = read_jsonl(case_plans_path)
    rows = read_jsonl(row_plans_path)
    plan = read_json(selection_plan_path)
    if len(membership) != CASE_COUNT:
        raise ValueError("DEV membership must contain exactly 40 selected cases")
    members = unique_index(membership, "case_id", "DEV membership")
    case_index = unique_index(cases, "case_id", "selected DEV case plans")
    if set(case_index) != set(members):
        raise ValueError("selected DEV case plans do not exactly cover the frozen membership")
    products = Counter()
    for case_id, member in members.items():
        if member.get("source_version") not in {"v2", "v3"}:
            raise ValueError(f"unsupported DEV source version: {case_id}")
        if member.get("split", "dev") != "dev":
            raise ValueError(f"selected membership is not DEV: {case_id}")
        if not isinstance(member.get("source_case_line_sha256"), str) or len(member["source_case_line_sha256"]) != 64:
            raise ValueError(f"missing exact source-case line digest: {case_id}")
        if not isinstance(member.get("source_case_input_sha256"), str) or len(member["source_case_input_sha256"]) != 64:
            raise ValueError(f"missing source-case input digest: {case_id}")
        expected_source = plan.get("source_case_input_sha256_by_version", {}).get(member["source_version"])
        if member["source_case_input_sha256"] != expected_source:
            raise ValueError(f"source-version/input SHA mismatch: {case_id}")
        case = case_index[case_id]
        for key in ("case_id", "dossier_id", "au_product_id", "source_version"):
            if case.get(key) != member.get(key):
                raise ValueError(f"membership/case-plan identity mismatch ({key}): {case_id}")
        if case.get("split") != "dev":
            raise ValueError(f"case plan source split is not DEV: {case_id}")
        products[str(member.get("au_product_id"))] += 1
    if len(products) != PRODUCT_COUNT or set(products.values()) != {2}:
        raise ValueError("frozen DEV selection must contain two cases from each of exactly 20 products")
    if (plan.get("schema_version") != "sku-next-dev-v7-subset-plan-v1"
            or plan.get("seed") != SEED or plan.get("source_split") != "dev"
            or plan.get("selected_case_count") != CASE_COUNT
            or plan.get("selected_product_count") != PRODUCT_COUNT
            or plan.get("membership_sha256") != MEMBERSHIP_SHA256
            or plan.get("labels_read") is not False
            or plan.get("label_archive_read") is not False
            or plan.get("test_split_read") is not False
            or plan.get("predictions_read") is not False
            or plan.get("selection_uses_only_case_and_product_identity") is not True
            or plan.get("selection_uses_condition_text_or_availability") is not False
            or plan.get("selection_uses_v7_status_or_predictions") is not False):
        raise ValueError("DEV selection plan is not the frozen label-blind DEV cohort")
    if (plan.get("selection_excludes_missing_or_wrong_options") is not False
            or plan.get("model_inference_run") is not False
            or plan.get("gate_output_created") is not False
            or plan.get("selection_membership_binds_exact_source_case_line") is not True):
        raise ValueError("DEV selection plan does not preserve its preclassification/source-line contract")
    row_keys: dict[str, set[str]] = {identity: set() for identity in members}
    for row in rows:
        case_id = row.get("case_id")
        if case_id not in members:
            continue
        key = row.get("au_row_key", row.get("row_key"))
        if not isinstance(key, str) or not key:
            raise ValueError(f"selected row plan has a missing AU row key: {case_id}")
        row_keys[case_id].add(key)
    for case_id, case in case_index.items():
        declared = case.get("row_keys")
        if not isinstance(declared, list) or not declared or set(declared) != row_keys[case_id]:
            raise ValueError(f"selected case row plans do not match their frozen complete row-key set: {case_id}")
        if case.get("all_original_au_rows_covered") is not True:
            raise ValueError(f"selected case omits an original AU row: {case_id}")
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
    if receipt.get("schema_version") != "sku-next-dev-grading-freeze-v1":
        raise ValueError("unsupported DEV grading-freeze schema")
    required_true = ("all_predictions_frozen", "all_gates_frozen")
    if receipt.get("freeze_status") != "all_dev_predictions_and_gates_frozen" or any(receipt.get(k) is not True for k in required_true):
        raise ValueError("DEV predictions and every gate are not confirmed frozen")
    if receipt.get("source_split") != "dev" or receipt.get("independent_test") is not False:
        raise ValueError("scoring source must be explicitly DEV and independent_test=false")
    if receipt.get("labels_read") is not False or receipt.get("test_split_read") is not False:
        raise ValueError("freeze receipt does not prove that labels/test split remained unread")
    if receipt.get("membership_sha256") != MEMBERSHIP_SHA256:
        raise ValueError("freeze receipt has a different DEV membership SHA")
    if receipt.get("policy_or_threshold_changed") is not False or receipt.get("classifier_outputs_changed_after_freeze") is not False:
        raise ValueError("policy or classifier outputs changed after freeze")
    variants = receipt.get("variants")
    if not isinstance(variants, dict) or not variants or (expected_variants is not None and set(variants) != expected_variants):
        raise ValueError("frozen DEV variant set is missing or differs from the requested variants")
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
    required_artifacts = receipt.get("required_artifact_paths")
    if not isinstance(required_artifacts, list) or not required_artifacts or any(not isinstance(x, str) for x in required_artifacts):
        raise ValueError("DEV freeze must list all source/proof/runtime/model/code artifacts explicitly")
    needed.update(required_artifacts)
    for rel in needed:
        if rel not in pins:
            raise ValueError(f"frozen receipt lacks an input/prediction/gate pin: {rel}")
    grader_rel = Path(__file__).resolve().relative_to(ROOT).as_posix()
    helper_rel = "experiments/sku-matching/route_mixed_sku_references_v1.py"
    score_helper_rel = "experiments/sku-matching/grade_final_test_subset_v1.py"
    tests_rel = "tests/test_lab_fresh_dev_sku_gate.py"
    for rel in (grader_rel, helper_rel, score_helper_rel, tests_rel):
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

    actual_pins: dict[str, str] = {}
    for rel, pin in pins.items():
        path = safe_repo_path(rel)
        lower_parts = {part.lower() for part in Path(rel).parts}
        if (path.resolve() in {V2_ARCHIVE.resolve(), V3_ARCHIVE.resolve()}
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
    membership, _, row_keys = validate_dev_membership(
        membership_path, case_plans_path, row_plans_path, selection_plan_path)
    receipt = read_json(receipt_path)
    variants = receipt["variants"]
    prediction_rows = {}
    for name, entry in variants.items():
        path = safe_repo_path(entry["predictions"])
        prediction_rows[name] = read_jsonl(path)
    gate_rows = []
    for rel in receipt["gate_files"]:
        gate_path = safe_repo_path(rel)
        # Read only the already-frozen gate outputs; no output is modified.
        gate_rows.append({"path": rel, "sha256": sha_file(gate_path), "bytes": gate_path.stat().st_size})

    # The existing parser validates archive/member/answer-row pins, keeps every
    # nonselected resolved row opaque, and checks exact source AU row keys.
    references = read_label_archives(archive_v2, archive_v3, membership, row_plans_path)
    expected_ids = {row["case_id"] for row in membership}
    if set(references) != expected_ids or len(references) != CASE_COUNT:
        raise ValueError("selected DEV reference rows do not exactly cover the frozen 40-case membership")
    results = {name: score_variant(membership, rows, references)
               for name, rows in sorted(prediction_rows.items())}
    score = {
        "schema_version": "sku-next-dev-gate-score-v1",
        "phase": "dev_score",
        "source_split": "dev",
        "independent_test": False,
        "membership_sha256": MEMBERSHIP_SHA256,
        "selected_case_count": CASE_COUNT,
        "selected_product_count": PRODUCT_COUNT,
        "reference_kind": "claude-haiku-5-5 machine annotation; not human gold",
        "labels_read_after_all_predictions_and_gates_frozen": True,
        "predictions_changed": False,
        "gates_changed": False,
        "thresholds_selected": False,
        "production_adoption_allowed": False,
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
    parser.add_argument("--membership", type=Path, default=ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/membership.jsonl")
    parser.add_argument("--case-plans", type=Path, default=ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/case-plans.jsonl")
    parser.add_argument("--row-plans", type=Path, default=ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/row-plans.jsonl")
    parser.add_argument("--selection-plan", type=Path, default=ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/selection-plan.json")
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
