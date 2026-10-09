#!/usr/bin/env python3
"""Prepare compact and full-evidence ZIPs for the frozen real Luna SKU evaluation.

This utility uses a narrow, explicit allowlist. It never crawls input trees and
refuses synthetic provenance, incomplete labels/audit/evaluations, changed source
files, and existing output artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v2"
LABELS_DIR = ROOT / ".lab-output/sku-real-luna-labels-20261010-v1"
AUDIT_DIR = ROOT / ".lab-output/sku-real-luna-label-audit-20261010-final-v1"
INDEPENDENT_REVIEW = ROOT / ".lab-output/sku-real-luna-label-audit-20261010-independent-review-v3.json"
MODEL_DIR = ROOT / ".lab-output/sku-real-luna-model-evaluation-20261010-v1"
DECIDE_DIR = ROOT / ".lab-output/sku-real-luna-decide-evaluation-20261010-v1"
DECIDE_EXCLUSIVE_DIR = DECIDE_DIR / "newexclusive"
AU_SOURCE_DIR = ROOT / ".lab-output/sku-observed-product-pairs-20261010-final-v4"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-real-luna-delivery-20261010-v1"

SHARDS = ("shard-1", "shard-2", "shard-3")
DECISIONS = {"matched", "unmatched", "review"}
MODELS = ("minilm", "ruri", "bekko", "granite")
MODEL_OUTPUT_NAMES = (
    "summary.json", "predictions-title-sku.json", "predictions-sku.json",
    "eligibility-title-sku.json", "eligibility-sku.json",
)
INPUT_ROOT_FILES = (
    "TASK_SPEC.txt", "au_eligibility.jsonl", "cases.jsonl", "dossier_index.json",
    "eligibility.jsonl", "group_manifest.json", "manifest.json",
)
ACTIVE_LABEL_NAMES = (
    "labels.jsonl", "manifest.json",
    "shard-1-labels.jsonl", "shard-1-review.json",
    "shard-2-labels.jsonl", "shard-2-review.json",
    "shard-3-labels.jsonl", "shard-3-review.json", "shard-2-audit-corrections.jsonl",
)
CODE_FILES = (
    "annotate_luna_curtains.py", "annotate_luna_blankets.py", "annotate_luna_other.py",
    "build_luna_annotation_inputs.py", "audit_luna_sku_labels.py",
    "evaluate_luna_real_skus.py", "evaluate_luna_decide.py",
    "package_luna_real_evaluation.py", "merge_luna_labels.py", "embeddings.py", "backend_ruri.py",
    "backend_bekko.py", "backend_granite.py", "backend_gliner_extract.py",
    "evaluate_luna_decide.py", "try_gliner.py", "summarize_luna_real_models.py",
    "fetch_rakuten.py", "requirements-runtime.txt", "candidate-runtime.txt",
    "export_luna_review_data.py",
    "model-manifest.json", "manifests/ruri.json", "manifests/bekko.json",
    "manifests/granite.json", "manifests/gliner-extract.json",
)
RAW_ROOTS = (".lab-output/sku-real-au-20261010/", ".lab-output/sku-real-au-20261010-expanded/",
             ".lab-output/sku-real-rakuten-20261010/")
SYNTHETIC_PARTS = {"sku-synthetic", "sku-fixed-product-20261010"}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError(f"{path}:{line_no}: expected a JSON object")
                rows.append(item)
    return rows


def has_synthetic_path(path: Path) -> bool:
    return any(part.casefold() in SYNTHETIC_PARTS or part.casefold().startswith("sku-synthetic")
               or part.casefold().startswith("sku-fixed-product-") for part in path.parts)


def reject_synthetic_markers(value, context: str) -> None:
    """Reject affirmative synthetic markers in structured real-data payloads."""
    if isinstance(value, dict):
        for key, child in value.items():
            key_norm = str(key).casefold()
            if key_norm in {"synthetic", "is_synthetic", "synthetic_data_included"} and child not in (False, None, 0, ""):
                raise ValueError(f"synthetic marker set in {context}: {key}")
            if key_norm in {"synthetic_rows", "synthetic_data_rows", "synthetic_input_count"} and child not in (None, 0, ""):
                raise ValueError(f"synthetic count is nonzero in {context}: {key}")
            if key_norm in {"record_kind", "data_origin", "source_data_origin", "sample_type"}:
                if isinstance(child, str) and "synthetic" in child.casefold():
                    raise ValueError(f"synthetic provenance in {context}: {key}")
            if key_norm in {"path", "file", "raw_file", "source_file", "input_dir"} and isinstance(child, str):
                if has_synthetic_path(Path(child)):
                    raise ValueError(f"synthetic source path in {context}: {child}")
            reject_synthetic_markers(child, context)
    elif isinstance(value, list):
        for child in value:
            reject_synthetic_markers(child, context)


def validate_structured_payload(path: Path) -> None:
    if path.suffix == ".json":
        reject_synthetic_markers(read_json(path), str(path))
    elif path.suffix == ".jsonl":
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                reject_synthetic_markers(json.loads(line), f"{path}:{line_no}")


def add_payload(payloads: dict[str, Path], source: Path, archive_name: str | None = None) -> None:
    """Add one explicit repository file, rejecting symlinks and path escapes."""
    source = source if source.is_absolute() else ROOT / source
    source = source.absolute()
    if not source.is_relative_to(ROOT) or not source.is_file():
        raise ValueError(f"Missing, symlinked, or out-of-repository source: {source}")
    current = source
    while current != ROOT:
        if current.is_symlink():
            raise ValueError(f"Symlinked source cannot be packaged: {current}")
        current = current.parent
    source = source.resolve(strict=True)
    if not source.is_relative_to(ROOT) or not source.is_file():
        raise ValueError(f"Resolved source escapes repository or is missing: {source}")
    if has_synthetic_path(source.relative_to(ROOT)):
        raise ValueError(f"Synthetic path cannot be packaged: {source}")
    member = archive_name or source.relative_to(ROOT).as_posix()
    if member in payloads and payloads[member] != source:
        raise ValueError(f"Archive member collision: {member}")
    payloads[member] = source


def active_label_paths(labels_dir: Path = LABELS_DIR) -> list[Path]:
    """Return only canonical merged labels and the three active shard outputs."""
    return [labels_dir / name for name in ACTIVE_LABEL_NAMES if (labels_dir / name).is_file()]


def case_metrics(cases: list[dict]) -> dict:
    case_ids = [str(row.get("case_id", "")) for row in cases]
    raku_keys = [str(row.get("rakuten", {}).get("source", {}).get("sku_record_key", "")) for row in cases]
    pair_ids = set()
    group_ids = set()
    urls = set()
    for row in cases:
        group_ids.add(str(row.get("group_id", "")))
        source = row.get("rakuten", {}).get("source", {})
        urls.add(str(source.get("url", "")))
        pair_ids.add(str(row.get("dossier_id", "")))
    unique_sku_count = len(set(raku_keys))
    return {
        "case_count": len(cases),
        "unique_case_id_count": len(set(case_ids)),
        "product_pair_dossier_count": len(pair_ids),
        "unique_rakuten_sku_record_count": unique_sku_count,
        "rakuten_sku_case_references": len(raku_keys),
        "rakuten_sku_references_repeated_across_cases": len(raku_keys) - unique_sku_count,
        "rakuten_url_count": len(urls),
        "family_group_count": len(group_ids),
    }


def snapshot_payloads(payloads: dict[str, Path]) -> dict[str, dict]:
    return {member: {"bytes": source.stat().st_size, "sha256": sha256_file(source)}
            for member, source in sorted(payloads.items())}


def assert_payloads_unchanged(payloads: dict[str, Path], snapshot: dict[str, dict]) -> None:
    current = snapshot_payloads(payloads)
    if current != snapshot:
        changed = sorted(name for name in set(current) | set(snapshot) if current.get(name) != snapshot.get(name))
        raise RuntimeError(f"Pack source changed during package preparation: {changed[:10]}")


def checked_add(payloads: dict[str, Path], path: Path) -> None:
    add_payload(payloads, path)
    validate_structured_payload(payloads[path.relative_to(ROOT).as_posix()])


def collect_input_payloads(inputs_dir: Path = INPUTS) -> tuple[dict[str, Path], dict, list[dict]]:
    if inputs_dir.resolve() != INPUTS.resolve():
        raise ValueError("The frozen v2 annotation input path is fixed and cannot be overridden")
    manifest_path = inputs_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("blindness", {}).get("synthetic_data_included") is not False:
        raise ValueError("Annotation input manifest does not attest real-only data")
    files = {name for name in INPUT_ROOT_FILES}
    files.update(manifest.get("dossier_sha256", {}).keys())
    for shard in SHARDS:
        files.update({f"{shard}/cases.jsonl", f"{shard}/manifest.json"})
    expected_input_names = {p.relative_to(inputs_dir).as_posix() for p in inputs_dir.rglob("*")
                            if p.is_file() and p.name != "TASK_SPEC.txt"}
    # The input delivery directory has only its fixed data files plus TASK_SPEC.
    unrecognized = expected_input_names - files
    missing = files - expected_input_names - {"TASK_SPEC.txt"}
    if unrecognized:
        raise ValueError(f"Unexpected files under frozen annotation inputs: {sorted(unrecognized)[:10]}")
    if missing:
        raise FileNotFoundError(f"Required frozen annotation input files are missing: {sorted(missing)[:10]}")
    payloads = {}
    for relative in sorted(files):
        path = inputs_dir / relative
        checked_add(payloads, path)
    cases = read_jsonl(inputs_dir / "cases.jsonl")
    metrics = case_metrics(cases)
    if metrics != {"case_count": 1383, "unique_case_id_count": 1383,
                   "product_pair_dossier_count": 29, "unique_rakuten_sku_record_count": 644,
                   "rakuten_sku_case_references": 1383,
                   "rakuten_sku_references_repeated_across_cases": 739,
                   "rakuten_url_count": 17, "family_group_count": 12}:
        raise ValueError(f"Frozen input counts differ from expected real SKU set: {metrics}")
    dossier_index = read_json(inputs_dir / "dossier_index.json")
    dossier_refs = {row.get("dossier_id") for row in cases}
    if (len(dossier_index) != 29 or len(manifest.get("dossier_sha256", {})) != 29
            or dossier_refs != {Path(value).stem for value in dossier_index.values()}):
        raise ValueError("Frozen case-to-dossier references do not resolve to all 29 dossiers")
    if manifest.get("case_count") != metrics["case_count"] or manifest.get("group_count") != metrics["family_group_count"]:
        raise ValueError("Frozen annotation manifest counts do not match case records")
    if manifest.get("raw_source_pre_post_sha256", {}).get("unchanged") is not True:
        raise ValueError("Frozen annotation source raw pre/post hashes do not agree")
    if manifest.get("source_inputs_pre_post_sha256", {}).get("unchanged") is not True:
        raise ValueError("Frozen annotation source table pre/post hashes do not agree")
    for name, expected in manifest.get("output_sha256", {}).items():
        path = inputs_dir / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Frozen annotation input output digest mismatch: {name}")
    for name, expected in manifest.get("dossier_sha256", {}).items():
        path = inputs_dir / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Frozen dossier digest mismatch: {name}")
    return payloads, manifest, cases


def collect_labels(cases: list[dict], inputs_manifest: dict, labels_dir: Path = LABELS_DIR) -> tuple[dict[str, Path], dict, dict]:
    if labels_dir.resolve() != LABELS_DIR.resolve():
        raise ValueError("The active labels directory is fixed and cannot be overridden")
    canonical_files = active_label_paths(labels_dir)
    missing = sorted(set(ACTIVE_LABEL_NAMES) - {p.name for p in canonical_files})
    if missing:
        raise FileNotFoundError(f"Active final label outputs are not complete yet: {missing}")
    payloads = {}
    for path in canonical_files:
        checked_add(payloads, path)
    labels_path = labels_dir / "labels.jsonl"
    labels = read_jsonl(labels_path)
    label_by_id = {}
    counts = Counter()
    for label in labels:
        case_id = label.get("case_id")
        if not isinstance(case_id, str) or case_id in label_by_id:
            raise ValueError(f"Missing or duplicate active label case_id: {case_id!r}")
        decision = label.get("decision", label.get("semantic_label"))
        if decision not in DECISIONS:
            raise ValueError(f"Invalid active decision for {case_id}: {decision!r}")
        keys = label.get("matching_au_row_keys", [])
        if (decision == "matched" and not keys) or (decision != "matched" and keys):
            raise ValueError(f"Invalid AU row-key mapping for decision {decision}: {case_id}")
        label_by_id[case_id] = label
        counts[decision] += 1
    case_ids = {row["case_id"] for row in cases}
    if set(label_by_id) != case_ids or len(labels) != len(cases):
        raise ValueError("Active labels do not cover the frozen case IDs exactly")

    # Canonical labels must be an exact merge of only the three current shard files.
    shard_labels = []
    for shard in SHARDS:
        shard_rows = read_jsonl(labels_dir / f"{shard}-labels.jsonl")
        shard_case_ids = {row["case_id"] for row in shard_rows}
        expected_ids = {row["case_id"] for row in cases if row.get("shard_id") == shard}
        if shard_case_ids != expected_ids or len(shard_rows) != len(expected_ids):
            raise ValueError(f"Active {shard} labels do not match frozen shard membership")
        shard_labels.extend(shard_rows)
    if {r["case_id"] for r in shard_labels} != case_ids or len(shard_labels) != len(labels):
        raise ValueError("Canonical label file is not a merge of the three active shard outputs")
    if any(label_by_id[row["case_id"]] != row for row in shard_labels):
        raise ValueError("Canonical label rows differ from active shard label rows")

    label_manifest = read_json(labels_dir / "manifest.json")
    if label_manifest.get("schema_version") != "luna-sku-label-manifest-v1":
        raise ValueError("Unexpected final labels manifest schema")
    protocol = label_manifest.get("annotation_protocol", {})
    if (protocol.get("model") != "gpt-6-luna" or protocol.get("human_verified") is not False
            or protocol.get("existing_model_predictions_used") is not False
            or protocol.get("synthetic_data_included") is not False):
        raise ValueError("Labels must remain machine-annotated and synthetic-free")
    if label_manifest.get("case_count") != len(labels):
        raise ValueError("Labels manifest case_count does not equal canonical labels rows")
    if (label_manifest.get("unique_rakuten_source_skus") != 644
            or label_manifest.get("family_group_count") != 12):
        raise ValueError("Labels manifest unique-SKU or family counts are inconsistent")
    declared_counts = label_manifest.get("decision_counts", label_manifest.get("label_counts"))
    if declared_counts is not None and any(declared_counts.get(key, 0) != counts.get(key, 0)
                                           for key in DECISIONS):
        raise ValueError("Labels manifest decision counts differ from active labels.jsonl")
    expected_input_sha = sha256_file(INPUTS / "manifest.json")
    if label_manifest.get("input_manifest_sha256") != expected_input_sha:
        raise ValueError("Labels manifest does not pin the v2 input manifest")
    declared = label_manifest.get("output_sha256", {})
    for path in canonical_files:
        if path.name == "manifest.json":
            continue
        expected = declared.get(path.name)
        if not expected or expected != sha256_file(path):
            raise ValueError(f"Labels manifest digest missing/mismatched for {path.name}")
    return payloads, label_manifest, {"row_count": len(labels), "decision_counts": dict(counts)}


def collect_audit(inputs_manifest: dict, label_manifest: dict, audit_dir: Path = AUDIT_DIR) -> tuple[dict[str, Path], dict]:
    audit_path = audit_dir / "audit.json"
    payloads = {}
    checked_add(payloads, audit_path)
    checked_add(payloads, INDEPENDENT_REVIEW)
    audit = read_json(audit_path)
    if audit.get("record_kind") != "LUNA_MACHINE_ANNOTATION_AUDIT":
        raise ValueError("Final annotation audit record kind is unexpected")
    if audit.get("errors"):
        raise ValueError(f"Final annotation audit reports errors: {audit['errors'][:3]}")
    if audit.get("human_gold") is not False or audit.get("human_reviewed") is not False:
        raise ValueError("Machine labels must not be represented as human-verified gold")
    if audit.get("case_count") != 1383 or audit.get("label_count") != 1383:
        raise ValueError("Final audit counts do not cover all 1383 case labels")
    if audit.get("input_manifest_sha256") != sha256_file(INPUTS / "manifest.json"):
        raise ValueError("Final audit does not attest the frozen v2 annotation manifest")
    independent = read_json(INDEPENDENT_REVIEW)
    audit_independent = audit.get("independent_second_review", {})
    if (audit.get("independent_second_review_sha256") != sha256_file(INDEPENDENT_REVIEW)
            or {k: v for k, v in audit_independent.items() if not k.startswith("checked_")} != independent
            or independent.get("status") != "completed"
            or independent.get("human_reviewed") is not False
            or independent.get("human_gold") is not False):
        raise ValueError("Standalone independent source review does not match final audit attestation")
    if (len(independent.get("reviewed_case_ids", [])) != 56
            or len(independent.get("reviewed_pair_refs", [])) != 29
            or len(independent.get("reviewed_group_ids", [])) != 12):
        raise ValueError("Independent source-review counts do not match the final audited scope")
    if (audit_independent.get("checked_case_count") != 56
            or audit_independent.get("checked_product_pair_count") != 29
            or audit_independent.get("checked_family_count") != 12
            or audit_independent.get("checked_rakuten_url_count") != 17):
        raise ValueError("Final audit report disagrees with independent-review coverage counts")
    if audit.get("label_files_sha256", {}).get(str(LABELS_DIR / "labels.jsonl")) != sha256_file(LABELS_DIR / "labels.jsonl"):
        raise ValueError("Final audit does not attest active merged labels.jsonl")
    integrity = audit.get("raw_integrity", {})
    if integrity.get("missing") or integrity.get("sha256_mismatch") or integrity.get("checked", 0) < 46:
        raise ValueError(f"Final audit raw source integrity is incomplete: {integrity}")
    return payloads, {"report": audit,
                      "independent_review_counts": {"case_reads": len(independent["reviewed_case_ids"]),
                                                    "pair_reads": len(independent["reviewed_pair_refs"]),
                                                    "family_group_reads": len(independent["reviewed_group_ids"]),
                                                    "rakuten_url_reads": len(independent["reviewed_family_urls"]),
                                                    "review_status": independent["status"]}}


def collect_model_outputs() -> tuple[dict[str, Path], dict[str, dict]]:
    payloads = {}
    summaries = {}
    model_code = {
        "minilm": ("embeddings.py", "model-manifest.json"),
        "ruri": ("backend_ruri.py", "manifests/ruri.json"),
        "bekko": ("backend_bekko.py", "manifests/bekko.json"),
        "granite": ("backend_granite.py", "manifests/granite.json"),
    }
    for model in MODELS:
        model_root = MODEL_DIR / model
        selected = [model_root / name for name in MODEL_OUTPUT_NAMES]
        for path in selected:
            checked_add(payloads, path)
        summary = read_json(model_root / "summary.json")
        if summary.get("model") != model:
            raise ValueError(f"Model result directory {model} contains model={summary.get('model')!r}")
        if summary.get("case_count") != 1383:
            raise ValueError(f"Model {model} did not score all 1383 cases")
        if summary.get("unique_rakuten_sku_references") != 644:
            raise ValueError(f"Model {model} unique SKU count differs from the 644-source-SKU input")
        if summary.get("rakuten_sku_reference_count") != 1383:
            raise ValueError(f"Model {model} did not retain 1383 case-level SKU references")
        if "machine annotation" not in str(summary.get("label_source", "")).casefold():
            raise ValueError(f"Model {model} does not preserve machine annotation status")
        backend_file, manifest_file = model_code[model]
        expected_code = {
            "evaluate_luna_real_skus.py": sha256_file(ROOT / "experiments/sku-matching/evaluate_luna_real_skus.py"),
            backend_file: sha256_file(ROOT / "experiments/sku-matching" / backend_file),
        }
        if summary.get("code_sha256") != expected_code:
            raise ValueError(f"Model {model} code SHA does not match the current evaluator/backend sources")
        expected_manifest_sha = sha256_file(ROOT / "experiments/sku-matching" / manifest_file)
        if summary.get("model_manifest_sha256") != expected_manifest_sha:
            raise ValueError(f"Model {model} manifest SHA does not match the current model manifest")
        summaries[model] = summary
    return payloads, summaries


def collect_decide_outputs(decide_dir: Path | None) -> tuple[dict[str, Path], dict | None]:
    if decide_dir is None:
        return {}, None
    decide_dir = decide_dir.resolve()
    if decide_dir != DECIDE_EXCLUSIVE_DIR.resolve():
        raise ValueError("--decide-dir must point to the frozen newexclusive diagnostic directory")
    summary_path = decide_dir / "summary.json"
    payloads = {}
    checked_add(payloads, summary_path)
    summary = read_json(summary_path)
    if summary.get("label_gold_status") != "human_unreviewed" or summary.get("synthetic_input_count") != 0:
        raise ValueError("Optional decide-evaluation result has an unexpected label/synthetic status")
    if summary.get("sampling", {}).get("source_case_count") != 1383:
        raise ValueError("Optional decide-evaluation source case count is not 1383")
    expected_code = {
        "evaluate_luna_decide.py": sha256_file(ROOT / "experiments/sku-matching/evaluate_luna_decide.py"),
        "try_gliner.py": sha256_file(ROOT / "experiments/sku-matching/try_gliner.py"),
    }
    if summary.get("code_sha256") != expected_code:
        raise ValueError("Decide diagnostic code SHA does not match current source files")
    if summary.get("sampling", {}).get("selected_case_count") != 166:
        raise ValueError("Decide diagnostic does not contain the frozen 166-case sample")
    source_manifest_bytes = (json.dumps(summary.get("source_manifest"), indent=2) + "\n").encode("utf-8")
    source_manifest_sha = sha256_bytes(source_manifest_bytes)
    if (summary.get("source_manifest_sha256") != source_manifest_sha
            or summary.get("model_weight_sha256", {}).get("source-manifest.json") != source_manifest_sha):
        raise ValueError("Decide diagnostic source-manifest digest is inconsistent")
    pinned_file_hashes = {name: item.get("sha256")
                          for name, item in summary.get("model_files_pinned_sha256", {}).items()}
    expected_weight_hashes = {**pinned_file_hashes, "source-manifest.json": source_manifest_sha}
    if summary.get("model_weight_sha256") != expected_weight_hashes:
        raise ValueError("Decide diagnostic pinned model-file hashes are inconsistent")
    dossier_index = read_json(INPUTS / "dossier_index.json")
    expected_inputs = {
        "cases": INPUTS / "cases.jsonl",
        "labels": LABELS_DIR / "labels.jsonl",
        "labels_manifest": LABELS_DIR / "manifest.json",
        "ruri_predictions": MODEL_DIR / "ruri/predictions-sku.json",
        "au_product_sku_arrays": AU_SOURCE_DIR / "au_product_sku_arrays.jsonl",
    }
    actual_input_hashes = {key: sha256_file(path) for key, path in expected_inputs.items()}
    actual_input_hashes["dossiers"] = {
        Path(name).name: sha256_file(INPUTS / name) for name in dossier_index.values()
    }
    for key, actual in actual_input_hashes.items():
        if summary.get("input_sha256", {}).get(key) != actual:
            raise ValueError(f"Decide diagnostic frozen input SHA mismatch: {key}")
    return payloads, summary


def collect_derived_model_summaries() -> tuple[dict[str, Path], dict]:
    """Include the frozen combined report and curtain-specific diagnostic."""
    paths = {
        "comparison": MODEL_DIR / "comparison.json",
        "curtain_only": MODEL_DIR / "curtain-only.json",
    }
    payloads = {}
    for path in paths.values():
        checked_add(payloads, path)
    comparison = read_json(paths["comparison"])
    if (comparison.get("case_count") != 1383 or comparison.get("unique_rakuten_skus") != 644
            or comparison.get("family_groups") != 12 or comparison.get("synthetic_data_count") != 0):
        raise ValueError("Combined model comparison does not describe the frozen real dataset")
    for relative, expected in comparison.get("source_sha256", {}).items():
        source = ROOT / relative
        if not source.is_file() or source.is_symlink() or sha256_file(source) != expected:
            raise ValueError(f"Combined model comparison source SHA mismatch: {relative}")
    curtain = read_json(paths["curtain_only"])
    if (curtain.get("case_count") != 720 or curtain.get("gold_matched") != 360
            or curtain.get("gold_unmatched") != 360
            or curtain.get("metrics", {}).get("bekko", {}).get("sku", {}).get(
                "gold_matched_top1_row_accuracy_before_threshold") != 1.0
            or curtain.get("metrics", {}).get("bekko", {}).get("sku", {}).get("accepted_count") != 0):
        raise ValueError("Curtain-only analysis does not match the frozen 720-case result")
    return payloads, {"comparison": comparison, "curtain_only": curtain}


def collect_raw_evidence(cases: list[dict], manifest: dict, inputs_dir: Path = INPUTS,
                         au_source_dir: Path = AU_SOURCE_DIR) -> tuple[dict[str, Path], dict]:
    arrays_path = au_source_dir / "au_product_sku_arrays.jsonl"
    arrays_manifest_path = au_source_dir / "manifest.json"
    arrays = read_jsonl(arrays_path)
    arrays_manifest = read_json(arrays_manifest_path)
    expected_arrays_sha = arrays_manifest.get("outputs", {}).get(arrays_path.name)
    if (arrays_manifest.get("synthetic_data_included") is not False
            or expected_arrays_sha != sha256_file(arrays_path)):
        raise ValueError("Original AU SKU array manifest is missing, synthetic, or has a digest mismatch")
    arrays_by_id = {str(row["au_product_id"]): row for row in arrays}
    dossier_index = read_json(inputs_dir / "dossier_index.json")
    dossier_paths = [inputs_dir / path for path in dossier_index.values()]
    dossier_by_product = {}
    dossier_rakuten_raw = set()
    for path in dossier_paths:
        dossier = read_json(path)
        product_id = str(dossier["au_product"]["product_id"])
        dossier_by_product[product_id] = dossier
        source = dossier["rakuten_product"]["source"]
        dossier_rakuten_raw.add((source["raw_file"], source["sha256"], source["url"]))
    if len(dossier_by_product) != 29 or len(dossier_rakuten_raw) != 17:
        raise ValueError("Source dossier references do not contain 29 AU products and 17 Rakuten pages")

    payloads = {}
    # The original source array and its manifest are required by the evaluator's provenance checks.
    checked_add(payloads, arrays_path)
    checked_add(payloads, arrays_manifest_path)
    raw_au_items, raw_au_options = set(), set()
    for product_id in sorted(dossier_by_product):
        array = arrays_by_id.get(product_id)
        if array is None:
            raise ValueError(f"Original AU array is missing product {product_id}")
        provenance = array.get("provenance", {})
        item_ref = provenance.get("raw_item_json", {})
        item_file = item_ref.get("file")
        item_sha = item_ref.get("sha256") or provenance.get("item_api", {}).get("sha256")
        if not item_file or not item_sha:
            raise ValueError(f"AU raw item reference is missing for product {product_id}")
        options_path = Path(item_file).with_name(f"{product_id}-options.json")
        options_sha = provenance.get("options_api", {}).get("sha256")
        if not options_sha:
            raise ValueError(f"AU purchase-options SHA is missing for product {product_id}")
        for relative, expected_sha, kind in ((item_file, item_sha, "item"),
                                             (options_path.as_posix(), options_sha, "options")):
            if not relative.startswith(RAW_ROOTS[:2]):
                raise ValueError(f"AU raw source outside explicit capture roots: {relative}")
            source = ROOT / relative
            if not source.is_file() or source.is_symlink():
                raise FileNotFoundError(f"Required AU {kind} evidence is missing: {relative}")
            if sha256_file(source) != expected_sha:
                raise ValueError(f"AU {kind} evidence SHA mismatch: {relative}")
            add_payload(payloads, source)
            (raw_au_items if kind == "item" else raw_au_options).add(relative)
    raw_rakuten = set()
    for relative, expected_sha, url in sorted(dossier_rakuten_raw):
        if not relative.startswith(RAW_ROOTS[2]):
            raise ValueError(f"Rakuten raw source outside explicit capture root: {relative}")
        source = ROOT / relative
        if not source.is_file() or source.is_symlink() or sha256_file(source) != expected_sha:
            raise ValueError(f"Required Rakuten raw page is missing or changed: {relative}")
        add_payload(payloads, source)
        raw_rakuten.add(relative)
    if len(raw_au_items) != 29 or len(raw_au_options) != 29 or len(raw_rakuten) != 17:
        raise ValueError("Full evidence source refs do not resolve to expected raw counts")
    evidence = {"au_item_api_raw_file_count": len(raw_au_items),
                "au_purchase_options_raw_file_count": len(raw_au_options),
                "rakuten_raw_html_count": len(raw_rakuten),
                "raw_paths": sorted(raw_au_items | raw_au_options | raw_rakuten)}
    return payloads, evidence


def collect_code_payloads() -> dict[str, Path]:
    code_dir = ROOT / "experiments/sku-matching"
    payloads = {}
    for relative in CODE_FILES:
        checked_add(payloads, code_dir / relative)
    return payloads


def validate_result_review(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"Final result review is not ready: {path}")
    if path.stat().st_size == 0:
        raise ValueError("Final result review is empty")


def validate_annotation_artifact_names(members) -> None:
    """Reject candidate/history files while permitting the active correction ledger."""
    active_corrections = f"{LABELS_DIR.relative_to(ROOT).as_posix()}/shard-2-audit-corrections.jsonl"
    for name in members:
        if "labels-candidate" in name or "diff-audit" in name:
            raise ValueError("A candidate/historical annotation artifact entered the package plan")
        if "audit-corrections" in name and name != active_corrections:
            raise ValueError("A non-active correction ledger entered the package plan")


def build_payload_plan(decide_dir: Path | None = None) -> dict:
    """Validate final artifacts and return payloads; does not write archives."""
    input_payloads, input_manifest, cases = collect_input_payloads()
    labels_payloads, labels_manifest, label_counts = collect_labels(cases, input_manifest)
    audit_payloads, audit = collect_audit(input_manifest, labels_manifest)
    model_payloads, model_summaries = collect_model_outputs()
    decide_payloads, decide_summary = collect_decide_outputs(decide_dir)
    derived_payloads, derived_summaries = collect_derived_model_summaries()
    arrays_payloads, evidence_counts = collect_raw_evidence(cases, input_manifest)
    code_payloads = collect_code_payloads()
    review_path = MODEL_DIR / "result-review.txt"
    validate_result_review(review_path)
    common = {}
    for collection in (input_payloads, labels_payloads, audit_payloads, model_payloads,
                       decide_payloads, derived_payloads, arrays_payloads, code_payloads):
        for member, path in collection.items():
            if member in common and common[member] != path:
                raise ValueError(f"Conflicting allowlist member: {member}")
            common[member] = path
    add_payload(common, review_path)
    raw_members = {member: path for member, path in arrays_payloads.items()
                   if any(member.startswith(root) for root in RAW_ROOTS)}
    compact = {member: path for member, path in common.items() if member not in raw_members}
    full = dict(common)
    if len(raw_members) != 75:
        raise ValueError(f"Full evidence allowlist should contain 75 raw refs, found {len(raw_members)}")
    validate_annotation_artifact_names(common)
    return {
        "compact_payloads": compact,
        "full_payloads": full,
        "input_manifest": input_manifest,
        "labels_manifest": labels_manifest,
        "audit": audit,
        "label_counts": label_counts,
        "case_counts": case_metrics(cases),
        "model_summaries": {model: {
            "model": model,
            "case_count": model_summaries[model].get("case_count"),
            "unique_rakuten_sku_references": model_summaries[model].get("unique_rakuten_sku_references"),
            "dev_case_count": model_summaries[model].get("dev_case_count"),
            "test_case_count": model_summaries[model].get("test_case_count"),
        } for model in MODELS},
        "decide_summary": decide_summary,
        "derived_summaries": derived_summaries,
        "evidence_counts": evidence_counts,
    }


def zip_archive(path: Path, payloads: dict[str, Path], kind: str,
                payload_snapshot: dict[str, dict], metadata: dict) -> dict:
    """Write one deterministic archive and verify CRC plus every member SHA."""
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing package or sidecar: {path}")
    internal = {
        "record_kind": "LUNA_REAL_SKU_EVALUATION_DELIVERY",
        "package_kind": kind,
        "synthetic_data_included": False,
        "annotation_status": "machine_annotation_not_human_verified_gold",
        "human_verified": False,
        "case_counts": metadata["case_counts"],
        "label_counts": metadata["label_counts"],
        "evidence_counts": metadata["evidence_counts"] if kind == "full-evidence" else {"raw_sources_included": 0},
        "payload_sha256_pre": payload_snapshot,
        # Written as the expected post-state and verified after the archive closes.
        "payload_sha256_post": payload_snapshot,
        "files": [],
    }
    temp_path = path.with_name(path.name + ".tmp")
    if temp_path.exists() or temp_path.is_symlink():
        raise FileExistsError(f"Refusing to overwrite package temp sidecar: {temp_path}")
    try:
        with zipfile.ZipFile(temp_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for member, source in sorted(payloads.items()):
                before = payload_snapshot[member]
                data = source.read_bytes()
                actual = {"bytes": len(data), "sha256": sha256_bytes(data)}
                if actual != before:
                    raise RuntimeError(f"Source changed before archive write: {member}")
                info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
                internal["files"].append({"path": member, **actual})
            archive.writestr("LUNA-DELIVERY-MANIFEST.json",
                             json.dumps(internal, ensure_ascii=False, indent=2) + "\n")
        assert_payloads_unchanged(payloads, payload_snapshot)
        with zipfile.ZipFile(temp_path, "r") as archive:
            if archive.testzip() is not None:
                raise ValueError(f"ZIP CRC validation failed: {temp_path}")
            for row in internal["files"]:
                data = archive.read(row["path"])
                if len(data) != row["bytes"] or sha256_bytes(data) != row["sha256"]:
                    raise ValueError(f"ZIP payload digest mismatch: {row['path']}")
        internal["payload_sha256_post"] = snapshot_payloads(payloads)
        if internal["payload_sha256_post"] != payload_snapshot:
            raise RuntimeError("Package source pre/post digest mismatch")
        os.link(temp_path, path)
        temp_path.unlink()
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path),
            "payload_file_count": len(payloads), "crc_checked": True,
            "all_payload_sha256_checked": True, "source_pre_post_unchanged": True,
            "internal_manifest_sha256": sha256_bytes(json.dumps(internal, ensure_ascii=False, indent=2).encode() + b"\n")}


def create_packages(output_dir: Path, decide_dir: Path | None = None) -> dict:
    """Create both allowlisted archives. This function is intentionally not run during preparation."""
    output_dir = output_dir if output_dir.is_absolute() else ROOT / output_dir
    output_dir = output_dir.absolute()
    if output_dir.is_symlink():
        raise ValueError("Package output directory cannot be a symlink")
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(ROOT / ".lab-output") or has_synthetic_path(output_dir.relative_to(ROOT)):
        raise ValueError("Output must be a new non-synthetic directory under .lab-output")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing output directory/sidecars: {output_dir}")
    plan = build_payload_plan(decide_dir)
    assert_payloads_unchanged(plan["compact_payloads"], snapshot_payloads(plan["compact_payloads"]))
    assert_payloads_unchanged(plan["full_payloads"], snapshot_payloads(plan["full_payloads"]))
    output_dir.mkdir(parents=True, exist_ok=False)
    compact_path, full_path = output_dir / "compact.zip", output_dir / "full-evidence.zip"
    compact_before = snapshot_payloads(plan["compact_payloads"])
    full_before = snapshot_payloads(plan["full_payloads"])
    metadata = {"case_counts": plan["case_counts"], "label_counts": plan["label_counts"],
                "evidence_counts": plan["evidence_counts"]}
    try:
        compact_meta = zip_archive(compact_path, plan["compact_payloads"], "compact", compact_before, metadata)
        full_meta = zip_archive(full_path, plan["full_payloads"], "full-evidence", full_before, metadata)
        assert_payloads_unchanged(plan["compact_payloads"], compact_before)
        assert_payloads_unchanged(plan["full_payloads"], full_before)
        manifest = {
            "record_kind": "LUNA_REAL_SKU_EVALUATION_DELIVERY",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "synthetic_data_included": False,
            "annotation_status": "machine_annotation_not_human_verified_gold",
            "human_verified": False,
            "case_counts": plan["case_counts"],
            "label_counts": plan["label_counts"],
            "annotation_protocol": plan["labels_manifest"].get("annotation_protocol", {}),
            "price_grain_policy": {
                "au": "product-level price from AU product page; no per-SKU AU price is inferred",
                "rakuten": "per-Rakuten-SKU price retained only in separate eligibility data",
                "not_identity_evidence": True,
            },
            "input_manifest_sha256": sha256_file(INPUTS / "manifest.json"),
            "labels_manifest_sha256": sha256_file(LABELS_DIR / "manifest.json"),
            "audit_sha256": sha256_file(AUDIT_DIR / "audit.json"),
            "independent_review_sha256": sha256_file(INDEPENDENT_REVIEW),
            "independent_review_counts": plan["audit"]["independent_review_counts"],
            "model_outputs": plan["model_summaries"],
            "optional_decide_result_included": plan["decide_summary"] is not None,
            "full_evidence_sources": plan["evidence_counts"],
            "archives": {"compact.zip": compact_meta, "full-evidence.zip": full_meta},
            "payloads": {"compact": compact_before, "full-evidence": full_before},
            "source_pre_post_unchanged": True,
            "notes": ["Luna labels are machine annotations and have not been human verified.",
                      "Counts distinguish annotation case references from unique Rakuten SKU source records.",
                      "The compact archive omits raw source captures; the full-evidence archive includes only explicit raw references."],
        }
        manifest_path = output_dir / "manifest.json"
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for zip_path in (compact_path, full_path):
            with zip_path.with_name(zip_path.name + ".sha256").open("x", encoding="ascii") as stream:
                stream.write(f"{sha256_file(zip_path)}  {zip_path.name}\n")
        return manifest
    except Exception:
        # The directory was created by this invocation; remove partial outputs only.
        for child in output_dir.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        output_dir.rmdir()
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--decide-dir", type=Path,
                        help="Optional result subdirectory under the fixed decide-evaluation root")
    args = parser.parse_args(argv)
    manifest = create_packages(args.output_dir, args.decide_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "case_counts": manifest["case_counts"],
                      "label_counts": manifest["label_counts"], "archives": manifest["archives"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
