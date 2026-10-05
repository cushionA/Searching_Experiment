#!/usr/bin/env python3
"""Verify and summarize the fixed public CAPTCHA multimodal evaluation outputs.

This script reads the supplied manifest and downloaded model artifacts only. It
does not run models, alter references, or overwrite an existing output folder.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Any


EXPECTED_MODELS = {
    "tinyclip_vit_40m_32_text_19m": "TinyCLIP-ViT-40M-32-Text-19M",
    "mobileclip2_s0": "MobileCLIP2-S0",
    "mobileclip2_s2": "MobileCLIP2-S2",
    "moe_vie_b16": "MoE-ViE-B16",
}
EXPECTED_SAMPLES = 4068
EXPECTED_BOARDS = 1000
EXPECTED_TYPES = {"Type A": 662, "Type B": 338}
TEXT_RUN_NAME = "text-ocr-evaluation"
LIVE_RUN_NAME = "live-observations"


class IntegrityError(ValueError):
    """Raised when an artifact does not match its pinned input or labels."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntegrityError(f"Cannot read JSON {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def same_float(actual: Any, expected: float, what: str) -> None:
    require(is_finite_number(actual), f"{what} is not finite numeric: {actual!r}")
    require(math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-9),
            f"{what} mismatch: artifact={actual}, recomputed={expected}")


def jsonl_rows(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream if line.strip()]
    except Exception as exc:
        raise IntegrityError(f"Cannot read JSONL {path}: {exc}") from exc
    require(all(isinstance(row, dict) for row in rows), f"Non-object JSONL row in {path}")
    return rows


def accuracy(rows: list[dict[str, Any]], key: str = "correct") -> float | None:
    return sum(bool(row[key]) for row in rows) / len(rows) if rows else None


def confusion_matrix(rows: list[dict[str, Any]], labels: list[str]) -> dict[str, dict[str, int]]:
    return {
        actual: {pred: sum(r["label"] == actual and r["prediction"] == pred for r in rows)
                 for pred in labels}
        for actual in labels
    }


def sample_metrics(rows: list[dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    by_target: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_target[row["label"]].append(row)
    return {
        "count": len(rows),
        "correct": sum(r["prediction"] == r["label"] for r in rows),
        "accuracy": accuracy([{"correct": r["prediction"] == r["label"]} for r in rows]),
        "by_target": {
            label: {
                "count": len(group),
                "correct": sum(r["prediction"] == label for r in group),
                "accuracy": sum(r["prediction"] == label for r in group) / len(group),
            }
            for label, group in sorted(by_target.items())
        },
        "confusion": confusion_matrix(rows, labels),
    }


def board_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    selected = sum(len(row["selection"]) for row in rows)
    gold = sum(len(row["reference_selection"]) for row in rows)
    overlap = sum(len(set(row["selection"]) & set(row["reference_selection"])) for row in rows)
    return {
        "count": count,
        "board_exact_count": sum(row["board_exact_recomputed"] for row in rows),
        "board_exact_rate": sum(row["board_exact_recomputed"] for row in rows) / count if count else None,
        "zero_target_count": sum(not row["reference_selection"] for row in rows),
        "selected_tiles": selected,
        "gold_positive_tiles": gold,
        "true_positive_tiles": overlap,
        "tile_pooled_precision": overlap / selected if selected else (1.0 if gold == 0 else 0.0),
        "tile_pooled_recall": overlap / gold if gold else (1.0 if selected == 0 else 0.0),
        "mean_tile_precision": statistics.fmean(row["tile_precision_recomputed"] for row in rows) if rows else None,
        "mean_tile_recall": statistics.fmean(row["tile_recall_recomputed"] for row in rows) if rows else None,
    }


def check_sample_rows(
    rows: list[dict[str, Any]], samples: list[dict[str, Any]], labels: list[str], model: str,
    source_classes: dict[str, list[str]],
) -> list[dict[str, Any]]:
    expected = {sample["id"]: sample for sample in samples}
    got_ids = [row.get("id") for row in rows if row.get("type") == "sample"]
    require(len(got_ids) == EXPECTED_SAMPLES,
            f"{model}: expected {EXPECTED_SAMPLES} sample rows, got {len(got_ids)}")
    require(len(set(got_ids)) == len(got_ids), f"{model}: duplicate sample IDs in JSONL")
    require(set(got_ids) == set(expected), f"{model}: sample ID set differs from pinned manifest")
    verified = []
    for row in rows:
        if row.get("type") != "sample":
            continue
        ref = expected[row["id"]]
        require(row.get("label") == ref.get("label"), f"{model}/{row['id']}: label mismatch")
        require(row.get("source") == ref.get("source"), f"{model}/{row['id']}: source mismatch")
        require(row.get("path") == ref.get("path"), f"{model}/{row['id']}: path mismatch")
        pred = row.get("prediction")
        require(pred in labels, f"{model}/{row['id']}: invalid prediction {pred!r}")
        require(row.get("correct") is (pred == ref["label"]),
                f"{model}/{row['id']}: stored correct flag disagrees with pinned label")
        scores = row.get("scores")
        require(isinstance(scores, dict) and set(scores) == set(labels),
                f"{model}/{row['id']}: score keys differ from fixed class list")
        require(all(is_finite_number(value) for value in scores.values()),
                f"{model}/{row['id']}: non-finite class score")
        # Prediction is defined by the highest fixed-prompt similarity score.
        allowed = source_classes.get(ref['source'], labels)
        require(pred in allowed, f"{model}/{row['id']}: prediction is outside the source class set")
        max_score = max(float(scores[name]) for name in allowed)
        top = {name for name in allowed if math.isclose(float(scores[name]), max_score, rel_tol=0, abs_tol=1e-12)}
        require(pred in top, f"{model}/{row['id']}: prediction is not a maximum-scoring class")
        verified.append({**row, "correct_recomputed": pred == ref["label"]})
    return verified


def check_board_rows(
    rows: list[dict[str, Any]], cases: list[dict[str, Any]], model: str
) -> list[dict[str, Any]]:
    expected = {case["id"]: case for case in cases}
    got = [row for row in rows if row.get("type") == "board"]
    got_ids = [row.get("id") for row in got]
    require(len(got) == EXPECTED_BOARDS, f"{model}: expected {EXPECTED_BOARDS} board rows, got {len(got)}")
    require(len(set(got_ids)) == len(got_ids), f"{model}: duplicate board IDs in JSONL")
    require(set(got_ids) == set(expected), f"{model}: board ID set differs from pinned manifest")
    verified = []
    for row in got:
        ref = expected[row["id"]]
        for key in ("target", "source", "source_type", "reference_type", "selection_rule"):
            require(row.get(key) == ref.get(key), f"{model}/{row['id']}: {key} mismatch")
        gold = ref.get("gold_selected_indices", ref.get("reference_selection"))
        original_reference = ref.get("reference_selection", gold)
        require(gold == original_reference, f"Manifest conflict: {row['id']} gold indices differ from reference selection")
        # Assert artifact references equal the original references exactly; never normalize or repair them.
        require(row.get("reference_selection") == original_reference,
                f"{model}/{row['id']}: artifact reference differs from manifest; references are immutable")
        selected = row.get("selection")
        margins = row.get("margins")
        require(isinstance(selected, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in selected),
                f"{model}/{row['id']}: selection must be an integer list")
        require(len(set(selected)) == len(selected), f"{model}/{row['id']}: duplicate selected index")
        tile_count = ref.get("tile_count")
        require(isinstance(tile_count, int) and tile_count > 0, f"Manifest case {row['id']} has invalid tile_count")
        require(all(0 <= i < tile_count for i in selected), f"{model}/{row['id']}: selected index out of range")
        require(isinstance(margins, list) and len(margins) == tile_count,
                f"{model}/{row['id']}: margin count differs from tile count")
        require(all(is_finite_number(x) for x in margins), f"{model}/{row['id']}: non-finite margin")
        rule = ref["selection_rule"]
        if rule == "select_all_margin_positive":
            chosen = [i for i, margin in enumerate(margins) if margin > 0]
        elif rule == "known_count_topk":
            count = ref.get("requested_count")
            require(isinstance(count, int) and 1 <= count <= tile_count,
                    f"Manifest case {row['id']} has invalid requested_count")
            chosen = sorted(sorted(range(tile_count), key=lambda i: margins[i], reverse=True)[:count])
        else:
            raise IntegrityError(f"Manifest case {row['id']} has unsupported selection_rule {rule!r}")
        require(selected == chosen, f"{model}/{row['id']}: selection is inconsistent with margins/rule")
        overlap = len(set(selected) & set(original_reference))
        precision = overlap / len(selected) if selected else (1.0 if not original_reference else 0.0)
        recall = overlap / len(original_reference) if original_reference else (1.0 if not selected else 0.0)
        exact = selected == original_reference
        require(row.get("board_exact") is exact, f"{model}/{row['id']}: stored exact flag differs from original gold")
        require(row.get("positive_overlap") == overlap, f"{model}/{row['id']}: overlap mismatch")
        same_float(row.get("tile_precision"), precision, f"{model}/{row['id']} precision")
        same_float(row.get("tile_recall"), recall, f"{model}/{row['id']} recall")
        verified.append({**row, "reference_selection": original_reference,
                         "board_exact_recomputed": exact,
                         "tile_precision_recomputed": precision,
                         "tile_recall_recomputed": recall})
    return verified


def check_aggregate(aggregate: dict[str, Any], protocol: dict[str, Any], model: str,
                    manifest_sha: str, labels: list[str]) -> dict[str, Any]:
    aggregate_protocol = aggregate.get("protocol", aggregate)
    for source, name in ((aggregate_protocol, "aggregate protocol"), (protocol, "run_protocol")):
        sha = source.get("input_sha256")
        require(sha == manifest_sha, f"{model}: {name} manifest SHA mismatch: {sha!r}")
        require(source.get("sample_count") == EXPECTED_SAMPLES,
                f"{model}: {name} sample_count mismatch")
        require(source.get("board_count") == EXPECTED_BOARDS,
                f"{model}: {name} board_count mismatch")
        require(source.get("class_labels") == labels, f"{model}: {name} fixed classes mismatch")
    models = aggregate.get("models")
    require(isinstance(models, list) and len(models) == 1, f"{model}: aggregate must contain exactly one model")
    summary = models[0]
    require(summary.get("model") == model, f"Model directory says {model}, aggregate says {summary.get('model')!r}")
    require(protocol.get("models") == [model], f"{model}: run_protocol model mismatch")
    for key, expected in (("samples", EXPECTED_SAMPLES), ("boards", EXPECTED_BOARDS)):
        require(summary.get(key) == expected, f"{model}: aggregate model {key} mismatch")
    return summary


def verify_model(artifacts: Path, slug: str, model: str, manifest: dict[str, Any],
                 manifest_sha: str, labels: list[str]) -> dict[str, Any]:
    folder = artifacts / f"public-eval-result-{slug}"
    require(folder.is_dir(), f"Missing model result folder: {folder}")
    aggregate = read_json(folder / "aggregate.json")
    protocol = read_json(folder / "run_protocol.json")
    model_summary = check_aggregate(aggregate, protocol, model, manifest_sha, labels)
    jsonl_name = model_summary.get("sample_scores_jsonl", f"{model}.jsonl")
    require(Path(jsonl_name).name == jsonl_name, f"{model}: unsafe JSONL file name")
    rows = jsonl_rows(folder / jsonl_name)
    sample_rows = check_sample_rows(rows, manifest["samples"], labels, model,
                                   manifest.get('source_classes', {}))
    board_rows = check_board_rows(rows, manifest["cases"], model)
    sm = sample_metrics(sample_rows, labels)
    bm = board_metrics(board_rows)
    require(bm["zero_target_count"] == 10,
            f"{model}: expected 10 zero-target boards, got {bm['zero_target_count']}")
    same_float(model_summary.get("accuracy"), sm["accuracy"], f"{model} aggregate accuracy")
    by_source_type = collections.defaultdict(list)
    for row in board_rows:
        by_source_type[row["source_type"]].append(row)
    type_metrics = {kind: board_metrics(group) for kind, group in sorted(by_source_type.items())}
    require({k: v["count"] for k, v in type_metrics.items()} == EXPECTED_TYPES,
            f"{model}: board source_type counts differ from expected {EXPECTED_TYPES}: "
            f"{ {k: v['count'] for k, v in type_metrics.items()} }")
    by_target: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in board_rows:
        by_target[row["target"]].append(row)
    target_metrics = {target: board_metrics(group) for target, group in sorted(by_target.items())}

    # Conflicts refer to source-board IDs; match via their exact pixel hashes without changing gold.
    conflicts = read_json(Path(manifest["_run_dir"]) / "dedup_report.json").get("label_conflicts", [])
    conflict_pixels = {x.get("pixel_sha256") for x in conflicts if x.get("pixel_sha256")}
    conflict_board_ids = set()
    for case in manifest["cases"]:
        if conflict_pixels.intersection(case.get("tile_pixel_sha256", [])):
            conflict_board_ids.add(case["id"])
    require(len(conflict_board_ids) == 2,
            f"Expected 2 board IDs touched by annotation conflicts, got {sorted(conflict_board_ids)}")
    clean = [row for row in board_rows if row["id"] not in conflict_board_ids]
    require(len(clean) == 998, f"Expected clean sensitivity 998 boards, got {len(clean)}")
    clean_by_type: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in clean:
        clean_by_type[row["source_type"]].append(row)
    # Keep every original reference in the main result; this is a separate sensitivity view only.
    return {
        "model": model,
        "artifact_folder": str(folder),
        "verified_sample_count": len(sample_rows),
        "verified_board_count": len(board_rows),
        "manifest_sha256": manifest_sha,
        "model_metrics": model_summary,
        "sample_metrics": sm,
        "hcaptcha_sample_count": len(sample_rows),
        "hcaptcha_eight_label_space": sorted({r["label"] for r in sample_rows}),
        "hcaptcha_confusion_8_true_by_16_predicted": sm["confusion"],
        "board_metrics": bm,
        "boards_by_source_type": type_metrics,
        "boards_by_target": target_metrics,
        "label_conflict_board_ids_excluded_only_for_sensitivity": sorted(conflict_board_ids),
        "clean_998_sensitivity": {
            "overall": board_metrics(clean),
            "by_source_type": {kind: board_metrics(group) for kind, group in sorted(clean_by_type.items())},
        },
    }


def source_context(run: Path) -> dict[str, Any]:
    source_meta_path = run / "source_metadata" / "source_metadata.json"
    source_meta = read_json(source_meta_path)
    licensing = {}
    for source in source_meta.get("sources", []):
        repo = source.get("repo")
        license_file = run / "source_metadata" / f"{repo.replace('/', '_')}-LICENSE"
        license_text = license_file.read_text(encoding="utf-8", errors="replace") if license_file.exists() else ""
        if repo == "orlov-ai/hcaptcha-dataset":
            license_note = "Repository LICENSE is MIT; image/source rights and fair-use status are not independently established."
            url = f"https://github.com/{repo}/tree/{source['commit']}"
        elif repo == "ssivakorn/reCAPTCHA-study":
            license_note = "CC BY-NC 4.0 (repository LICENSE)."
            url = f"https://github.com/{repo}/tree/{source['commit']}"
        else:
            license_note = license_text[:120].strip()
            url = source.get("tree_url")
        licensing[repo] = {
            "url": url,
            "commit": source.get("commit"),
            "license_note": license_note,
            "license_file": str(license_file) if license_file.exists() else None,
        }
    return {"created_utc": source_meta.get("created_utc"), "sources": licensing,
            "retrieval_results": source_meta.get("retrieval_results")}


def optional_related_context(run: Path) -> dict[str, Any]:
    parent = run.parent
    text_path = parent / TEXT_RUN_NAME / "metrics.json"
    text_result = None
    if text_path.is_file():
        data = read_json(text_path)
        text_result = {
            "path": str(text_path),
            "by_source": data.get("by_source"),
            "kaggle_original_test": data.get("kaggle_original_split_counts_and_metrics", {}).get("test"),
            "provenance_note": "Separate OCR-only run; pretrained OCR training-set overlap remains unknown.",
        }
    live_path = parent / LIVE_RUN_NAME
    live_result = None
    if (live_path / "summary.json").is_file() and (live_path / "tinyclip40_predictions.json").is_file():
        summary = read_json(live_path / "summary.json")
        predictions = read_json(live_path / "tinyclip40_predictions.json")
        provenance = read_json(live_path / "tinyclip40_provenance.json")
        comparison = provenance.get("ocr_manual_instruction_comparison", {})
        live_result = {
            "path": str(live_path),
            "summary_path": str(live_path / "summary.json"),
            "prediction_path": str(live_path / "tinyclip40_predictions.json"),
            "ocr_comparison_path": str(live_path / "tinyclip40_provenance.json"),
            "requested": summary.get("requested"),
            "attempted": summary.get("attempted"),
            "board_predictions": len(predictions.get("predictions", [])),
            "answer_submissions": 0 if provenance.get("no_answer_submission") else None,
            "manual_instruction_ocr_exact": comparison.get("exact_matches"),
            "manual_instruction_ocr_total": comparison.get("total"),
            "scope": "Separate 4get observation-only dataset; image model predictions were not followed by answer submission or acceptance validation.",
        }
    return {"text_ocr": text_result, "fourget_observation": live_result}


def timing_and_hardware(artifacts: Path, models: dict[str, Any]) -> dict[str, Any]:
    env_path = artifacts / "environment.json"
    require(env_path.is_file(), f"Missing hardware metadata: {env_path}")
    env = read_json(env_path)
    summarized = {}
    for model, data in models.items():
        raw = data["model_metrics"]
        prep = raw.get("preprocessing_ms_total")
        encoder = raw.get("image_encoder_and_scoring_ms_total")
        require(is_finite_number(prep) and is_finite_number(encoder),
                f"{model}: preprocessing/encoder timing unavailable")
        summarized[model] = {
            "device": raw.get("device"),
            "total_parameters": raw.get("total_parameters"),
            "image_parameters": raw.get("image_parameters"),
            "model_load_ms": raw.get("load_ms"),
            "cached_text_embedding_ms": raw.get("cached_text_embedding_ms"),
            "preprocessing_total_ms_single_pass": prep,
            "image_encoder_and_scoring_total_ms_single_pass": encoder,
            "preprocessing_plus_encoder_scoring_total_ms_single_pass": prep + encoder,
            "cuda_peak_allocated_mib": raw.get("cuda_peak_allocated_mib"),
            "cuda_peak_reserved_mib": raw.get("cuda_peak_reserved_mib"),
            "unique_images_encoded": raw.get("unique_images_encoded"),
        }
    return {"environment": env, "environment_path": str(env_path), "models": summarized,
            "timing_note": "Timings are reported single-pass totals from the run artifacts; they are not repeated-run medians."}


def build_report(report: dict[str, Any]) -> str:
    lines = [
        "# 公開CAPTCHA画像のモデル評価",
        "",
        "同じ固定プロンプトで4モデルを実行し、4068枚の公開ラベル付き画像と1000問の公開reCAPTCHAボード注釈を検証しました。この実験でモデル学習は行っていません。各出力のID、ラベル、選択、スコア、参照、マニフェストSHAを再照合しています。",
        "",
    ]
    runtime_errors = report.get("source_job_runtime_errors", {})
    if runtime_errors:
        moe = runtime_errors.get("MoE-ViE-B16", {})
        lines += [
            "> **実行上のエラー:** MoE-ViE-B16 の元Kaggle subprocessは、482/482 batchを処理し `model_complete` を出力した後に1200秒timeoutとなり、Notebook jobはerrorのままです。保存済み出力4モデルは厳密なオフライン検証に合格しましたが、この検証は元のtimeoutやNotebook errorを成功に変更しません。",
            "",
        ]
    lines += [
        "## 画像分類（hCaptcha由来の4068画像）",
        "",
        "各モデルの正解率はマニフェストの画像ラベルから再計算しました。混同行列は8つの真の対象ラベルと16クラスのモデル出力を保存します。",
        "",
        "| モデル | 正解 / 4068 | 正解率 |",
        "|---|---:|---:|",
    ]
    for model, item in report["models"].items():
        sm = item["sample_metrics"]
        lines.append(f"| {model} | {sm['correct']} / {sm['count']} | {sm['accuracy']:.1%} |")
    lines += ["", "## ボード注釈との照合（1000ボード）", "",
              "参照は入力マニフェストのまま保持し、モデルの選択とmarginから完全一致・タイル適合率・再現率を再計算しました。Type A/Bの選択ルールは`select_all_margin_positive`です。これは公開注釈との照合で、サーバー受理率やCAPTCHA通過率ではありません。", "",
              "| モデル | 集計 | ボード完全一致 | pooled tile P/R | zero-target |",
              "|---|---|---:|---:|---:|"]
    for model, item in report["models"].items():
        for label, metrics in [("全1000", item["board_metrics"]),
                               ("Type A", item["boards_by_source_type"]["Type A"]),
                               ("Type B", item["boards_by_source_type"]["Type B"]),
                               ("競合2問除外・998感度", item["clean_998_sensitivity"]["overall"])]:
            p = metrics["tile_pooled_precision"]
            r = metrics["tile_pooled_recall"]
            lines.append(f"| {model} | {label} ({metrics['count']}) | {metrics['board_exact_count']}/{metrics['count']} ({metrics['board_exact_rate']:.1%}) | {p:.1%} / {r:.1%} | {metrics['zero_target_count']} |")
    lines += ["", "対象別の件数、完全一致、タイル適合率・再現率、hCaptcha 8ラベル混同行列、Type A/Bの998件感度値は`metrics.json`を参照してください。重複画像のラベル競合に触れる2ボードは、通常の1000問の集計に残したまま、追加感度集計だけから除外しています。参照ラベルは書き換えていません。", "",
              "## 実行時間とメモリ", "", report["performance"]["timing_note"], "",
              "| モデル | encoder画像側params / 全params | load ms | cached text ms | preprocess + encoder/scoring 合計ms | CUDA allocated / reserved MiB |",
              "|---|---:|---:|---:|---:|---:|"]
    for model, d in report["performance"]["models"].items():
        lines.append(f"| {model} | {d['image_parameters']} / {d['total_parameters']} | {d['model_load_ms']:.1f} | {d['cached_text_embedding_ms']:.1f} | {d['preprocessing_plus_encoder_scoring_total_ms_single_pass']:.1f} | {d['cuda_peak_allocated_mib']} / {d['cuda_peak_reserved_mib']} |")
    environment = report["performance"]["environment"]
    torch_info = environment.get("torch")
    if isinstance(torch_info, dict):
        gpu_name = torch_info.get("gpu_name", environment.get("gpu_name", "unknown GPU"))
        cuda_runtime = torch_info.get("cuda_runtime", environment.get("cuda_runtime", "unknown"))
        torch_version = torch_info.get("version", environment.get("torch_version", "unknown"))
    else:
        gpu_name = environment.get("gpu_name", "unknown GPU")
        cuda_runtime = environment.get("cuda_runtime", "unknown")
        torch_version = torch_info or environment.get("torch_version", "unknown")
    lines += ["", f"ハードウェア: {gpu_name}, CUDA {cuda_runtime}, PyTorch {torch_version}; 詳細は`verification.json`のenvironmentを参照。"]
    run_provenance = environment.get("model_run_provenance")
    provenance_rows = []
    if isinstance(run_provenance, dict):
        provenance_rows = list(run_provenance.items())
    elif isinstance(run_provenance, list):
        provenance_rows = [(str(index + 1), item) for index, item in enumerate(run_provenance)]
    provenance_lines = []
    for label, item in provenance_rows:
        if isinstance(item, dict):
            models = item.get("models")
            model_names = ", ".join(str(name) for name in models) if isinstance(models, list) else item.get("model", label)
            ref = item.get("job_ref", item.get("ref", "unknown Kaggle job"))
            version = item.get("version", "unknown")
            status = item.get("status")
            suffix = f" ({status})" if status else ""
            provenance_lines.append(f"- {model_names}: `{ref}` version {version}{suffix}.")
        else:
            provenance_lines.append(f"- {label}: {item}.")
    if provenance_lines:
        lines += ["", "Model artifacts came from separate Kaggle jobs:", *provenance_lines]
    lines += ["",
              "## 出典と限界", "",
              "* reCAPTCHAボード: [ssivakorn/reCAPTCHA-study](https://github.com/ssivakorn/reCAPTCHA-study)（固定commit; CC BY-NC 4.0）。公開注釈と保存スナップショットの評価です。完全なブラウザーセッションや現在のサービス動作を再現しません。",
              "* hCaptcha由来タイル: [orlov-ai/hcaptcha-dataset](https://github.com/orlov-ai/hcaptcha-dataset)（固定commit; repository LICENSEはMIT）。画像の出所・権利やfair-use statusは確認できていません。",
              "* 固定8/16ラベル分類はこの画像集合の分類指標であり、ボード単位の受理や未知クラス認識を示しません。両画像ソースと事前学習モデルの学習データ重複は不明です。",
              "* 別の4get観察runでは20問の画像モデル予測、回答送信0件、手書き指示とのOCR一致17/20を記録しています。これはライブな受理判定ではなく、本レポートの公開注釈評価とは別の単位です。関連ファイルは`related_context`と元runのsummary/prediction/provenanceです。"]
    text = report.get("related_context", {}).get("text_ocr")
    if text:
        lines += ["", f"文字OCRの別評価: Project Sloth 2000枚 {text['by_source'].get('project_sloth_captcha_images_test', {}).get('literal_exact_match', float('nan')):.1%}、Kaggle 1070枚 {text['by_source'].get('kaggle_fournierp_captcha_version_2', {}).get('literal_exact_match', float('nan')):.1%}、Kaggle元test 214枚 {text.get('kaggle_original_test', {}).get('literal_exact_match', float('nan')):.1%}。これは画像分類モデルの指標と合算しません。"]
    return "\n".join(lines) + "\n"


def summarize(run_dir: Path, artifacts_dir: Path, output_dir: Path) -> dict[str, Any]:
    run = run_dir.resolve()
    artifacts = artifacts_dir.resolve()
    output = output_dir.resolve()
    require(run.is_dir(), f"Run directory not found: {run}")
    require(artifacts.is_dir(), f"Artifacts directory not found: {artifacts}")
    require(not output.exists(), f"Refusing to overwrite existing output directory: {output}")
    manifest_path = run / "public_full.json"
    manifest = read_json(manifest_path)
    require(len(manifest.get("samples", [])) == EXPECTED_SAMPLES,
            f"Pinned manifest sample count mismatch: {len(manifest.get('samples', []))}")
    require(len(manifest.get("cases", [])) == EXPECTED_BOARDS,
            f"Pinned manifest board count mismatch: {len(manifest.get('cases', []))}")
    labels = [item["label"] for item in manifest.get("classes", [])]
    require(len(labels) == 16 and len(set(labels)) == 16, "Pinned manifest must contain 16 unique fixed classes")
    sample_ids = [item.get("id") for item in manifest["samples"]]
    case_ids = [item.get("id") for item in manifest["cases"]]
    require(None not in sample_ids and len(set(sample_ids)) == EXPECTED_SAMPLES, "Duplicate/missing manifest sample IDs")
    require(None not in case_ids and len(set(case_ids)) == EXPECTED_BOARDS, "Duplicate/missing manifest board IDs")
    manifest_sha = sha256_file(manifest_path)
    # Attach only in memory for matching annotation conflict hashes.
    manifest["_run_dir"] = str(run)
    results = {}
    for slug, model in EXPECTED_MODELS.items():
        results[model] = verify_model(artifacts, slug, model, manifest, manifest_sha, labels)
    environment_metrics = timing_and_hardware(artifacts, results)
    report = {
        "run_dir": str(run),
        "artifacts_dir": str(artifacts),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "sample_count": EXPECTED_SAMPLES,
        "board_count": EXPECTED_BOARDS,
        "fixed_class_labels": labels,
        "verified_model_count": len(results),
        "models": results,
        "performance": environment_metrics,
        "sources": source_context(run),
        "related_context": optional_related_context(run),
        "limitations": [
            "References and labels are public annotations; none are server-side acceptance results.",
            "Type A/B boards are saved public snapshots rather than complete, dynamic user sessions.",
            "Classification uses the source's 8 allowed hCaptcha labels; the stored score vector has 16 labels. Classification accuracy is not board selection accuracy.",
            "Pretrained-model exposure to either image source is unknown.",
        ],
        "integrity": "passed",
        "artifact_verification_status": "all_four_models_verified_with_source_runtime_error"
            if read_json(artifacts / "public-eval-summary.json").get("source_job_runtime_errors")
            else "all_four_models_verified",
        "source_job_runtime_errors": read_json(artifacts / "public-eval-summary.json").get("source_job_runtime_errors", {}),
    }
    # Output is created only after all model files and metadata passed validation.
    output.mkdir(parents=True)
    (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics = {"manifest_sha256": manifest_sha, "models": {
        name: {key: item[key] for key in (
            "sample_metrics", "hcaptcha_sample_count", "hcaptcha_eight_label_space",
            "hcaptcha_confusion_8_true_by_16_predicted", "board_metrics",
            "boards_by_source_type", "boards_by_target",
            "label_conflict_board_ids_excluded_only_for_sensitivity", "clean_998_sensitivity",
        )}
        for name, item in results.items()
    }, "performance": environment_metrics, "sources": report["sources"], "related_context": report["related_context"]}
    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "REPORT.md").write_text(build_report(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="run folder containing public_full.json and source metadata")
    parser.add_argument("--artifacts", type=Path, required=True, help="extracted GPU artifacts folder with four result directories")
    parser.add_argument("--output-dir", type=Path, required=True, help="new folder for verification.json, metrics.json, REPORT.md")
    args = parser.parse_args()
    try:
        report = summarize(args.run, args.artifacts, args.output_dir)
    except IntegrityError as exc:
        print(f"integrity error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except FileExistsError as exc:
        print(f"output error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps({"status": "verified", "models": report["verified_model_count"],
                      "samples": report["sample_count"], "boards": report["board_count"],
                      "output_dir": str(args.output_dir.resolve())}))


if __name__ == "__main__":
    main()
