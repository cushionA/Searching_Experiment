#!/usr/bin/env python3
"""Reproduce frozen SKU grading in a separate evaluation directory.

This script does not call an inference service. It is intentionally kept in the
prediction run so it can be audited after the review is frozen.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import time
from typing import Any
from zipfile import ZipFile


RUN_ID = "20261010T174640Z-luna-expanded50"
OLD_RUN_ID = "20261010T160225Z-luna-task-improvement"
INVENTORY_RUN_ID = "20261010T171543Z-labeled-sku-accuracy"
EXPECTED_GRADER_SHA256 = "de67259b5f81729e3c8e5916d7752c3d4936ff2483a21cbe03ee8ff6de817d7f"
LABEL_TAGS = ("legacy", "novel")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON from {path}: {exc}") from exc


def read_cases(path: Path) -> list[dict[str, Any]]:
    value = read_json(path)
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = value.get("cases", value.get("predictions"))
    else:
        rows = None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must be an array or contain a cases/predictions array")
    return rows


def case_id_set(rows: list[dict[str, Any]], label: str) -> set[str]:
    ids: set[str] = set()
    for index, row in enumerate(rows):
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{label}[{index}] has no case_id")
        if case_id in ids:
            raise ValueError(f"duplicate case_id in {label}: {case_id}")
        ids.add(case_id)
    return ids


def candidate_keys(rows: list[dict[str, Any]], label: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    units: dict[tuple[str, str], str] = {}
    for index, case in enumerate(rows):
        if not isinstance(case, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        case_id = case.get("case_id")
        sku = case.get("rakuten_sku_key")
        product = case.get("au_product_id")
        au_rows = case.get("au_rows")
        if not all(isinstance(x, str) and x for x in (case_id, sku, product)):
            raise ValueError(f"{label}[{index}] lacks case_id/rakuten_sku_key/au_product_id")
        if case_id in result:
            raise ValueError(f"duplicate input case_id: {case_id}")
        unit = (sku, product)
        if unit in units:
            raise ValueError(f"duplicate Rakuten SKU × fixed AU product: {unit} ({units[unit]}, {case_id})")
        units[unit] = case_id
        if not isinstance(au_rows, list):
            raise ValueError(f"{label} au_rows must be an array: {case_id}")
        keys: set[str] = set()
        for row in au_rows:
            if not isinstance(row, dict) or not isinstance(row.get("row_key"), str) or not row["row_key"]:
                raise ValueError(f"invalid AU row key in {case_id}")
            if row["row_key"] in keys:
                raise ValueError(f"duplicate AU row key in input case {case_id}: {row['row_key']}")
            keys.add(row["row_key"])
        result[case_id] = keys
    return result


def load_label_bytes(inventory: dict[str, Any], repo: Path) -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    raw_labels: dict[str, bytes] = {}
    records: dict[str, dict[str, Any]] = {}
    for item in inventory.get("labels", []):
        tag = item.get("tag")
        if tag not in LABEL_TAGS or tag in raw_labels:
            raise ValueError(f"unexpected/duplicate label tag: {tag!r}")
        archive = repo / "experiments/sku-matching/results" / item["archive"]
        if sha256(archive) != item["archive_sha256"]:
            raise ValueError(f"label archive SHA-256 mismatch: {archive}")
        member = item["label_member"]
        manifest_member = str(Path(member).parent / "manifest.json")
        with ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            if member not in names or manifest_member not in names:
                raise ValueError(f"missing label or manifest member in {archive}")
            label_data = bundle.read(member)
            manifest_data = bundle.read(manifest_member)
        if sha256_bytes(label_data) != item["label_sha256"]:
            raise ValueError(f"label member SHA-256 mismatch: {tag}")
        if sha256_bytes(manifest_data) != item["manifest_sha256"]:
            raise ValueError(f"label manifest SHA-256 mismatch: {tag}")
        manifest_obj = json.loads(manifest_data.decode("utf-8"))
        declared = item.get("manifest_declared_label_sha256")
        if declared is not None:
            # The legacy manifest stores output hashes under output_sha256,
            # keyed by the archive member basename (for example labels.jsonl).
            output_hashes = manifest_obj.get("output_sha256")
            member_hash = output_hashes.get(Path(member).name) if isinstance(output_hashes, dict) else None
            if member_hash != declared:
                raise ValueError(f"archive manifest does not declare expected label hash: {tag}")
        raw_labels[tag] = label_data
        records[tag] = {
            "archive": str(archive), "archive_sha256": item["archive_sha256"],
            "label_member": member, "label_sha256": item["label_sha256"],
            "manifest_member": manifest_member, "manifest_sha256": item["manifest_sha256"],
            "declared_label_sha256": declared,
        }
    if set(raw_labels) != set(LABEL_TAGS):
        raise ValueError("source inventory must contain legacy and novel labels")
    return raw_labels, records


def load_grader(path: Path, frozen: dict[str, Any]):
    grader_sha = sha256(path)
    expected = frozen.get("code_sha256", {}).get("evaluate_frozen_sku_accuracy.py")
    if expected != EXPECTED_GRADER_SHA256 or grader_sha != expected:
        raise ValueError("evaluate_frozen_sku_accuracy.py hash does not match frozen-protocol.json")
    spec = importlib.util.spec_from_file_location("frozen_sku_grader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import grader: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, grader_sha


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-dir", required=True, type=Path,
                        help="new directory outside this checkout; extracted labels and results are written here")
    args = parser.parse_args(argv)

    run_dir = Path(__file__).resolve().parent
    repo = run_dir.parents[3]
    eval_dir = args.evaluation_dir.expanduser().resolve()
    if eval_dir == repo or repo in eval_dir.parents:
        raise ValueError("--evaluation-dir must be outside the repository checkout")
    if eval_dir.exists():
        raise ValueError(f"--evaluation-dir must not already exist: {eval_dir}")

    inventory_path = repo / "experiments/sku-matching/results" / INVENTORY_RUN_ID / "source-inventory.json"
    frozen_path = run_dir / "frozen-protocol.json"
    selection_path = run_dir / "selection.json"
    novelty_audit_path = run_dir / "novelty-audit.json"
    manifest_path = run_dir / "manifest.json"
    new_inputs_path = run_dir / "inputs.json"
    raw_prediction_path = run_dir / "matching-stage-summary.json"
    final_prediction_path = run_dir / "reviewed-summary.json"
    old_root = repo / "experiments/sku-matching/results" / OLD_RUN_ID
    old_summary_path = old_root / "summary.json"
    old_input_paths = [old_root / "r02-reference-development/inputs.json",
                       old_root / "r03-reference-holdout/inputs.json"]
    required = [inventory_path, frozen_path, selection_path, novelty_audit_path, manifest_path, new_inputs_path,
                raw_prediction_path, final_prediction_path, old_summary_path, *old_input_paths]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("required frozen grading source missing: " + ", ".join(missing))

    inventory = read_json(inventory_path)
    frozen = read_json(frozen_path)
    selection = read_json(selection_path)
    novelty_audit = read_json(novelty_audit_path)
    run_manifest = read_json(manifest_path)
    grader_path = repo / "experiments/sku-matching/evaluate_frozen_sku_accuracy.py"
    grader, grader_sha = load_grader(grader_path, frozen)

    new_inputs = read_cases(new_inputs_path)
    old_inputs_parts = [read_cases(path) for path in old_input_paths]
    if [len(part) for part in old_inputs_parts] != [6, 12] or len(new_inputs) != 50:
        raise ValueError("expected 6 r02, 12 r03, and 50 new input cases")
    combined_inputs = [*old_inputs_parts[0], *old_inputs_parts[1], *new_inputs]
    candidates_all = candidate_keys(combined_inputs, "combined inputs")
    candidate_keys(new_inputs, "new inputs")
    new_ids = case_id_set(new_inputs, "new inputs")
    old_ids = case_id_set([*old_inputs_parts[0], *old_inputs_parts[1]], "old inputs")
    if new_ids & old_ids:
        raise ValueError("new input case IDs overlap r02/r03")
    selected_ids = selection.get("selected_case_ids")
    if not isinstance(selected_ids, list) or set(selected_ids) != new_ids or len(selected_ids) != 50:
        raise ValueError("selection case IDs do not match the 50 new inputs")
    joint_new_products = selection.get("new_au_products")
    if not isinstance(joint_new_products, list) or len(set(map(str, joint_new_products))) != 4:
        raise ValueError("selection must declare exactly four new AU product IDs")
    joint_new_products = {str(value) for value in joint_new_products}

    raw_predictions = read_cases(raw_prediction_path)
    final_predictions = read_cases(final_prediction_path)
    old_predictions = read_cases(old_summary_path)
    if len(raw_predictions) != 50 or case_id_set(raw_predictions, "raw predictions") != new_ids:
        raise ValueError("raw matching-stage predictions must cover exactly the 50 new inputs")
    if len(final_predictions) != 50 or case_id_set(final_predictions, "final predictions") != new_ids:
        raise ValueError("reviewed final predictions must cover exactly the 50 new inputs")
    if len(old_predictions) != 18 or case_id_set(old_predictions, "old final predictions") != old_ids:
        raise ValueError("old summary must contain exactly the 18 r02/r03 final predictions")

    new_products_by_id = {case["case_id"]: str(case["au_product_id"]) for case in new_inputs}
    old_products = {str(case["au_product_id"]) for case in [*old_inputs_parts[0], *old_inputs_parts[1]]}
    unseen_au_cases = {cid for cid, product in new_products_by_id.items() if product not in old_products}
    seen_au_cases = new_ids - unseen_au_cases
    joint_unseen_cases = {cid for cid, product in new_products_by_id.items() if product in joint_new_products}
    shared_sources_cases = new_ids - joint_unseen_cases
    if len({new_products_by_id[cid] for cid in unseen_au_cases}) != 14:
        raise ValueError("expected 14 AU products absent from the prior 18 inputs")
    if len(unseen_au_cases) != 23 or len(seen_au_cases) != 27:
        raise ValueError("expected 23 unseen-AU and 27 seen-AU SKU cases")
    if len({new_products_by_id[cid] for cid in joint_unseen_cases}) != 4 or len(joint_unseen_cases) != 6:
        raise ValueError("selection.new_au_products should map to 4 products and 6 SKU cases")
    if len(shared_sources_cases) != 44:
        raise ValueError("expected 44 SKU cases outside the joint-unseen product set")
    if (novelty_audit.get("actual_new_au_product_count") != 14
            or novelty_audit.get("actual_new_au_sku_cases") != 23
            or novelty_audit.get("seen_au_sku_cases") != 27
            or set(map(str, novelty_audit.get("actual_new_au_product_ids", [])))
            != {new_products_by_id[cid] for cid in unseen_au_cases}
            or set(map(str, novelty_audit.get("joint_unseen_au_and_rakuten_source_product_ids", [])))
            != joint_new_products):
        raise ValueError("novelty-audit.json does not agree with input-derived strata")

    label_payloads, label_records = load_label_bytes(inventory, repo)
    label_inventory_sha = sha256(inventory_path)
    labels: list[dict[str, Any]] = []
    for tag in LABEL_TAGS:
        labels.extend(json.loads(line) for line in label_payloads[tag].decode("utf-8").splitlines() if line.strip())
    if len(labels) != 1439:
        raise ValueError(f"expected 1,439 labels; got {len(labels)}")

    inputs_for_hash = [inventory_path, frozen_path, selection_path, novelty_audit_path, manifest_path, new_inputs_path,
                       raw_prediction_path, final_prediction_path, old_summary_path,
                       *old_input_paths, grader_path]
    before = {str(path): sha256(path) for path in inputs_for_hash}
    archive_paths = [repo / "experiments/sku-matching/results" / item["archive"] for item in inventory["labels"]]
    before.update({str(path): sha256(path) for path in archive_paths})
    if before[str(inventory_path)] != label_inventory_sha:
        raise ValueError("source-inventory.json changed while grading was prepared")

    raw_candidates = candidate_keys(new_inputs, "raw prediction candidates")
    final_candidates = candidate_keys(new_inputs, "final prediction candidates")
    old_candidates = candidate_keys([*old_inputs_parts[0], *old_inputs_parts[1]], "old prediction candidates")
    all_candidates = {**old_candidates, **final_candidates}
    comparisons: dict[str, dict[str, Any]] = {}
    comparisons["matching_stage_50"] = grader.score(raw_predictions, labels, raw_candidates)
    comparisons["new_final_50"] = grader.score(final_predictions, labels, final_candidates)
    comparisons["new_final_unseen_au"] = grader.score(
        [row for row in final_predictions if row["case_id"] in unseen_au_cases], labels, final_candidates)
    comparisons["new_final_seen_au"] = grader.score(
        [row for row in final_predictions if row["case_id"] in seen_au_cases], labels, final_candidates)
    comparisons["new_final_joint_unseen_sources"] = grader.score(
        [row for row in final_predictions if row["case_id"] in joint_unseen_cases], labels, final_candidates)
    comparisons["new_final_shared_sources"] = grader.score(
        [row for row in final_predictions if row["case_id"] in shared_sources_cases], labels, final_candidates)
    comparisons["cumulative_final_68"] = grader.score([*old_predictions, *final_predictions], labels, all_candidates)

    # Confirm immutable sources still match their pre-grading byte hashes.
    after = {str(path): sha256(path) for path in inputs_for_hash}
    after.update({str(path): sha256(path) for path in archive_paths})
    if before != after:
        raise ValueError("a label, prediction, input, or grading source changed during grading")

    # Only now create the explicitly external evaluation workspace and extract
    # the two original label files there.
    eval_dir.parent.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=False, exist_ok=False)
    label_dir = eval_dir / "labels"
    label_dir.mkdir()
    extracted_label_records: dict[str, dict[str, str]] = {}
    extracted_labels: list[dict[str, Any]] = []
    for tag in LABEL_TAGS:
        label_file = label_dir / f"{tag}-labels.jsonl"
        label_file.write_bytes(label_payloads[tag])
        extracted_label_records[tag] = {"path": str(label_file), "sha256": sha256(label_file)}
        extracted_labels.extend(json.loads(line) for line in label_file.read_text(encoding="utf-8").splitlines() if line.strip())
    if extracted_labels != labels:
        raise ValueError("extracted label files differ from verified archive members")

    tag_dir_names = {
        "matching_stage_50": "matching-stage-50",
        "new_final_50": "new-final-50",
        "new_final_unseen_au": "new-final-unseen-au",
        "new_final_seen_au": "new-final-seen-au",
        "new_final_joint_unseen_sources": "new-final-joint-unseen-sources",
        "new_final_shared_sources": "new-final-shared-sources",
        "cumulative_final_68": "cumulative-final-68",
    }
    metric_view: dict[str, Any] = {}
    for tag, scored in comparisons.items():
        tag_dir = eval_dir / tag_dir_names[tag]
        tag_dir.mkdir()
        summary = {
            "tag": tag,
            "metrics": scored["metrics"],
            "case_count": len(scored["cases"]),
            "dataset_label_count": len(labels),
            "new_inference_calls_for_grading": 0,
            "human_verified": False,
            "elapsedSeconds": round(time.monotonic() - started, 6),
        }
        (tag_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with (tag_dir / "case-results.jsonl").open("w", encoding="utf-8") as stream:
            for row in scored["cases"]:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        metric_view[tag] = scored["metrics"]
    comparisons_path = eval_dir / "comparisons.json"
    comparisons_path.write_text(json.dumps({
        "metrics": metric_view,
        "case_counts": {tag: len(value["cases"]) for tag, value in comparisons.items()},
        "strata": {
            "au_unseen_against_prior18_product_ids": sorted({new_products_by_id[cid] for cid in unseen_au_cases}),
            "au_unseen_against_prior18_product_count": len({new_products_by_id[cid] for cid in unseen_au_cases}),
            "au_unseen_against_prior18_case_count": len(unseen_au_cases),
            "seen_au_case_count": len(seen_au_cases),
            "joint_unseen_au_and_rakuten_source_product_ids": sorted(joint_new_products),
            "joint_unseen_product_count": len(joint_new_products),
            "joint_unseen_sku_case_count": len(joint_unseen_cases),
            "shared_sources_case_count": len(shared_sources_cases),
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    source_manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_dir": str(eval_dir),
        "source_inventory": {"path": str(inventory_path), "sha256": label_inventory_sha},
        "labels": label_records,
        "extracted_labels": extracted_label_records,
        "frozen_prediction_inputs": {
            str(path): before[str(path)] for path in [frozen_path, selection_path, manifest_path,
                novelty_audit_path, new_inputs_path, raw_prediction_path, final_prediction_path, old_summary_path, *old_input_paths]
        },
        "related_code": {
            str(grader_path): grader_sha,
            str(Path(__file__).resolve()): sha256(Path(__file__).resolve()),
        },
        "all_source_sha256_pre": before,
        "all_source_sha256_post": after,
        "label_sha256_pre_post_unchanged": True,
        "new_inference_calls_for_grading": 0,
        "human_verified": False,
        "dataset_label_count": len(labels),
        "new_prediction_count": len(new_ids),
        "cumulative_prediction_count": len(old_ids | new_ids),
        "cumulative_old_prediction_count": len(old_ids),
        "elapsedSeconds": round(time.monotonic() - started, 6),
    }
    (eval_dir / "grading-source-manifest.json").write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    saved_comparisons = run_dir / "grading/comparisons.json"
    if saved_comparisons.is_file():
        saved = read_json(saved_comparisons)
        saved_metrics = saved.get("metrics", saved.get("comparisons")) if isinstance(saved, dict) else None
        if saved_metrics != metric_view:
            raise ValueError("saved run grading/comparisons.json metrics do not reproduce")
        for tag, dirname in tag_dir_names.items():
            saved_cases_path = saved_comparisons.parent / dirname / "case-results.jsonl"
            if not saved_cases_path.is_file():
                raise ValueError(f"saved comparison lacks per-case results: {saved_cases_path}")
            expected_rows = comparisons[tag]["cases"]
            saved_rows = [json.loads(line) for line in saved_cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if saved_rows != expected_rows:
                raise ValueError(f"saved run case results do not reproduce for {tag}")
        (eval_dir / "reproduction-check.json").write_text(json.dumps({
            "saved_run_comparisons": str(saved_comparisons),
            "metrics_match": True,
            "all_case_results_match": True,
        }, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
