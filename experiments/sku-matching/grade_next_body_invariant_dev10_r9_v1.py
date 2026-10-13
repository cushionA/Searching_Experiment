#!/usr/bin/env python3
"""Freeze or score the exact DEV10 body-invariant run after a separate root GO."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COHORT = ROOT / ".lab-output/sku-next-body-invariant-dev-20261013-v1"
PREPARED = ROOT / ".lab-output/sku-next-body-invariant-v3-source-only-20261013-r9-v3"
PROOF = ROOT / ".lab-output/sku-next-body-invariant-literal-proofs-20261013-v1"
PAIR_PARTITION = ROOT / ".lab-output/sku-next-body-invariant-dev10-pair-partition-20261013-v5"
EXECUTION = ROOT / ".lab-output/sku-next-body-invariant-execution-20261013-v1"
VARIANTS = {"body-invariant-v3", "literal-only"}
CASE_COUNT, PRODUCT_COUNT, ROW_COUNT, TASK_COUNT = 10, 5, 110, 452

sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import assemble_next_body_invariant_dev10_r9_v1 as ASSEMBLER  # noqa: E402
import evaluate_dev_source_bound_whole_sku_gate_v1 as EVAL  # noqa: E402
import grade_final_test_subset_v1 as SCORE  # noqa: E402
import sku_body_invariant_choice_v3 as BODY  # noqa: E402
import sku_body_invariant_native_rakuten_source_v1 as NATIVE  # noqa: E402
import sku_directional_choice_nli_v1 as NLI  # noqa: E402


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pin(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": sha_file(path)}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def read_rows(path: Path) -> list[dict[str, Any]]:
    return NLI.read_rows(Path(path))


def safe_file(rel: str) -> Path:
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"freeze path must be safe and repo-relative: {rel}")
    lower = {part.lower() for part in path.parts}
    if lower & {"labels", "gold", "truth", "answers", "answer-shards", "labels-token"}:
        raise ValueError(f"label-bearing artifact cannot be frozen before root GO: {rel}")
    result = ROOT / path
    if not result.is_file() or result.is_symlink():
        raise FileNotFoundError(f"frozen source/output artifact missing or unsafe: {rel}")
    return result


def _output_files(assembly_dir: Path) -> dict[str, dict[str, Any]]:
    inventory_path = assembly_dir / "assembly-output-inventory.json"
    receipt_path = assembly_dir / "assembly-receipt.json"
    if not inventory_path.is_file() or not receipt_path.is_file():
        raise ValueError("completed all-variant DEV10 assembly is required before freeze")
    inventory = read_json(inventory_path)
    receipt = read_json(receipt_path)
    if (inventory.get("schema_version") != "body-dev10-r9-output-inventory-v1"
            or inventory.get("labels_read") is not False or inventory.get("scores_created") is not False
            or receipt.get("schema_version") != "sku-next-body-invariant-dev10-r9-assembly-v1"
            or receipt.get("status") != "complete_both_whole_gate_variants_frozen"
            or receipt.get("labels_read") is not False or receipt.get("scores_created") is not False
            or receipt.get("case_count") != CASE_COUNT or receipt.get("product_count") != PRODUCT_COUNT
            or receipt.get("row_count") != ROW_COUNT or receipt.get("task_count") != TASK_COUNT
            or set(receipt.get("variants", {})) != VARIANTS
            or receipt.get("prepared_pair_count") != 141
            or receipt.get("expected_dispatched_pair_count", 0) + receipt.get("literal_protected_pair_count", 0) != 141
            or receipt.get("raw_response_count", 0) + receipt.get("missing_response_count", 0)
                != receipt.get("expected_dispatched_pair_count")):
        raise ValueError("assembly outputs/receipt are not the complete blind DEV10 run")
    pins = inventory.get("files")
    if not isinstance(pins, dict) or not pins:
        raise ValueError("assembly output inventory is empty")
    current = {p.relative_to(assembly_dir).as_posix() for p in assembly_dir.rglob("*")
               if p.is_file() and p != inventory_path}
    if current != set(pins):
        raise ValueError("assembly output directory has missing or unlisted files")
    for rel, expected in pins.items():
        path = assembly_dir / rel
        if not path.is_file() or path.is_symlink() or pin(path) != expected:
            raise ValueError(f"DEV10 assembly output SHA mismatch: {rel}")
    partition_receipt_path = assembly_dir / "pair-partition-receipt.json"
    if pin(partition_receipt_path)["sha256"] != receipt.get("pair_partition_receipt_sha256"):
        raise ValueError("assembly output is not bound to its exact pre-model pair partition")
    dispatch_ids = [row["pair_id"] for row in read_rows(assembly_dir / "dispatch-pair-ids.jsonl")]
    protected_ids = [row["pair_id"] for row in read_rows(assembly_dir / "literal-protected-pair-ids.jsonl")]
    responses = read_rows(assembly_dir / "raw-literal-responses.jsonl")
    response_ids = [row.get("pair_id") for row in responses]
    failures = read_rows(assembly_dir / "missing-response-failures.jsonl")
    report = read_rows(assembly_dir / "source-validation-report.jsonl")
    validated = read_rows(assembly_dir / "validated-pairs-for-eval.jsonl")
    if (len(dispatch_ids) != len(set(dispatch_ids)) or len(protected_ids) != len(set(protected_ids))
            or set(dispatch_ids) & set(protected_ids) or len(dispatch_ids) + len(protected_ids) != 141
            or len(response_ids) != len(set(response_ids)) or not set(response_ids) <= set(dispatch_ids)
            or set(response_ids) & set(protected_ids)
            or len(failures) != receipt.get("missing_response_count")
            or len(report) != len(response_ids)
            or set(row.get("pair_id") for row in failures) != set(dispatch_ids) - set(response_ids)
            or set(row.get("pair_id") for row in report) != set(response_ids)
            or not set(row.get("pair_id") for row in validated) <= set(response_ids)
            or receipt.get("expected_dispatched_pair_ids_sha256") != BODY.sha_bytes(BODY.canon(sorted(dispatch_ids)))
            or receipt.get("literal_protected_pair_ids_sha256") != BODY.sha_bytes(BODY.canon(sorted(protected_ids)))):
        raise ValueError("frozen raw responses, protected pairs, or failure ledger violate pre-model partition")
    _members, cases, row_plans, tasks, _requests = ASSEMBLER.verify_dev10_cohort(PREPARED)
    expected_cases = {row["case_id"] for row in cases}
    expected_rows = {(row["case_id"], row["au_row_key"]) for row in row_plans}
    expected_tasks = {row["task_id"] for row in tasks}
    for variant in VARIANTS:
        variant_dir = assembly_dir / variant
        resolutions = read_rows(variant_dir / "task-resolutions.jsonl")
        row_results = read_rows(variant_dir / "all-row-results.jsonl")
        decisions = read_rows(variant_dir / "case-decisions.jsonl")
        manifest = read_json(variant_dir / "gate-manifest.json")
        if (set(EVAL.index(resolutions, "task_id", "frozen body task resolution")) != expected_tasks
                or {(row.get("case_id"), row.get("au_row_key")) for row in row_results} != expected_rows
                or len(row_results) != ROW_COUNT
                or set(EVAL.index(decisions, "case_id", "frozen body case decision")) != expected_cases
                or manifest.get("variant") != variant
                or manifest.get("source_split") != "dev"
                or manifest.get("independent_test") is not False
                or manifest.get("labels_read") is not False
                or manifest.get("test_labels_read") is not False
                or manifest.get("production_adoption_allowed") is not False):
            raise ValueError(f"body DEV10 {variant} gate outputs lack exact frozen task/row/case coverage")
    return {rel: pin(assembly_dir / rel) for rel in pins} | {
        "assembly-receipt.json": pin(receipt_path), "assembly-output-inventory.json": pin(inventory_path)}


def assemble_freeze(spec: dict[str, Any], output: Path) -> dict[str, Any]:
    if spec.get("root_go_for_grading") is not False or spec.get("labels_read") is not False:
        raise ValueError("pre-grade DEV10 freeze must explicitly withhold root GO and labels")
    members, cases, rows, tasks, _requests = ASSEMBLER.verify_dev10_cohort(PREPARED)
    assembly_dir = Path(spec.get("assembly_output_dir", "")).resolve()
    if not assembly_dir.is_relative_to(ROOT):
        raise ValueError("assembly directory must be inside repository workspace")
    partition_dir = Path(spec.get("pair_partition_dir", PAIR_PARTITION)).resolve()
    partition_receipt, _partition = ASSEMBLER.validate_partition(partition_dir, PREPARED, PROOF)
    model_output_files = _output_files(assembly_dir)
    groups = spec.get("frozen_artifact_groups")
    expected_groups = {"runtime", "proofs", "code"}
    if not isinstance(groups, dict) or set(groups) != expected_groups:
        raise ValueError("freeze spec must enumerate runtime, proof, and code artifacts")
    artifact_groups: dict[str, set[str]] = {
        "model_outputs": {f"{assembly_dir.relative_to(ROOT).as_posix()}/{rel}" for rel in model_output_files},
        "runtime": set(), "proofs": set(), "code": set()}
    for group in expected_groups:
        paths = groups[group]
        if not isinstance(paths, list) or not paths or any(not isinstance(x, str) for x in paths):
            raise ValueError(f"freeze {group} group must be a nonempty repo-relative file list")
        artifact_groups[group].update(paths)
    # Pin every source/input artifact named by its frozen manifest, plus the
    # identity-only policy and prior-cohort membership receipts.
    for manifest_path, hash_key, group in (
        (COHORT / "manifest.json", "outputs_sha256", "runtime"),
        (PREPARED / "manifest.json", "artifact_sha256", "runtime"),
        (PROOF / "manifest.json", "outputs_sha256", "proofs"),
    ):
        source_manifest = read_json(manifest_path)
        artifact_groups[group].update(
            f"{manifest_path.parent.relative_to(ROOT).as_posix()}/{name}"
            for name in source_manifest.get(hash_key, {}))
        artifact_groups[group].add(manifest_path.relative_to(ROOT).as_posix())
    artifact_groups["runtime"].update(
        path.relative_to(ROOT).as_posix()
        for path in ASSEMBLER.referenced_source_artifacts(PREPARED, BODY.DEFAULT_SOURCE))
    artifact_groups["runtime"].update({
        ".lab-output/sku-next-body-invariant-dev-20261013-v1/selection-policy.json",
        ".lab-output/sku-next-body-invariant-dev-20261013-v1/selection-plan.json",
        ".lab-output/sku-next-body-invariant-v3-source-only-20261013-r9-v3/freeze.json",
        ".lab-output/sku-next-body-invariant-v3-source-only-20261013-r9-v3/summary.json",
        ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8/membership.jsonl",
        ".lab-output/sku-next-dev-coordinate-refids-20261013-v1/membership.jsonl",
    })
    artifact_groups["runtime"].update(
        (partition_dir / name).relative_to(ROOT).as_posix()
        for name in ("pair-partition-receipt.json", "dispatch-pair-ids.jsonl",
                     "literal-protected-pair-ids.jsonl", "literal-protected-no-dispatch.jsonl",
                     "partition-output-inventory.json"))
    artifact_groups["code"].update({
        "experiments/sku-matching/assemble_next_body_invariant_dev10_r9_v1.py",
        "experiments/sku-matching/grade_next_body_invariant_dev10_r9_v1.py",
        "experiments/sku-matching/sku_body_invariant_native_rakuten_source_v1.py",
        "experiments/sku-matching/build_scoped_html_blocks_v1.py",
        "experiments/sku-matching/sku_body_invariant_choice_v3.py",
        "experiments/sku-matching/sku_body_invariant_choice_v3.schema.json",
        "experiments/sku-matching/evaluate_dev_source_bound_whole_sku_gate_v1.py",
        "experiments/sku-matching/sku_directional_choice_nli_v1.py",
        "experiments/sku-matching/sku_literal_family_proof_v1.py",
        "experiments/sku-matching/route_mixed_sku_references_v1.py",
        "experiments/sku-matching/grade_final_test_subset_v1.py",
        "tests/test_lab_next_body_invariant_dev10_r9_assembly.py",
    })
    frozen = {}
    for rel in sorted(set().union(*artifact_groups.values())):
        path = safe_file(rel)
        frozen[rel] = pin(path)
    for rel, expected in model_output_files.items():
        full = f"{assembly_dir.relative_to(ROOT).as_posix()}/{rel}"
        if frozen[full] != expected:
            raise ValueError(f"assembly output changed during freeze: {rel}")
    prepared_manifest = read_json(PREPARED / "manifest.json")
    assembly_receipt_path = assembly_dir / "assembly-receipt.json"
    assembly_receipt = read_json(assembly_receipt_path)
    if assembly_receipt.get("pair_partition_receipt_sha256") != sha_file(
            partition_dir / "pair-partition-receipt.json"):
        raise ValueError("assembly receipt is not bound to the frozen root pre-model pair partition")
    output_obj = {
        "schema_version": "sku-next-body-invariant-dev10-r9-grading-freeze-v1",
        "freeze_status": "all_dev10_predictions_and_gates_frozen",
        "source_split": "dev", "independent_test": False, "production_adoption_allowed": False,
        "all_predictions_frozen": True, "all_gates_frozen": True,
        "labels_read": False, "test_labels_read": False, "root_go_for_grading": False,
        "membership_sha256": sha_file(COHORT / "membership.jsonl"),
        "pair_partition_dir": partition_dir.relative_to(ROOT).as_posix(),
        "pair_partition_receipt_sha256": sha_file(partition_dir / "pair-partition-receipt.json"),
        "selection_policy_sha256": sha_file(COHORT / "selection-policy.json"),
        "selected_case_count": len(members), "selected_product_count": len({m["au_product_id"] for m in members}),
        "row_count": len(rows), "task_count": len(tasks),
        "prepared_pair_count": prepared_manifest.get("prepared_pair_count"),
        "expected_dispatched_pair_count": partition_receipt["dispatch_pair_count"],
        "expected_dispatched_pair_ids_sha256": partition_receipt["dispatch_pair_ids_sha256"],
        "literal_protected_pair_count": partition_receipt["literal_protected_pair_count"],
        "literal_protected_pair_ids_sha256": partition_receipt["literal_protected_pair_ids_sha256"],
        "variants": {variant: f"{assembly_dir.relative_to(ROOT).as_posix()}/{variant}/case-decisions.jsonl"
                     for variant in sorted(VARIANTS)},
        "gate_files": sorted(f"{assembly_dir.relative_to(ROOT).as_posix()}/{variant}/{name}"
            for variant in VARIANTS for name in ("case-decisions.jsonl", "all-row-results.jsonl", "task-resolutions.jsonl")),
        "frozen_artifact_groups": {k: sorted(v) for k, v in artifact_groups.items()},
        "frozen_files": frozen,
        "grader_code_sha256": frozen["experiments/sku-matching/grade_next_body_invariant_dev10_r9_v1.py"]["sha256"],
        "assembly_code_sha256": frozen["experiments/sku-matching/assemble_next_body_invariant_dev10_r9_v1.py"]["sha256"],
        "source_validation_code_sha256": frozen["experiments/sku-matching/sku_body_invariant_choice_v3.py"]["sha256"],
        "native_rakuten_source_adapter_sha256": frozen["experiments/sku-matching/sku_body_invariant_native_rakuten_source_v1.py"]["sha256"],
        "native_html_block_builder_sha256": frozen["experiments/sku-matching/build_scoped_html_blocks_v1.py"]["sha256"],
        "score_helper_sha256": frozen["experiments/sku-matching/grade_final_test_subset_v1.py"]["sha256"],
        "source_citations_verified_only": True, "semantic_truth_verified": False,
        "classifier_outputs_changed_after_freeze": False, "scores_created": False,
        "labels_opened": False, "separate_root_go_required_before_labels": True,
    }
    output = Path(output).resolve()
    out_rel = output.relative_to(ROOT).as_posix()
    if not out_rel.startswith(".lab-output/") or output.exists():
        raise FileExistsError("freeze receipt output must be a new .lab-output file")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, output_obj)
    return output_obj


def validate_freeze(receipt_path: Path, membership_path: Path) -> tuple[dict, dict[str, str]]:
    receipt = read_json(receipt_path)
    partition_rel = receipt.get("pair_partition_dir")
    if not isinstance(partition_rel, str):
        raise ValueError("body DEV10 freeze lacks its pre-model source partition path")
    partition_receipt_path = safe_file(f"{partition_rel}/pair-partition-receipt.json")
    if (receipt.get("schema_version") != "sku-next-body-invariant-dev10-r9-grading-freeze-v1"
            or receipt.get("freeze_status") != "all_dev10_predictions_and_gates_frozen"
            or receipt.get("source_split") != "dev" or receipt.get("independent_test") is not False
            or receipt.get("labels_read") is not False or receipt.get("root_go_for_grading") is not False
            or receipt.get("selected_case_count") != CASE_COUNT
            or receipt.get("selected_product_count") != PRODUCT_COUNT
            or receipt.get("row_count") != ROW_COUNT or receipt.get("task_count") != TASK_COUNT
            or receipt.get("membership_sha256") != sha_file(membership_path)
            or receipt.get("membership_sha256") != sha_file(COHORT / "membership.jsonl")
            or receipt.get("pair_partition_receipt_sha256") != sha_file(partition_receipt_path)
            or set(receipt.get("variants", {})) != VARIANTS):
        raise ValueError("body DEV10 pre-grade freeze receipt is incomplete or inconsistent")
    frozen = receipt.get("frozen_files")
    if not isinstance(frozen, dict) or not frozen:
        raise ValueError("body DEV10 freeze has no complete artifact pin map")
    groups = receipt.get("frozen_artifact_groups")
    if (not isinstance(groups, dict) or set(groups) != {"model_outputs", "runtime", "proofs", "code"}
            or set().union(*(set(v) for v in groups.values())) != set(frozen)):
        raise ValueError("body DEV10 freeze artifact groups do not enumerate its complete pin map")
    for rel, expected in frozen.items():
        path = safe_file(rel)
        if pin(path) != expected:
            raise ValueError(f"body DEV10 frozen artifact changed: {rel}")
    partition_dir = partition_receipt_path.parent
    partition_receipt, partition = ASSEMBLER.validate_partition(partition_dir, PREPARED, PROOF)
    if (partition_receipt.get("dispatch_pair_count") != receipt.get("expected_dispatched_pair_count")
            or partition_receipt.get("dispatch_pair_ids_sha256") != receipt.get("expected_dispatched_pair_ids_sha256")
            or partition_receipt.get("literal_protected_pair_count") != receipt.get("literal_protected_pair_count")
            or partition_receipt.get("literal_protected_pair_ids_sha256") != receipt.get("literal_protected_pair_ids_sha256")):
        raise ValueError("body DEV10 freeze dispatch partition differs from its pre-model source receipt")
    variants = {}
    for name, rel in receipt["variants"].items():
        path = safe_file(rel)
        if frozen.get(rel) != pin(path):
            raise ValueError(f"body DEV10 variant prediction is not frozen: {name}")
        variants[name] = str(path)
    expected_gate_files = {
        f"{Path(receipt['variants'][variant]).parent.as_posix()}/{name}"
        for variant in VARIANTS for name in ("case-decisions.jsonl", "all-row-results.jsonl", "task-resolutions.jsonl")
    }
    if set(receipt.get("gate_files", [])) != expected_gate_files:
        raise ValueError("body DEV10 freeze does not name every exact whole-gate output")
    for rel in receipt.get("gate_files", []):
        path = safe_file(rel)
        if frozen.get(rel) != pin(path):
            raise ValueError(f"body DEV10 gate file is not frozen: {rel}")
    if (len(receipt.get("gate_files", [])) != len(VARIANTS) * 3
            or len(receipt.get("gate_files", [])) != len(set(receipt.get("gate_files", [])))):
        raise ValueError("body DEV10 freeze must pin exactly three gate outputs for each variant")
    return receipt, variants


def score_membership_for_archive(members: list[dict], cases: dict[str, dict]) -> list[dict]:
    """Adapt only archive routing metadata for the unchanged v2/v3 scorer."""
    output = []
    for member in members:
        case = cases.get(member.get("case_id"))
        source_version = case.get("source_version") if isinstance(case, dict) else None
        if source_version not in {"v2", "v3"}:
            raise ValueError("DEV10 selected source case lacks an exact archive version")
        # Preserve frozen DEV membership as dev; the archive parser uses split
        # solely to select the correct v2/v3 reference source.
        output.append({**member, "source_version": source_version, "split": source_version})
    if len(output) != len(members) or {x["case_id"] for x in output} != {x["case_id"] for x in members}:
        raise ValueError("archive-routing membership differs from frozen DEV10 membership")
    return output


def grade(root_go_path: Path, receipt_path: Path, membership_path: Path,
          output_path: Path, archive_v2: Path = ROOT / SCORE.V2_LABEL_ARCHIVE,
          archive_v3: Path = ROOT / SCORE.V3_LABEL_ARCHIVE) -> dict[str, Any]:
    """Only this function reads the exact selected private labels, after root GO."""
    go = read_json(root_go_path)
    if go.get("root_go_for_body_dev10_grading") is not True:
        raise PermissionError("grading requires explicit body DEV10 root GO")
    receipt, prediction_paths = validate_freeze(receipt_path, membership_path)
    if go.get("freeze_receipt_sha256") != sha_file(receipt_path):
        raise PermissionError("root GO does not bind the exact all-predictions/all-gates freeze")
    members = read_rows(membership_path)
    if len(members) != CASE_COUNT or len({m.get("case_id") for m in members}) != CASE_COUNT:
        raise ValueError("frozen body DEV10 membership must contain exactly ten unique cases")
    cases = {row["case_id"]: row for row in read_rows(COHORT / "case-plans.jsonl")}
    if len(cases) != CASE_COUNT or set(cases) != {m["case_id"] for m in members}:
        raise ValueError("frozen DEV10 case plans do not exactly cover selected membership")
    score_membership = score_membership_for_archive(members, cases)
    references = SCORE.read_label_archives(archive_v2, archive_v3, score_membership, COHORT / "row-plans.jsonl")
    if set(references) != {m["case_id"] for m in score_membership}:
        raise ValueError("selected exact ten body DEV references are incomplete")
    predictions = {variant: read_rows(Path(path)) for variant, path in prediction_paths.items()}
    output = {"schema_version": "sku-next-body-invariant-dev10-r9-grade-v1",
        "phase": "post-freeze-grade", "freeze_receipt_sha256": sha_file(receipt_path),
        "root_go_sha256": sha_file(root_go_path), "membership_sha256": sha_file(membership_path),
        "source_split": "dev", "independent_test": False,
        "exact_selected_case_count": CASE_COUNT, "exact_selected_product_count": PRODUCT_COUNT,
        "labels_read_after_root_go": True, "classifier_outputs_changed": False,
        "gates_changed": False, "production_adoption_allowed": False,
        "label_archive_sha256": {"v2": sha_file(archive_v2), "v3": sha_file(archive_v3)},
        "references_used": len(references), "variants": {
            name: SCORE.score_variant(score_membership, predictions[name], references)
            for name in sorted(VARIANTS)}}
    out = Path(output_path).resolve()
    if not out.is_relative_to(ROOT / ".lab-output") or out.exists():
        raise FileExistsError("grade output must be a new .lab-output file")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze", help="assemble pre-grade pins without labels")
    freeze.add_argument("--spec", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    score = sub.add_parser("grade", help="score after explicit root GO and freeze validation")
    score.add_argument("--root-go", type=Path, required=True)
    score.add_argument("--freeze", type=Path, required=True)
    score.add_argument("--membership", type=Path, default=COHORT / "membership.jsonl")
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--v2-label-archive", type=Path, default=ROOT / SCORE.V2_LABEL_ARCHIVE)
    score.add_argument("--v3-label-archive", type=Path, default=ROOT / SCORE.V3_LABEL_ARCHIVE)
    args = parser.parse_args()
    if args.command == "freeze":
        result = assemble_freeze(read_json(args.spec), args.output)
        print(json.dumps({"freeze_status": result["freeze_status"], "labels_read": False,
                          "root_go_for_grading": False}, ensure_ascii=False))
    else:
        result = grade(args.root_go, args.freeze, args.membership, args.output,
                       args.v2_label_archive, args.v3_label_archive)
        print(json.dumps({"phase": result["phase"], "labels_read_after_root_go": True,
                          "variant_names": sorted(result["variants"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
