#!/usr/bin/env python3
"""Small, reproducible CPU-only GLiNER2.5 Decide SKU pair trial.

Use --download to fetch and SHA256-verify the pinned official Hugging Face snapshot,
then --run to classify the fixed synthetic Japanese cases into three supplied labels.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODEL_DIR = ROOT / ".deps" / "sku-gliner-model"
RESULT_PATH = HERE / "results" / "20261009-gliner-smoke.json"
REAL_PAIR_PATH = HERE / "results" / "real-original-pair.jsonl"
REPO = "fastino/GLiNER2.5-multi-Decide"
REVISION = "e173bca1f0c4217d7a55b3b0c705d5ece8a0218d"
FILES = {
    "model.safetensors": {
        "size_bytes": 1149461028,
        "sha256": "9efe0f88c99f2aa794452e9559dc60e98d60d9fa2bf1b60cf2710411b6da5b4e",
    },
    "tokenizer.json": {
        "size_bytes": 16035853,
        "sha256": "c62446df87ae18ec98b133f8f84fc449a07cc89bbf8ef192a4cb5f9c53777a7a",
    },
    "config.json": {
        "size_bytes": 4106,
        "sha256": "aa29cd584316b61b0bfed94d03ee1cfdaf28b46a37686293742929c49b8bd2f7",
    },
    "encoder_config/config.json": {
        "size_bytes": 858,
        "sha256": "d0ebbcb8b458e285a39e12cc315cbaf3d1c6f631e7281e6b22dd5b4071183f83",
    },
    "tokenizer_config.json": {
        "size_bytes": 646,
        "sha256": "fd4a31dc2f1f17e31638c5f0e783b81cdb2fbe6bddd116a8d9e5d50d78148cf1",
    },
}

LABELS = [
    "same SKU and same product variant",
    "different SKU or product variant",
    "needs human review",
]

# Controlled examples only. These labels are test expectations, not production gold.
CASES = [
    {
        "id": "same_exact_sku",
        "expected": LABELS[0],
        "text": "商品A: エレコム USB-C充電器 65W ブラック SKU ACDC-PD65BK。\n商品B: エレコム USB-C充電器 65W ブラック SKU ACDC-PD65BK。\n同一SKUか判定してください。",
    },
    {
        "id": "same_sku_reordered_title",
        "expected": LABELS[0],
        "text": "商品A: エレコム ACDC-PD65BK USB-C PD充電器、65W、ブラック。\n商品B: ブラック 65W USB-C PD充電器（エレコム）、SKU: ACDC-PD65BK。\n同一SKUか判定してください。",
    },
    {
        "id": "same_name_different_color_sku",
        "expected": LABELS[1],
        "text": "商品A: USB-C充電器 65W ブラック SKU ACDC-PD65BK。\n商品B: USB-C充電器 65W ホワイト SKU ACDC-PD65WH。\n色違い型番の同一SKUか判定してください。",
    },
    {
        "id": "wrong_curtain_height",
        "expected": LABELS[1],
        "text": "商品A: 1級遮光カーテン 幅100cm 丈178cm 2枚組 ベージュ SKU CT-100178-BE。\n商品B: 1級遮光カーテン 幅100cm 丈200cm 2枚組 ベージュ SKU CT-100200-BE。\n丈が違う商品を同一SKUとして扱えるか判定してください。",
    },
    {
        "id": "lace_vs_main_curtain",
        "expected": LABELS[1],
        "text": "商品A: 遮光ドレープカーテン 幅100×丈178cm 2枚組 SKU DR-100178。\n商品B: ミラーレースカーテン 幅100×丈176cm 2枚組 SKU LC-100176。\nセット商品ではなくレースとドレープの違いを見て同一SKUか判定してください。",
    },
    {
        "id": "piece_count_difference",
        "expected": LABELS[1],
        "text": "商品A: 遮光カーテン 幅100×丈135cm 1枚 SKU BK-100135-1。\n商品B: 遮光カーテン 幅100×丈135cm 2枚組 SKU BK-100135-2。\n枚数違いを見て同一SKUか判定してください。",
    },
    {
        "id": "near_color_difference",
        "expected": LABELS[1],
        "text": "商品A: カーテン 幅100×丈178cm グレージュ 2枚組 SKU GV-100178-GJ。\n商品B: カーテン 幅100×丈178cm グレー 2枚組 SKU GV-100178-GY。\n色違いを見て同一SKUか判定してください。",
    },
    {
        "id": "width_difference",
        "expected": LABELS[1],
        "text": "商品A: 遮光カーテン 幅100×丈200cm 2枚組 アイボリー SKU SH-100200-IV。\n商品B: 遮光カーテン 幅150×丈200cm 2枚組 アイボリー SKU SH-150200-IV。\n幅違いを見て同一SKUか判定してください。",
    },
    {
        "id": "similar_family_different_model",
        "expected": LABELS[1],
        "text": "商品A: カーテンレール 伸縮 1.1〜2.0m シングル SKU RAIL-S-20。\n商品B: カーテンレール 伸縮 1.1〜2.0m ダブル SKU RAIL-D-20。\n型番とシングル・ダブルの違いを見て同一SKUか判定してください。",
    },
    {
        "id": "same_sku_title_abbreviation",
        "expected": LABELS[0],
        "text": "商品A: 遮光カーテン 幅100×丈178cm 2枚入 ネイビー SKU CUR-100178-NV。\n商品B: CUR-100178-NV / 遮光カーテン / 100-178cm / NAVY / 2P。\nSKUが同じで属性も一致するため同一SKUか判定してください。",
    },
    {
        "id": "sku_conflicts_with_title",
        "expected": LABELS[2],
        "text": "商品A: カーテン 幅100×丈178cm ベージュ SKU CUR-100178-BE。\n商品B: カーテン 幅100×丈200cm ベージュ SKU CUR-100178-BE。\nSKU表記は同じですが丈が矛盾します。同一SKUか人手確認かを判定してください。",
    },
    {
        "id": "ambiguous_incomplete_title",
        "expected": LABELS[2],
        "text": "商品A: 遮光カーテン 型番 UNKNOWN-A。\n商品B: 遮光カーテン 型番 UNKNOWN-B。\n幅、丈、色、枚数の情報がありません。人手確認が必要か判定してください。",
    },
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_snapshot() -> dict[str, Any]:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    verified = {}
    for name, expected in FILES.items():
        target = MODEL_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            size, digest = target.stat().st_size, _sha256(target)
            if size != expected["size_bytes"] or digest != expected["sha256"]:
                raise RuntimeError(f"Existing file does not match pinned revision: {target}")
            verified[name] = {"size_bytes": size, "sha256": digest}
            continue
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        request = urllib.request.Request(url, headers={"User-Agent": "sku-matching-gliner-smoke/1"})
        partial = target.with_name(target.name + ".partial")
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
            while chunk := response.read(8 * 1024 * 1024):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        actual = digest.hexdigest()
        if size != expected["size_bytes"] or actual != expected["sha256"]:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"Pinned integrity check failed for {name}: {size} bytes sha256={actual}")
        os.replace(partial, target)
        verified[name] = {"size_bytes": size, "sha256": actual}
        print(f"verified {name}: {size:,} bytes", flush=True)
    (MODEL_DIR / "source-manifest.json").write_text(
        json.dumps({"repo": REPO, "revision": REVISION, "files": verified}, indent=2) + "\n",
        encoding="utf-8",
    )
    return verified


def _real_pair_controls(model: Any) -> dict[str, Any]:
    """Check whether a question appended to observed title+SKU pairs biases labels."""
    import copy

    import numpy as np

    from match_skus import model_text

    with REAL_PAIR_PATH.open(encoding="utf-8") as source:
        pair = json.loads(next(line for line in source if line.strip()))
    au = next(
        row for row in pair["au"]
        if row["sku_label"] == "幅100×丈80cm(4枚組) / ベージュ"
    )
    rakuten_by_id = {row["sku_id"]: row for row in pair["rakuten"]}
    specs = [
        ("observed_exact_variant", "CT0180BE", LABELS[0], False),
        ("observed_wrong_height", "CT0190BE", LABELS[1], False),
        ("observed_near_color", "CT0180SB", LABELS[1], False),
        ("observed_no_lace", "CT0180BEH", LABELS[1], False),
        # The observed product's broad title says "lace-curtain set"; remove only
        # the concrete SKU option so the specific lace selection is unknown.
        ("observed_lace_option_omitted", "CT0180BE", LABELS[2], True),
        ("observed_different_width_piece_count", "CT02200BE", LABELS[1], False),
    ]
    results = []
    for case_id, rk_sku_id, expected, omit_lace_option in specs:
        rakuten = copy.deepcopy(rakuten_by_id[rk_sku_id])
        rakuten_text = model_text(rakuten)
        if omit_lace_option:
            rakuten_text = rakuten_text.removesuffix(" / あり").removesuffix(" / なし")
        pair_text = f"au: {model_text(au)}\nRakuten: {rakuten_text}"
        texts = [pair_text, pair_text + "\n同一SKUか判定してください。"]
        start = time.perf_counter()
        outputs = model.batch_classify_text(
            texts, {"decision": LABELS}, batch_size=2, include_confidence=True
        )
        elapsed = time.perf_counter() - start
        comparisons = []
        for template, output in zip(("pair_only", "question_ended"), outputs, strict=True):
            decision = output.get("decision") if isinstance(output, dict) else output
            label = decision.get("label") if isinstance(decision, dict) else decision
            confidence = decision.get("confidence") if isinstance(decision, dict) else None
            comparisons.append({
                "template": template,
                "prediction": label,
                "confidence": confidence,
                "passed_synthetic_expectation": label == expected,
                "raw_output": output,
            })
        results.append({
            "case_id": case_id,
            "source_au_sku_id": au["sku_id"],
            "source_rakuten_sku_id": rk_sku_id,
            "expected_control_label": expected,
            "pair_text": pair_text,
            "sku_option_removed_for_test": omit_lace_option,
            "batch_elapsed_seconds": round(elapsed, 4),
            "comparisons": comparisons,
        })
    return {
        "source_file": str(REAL_PAIR_PATH.relative_to(ROOT)),
        "source_file_sha256": _sha256(REAL_PAIR_PATH),
        "source_pair_id": pair["pair_id"],
        "source_scope": pair.get("scope"),
        "source_note": "Observed au options and embedded Rakuten SKU rows; source artifact notes the collection is not independent product-identity ground truth.",
        "template_definition": {
            "pair_only": "AU title / SKU and Rakuten title / SKU with no classification question appended",
            "question_ended": "same pair text followed by 同一SKUか判定してください。",
        },
        "controls_are_synthetic_expectations_not_production_gold": True,
        "case_count": len(results),
        "total_classifications": 2 * len(results),
        "pair_only_correct": sum(x["comparisons"][0]["passed_synthetic_expectation"] for x in results),
        "question_ended_correct": sum(x["comparisons"][1]["passed_synthetic_expectation"] for x in results),
        "results": results,
    }


def run_trial() -> dict[str, Any]:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    if torch.cuda.is_available():
        raise RuntimeError("CUDA is available in this environment; refusing to use GPU")

    from gliner2 import AutoExtractor

    start = time.perf_counter()
    model = AutoExtractor.from_pretrained(str(MODEL_DIR), local_files_only=True)
    load_seconds = time.perf_counter() - start
    model.eval()

    rows = []
    elapsed = []
    for case in CASES:
        start = time.perf_counter()
        output = model.classify_text(
            case["text"], {"decision": LABELS}, include_confidence=True
        )
        seconds = time.perf_counter() - start
        elapsed.append(seconds)
        decision = output.get("decision") if isinstance(output, dict) else output
        prediction = decision.get("label") if isinstance(decision, dict) else decision
        confidence = decision.get("confidence") if isinstance(decision, dict) else None
        rows.append({
            "id": case["id"],
            "text": case["text"],
            "expected_synthetic_check": case["expected"],
            "prediction": prediction,
            "confidence": confidence,
            "raw_output": output,
            "elapsed_seconds": round(seconds, 4),
            "passed_synthetic_check": prediction == case["expected"],
        })
    template_sensitivity = _real_pair_controls(model)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; make the output portable enough for this cloud run.
    rss_bytes = int(rss * 1024)
    versions = {}
    for package in ("gliner2", "torch", "transformers", "tokenizers", "safetensors", "huggingface-hub"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    pip_check = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True, check=False
    )
    source_manifest = json.loads((MODEL_DIR / "source-manifest.json").read_text(encoding="utf-8"))
    return {
        "model": REPO,
        "revision": REVISION,
        "task": "pairwise SKU classification with three supplied labels",
        "provider": "CPU / PyTorch",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "threads": {"intra_op": torch.get_num_threads(), "inter_op": torch.get_num_interop_threads()},
        "dependency_versions": versions,
        "pip_check": {
            "returncode": pip_check.returncode,
            "stdout": pip_check.stdout.strip(),
            "stderr": pip_check.stderr.strip(),
        },
        "reproduction_commands": [
            "python3 -m venv .deps/sku-gliner-venv",
            ".deps/sku-gliner-venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.7.1",
            ".deps/sku-gliner-venv/bin/python -m pip install 'gliner2[local]'",
            ".deps/sku-gliner-venv/bin/python -m pip install transformers==5.17.0",
            ".deps/sku-gliner-venv/bin/python experiments/sku-matching/try_gliner.py --download",
            ".deps/sku-gliner-venv/bin/python experiments/sku-matching/try_gliner.py --run",
        ],
        "source_manifest": source_manifest,
        "load_seconds": round(load_seconds, 3),
        "peak_rss_bytes": rss_bytes,
        "items": len(rows),
        "correct_synthetic_checks": sum(row["passed_synthetic_check"] for row in rows),
        "mean_classification_seconds": round(sum(elapsed) / len(elapsed), 4),
        "classifications_per_second": round(len(elapsed) / sum(elapsed), 3),
        "score_kind": "confidence for the selected label, not a full candidate-label distribution",
        "label_scores_available": True,
        "runtime_notes": [
            "Checkpoint-declared transformers version 5.17.0 was installed after gliner2[local] resolved 4.57.6; the older version failed on tokenizer extra_special_tokens format.",
            "pip check was run after installing transformers 5.17.0.",
            "Model loaded from the verified local snapshot; no base encoder checkpoint was downloaded.",
            "Transformers reported that DeBERTa does not support SDPA and used eager attention.",
        ],
        "results_are_synthetic_checks_not_production_ground_truth": True,
        "results": rows,
        "template_sensitivity_on_observed_pair": template_sensitivity,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="download and verify the pinned model")
    parser.add_argument("--run", action="store_true", help="run the CPU-only synthetic comparison")
    args = parser.parse_args()
    if not args.download and not args.run:
        parser.error("choose --download and/or --run")
    if args.download:
        download_snapshot()
    if args.run:
        result = run_trial()
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: result[key] for key in (
            "load_seconds", "peak_rss_bytes", "items", "correct_synthetic_checks",
            "mean_classification_seconds", "classifications_per_second", "label_scores_available",
        )}, ensure_ascii=False), flush=True)
        print(f"saved {RESULT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
