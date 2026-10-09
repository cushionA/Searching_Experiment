#!/usr/bin/env python3
"""Run a sampled, real-SKU pairwise diagnostic with pinned GLiNER2.5 Decide.

Sampling uses only stable case IDs and AU family metadata. Labels and frozen
retrieval predictions are joined after sampling and are never model inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_INPUTS = ROOT / ".lab-output/sku-real-luna-annotation-inputs-20261010-v2"
DEFAULT_LABELS = ROOT / ".lab-output/sku-real-luna-labels-20261010-v1/labels.jsonl"
DEFAULT_RURI = ROOT / ".lab-output/sku-real-luna-model-evaluation-20261010-v1/ruri/predictions-sku.json"
DEFAULT_AU_ARRAYS = ROOT / ".lab-output/sku-observed-product-pairs-20261010-final-v4/au_product_sku_arrays.jsonl"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-real-luna-decide-evaluation-20261010-v1/newexclusive"
MODEL_DIR = ROOT / ".deps/sku-gliner-model"
REPO = "fastino/GLiNER2.5-multi-Decide"
REVISION = "e173bca1f0c4217d7a55b3b0c705d5ece8a0218d"
PINNED_RURI_SUMMARY_SHA256 = "aa3458ac7297b0c1106fca5ba3730724f55bcb1e4a1d01bade0513d249b7fa3b"
PINNED_RURI_PREDICTIONS_SHA256 = "d8f83c8740fd2942351bb9ee001f4bd9316d86a25c1b80d0c4258219d2d8110e"
RURI_MODE = "sku"
LABELS = [
    "same SKU and same product variant",
    "different SKU or product variant",
    "needs human review",
]
DECISION_TO_LABEL = {
    "matched": LABELS[0], "unmatched": LABELS[1], "review": LABELS[2],
}
LABEL_TO_DECISION = {v: k for k, v in DECISION_TO_LABEL.items()}
MAX_PER_FAMILY = 16
MAX_CURTAIN_FAMILY = 32


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integrity_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    """Verify frozen annotation/label manifests and capture Ruri/code context."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from evaluate_luna_real_skus import collect_input_snapshot, verify_input_manifests

    input_snapshot = collect_input_snapshot(args.inputs_dir, args.labels, args.au_arrays)
    manifest_checks = verify_input_manifests(args.inputs_dir, args.labels, args.au_arrays,
                                             input_snapshot)
    ruri_dir = args.ruri if args.ruri.is_dir() else args.ruri.parent
    prediction_path = args.ruri if args.ruri.is_file() else ruri_dir / "predictions-title-sku.json"
    summary_path = ruri_dir / "summary.json"
    if not prediction_path.is_file() or not summary_path.is_file():
        raise FileNotFoundError("Frozen Ruri predictions and summary.json must both exist")
    prediction_sha = sha256(prediction_path)
    summary_sha = sha256(summary_path)
    if summary_sha != PINNED_RURI_SUMMARY_SHA256:
        raise ValueError("Frozen Ruri summary SHA does not match the pinned source snapshot")
    if prediction_sha != PINNED_RURI_PREDICTIONS_SHA256:
        raise ValueError("Frozen Ruri SKU prediction SHA does not match the pinned source snapshot")
    prediction_doc = json.loads(prediction_path.read_text(encoding="utf-8"))
    if prediction_doc.get("mode") != RURI_MODE:
        raise ValueError(f"Expected frozen Ruri mode={RURI_MODE!r}, got {prediction_doc.get('mode')!r}")
    if len(prediction_doc.get("rows", [])) != 1383:
        raise ValueError("Frozen Ruri SKU predictions must cover the full 1,383-case set")
    ruri_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if RURI_MODE not in ruri_summary.get("modes", {}):
        raise ValueError("Frozen Ruri summary has no SKU-mode evaluation section")
    label_manifest_path = args.labels.parent / "manifest.json"
    label_manifest = json.loads(label_manifest_path.read_text(encoding="utf-8"))
    declared_label_sha = label_manifest.get("output_sha256", {}).get(args.labels.name)
    actual_label_sha = sha256(args.labels)
    if not declared_label_sha or declared_label_sha != actual_label_sha:
        raise ValueError("Luna label SHA does not match labels manifest output_sha256")

    ruri_input = ruri_summary.get("input_sha256", {})
    ruri_labels = ruri_input.get("labels", {})
    if isinstance(ruri_labels, dict):
        ruri_label_sha = ruri_labels.get(args.labels.name)
    else:
        ruri_label_sha = ruri_labels
    if ruri_label_sha != actual_label_sha:
        raise ValueError("Frozen Ruri summary does not reference the current frozen labels SHA")
    expected_cases_sha = input_snapshot["annotation_files"].get("cases.jsonl")
    ruri_cases_sha = ruri_input.get("annotation_files", {}).get("cases.jsonl")
    if ruri_cases_sha is None:
        ruri_cases_sha = ruri_input.get("cases")
    if ruri_cases_sha != expected_cases_sha:
        raise ValueError("Frozen Ruri summary does not reference the current annotation cases SHA")

    ruri_code_files = {
        "evaluate_luna_real_skus.py": HERE / "evaluate_luna_real_skus.py",
        "backend_ruri.py": HERE / "backend_ruri.py",
    }
    recorded_code = ruri_summary.get("code_sha256", {})
    if not recorded_code:
        raise ValueError("Frozen Ruri summary is missing code_sha256")
    current_code = {}
    for name, path in ruri_code_files.items():
        recorded = recorded_code.get(name)
        if not recorded:
            raise ValueError(f"Frozen Ruri summary is missing code hash for {name}")
        actual = sha256(path)
        if actual != recorded:
            raise ValueError(f"Ruri source code differs from frozen summary: {name}")
        current_code[name] = actual

    return {
        "annotation_and_label_inputs": input_snapshot,
        "manifest_checks": manifest_checks,
        "labels_manifest_declared_sha256": declared_label_sha,
        "labels_actual_sha256": actual_label_sha,
        "ruri_prediction_path": str(prediction_path),
        "ruri_prediction_sha256": prediction_sha,
        "ruri_prediction_mode": prediction_doc["mode"],
        "ruri_summary_path": str(summary_path),
        "ruri_summary_sha256": summary_sha,
        "ruri_summary_code_sha256": recorded_code,
        "ruri_code_sha256_verified": current_code,
        "ruri_summary_inputs": ruri_input,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_no}: expected object")
                rows.append(row)
    return rows


def stable_case_hash(case_id: str) -> str:
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()


def select_sample(cases: list[dict[str, Any]], dossiers: dict[str, dict[str, Any]],
                  *, normal_cap: int = MAX_PER_FAMILY,
                  curtain_cap: int = MAX_CURTAIN_FAMILY) -> list[dict[str, Any]]:
    """Select within family/split by case-ID hash, independent of labels/predictions."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    family_titles: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        dossier = dossiers.get(case.get("dossier_id"))
        if dossier is None:
            raise ValueError(f"Missing dossier for {case.get('case_id')}")
        product = dossier.get("au_product", {})
        # group_id is the frozen family grouping in the annotation inputs. AU
        # product_id can identify several sibling products inside one family.
        family = str(case.get("group_id") or "")
        if not family:
            raise ValueError(f"Missing annotation family group_id for {case.get('case_id')}")
        split = case.get("split") or case.get("shard_id")
        if split not in {"dev", "test"}:
            raise ValueError(f"split must be dev or test: {case.get('case_id')}")
        family_titles[family].add(str(product.get("title_raw", "")).casefold())
        grouped[family].append({**case, "_sample_family": family, "_sample_split": split,
                                "_sample_hash": stable_case_hash(str(case["case_id"]))})

    selected: list[dict[str, Any]] = []
    for family, family_cases in grouped.items():
        titles = family_titles[family]
        is_curtain = any("カーテン" in title or "curtain" in title for title in titles)
        cap = curtain_cap if is_curtain else normal_cap
        by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for case in family_cases:
            by_split[case["_sample_split"]].append(case)
        present_splits = sorted(by_split)
        if len(family_cases) <= cap:
            quotas = {split: len(by_split[split]) for split in present_splits}
        else:
            # Reserve one place per existing split, then allocate proportionally.
            quotas = {split: 1 for split in present_splits}
            remaining = cap - len(present_splits)
            if remaining < 0:
                raise ValueError("family cap is smaller than number of splits")
            total = len(family_cases)
            exact = {split: remaining * len(by_split[split]) / total for split in present_splits}
            floors = {split: int(exact[split]) for split in present_splits}
            for split in present_splits:
                quotas[split] += floors[split]
            left = cap - sum(quotas.values())
            for split in sorted(present_splits, key=lambda s: (-(exact[s] - floors[s]), s))[:left]:
                quotas[split] += 1
        for split in present_splits:
            ranked = sorted(by_split[split], key=lambda c: (c["_sample_hash"], c["case_id"]))
            selected.extend({**case, "sample_rank_in_family_split": rank,
                             "sample_family_cap": cap}
                            for rank, case in enumerate(ranked[:quotas[split]], 1))
    selected.sort(key=lambda c: c["case_id"])
    return selected


def model_pair_text(case: dict[str, Any], au_title_sku: str, mode: str) -> str:
    """Build balanced pair text from observed titles and option/SKU strings only."""
    rakuten = case["rakuten"]
    query_sku = case["query_sku"]
    au_sku = case["top_au_sku"]
    if mode == "title-sku":
        query = f"{rakuten.get('title_raw', '')} / {query_sku}"
        candidate = au_title_sku
    elif mode == "sku":
        query, candidate = query_sku, au_sku
    else:
        raise ValueError(f"Unsupported mode: {mode}")
    return f"商品A: {query}\n商品B: {candidate}"


def _label_decision(row: dict[str, Any]) -> str:
    value = row.get("decision", row.get("semantic_label"))
    if value not in DECISION_TO_LABEL:
        raise ValueError(f"Invalid Luna semantic label for {row.get('case_id')}: {value!r}")
    return value


def _prediction_rows(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        path = path / "predictions-title-sku.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("rows") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"Expected frozen Ruri prediction object with rows: {path}")
    return rows


def _axis_text(values: list[dict[str, Any]], name_key: str, value_key: str) -> str:
    parts = []
    for axis in values:
        value = str(axis.get(value_key) or "").strip()
        if value:
            name = str(axis.get(name_key) or axis.get("axis_key") or axis.get("key") or "").strip()
            parts.append(f"{name}={value}")
    return " / ".join(parts)


def rakuten_sku_text(rakuten: dict[str, Any]) -> str:
    """Match the embedding adapter's axis-label lookup and serialization."""
    axis_names = {a.get("key"): a.get("label") or a.get("name")
                  for a in rakuten.get("axes_labels", []) if a.get("key")}
    query_options = []
    for value in rakuten.get("option_values", []):
        option = dict(value)
        option["axis_name"] = (option.get("axis_name")
                               or axis_names.get(option.get("axis_key"))
                               or option.get("axis_key"))
        query_options.append(option)
    return _axis_text(query_options, "axis_name", "value")


def build_sample_pairs(sample: list[dict[str, Any]], labels_path: Path,
                       ruri_path: Path, au_arrays_path: Path,
                       dossiers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Join gold and top1 only after selection; never return label text to model input."""
    labels = read_jsonl(labels_path)
    labels_by_id = {row["case_id"]: row for row in labels}
    if len(labels_by_id) != len(labels):
        raise ValueError("Duplicate case_id in Luna labels")
    predictions = _prediction_rows(ruri_path)
    predictions_by_id = {row["case_id"]: row for row in predictions}
    if len(predictions_by_id) != len(predictions):
        raise ValueError("Duplicate case_id in frozen Ruri predictions")
    selected_ids = {row["case_id"] for row in sample}
    if not selected_ids <= set(labels_by_id):
        raise ValueError("Luna labels do not cover all selected case IDs")
    if not selected_ids <= set(predictions_by_id):
        raise ValueError("Frozen Ruri predictions do not cover all selected case IDs")
    arrays_by_product = {str(row["au_product_id"]): row for row in read_jsonl(au_arrays_path)}

    prepared = []
    for sampled in sample:
        case = dict(sampled)
        dossier = dossiers[case["dossier_id"]]
        product_id = str(dossier["au_product"]["product_id"])
        product = arrays_by_product.get(product_id)
        if product is None:
            raise ValueError(f"No AU SKU array for product family {product_id}")
        candidate_by_key = {}
        for cell in product["au_sku_cells_raw"]:
            source = cell["source_grain"]
            row_key = (f"au:{product_id}:{source['sku_id']}:{source['row_index']}"
                       f":{source['column_index']}")
            axes = _axis_text(cell.get("axes_raw", []), "axis_name_raw", "value_raw")
            candidate_by_key[row_key] = {
                "row_key": row_key,
                "sku": axes,
                "title_sku": f"{product['au_product_title_raw']} / {axes}",
            }
        pred = predictions_by_id[case["case_id"]]
        top_key = pred.get("top_row_key")
        if top_key not in candidate_by_key:
            raise ValueError(f"Ruri top1 row key is absent from fixed AU pool: {case['case_id']}")
        rakuten = case["rakuten"]
        case["query_sku"] = rakuten_sku_text(rakuten)
        top = candidate_by_key[top_key]
        case["top_row_key"] = top_key
        case["top_au_sku"] = top["sku"]
        case["top_au_title_sku"] = top["title_sku"]
        case["gold_decision"] = _label_decision(labels_by_id[case["case_id"]])
        case["gold_matching_au_row_keys"] = labels_by_id[case["case_id"]].get("matching_au_row_keys", [])
        case["ruri_top10"] = pred.get("top10", [])
        case["retrieval_rank_of_any_gold_row"] = next(
            (rank for rank, row in enumerate(case["ruri_top10"], 1)
             if row.get("row_key") in case["gold_matching_au_row_keys"]), None)
        case["top1_retrieval_hit"] = top_key in case["gold_matching_au_row_keys"] if case["gold_decision"] == "matched" else None
        prepared.append(case)
    return prepared


def metric_summary(cases: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    confusion = {decision: {label: 0 for label in LABELS} for decision in DECISION_TO_LABEL}
    known = [i for i, case in enumerate(cases) if case["gold_decision"] != "review"]
    correct = sum(predictions[i]["prediction"] == DECISION_TO_LABEL[cases[i]["gold_decision"]] for i in known)
    gold_matched = [case for case in cases if case["gold_decision"] == "matched"]
    top1_hits = sum(case["top1_retrieval_hit"] is True for case in gold_matched)
    e2e_correct_matched = sum(
        case["gold_decision"] == "matched"
        and case["top1_retrieval_hit"] is True
        and predictions[i]["prediction"] == LABELS[0]
        for i, case in enumerate(cases))
    accepted_wrong = sum(
        case["gold_decision"] == "matched"
        and (case["top1_retrieval_hit"] is not True)
        and predictions[i]["prediction"] == LABELS[0]
        for i, case in enumerate(cases))
    accepted_known = sum(predictions[i]["prediction"] == LABELS[0] for i in known)
    for case, pred in zip(cases, predictions, strict=True):
        confusion[case["gold_decision"]][pred["prediction"]] += 1
    return {
        "confusion_matrix": confusion,
        "known_luna_label_accuracy_excluding_review": correct / len(known) if known else None,
        "known_luna_label_case_count": len(known),
        "review_gold_unknown_count": sum(c["gold_decision"] == "review" for c in cases),
        "accepted_review_unknown_count": sum(c["gold_decision"] == "review" and p["prediction"] == LABELS[0]
                                              for c, p in zip(cases, predictions, strict=True)),
        "gold_matched_case_count": len(gold_matched),
        "ruri_top1_retrieval_recall_on_luna_matched": top1_hits / len(gold_matched) if gold_matched else None,
        "ruri_top1_retrieval_miss_count_on_luna_matched": len(gold_matched) - top1_hits,
        "end_to_end_correct_same_sku_count": e2e_correct_matched,
        "end_to_end_same_sku_recall_on_luna_matched": e2e_correct_matched / len(gold_matched) if gold_matched else None,
        "accepted_wrong_top1_same_sku_count": accepted_wrong,
        "known_luna_matched_precision_including_wrong_top1_as_error": (
            sum(c["gold_decision"] == "matched" and c["top1_retrieval_hit"] is True
                and p["prediction"] == LABELS[0]
                for c, p in zip(cases, predictions, strict=True)) / accepted_known if accepted_known else None),
    }


def tokenization_audit(texts_by_mode: dict[str, list[str]], model_dir: Path) -> dict[str, Any]:
    """Measure untruncated input lengths against checkpoint max_len."""
    from transformers import AutoTokenizer

    config_path = model_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    effective_max_len = int(config.get("max_len", 0))
    encoder_config = config.get("encoder_config", {})
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True,
                                              trust_remote_code=False, use_fast=True)
    audit = {
        "checkpoint_max_len": effective_max_len or None,
        "encoder_max_position_embeddings": encoder_config.get("max_position_embeddings"),
        "tokenizer_model_max_length_metadata": tokenizer.model_max_length,
        "config_sha256": sha256(config_path),
        "encoder_config_sha256": sha256(model_dir / "encoder_config/config.json"),
        "modes": {},
        "note": "Token counts are measured without truncation; checkpoint max_len is the GLiNER limit. Encoder max_position_embeddings is recorded separately because the encoder uses relative positions.",
    }
    for mode, texts in texts_by_mode.items():
        lengths = []
        for start in range(0, len(texts), 64):
            encoded = tokenizer(texts[start:start + 64], add_special_tokens=True,
                                 truncation=False, padding=False)
            lengths.extend(len(ids) for ids in encoded["input_ids"])
        over_limit = [i for i, length in enumerate(lengths)
                      if effective_max_len and length > effective_max_len]
        audit["modes"][mode] = {
            "case_count": len(texts), "max_token_length": max(lengths, default=0),
            "mean_token_length": sum(lengths) / len(lengths) if lengths else None,
            "over_checkpoint_max_len_count": len(over_limit),
            "over_checkpoint_max_len_case_indices": over_limit,
            "per_case_token_lengths": lengths,
        }
    del tokenizer
    return audit


def run(args: argparse.Namespace) -> dict[str, Any]:
    from try_gliner import FILES, MODEL_DIR as PINNED_MODEL_DIR
    model_dir = args.model_dir or PINNED_MODEL_DIR
    integrity_pre = integrity_snapshot(args)
    cases_path = args.inputs_dir / "cases.jsonl"
    cases = read_jsonl(cases_path)
    dossier_dir = args.inputs_dir / "dossiers"
    dossiers = {}
    for path in dossier_dir.glob("*.json"):
        dossier = json.loads(path.read_text(encoding="utf-8"))
        dossiers[dossier["dossier_id"]] = dossier
    sample = select_sample(cases, dossiers)
    sampled_test_count = sum(case.get("split") == "test" for case in sample)
    if len(sample) != 166 or sampled_test_count != 62:
        raise RuntimeError(f"Frozen stable sample changed: {len(sample)} cases, {sampled_test_count} test cases")
    # Only now read labels and predictions.
    prepared = build_sample_pairs(sample, args.labels, args.ruri, args.au_arrays, dossiers)
    if not prepared:
        raise ValueError("No sampled real cases")
    texts_by_mode = {
        mode: [model_pair_text(case, case["top_au_title_sku"], mode) for case in prepared]
        for mode in ("title-sku", "sku")
    }
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    if torch.cuda.is_available():
        raise RuntimeError("CUDA is available; refusing GPU inference")
    token_audit_start = time.perf_counter()
    token_audit = tokenization_audit(texts_by_mode, model_dir)
    token_audit["elapsed_seconds"] = time.perf_counter() - token_audit_start

    # Verify all checkpoint files against the SHA pinned in try_gliner.py.
    model_files = {}
    for name, expected in FILES.items():
        path = model_dir / name
        digest = sha256(path)
        size = path.stat().st_size
        if size != expected["size_bytes"] or digest != expected["sha256"]:
            raise RuntimeError(f"Pinned GLiNER checkpoint verification failed: {name}")
        model_files[name] = {"size_bytes": size, "sha256": digest}
    source_manifest_path = model_dir / "source-manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("repo") != REPO or source_manifest.get("revision") != REVISION:
        raise RuntimeError("GLiNER local source manifest does not match pinned repo/revision")
    manifest_files = source_manifest.get("files", {})
    for name, verified in model_files.items():
        if manifest_files.get(name) != verified:
            raise RuntimeError(f"GLiNER source manifest does not match verified checkpoint file: {name}")

    from gliner2 import AutoExtractor
    load_start = time.perf_counter()
    model = AutoExtractor.from_pretrained(str(model_dir), local_files_only=True)
    model.eval()
    load_seconds = time.perf_counter() - load_start

    rows_by_mode: dict[str, list[dict[str, Any]]] = {}
    timing = {}
    for mode in ("title-sku", "sku"):
        texts = texts_by_mode[mode]
        start = time.perf_counter()
        raw_outputs = model.batch_classify_text(texts, {"decision": LABELS},
                                                batch_size=args.batch_size,
                                                include_confidence=True)
        elapsed = time.perf_counter() - start
        if len(raw_outputs) != len(prepared):
            raise RuntimeError("GLiNER returned an unexpected number of rows")
        mode_rows = []
        for case, text, output in zip(prepared, texts, raw_outputs, strict=True):
            decision = output.get("decision") if isinstance(output, dict) else output
            label = decision.get("label") if isinstance(decision, dict) else decision
            confidence = decision.get("confidence") if isinstance(decision, dict) else None
            if label not in LABEL_TO_DECISION:
                raise ValueError(f"Unexpected GLiNER label for {case['case_id']}: {label!r}")
            mode_rows.append({
                "case_id": case["case_id"], "split": case.get("split", case.get("shard_id")),
                "sample_family": case["_sample_family"], "sample_hash": case["_sample_hash"],
                "sample_rank_in_family_split": case["sample_rank_in_family_split"],
                "retrieval_candidate_rank": 1, "top_row_key": case["top_row_key"],
                "ruri_ranked_candidates": case["ruri_top10"],
                "top1_retrieval_hit": case["top1_retrieval_hit"],
                "gold_decision_for_metrics_only": case["gold_decision"],
                "gold_matching_au_row_keys_for_metrics_only": case["gold_matching_au_row_keys"],
                "retrieval_rank_of_any_gold_row": case["retrieval_rank_of_any_gold_row"],
                "model_input": text, "prediction": label,
                "prediction_decision": LABEL_TO_DECISION[label],
                "confidence_raw": confidence, "raw_output": output,
            })
        rows_by_mode[mode] = mode_rows
        timing[mode] = {"seconds": elapsed, "cases": len(texts), "batch_size": args.batch_size}

    metrics = {mode: metric_summary(prepared, rows) for mode, rows in rows_by_mode.items()}
    labels_manifest = args.labels.parent / "manifest.json"
    ruri_file = args.ruri if args.ruri.is_file() else args.ruri / "predictions-title-sku.json"
    dossier_hashes = {p.name: sha256(p) for p in sorted(dossier_dir.glob("*.json"))}
    weight_hashes = {p.relative_to(model_dir).as_posix(): sha256(p)
                     for p in sorted(model_dir.rglob("*")) if p.is_file() and p.name != "README.md"}
    versions = {}
    for package in ("gliner2", "torch", "transformers", "tokenizers", "safetensors", "huggingface-hub"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    integrity_post = integrity_snapshot(args)
    if integrity_post != integrity_pre:
        raise RuntimeError("Frozen inputs, labels, Ruri predictions/summary, or Ruri code changed during run")
    sample_ids = [c["case_id"] for c in prepared]
    result = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": REPO, "revision": REVISION, "model_files_pinned_sha256": model_files,
        "model_weight_sha256": weight_hashes,
        "source_manifest_sha256": sha256(source_manifest_path), "source_manifest": source_manifest,
        "code_sha256": {
            Path(__file__).name: sha256(Path(__file__)),
            "try_gliner.py": sha256(HERE / "try_gliner.py"),
        },
        "label_source": "Luna semantic labels; machine annotation, not human-verified gold",
        "label_gold_status": "human_unreviewed", "synthetic_input_count": 0,
        "threads": {"intra_op": torch.get_num_threads(), "inter_op": torch.get_num_interop_threads()},
        "batch_size": args.batch_size, "runtime_versions": versions,
        "platform": platform.platform(), "python": platform.python_version(),
        "cold_load_seconds": load_seconds, "inference_timing": timing,
        "tokenization_truncation_audit": token_audit,
        "input_sha256": {
            "cases": sha256(cases_path), "labels": sha256(args.labels),
            "labels_manifest": sha256(labels_manifest) if labels_manifest.is_file() else None,
            "ruri_predictions": sha256(ruri_file),
            "au_product_sku_arrays": sha256(args.au_arrays), "dossiers": dossier_hashes,
        },
        "input_integrity": {
            "pre_run_snapshot": integrity_pre,
            "post_run_snapshot": integrity_post,
            "unchanged_during_run": True,
            "manifest_validation": "annotation input, dossier, shard, label output SHA, and label-to-input linkage verified",
            "ruri_context_validation": "prediction, summary, labels/cases context hashes, and Ruri code SHA verified",
        },
        "sampling": {
            "source_case_count": len(cases), "selected_case_count": len(sample_ids),
            "case_id_hash": "sha256(case_id UTF-8)", "selected_case_ids": sample_ids,
            "family_caps": {"default": MAX_PER_FAMILY, "curtain": MAX_CURTAIN_FAMILY},
            "selection_independent_of_labels_and_predictions": True,
            "selected_test_case_count": sum(c.get("split") == "test" for c in prepared),
            "full_embedding_evaluation_case_count": 1383,
            "denominator_note": "This 166-case diagnostic sample has 62 test cases; its denominator differs from the full 1,383-case embedding evaluation.",
            "family_counts": dict(Counter(c["_sample_family"] for c in prepared)),
            "split_counts": dict(Counter(str(c.get("split", c.get("shard_id"))) for c in prepared)),
        },
        "retrieval_selection_basis": {
            "retrieval_mode": RURI_MODE,
            "selection_source": "Frozen Ruri SKU-only top1 predictions",
            "dev_top1_accuracy_sku_only": 0.9368,
            "dev_top1_accuracy_title_sku": 0.7579,
            "reason": "Selected SKU-only retrieval because development-set top1 accuracy was higher.",
            "test_interpretation": "These development-only retrieval figures motivated candidate source selection; they are not used to explain test results.",
            "embedding_vs_classifier": "GLiNER Decide classifies one frozen Ruri candidate pair per case. It is a pairwise classifier diagnostic, not an embedding ranker comparison.",
        },
        "candidate_policy": "Only the frozen Ruri SKU-only top1 AU row is classified in both GLiNER input modes; candidate rank is always 1.",
        "model_input_contract": {
            "title-sku": "Rakuten observed title and option text paired with fixed AU product title and top1 SKU axes",
            "sku": "Rakuten option text paired with top1 AU SKU axes",
            "pair_template": "商品A: <query>\\n商品B: <AU candidate>; no gold hint, task question, labels, or annotated attributes",
            "excluded": ["gold labels", "gold AU row keys", "annotated attributes", "price", "stock", "availability", "URL", "evidence"],
        },
        "label_mapping": DECISION_TO_LABEL,
        "retrieval_miss_policy": "A Luna-matched case whose Ruri top1 is not a gold AU row remains an end-to-end false selection if GLiNER predicts same SKU; miss is reported separately.",
        "interpretation_limits": [
            "Luna labels are machine annotations and have not been human reviewed.",
            "Review cases have unknown truth and are reported separately.",
            "This is a sampled diagnostic of frozen Ruri top1 pairs, not a full retrieval comparison or population precision claim.",
            "No threshold tuning was performed; GLiNER labels are used directly.",
        ],
        "case_rows": {mode: rows for mode, rows in rows_by_mode.items()},
        "metrics": metrics,
        "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-dir", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--ruri", type=Path, default=DEFAULT_RURI)
    parser.add_argument("--au-arrays", type=Path, default=DEFAULT_AU_ARRAYS)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    if args.batch_size != 8:
        parser.error("this diagnostic is pinned to batch_size=8")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(exist_ok=False)
    with (args.output / "summary.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "sampled_cases": result["sampling"]["selected_case_count"],
                      "metrics": result["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
