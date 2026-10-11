#!/usr/bin/env python3
"""Compare a same-input Gemini replay with frozen Luna V13 using external labels only.

This script only grades already-frozen run artifacts; it performs no inference.
Labels and label-derived case results are read/written only under external paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
V13 = EXP / "results/20261010T210700Z-luna-width-contents-v13"
V13_EVAL = Path("/workspace/sku-width-contents-v13-evaluation-20261010T211800Z")

sys.path.insert(0, str(EXP))
import evaluate_frozen_sku_accuracy as scorer  # noqa: E402
import trial_luna_sku_matching as core  # noqa: E402


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_freeze(run_dir: Path) -> dict[str, str]:
    freeze_path = run_dir / "grading-freeze.json"
    if not freeze_path.is_file():
        raise ValueError(f"missing frozen grading manifest: {freeze_path}")
    manifest = core.read(freeze_path)
    entries = manifest.get("sha256")
    if not isinstance(entries, dict) or not entries:
        raise ValueError(f"empty freeze: {freeze_path}")
    for rel, expected in entries.items():
        p = (EXP / rel).resolve()
        if not p.is_relative_to(EXP.resolve()):
            raise ValueError(f"freeze path escapes experiment tree: {rel}")
        if not p.is_file() or digest(p) != expected:
            raise ValueError(f"freeze hash mismatch: {rel}")
    return entries


def read_labels(labels_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int]]:
    files = sorted(p for p in labels_dir.glob("*.jsonl") if p.is_file())
    if {p.name for p in files} != {"legacy-labels.jsonl", "novel-labels.jsonl"}:
        raise ValueError("external labels must be the unchanged legacy/novel V13 files")
    hashes = {p.name: digest(p) for p in files}
    line_counts: dict[str, int] = {}
    labels: list[dict[str, Any]] = []
    for p in files:
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"label file contains a non-object row: {p.name}")
        line_counts[p.name] = len(rows)
        labels.extend(rows)
    if len(labels) != 1439:
        raise ValueError(f"expected the frozen external 1439-label set; found {len(labels)}")
    if len({row.get("case_id") for row in labels}) != len(labels):
        raise ValueError("duplicate case_id in frozen external labels")
    return labels, hashes, line_counts


def cases_from(path: Path) -> list[dict[str, Any]]:
    obj = core.read(path)
    cases = obj.get("cases") if isinstance(obj, dict) else obj
    if not isinstance(cases, list):
        raise ValueError(f"expected an array of cases: {path}")
    return cases


def candidate_keys(cases: list[dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for case in cases:
        cid = case.get("case_id")
        if not isinstance(cid, str) or cid in result:
            raise ValueError("missing or duplicate input case_id")
        rows = case.get("au_rows")
        if not isinstance(rows, list):
            raise ValueError(f"missing AU candidate rows: {cid}")
        keys = [row.get("row_key") for row in rows]
        if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError(f"missing or duplicate row_key in candidate rows: {cid}")
        result[cid] = set(keys)
    return result


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_case_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path, help="frozen Gemini replay run inside this experiment")
    ap.add_argument("--labels-dir", required=True, type=Path, help="external frozen V13 labels directory")
    ap.add_argument("--output-dir", required=True, type=Path, help="new external evaluation output directory")
    args = ap.parse_args()

    run_dir = args.run_dir.resolve()
    labels_dir = args.labels_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not run_dir.is_relative_to((EXP / "results").resolve()):
        raise ValueError("run-dir must be a result directory within this experiment")
    for name, path in (("labels-dir", labels_dir), ("output-dir", output_dir)):
        if path == REPO.resolve() or path.is_relative_to(REPO.resolve()):
            raise ValueError(f"{name} must stay outside the checkout")
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite evaluation output: {output_dir}")

    # Verify both frozen runs before opening the external labels.
    run_freeze = verify_freeze(run_dir)
    v13_freeze = verify_freeze(V13)
    canonical_labels = (V13_EVAL / "labels").resolve()
    if labels_dir != canonical_labels:
        raise ValueError("labels-dir must be the existing V13 external labels directory; do not copy labels")
    source_record_path = V13_EVAL / "grading-source-manifest.json"
    if not source_record_path.is_file():
        raise ValueError("missing external V13 grading source manifest")
    source_record = core.read(source_record_path)
    if not source_record.get("source_hashes_unchanged") or source_record.get("human_verified") is not False:
        raise ValueError("external V13 label source manifest is not verified/frozen")
    pre_hashes = source_record.get("all_source_sha256_pre")
    post_hashes = source_record.get("all_source_sha256_post")
    if not isinstance(pre_hashes, dict) or pre_hashes != post_hashes:
        raise ValueError("external V13 source hashes changed during its grading run")

    gemini_input_path = run_dir / "derived/inputs.json"
    gemini_context_path = run_dir / "derived/source-contexts.json"
    v13_input_path = V13 / "derived/inputs.json"
    v13_context_path = V13 / "derived/source-contexts.json"
    if not all(p.is_file() for p in (gemini_input_path, gemini_context_path, v13_input_path, v13_context_path)):
        raise ValueError("missing frozen inputs or source contexts")
    if gemini_input_path.read_bytes() != v13_input_path.read_bytes():
        raise ValueError("Gemini inputs are not byte-identical to V13 inputs")
    if gemini_context_path.read_bytes() != v13_context_path.read_bytes():
        raise ValueError("Gemini source contexts are not byte-identical to V13 contexts")

    gemini_inputs = cases_from(gemini_input_path)
    v13_inputs = cases_from(v13_input_path)
    gemini_keys = candidate_keys(gemini_inputs)
    v13_keys = candidate_keys(v13_inputs)
    if len(gemini_inputs) != 50 or set(gemini_keys) != set(v13_keys):
        raise ValueError("expected the exact same 50 unique case IDs as V13")
    pairs = {(c["rakuten_sku_key"], c["au_product_id"]) for c in gemini_inputs}
    if len(pairs) != 50 or sum(len(c["au_rows"]) for c in gemini_inputs) != 934:
        raise ValueError("expected 50 unique Rakuten/AU pairs and 934 candidate rows")

    gemini_review_path = run_dir / "derived/reviewed-summary.json"
    v13_review_path = V13 / "derived/reviewed-summary.json"
    if not gemini_review_path.is_file() or not v13_review_path.is_file():
        raise ValueError("both frozen runs must have reviewed-summary.json")
    gemini_predictions = cases_from(gemini_review_path)
    v13_predictions = cases_from(v13_review_path)
    expected_ids = set(gemini_keys)
    for title, predictions in (("Gemini", gemini_predictions), ("V13", v13_predictions)):
        ids = [row.get("case_id") for row in predictions]
        if len(ids) != 50 or len(set(ids)) != 50 or set(ids) != expected_ids:
            raise ValueError(f"{title} predictions must cover each fixed case exactly once")

    # Only after all run/freeze/input checks do we access the external label set.
    labels, label_hashes_before, label_counts = read_labels(labels_dir)
    baseline = scorer.score(v13_predictions, labels, v13_keys)
    baseline_saved = core.read(V13 / "grading/broad50_v13/summary.json")
    baseline_case_path = V13 / "grading/broad50_v13/case-results.jsonl"
    baseline_rows = [json.loads(line) for line in baseline_case_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if baseline["metrics"] != baseline_saved.get("metrics"):
        raise ValueError("recomputed V13 baseline metrics differ from frozen V13 grading")
    if baseline["cases"] != baseline_rows:
        raise ValueError("recomputed V13 per-case grading differs from frozen V13 grading")
    if baseline["metrics"]["known_case_accuracy"]["denominator"] != 48:
        raise ValueError("V13 baseline no longer has the frozen 48-known-case denominator")

    gemini = scorer.score(gemini_predictions, labels, gemini_keys)
    if gemini["metrics"]["known_case_accuracy"]["denominator"] != 48:
        raise ValueError("Gemini must be scored on the same 48 known-label cases")
    if gemini["metrics"]["unknown_truth_count"] != 2:
        raise ValueError("expected the same two unknown-truth cases as V13")

    gemini_by_id = {row["case_id"]: row for row in gemini["cases"]}
    v13_by_id = {row["case_id"]: row for row in baseline["cases"]}
    pred_g = {row["case_id"]: row for row in gemini_predictions}
    pred_v = {row["case_id"]: row for row in v13_predictions}
    paired = []
    for cid in sorted(expected_ids):
        before, after = v13_by_id[cid], gemini_by_id[cid]
        paired.append({
            "case_id": cid,
            "v13_decision": pred_v[cid]["decision"],
            "v13_row_key": pred_v[cid].get("row_key"),
            "v13_correct": before["correct"],
            "gemini_decision": pred_g[cid]["decision"],
            "gemini_row_key": pred_g[cid].get("row_key"),
            "gemini_correct": after["correct"],
            "correctness_changed": before["correct"] != after["correct"],
        })

    label_hashes_after = {p.name: digest(p) for p in sorted(labels_dir.glob("*.jsonl")) if p.is_file()}
    if label_hashes_after != label_hashes_before:
        raise ValueError("external label files changed during grading")

    output_dir.mkdir(parents=True, exist_ok=False)
    save_json(output_dir / "summary.json", {
        "mode": "frozen external-label comparison; no inference",
        "case_count": 50,
        "unique_rakuten_au_pairs": len(pairs),
        "candidate_row_count": 934,
        "known_label_denominator": 48,
        "unknown_truth_count": 2,
        "labels_total": len(labels),
        "labels_dir": str(labels_dir),
        "label_source_manifest_sha256": digest(source_record_path),
        "label_file_sha256": label_hashes_before,
        "label_file_line_counts": label_counts,
        "v13_metrics": baseline["metrics"],
        "gemini_metrics": gemini["metrics"],
        "human_verified": False,
    })
    save_case_rows(output_dir / "v13-case-results.jsonl", baseline["cases"])
    save_case_rows(output_dir / "gemini-case-results.jsonl", gemini["cases"])
    save_json(output_dir / "paired-comparison.json", {
        "same_50_case_ids": True,
        "same_inputs_bytes": True,
        "same_source_contexts_bytes": True,
        "v13_baseline_recomputed_exactly": True,
        "cases": paired,
    })
    save_json(output_dir / "source-manifest.json", {
        "gemini_run_dir": str(run_dir),
        "gemini_grading_freeze_entry_count": len(run_freeze),
        "gemini_grading_freeze_all_match": True,
        "v13_run_dir": str(V13),
        "v13_grading_freeze_entry_count": len(v13_freeze),
        "v13_grading_freeze_all_match": True,
        "gemini_inputs_sha256": digest(gemini_input_path),
        "v13_inputs_sha256": digest(v13_input_path),
        "gemini_source_contexts_sha256": digest(gemini_context_path),
        "v13_source_contexts_sha256": digest(v13_context_path),
        "gemini_reviewed_summary_sha256": digest(gemini_review_path),
        "v13_reviewed_summary_sha256": digest(v13_review_path),
        "labels_external_only": True,
        "label_source_manifest_sha256": digest(source_record_path),
        "label_file_sha256": label_hashes_before,
        "label_hashes_unchanged_during_grade": True,
    })
    print(json.dumps({
        "v13_known_case_accuracy": baseline["metrics"]["known_case_accuracy"],
        "gemini_known_case_accuracy": gemini["metrics"]["known_case_accuracy"],
        "paired_cases": len(paired),
        "label_files": len(label_hashes_before),
        "labels": len(labels),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
