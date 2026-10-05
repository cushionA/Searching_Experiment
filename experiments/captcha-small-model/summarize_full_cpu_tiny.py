#!/usr/bin/env python3
"""Independently verify and summarize the full public-set TinyCLIP CPU run."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

import summarize_public_eval as shared


MODEL = "TinyCLIP-ViT-40M-32-Text-19M"
SLUG = "tinyclip_vit_40m_32_text_19m"
EXPECTED_SAMPLE_COUNT = 4068
EXPECTED_BOARD_COUNT = 1000
EXPECTED_TYPE_COUNTS = {"Type A": 662, "Type B": 338}


def read_json(path: Path) -> Any:
    return shared.read_json(path)


def write_new(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.write_text(text, encoding="utf-8")


def percent(value: float) -> str:
    return f"{100.0 * value:.2f}%"


def render_report(metrics: dict[str, Any]) -> str:
    summary = metrics["model"]
    sample = metrics["sample_metrics"]
    boards = metrics["board_metrics"]
    lines = [
        "# TinyCLIP全件CPU評価：独立検証",
        "",
        f"入力は`public_full.json`（SHA-256 `{metrics['manifest_sha256']}`）です。hCaptcha由来のラベル付き画像{metrics['sample_count']}枚と、公開reCAPTCHAの人手注釈board {metrics['board_count']}件を評価しました。学習は行っていません。",
        "",
        f"実行条件はCPU {metrics['protocol']['threads']}スレッド、FP32、{metrics['protocol']['memory_format']}、batch size {metrics['protocol']['batch_size']}です。warmupを除いた1回の計測で、画像encoderと類似度計算、前処理、モデル読み込み、キャッシュ済みテキスト特徴の時間を分けて記録しています。",
        "",
        "## hCaptcha画像分類",
        "",
        f"正解は{sample['correct']}/{sample['count']}（{percent(sample['accuracy'])}）、8クラスのmacro平均は{percent(metrics['hcaptcha_macro_accuracy'])}です。予測はsource whitelistで許可された8クラス内のargmaxです。",
        "",
        "| クラス | 正解 / 件数 | 正解率 |",
        "|---|---:|---:|",
    ]
    for label, item in metrics["hcaptcha_by_label"].items():
        lines.append(f"| {label} | {item['correct']} / {item['count']} | {percent(item['accuracy'])} |")
    lines += [
        "",
        "## 公開reCAPTCHA board注釈との照合",
        "",
        f"marginが0を超える全タイルを選ぶ固定ルールで、board完全一致は{boards['board_exact_count']}/{boards['count']}（{percent(boards['board_exact_rate'])}）でした。参照選択は元manifestのまま照合し、注釈競合に触れる2 boardを除外した感度集計も別に算出しています。",
        "",
        "| Board群 | 件数 | 完全一致 | Tile pooled precision / recall |",
        "|---|---:|---:|---:|",
    ]
    for label, item in [("全件", boards), *sorted(metrics["boards_by_source_type"].items()),
                        ("競合2件を除外した感度集計", metrics["clean_998_sensitivity"]["overall"])]:
        lines.append(f"| {label} | {item['count']} | {item['board_exact_count']}/{item['count']} ({percent(item['board_exact_rate'])}) | {percent(item['tile_pooled_precision'])} / {percent(item['tile_pooled_recall'])} |")
    lines += ["", "### 対象クラス別board完全一致", "", "| 対象クラス | 件数 | 完全一致 |", "|---|---:|---:|"]
    for target, item in metrics["boards_by_target"].items():
        lines.append(f"| {target} | {item['count']} | {item['board_exact_count']}/{item['count']} ({percent(item['board_exact_rate'])}) |")
    lines += [
        "",
        "## CPU実行時間",
        "",
        f"| モデル読み込み | テキスト特徴 | 前処理 | 画像encoder＋類似度計算 | 合計（全4区分） |",
        "|---:|---:|---:|---:|---:|",
        f"| {summary['load_ms']/1000:.2f}秒 | {summary['cached_text_embedding_ms']/1000:.2f}秒 | {summary['preprocessing_ms_total']/1000:.2f}秒 | {summary['image_encoder_and_scoring_ms_total']/1000:.2f}秒 | {metrics['total_recorded_seconds']:.2f}秒 |",
        "",
        "## 検証と限界",
        "",
        "独立検証では、入力manifest SHA、サンプル4068件とboard 1000件のID、各画像のsource whitelist、有限なスコアとmargin、全margin>0タイルの選択、参照との一致、集計値を確認しました。競合する二つのbicycleタイル注釈に関係するboardは通常の全件集計に残し、感度集計だけから除外しています。詳細な数値は`metrics.json`、検証ログは`verification.json`にあります。",
        "",
        f"全件のhCaptcha画像はクラス頻度に偏りがあります。800枚のCPU subsetは各クラス100枚ずつでした。board構成も全件ではType A {metrics['board_type_counts']['Type A']}件 / Type B {metrics['board_type_counts']['Type B']}件ですが、120問のCPU subsetはType A 53件 / Type B 67件でした。このため両runの総合率を単純比較できません。subsetとの数値比較は`metrics.json`に記録しています。",
        "",
        "hCaptcha分類は出典で許可した8クラスのclosed-world評価で、背景・未知クラスを含みません。reCAPTCHA参照は公開人手注釈で、サーバー受理結果ではありません。保存されたboardは動的な完全セッションではありません。事前学習データと公開画像の重複、hCaptcha画像の再利用権も確認できていません。CPU時間からGPU速度は推定できません。",
        "",
    ]
    return "\n".join(lines)


def summarize(run: Path, artifacts: Path, output_dir: Path) -> dict[str, Any]:
    run, artifacts, output_dir = run.resolve(), artifacts.resolve(), output_dir.resolve()
    shared.require(run.is_dir(), f"Missing run folder: {run}")
    shared.require(artifacts.is_dir(), f"Missing CPU artifacts: {artifacts}")
    shared.require(not output_dir.exists(), f"Refusing to overwrite: {output_dir}")
    manifest_path = run / "public_full.json"
    manifest = read_json(manifest_path)
    manifest_sha = shared.sha256_file(manifest_path)
    samples, cases = manifest.get("samples", []), manifest.get("cases", [])
    labels = [item["label"] for item in manifest.get("classes", [])]
    shared.require(len(samples) == EXPECTED_SAMPLE_COUNT, f"Expected {EXPECTED_SAMPLE_COUNT} samples, got {len(samples)}")
    shared.require(len(cases) == EXPECTED_BOARD_COUNT, f"Expected {EXPECTED_BOARD_COUNT} boards, got {len(cases)}")
    shared.require(len(labels) == 16 and len(set(labels)) == 16, "Expected 16 unique prompt classes")
    sample_ids = [item.get("id") for item in samples]
    case_ids = [item.get("id") for item in cases]
    shared.require(None not in sample_ids and len(set(sample_ids)) == EXPECTED_SAMPLE_COUNT, "Sample IDs are missing or duplicated")
    shared.require(None not in case_ids and len(set(case_ids)) == EXPECTED_BOARD_COUNT, "Board IDs are missing or duplicated")
    shared.require(all(c.get("correct_answers") == c.get("reference_selection") == c.get("gold_selected_indices") for c in cases),
                    "One or more source correct_answers/reference fields disagree")
    source_classes = manifest.get("source_classes", {})
    hcaptcha_allowed = source_classes.get("orlov-ai/hcaptcha-dataset", [])
    hcaptcha_counts = collections.Counter(s["label"] for s in samples)
    shared.require(len(hcaptcha_allowed) == 8 and set(hcaptcha_counts) == set(hcaptcha_allowed), "Unexpected HCaptcha class allowlist")
    board_type_counts = dict(collections.Counter(c["source_type"] for c in cases))
    shared.require(board_type_counts == EXPECTED_TYPE_COUNTS, f"Unexpected board type composition: {board_type_counts}")
    protocol = read_json(artifacts / "run_protocol.json")
    aggregate = read_json(artifacts / "aggregate.json")
    shared.require(protocol.get("input_sha256") == manifest_sha, "run_protocol manifest SHA mismatch")
    shared.require(protocol.get("device") == "cpu" and protocol.get("threads") == 2, "Unexpected CPU/thread protocol")
    shared.require(protocol.get("precision") == "fp32" and protocol.get("memory_format") == "channels_last", "Unexpected precision/layout")
    shared.require(protocol.get("sample_count") == EXPECTED_SAMPLE_COUNT and protocol.get("board_count") == EXPECTED_BOARD_COUNT,
                    "run_protocol item counts mismatch")
    model_summary = shared.check_aggregate(aggregate, protocol, MODEL, manifest_sha, labels)
    shared.require(aggregate["protocol"].get("models") == [MODEL], "Aggregate model list mismatch")
    rows = shared.jsonl_rows(artifacts / f"{MODEL}.jsonl")
    sample_rows = shared.check_sample_rows(rows, samples, labels, MODEL, source_classes)
    board_rows = shared.check_board_rows(rows, cases, MODEL)
    sm, bm = shared.sample_metrics(sample_rows, labels), shared.board_metrics(board_rows)
    shared.same_float(model_summary.get("accuracy"), sm["accuracy"], "Aggregate sample accuracy")
    # 15,434 source references minus 14 exact repeated tiles = 15,420 encodings.
    shared.require(model_summary.get("unique_images_encoded") == 15420, "Unexpected unique image count")
    shared.require(bm["count"] == EXPECTED_BOARD_COUNT, "Board count mismatch")
    by_type_rows: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    by_target_rows: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in board_rows:
        by_type_rows[row["source_type"]].append(row)
        by_target_rows[row["target"]].append(row)
    by_type = {k: shared.board_metrics(v) for k, v in sorted(by_type_rows.items())}
    by_target = {k: shared.board_metrics(v) for k, v in sorted(by_target_rows.items())}
    shared.require({k: v["count"] for k, v in by_type.items()} == EXPECTED_TYPE_COUNTS, "Verified Type A/B counts mismatch")

    dedup = read_json(run / "dedup_report.json")
    conflict_pixels = {x["pixel_sha256"] for x in dedup.get("label_conflicts", [])}
    conflict_case_ids = sorted(c["id"] for c in cases if conflict_pixels.intersection(c.get("tile_pixel_sha256", [])))
    shared.require(len(conflict_pixels) == 2 and len(conflict_case_ids) == 2, f"Expected 2 conflicted pixels/boards, got {len(conflict_pixels)}/{len(conflict_case_ids)}")
    clean_rows = [r for r in board_rows if r["id"] not in set(conflict_case_ids)]
    shared.require(len(clean_rows) == 998, "Conflict-excluded sensitivity must include 998 boards")
    clean_by_type_rows: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in clean_rows:
        clean_by_type_rows[row["source_type"]].append(row)
    clean_sensitivity = {"overall": shared.board_metrics(clean_rows),
                         "by_source_type": {k: shared.board_metrics(v) for k, v in sorted(clean_by_type_rows.items())}}

    subset_path = run / "cpu-subset-summary" / "metrics.json"
    subset_comparison = None
    if subset_path.is_file():
        subset = read_json(subset_path)
        tiny_subset = next((x for x in subset.get("models", []) if x.get("model") == MODEL), None)
        if tiny_subset:
            subset_comparison = {
                "subset_hcaptcha_correct": tiny_subset["hcaptcha_correct"],
                "subset_hcaptcha_count": tiny_subset["hcaptcha_count"],
                "subset_hcaptcha_accuracy": tiny_subset["hcaptcha_accuracy"],
                "subset_boards_exact": tiny_subset["boards_exact"],
                "subset_boards_count": tiny_subset["boards_count"],
                "subset_board_exact_rate": tiny_subset["board_exact_rate"],
                "subset_hcaptcha_class_counts": subset["input"]["samples_per_class"],
                "subset_board_type_counts": subset["input"]["board_types"],
                "comparability_note": "Subset is balanced at 100 per HCaptcha class and stratified to 53 Type A / 67 Type B; full-set classes are imbalanced and its board composition is 662 Type A / 338 Type B. Aggregate rates are not a like-for-like comparison.",
            }

    timing_keys = ("load_ms", "cached_text_embedding_ms", "preprocessing_ms_total", "image_encoder_and_scoring_ms_total")
    for key in timing_keys:
        shared.require(shared.is_finite_number(model_summary.get(key)) and model_summary[key] >= 0, f"Invalid timing {key}")
    metrics = {
        "model": {"name": MODEL, "weights": model_summary.get("weights"), "total_parameters": model_summary.get("total_parameters"),
                  "image_parameters": model_summary.get("image_parameters"), "load_ms": model_summary["load_ms"],
                  "cached_text_embedding_ms": model_summary["cached_text_embedding_ms"],
                  "preprocessing_ms_total": model_summary["preprocessing_ms_total"],
                  "image_encoder_and_scoring_ms_total": model_summary["image_encoder_and_scoring_ms_total"],
                  "unique_images_encoded": model_summary["unique_images_encoded"]},
        "manifest_sha256": manifest_sha,
        "sample_count": len(samples), "board_count": len(cases),
        "protocol": {k: protocol[k] for k in ("device", "threads", "precision", "memory_format", "batch_size", "warmup_excluded", "timing_passes", "torch", "open_clip")},
        "hcaptcha_source": "orlov-ai/hcaptcha-dataset", "hcaptcha_allowed_labels": hcaptcha_allowed,
        "hcaptcha_label_counts": dict(sorted(hcaptcha_counts.items())),
        "hcaptcha_macro_accuracy": statistics.fmean(item["accuracy"] for item in sm["by_target"].values()),
        "hcaptcha_by_label": sm["by_target"], "sample_metrics": sm,
        "board_type_counts": board_type_counts, "board_metrics": bm,
        "boards_by_source_type": by_type, "boards_by_target": by_target,
        "conflicted_pixel_hashes": sorted(conflict_pixels), "conflicted_board_ids": conflict_case_ids,
        "clean_998_sensitivity": clean_sensitivity,
        "total_recorded_seconds": sum(model_summary[k] for k in timing_keys) / 1000.0,
        "cpu_subset_comparison": subset_comparison,
        "limitations": [
            "All 4068 samples and 1000 boards are from the fixed public evaluation sources; no train/validation split or new model training was performed.",
            "Sample prediction is restricted to the source's eight HCaptcha labels and has no background/unknown class.",
            "Board references are public human annotations, not live-server acceptance; the saved boards are snapshots, not full dynamic sessions.",
            "Two pairs of identical bicycle tiles have contradictory labels; unchanged full-set metrics retain both, with a separate 998-board sensitivity view.",
            "Comparison with the stratified CPU subset is not like-for-like due to different class and Type A/B distributions.",
            "Pretraining overlap and HCaptcha image reuse rights are unknown; CPU timing does not predict GPU latency.",
        ],
    }
    verification = {
        "status": "passed", "model": MODEL, "manifest_sha256": manifest_sha,
        "artifact_jsonl_rows": len(rows), "sample_rows_verified": len(sample_rows), "board_rows_verified": len(board_rows),
        "sample_ids_match_manifest": True, "board_ids_match_manifest": True,
        "correct_answers_reference_gold_exact_1000": True,
        "sample_predictions_obey_eight_class_source_whitelist": True,
        "sample_scores_and_board_margins_finite": True,
        "board_selection_recomputed_from_fixed_margin_rule": "select_all_margin_positive",
        "board_references_unchanged": True, "aggregate_accuracy_matches_independent_recount": True,
        "conflicted_annotation_board_ids": conflict_case_ids,
        "clean_sensitivity_board_count": len(clean_rows),
        "protocol": metrics["protocol"],
    }
    report_text = render_report(metrics)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite nonempty output folder: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_new(output_dir / "metrics.json", json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    write_new(output_dir / "verification.json", json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
    write_new(output_dir / "REPORT.md", report_text)
    return {"metrics": metrics, "verification": verification}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = summarize(args.run, args.artifacts, args.output_dir)
    except (shared.IntegrityError, FileExistsError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps({"status": "verified", "samples": EXPECTED_SAMPLE_COUNT,
                      "boards": EXPECTED_BOARD_COUNT, "output_dir": str(args.output_dir.resolve()),
                      "hcaptcha_accuracy": report["metrics"]["sample_metrics"]["accuracy"],
                      "board_exact_rate": report["metrics"]["board_metrics"]["board_exact_rate"]}))


if __name__ == "__main__":
    main()
